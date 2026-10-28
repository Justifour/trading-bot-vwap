#!/usr/bin/env python3
"""
VWAP Deviation Mean-Reversion scalp — canonical backtest.
BTCUSDT 15m. Data = Bybit public API (= TV chart BYBIT:BTCUSDT.P).

Two configs (partial ½@1R BE runner 1:5, stop 0.3%):
  HYBRID  (default) — L: 4H 1σ pen50 ER<0.1 | S: Rolling 20 2σ+pen20 no chop
  CLASSIC           — L+S: 4H 1σ pen20 ER<0.3

Usage:
  python3 backtest.py            # run on existing dec_jun_15m.csv
  python3 backtest.py --refetch  # pull fresh data from Bybit first
"""
import sys, os, json, time, urllib.request, datetime as dt, collections

DATA = os.path.join(os.path.dirname(__file__), "dec_jun_15m.csv")

# ---- shared config ----
STDEV_WIN, MIN_SIGMA, PEN = 100, 0.1, 20
LONG_PEN, SHORT_PEN = 50, 20
ERMAX_CLASSIC, ERMAX_LONG, ERLEN = 0.3, 0.1, 20
VWAP_BUCKET = 4 * 3600
ROLL, BAND2 = 20, 2.0
STOP_PCT, RR = 0.30, 5.0

MAR = int(dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc).timestamp())
JUL = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp())


