"""Exit comparison on 11yr data. All configs: pin-respect ON + close respect, book382, 5m.
  OFF          = full square at T1 (0.95)  [current]
  run@1.272    = full square at a further single target
  ON part+trail= scale out 1/3 at 0.95/1.272/1.618, BE after T1, ratchet stop to prev target
Segments: All and A+ & M/W."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
TFS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["45","120"])]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 999
tickers = feeds.csv_dir_symbols(DIR)[:N]
print(f"{len(tickers)} stocks, TFs={TFS}, book382 5m, pin+close respect ON", flush=True)

def base():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry = True; c.nested_entry = True
    c.zone_respect = True; c.zone_frac = 0.05; c.zone_pin_respect = True
    return c
def exits():
    e = {}
    c = base(); c.targets=(0.95,); c.target_fractions=(1.0,); c.move_sl_to_be_after_tp1=False; e["OFF full@0.95"]=c
    c = base(); c.targets=(1.272,); c.target_fractions=(1.0,); c.move_sl_to_be_after_tp1=False; e["run@1.272 "]=c
    c = base(); c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); c.move_sl_to_be_after_tp1=True; c.trail_sl_after_targets=True; e["ON part+trail"]=c
    return e

CFG = exits()
rows = {(tf,k): [] for tf in TFS for k in CFG}
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars = feeds.csv_dir_series(DIR, tk)
    except Exception as ex: print("  skip",tk,ex,flush=True); continue
    for tf in TFS:
        for k,c in CFG.items():
            eng,_ = scan._run_tf({tk:bars}, True, tf, c, "book382", 1, 5)
            e=eng[tk]
            for t in e.trades:
                if t.leg is None: continue
                rows[(tf,k)].append((t.realized_r, e.confluence_leg(t.leg), e.mw_confirmed(t.leg)))
    if (i+1)%20==0 or i+1==len(tickers): print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)",flush=True)

def line(R):
    if not R: return "   no trades"
    net=sum(r[0] for r in R); w=sum(1 for r in R if r[0]>0)
    return f"{net:+8.1f}R / {len(R):6d} / {round(100*w/len(R))}% / {net/len(R):+.3f}avg"
for tf in TFS:
    print(f"\n=== TF {tf}m ===",flush=True)
    for seg,pred in [("All",lambda r:True),("A+ & M/W",lambda r:r[1] and r[2])]:
        print(f"  -- {seg} --")
        for k in CFG: print(f"     {k:16s} {line([r for r in rows[(tf,k)] if pred(r)])}")
