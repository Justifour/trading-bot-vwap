#!/usr/bin/env python3
"""Find LIGHTER_ACCOUNT_INDEX by wallet address (no private key needed)."""
import asyncio
import os
import sys
import lighter

BASE = os.getenv("LIGHTER_URL", "https://mainnet.zklighter.elliot.ai")


async def main():
    addr = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("ETH_ADDRESS", "")).strip()
    if not addr:
        raise SystemExit("usage: ETH_ADDRESS=0x... venv/bin/python3 discover_account.py\n"
                           "   or: venv/bin/python3 discover_account.py 0x...")
    api = lighter.ApiClient(configuration=lighter.Configuration(host=BASE))
    try:
        subs = await lighter.AccountApi(api).accounts_by_l1_address(l1_address=addr)
        accounts = getattr(subs, "sub_accounts", None) or getattr(subs, "accounts", []) or []
        if not accounts:
            raise SystemExit(f"Аккаунт не найден для {addr} на {BASE}")
        for a in accounts:
            idx = getattr(a, "index", getattr(a, "account_index", "?"))
            print(f"account_index = {idx}")
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
