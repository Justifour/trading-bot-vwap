# VWAP-Deviation Scalp Bot

Automated bot: **hybrid selective** (L 4H ER<0.1 + S roll 2σ).
Signal from **Binance USDT-M 15m** (default, cloud-friendly) · **execution: Lighter mainnet** · Telegram control.

## Files
| File | What |
|---|---|
| `config.py` | Strategy + risk + Lighter |
| `signals.py` | Hybrid / classic signal engine |
| `lighter_exec.py` | Market entry, SL market, TP limit, resting BE limit |
| `bot.py` | Main loop + BE monitor + reconcile |
| `tg_bot.py` | Telegram `/start` `/stop` `/status` `/flatten` `/balance` |
| `main.py` | Entrypoint (bot + TG) |
| `setup_api_key.py` | One-time Lighter API key registration |

## Local setup
```bash
cd bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill LIGHTER_* + TG_*
```

### Lighter mainnet API key
1. Account on [app.lighter.xyz](https://app.lighter.xyz) → fund (min ~$10/order).
2. One-time locally:
   ```bash
   # add ETH_ADDRESS + ETH_PRIVATE_KEY to .env (wallet, used once)
   venv/bin/python3 setup_api_key.py
   ```
3. Copy printed `LIGHTER_*` into `.env`; remove `ETH_*` lines.

Verify:
```bash
venv/bin/python3 discover_account.py
venv/bin/python3 lighter_exec.py    # sizing selftest
venv/bin/python3 main.py            # LIVE if key set
```

## Exit lifecycle (Lighter)
| Leg | Type |
|-----|------|
| Entry | Market |
| Stop −0.3% | Stop Market |
| TP ½ + runner | TP Limit @ +1R / +4R |
| BE after partial | Resting Limit GTC @ entry ± 0.03% |
| BE fallback | Market if price passes entry ± 0.13% without fill |

## Railway deploy
Root `Dockerfile` builds `bot/`. Push to GitHub → Railway auto-deploy.

**Variables:**
```
LIGHTER_URL=https://mainnet.zklighter.elliot.ai
LIGHTER_ACCOUNT_INDEX=
LIGHTER_API_KEY_INDEX=4
LIGHTER_API_PRIVATE_KEY=
TG_BOT_TOKEN=
TG_CHAT_ID=
DB_PATH=/data/trades.db
```

Volume mounted at `/data`. Only one instance per `TG_BOT_TOKEN` (no local + Railway together).

**After deploy:** `/ping` → set `TG_CHAT_ID` → `/start` to enable trading.

## Manual E2E (mainnet, min size)
```bash
venv/bin/python3 test_trade.py check
venv/bin/python3 test_trade.py long --yes   # real $ — min ~$10
venv/bin/python3 test_trade.py flatten --yes
```

⚠️ Never commit `.env`.
