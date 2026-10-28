#!/usr/bin/env python3
"""Grid search: mirror L+S setups, no chop vs EMA trend filters. Mar–Jun 2026."""
import backtest as bt
import collections

MAR, JUL = bt.MAR, bt.JUL
ROLL, BAND2, STDEV_WIN, MIN_SIGMA = bt.ROLL, bt.BAND2, bt.STDEV_WIN, bt.MIN_SIGMA

bars = bt.load()
n = len(bars)
closes = [b["c"] for b in bars]


def stdev(v):
    m = sum(v) / len(v)
    return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5


def ema_series(period):
    out = [None] * n
    if n < period:
        return out
    k = 2 / (period + 1)
    out[period - 1] = sum(closes[:period]) / period
    for i in range(period, n):
        out[i] = closes[i] * k + out[i - 1] * (1 - k)
    return out


def roll_pct(roll=ROLL, band=BAND2):
    rp = []
    up = [None] * n
    lo = [None] * n
    for i, b in enumerate(bars):
        s = max(0, i - roll + 1)
        cpv = cv = 0.0
        for j in range(s, i + 1):
            bb = bars[j]
            h3 = (bb["h"] + bb["l"] + bb["c"]) / 3
            cpv += h3 * bb["v"]
            cv += bb["v"]
        vw = cpv / cv if cv else b["c"]
        h3 = (b["h"] + b["l"] + b["c"]) / 3
        rp.append(100 * (h3 / vw - 1))
        if i >= STDEV_WIN - 1:
            one = max(stdev(rp[i - STDEV_WIN + 1 : i + 1]), MIN_SIGMA)
            up[i] = one * band
            lo[i] = -one * band
    rS = [False] * n
    rL = [False] * n
    for i in range(1, n):
        if None in (up[i], up[i - 1], lo[i], lo[i - 1]):
            continue
        rS[i] = rp[i - 1] <= up[i - 1] and rp[i] > up[i]
        rL[i] = rp[i - 1] >= lo[i - 1] and rp[i] < lo[i]
    return rS, rL


def apply_er(rS, rL, er, ermax):
    if ermax is None:
        return rS, rL
    oS, oL = list(rS), list(rL)
    for i in range(n):
        if (er[i] or 1) >= ermax:
            oS[i] = oL[i] = False
    return oS, oL


def apply_ema(rS, rL, ema, mode):
    oS, oL = list(rS), list(rL)
    for i in range(n):
        e = ema[i]
        if e is None:
            oS[i] = oL[i] = False
            continue
        c = closes[i]
        if mode == "aligned":
            if c <= e:
                oL[i] = False
            if c >= e:
                oS[i] = False
        elif mode == "counter":
            if c >= e:
                oL[i] = False
            if c <= e:
                oS[i] = False
        elif mode == "slope":
            if i < 1 or ema[i - 1] is None:
                oS[i] = oL[i] = False
                continue
            up = ema[i] > ema[i - 1]
            if not up:
                oL[i] = False
            if up:
                oS[i] = False
        elif mode == "dist":
            # skip if too far from EMA (extended trend leg)
            dist = abs(c - e) / e
            if dist > 0.005:
                oS[i] = oL[i] = False
    return oS, oL


def run_hybrid_long(rL4, er):
    T = []
    i = 0
    while i < n:
        took = False
        if rL4[i] and MAR <= bars[i]["t"] < JUL and (er[i] or 1) < bt.ERMAX_LONG:
            r, k = bt.walk(bars, i, "long")
            T.append((bars[i]["t"], "long", r))
            i = k
            took = True
        i += 1 if not took else 1
    return T


def run_trades(rS, rL):
    T = []
    i = 0
    while i < n:
        took = False
        for d, sig in (("short", rS), ("long", rL)):
            if sig[i] and MAR <= bars[i]["t"] < JUL:
                r, k = bt.walk(bars, i, d)
                T.append((bars[i]["t"], d, r))
                i = k
                took = True
                break
        i += 1 if not took else 1
    return T


def merge_hybrid(rS_roll, rL_src, er=None, ermax_long=None):
    """Short from roll + long from rL_src with optional ER on long only."""
    rL = list(rL_src)
    if ermax_long is not None:
        for i in range(n):
            if (er[i] or 1) >= ermax_long:
                rL[i] = False
    T = []
    i = 0
    while i < n:
        took = False
        if rS_roll[i] and MAR <= bars[i]["t"] < JUL:
            r, k = bt.walk(bars, i, "short")
            T.append((bars[i]["t"], "short", r))
            i = k
            took = True
        elif rL[i] and MAR <= bars[i]["t"] < JUL:
            r, k = bt.walk(bars, i, "long")
            T.append((bars[i]["t"], "long", r))
            i = k
            took = True
        i += 1 if not took else 1
    return T


