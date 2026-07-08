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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import Bar

_IST = timezone(timedelta(hours=5, minutes=30))   # NSE trading timezone
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
                                      "refresh_token": resp.get("refresh_token", ""),
                                      "app_id": creds.app_id,
                                      "saved": datetime.now().isoformat()}))
    return token


def _token_fresh() -> bool:
    """Fyers tokens die daily. Fresh = saved today (IST trading day, approx)."""
    if not TOKEN_FILE.exists():
        return False
    try:
        saved = datetime.fromisoformat(json.loads(TOKEN_FILE.read_text())["saved"])
        return saved.date() == datetime.now().date()
    except Exception:  # noqa: BLE001
        return False


def auto_login() -> str:
    """HEADLESS daily login for the cloud loop (UNTESTED until credentials exist —
    community-documented flow; endpoints may shift). Requires, via env or
    ~/.fibleg/fyers.json: FYERS_FY_ID (login id), FYERS_TOTP_KEY (the TOTP secret
    shown when enabling 2FA), FYERS_PIN, plus the app creds. Stores the token like
    the interactive flow, so everything downstream is identical.
    SECURITY: keeping the TOTP secret in GitHub Actions secrets means your broker
    2FA lives in the cloud — the owner accepted this trade-off for automation."""
    import base64

    import pyotp
    import requests

    creds = FyersCreds.load()
    extra = {}
    if CREDS_FILE.exists():
        extra = json.loads(CREDS_FILE.read_text())
    fy_id = os.getenv("FYERS_FY_ID") or extra.get("fy_id")
    totp_key = os.getenv("FYERS_TOTP_KEY") or extra.get("totp_key")
    pin = os.getenv("FYERS_PIN") or extra.get("pin")
    if not (fy_id and totp_key and pin):
        raise RuntimeError("auto_login needs FYERS_FY_ID / FYERS_TOTP_KEY / FYERS_PIN")
    b64 = lambda s: base64.b64encode(str(s).encode()).decode()  # noqa: E731
    s = requests.Session()
    r1 = s.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2",
                json={"fy_id": b64(fy_id), "app_id": "2"}, timeout=30).json()
    if "request_key" not in r1:
        raise RuntimeError(f"fyers otp step failed: {r1}")
    r2 = s.post("https://api-t2.fyers.in/vagator/v2/verify_otp",
                json={"request_key": r1["request_key"],
                      "otp": pyotp.TOTP(totp_key).now()}, timeout=30).json()
    if "request_key" not in r2:
        raise RuntimeError(f"fyers totp step failed: {r2}")
    r3 = s.post("https://api-t2.fyers.in/vagator/v2/verify_pin_v2",
                json={"request_key": r2["request_key"], "identity_type": "pin",
                      "identifier": b64(pin)}, timeout=30).json()
    t1 = (r3.get("data") or {}).get("access_token")
    if not t1:
        raise RuntimeError(f"fyers pin step failed: {r3}")
    appid, apptype = creds.app_id.rsplit("-", 1)
    body = {"fyers_id": fy_id, "app_id": appid, "redirect_uri": creds.redirect_uri,
            "appType": apptype, "code_challenge": "", "state": "None", "scope": "",
            "nonce": "", "response_type": "code", "create_cookie": False}
    r4 = s.post("https://api-t1.fyers.in/api/v3/token",
                headers={"Authorization": f"Bearer {t1}"}, json=body, timeout=30).json()
    url = r4.get("Url", "") or ""
    if "auth_code=" not in url:                       # retry once forcing a cookie
        body["create_cookie"] = True
        r4 = s.post("https://api-t1.fyers.in/api/v3/token",
                    headers={"Authorization": f"Bearer {t1}"}, json=body, timeout=30).json()
        url = r4.get("Url", "") or ""
    if "auth_code=" not in url:
        raise RuntimeError(f"fyers auth-code step failed: {r4}")
    auth_code = url.split("auth_code=")[1].split("&")[0]
    return exchange_auth_code(auth_code, creds)


FAIL_FILE = CFG_DIR / "fyers_login_fail"     # cooldown marker so a failing login
LOGIN_COOLDOWN = 900                          # isn't hammered every 5-min scan (15m)


