"""Market context for setup gating — the validated 'best of the best' layer.

Fetches benchmarks once per scan (yfinance, daily bars):
  - Nifty (^NSEI): regime = 50d-SMA direction + ADX(14) -> UPT / DNT / SDW / WHP
    (WHP = ADX 20-25 whipsaw, the only regime that LOSES money; SDW is the best)
  - India VIX (^INDIAVIX): elevated = above its own 20d average (calm markets carry
    nearly all the profit)
  - Sector indices (CNX*): 50d-SMA trend per sector, stock mapped via S2S

A setup PASSES when: VIX not elevated, sector not against the trade, regime not
whipsaw, and projected R:R (to the ~1.272 target) >= 1.0.
11-yr walk-forward: gated set = +183R/1710 trades vs +122R/5114 ungated (net 0.15%).
Fail-safe: if a benchmark can't be fetched the corresponding check abstains (None)
rather than blocking setups."""
from __future__ import annotations

import bisect
from datetime import datetime

SEC_TICKERS = {"BANK": "^NSEBANK", "IT": "^CNXIT", "AUTO": "^CNXAUTO", "FMCG": "^CNXFMCG",
               "PHARMA": "^CNXPHARMA", "METAL": "^CNXMETAL", "ENERGY": "^CNXENERGY",
               "FIN": "^CNXFIN", "REALTY": "^CNXREALTY", "PSE": "^CNXPSE"}

S2S = {"HDFCBANK": "BANK", "ICICIBANK": "BANK", "AXISBANK": "BANK", "KOTAKBANK": "BANK",
       "SBIN": "BANK", "INDUSINDBK": "BANK", "BANKBARODA": "BANK", "PNB": "BANK",
       "FEDERALBNK": "BANK", "IDFCFIRSTB": "BANK", "AUBANK": "BANK",
       "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
       "LTIM": "IT", "MPHASIS": "IT", "COFORGE": "IT", "PERSISTENT": "IT",
       "MARUTI": "AUTO", "M&M": "AUTO", "TATAMOTORS": "AUTO", "BAJAJ-AUTO": "AUTO",
       "HEROMOTOCO": "AUTO", "EICHERMOT": "AUTO", "ASHOKLEY": "AUTO", "TVSMOTOR": "AUTO",
       "BALKRISIND": "AUTO", "MOTHERSON": "AUTO",
       "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
       "DABUR": "FMCG", "GODREJCP": "FMCG", "MARICO": "FMCG", "COLPAL": "FMCG",
       "TATACONSUM": "FMCG", "UBL": "FMCG",
       "SUNPHARMA": "PHARMA", "DRREDDY": "PHARMA", "CIPLA": "PHARMA", "DIVISLAB": "PHARMA",
       "AUROPHARMA": "PHARMA", "LUPIN": "PHARMA", "BIOCON": "PHARMA", "TORNTPHARM": "PHARMA",
       "ALKEM": "PHARMA",
       "TATASTEEL": "METAL", "JSWSTEEL": "METAL", "HINDALCO": "METAL", "VEDL": "METAL",
       "COALINDIA": "METAL", "JINDALSTEL": "METAL", "NMDC": "METAL", "SAIL": "METAL",
       "NATIONALUM": "METAL", "APLAPOLLO": "METAL",
       "RELIANCE": "ENERGY", "ONGC": "ENERGY", "NTPC": "ENERGY", "POWERGRID": "ENERGY",
       "BPCL": "ENERGY", "IOC": "ENERGY", "GAIL": "ENERGY", "TATAPOWER": "ENERGY",
       "ADANIGREEN": "ENERGY", "ADANIENSOL": "ENERGY",
       "BAJFINANCE": "FIN", "BAJAJFINSV": "FIN", "SBILIFE": "FIN", "HDFCLIFE": "FIN",
       "ICICIPRULI": "FIN", "CHOLAFIN": "FIN", "MUTHOOTFIN": "FIN", "SHRIRAMFIN": "FIN",
       "ICICIGI": "FIN",
       "DLF": "REALTY", "GODREJPROP": "REALTY", "OBEROIRLTY": "REALTY", "PRESTIGE": "REALTY",
       "LODHA": "REALTY", "PHOENIXLTD": "REALTY"}


