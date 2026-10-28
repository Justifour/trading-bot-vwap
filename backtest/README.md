# VWAP Deviation Mean-Reversion Scalp — Backtest

Двусторонний фейд отклонения VWAP на **BYBIT:BTCUSDT.P · 15m**. Исполнение — **Lighter (0 комиссий)**.

## Два конфига (бот: `STRATEGY=hybrid|classic`)

Общее: partial **½@1:1 + BE runner 1:4**, стоп **0.3%**, одна позиция, short→long на одном баре.

| | **HYBRID (default)** | **CLASSIC** |
|---|---|---|
| Long | 4H VWAP · 1σ + pen20% · cross · **ER < 0.1** | 4H · 1σ + pen20% · **ER < 0.3** |
| Short | Rolling **20 bar** · **±2σ** cross · **без chop** | 4H · 1σ + pen20% · **ER < 0.3** |

### Mar–Jun 2026 (partial 1:4, Bybit 15m; Jun неполный — CSV ~14 Jun)

| | Trades | WR | R | maxDD | streak |
|---|---|---|---|---|---|
| **Hybrid** | 168 | 63.7% | **+58.5R** | −4.0R | 3 |
| **Classic** | 319 | 54.9% | **+62.5R** | −7.0R | 7 |

Исходы: FULL **+2.5R** · PART **+0.5R** · LOSS **−1R**.

## Файлы
| Файл | Что |
|---|---|
| `backtest.py` | Канонический скрипт (hybrid + classic). `python3 backtest.py [--refetch]` |
| `dec_jun_15m.csv` | BTCUSDT 15m (t,o,h,l,c,v). Refetch через `--refetch` |

## Запуск
```bash
python3 backtest.py            # на существующих данных
python3 backtest.py --refetch  # дотянуть свежие бары с Bybit
```

## Ключевые выводы
- **4H VWAP** — оптимум для лонгов / classic both sides.
- **Rolling 2σ shorts** (без chop) + selective longs ER<0.1 — лучший EV и просадка, меньше сделок.
- **Classic both 4H ER<0.3** — больше R суммарно, но lumpier (DD −7R vs −4R).
- **Limit-retrace entry LOSES** — вход на сигнале.
- **Alts don't work** (SOL ≈ 0, HYPE −). Нужны **0 fees** (Lighter).