def _login_on_cooldown() -> bool:
    """True if a login failed within the last LOGIN_COOLDOWN seconds — skip retrying
    (fall back) so we don't spam Fyers' login and risk a rate-limit/flag. After the
    window we retry, which is also how the feed auto-RECOVERS to Fyers once it's back."""
    if not FAIL_FILE.exists():
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(
            FAIL_FILE.read_text().strip())).total_seconds() < LOGIN_COOLDOWN
    except Exception:  # noqa: BLE001
        return False


def get_client(creds: FyersCreds | None = None):
    from fyers_apiv3 import fyersModel
    creds = creds or FyersCreds.load()
    if not _token_fresh():
        if _login_on_cooldown() and not TOKEN_FILE.exists():
            raise RuntimeError("fyers login on cooldown after a recent failure "
                               "— using fallback; will retry Fyers after 15 min")
        try:
            auto_login()
            FAIL_FILE.unlink(missing_ok=True)     # recovered -> clear the marker
        except Exception as e:  # noqa: BLE001
            CFG_DIR.mkdir(parents=True, exist_ok=True)
            FAIL_FILE.write_text(datetime.now().isoformat())
            if not TOKEN_FILE.exists():
                raise RuntimeError(
                    f"No cached token at {TOKEN_FILE} and auto_login failed ({e}). "
                    f"Run fyers_login.py or set FYERS_FY_ID/FYERS_TOTP_KEY/FYERS_PIN.") from e
            print(f"fyers: auto_login failed ({e}) — using cached token (may be stale)")
    token = json.loads(TOKEN_FILE.read_text())["access_token"]
    return fyersModel.FyersModel(client_id=creds.app_id, token=token,
                                 log_path=str(CFG_DIR))


# -- history --------------------------------------------------------------
def fyers_series(client, symbol: str, tf: str = "60m", days: int = 365,
                 chunk_days: int | None = None) -> list[Bar]:
    """Paginated history fetch. Fyers caps each intraday request to a small window
    (a too-large range returns EMPTY, not an error), so intraday resolutions must
    page in small chunks — daily can take big ones."""
    fsym = to_fyers_symbol(symbol)
    res = _resolution(tf)
    if chunk_days is None:                       # per-resolution safe chunk sizes
        chunk_days = 365 if res == "D" else (20 if res in ("5", "15") else 60)
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
            # IST-aware (matches the yfinance feed + the ledger timestamps, so the
            # paper books never mix naive/aware datetimes)
            seen[ts_epoch] = Bar(datetime.fromtimestamp(ts_epoch, tz=_IST),
                                 float(c[1]), float(c[2]), float(c[3]),
                                 float(c[4]), float(c[5]))
        cursor = c_end + timedelta(days=1)
    return [seen[k] for k in sorted(seen)]


def fyers_dual(client, symbol: str, days_1h: int = 365, days_15m: int = 365
               ) -> tuple[list[Bar], list[Bar]]:
    return (fyers_series(client, symbol, "60m", days_1h),
            fyers_series(client, symbol, "15m", days_15m))


def _quote_ltp(client, fsym: str) -> float | None:
    """Last traded price for a Fyers symbol (fallback spot when the chain omits it)."""
    try:
        resp = client.quotes(data={"symbols": fsym})
        for d in (resp.get("d") or []):
            lp = ((d.get("v") or {}).get("lp"))
            if lp is not None:
                return float(lp)
    except Exception:  # noqa: BLE001
        pass
    return None


def option_quotes(client, symbols: list[str]) -> dict[str, dict]:
    """Batched bid/ask/ltp for a list of Fyers option symbols (to re-price open positions).
    Returns {symbol: {bid, ask, ltp}}; missing/failed symbols simply absent. Never raises."""
    out: dict[str, dict] = {}
    syms = [s for s in (symbols or []) if s]
    if not syms:
        return out
    for i in range(0, len(syms), 45):                # Fyers caps the batch; chunk to be safe
        chunk = syms[i:i + 45]
        try:
            resp = client.quotes(data={"symbols": ",".join(chunk)})
            for d in (resp.get("d") or []):
                v = d.get("v") or {}
                sym = d.get("n") or v.get("symbol")
                if not sym:
                    continue
                out[sym] = {"bid": v.get("bid"), "ask": v.get("ask"), "ltp": v.get("lp")}
        except Exception:  # noqa: BLE001
            pass
    return out


