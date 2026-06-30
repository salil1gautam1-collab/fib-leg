"""DhanHQ data feed (design §3) — free NSE 1H/15m history. No execution.

Dhan's API keys off a numeric `security_id`, so we download Dhan's scrip-master
CSV once (cached) and resolve trading symbols -> (security_id, segment, type).

Credentials are NEVER hardcoded — read from:
  1. env vars  DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN
  2. ~/.fibleg/dhan.json  {"client_id": "...", "access_token": "..."}

Get the access token from web.dhan.co -> DhanHQ Trading API -> generate token.
Dhan tokens are time-limited (≈ a day) — re-generate when it expires; for an
unattended cloud cron we'll automate that later.

Lazy imports so the core engine never depends on dhanhq.
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..models import Bar

CFG_DIR = Path.home() / ".fibleg"
CREDS_FILE = CFG_DIR / "dhan.json"
SCRIP_CACHE = CFG_DIR / "dhan_scrip.csv"
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# well-known index security ids (IDX_I segment) as a fallback if CSV lookup misses
_INDEX_IDS = {"NSE:NIFTY50-INDEX": "13", "NSE:NIFTYBANK-INDEX": "25"}


@dataclass
class DhanCreds:
    client_id: str
    access_token: str

    @classmethod
    def load(cls) -> "DhanCreds":
        cid, tok = os.getenv("DHAN_CLIENT_ID"), os.getenv("DHAN_ACCESS_TOKEN")
        if cid and tok:
            return cls(cid, tok)
        if CREDS_FILE.exists():
            d = json.loads(CREDS_FILE.read_text())
            return cls(d["client_id"], d["access_token"])
        raise RuntimeError(
            f"No Dhan creds. Set DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN or create "
            f"{CREDS_FILE} (see config/dhan.example.json).")


def get_client(creds: DhanCreds | None = None):
    from dhanhq import dhanhq
    creds = creds or DhanCreds.load()
    return dhanhq(creds.client_id, creds.access_token)


# -- symbol normalisation + resolution -----------------------------------
def normalize(sym: str) -> str:
    """Internal/Yahoo ticker -> canonical key used for resolution."""
    s = sym.upper().strip()
    idx = {"^NSEI": "NSE:NIFTY50-INDEX", "NIFTY": "NSE:NIFTY50-INDEX",
           "NIFTY50": "NSE:NIFTY50-INDEX", "^NSEBANK": "NSE:NIFTYBANK-INDEX",
           "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "NIFTYBANK": "NSE:NIFTYBANK-INDEX"}
    if s in idx:
        return idx[s]
    if s.endswith(".NS"):
        s = s[:-3]
    return s


def _scrip_text() -> str:
    if not SCRIP_CACHE.exists():
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(SCRIP_URL, timeout=60).read()
        SCRIP_CACHE.write_bytes(data)
    return SCRIP_CACHE.read_text(encoding="utf-8", errors="ignore")


_resolver: dict[str, tuple[str, str, str]] | None = None


def _build_resolver() -> dict[str, tuple[str, str, str]]:
    """key -> (security_id, exchange_segment, instrument_type)."""
    out: dict[str, tuple[str, str, str]] = {}
    rows = csv.DictReader(io.StringIO(_scrip_text()))
    for r in rows:
        if r.get("SEM_EXM_EXCH_ID") != "NSE":
            continue
        inst = r.get("SEM_INSTRUMENT_NAME", "")
        sid = r.get("SEM_SMST_SECURITY_ID", "")
        tsym = (r.get("SEM_TRADING_SYMBOL") or "").upper()
        uname = (r.get("SM_SYMBOL_NAME") or "").upper()
        if inst == "EQUITY":
            out.setdefault(tsym, (sid, "NSE_EQ", "EQUITY"))
        elif inst == "INDEX":
            seg = "IDX_I"
            if "NIFTY 50" in uname or tsym in ("NIFTY", "NIFTY 50"):
                out["NSE:NIFTY50-INDEX"] = (sid, seg, "INDEX")
            elif "BANK" in uname and "NIFTY" in uname:
                out["NSE:NIFTYBANK-INDEX"] = (sid, seg, "INDEX")
    return out


def resolve(symbol: str) -> tuple[str, str, str]:
    global _resolver
    if _resolver is None:
        _resolver = _build_resolver()
    key = normalize(symbol)
    if key in _resolver:
        return _resolver[key]
    if key in _INDEX_IDS:                       # fallback for indices
        return (_INDEX_IDS[key], "IDX_I", "INDEX")
    raise KeyError(f"Could not resolve '{symbol}' (key '{key}') in Dhan scrip master.")


_INTERVAL = {"60m": 60, "1h": 60, "60": 60, "15m": 15, "15": 15, "5m": 5}


# -- history --------------------------------------------------------------
def dhan_series(client, symbol: str, tf: str = "60m", days: int = 365,
                chunk_days: int = 90) -> list[Bar]:
    sid, seg, itype = resolve(symbol)
    interval = _INTERVAL.get(tf, 60)
    end = datetime.now()
    start = end - timedelta(days=days)
    seen: dict[float, Bar] = {}
    cursor = start
    while cursor < end:
        c_end = min(cursor + timedelta(days=chunk_days), end)
        resp = client.intraday_minute_data(
            security_id=sid, exchange_segment=seg, instrument_type=itype,
            from_date=cursor.strftime("%Y-%m-%d"),
            to_date=c_end.strftime("%Y-%m-%d"), interval=interval)
        data = resp.get("data", resp) if isinstance(resp, dict) else {}
        ts = data.get("timestamp") or data.get("start_Time") or []
        o, h, l, c, v = (data.get("open", []), data.get("high", []),
                         data.get("low", []), data.get("close", []),
                         data.get("volume", []))
        for i in range(len(ts)):
            seen[ts[i]] = Bar(datetime.fromtimestamp(ts[i]),
                              float(o[i]), float(h[i]), float(l[i]),
                              float(c[i]), float(v[i]) if i < len(v) else 0.0)
        cursor = c_end + timedelta(days=1)
    return [seen[k] for k in sorted(seen)]


def dhan_dual(client, symbol: str, days_1h: int = 365, days_15m: int = 365
              ) -> tuple[list[Bar], list[Bar]]:
    return (dhan_series(client, symbol, "60m", days_1h),
            dhan_series(client, symbol, "15m", days_15m))
