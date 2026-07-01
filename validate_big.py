"""Validate on the LOCAL 118-stock / 11-year 1-min data (Stocks_data). Zone-entry
mode (mountain optional). Loads each stock ONCE, runs every TF. Segments trades by
flags to compare the A+ (mountain) edge vs no-mountain. book382, FULL, 5m."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
TFS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["45","60","120"])]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 999
tickers = feeds.csv_dir_symbols(DIR)[:N]
print(f"{len(tickers)} stocks, TFs={TFS}, zone-entry (mountain optional) | book382 | FULL | 5m", flush=True)

def cfg():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry = True; c.nested_entry = True
    c.zone_respect = True; c.zone_frac = 0.05
    c.targets, c.target_fractions, c.move_sl_to_be_after_tp1 = (0.95,), (1.0,), False
    return c

rows = {tf: [] for tf in TFS}   # tf -> [(r, conf, mw, pin)]
t0 = time.time()
for i, tk in enumerate(tickers):
    try:
        bars = feeds.csv_dir_series(DIR, tk)
    except Exception as e:
        print("  skip", tk, e, flush=True); continue
    for tf in TFS:
        eng, _ = scan._run_tf({tk: bars}, True, tf, cfg(), "book382", 1, 5)
        e = eng[tk]
        for t in e.trades:
            if t.leg is None: continue
            rows[tf].append((t.realized_r, e.confluence_leg(t.leg), e.mw_confirmed(t.leg), e.pin_bar_confirmed(t.leg)))
    if (i+1) % 10 == 0 or i+1 == len(tickers):
        print(f"  [{i+1}/{len(tickers)}] {tk:12s} ({time.time()-t0:.0f}s)", flush=True)

for tf in TFS:
    R = rows[tf]
    print(f"\n=== TF {tf}m  ({len(R)} trades total) ===", flush=True)
    def seg(name, pred):
        s = [r for r in R if pred(r)]
        if not s: print(f"  {name:30s}   no trades"); return
        net = sum(r[0] for r in s); w = sum(1 for r in s if r[0] > 0)
        print(f"  {name:30s} {net:+9.1f}R / {len(s):6d} / {round(100*w/len(s))}% win / {net/len(s):+.3f}R-avg")
    seg("All (zone entry)",             lambda r: True)
    seg("A+ (mountain)",                lambda r: r[1])
    seg("A+ & reversal (mtn+M/W|pin)",  lambda r: r[1] and (r[2] or r[3]))
    seg("No mountain",                  lambda r: not r[1])
    seg("Only M/W",                     lambda r: r[2])
    seg("Only Pin",                     lambda r: r[3])
