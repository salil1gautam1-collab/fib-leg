"""Clean full-universe (indices EXCLUDED), ALL timeframes, book382, pin+close respect.
Compares the two finalist exits (3T and entry-dependent 1/d), GROSS and net of costs.
Cost = %-of-notional / risk_frac (R). hard-stop = 0.10% (txn+slippage);
+hedge0.6% adds a 0.6% option premium for reference."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
TFS = [45, 60, 120, 180, 240]
avail = feeds.csv_dir_symbols(DIR)
tickers = [s for s in avail if "NIFTY" not in s.upper()]
print(f"Full universe: {len(tickers)} stocks (excluded {len(avail)-len(tickers)} indices). TFs={TFS}\n", flush=True)

def base():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.move_sl_to_be_after_tp1=True; c.trail_sl_after_targets=True
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); return c
def cfg(ed):
    c=base(); c.entry_dependent_targets=ed; return c
CFG = {"3T .95/1.27/1.62": cfg(False), "entry-dep 1/d": cfg(True)}

rows={(tf,k):[] for tf in TFS for k in CFG}
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    for tf in TFS:
        for k,c in CFG.items():
            eng,_=scan._run_tf({tk:bars},True,tf,c,"book382",1,5)
            e=eng[tk]
            for t in e.trades:
                if t.leg is None or not t.entry: continue
                rf=abs(t.entry - t.leg.retracement(0.786))/t.entry
                if rf>0: rows[(tf,k)].append((t.realized_r, rf))
    if (i+1)%20==0 or i+1==len(tickers): print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)",flush=True)

def stat(R):
    if not R: return "  no trades"
    gr=sum(r[0] for r in R); w=sum(1 for r in R if r[0]>0); arf=sum(r[1] for r in R)/len(R)
    noh=sum(r[0]-(0.10/100)/r[1] for r in R); h6=sum(r[0]-(0.70/100)/r[1] for r in R)
    return (f"{len(R):6d} {round(100*w/len(R)):4d}% risk{arf*100:5.2f}% "
            f"GROSS{gr:+8.1f}  net(hardstop){noh:+8.1f}  net(+hedge.6%){h6:+8.1f}")
for tf in TFS:
    print(f"\n=== TF {tf}m ===")
    for k in CFG: print(f"  {k:16s} {stat(rows[(tf,k)])}")
