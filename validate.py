"""Large-sample backtest on a multi-ticker 1m CSV. Aggregates ALL trades (no 50
cap) across N symbols per (TF, method, exec) and ranks by net R.

    python validate.py "<csv>" [n_symbols]
"""
import sys

import pandas as pd

import scan
from fibleg.data import feeds


def main() -> None:
    csv = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    tickers = list(pd.read_csv(csv, usecols=["Ticker"])["Ticker"].unique())[:n]
    symbols = [t + ".NS" for t in tickers]
    print("loading %d symbols from CSV..." % len(symbols))
    base = feeds.csv_multi(csv, symbols)
    base = {s: b for s, b in base.items() if b}          # drop empties
    print("loaded %d with data" % len(base))

    rows = []
    for tf in scan.DETECT_TFS:
        for method in scan.METHODS:
            for ex in scan.EXECS:
                cfg = scan._cfg_for(ex)
                engines, _ = scan._run_tf(base, True, tf, cfg, method, 1, ex["trig"])
                trades = [t for e in engines.values() for t in e.trades]
                if not trades:
                    continue
                nr = sum(t.realized_r for t in trades)
                w = sum(1 for t in trades if t.realized_r > 0)
                rows.append((nr, tf, method, ex["key"], len(trades), round(100 * w / len(trades))))

    rows.sort(reverse=True)
    print("\nTOP 20 (by net R, all trades pooled):")
    for nr, tf, m, ex, cnt, wr in rows[:20]:
        print("  %+8.1fR  %-3sm %-8s %-16s n=%4d win=%d%%" % (nr, tf, m, ex, cnt, wr))
    print("\nby METHOD:")
    for m in scan.METHODS:
        mr = [r for r in rows if r[2] == m]
        pos = [r for r in mr if r[0] > 0]
        print("  %-8s: %d/%d combos pos, best=%+.1fR, avg=%+.1f, trades=%d" % (
            m, len(pos), len(mr), max(r[0] for r in mr),
            sum(r[0] for r in mr) / len(mr), sum(r[4] for r in mr)))
    print("\nby EXIT / SL (book382, pooled netR):")
    for tag in ("full|5|0.618", "full|5|0.786", "partial|5|0.618", "partial|5|0.786"):
        er = [r for r in rows if r[2] == "book382" and tag in r[3]]
        if er:
            print("  %-18s netR=%+.1f over %d trades" % (
                tag, sum(r[0] for r in er), sum(r[4] for r in er)))


if __name__ == "__main__":
    main()
