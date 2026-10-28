#!/usr/bin/env python3
"""
Signal engine for the Lighter VWAP-deviation scalp bot.

Two strategy modes (config.STRATEGY):
  hybrid  (default): long = 4H 1σ pen20 ER<0.1; short = Rolling 20 ±2σ no chop
  classic:           long + short = 4H 1σ pen20 ER<0.3

All signals: confirmed (closed) bar cross; entry reference = signal-bar close.

Data source: Binance BTCUSDT perp 15m (default — reliable from Railway/cloud).
Backtest reference remains Bybit CSV / TV BYBIT:BTCUSDT.P (~2.4× volume diff vs Binance).
Set DATA_SOURCE=bybit|hyperliquid to override.

Usage:
    python3 signals.py            # indicator parity vs ../backtest/backtest.py (Bybit CSV)
    python3 signals.py --live     # seed from live feed and print latest closed-bar signal
"""
from __future__ import annotations
import json
import os
import time, datetime as dt
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import requests

try:
    from curl_cffi import requests as _curl
    _HAS_CURL = True
except ImportError:
    _HAS_CURL = False

import config as C

# Railway/cloud IPs hit Cloudflare TLS fingerprinting on plain requests.
_BYBIT_HOSTS = ("https://api.bybit.com", "https://api.bytick.com")
_CURL_IMPERSONATE = ("chrome124", "chrome120", "chrome110")

_BYBIT = requests.Session()
_BYBIT.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bybit.com/",
})


def _bars_cache_path() -> str:
    base = os.path.dirname(os.path.abspath(C.DB_PATH))
    return os.path.join(base or ".", "bars_15m_cache.json")


def _load_bars_cache() -> list[dict] | None:
    path = _bars_cache_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) and data else None
    except Exception:
        return None


def _save_bars_cache(bars: list[dict]):
    if not bars:
        return
    path = _bars_cache_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bars, f)
    os.replace(tmp, path)


def _swap_host(url: str, host: str) -> str:
    p = urlparse(url)
    return urlunparse(p._replace(netloc=urlparse(host).netloc))


def _http_get(url: str, timeout: int = 30):
    """GET with curl_cffi TLS impersonation (primary) or requests (fallback)."""
    proxy = C.BYBIT_PROXY.strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last_err: Exception | None = None

    if _HAS_CURL:
        for imp in _CURL_IMPERSONATE:
            try:
                return _curl.get(url, impersonate=imp, timeout=timeout, proxies=proxies)
            except Exception as e:
                last_err = e

    try:
        return _BYBIT.get(url, timeout=timeout, proxies=proxies)
    except requests.RequestException as e:
        raise last_err or e