def summarize(T):
    if not T:
        return dict(N=0, R=0, ev=0, wr=0, dd=0, streak=0, lR=0, sR=0, lN=0, sN=0)
    rs = [t[2] for t in T]
    N = len(rs)
    tot = sum(rs)
    wins = sum(1 for r in rs if r > 0.001)
    eq = pk = mdd = 0
    rl = mx = 0
    for r in rs:
        eq += r
        pk = max(pk, eq)
        mdd = min(mdd, eq - pk)
        if r < -0.001:
            rl += 1
            mx = max(mx, rl)
        else:
            rl = 0
    by = collections.defaultdict(list)
    for t in T:
        by[t[1]].append(t[2])
    return dict(
        N=N, R=tot, ev=tot / N, wr=100 * wins / N, dd=mdd, streak=mx,
        lR=sum(by["long"]), sR=sum(by["short"]), lN=len(by["long"]), sN=len(by["short"]),
    )


def test(label, T):
    results.append((label, summarize(T)))


def main():
    global results
    results = []
    rS4, rL4, er = bt.indicators_4h(bars)
    rSr, rLr = roll_pct()
    emas = {p: ema_series(p) for p in (9, 20, 50, 100, 200)}

    test("HYBRID baseline: L 4H ER0.1 + S roll2σ", bt.run_hybrid(bars, rL4, bt.indicators_roll_short(bars), er, MAR, JUL))
    test("ROLL mirror pct: L+S roll2σ no filter", run_trades(rSr, rLr))
    test("4H mirror: L+S 1σ pen20 no chop", run_trades(*apply_er(rS4, rL4, er, None)))

    for p in (20, 50, 100, 200):
        for mode in ("aligned", "counter", "slope"):
            rS, rL = apply_ema(rSr, rLr, emas[p], mode)
            test(f"ROLL2σ + EMA{p} {mode}", run_trades(rS, rL))

    for p in (50, 100, 200):
        for mode in ("aligned", "counter"):
            rS, rL = apply_ema(rS4, rL4, emas[p], mode)
            test(f"4H pen20 + EMA{p} {mode} no chop", run_trades(rS, rL))

    for p in (50, 100, 200):
        for mode in ("aligned", "counter"):
            _, rL = apply_ema([False] * n, rLr, emas[p], mode)
            test(f"S roll2σ + L roll2σ EMA{p} {mode} (long filt)", merge_hybrid(rSr, rL))

    test("S roll2σ + L roll2σ mirror no filt", merge_hybrid(rSr, rLr))
    test("S roll2σ + L 4H NO chop", merge_hybrid(rSr, rL4))

    for p in (50, 100):
        _, rL = apply_ema([False] * n, rL4, emas[p], "aligned")
        test(f"S roll2σ + L 4H EMA{p} aligned", merge_hybrid(rSr, rL))
        _, rL = apply_ema([False] * n, rL4, emas[p], "counter")
        test(f"S roll2σ + L 4H EMA{p} counter", merge_hybrid(rSr, rL))

    results.sort(key=lambda x: -x[1]["R"])

    print("Mar–Jun 2026 | partial ½@1R BE runner 1:4 | top mirror / EMA configs\n")
    print(f"{'config':<48} {'N':>4} {'R':>7} {'EV':>6} {'WR':>5} {'DD':>6} {'L_R':>6} {'S_R':>6}")
    print("-" * 95)
    for label, s in results[:25]:
        print(f"{label:<48} {s['N']:4d} {s['R']:+7.1f} {s['ev']:+6.2f} {s['wr']:4.0f}% {s['dd']:6.1f} "
              f"{s['lR']:+6.1f} {s['sR']:+6.1f}")

    hybrid = next(s for l, s in results if "HYBRID baseline" in l)
    print(f"\nHYBRID baseline reference: {hybrid['R']:+.1f}R | L {hybrid['lR']:+.1f} S {hybrid['sR']:+.1f}")

    best = results[0]
    print(f"\nBest in grid: {best[0]}")
    print(f"  {best[1]['R']:+.1f}R | L {best[1]['lN']}tr {best[1]['lR']:+.1f}R | S {best[1]['sN']}tr {best[1]['sR']:+.1f}R")

    mirror = [x for x in results if "ROLL mirror" in x[0] or ("ROLL2σ" in x[0] and "L 4H" not in x[0] and "long filt" not in x[0])]
    mirror.sort(key=lambda x: -x[1]["R"])
    print("\nTop pure roll-mirror variants:")
    for label, s in mirror[:8]:
        print(f"  {label}: {s['R']:+.1f}R (L{s['lR']:+.1f}/S{s['sR']:+.1f})")


if __name__ == "__main__":
    main()
