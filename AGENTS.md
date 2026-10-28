# AGENTS.md — trading-bot project context

Context handoff for any agent picking up this project. Read this first.

## What this project is
Automating a single trading strategy end-to-end: **VWAP Deviation Mean-Reversion scalp on BTC**.
The owner trades it manually on **Lighter** (perp DEX, 0 fees) and is building a bot to run it.

Three parts:
1. **`indicators/*.pine`** — TradingView indicators (separate **long** + **short** signal tools).
2. **`backtest/`** — Python backtest engine + Bybit data + the research that validated the edge.
3. **`bot/`** — the automated bot: Bybit signal → Lighter execution → Telegram control.

---

## The strategy (LOCKED — validated, don't relitigate)
- **Instrument:** BTC, **15m** chart (`BYBIT:BTCUSDT.P` / Hyperliquid BTC).
- **Two modes** (`STRATEGY` env, default **`hybrid`**):

| | **HYBRID (default, CHOSEN 2026-06-16)** | **CLASSIC** (`STRATEGY=classic`) |
|---|---|---|
| Long | 4H VWAP · Percent · 1σ+**pen50%** (=1.5σ) · cross · **ER < 0.1** | 4H · 1σ+pen20% · **ER < 0.3** |
| Short | Rolling 20 bar · Percent · **2σ+pen20%** (=2.2σ) cross · **no chop** | 4H · 1σ+pen20% · **ER < 0.3** |

- **Direction:** both (live-changeable to long/short). Long checked first (matches backtest mix order).
- **Entry:** market on the signal candle (confirmed close).
- **Stop:** **0.3%**.
- **Exit (partial):** ½ @ **+1R**, runner stop → **BE**, runner target **1:5** (`C.RR=5`, global).
  Outcomes: **FULL +3.0R**, **PART +0.5R**, **LOSS −1R**.

### Backtest performance (Mar–Jun 2026, partial, Bybit 15m; Jun partial)
| Config | Trades | WR | R | maxDD | all-history |
|---|---|---|---|---|---|
| **Hybrid (default, CHOSEN, RR5)** | 148 | 63% | **+61.5R** | −4.5R | +70R / DD −21 |
| **Classic both 4H (RR4 ref)** | 319 | 55% | **+62.5R** | −7.0R | **+74.5R / DD −14.5** |

$1000 @1% risk, hybrid Mar–Jun → **$1820 (+82%)**.

### Research conclusions (don't re-test)
- **BTC up/down ASYMMETRY (core insight):** longs want the **SLOW 4H anchor + chop** (Rolling longs are a
  DISASTER, −17R Mar–Jun / −28R all — they catch falling knives); shorts want the **FAST Rolling-20, NO
  chop** (robust +40.5R all-history, green 6/7 months where 4H shorts lose; chop KILLS it +42→+12R). Each
  leg on its own anchor.
- **Penetration asymmetry:** longs **pen50** (deep 1.5σ dip = fewer, cleaner) ; shorts **pen20** (2.2σ).
  **RR5 + pen50** lift EV ×2.6 and cut trades ~60% at ≈same Mar–Jun R (user goal: fewer trades, same +60R).
- ⚠️ **Hybrid is short-heavy (~70% of R) → regime-dependent (bearish/chop); pen50+RR5 are Mar–Jun-tuned.**
  All-history it TRAILS classic (+70R vs +74.5R, DD −21 vs −14.5). **Classic both-4H = all-weather.**
  Validate hybrid on full history before scaling real size.
- **chop ONLY on longs** (the knife-saver); shorts never use chop.
- **Limit-retrace entry LOSES** — enter at signal.
- **Alts don't work:** SOL ≈ breakeven, HYPE negative. BTC-specific edge.
- **Needs 0/maker fees** — taker fees at 0.3% stop kill it (Lighter 0/0 essential).
- Full detail: `backtest/README.md`.

### Venues & order routing
- **Signal source:** Binance USDT-M 15m (default `DATA_SOURCE=binance`, reliable from cloud). Backtest ref = Bybit CSV / TV. `bybit` | `hyperliquid` optional.
- **Execution: Lighter mainnet** (`mainnet.zklighter.elliot.ai`). 0 maker / 0 taker.
  BTC market_id=1, size×1e5, price×10, min 0.0002 BTC / $10.
- Exits: **SL Stop Market**, **TP Limit**, after partial **BE resting Limit GTC** @ entry±0.03%,
  **BE fallback market** if price passes entry±0.13% without fill.

