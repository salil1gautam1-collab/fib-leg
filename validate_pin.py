"""Does adding a solid pin bar (M/W OR pin) recover trades M/W's double-structure
misses, without hurting win rate? Base = the shipped A+ (conf+nested+zone5%+M/W).
book382, FULL, 5m, SL 0.786, 501 stocks."""
import sys, pandas as pd, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

csv = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 501
tickers = list(pd.read_csv(csv, usecols=["Ticker"])["Ticker"].unique())[:n]
base = {s:b for s,b in feeds.csv_multi(csv,[t+".NS" for t in tickers]).items() if b}
print("symbols:", len(base), "\n")

VARIANTS = {
    "shipped: M/W only        ": dict(),
    "M/W OR pin               ": dict(reversal_pin=True),
    "pin-only (no zone/MW)*   ": dict(_pin_only=True),  # M/W off, pin as sole origin gate
}

def run(tf, opts):
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.require_confluence = c.nested_entry = True
    c.zone_respect = True; c.zone_frac = 0.05; c.require_mw = True
    c.targets, c.target_fractions, c.move_sl_to_be_after_tp1 = (0.95,), (1.0,), False
    if opts.get("_pin_only"):
        c.require_mw = True; c.reversal_pin = True
        # emulate 'pin OR mw' already; for pure-pin we'd need mw off — skip, informational
    for k,v in opts.items():
        if not k.startswith("_"): setattr(c,k,v)
    eng,_ = scan._run_tf(base, True, tf, c, "book382", 1, 5)
    tr=[t for e in eng.values() for t in e.trades]
    if not tr: return (0.0,0,0)
    w=sum(1 for t in tr if t.realized_r>0)
    return (sum(t.realized_r for t in tr), len(tr), round(100*w/len(tr)))

print("book382 | conf+nested+zone5% | FULL | 5m | SL 0.786    (netR/trades/win%)\n")
print("  %-26s" % "variant", "".join("%14s" % f"{tf}m" for tf in scan.DETECT_TFS))
for label, opts in list(VARIANTS.items())[:2]:
    row = "  %-26s" % label
    for tf in scan.DETECT_TFS:
        netr,ntr,win = run(tf,opts)
        row += "%14s" % (f"{netr:+.1f}/{ntr}/{win}%" if ntr else "  -  ")
    print(row)
