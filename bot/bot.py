#!/usr/bin/env python3
"""
Main bot loop: signal (Bybit 15m) -> decision -> execution (Lighter) -> breakeven monitor.

Two modes:
  - DRY-RUN (exec_layer=None): detects live signals, computes the trade plan, notifies.
    Runs with NO keys — use it to watch the brain work live and confirm signals == chart.
  - LIVE (exec_layer=LighterExec): also places the orders and manages BE/close on the exchange.

Run dry-run:  python3 bot.py
"""
from __future__ import annotations
import asyncio
import time
import config as C
from signals import SignalEngine, Signal
from risk import RiskManager


def plan_trade(direction: str, entry: float) -> dict:
    """Pure: stop / partial-TP(1:1) / runner-TP(1:RR) price levels from config."""
    risk = entry * C.STOP_PCT / 100.0
    if direction == "short":
        stop = entry + risk
        tp1  = entry - C.PARTIAL_AT_R * risk
        tpR  = entry - C.RR * risk
    else:
        stop = entry - risk
        tp1  = entry + C.PARTIAL_AT_R * risk
        tpR  = entry + C.RR * risk
    return {"direction": direction, "entry": entry, "stop": stop,
            "tp1": tp1, "tp_runner": tpR, "risk_per_unit": risk}


