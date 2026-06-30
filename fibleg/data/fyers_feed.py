"""Fyers API v3 data feed (design §3) — accurate NSE 1H/15m + years of history.

Credentials are NEVER hardcoded. They're read from (in order):
  1. env vars  FYERS_APP_ID / FYERS_SECRET_ID / FYERS_REDIRECT_URI
  2. a local json file  ~/.fibleg/fyers.json  {"app_id","secret_id","redirect_uri"}

One-time auth (interactive) is done via `fyers_login.py`, which caches the
access token at ~/.fibleg/fyers_token.json. After that, `get_client()` is
non-interactive until the token expires (Fyers tokens are daily — re-run login).

Lazy imports so the core engine never depends on fyers-apiv3.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..models import Bar

CFG_DIR = Path.home() / ".fibleg"
CREDS_FILE = CFG_DIR / "fyers.json"
TOKEN_FILE = CFG_DIR / "fyers_token.json"


@dataclass
class FyersCreds:
    app_id: str            # e.g. "ABCD1234-100" (client_id from the Fyers dashboard)
    secret_id: str
    redirect_uri: str

    @classmethod
    def load(cls) -> "FyersCreds":
        env = (os.getenv("FYERS_APP_ID"), os.getenv("FYERS_SECRET_ID"),
               os.getenv("FYERS_REDIRECT_URI"))
        if all(env):
            return cls(*env)  # type: ignore[arg-type]
        if CREDS_FILE.exists():
            d = json.loads(CREDS_FILE.read_text())
            return cls(d["app_id"], d["secret_id"], d.get("redirect_uri", "https://127.0.0.1/"))
        raise RuntimeError(
            f"No Fyers creds. Set FYERS_APP_ID/FYERS_SECRET_ID/FYERS_REDIRECT_URI "
            f"or create {CREDS_FILE} — then run fyers_login.py")


# -- symbol + resolution mapping -----------------------------------------
def to_fyers_symbol(sym: str) -> str:
    """Yahoo/internal ticker -> Fyers symbol (NSE:RELIANCE-EQ, NSE:NIFTY50-INDEX)."""
    s = sym.upper().strip()
    indices = {
        "^NSEI": "NSE:NIFTY50-INDEX", "NIFTY": "NSE:NIFTY50-INDEX",
        "NIFTY50": "NSE:NIFTY50-INDEX",
        "^NSEBANK": "NSE:NIFTYBANK-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
        "NIFTYBANK": "NSE:NIFTYBANK-INDEX",
    }
    if s in indices:
        return indices[s]
    if s.startswith("NSE:") or s.startswith("BSE:"):
        return s
    if s.endswith(".NS"):
        s = s[:-3]
    return f"NSE:{s}-EQ"


_RES = {"60m": "60", "1h": "60", "60": "60", "15m": "15", "15": "15", "5m": "5", "D": "D"}


def _resolution(tf: str) -> str:
    return _RES.get(tf, tf)


# -- auth -----------------------------------------------------------------
def generate_auth_url(creds: FyersCreds | None = None) -> str:
    from fyers_apiv3 import fyersModel
    creds = creds or FyersCreds.load()
    session = fyersModel.SessionModel(
        client_id=creds.app_id, secret_key=creds.secret_id,
        redirect_uri=creds.redirect_uri, response_type="code",
        grant_type="authorization_code")
    return session.generate_authcode()


def exchange_auth_code(auth_code: str, creds: FyersCreds | None = None) -> str:
    """Exchange the redirect's auth_code for an access token and cache it."""
    from fyers_apiv3 import fyersModel
    creds = creds or FyersCreds.load()
    session = fyersModel.SessionModel(
        client_id=creds.app_id, secret_key=creds.secret_id,
        redirect_uri=creds.redirect_uri, response_type="code",
        grant_type="authorization_code")
    session.set_token(auth_code)
    resp = session.generate_token()
    token = resp.get("access_token")
    if not token:
        raise RuntimeError(f"Token exchange failed: {resp}")
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"access_token": token,
                                      "app_id": creds.app_id,
                                      "saved": datetime.now().isoformat()}))
    return token


def get_client(creds: FyersCreds | None = None):
    from fyers_apiv3 import fyersModel
    creds = creds or FyersCreds.load()
    if not TOKEN_FILE.exists():
        raise RuntimeError(f"No cached token at {TOKEN_FILE}. Run fyers_login.py first.")
    token = json.loads(TOKEN_FILE.read_text())["access_token"]
    return fyersModel.FyersModel(client_id=creds.app_id, token=token,
                                 log_path=str(CFG_DIR))


# -- history --------------------------------------------------------------
def fyers_series(client, symbol: str, tf: str = "60m", days: int = 365,
                 chunk_days: int = 90) -> list[Bar]:
    """Paginated history fetch (Fyers caps the range per intraday request)."""
    fsym = to_fyers_symbol(symbol)
    res = _resolution(tf)
    end = datetime.now()
    start = end - timedelta(days=days)
    seen: dict[float, Bar] = {}
    cursor = start
    while cursor < end:
        c_end = min(cursor + timedelta(days=chunk_days), end)
        resp = client.history({
            "symbol": fsym, "resolution": res, "date_format": "1",
            "range_from": cursor.strftime("%Y-%m-%d"),
            "range_to": c_end.strftime("%Y-%m-%d"), "cont_flag": "1",
        })
        for c in resp.get("candles", []):
            ts_epoch = c[0]
            seen[ts_epoch] = Bar(datetime.fromtimestamp(ts_epoch),
                                 float(c[1]), float(c[2]), float(c[3]),
                                 float(c[4]), float(c[5]))
        cursor = c_end + timedelta(days=1)
    return [seen[k] for k in sorted(seen)]


def fyers_dual(client, symbol: str, days_1h: int = 365, days_15m: int = 365
               ) -> tuple[list[Bar], list[Bar]]:
    return (fyers_series(client, symbol, "60m", days_1h),
            fyers_series(client, symbol, "15m", days_15m))