def refetch(start=1766188800, end=None):
    """Fetch BTCUSDT 15m klines from Bybit into DATA."""
    end = end or int(time.time())
    STEP = 900; bars = {}
    if os.path.exists(DATA):
        for l in open(DATA):
            p = l.split(","); bars[int(p[0])] = tuple(x.strip() for x in p)
        start = max(bars) - STEP
    cur = start
    while cur < end:
        we = min(cur + 1000 * STEP, end)
        u = (f"https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT"
             f"&interval=15&start={cur*1000}&end={we*1000}&limit=1000")
        d = json.load(urllib.request.urlopen(u, timeout=30))["result"]["list"]
        if not d: break
        for r in d:
            t = int(r[0]) // 1000; bars[t] = (str(t), r[1], r[2], r[3], r[4], r[5])
        nw = max(int(r[0]) // 1000 for r in d)
        if nw + STEP <= cur: break
        cur = nw + STEP; time.sleep(0.15)
    rows = [bars[t] for t in sorted(bars)]
    open(DATA, "w").write("".join(",".join(r) + "\n" for r in rows))
    print(f"data: {len(rows)} bars, last {dt.datetime.utcfromtimestamp(int(rows[-1][0]))}")


def load():
    b = []
    for l in open(DATA):
        t, o, h, lo, c, v = l.split(",")
        b.append({"t": int(t), "o": float(o), "h": float(h), "l": float(lo),
                  "c": float(c), "v": float(v)})
    return b


def stdev(v):
    m = sum(v) / len(v); return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5


def er_at(bars, i):
    if i < ERLEN: return None
    chg = abs(bars[i]["c"] - bars[i - ERLEN]["c"])
    vol = sum(abs(bars[j]["c"] - bars[j - 1]["c"]) for j in range(i - ERLEN + 1, i + 1))
    return chg / vol if vol else 0


def indicators_4h(bars, pen=PEN):
    """4H VWAP Percent 1σ+pen% → short/long crosses + ER."""
    n = len(bars); osc = [None] * n; up = [None] * n; lo = [None] * n; er = [None] * n
    cpv = cv = 0.0; bk = None; rp = []
    for i, b in enumerate(bars):
        kk = b["t"] // VWAP_BUCKET
        if kk != bk: cpv = cv = 0.0; bk = kk
        h3 = (b["h"] + b["l"] + b["c"]) / 3
        cpv += h3 * b["v"]; cv += b["v"]; vw = cpv / cv
        rp.append(100 * (h3 / vw - 1)); osc[i] = rp[-1]
        s = stdev(rp[i - STDEV_WIN + 1:i + 1]) if i >= STDEV_WIN - 1 else None
        fb = max(s if s is not None else MIN_SIGMA, MIN_SIGMA)
        up[i] = fb * (1 + pen / 100); lo[i] = -up[i]
        er[i] = er_at(bars, i)
    rS = [False] * n; rL = [False] * n
    for i in range(1, n):
        if None in (osc[i], osc[i - 1], up[i], up[i - 1], lo[i], lo[i - 1]): continue
        rS[i] = osc[i - 1] <= up[i - 1] and osc[i] > up[i]
        rL[i] = osc[i - 1] >= lo[i - 1] and osc[i] < lo[i]
    return rS, rL, er


indicators = indicators_4h   # alias for signals.py parity


def indicators_roll_short(bars):
    """Rolling ROLL-bar VWAP → (BAND2 + SHORT_PEN%)σ short crosses only."""
    n = len(bars); osc = [None] * n; up = [None] * n; rp = []
    for i, b in enumerate(bars):
        s = max(0, i - ROLL + 1)
        cpv = cv = 0.0
        for j in range(s, i + 1):
            bb = bars[j]; h3 = (bb["h"] + bb["l"] + bb["c"]) / 3
            cpv += h3 * bb["v"]; cv += bb["v"]
        vw = cpv / cv if cv else b["c"]
        h3 = (b["h"] + b["l"] + b["c"]) / 3
        rp.append(100 * (h3 / vw - 1)); osc[i] = rp[-1]
        if i >= STDEV_WIN - 1:
            one = max(stdev(rp[i - STDEV_WIN + 1:i + 1]), MIN_SIGMA)
            up[i] = one * (BAND2 + SHORT_PEN / 100.0)
    rS = [False] * n
    for i in range(1, n):
        if up[i] is None or up[i - 1] is None: continue
        rS[i] = rp[i - 1] <= up[i - 1] and rp[i] > up[i]
    return rS


def walk(bars, i, d):
    """Partial ½@1:1 + BE runner to 1:RR."""
    n = len(bars); e = bars[i]["c"]; risk = e * STOP_PCT / 100
    if d == "short": stop = e + risk; tp = e - RR * risk; t1 = e - risk
    else: stop = e - risk; tp = e + RR * risk; t1 = e + risk
    banked = 0.0; cur = stop; phase = 0
    for k in range(i + 1, n):
        H, L = bars[k]["h"], bars[k]["l"]
        adv = (H >= cur) if d == "short" else (L <= cur)
        ftp = (L <= tp) if d == "short" else (H >= tp)
        ft1 = (L <= t1) if d == "short" else (H >= t1)
        if phase == 0:
            if adv: return -1.0, k
            if ft1:
                banked = 0.5; phase = 1; cur = e
                if ftp: return banked + 0.5 * RR, k
        else:
            if adv: return banked, k
            if ftp: return banked + 0.5 * RR, k
    return banked if phase else 0.0, n - 1


def run_classic(bars, rS, rL, er, a, b):
    n = len(bars); T = []; i = 0
    while i < n:
        took = False
        for d, sig in (("short", rS), ("long", rL)):
            if sig[i] and a <= bars[i]["t"] < b and (er[i] or 1) < ERMAX_CLASSIC:
                r, k = walk(bars, i, d); T.append((bars[i]["t"], d, r)); i = k; took = True; break
        i += 1 if not took else 1
    return T


def run_hybrid(bars, rL4, rS_roll, er, a, b, ermax_long=ERMAX_LONG):
    n = len(bars); T = []; i = 0
    while i < n:
        took = False
        if rS_roll[i] and a <= bars[i]["t"] < b:
            r, k = walk(bars, i, "short"); T.append((bars[i]["t"], "short", r)); i = k; took = True
        elif rL4[i] and a <= bars[i]["t"] < b and (er[i] or 1) < ermax_long:
            r, k = walk(bars, i, "long"); T.append((bars[i]["t"], "long", r)); i = k; took = True
        i += 1 if not took else 1
    return T


def report(T, label):
    mo = collections.defaultdict(float)
    by = collections.defaultdict(list)
    for t in T:
        mo[dt.datetime.utcfromtimestamp(t[0]).strftime("%Y-%m")] += t[2]
        by[t[1]].append(t[2])
    N = len(T); wins = sum(1 for t in T if t[2] > 0.001); tot = sum(t[2] for t in T)
    eq = pk = mdd = 0; rl = mx = 0
    for t in T:
        eq += t[2]; pk = max(pk, eq); mdd = min(mdd, eq - pk)
        if t[2] < -0.001: rl += 1; mx = max(mx, rl)
        else: rl = 0
    print(f"\n=== {label} ===")
    for m in sorted(mo): print(f"  {m}: {mo[m]:+.1f}R")
    print(f"  TOTAL: {N} trades | WR {100*wins/N:.1f}% | {tot:+.1f}R | maxDD {mdd:.1f}R | streak {mx}")
    if by:
        print(f"  Long {len(by['long'])}tr {sum(by['long']):+.1f}R | Short {len(by['short'])}tr {sum(by['short']):+.1f}R")


if __name__ == "__main__":
    if "--refetch" in sys.argv: refetch()
    bars = load()
    rS, rL, er = indicators_4h(bars)
    _, rL_hybrid, er = indicators_4h(bars, LONG_PEN)
    rS_roll = indicators_roll_short(bars)
    report(run_hybrid(bars, rL_hybrid, rS_roll, er, MAR, JUL),
           "HYBRID (default): L 4H pen50 ER<0.1 | S roll 2.2σ | RR5")
    report(run_classic(bars, rS, rL, er, MAR, JUL),
           "CLASSIC: both 4H pen20 ER<0.3 | RR5")