class Bot:
    def __init__(self, exec_layer=None, notifier=None, poll_sec: int = 20):
        self.exec = exec_layer                       # LighterExec or None (dry-run)
        self.notify = notifier or (lambda m: print(m, flush=True))
        self.poll_sec = poll_sec
        self.risk = RiskManager()
        self.sig = SignalEngine(direction=self.risk.direction)
        self._be_moved = False
        self._full_size = 0.0

    # --------------- entry ---------------
    async def on_signal(self, s: Signal):
        self.sig.direction = self.risk.direction      # honor /direction
        if not self.risk.direction_ok(s.direction):
            return
        ok, why = self.risk.can_enter()
        if not ok:
            self.notify(f"⏭ сигнал {s.direction} @ {s.price:.0f} пропущен: {why}")
            return
        balance = (await self.exec.balance()) if self.exec else 1000.0
        size = self.exec.size_btc(balance, s.price) if self.exec else \
               round(balance * C.RISK_PCT / 100 / (s.price * C.STOP_PCT / 100), 5)
        p = plan_trade(s.direction, s.price)
        chop = (f"ER<{C.ER_MAX_LONG}" if s.direction == "long" and C.STRATEGY == "hybrid"
                else f"ER<{C.ER_MAX_CLASSIC}" if C.STRATEGY == "classic"
                else "roll 2σ")
        self.notify(f"📈 ВХОД {'🔴 SHORT' if s.direction=='short' else '🟢 LONG'} @ {s.price:.0f}\n"
                    f"   стоп {p['stop']:.0f} · тейк½ {p['tp1']:.0f} · ранер {p['tp_runner']:.0f}\n"
                    f"   размер {size:.5f} BTC · {C.STRATEGY} · {chop}")
        if self.exec:
            if await self.exec.has_open_position():
                self.notify("⏭ сигнал пропущен: на бирже уже есть позиция")
                return
            await self.exec.enter(s.direction, s.price, p["stop"], p["tp1"], p["tp_runner"], size)
        self.risk.open_position({"direction": s.direction, "entry": s.price,
                                 "stop": p["stop"], "tp1": p["tp1"], "tp_runner": p["tp_runner"],
                                 "size_btc": size})
        self._be_moved = False
        self._full_size = size

    # --------------- exit monitor (LIVE only) ---------------
    async def monitor(self):
        """Detect ½-fill -> move stop to BE; detect full close -> record outcome + notify.
        NOTE: outcome classification + fill detection to be finalized against testnet fills."""
        if not (self.exec and self.risk.in_position):
            return
        pos = await self.exec.position()
        cur = abs(pos["size_btc"]) if pos else 0.0
        # partial fill: size dropped to ~half -> move runner stop to breakeven
        if (C.BREAKEVEN and not self._be_moved and 0 < cur <= self._full_size * 0.6):
            be_px = await self.exec.move_stop_to_be(self.risk.position)
            if be_px is not None:
                self._be_moved = True
                ep = float(pos["entry"]) if pos else self.risk.position["entry"]
                self.notify(f"🟡 фикс ½ на 1:1 → BE limit @ {be_px:.0f} (entry {ep:.0f})")
        elif self._be_moved and pos and cur > 0:
            if await self.exec.maybe_be_fallback():
                self.notify("⚠️ BE limit не fill → market раннер")
        # closed
        if cur == 0:
            if self.exec:
                outcome, r = await self.exec.classify_exit(self.risk.position, self._be_moved)
            else:
                outcome, r = self._classify_close_dry()
            pnl = r * (self._full_size * self.risk.position["entry"] * C.STOP_PCT / 100)
            self.risk.close_position(outcome, r, pnl)
            self.notify(f"✅ закрыто: {outcome} {r:+.2f}R")

    def _classify_close_dry(self) -> tuple[str, float]:
        full_r = C.PARTIAL_FRAC * C.PARTIAL_AT_R + (1 - C.PARTIAL_FRAC) * C.RR
        if self._be_moved:
            return ("FULL", full_r)
        return ("LOSS", -1.0)

    # --------------- startup reconciliation ---------------
    async def reconcile(self):
        """Restore in-memory position state from exchange after worker restart."""
        if not self.exec:
            return
        pos = await self.exec.position()
        if not pos or abs(pos["size_btc"]) == 0:
            return
        size = abs(pos["size_btc"])
        p = plan_trade(pos["direction"], pos["entry"])
        self.risk.in_position = True
        self.risk.position = {
            "direction": pos["direction"],
            "entry": pos["entry"],
            "stop": p["stop"],
            "tp1": p["tp1"],
            "tp_runner": p["tp_runner"],
            "size_btc": size,
        }
        self._be_moved = False
        self._full_size = size
        try:
            self._be_moved, self._full_size = await self.exec.detect_be_state(pos)
        except Exception:
            pass
        self.notify(f"♻️ восстановлена позиция: {pos['direction']} {size:.5f} BTC @ {pos['entry']:.0f}"
                    + (" · partial/BE" if self._be_moved else ""))

    # --------------- main loop ---------------
    async def _ensure_seeded(self):
        """Load candle history without killing the worker — TG stays up if API is flaky."""
        last_notify = 0.0
        while not self.sig.bars:
            try:
                await asyncio.to_thread(self.sig.seed)
                return
            except Exception as e:
                print(f"seed failed ({e}), retry in {self.poll_sec}s", flush=True)
                if time.time() - last_notify > 3600:
                    self.notify(f"⚠️ не могу загрузить свечи ({C.DATA_SOURCE}): {e}")
                    last_notify = time.time()
                await asyncio.sleep(self.poll_sec)

    async def run(self):
        await self._ensure_seeded()
        await self.reconcile()
        self.notify(f"🤖 бот запущен (mode: {'LIVE' if self.exec else 'DRY-RUN'}) · "
                    f"Lighter · {C.DATA_SOURCE} · {len(self.sig.bars)} баров · "
                    f"направление {self.risk.direction}")
        while True:
            try:
                if await asyncio.to_thread(self.sig.update):
                    if self.risk.active and not self.risk.in_position:
                        s = self.sig.latest_signal()
                        if s:
                            await self.on_signal(s)
                if self.risk.in_position:
                    await self.monitor()
            except Exception as e:
                self.notify(f"⚠️ ошибка цикла: {e}")
            sleep = C.POSITION_POLL_SEC if self.risk.in_position else self.poll_sec
            await asyncio.sleep(sleep)


# ------------------------- self-test / dry-run -------------------------
def _selftest():
    s = plan_trade("short", 62000)
    assert abs(s["stop"] - 62186) < 1e-6 and abs(s["tp1"] - 61814) < 1e-6 and abs(s["tp_runner"] - 61070) < 1e-6, s
    l = plan_trade("long", 62000)
    assert abs(l["stop"] - 61814) < 1e-6 and abs(l["tp1"] - 62186) < 1e-6 and abs(l["tp_runner"] - 62930) < 1e-6, l
    print("plan_trade short @62000:", {k: round(v) for k, v in s.items() if isinstance(v, float)})
    print("plan_trade long  @62000:", {k: round(v) for k, v in l.items() if isinstance(v, float)})
    print("✅ bot.py plan_trade self-test PASS")


if __name__ == "__main__":
    import sys
    if "--run" in sys.argv:
        asyncio.run(Bot().run())            # DRY-RUN: live signals, no execution
    else:
        _selftest()
