#!/usr/bin/env python3
"""One-time Fyers auth — caches a daily access token at ~/.fibleg/fyers_token.json.

Prereq: put your Fyers app credentials in ~/.fibleg/fyers.json (see
config/fyers.example.json) or in env vars FYERS_APP_ID / FYERS_SECRET_ID /
FYERS_REDIRECT_URI. Get them from https://myapi.fyers.in/dashboard (create an
app; redirect_uri can be https://127.0.0.1/).

Run:
    python fyers_login.py
It prints a login URL. Open it, log in, approve. Fyers redirects to your
redirect_uri with `?auth_code=...` in the URL. Paste that auth_code back here.
Fyers access tokens expire daily — re-run this each trading day (or automate).
"""
from __future__ import annotations

from fibleg.data import fyers_feed


def main() -> None:
    creds = fyers_feed.FyersCreds.load()
    print(f"\nApp: {creds.app_id}   redirect: {creds.redirect_uri}\n")
    url = fyers_feed.generate_auth_url(creds)
    print("1) Open this URL, log in, and approve:\n")
    print("   " + url + "\n")
    print("2) After redirect, paste the WHOLE address-bar URL (or just the auth_code).\n")
    raw = input("Paste here: ").strip()
    # accept either the full redirect URL or the bare code
    if "auth_code=" in raw:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(raw).query)
        auth_code = (q.get("auth_code") or [""])[0]
    else:
        auth_code = raw
    if not auth_code:
        print("Couldn't find an auth_code in that. Re-run and paste the redirect URL.")
        return
    token = fyers_feed.exchange_auth_code(auth_code, creds)
    print(f"\n✓ Token cached at {fyers_feed.TOKEN_FILE}")
    print(f"  (…{token[-8:]})  — you can now run:  python run_backtest.py --fyers RELIANCE.NS --dual")


if __name__ == "__main__":
    main()
