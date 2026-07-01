"""Point #3 test: does adding the big-rejection-PIN respect (zone_pin_respect) help?
For each stock (loaded once) run every TF with pin OFF vs pin ON, segment by flag."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
TFS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["45","60","120","240"])]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 999
tickers = feeds.csv_dir_symbols(DIR)[:N]
print(f"{len(tickers)} stocks, TFs={TFS}, book382 FULL 5m — pin-respect OFF vs ON", flush=True)

def cfg(pin):
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry = True; c.nested_entry = True
    c.zone_respect = True; c.zone_frac = 0.05
    c.zone_pin_respect = pin
    c.targets, c.target_fractions, c.move_sl_to_be_after_tp1 = (0.95,), (1.0,), False
    return c

rows = {(tf, pin): [] for tf in TFS for pin in (False, True)}
t0 = time.time()
for i, tk in enumerate(tickers):
    try:
        bars = feeds.csv_dir_series(DIR, tk)
    except Exception as e:
        print("  skip", tk, e, flush=True); continue
    for tf in TFS:
        for pin in (False, True):
            eng, _ = scan._run_tf({tk: bars}, True, tf, cfg(pin), "book382", 1, 5)
            e = eng[tk]
            for t in e.trades:
                if t.leg is None: continue
                rows[(tf, pin)].append((t.realized_r, e.confluence_leg(t.leg), e.mw_confirmed(t.leg)))
    if (i+1) % 20 == 0 or i+1 == len(tickers):
        print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)", flush=True)

def line(R):
    if not R: return "   no trades"
    net = sum(r[0] for r in R); w = sum(1 for r in R if r[0] > 0)
    return f"{net:+8.1f}R / {len(R):6d} / {round(100*w/len(R))}% / {net/len(R):+.3f}avg"

for tf in TFS:
    print(f"\n=== TF {tf}m ===", flush=True)
    for seg, pred in [("All", lambda r: True), ("A+ (mtn)", lambda r: r[1]), ("A+ & M/W", lambda r: r[1] and r[2])]:
        off = [r for r in rows[(tf, False)] if pred(r)]
        on  = [r for r in rows[(tf, True)]  if pred(r)]
        print(f"  {seg:10s} pinOFF {line(off)}")
        print(f"  {'':10s} pinON  {line(on)}")
