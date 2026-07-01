"""Data feeds.

`synthetic_series` — pure stdlib, deterministic trending/zigzag bars so the
backtest runs offline with zero installs (the design's accuracy-critical NSE
feed is Fyers/Dhan; this is just for wiring + tests).

`yfinance_series` — optional real data (US + NSE via the .NS suffix). Requires
`pip install yfinance`. NSE intraday from yfinance is delayed/gappy — fine for
exploration, replace with the Fyers feed for production (design §3).
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from ..models import Bar


def synthetic_series(n: int = 1500, seed: int = 7, start: float = 1000.0,
                     step: timedelta = timedelta(hours=1)) -> list[Bar]:
    """A trending series with periodic impulses + pullbacks so fib legs form."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = start
    t = datetime(2024, 1, 1, 9, 0)
    drift_cycle = 120  # bars per up/down regime
    for i in range(n):
        regime = 1 if (i // drift_cycle) % 2 == 0 else -1
        drift = regime * 0.0009 * price
        wobble = math.sin(i / 9.0) * 0.0015 * price
        shock = rng.gauss(0, 0.004) * price
        close = max(1.0, price + drift + wobble + shock)
        hi = max(price, close) + abs(rng.gauss(0, 0.0025)) * price
        lo = min(price, close) - abs(rng.gauss(0, 0.0025)) * price
        bars.append(Bar(t, round(price, 2), round(hi, 2), round(lo, 2), round(close, 2),
                        volume=rng.randint(1000, 5000)))
        price = close
        t += step
    return bars


def resample(bars: list[Bar], factor: int) -> list[Bar]:
    """Aggregate bars into `factor`-sized candles (e.g. 1H -> 4H with factor=4)."""
    if factor <= 1:
        return bars
    out: list[Bar] = []
    for i in range(0, len(bars), factor):
        g = bars[i:i + factor]
        if not g:
            break
        out.append(Bar(g[-1].ts, g[0].open, max(b.high for b in g),
                       min(b.low for b in g), g[-1].close, sum(b.volume for b in g)))
    return out


def csv_multi(path: str, symbols: list[str]) -> dict[str, list[Bar]]:
    """Load a multi-ticker OHLC CSV (Kaggle nifty500_1m format):
        Datetime,Ticker,Company,Open,High,Low,Close,Volume
    Returns {symbol: [Bar]} for the requested symbols (a '.NS' suffix is stripped
    to match the file's bare tickers). Bars are ascending by time."""
    import pandas as pd

    def tk(s: str) -> str:
        return s[:-3] if s.upper().endswith(".NS") else s

    want = {tk(s): s for s in symbols}
    cols = ["Datetime", "Ticker", "Open", "High", "Low", "Close", "Volume"]
    df = pd.read_csv(path, usecols=cols)
    df = df[df["Ticker"].isin(want)]
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    out: dict[str, list[Bar]] = {s: [] for s in symbols}
    for t, sub in df.groupby("Ticker"):
        sub = sub.sort_values("Datetime")
        out[want[t]] = [Bar(r.Datetime.to_pydatetime(), float(r.Open), float(r.High),
                            float(r.Low), float(r.Close),
                            float(r.Volume) if not pd.isna(r.Volume) else 0.0)
                        for r in sub.itertuples(index=False)]
    return out


def yfinance_dual(symbol: str) -> tuple[list[Bar], list[Bar]]:
    """(1H, 15m) bars for dual-timeframe runs. yfinance caps 15m history at ~60d,
    so both use 60d to stay aligned (longer history needs the Fyers feed)."""
    return (yfinance_series(symbol, period="60d", interval="60m"),
            yfinance_series(symbol, period="60d", interval="15m"))


def yfinance_series(symbol: str, period: str = "60d", interval: str = "60m") -> list[Bar]:
    import yfinance as yf  # lazy import; optional dependency

    df = yf.download(symbol, period=period, interval=interval,
                     auto_adjust=False, progress=False)
    # recent yfinance returns MultiIndex columns (field, ticker) even for one symbol
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    bars: list[Bar] = []
    for ts, row in df.iterrows():
        o, h, l, c = (float(row["Open"]), float(row["High"]),
                      float(row["Low"]), float(row["Close"]))
        v = float(row.get("Volume", 0) or 0)
        bars.append(Bar(ts.to_pydatetime(), o, h, l, c, v))
    return bars
