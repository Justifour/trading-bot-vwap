"""
Hardcoded configuration for the Lighter VWAP-deviation scalping bot.

Two strategy modes (STRATEGY env):
  hybrid  (default, CHOSEN) — L: 4H 1σ pen50 ER<0.1 | S: Rolling 20 2σ pen20 no chop | runner 1:5
  classic                   — L+S: 4H 1σ pen20 ER<0.3 (validated +76R both-sides; parity reference)

CHOSEN config 2026-06-16 (hybrid): Mar–Jun +61.5R / WR 63% / DD −4.5 / 148 trades / +82% @1%.
Each leg on its optimal anchor (BTC up/down asymmetry): longs→slow 4H+chop (no knives),
shorts→fast Rolling, no chop. ⚠️ hybrid is short-heavy + Mar–Jun-tuned — sanity-check on full
history before scaling real size (all-history it trails classic; pen50/RR5 were fit to Mar–Jun).

Secrets (keys, tokens) come from environment variables — never hardcode them here.
"""
import os

# load bot/.env into the environment if present (simple, no dependency)
_envf = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_envf):
    for _line in open(_envf):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            if _v.strip():                                   # ignore empty placeholders in .env
                os.environ.setdefault(_k.strip(), _v.strip())

# ---------------- Strategy mode ----------------
STRATEGY         = os.getenv("STRATEGY", "hybrid").lower()   # hybrid | classic

# ---------------- Shared indicator params ----------------
SYMBOL_BYBIT     = "BTCUSDT"
TIMEFRAME_MIN    = 15
VWAP_BUCKET_SEC  = 4 * 3600      # 4H anchored VWAP
STDEV_WIN        = 100
MIN_SIGMA        = 0.1
PENETRATION      = 20            # classic both-sides 4H (= backtest parity reference)
LONG_PEN         = 50            # hybrid long: 4H 1σ band × (1 + 50%) → entry at 1.5σ
ER_LEN           = 20
DIRECTION        = "both"        # both | long | short  (live-changeable via /direction)

# Classic: both sides 4H, chop ER < 0.3
ER_MAX_CLASSIC   = 0.3

# Hybrid longs: 4H 1σ pen20, chop ER < 0.1
ER_MAX_LONG      = 0.1

# Hybrid shorts: rolling VWAP, 2σ + pen20 (= 2.2σ) cross, no chop
ROLLING_BARS     = 20
SHORT_BAND_MULT  = 2.0
SHORT_PEN        = 20            # short band = (2 + 20%)σ = 2.2σ

# Alias used by classic parity / backtest reference
ER_MAX           = ER_MAX_CLASSIC

# ---------------- Trade / exit (partial config) ------------------
STOP_PCT         = 0.30
RR               = 5.0           # runner 1:5 → FULL +3.0R, PART +0.5R, LOSS −1R (chosen low-trade config)
PARTIAL_FRAC     = 0.5
PARTIAL_AT_R     = 1.0
BREAKEVEN        = True

# ---------------- Risk ----------------
RISK_PCT             = 1.0
MAX_POSITIONS        = 1
DAILY_LOSS_LIMIT_R   = -5.0
MAX_TRADES_PER_DAY   = 20
SLIPPAGE_PCT         = 0.05
# Lighter exits: SL market cap, TP/BE limit buffers (fraction, not %)
EXIT_SL_BUF          = 0.0005   # 0.05% worst-case beyond stop trigger (market SL cap)
EXIT_BE_BUF          = 0.0003   # 0.03% BE limit beyond avg entry
BE_FALLBACK_PCT      = 0.001    # if still in runner past BE limit by this → market close
POSITION_POLL_SEC    = 5        # faster loop while in a trade

# ---------------- Lighter (mainnet-only at runtime) ----------------
LIGHTER_MAINNET_URL   = "https://mainnet.zklighter.elliot.ai"
_env_lighter_url      = os.getenv("LIGHTER_URL", "").strip()
if _env_lighter_url and _env_lighter_url.rstrip("/") != LIGHTER_MAINNET_URL:
    print(f"WARN: LIGHTER_URL={_env_lighter_url!r} ignored — bot is mainnet-only", flush=True)
LIGHTER_BASE_URL      = LIGHTER_MAINNET_URL
LIGHTER_MARKET_SYMBOL = "BTC"
LIGHTER_ACCOUNT_INDEX = int(os.getenv("LIGHTER_ACCOUNT_INDEX") or "0")
LIGHTER_API_KEY_INDEX = int(os.getenv("LIGHTER_API_KEY_INDEX") or "4")
LIGHTER_API_PRIVATE_KEY = os.getenv("LIGHTER_API_PRIVATE_KEY", "")
# SOCKS5/HTTP proxy with IP outside US/UK/CA/RU/etc — required on Railway (US datacenter)
LIGHTER_PROXY           = os.getenv("LIGHTER_PROXY", "")

LIGHTER_BTC_MARKET_INDEX   = 1
LIGHTER_BTC_SIZE_DECIMALS  = 5
LIGHTER_BTC_PRICE_DECIMALS = 1
LIGHTER_BTC_MIN_BASE       = 0.0002
LIGHTER_BTC_MIN_NOTIONAL   = 10.0

# ---------------- Telegram ----------------
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID", "")

# ---------------- Persistence ----------------
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "trades.db"))

# ---------------- Data source ----------------
# Live default: Binance USDT-M 15m (reliable from cloud; ~2.4× Bybit volume → slightly different VWAP).
# Backtest reference: Bybit CSV + TV BYBIT:BTCUSDT.P. Set DATA_SOURCE=bybit for exact parity.
DATA_SOURCE       = os.getenv("DATA_SOURCE", "binance").lower()
BYBIT_KLINE_URL   = "https://api.bybit.com/v5/market/kline"
BYBIT_PROXY       = os.getenv("BYBIT_PROXY", "")
BINANCE_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_SYMBOL    = "BTCUSDT"
# Railway US → Binance 451; default to same EU proxy as Lighter if BINANCE_PROXY unset
BINANCE_PROXY     = os.getenv("BINANCE_PROXY", "").strip() or LIGHTER_PROXY
HL_INFO_URL       = "https://api.hyperliquid.xyz/info"
HL_COIN           = "BTC"
