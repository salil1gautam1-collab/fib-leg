"""ABCD 'safe' lock-at-B test. Best config = 2H ED+M/W. Compare stop-after-T1:
breakeven (current) vs lock-at-T1 (book). Walk-forward, net of hard-stop costs."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig
DIR = sys.argv[1]
tickers=[s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
print(f"{len(tickers)} stocks, 2H, ED+M/W, net of 0.10% cost\n", flush=True)
def cfg(lock):
    c=StrategyConfig(); c.entry_ratio,c.sl_ratio=0.5,0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.require_mw=True; c.entry_dependent_targets=True; c.trail_sl_after_targets=True
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3)
    c.move_sl_to_be_after_tp1=not lock; c.sl_lock_at_t1=lock; return c
CFG={"BE after T1 (current)":cfg(False), "lock at B (safe)":cfg(True)}
rows={k:[] for k in CFG}; t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    for k,c in CFG.items():
        eng,_=scan._run_tf({tk:bars},True,120,c,"book382",1,5); e=eng[tk]
        for t in e.trades:
            if t.leg is None or not t.entry or t.entry_ts is None: continue
            rf=abs(t.entry - t.leg.retracement(0.786))/t.entry
            if rf>0: rows[k].append((t.realized_r,rf,t.entry_ts.year))
    if (i+1)%30==0 or i+1==len(tickers): print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)",flush=True)
def line(R):
    if not R: return "-"
    net=sum(r[0]-(0.10/100)/r[1] for r in R); w=sum(1 for r in R if r[0]>0)
    return f"{net:+8.1f}R/{len(R):5d}/{round(100*w/len(R))}%"
print(f"\n{'config':22s} {'FULL':>20s} {'TRAIN':>20s} {'TEST':>20s}")
for k,R in rows.items():
    tr=[r for r in R if r[2]<=2020]; te=[r for r in R if r[2]>=2021]
    print(f"{k:22s} {line(R):>20s} {line(tr):>20s} {line(te):>20s}")
