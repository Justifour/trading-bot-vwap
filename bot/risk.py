#!/usr/bin/env python3
"""
Risk & state manager for the bot:
  - one position at a time (kill-switch + pause via `active`)
  - daily loss limit (cumulative R) + max trades/day
  - direction gate (both | long | short)
  - sqlite trade log feeding /status month analytics

In-memory flags (active / direction / in_position) reset on restart; the trade log
persists. bot.py reconciles the live position from the exchange on startup.
"""
from __future__ import annotations
import os, time, sqlite3
from datetime import datetime, timezone
import config as C

class RiskManager:
    def __init__(self, db: str | None = None):
        db = db or C.DB_PATH
        parent = os.path.dirname(os.path.abspath(db))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(db)
        self.db.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            ts INTEGER, exit_ts INTEGER, direction TEXT,
            entry REAL, stop REAL, outcome TEXT, r REAL, size_btc REAL, pnl_usd REAL
        )""")
        self.db.commit()
        self.active = True               # bot running? (/start, /stop)
        self.direction = C.DIRECTION     # both | long | short  (/direction)
        self.in_position = False
        self.position = None             # current open trade dict

    # ---------------- gating ----------------
    def can_enter(self) -> tuple[bool, str]:
        if not self.active:                                   return False, "бот остановлен (/start)"
        if self.in_position:                                  return False, "уже в позиции"
        if self.trades_today() >= C.MAX_TRADES_PER_DAY:       return False, "лимит сделок/день"
        if C.DAILY_LOSS_LIMIT_R is not None and self.r_today() <= C.DAILY_LOSS_LIMIT_R:
            return False, f"дневной лимит просадки ({C.DAILY_LOSS_LIMIT_R}R)"
        return True, "ok"

    def direction_ok(self, d: str) -> bool:
        return self.direction == "both" or self.direction == d

    # ---------------- lifecycle ----------------
    def open_position(self, pos: dict):
        self.in_position = True
        self.position = {"ts": int(time.time()), **pos}

    def close_position(self, outcome: str, r: float, pnl_usd: float):
        p = self.position or {}
        self.db.execute(
            "INSERT INTO trades(ts,exit_ts,direction,entry,stop,outcome,r,size_btc,pnl_usd) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (p.get("ts"), int(time.time()), p.get("direction"), p.get("entry"),
             p.get("stop"), outcome, r, p.get("size_btc"), pnl_usd))
        self.db.commit()
        self.in_position = False
        self.position = None

    # ---------------- stats ----------------
    @staticmethod
    def _epoch(y, m, d) -> int:
        return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())

    def _day_start(self) -> int:
        n = datetime.now(timezone.utc); return self._epoch(n.year, n.month, n.day)

    def _month_start(self) -> int:
        n = datetime.now(timezone.utc); return self._epoch(n.year, n.month, 1)

    def trades_today(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM trades WHERE exit_ts>=?", (self._day_start(),)).fetchone()[0]

    def r_today(self) -> float:
        return self.db.execute("SELECT COALESCE(SUM(r),0) FROM trades WHERE exit_ts>=?", (self._day_start(),)).fetchone()[0]

    def month_stats(self) -> dict:
        rows = self.db.execute(
            "SELECT outcome,r,pnl_usd FROM trades WHERE exit_ts>=?", (self._month_start(),)).fetchall()
        n = len(rows)
        full = sum(1 for o, r, p in rows if o == "FULL")
        part = sum(1 for o, r, p in rows if o == "PART")
        loss = sum(1 for o, r, p in rows if o == "LOSS")
        return {
            "n": n, "full": full, "part": part, "loss": loss,
            "wr": (100 * (full + part) / n) if n else 0.0,
            "R": sum(r for o, r, p in rows),
            "pnl": sum(p for o, r, p in rows),
        }

    def status_text(self) -> str:
        s = self.month_stats()
        pos = "нет" if not self.in_position else f"{self.position['direction']} @ {self.position.get('entry')}"
        month = datetime.now(timezone.utc).strftime("%B %Y")
        return (f"🤖 бот: {'🟢 ON' if self.active else '🔴 OFF'} | Lighter mainnet | {C.STRATEGY} | направление: {self.direction}\n"
                f"позиция: {pos}\n"
                f"сегодня: {self.trades_today()} сделок, {self.r_today():+.1f}R\n"
                f"— {month} —\n"
                f"сделок {s['n']} | WR {s['wr']:.0f}% | 🟢{s['full']} 🟡{s['part']} 🔴{s['loss']}\n"
                f"итог: {s['R']:+.1f}R | ${s['pnl']:+.2f}")


# ------------------------- self-test (no keys) -------------------------
if __name__ == "__main__":
    t = "/tmp/_risk_test.db"
    if os.path.exists(t): os.remove(t)
    rm = RiskManager(t)
    for oc, r in [("FULL", 2.25), ("PART", 0.5), ("LOSS", -1.0), ("PART", 0.5)]:
        rm.open_position({"direction": "short", "entry": 62000, "stop": 62186, "size_btc": 0.0269})
        rm.close_position(oc, r, r * 5.0)        # 1R = $5
    s = rm.month_stats()
    assert s["n"] == 4 and abs(s["R"] - 2.25) < 1e-9 and s["full"] == 1 and s["part"] == 2 and s["loss"] == 1
    ok, why = rm.can_enter()
    assert ok, why
    rm.active = False
    assert not rm.can_enter()[0]
    print(rm.status_text().replace("🔴 OFF", "OFF"))
    print("\n✅ risk.py self-test PASS")
    os.remove(t)
