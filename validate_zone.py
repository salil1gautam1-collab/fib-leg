"""Zone-respect refinement: mountain as an S/R zone, enter only when price trades
into it and closes back out (held); skip if it closes through (failed). Compare
base A+ vs +zone-respect (2 widths) vs +zone-respect+M/W. book382, FULL, 5m, SL 0.786."""
import sys, pandas as pd, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

csv = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 501
tickers = list(pd.read_csv(csv, usecols=["Ticker"])["Ticker"].unique())[:n]
base = {s:b for s,b in feeds.csv_multi(csv,[t+".NS" for t in tickers]).items() if b}
print("symbols:", len(base), "\n")

VARIANTS = {
    "A+ base (conf+nested)     ": dict(),
    "+ zone-respect 3%         ": dict(zone_respect=True, zone_frac=0.03),
    "+ zone-respect 5%         ": dict(zone_respect=True, zone_frac=0.05),
    "+ zone-respect 3% + M/W   ": dict(zone_respect=True, zone_frac=0.03, require_mw=True),
    "+ zone-respect 5% + M/W   ": dict(zone_respect=True, zone_frac=0.05, require_mw=True),
    "+ M/W only (prev winner)  ": dict(require_mw=True),
}

def run(tf, opts):
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.require_confluence = c.nested_entry = True
    c.targets, c.target_fractions, c.move_sl_to_be_after_tp1 = (0.95,), (1.0,), False
    for k,v in opts.items(): setattr(c,k,v)
    eng,_ = scan._run_tf(base, True, tf, c, "book382", 1, 5)
    tr=[t for e in eng.values() for t in e.trades]
    if not tr: return (0.0,0,0)
    w=sum(1 for t in tr if t.realized_r>0)
    return (sum(t.realized_r for t in tr), len(tr), round(100*w/len(tr)))

print("book382 | conf+nested | FULL | 5m | SL 0.786    (netR/trades/win%)\n")
print("  %-27s" % "variant", "".join("%14s" % f"{tf}m" for tf in scan.DETECT_TFS))
for label, opts in VARIANTS.items():
    row = "  %-27s" % label
    for tf in scan.DETECT_TFS:
        netr,ntr,win = run(tf,opts)
        row += "%14s" % (f"{netr:+.1f}/{ntr}/{win}%" if ntr else "  -  ")
    print(row)