def _fetch(ticker: str, start: str = "2014-06-01"):
    import yfinance as yf
    df = yf.download(ticker, start=start, interval="1d", progress=False)
    if df is None or df.empty:
        return None
    def col(n):
        s = df[n]
        if hasattr(s, "columns"):
            s = s.iloc[:, 0]
        return s.to_numpy().flatten().tolist()
    dts = [d.to_pydatetime().replace(tzinfo=None) for d in df.index]
    return dts, col("Open"), col("High"), col("Low"), col("Close")


class MarketContext:
    def __init__(self):
        self.mkt = []      # (date, regime)  regime in UPT/DNT/SDW/WHP
        self.vix = []      # (date, elevated_bool)
        self.vix_raw = None  # (last close, 20d avg) — raw level for per-fill stamping
        self.sec = {}      # sector -> ([(date, up_bool)], [dates])

    @classmethod
    def load(cls) -> "MarketContext":
        """Fetch all benchmarks; each is independently fail-safe."""
        from .indicators.trend import AdxStreamer
        from .models import Bar
        mc = cls()
        try:
            d = _fetch("^NSEI")
            if d:
                dts, o, h, l, c = d
                adx = AdxStreamer(14)
                for i in range(len(c)):
                    a = adx.update(Bar(dts[i], o[i], h[i], l[i], c[i]))
                    if i < 50:
                        continue
                    up = c[i] > sum(c[i - 50:i]) / 50
                    reg = ("UPT" if up else "DNT") if a >= 25 else ("SDW" if a < 20 else "WHP")
                    mc.mkt.append((dts[i], reg))
        except Exception as e:
            print("context: NSEI fetch failed:", e, flush=True)
        try:
            d = _fetch("^INDIAVIX")
            if d:
                dts, _, _, _, c = d
                for i in range(len(c)):
                    avg = sum(c[max(0, i - 19):i + 1]) / min(20, i + 1)
                    mc.vix.append((dts[i], c[i] > avg))
                if len(c):
                    a20 = sum(c[-20:]) / min(20, len(c))
                    mc.vix_raw = (round(float(c[-1]), 2), round(float(a20), 2))
        except Exception as e:
            print("context: VIX fetch failed:", e, flush=True)
        for s, tk in SEC_TICKERS.items():
            try:
                d = _fetch(tk)
                if d:
                    dts, _, _, _, c = d
                    arr = [(dts[i], c[i] > sum(c[i - 50:i]) / 50) for i in range(50, len(c))]
                    mc.sec[s] = (arr, [a[0] for a in arr])
            except Exception:
                pass
        return mc

    def _at(self, series, dates, ts):
        if not series:
            return None
        j = bisect.bisect_right(dates, ts) - 1
        return series[j][1] if j >= 0 else None

    def regime(self, ts: datetime):
        return self._at(self.mkt, [m[0] for m in self.mkt], ts)

    def vix_elevated(self, ts: datetime):
        return self._at(self.vix, [v[0] for v in self.vix], ts)

    def sector_up(self, symbol: str, ts: datetime):
        base = symbol.upper().replace(".NS", "").replace("^", "")
        s = S2S.get(base)
        if s is None or s not in self.sec:
            return None
        arr, dates = self.sec[s]
        return self._at(arr, dates, ts)

    def flags(self, symbol: str, ts: datetime, is_long: bool,
              entry: float, sl: float, t2: float | None) -> dict:
        """Per-setup context verdict. Abstaining checks (None) don't block."""
        reg = self.regime(ts)
        vhi = self.vix_elevated(ts)
        su = self.sector_up(symbol, ts)
        risk = abs(entry - sl)
        rr = round(abs(t2 - entry) / risk, 2) if (t2 is not None and risk > 0) else None
        sec_ok = None if su is None else (is_long == su)
        ok = ((vhi is not True) and (sec_ok is not False) and (reg != "WHP")
              and (rr is None or rr >= 1.0))
        return {"regime": reg, "vix_hi": vhi, "sector_ok": sec_ok, "rr": rr, "pass": ok}