_LOTS_CACHE: dict[str, int] | None = None


def lot_sizes() -> dict[str, int]:
    """REAL NSE F&O lot sizes from Fyers' public symbol master (no auth): underlying ->
    lot. Read off the futures rows (col 1 has 'FUT', col 3 = lot, col 13 = underlying).
    Cached per process (the session loop fetches once); {} on any failure — callers
    treat lot data as optional."""
    global _LOTS_CACHE  # noqa: PLW0603
    if _LOTS_CACHE is not None:
        return _LOTS_CACHE
    out: dict[str, int] = {}
    try:
        import requests  # local import — keep the module import-light
        r = requests.get("https://public.fyers.in/sym_details/NSE_FO.csv", timeout=40)
        for line in r.text.splitlines():
            c = line.split(",")
            if len(c) > 13 and "FUT" in c[1]:
                try:
                    lot = int(float(c[3]))
                except Exception:  # noqa: BLE001
                    continue
                und = c[13].strip()
                if lot > 0 and und:                      # keep the smallest (current-month) lot
                    key = und + ".NS"
                    if key not in out or lot < out[key]:
                        out[key] = lot
        # index names arrive as NIFTY/BANKNIFTY — map to the app's yahoo-style tickers
        for src, dst in (("NIFTY.NS", "^NSEI"), ("BANKNIFTY.NS", "^NSEBANK")):
            if src in out:
                out[dst] = out.pop(src)
    except Exception:  # noqa: BLE001
        out = {}
    _LOTS_CACHE = out
    return out


def option_chain(client, symbol: str, strikecount: int = 20) -> dict | None:
    """Fyers option chain for an underlying -> {symbol, spot, expiry_days, strikes:
    {K: {ce_oi, pe_oi, ce_ltp, pe_ltp}}}. Returns None on ANY failure (never raises) so
    a bad chain can't kill the scan. Fyers' chain gives OI + LTP but no IV (we solve it)."""
    fsym = to_fyers_symbol(symbol)
    try:
        resp = client.optionchain(data={"symbol": fsym, "strikecount": strikecount,
                                        "timestamp": ""})
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(resp, dict) or resp.get("s") == "error":
        return None
    data = resp.get("data") or {}
    spot = None
    strikes: dict[float, dict] = {}
    for r in (data.get("optionsChain") or []):
        otype = str(r.get("option_type", "")).upper()
        if otype not in ("CE", "PE"):            # the underlying row carries the spot LTP
            if spot is None and r.get("ltp"):
                try:
                    spot = float(r["ltp"])
                except Exception:  # noqa: BLE001
                    pass
            continue
        try:
            k = float(r.get("strike_price") or 0)
        except Exception:  # noqa: BLE001
            continue
        if k <= 0:
            continue
        rec = strikes.setdefault(k, {})
        pref = "ce" if otype == "CE" else "pe"
        try:
            rec[pref + "_oi"] = float(r.get("oi") or 0)
        except Exception:  # noqa: BLE001
            rec[pref + "_oi"] = 0.0
        for fld, key in (("ltp", "_ltp"), ("bid", "_bid"), ("ask", "_ask")):
            try:
                rec[pref + key] = float(r.get(fld) or 0)
            except Exception:  # noqa: BLE001
                rec[pref + key] = 0.0
        rec[pref + "_sym"] = r.get("symbol")     # the tradeable option symbol (to re-quote later)
    if spot is None:
        spot = _quote_ltp(client, fsym)
    if not strikes or not spot:
        return None
    exp_days = None                              # days to the nearest expiry, if given
    try:
        exps = data.get("expiryData") or []
        e0 = int(exps[0].get("expiry") or exps[0].get("date") or 0) if exps else 0
        if e0:
            exp_days = max(0.5, (datetime.fromtimestamp(e0, tz=_IST)
                                 - datetime.now(tz=_IST)).days + 1)
    except Exception:  # noqa: BLE001
        pass
    return {"symbol": symbol, "spot": float(spot), "strikes": strikes,
            "expiry_days": exp_days}
