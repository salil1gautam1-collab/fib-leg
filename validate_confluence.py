"""Does the CONFLUENCE filter create an edge? Compare RAW (every fib entry) vs
CONFLUENCE (only setups where a broken mountain sits in the 0.5-0.618 band), on
the CSV universe. Book 0.236 | FULL | 0.786 SL | 5m, all entries & timeframes.

    python validate_confluence.py "<csv>" [n_symbols]
"""
import sys

import pandas as pd

import scan
from fibleg.data import feeds

csv = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 501
tickers = list(pd.read_csv(csv, usecols=["Ticker"])["Ticker"].unique())[:n]
base = {s: b for s, b in feeds.csv_multi(csv, [t + ".NS" for t in tickers]).items() if b}
print("symbols with data:", len(base), "\n")


def run(entry, tf, conf):
    cfg = scan._cfg_for({"entry": entry, "exit": "full", "trig": 5, "sl": 0.786})
    cfg.require_confluence = conf
    eng, _ = scan._run_tf(base, True, tf, cfg, "book", 1, 5)
    tr = [t for e in eng.values() for t in e.trades]
    w = sum(1 for t in tr if t.realized_r > 0)
    return sum(t.realized_r for t in tr), len(tr), (round(100 * w / len(tr)) if tr else 0)


print("Book 0.236 | FULL | 0.786 SL | 5m   —   RAW (all entries)  vs  CONFLUENCE (A+ only)")
print("           netR / trades / win%\n")
for entry in (0.382, 0.5, 0.618):
    print("entry %s:" % entry)
    for tf in scan.DETECT_TFS:
        r = run(entry, tf, False)
        c = run(entry, tf, True)
        print("  %3dm:  RAW %+7.1f/%4d/%2d%%    CONF %+7.1f/%4d/%2d%%" % (
            tf, r[0], r[1], r[2], c[0], c[1], c[2]))
    print()