def _bybit_json(url: str, retries: int = 10) -> dict:
    """Bybit public API GET with TLS impersonation, host rotation, retry."""
    last_err: Exception | None = None
    for attempt in range(retries):
        trial = _swap_host(url, _BYBIT_HOSTS[attempt % len(_BYBIT_HOSTS)])
        try:
            resp = _http_get(trial, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            if body.get("retCode", 0) != 0:
                raise RuntimeError(f"Bybit retCode={body.get('retCode')} {body.get('retMsg')}")
            return body
        except (requests.HTTPError, RuntimeError, requests.RequestException) as e:
            last_err = e
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (403, 429, 503) or isinstance(e, requests.RequestException):
                time.sleep(min(2 ** attempt, 30))
                continue
            raise
    raise last_err or RuntimeError("Bybit request failed")


def _parse_klines(raw: list) -> list[dict]:
    bars = []
    for r in raw:
        t = int(r[0]) // 1000
        bars.append({"t": t, "o": float(r[1]), "h": float(r[2]),
                     "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
    return sorted(bars, key=lambda b: b["t"])


# ------------------------------ data ------------------------------
def fetch_bybit_15m_recent(limit: int = 1000) -> list[dict]:
    """Latest N closed 15m bars — single API call (no start/end pagination)."""
    url = (f"{C.BYBIT_KLINE_URL}?category=linear&symbol={C.SYMBOL_BYBIT}"
           f"&interval={C.TIMEFRAME_MIN}&limit={limit}")
    return _parse_klines(_bybit_json(url)["result"]["list"])


def fetch_bybit_15m(start: int, end: int | None = None) -> list[dict]:
    """Fetch BTCUSDT 15m klines in [start, end). Prefer fetch_bybit_15m_recent when possible."""
    end = end or int(time.time())
    step = C.TIMEFRAME_MIN * 60
    need = max(1, (end - start) // step)
    if need <= 1000:
        recent = fetch_bybit_15m_recent(min(1000, need + 5))
        return [b for b in recent if start <= b["t"] < end]
    bars: dict[int, dict] = {}
    cur = start
    while cur < end:
        we = min(cur + 1000 * step, end)
        url = (f"{C.BYBIT_KLINE_URL}?category=linear&symbol={C.SYMBOL_BYBIT}"
               f"&interval={C.TIMEFRAME_MIN}&start={cur*1000}&end={we*1000}&limit=1000")
        data = _bybit_json(url)["result"]["list"]
        if not data:
            break
        for b in _parse_klines(data):
            if start <= b["t"] < end:
                bars[b["t"]] = b
        nw = max(int(r[0]) // 1000 for r in data)
        if nw + step <= cur:
            break
        cur = nw + step
        time.sleep(0.25)
    return [bars[t] for t in sorted(bars)]


def _hl_post(start_ms: int, end_ms: int) -> list:
    resp = requests.post(
        C.HL_INFO_URL,
        json={"type": "candleSnapshot", "req": {
            "coin": C.HL_COIN, "interval": f"{C.TIMEFRAME_MIN}m",
            "startTime": start_ms, "endTime": end_ms,
        }},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, list):
        raise RuntimeError(f"Hyperliquid unexpected response: {repr(body)[:200]}")
    return body


def _parse_hl_candles(raw: list) -> list[dict]:
    bars = [{"t": int(c["t"]) // 1000,
             "o": float(c["o"]), "h": float(c["h"]),
             "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])}
            for c in raw]
    return sorted(bars, key=lambda b: b["t"])


def fetch_hl_15m_recent(bars_back: int = 1000) -> list[dict]:
    """Latest N 15m bars from Hyperliquid (single POST, cloud-friendly)."""
    step = C.TIMEFRAME_MIN * 60
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - bars_back * step * 1000
    return _parse_hl_candles(_hl_post(start_ms, end_ms))


def _parse_binance_klines(raw: list) -> list[dict]:
    bars = [{"t": int(r[0]) // 1000,
             "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
            for r in raw]
    return sorted(bars, key=lambda b: b["t"])


def _binance_klines(params: dict, retries: int = 5) -> list:
    """Binance USDT-M klines. SOCKS proxy → curl_cffi (requests needs PySocks)."""
    proxy = C.BINANCE_PROXY.strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            if proxy and _HAS_CURL:
                resp = _curl.get(
                    C.BINANCE_KLINE_URL, params=params, timeout=30, proxies=proxies,
                    impersonate=_CURL_IMPERSONATE[attempt % len(_CURL_IMPERSONATE)],
                )
            else:
                resp = requests.get(C.BINANCE_KLINE_URL, params=params, timeout=30,
                                    proxies=proxies)
            resp.raise_for_status()
            body = resp.json()
            if isinstance(body, dict) and body.get("code"):
                raise RuntimeError(f"Binance code={body.get('code')} {body.get('msg')}")
            if not isinstance(body, list):
                raise RuntimeError(f"Binance unexpected response: {repr(body)[:200]}")
            return body
        except (requests.HTTPError, RuntimeError, requests.RequestException) as e:
            last_err = e
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (418, 429, 451, 503) or isinstance(e, requests.RequestException):
                time.sleep(min(2 ** attempt, 30))
                continue
            raise
        except Exception as e:
            last_err = e
            time.sleep(min(2 ** attempt, 30))
            continue
    raise last_err or RuntimeError("Binance request failed")


def fetch_binance_15m_recent(limit: int = 1000) -> list[dict]:
    """Latest N closed 15m bars from Binance USDT-M futures."""
    return _parse_binance_klines(_binance_klines({
        "symbol": C.BINANCE_SYMBOL,
        "interval": f"{C.TIMEFRAME_MIN}m",
        "limit": min(limit, 1500),
    }))


def fetch_15m_recent(limit: int = 1000) -> list[dict]:
    if C.DATA_SOURCE == "bybit":
        return fetch_bybit_15m_recent(limit)
    if C.DATA_SOURCE == "binance":
        return fetch_binance_15m_recent(limit)
    return fetch_hl_15m_recent(limit)


# --------------------------- indicators ---------------------------
def _stdev(v: list[float]) -> float:
    m = sum(v) / len(v)
    return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5


def compute_indicators_4h(bars: list[dict], pen: float | None = None):
    """4H VWAP → (osc, up, lo, er). pen = penetration % past the 1σ band.
    Default = C.PENETRATION (classic/parity); hybrid long passes C.LONG_PEN.
    Math identical to backtest.py.indicators_4h() when pen == C.PENETRATION."""
    pen = C.PENETRATION if pen is None else pen
    n = len(bars)
    osc = [None] * n; up = [None] * n; lo = [None] * n; er = [None] * n
    cpv = cv = 0.0; bucket = None; rp: list[float] = []
    for i, b in enumerate(bars):
        k = b["t"] // C.VWAP_BUCKET_SEC
        if k != bucket:
            cpv = cv = 0.0; bucket = k
        h3 = (b["h"] + b["l"] + b["c"]) / 3
        cpv += h3 * b["v"]; cv += b["v"]
        vwap = cpv / cv if cv else b["c"]
        rp.append(100 * (h3 / vwap - 1)); osc[i] = rp[-1]
        s = _stdev(rp[i - C.STDEV_WIN + 1:i + 1]) if i >= C.STDEV_WIN - 1 else None
        fb = max(s if s is not None else C.MIN_SIGMA, C.MIN_SIGMA)
        up[i] = fb * (1 + pen / 100); lo[i] = -up[i]
        if i >= C.ER_LEN:
            chg = abs(b["c"] - bars[i - C.ER_LEN]["c"])
            vol = sum(abs(bars[j]["c"] - bars[j - 1]["c"]) for j in range(i - C.ER_LEN + 1, i + 1))
            er[i] = chg / vol if vol else 0.0
    return osc, up, lo, er


compute_indicators = compute_indicators_4h   # alias


def compute_roll_short(bars: list[dict]):
    """Rolling ROLLING_BARS VWAP → (osc, up_band, rS). Band = (SHORT_BAND_MULT + SHORT_PEN%)σ, no chop."""
    n = len(bars)
    osc = [None] * n
    up = [None] * n
    rp: list[float] = []
    for i, b in enumerate(bars):
        s = max(0, i - C.ROLLING_BARS + 1)
        cpv = cv = 0.0
        for j in range(s, i + 1):
            bb = bars[j]
            h3 = (bb["h"] + bb["l"] + bb["c"]) / 3
            cpv += h3 * bb["v"]
            cv += bb["v"]
        vw = cpv / cv if cv else b["c"]
        h3 = (b["h"] + b["l"] + b["c"]) / 3
        rp.append(100 * (h3 / vw - 1))
        osc[i] = rp[-1]
        if i >= C.STDEV_WIN - 1:
            one = max(_stdev(rp[i - C.STDEV_WIN + 1:i + 1]), C.MIN_SIGMA)
            up[i] = one * (C.SHORT_BAND_MULT + C.SHORT_PEN / 100.0)
    rS = [False] * n
    for i in range(1, n):
        if up[i] is None or up[i - 1] is None:
            continue
        rS[i] = rp[i - 1] <= up[i - 1] and rp[i] > up[i]
    return osc, up, rS


def detect_signals(osc, up, lo):
    """Cross-into-zone signals on confirmed bars. rS = short (overbought), rL = long (oversold)."""
    n = len(osc); rS = [False] * n; rL = [False] * n
    for i in range(1, n):
        if osc[i] is None or osc[i - 1] is None:
            continue
        rS[i] = osc[i - 1] <= up[i - 1] and osc[i] > up[i]
        rL[i] = osc[i - 1] >= lo[i - 1] and osc[i] < lo[i]
    return rS, rL


@dataclass
class Signal:
    t: int
    direction: str        # "long" | "short"
    price: float          # signal-bar close (intended market entry reference)
    osc: float
    band: float
    er: float

    @property
    def time_utc(self) -> str:
        return dt.datetime.utcfromtimestamp(self.t).strftime("%Y-%m-%d %H:%M")

    def __str__(self) -> str:
        arrow = "🔴 SHORT" if self.direction == "short" else "🟢 LONG"
        return (f"{arrow} BTC @ {self.price:.1f}  "
                f"[{self.time_utc} UTC | dev {self.osc:+.2f} band {self.band:.2f} ER {self.er:.2f}]")


# ----------------------------- engine -----------------------------
class SignalEngine:
    """Rolling buffer of 15m bars; emits a signal on the latest CLOSED bar with all filters applied."""
    MIN_BARS = C.STDEV_WIN + C.ER_LEN + 5   # warmup for stdev + ER

    def __init__(self, direction: str = C.DIRECTION):
        self.bars: list[dict] = []
        self.direction = direction

    def _closed(self, fetched: list[dict]) -> list[dict]:
        """Drop the still-forming current candle — act only on fully closed bars."""
        step = C.TIMEFRAME_MIN * 60
        now = time.time()
        return [b for b in fetched if b["t"] + step <= now]

    def _fetch_bars(self, limit: int = 1000) -> list[dict]:
        try:
            bars = self._closed(fetch_15m_recent(limit))
            _save_bars_cache(bars)
            return bars
        except Exception as e:
            cached = _load_bars_cache()
            if cached:
                age_min = (time.time() - cached[-1]["t"]) / 60
                if age_min < 120:
                    print(f"{C.DATA_SOURCE} fetch failed ({e}) — cache ({age_min:.0f}m old)", flush=True)
                    return self._closed(cached)
            raise

    def seed(self, lookback_days: int = 10):
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                self.bars = self._fetch_bars(1000)
                return
            except Exception as e:
                last_err = e
                if attempt + 1 >= 5:
                    break
                time.sleep(3 * (attempt + 1))
        raise last_err or RuntimeError(f"{C.DATA_SOURCE} seed failed")

    def update(self) -> bool:
        """Pull any newly CLOSED bars. Returns True if a new bar closed since last call."""
        if not self.bars:
            self.seed()
            return bool(self.bars)
        last = self.bars[-1]["t"]
        try:
            recent = self._closed(fetch_15m_recent(96))
        except Exception:
            return False
        new = [b for b in recent if b["t"] > last]
        if new:
            self.bars.extend(new)
            self.bars = self.bars[-2000:]
            _save_bars_cache(self.bars)
        return bool(new)

    def latest_signal(self) -> Signal | None:
        """Check the most recent CLOSED bar for a tradable signal (cross + filters + direction)."""
        if len(self.bars) < self.MIN_BARS:
            return None
        i = len(self.bars) - 1
        b = self.bars[i]

        if C.STRATEGY == "hybrid":
            # long = 4H 1σ pen50 ER<0.1 ; short = Rolling 20 2σ pen20 no chop. Long checked first
            # (matches backtest mix order): a chop-failed long falls through to the short check.
            osc4, up4, lo4, er = compute_indicators_4h(self.bars, C.LONG_PEN)
            _, rL4 = detect_signals(osc4, up4, lo4)
            osc_roll, up_roll, rS_roll = compute_roll_short(self.bars)
            if (rL4[i] and self.direction in ("both", "long")
                    and er[i] is not None and er[i] < C.ER_MAX_LONG):
                return Signal(b["t"], "long", b["c"], osc4[i], lo4[i], er[i])
            if rS_roll[i] and self.direction in ("both", "short"):
                return Signal(b["t"], "short", b["c"], osc_roll[i], up_roll[i], er[i] or 0.0)
            return None

        # classic: both sides 4H pen20, chop ER < ER_MAX_CLASSIC
        osc4, up4, lo4, er = compute_indicators_4h(self.bars, C.PENETRATION)
        rS4, rL4 = detect_signals(osc4, up4, lo4)
        if er[i] is None or er[i] >= C.ER_MAX_CLASSIC:
            return None
        if rS4[i] and self.direction in ("both", "short"):
            return Signal(b["t"], "short", b["c"], osc4[i], up4[i], er[i])
        if rL4[i] and self.direction in ("both", "long"):
            return Signal(b["t"], "long", b["c"], osc4[i], lo4[i], er[i])
        return None


# --------------------------- validation ---------------------------
def _validate() -> bool:
    """Classic mode: prove 4H indicators == backtest.py. Hybrid: sanity-check roll-short count."""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))
    import backtest as BT

    bars = BT.load()
    rS_ref, rL_ref, er_ref = BT.indicators_4h(bars)
    osc, up, lo, er = compute_indicators_4h(bars)
    rS, rL = detect_signals(osc, up, lo)
    _, _, rS_roll = compute_roll_short(bars)

    er_ok = all(abs((er[i] or 0) - (er_ref[i] or 0)) < 1e-9 for i in range(len(bars)))
    ok_classic = rS == rS_ref and rL == rL_ref and er_ok
    print(f"bars                = {len(bars)}")
    print(f"classic 4H short    : ours={sum(rS):>4}  ref={sum(rS_ref):>4}  ok={rS == rS_ref}")
    print(f"classic 4H long     : ours={sum(rL):>4}  ref={sum(rL_ref):>4}  ok={rL == rL_ref}")
    print(f"hybrid roll 2σ short: {sum(rS_roll)} crosses")
    print(f"ER identical        : {er_ok}")
    print("PARITY:", "✅ PASS" if ok_classic and er_ok else "❌ MISMATCH")
    return ok_classic and er_ok


if __name__ == "__main__":
    import sys
    if "--live" in sys.argv:
        eng = SignalEngine()
        eng.seed()
        last = dt.datetime.utcfromtimestamp(eng.bars[-1]["t"])
        print(f"seeded {len(eng.bars)} closed bars ({C.DATA_SOURCE}), strategy={C.STRATEGY}, last {last} UTC")
        print("latest signal:", eng.latest_signal() or "none on the last closed bar")
    else:
        _validate()
