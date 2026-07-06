"""Discover the F&O universe from Fyers' symbol master and rank stocks by REAL
liquidity (avg daily traded value), so the books trade the most liquid names —
the ones with tradeable option spreads — instead of a hardcoded list.

Writes docs/book_universe.json: {generated, indices:[...], stocks:[{sym,val_cr}], top:[...]}.
Run:  python build_universe.py [TOP_N]        (default 50)
"""
import csv
import io
import json
import sys
import time

import requests

from fibleg.data import fyers_feed

TOP_N = int(sys.argv[1]) if len(sys.argv) > 1 else 50
INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
               "SENSEX", "BANKEX", "NIFTYIT"}

print("fetching Fyers F&O symbol master…", flush=True)
raw = requests.get("https://public.fyers.in/sym_details/NSE_FO.csv", timeout=60).text
underlyings = set()
for row in csv.reader(io.StringIO(raw)):
    if len(row) < 14:
        continue
    fysym = row[9]                       # e.g. NSE:RELIANCE26JULFUT
    if fysym.endswith("FUT"):
        underlyings.add(row[13].strip())  # the underlying name
stocks = sorted(u for u in underlyings if u and u not in INDEX_NAMES)
idxs = sorted(u for u in underlyings if u in INDEX_NAMES)
print(f"F&O underlyings: {len(stocks)} stocks + {len(idxs)} indices", flush=True)

client = fyers_feed.get_client()
ranked, t0, miss = [], time.time(), []
for i, s in enumerate(stocks):
    bars = None
    for attempt in range(3):                 # throttle + retry: Fyers rate-limits bursts
        try:
            bars = fyers_feed.fyers_series(client, s, "D", days=40)
            if bars:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6 * (attempt + 1))       # back off, then retry
    time.sleep(0.35)                          # stay well under Fyers' rate limit
    recent = (bars or [])[-20:]
    if not recent:
        miss.append(s)
        continue
    val_cr = sum(b.close * b.volume for b in recent) / len(recent) / 1e7
    ranked.append({"sym": s, "val_cr": round(val_cr, 1)})
    if (i + 1) % 30 == 0:
        print(f"  [{i+1}/{len(stocks)}] {len(ranked)} ranked ({time.time()-t0:.0f}s)", flush=True)
if miss:
    print(f"  no-data for {len(miss)}: {miss[:15]}{'…' if len(miss) > 15 else ''}", flush=True)

ranked.sort(key=lambda x: -x["val_cr"])
top = [r["sym"] for r in ranked[:TOP_N]]
out = {"generated": time.strftime("%Y-%m-%d"), "top_n": TOP_N,
       "indices": idxs, "stocks_ranked": ranked, "top": top}
json.dump(out, open("docs/book_universe.json", "w"), separators=(",", ":"))

print(f"\n=== TOP {TOP_N} most liquid F&O stocks (avg daily Rs-cr traded) ===")
for r in ranked[:TOP_N]:
    print(f"  {r['sym']:14s} {r['val_cr']:>8.0f} cr")
print(f"\nwrote docs/book_universe.json · {len(ranked)} stocks ranked")
