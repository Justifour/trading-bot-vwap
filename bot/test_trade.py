#!/usr/bin/env python3
"""
One-off Lighter mainnet trade — verify entry + server-side SL + 2×TP lifecycle.

Requires bot/.env with LIGHTER_* keys (see setup_api_key.py).

Usage:
    venv/bin/python3 test_trade.py check
    venv/bin/python3 test_trade.py long --yes
    venv/bin/python3 test_trade.py short --yes
    venv/bin/python3 test_trade.py demo-long --yes   # 100x long $10k notional, no SL
"""
from __future__ import annotations
import argparse
import asyncio
import sys

import config as C
from bot import plan_trade
from lighter_exec import LighterExec


def _min_size_btc(price: float) -> float:
    """Exchange minimum: 0.0002 BTC and $10 notional."""
    by_notional = C.LIGHTER_BTC_MIN_NOTIONAL / price
    return max(C.LIGHTER_BTC_MIN_BASE, by_notional)


async def _connect() -> LighterExec:
    if not C.LIGHTER_API_PRIVATE_KEY:
        raise SystemExit(
            "LIGHTER_API_PRIVATE_KEY пустой в .env\n"
            "1) Создай аккаунт на Lighter mainnet и пополни\n"
            "2) venv/bin/python3 setup_api_key.py  (нужен ETH_PRIVATE_KEY + ETH_ADDRESS)\n"
            "3) Вставь вывод в bot/.env"
        )
    ex = await LighterExec().connect()
    return ex


async def cmd_check(ex: LighterExec):
    bal = await ex.balance()
    ask = await ex.best_price(side_buy=True)
    bid = await ex.best_price(side_buy=False)
    mid = (ask + bid) / 2
    pos = await ex.position()
    print(f"URL:      {ex.url}")
    print(f"Account:  {ex.account_index}  ·  API key index: {ex.api_key_index}")
    print(f"Balance:  ${bal:,.2f}")
    print(f"BTC mid:  ${mid:,.1f}  (bid {bid:,.1f} / ask {ask:,.1f})")
    print(f"Min size: {_min_size_btc(mid):.5f} BTC  (~${_min_size_btc(mid)*mid:.0f})")
    if pos:
        print(f"Position: {pos['direction']} {abs(pos['size_btc']):.5f} BTC @ {pos['entry']:.1f}")
    else:
        print("Position: flat")
    try:
        orders = await ex._active_orders()
        n = len(orders.orders or [])
        print(f"Active orders (BTC): {n}")
        for o in (orders.orders or []):
            print(f"  · {o.type} {o.remaining_base_amount} BTC  trigger={o.trigger_price}  status={o.status}")
    except Exception as e:
        print(f"Active orders: (skip) {e}")


async def cmd_enter(ex: LighterExec, direction: str, yes: bool):
    pos = await ex.position()
    if pos and abs(pos["size_btc"]) > 0:
        raise SystemExit(f"Уже есть позиция: {pos}. Сначала: test_trade.py flatten --yes")

    entry = await ex.best_price(side_buy=(direction == "long"))
    size = ex.round_size(_min_size_btc(entry))
    p = plan_trade(direction, entry)

    print(f"TEST TRADE  {direction.upper()} @ ~${entry:,.1f}")
    print(f"  size:   {size:.5f} BTC  (~${size*entry:,.0f})")
    print(f"  stop:   {p['stop']:.1f}")
    print(f"  tp½:    {p['tp1']:.1f}")
    print(f"  runner: {p['tp_runner']:.1f}")
    if not yes:
        raise SystemExit("Добавь --yes чтобы отправить ордера на mainnet")

    await ex.enter(direction, entry, p["stop"], p["tp1"], p["tp_runner"], size)
    print("✅ ордера отправлены — проверь позицию и SL/TP в UI Lighter mainnet")
    await asyncio.sleep(2)
    await cmd_check(ex)


async def cmd_demo_long(ex: LighterExec, notional: float, leverage: int, yes: bool):
    pos = await ex.position()
    if pos and abs(pos["size_btc"]) > 0:
        raise SystemExit(f"Уже есть позиция: {pos}. Сначала: test_trade.py flatten --yes")

    entry = await ex.best_price(side_buy=True)
    size = ex.round_size(notional / entry)
    margin = notional / leverage

    print(f"DEMO LONG  mainnet · {leverage}x · no stop")
    print(f"  notional: ${notional:,.0f}")
    print(f"  margin:   ~${margin:,.0f}")
    print(f"  size:     {size:.5f} BTC @ ~${entry:,.1f}")
    if not yes:
        raise SystemExit("Добавь --yes чтобы отправить на mainnet")

    await ex.set_leverage(leverage)
    await ex.market_enter("long", size)
    print("✅ long открыт — без SL, закрыть: test_trade.py flatten --yes")
    await asyncio.sleep(2)
    await cmd_check(ex)


async def cmd_flatten(ex: LighterExec, yes: bool):
    pos = await ex.position()
    if not pos:
        print("Позиции нет — flatten не нужен")
        return
    print(f"Flatten: {pos['direction']} {abs(pos['size_btc']):.5f} BTC")
    if not yes:
        raise SystemExit("Добавь --yes чтобы закрыть всё")
    await ex.flatten()
    print("✅ flatten отправлен")
    await asyncio.sleep(2)
    await cmd_check(ex)


async def main():
    ap = argparse.ArgumentParser(description="Lighter mainnet trade helper")
    ap.add_argument("cmd", choices=["check", "long", "short", "flatten", "demo-long"])
    ap.add_argument("--yes", action="store_true", help="подтвердить отправку ордеров")
    ap.add_argument("--notional", type=float, default=10_000, help="demo-long: размер позиции USD")
    ap.add_argument("--leverage", type=int, default=100, help="demo-long: плечо")
    args = ap.parse_args()

    ex = await _connect()
    try:
        if args.cmd == "check":
            await cmd_check(ex)
        elif args.cmd in ("long", "short"):
            await cmd_enter(ex, args.cmd, args.yes)
        elif args.cmd == "flatten":
            await cmd_flatten(ex, args.yes)
        elif args.cmd == "demo-long":
            await cmd_demo_long(ex, args.notional, args.leverage, args.yes)
    finally:
        await ex.close()


if __name__ == "__main__":
    asyncio.run(main())
