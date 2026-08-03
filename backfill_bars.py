"""One-off cloud backfill (owner go 2026-08-03): extend the offline backtest
dataset (fibleg/data/Stocks_data, 1-minute CSVs, ends ~2026-04-08) through
end-July using the engine's own Fyers credentials (GitHub secrets — the local
machine must NOT attempt Fyers login: TOTP lockout risk, 2026-07 incident).

Runs in Actions via backfill.yml, writes backfill_out/<name>_minute.csv in the
exact local CSV format; the artifact is downloaded and merged locally (rows
strictly AFTER each local file's last timestamp), then the bakers re-run.

Fetch window starts 2026-03-20 (overlap on purpose — merge dedups by ts).
"""
import csv
import os
import sys
import time
from datetime import datetime

from fibleg.data.fyers_feed import auto_login, fyers_series, get_client

# local dataset file stems (118) -> fetch symbol where the stem isn't the ticker
NAMES = """ABB ADANIENSOL ADANIENT ADANIGREEN ADANIPORTS ADANIPOWER AMBUJACEM
APOLLOHOSP ASIANPAINT ATGL AXISBANK BAJAJ-AUTO BAJAJFINSV BAJAJHFL BAJAJHLDNG
BAJFINANCE BANKBARODA BEL BHARTIARTL BHEL BOSCHLTD BPCL BRITANNIA CANBK CGPOWER
CHOLAFIN CIPLA COALINDIA CUMMINSIND DABUR DIVISLAB DLF DMART DRREDDY EICHERMOT
ENRIN ETERNAL GAIL GODREJCP GRASIM HAL HAVELLS HCLTECH HDFCAMC HDFCBANK HDFCLIFE
HEROMOTOCO HINDALCO HINDUNILVR HINDZINC HYUNDAI ICICIBANK ICICIGI ICICIPRULI
INDHOTEL INDIGO INDUSINDBK INFY IOC IRCTC IRFC ITC JINDALSTEL JIOFIN JSWENERGY
JSWSTEEL KOTAKBANK LICI LODHA LTIM LTM LT MARUTI MAXHEALTH MAZDOCK MM MOTHERSON
MUTHOOTFIN NAUKRI NESTLEIND NHPC NTPC ONGC PFC PIDILITIND PNB POWERGRID RECLTD
RELIANCE SBILIFE SBIN SHREECEM SHRIRAMFIN SIEMENS SOLARINDS SUNPHARMA TATACAP
TATACONSUM TATAPOWER TATASTEEL TCS TECHM TITAN TMCV TMPV TORNTPHARM TRENT
TVSMOTOR ULTRACEMCO UNIONBANK UNITDSPR VBL VEDL WIPRO ZYDUSLIFE""".split()
NAMES += ["NIFTY 50", "NIFTY BANK"]
FETCH_AS = {"MM": "M&M", "NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK"}

SINCE = "2026-03-20"
OUT = "backfill_out"


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    auto_login()
    client = get_client()
    ok, empty, failed = [], [], []
    for i, name in enumerate(NAMES, 1):
        sym = FETCH_AS.get(name, name)
        try:
            # resolution "1" passes straight through _resolution(); 1m needs
            # small windows or Fyers returns EMPTY — 7-day chunks are safe
            bars = fyers_series(client, sym, tf="1",
                                days=(datetime.now()
                                      - datetime(2026, 3, 18)).days + 1,
                                chunk_days=7)
        except Exception as e:  # noqa: BLE001 - report and continue
            failed.append((name, str(e)[:80]))
            print(f"[{i:3}/{len(NAMES)}] {name}: FAILED {e}", flush=True)
            continue
        rows = [b for b in bars if b.ts.strftime("%Y-%m-%d") >= SINCE]
        if not rows:
            empty.append(name)
            print(f"[{i:3}/{len(NAMES)}] {name}: 0 bars", flush=True)
            continue
        with open(os.path.join(OUT, f"{name}_minute.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            for b in rows:
                w.writerow([b.ts.strftime("%Y-%m-%d %H:%M:%S"),
                            b.open, b.high, b.low, b.close, int(b.volume)])
        ok.append(name)
        print(f"[{i:3}/{len(NAMES)}] {name}: {len(rows)} bars "
              f"{rows[0].ts.date()} -> {rows[-1].ts.date()}", flush=True)
        time.sleep(0.35)                     # stay far under Fyers rate limits
    print(f"\nDONE ok={len(ok)} empty={len(empty)} failed={len(failed)}")
    for n, e in failed:
        print(f"  FAILED {n}: {e}")
    if empty:
        print("  EMPTY:", " ".join(empty))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
