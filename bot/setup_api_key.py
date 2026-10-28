#!/usr/bin/env python3
"""
ONE-TIME Lighter API-key setup. Run LOCALLY with your wallet key.

Generates a fresh API keypair, registers its PUBLIC key on Lighter under your account using your
L1 (Ethereum) private key, and prints the values for the bot's .env. The bot then uses only the
generated API key — your wallet key is used once here and is NEVER stored by the bot.

Usage (mainnet):
    export ETH_PRIVATE_KEY=0x...     # wallet private key (used once, not stored by bot)
    export ETH_ADDRESS=0x...         # same wallet as Lighter UI
    export LIGHTER_URL=https://mainnet.zklighter.elliot.ai
    export API_KEY_INDEX=4           # 0-3 reserved for UI; pick >= 4
    venv/bin/python3 setup_api_key.py
"""
import os, asyncio, lighter

# compat: hexbytes >=1.2 removed to_0x_hex(); lighter-sdk still calls it
try:
    from hexbytes import HexBytes
    if not hasattr(HexBytes, "to_0x_hex"):
        def _to_0x_hex(self):
            h = self.hex()
            return h if h.startswith("0x") else "0x" + h
        HexBytes.to_0x_hex = _to_0x_hex
except ImportError:
    pass

# load bot/.env (same as config.py) — for one-time ETH_PRIVATE_KEY / ETH_ADDRESS
_envf = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_envf):
    for _line in open(_envf):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            if _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip())

ETH_PRIVATE_KEY = os.environ.get("ETH_PRIVATE_KEY", "")
ETH_ADDRESS     = os.environ.get("ETH_ADDRESS", "")
BASE_URL        = os.getenv("LIGHTER_URL", "https://mainnet.zklighter.elliot.ai")
API_KEY_INDEX   = int(os.getenv("API_KEY_INDEX", "4"))


async def main():
    if not ETH_PRIVATE_KEY or not ETH_ADDRESS:
        raise SystemExit(
            "Нужны ETH_PRIVATE_KEY и ETH_ADDRESS.\n"
            "Добавь в bot/.env (локально, не в git):\n"
            "  ETH_PRIVATE_KEY=0x...\n"
            "  ETH_ADDRESS=0x101820e790f6df4920e7e3bb46af0b1eed8e3762\n"
            "Или: export ETH_PRIVATE_KEY=0x... ETH_ADDRESS=0x..."
        )
    api = lighter.ApiClient(configuration=lighter.Configuration(host=BASE_URL))

    # 1) resolve account_index from the wallet address
    subs = await lighter.AccountApi(api).accounts_by_l1_address(l1_address=ETH_ADDRESS)
    accounts = getattr(subs, "sub_accounts", None) or getattr(subs, "accounts", []) or []
    if not accounts:
        raise SystemExit(f"No Lighter account for {ETH_ADDRESS} on {BASE_URL}. "
                         "Create & fund an account in the Lighter UI first.")
    account_index = int(getattr(accounts[0], "index", getattr(accounts[0], "account_index", -1)))
    print(f"account_index = {account_index}")

    # 2) generate a fresh API keypair — the PRIVATE key here is what the BOT will use
    priv, pub, err = lighter.create_api_key()
    if err:
        raise SystemExit(f"create_api_key failed: {err}")

    # 3) register the new public key on-chain, signed once by the wallet (ETH) key
    signer = lighter.SignerClient(url=BASE_URL, account_index=account_index,
                                  api_private_keys={API_KEY_INDEX: priv})
    resp, err = await signer.change_api_key(eth_private_key=ETH_PRIVATE_KEY, new_pubkey=pub,
                                            api_key_index=API_KEY_INDEX)
    if err:
        raise SystemExit(f"change_api_key failed: {err}")
    print("register tx submitted:", resp)

    await asyncio.sleep(3)               # let the registration tx land
    chk = signer.check_client()
    print("signer check:", chk or "OK — key active")

    print("\n================ PUT THESE IN bot/.env (KEEP SECRET) ================")
    print(f"LIGHTER_URL={BASE_URL}")
    print(f"LIGHTER_ACCOUNT_INDEX={account_index}")
    print(f"LIGHTER_API_KEY_INDEX={API_KEY_INDEX}")
    print(f"LIGHTER_API_PRIVATE_KEY={priv}")
    print("=====================================================================")
    print("Your ETH key was used only to register and is NOT stored by the bot.")

    await signer.close()
    await api.close()


if __name__ == "__main__":
    asyncio.run(main())
