"""Nested-fib entry OFF vs ON, with confluence on (the intended combo).
Book | FULL | 0.786 SL | 5m trigger. Does the fractal 5m entry help?

    python validate_nested2.py "<csv>" [n_symbols]
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


def run(tf, nested):
    cfg = scan._cfg_for({"entry": 0.5, "exit": "full", "trig": 5, "sl": 0.786})
    cfg.require_confluence = True
    cfg.nested_entry = nested
    eng, _ = scan._run_tf(base, True, tf, cfg, "book", 1, 5)
    tr = [t for e in eng.values() for t in e.trades]
    w = sum(1 for t in tr if t.realized_r > 0)
    return sum(t.realized_r for t in tr), len(tr), (round(100 * w / len(tr)) if tr else 0)


print("CONFLUENCE ON | Book | FULL | 0.786 SL | 5m   —   NESTED entry OFF vs ON")
print("        netR / trades / win%\n")
for tf in scan.DETECT_TFS:
    a = run(tf, False)
    b = run(tf, True)
    print("  %3dm:  OFF %+8.1f/%4d/%2d%%    NESTED %+8.1f/%4d/%2d%%" % (
        tf, a[0], a[1], a[2], b[0], b[1], b[2]))
