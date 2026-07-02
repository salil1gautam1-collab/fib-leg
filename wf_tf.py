"""TF + frequency check on the finalized system (0.618 legs, lock-B). 45m vs 2H, All vs
(M/W|pin)+trend. Reports net R (train/test/full) AND trade counts + trades/year/stock."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
N = len(tickers)
print(f"{N} stocks, book382 0.618, lock-B — 45m vs 2H, All vs Reversal+trend\n", flush=True)

def base():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.trail_sl_after_targets=True; c.entry_dependent_targets=True; c.sl_lock_at_t1=True
    c.book_reanchor_ratio=0.618
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); return c
def rev():
    c=base(); c.require_mw=True; c.reversal_pin=True; c.require_htf=True; return c
CFG = {"All": base(), "Reversal+trend": rev()}
TFS = [45, 120]

rows={(tf,k):[] for tf in TFS for k in CFG}
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    for tf in TFS:
        for k,c in CFG.items():
            eng,_=scan._run_tf({tk:bars},True,tf,c,"book382",1,5)
            for t in eng[tk].trades:
                if t.leg is None or not t.entry or t.entry_ts is None: continue
                rf=abs(t.entry - t.leg.retracement(0.786))/t.entry
                if rf>0: rows[(tf,k)].append((t.realized_r, rf, t.entry_ts.year))
    if (i+1)%25==0 or i+1==len(tickers): print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)",flush=True)

def netR(R,cost=0.10): return sum(r[0]-(cost/100)/r[1] for r in R)
YEARS=11.3
for tf in TFS:
    for k in CFG:
        R=rows[(tf,k)]
        tr=[r for r in R if r[2]<=2020]; te=[r for r in R if r[2]>=2021]
        w=sum(1 for r in R if r[0]>0)
        lab=f"{tf}m {k}"
        print(f"\n=== {lab} ===")
        print(f"  FULL  {netR(R):+8.1f}R / {len(R):5d} trades / {round(100*w/len(R)) if R else 0}% win / {netR(R)/len(R) if R else 0:+.4f} R-avg")
        print(f"  TRAIN {netR(tr):+8.1f}R / {len(tr):5d}   TEST {netR(te):+8.1f}R / {len(te):5d}")
        print(f"  FREQ  {len(R)/YEARS:.0f} trades/yr universe · {len(R)/N/YEARS:.1f}/yr per stock · ~{len(R)/YEARS/250:.1f}/trading-day")
