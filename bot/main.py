#!/usr/bin/env python3
"""
Entrypoint — runs the bot loop + Telegram control together.

Modes auto-selected from env:
  - LIGHTER_API_PRIVATE_KEY set -> LIVE
  - no key -> DRY-RUN (signals only)

Run:  python3 main.py
"""
import asyncio
import os
import sys
import traceback
import config as C
from bot import Bot


def _ensure_db_dir():
    parent = os.path.dirname(os.path.abspath(C.DB_PATH))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _has_exec_keys() -> bool:
    return bool(C.LIGHTER_API_PRIVATE_KEY)


def _log_startup():
    mode = "LIVE" if _has_exec_keys() else "DRY-RUN"
    print("=== trading-bot starting ===", flush=True)
    print(f"mode={mode} lighter={C.LIGHTER_BASE_URL} strategy={C.STRATEGY} data={C.DATA_SOURCE}", flush=True)
    if C.LIGHTER_PROXY.strip():
        print(f"lighter_proxy=set", flush=True)
    if C.DATA_SOURCE == "binance" and C.BINANCE_PROXY.strip():
        print(f"binance_proxy=set", flush=True)
    print(f"account={C.LIGHTER_ACCOUNT_INDEX} db={C.DB_PATH} tg={'on' if C.TG_BOT_TOKEN else 'off'} "
          f"chat_id={'set' if C.TG_CHAT_ID else 'unset'}", flush=True)
    if not C.TG_BOT_TOKEN:
        print("WARN: TG_BOT_TOKEN missing — Telegram disabled", flush=True)


async def _connect_exec():
    if not _has_exec_keys():
        print("Lighter: skipped (no key) — DRY-RUN", flush=True)
        return None
    from lighter_exec import LighterExec
    try:
        ex = await LighterExec().connect()
        bal = await ex.balance()
        print(f"Lighter: connected mainnet account={C.LIGHTER_ACCOUNT_INDEX} balance=${bal:,.2f}",
              flush=True)
        return ex
    except Exception as e:
        print(f"WARN: Lighter connect failed ({e}) — continuing without execution", flush=True)
        return None


async def main():
    _ensure_db_dir()
    _log_startup()

    tg = None
    b = Bot(exec_layer=None)
    if C.TG_BOT_TOKEN:
        from tg_bot import TelegramControl
        tg = TelegramControl(b)
        b.notify = lambda m: asyncio.create_task(tg.send(m))
        try:
            await tg.start()
            me = await tg.app.bot.get_me()
            print(f"Telegram: @{me.username} polling started", flush=True)
        except Exception as e:
            msg = str(e)
            if "Conflict" in msg or "terminated by other getUpdates" in msg:
                print("FATAL: Telegram bot already running elsewhere (stop local main.py).", flush=True)
            else:
                print(f"FATAL: Telegram start failed: {e}", flush=True)
            raise
    else:
        print("FATAL: set TG_BOT_TOKEN in Railway Variables", flush=True)
        sys.exit(1)

    exec_layer = await _connect_exec()
    b.exec = exec_layer

    try:
        await b.run()
    finally:
        if tg:
            await tg.stop()
        if exec_layer:
            await exec_layer.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
