"""Which combination gets R positive? Base = A+ edge (confluence + nested, FULL, SL 0.786).
Sweep: method (book382/book) x gate (none/MW/HTF/MW+HTF/EW) x TF x TRIGGER (5m/15m close).
netR/trades/win% on 501 stocks. Trigger is a first-class dimension."""
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

GATES = {
    "none     ": dict(),
    "M/W      ": dict(require_mw=True),
    "HTF      ": dict(require_htf=True),
    "M/W+HTF  ": dict(require_mw=True, require_htf=True),
    "EW       ": dict(require_ew=True),
}

def run(tf, method, trig, gate):
    c = StrategyConfig()
    c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.require_confluence = c.nested_entry = True
    c.targets, c.target_fractions, c.move_sl_to_be_after_tp1 = (0.95,), (1.0,), False
    for k, v in gate.items():
        setattr(c, k, v)
    eng, _ = scan._run_tf(base, True, tf, c, method, 1, trig)
    tr = [t for e in eng.values() for t in e.trades]
    if not tr:
        return (0.0, 0)
    w = sum(1 for t in tr if t.realized_r > 0)
    return (sum(t.realized_r for t in tr), len(tr), round(100 * w / len(tr)))

for method in ("book382", "book"):
    for trig in (5, 15):
        print("=" * 84)
        print(f" {method}  |  {trig}m close trigger   (confluence + nested | FULL | SL 0.786)   netR/trades/win%")
        print("=" * 84)
        print("  %-9s" % "gate", "".join("%14s" % f"{tf}m" for tf in scan.DETECT_TFS))
        for label, gate in GATES.items():
            row = "  %-9s" % label
            for tf in scan.DETECT_TFS:
                netr, ntr, *rest = run(tf, method, trig, gate)
                row += "%14s" % (f"{netr:+.1f}/{ntr}/{rest[0]}%" if ntr else "   -   ")
            print(row)
        print()