---

## Repo layout
```
trading-bot/
├── indicators/             # TradingView pine indicators (params LOCKED; customize by hand after backtest)
│   ├── vwap-deviation-long.pine    # "VWAP Deviation Scalp Long"  — 4H % 1σ pen50 chop0.1
│   ├── vwap-deviation-short.pine   # "VWAP Deviation Scalp Short" — Rolling20 % 2σ pen20 noChop
│   └── vwap-range-bands.pine       # bands overlay (visual context, matched to oscillator math)
├── backtest/
│   ├── backtest.py         # canonical reusable backtester (the reference for signal parity)
│   ├── dec_jun_15m.csv     # BTC 15m data (refetch via Bybit API; gets stale)
│   └── README.md           # strategy spec + research conclusions
└── bot/
    ├── config.py           # hardcoded params + auto-loads bot/.env
    ├── signals.py          # signal engine: hybrid (default) or classic 4H
    ├── lighter_exec.py     # Lighter execution layer (market entry + server-side SL + 2 TP + BE)
    ├── risk.py             # one-pos-at-a-time, daily-loss limit, kill-switch, sqlite log → /status
    ├── bot.py              # main loop: signal → plan_trade → execute → BE monitor
    ├── tg_bot.py           # Telegram panel: /start /stop /status /direction /flatten /balance /help
    ├── main.py             # entrypoint (auto dry-run/live + telegram from env)
    ├── setup_api_key.py    # one-time Lighter API key generate+register
    ├── venv/               # has lighter SDK + python-telegram-bot
    ├── .env                # secrets (TG token set; Lighter keys empty). gitignored.
    └── README.md
```

---

## Bot build state (phases)
- **Phase 1 — signal engine** ✅ DONE. `signals.py` produces signals **bit-for-bit identical** to
  `backtest.py` over 16k bars (validated). Live mode drops the forming candle correctly.
- **Phase 2 — Lighter execution** ✅ SL market, TP limit, resting BE limit, fallback.
- **Phase 3 — Telegram** ✅ Railway + mainnet.
- **Phase 4 — risk + main loop** ✅ monitor, reconcile, classify from fills.
- **Phase 5 — mainnet live** 🔄 $100 @ 1% risk on Railway; validate first trades vs backtest.

---

## How to run
⚠️ **ALWAYS run from `bot/`** (the venv lives there). The owner repeatedly lands in the parent dir.
```bash
cd /Users/ev.andreev/Downloads/trading-bot/bot

# dry-run (no keys): watch the brain catch live signals
venv/bin/python3 bot.py --run

# full app (auto-selects dry-run/live + Telegram from .env)
venv/bin/python3 main.py

# self-tests / validation (no keys)
venv/bin/python3 signals.py        # parity vs backtest
venv/bin/python3 risk.py           # risk manager + /status format
venv/bin/python3 lighter_exec.py   # sizing/scaling math
venv/bin/python3 bot.py            # plan_trade levels
```
`.env` empty values are ignored (defaults apply). No Lighter key → dry-run. No TG token → console only.

---

## Next steps to go live
1. ✅ Mainnet API key via `setup_api_key.py`, Railway env set.
2. Deploy latest bot → `/start` in Telegram.
3. Compare first mainnet trades to backtest; tune only if systematic slippage.

---

## Gotchas / conventions
- **Run from `bot/`.** venv = `bot/venv/`; commands use `venv/bin/python3`.
- **Bybit public API** (`api.bybit.com/v5/market/kline`, linear, BTCUSDT, interval=15) = the chart,
  bar-for-bar. Data CSVs go stale — refetch. TradingView MCP is unreliable; use the Bybit API.
- **Secrets** live only in `bot/.env` (gitignored). Don't write live tokens/keys into files for the owner —
  hand them the command/lines and let them paste.
- The bot's **classic 4H signals** must stay in sync with `backtest/backtest.py:indicators_4h()` —
  re-run `signals.py` to confirm parity. Hybrid roll-short path is validated separately.

## Owner's persistent memory (richer context)
Anthropic auto-memory at `~/.claude/projects/-Users-ev-andreev-Downloads-trading-bot/memory/` holds the
full strategy spec, venue/fee facts, and research conclusions (files: `strategy-vwap-deviation-retest.md`,
`vwap-sigma-band-stats.md`, `approved-trend-indicator-version.md`, `drawing-use-my-theme.md`).
