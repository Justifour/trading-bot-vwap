#!/usr/bin/env python3
"""
Lighter execution layer for the VWAP-deviation scalp.

Exits (server-side, survive disconnect):
  - SL: Stop Market (full size) — guaranteed exit on loss
  - TP: Take-Profit Limit @ target price
  - BE: Resting Limit GTC @ avg entry (+ EXIT_BE_BUF) after partial
  - BE fallback: if price blows past BE limit while runner still open → market close

BTC market (market_id=1): size_int = round(size_btc * 1e5); price_int = round(price_usd * 10).
"""
from __future__ import annotations
import time, math
import lighter
import config as C


def _coi() -> int:
    return int(time.time() * 1000) % 2_000_000_000


def _lighter_configuration(host: str) -> lighter.Configuration:
    cfg = lighter.Configuration(host=host)
    proxy = C.LIGHTER_PROXY.strip()
    if proxy:
        cfg.proxy = proxy
    return cfg


class LighterExec:
    def __init__(self, base_url=None, account_index=None, api_key_index=None, api_private_key=None):
        self.url = base_url or C.LIGHTER_BASE_URL
        self.account_index = C.LIGHTER_ACCOUNT_INDEX if account_index is None else account_index
        self.api_key_index = C.LIGHTER_API_KEY_INDEX if api_key_index is None else api_key_index
        self.api_private_key = api_private_key or C.LIGHTER_API_PRIVATE_KEY
        self.mkt          = C.LIGHTER_BTC_MARKET_INDEX
        self.size_scale   = 10 ** C.LIGHTER_BTC_SIZE_DECIMALS
        self.price_scale  = 10 ** C.LIGHTER_BTC_PRICE_DECIMALS
        self.signer: "lighter.SignerClient | None" = None
        self.api: "lighter.ApiClient | None" = None

    async def connect(self):
        cfg = _lighter_configuration(self.url)
        self.api = lighter.ApiClient(configuration=cfg)
        self.signer = lighter.SignerClient(
            url=self.url,
            account_index=self.account_index,
            api_private_keys={self.api_key_index: self.api_private_key},
        )
        if C.LIGHTER_PROXY.strip():
            self.signer.api_client.configuration.proxy = C.LIGHTER_PROXY.strip()
        err = self.signer.check_client()
        if err:
            raise RuntimeError(f"Lighter signer invalid: {err}")
        return self

    async def close(self):
        if self.signer: await self.signer.close()
        if self.api:    await self.api.close()

    def p(self, usd: float) -> int:
        return int(round(usd * self.price_scale))

    def s(self, btc: float) -> int:
        return int(round(btc * self.size_scale))

    def round_size(self, btc: float) -> float:
        step = 1 / self.size_scale
        return math.floor(btc / step) * step

    def size_btc(self, balance_usd: float, entry_usd: float) -> float:
        risk_usd  = balance_usd * C.RISK_PCT / 100.0
        stop_frac = C.STOP_PCT / 100.0
        notional  = risk_usd / stop_frac
        size = self.round_size(notional / entry_usd)
        return max(size, C.LIGHTER_BTC_MIN_BASE)

    def _bound(self, price_usd: float, is_buy: bool, buf=None) -> int:
        b = (C.SLIPPAGE_PCT / 100.0) if buf is None else buf
        return self.p(price_usd * (1 + b) if is_buy else price_usd * (1 - b))

    def be_limit_price(self, entry: float, direction: str) -> float:
        """USD price for resting BE limit (short: buy above entry by EXIT_BE_BUF)."""
        if direction == "short":
            return entry * (1 + C.EXIT_BE_BUF)
        return entry * (1 - C.EXIT_BE_BUF)

    def be_limit_price_int(self, entry: float, direction: str) -> int:
        close_buy = direction == "short"
        return self._bound(entry, is_buy=close_buy, buf=C.EXIT_BE_BUF)

    def _order_price_usd(self, order) -> float:
        bp = getattr(order, "base_price", 0) or 0
        if bp:
            return float(bp) / self.price_scale
        return float(order.price)

    def _near_be_limit(self, order, entry: float, direction: str) -> bool:
        if order.type != "limit" or not order.reduce_only:
            return False
        be = self.be_limit_price(entry, direction)
        return abs(self._order_price_usd(order) - be) <= entry * 0.0005

    def be_fallback_level(self, entry: float, direction: str) -> float:
        """Price beyond which BE limit failed — force market runner."""
        t = C.EXIT_BE_BUF + C.BE_FALLBACK_PCT
        return entry * (1 + t) if direction == "short" else entry * (1 - t)

    async def best_price(self, side_buy: bool) -> float:
        oa = lighter.OrderApi(self.api)
        ob = await oa.order_book_orders(self.mkt, 1)
        lvl = ob.asks[0] if side_buy else ob.bids[0]
        return float(lvl.price)

    async def _account(self):
        aa = lighter.AccountApi(self.api)
        resp = await aa.account(by="index", value=str(self.account_index))
        accounts = getattr(resp, "accounts", None) or []
        return accounts[0] if accounts else resp

    async def balance(self) -> float:
        acc = await self._account()
        if not acc:
            return 0.0
        return float(getattr(acc, "available_balance", None) or getattr(acc, "collateral", 0) or 0)

    async def _auth_token(self) -> str:
        auth, err = self.signer.create_auth_token_with_expiry()
        if err:
            raise RuntimeError(f"auth token: {err}")
        return auth

    async def _active_orders(self):
        auth = await self._auth_token()
        return await lighter.OrderApi(self.api).account_active_orders(
            authorization=auth, account_index=self.account_index, market_id=self.mkt)

    async def inactive_orders(self, limit: int = 40) -> list:
        auth = await self._auth_token()
        resp = await lighter.OrderApi(self.api).account_inactive_orders(
            authorization=auth, account_index=self.account_index,
            market_id=self.mkt, limit=limit)
        return list(resp.orders or [])

    def _trigger_usd(self, order) -> float:
        return float(order.trigger_price) / self.price_scale

    @staticmethod
    def _check(label: str, result):
        if isinstance(result, tuple) and len(result) == 3:
            _, _, err = result
            if err:
                raise RuntimeError(f"{label}: {err}")
        return result

    def _is_sl(self, o) -> bool:
        return o.type in ("stop-loss", "stop-loss-limit")

    async def _cancel_sl_orders(self):
        resp = await self._active_orders()
        for o in resp.orders or []:
            if not self._is_sl(o):
                continue
            if o.status not in ("open", "pending", "in-progress"):
                continue
            self._check("cancel SL", await self.signer.cancel_order(
                self.mkt, o.order_index, api_key_index=self.api_key_index))

    async def _cancel_be_limits(self, entry: float, direction: str):
        resp = await self._active_orders()
        for o in resp.orders or []:
            if not self._near_be_limit(o, entry, direction):
                continue
            if o.status not in ("open", "pending", "in-progress"):
                continue
            self._check("cancel BE limit", await self.signer.cancel_order(
                self.mkt, o.order_index, api_key_index=self.api_key_index))

    async def enter(self, direction: str, entry_usd: float, stop_usd: float,
                    tp1_usd: float, tp_run_usd: float, size_btc: float):
        """Market entry + SL market + TP limit (½ @1:1, ½ runner)."""
        is_ask     = direction == "short"
        close_ask  = not is_ask
        close_buy  = is_ask
        sz   = self.s(size_btc)
        half = self.s(self.round_size(size_btc / 2))
        runner = sz - half

        entry_bound = self._bound(entry_usd, is_buy=not is_ask)
        self._check("entry", await self.signer.create_market_order(
            self.mkt, _coi(), sz, entry_bound, is_ask,
            reduce_only=False, api_key_index=self.api_key_index))

        # SL market — exit on loss even on impulse
        self._check("stop-loss", await self.signer.create_sl_order(
            self.mkt, _coi(), sz,
            trigger_price=self.p(stop_usd),
            price=self._bound(stop_usd, is_buy=close_buy, buf=C.EXIT_SL_BUF),
            is_ask=close_ask, reduce_only=True, api_key_index=self.api_key_index))

        tp_px = self.p(tp1_usd)
        self._check("take-profit 1", await self.signer.create_tp_limit_order(
            self.mkt, _coi(), half,
            trigger_price=tp_px, price=tp_px,
            is_ask=close_ask, reduce_only=True, api_key_index=self.api_key_index))

        tpR_px = self.p(tp_run_usd)
        self._check("take-profit runner", await self.signer.create_tp_limit_order(
            self.mkt, _coi(), runner,
            trigger_price=tpR_px, price=tpR_px,
            is_ask=close_ask, reduce_only=True, api_key_index=self.api_key_index))

    async def set_leverage(self, leverage: int, isolated: bool = False):
        mode = (self.signer.ISOLATED_MARGIN_MODE if isolated
                else self.signer.CROSS_MARGIN_MODE)
        self._check("update leverage", await self.signer.update_leverage(
            self.mkt, mode, leverage, api_key_index=self.api_key_index))

    async def market_enter(self, direction: str, size_btc: float):
        is_ask = direction == "short"
        entry = await self.best_price(side_buy=not is_ask)
        sz = self.s(self.round_size(size_btc))
        self._check("market entry", await self.signer.create_market_order(
            self.mkt, _coi(), sz, self._bound(entry, is_buy=not is_ask), is_ask,
            reduce_only=False, api_key_index=self.api_key_index))
        return entry, size_btc

    async def flatten(self):
        await self.signer.cancel_all_orders(
            self.signer.CANCEL_ALL_TIF_IMMEDIATE, int(time.time() * 1000),
            cancel_all_market_index=self.mkt, api_key_index=self.api_key_index)
        pos = await self.position()
        if pos and abs(pos["size_btc"]) > 0:
            is_ask = pos["size_btc"] > 0
            mid = await self.best_price(side_buy=not is_ask)
            await self.signer.create_market_order(
                self.mkt, _coi(), self.s(abs(pos["size_btc"])),
                self._bound(mid, is_buy=not is_ask, buf=0.003), is_ask,
                reduce_only=True, api_key_index=self.api_key_index)

    async def move_stop_to_be(self, pos_info: dict) -> float | None:
        """Cancel SL → resting BE limit GTC @ avg entry (+ EXIT_BE_BUF). Returns BE limit USD."""
        pos = await self.position()
        if not pos or abs(pos["size_btc"]) == 0:
            return None
        entry = float(pos["entry"])
        direction = pos["direction"]
        close_ask = direction == "long"
        sz = self.s(abs(pos["size_btc"]))
        be_px = self.be_limit_price(entry, direction)

        await self._cancel_sl_orders()

        self._check("BE limit", await self.signer.create_order(
            self.mkt, _coi(), sz, self.be_limit_price_int(entry, direction),
            is_ask=close_ask,
            order_type=self.signer.ORDER_TYPE_LIMIT,
            time_in_force=self.signer.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
            reduce_only=True, api_key_index=self.api_key_index))
        return be_px

    async def maybe_be_fallback(self) -> bool:
        """BE limit didn't fill and price moved past BE limit → market runner."""
        pos = await self.position()
        if not pos or abs(pos["size_btc"]) == 0:
            return False
        entry = float(pos["entry"])
        direction = pos["direction"]
        close_side_buy = direction == "short"
        px = await self.best_price(side_buy=close_side_buy)
        bad = px > self.be_fallback_level(entry, "short") if direction == "short" \
            else px < self.be_fallback_level(entry, "long")
        if not bad:
            return False

        sz = self.s(abs(pos["size_btc"]))
        await self._cancel_sl_orders()
        await self._cancel_be_limits(entry, direction)
        is_ask = direction == "long"
        self._check("BE fallback market", await self.signer.create_market_order(
            self.mkt, _coi(), sz,
            self._bound(px, is_buy=close_side_buy, buf=C.EXIT_BE_BUF),
            is_ask, reduce_only=True, api_key_index=self.api_key_index))
        return True

    async def has_open_position(self) -> bool:
        pos = await self.position()
        return bool(pos and abs(pos["size_btc"]) > 0)

    async def position(self) -> dict | None:
        acc = await self._account()
        if not acc:
            return None
        for p in getattr(acc, "positions", []) or []:
            if int(getattr(p, "market_id", -1)) != self.mkt:
                continue
            raw = float(getattr(p, "position", 0) or 0)
            if raw == 0:
                return None
            sign = int(getattr(p, "sign", 1) or 1)
            signed = raw if sign >= 0 else -raw
            return {
                "size_btc": signed,
                "entry": float(getattr(p, "avg_entry_price", 0) or 0),
                "direction": "long" if signed > 0 else "short",
            }
        return None

    async def detect_be_state(self, pos: dict) -> tuple[bool, float]:
        entry = float(pos["entry"])
        direction = pos["direction"]
        size = abs(pos["size_btc"])
        be = False
        try:
            resp = await self._active_orders()
            orders = list(resp.orders or [])
            tp_left = [o for o in orders if o.type.startswith("take-profit")]
            if len(tp_left) == 1:
                be = True
            if any(self._near_be_limit(o, entry, direction) for o in orders):
                be = True
        except Exception:
            pass
        full_est = size * 2 if be else size
        return be, full_est

    async def classify_exit(self, pos_info: dict, be_moved: bool) -> tuple[str, float]:
        full_r = C.PARTIAL_FRAC * C.PARTIAL_AT_R + (1 - C.PARTIAL_FRAC) * C.RR
        part_r = C.PARTIAL_FRAC * C.PARTIAL_AT_R
        if not be_moved:
            return ("LOSS", -1.0)

        entry = float(pos_info["entry"])
        direction = pos_info["direction"]
        tp_runner = float(pos_info.get("tp_runner") or 0)
        be_px = self.be_limit_price(entry, direction)
        tol = max(entry * C.STOP_PCT / 100.0 * 0.5, 1.0)

        runner_tp_hit = False
        be_limit_hit = False
        try:
            for o in await self.inactive_orders():
                if o.status != "filled" or float(o.filled_base_amount or 0) <= 0:
                    continue
                if o.type not in ("take-profit", "take-profit-limit", "limit", "market"):
                    continue
                if o.type.startswith("take-profit") and tp_runner:
                    trig = self._trigger_usd(o)
                    if abs(trig - tp_runner) <= tol:
                        runner_tp_hit = True
                if o.type == "limit" and abs(self._order_price_usd(o) - be_px) <= tol:
                    be_limit_hit = True
        except Exception:
            pass

        if runner_tp_hit:
            return ("FULL", full_r)
        if be_limit_hit:
            return ("PART", part_r)
        return ("PART", part_r)


def _selftest():
    x = LighterExec(api_private_key="dummy")
    entry = 63333.0
    be = x.be_limit_price(entry, "short")
    assert be > entry, be
    assert x.be_limit_price(entry, "long") < entry
    assert x.be_fallback_level(entry, "short") > be
    assert x.be_limit_price_int(entry, "short") > x.p(entry)
    assert x._bound(64449.0, is_buy=True, buf=C.EXIT_SL_BUF) > x.p(64449.0)
    sz = x.size_btc(100, entry)
    risk = sz * entry * (C.STOP_PCT / 100)
    assert abs(risk - 1.0) < 0.05, risk
    print(f"BE limit short @ entry {entry:.0f} → {be:.1f}")
    print(f"BE fallback short → market if ask > {x.be_fallback_level(entry, 'short'):.1f}")
    print("PURE-MATH SELF-TEST: ✅ PASS")


if __name__ == "__main__":
    _selftest()
