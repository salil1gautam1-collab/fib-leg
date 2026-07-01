"""Same full edge (confluence + nested + Book 0.382 | FULL | 5m), SL 0.618 vs 0.786,
every TF, 501 stocks. Shows how much of the R difference is just stop-width rescaling.
"""
import sys
import pandas as pd
import scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

csv = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 501
tickers = list(pd.read_csv(csv, usecols=["Ticker"])["Ticker"].unique())[:n]
base = {s: b for s, b in feeds.csv_multi(csv, [t + ".NS" for t in tickers]).items() if b}
print("symbols:", len(base), "\n")


def run(tf, sl):
    c = StrategyConfig()
    c.entry_ratio, c.sl_ratio = 0.5, sl
    c.require_confluence = c.nested_entry = True
    c.targets, c.target_fractions, c.move_sl_to_be_after_tp1 = (0.95,), (1.0,), False
    eng, _ = scan._run_tf(base, True, tf, c, "book382", 1, 5)
    tr = [t for e in eng.values() for t in e.trades]
    w = sum(1 for t in tr if t.realized_r > 0)
    return sum(t.realized_r for t in tr), len(tr), (round(100 * w / len(tr)) if tr else 0)


print("Confluence + nested | Book 0.382 | FULL | 5m   —   SL 0.618 vs 0.786   (netR/trades/win%)\n")
for tf in scan.DETECT_TFS:
    a = run(tf, 0.618)
    b = run(tf, 0.786)
    print("  %3dm |  SL0.618 %+7.1f/%4d/%2d%%   |  SL0.786 %+7.1f/%4d/%2d%%" % (
        tf, a[0], a[1], a[2], b[0], b[1], b[2]))
