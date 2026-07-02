"""Instrument comparison on the v5 legs (2H book382, lock-at-B). Regenerates per-trade R
for baseline + M/W+trend, then models each instrument and reports net R (train/test/full)
+ the TAIL (worst trade, max drawdown in R). Costs in R = cost%/risk_frac per trade.

  naked future : r - fut_cost
  future+DOTM  : max(r, -cap) - fut_cost - hedge_cost   (put strike caps the gap loss)
  DITM option  : 0.93*r (delta), floored at -cap_ditm, - ditm_spread   (built-in gap cap)
"""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
FUT = 0.07   # futures round-trip %

def base():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.trail_sl_after_targets=True; c.entry_dependent_targets=True; c.sl_lock_at_t1=True
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); return c
def mwtrend():
    c=base(); c.require_mw=True; c.require_htf=True; return c
CFG={"baseline": base(), "M/W+trend": mwtrend()}

rows={k:[] for k in CFG}
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    for k,c in CFG.items():
        eng,_=scan._run_tf({tk:bars},True,120,c,"book382",1,5)
        for t in eng[tk].trades:
            if t.leg is None or not t.entry or t.entry_ts is None: continue
            rf=abs(t.entry - t.leg.retracement(0.786))/t.entry
            if rf>0: rows[k].append((t.realized_r, rf, t.entry_ts.year))
    if (i+1)%25==0 or i+1==len(tickers): print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)",flush=True)

def costR(rf,c): return (c/100.0)/rf
def net_and_tail(R):
    """R = list of per-trade net r. Returns (sum, worst_trade, max_drawdown)."""
    if not R: return (0,0,0)
    s=sum(R); worst=min(R)
    peak=cum=0.0; mdd=0.0
    for r in R:
        cum+=r; peak=max(peak,cum); mdd=min(mdd,cum-peak)
    return (s,worst,mdd)
def seg(rows_cfg, transform):
    """apply per-trade transform (r,rf)->net r; return dict of period stats."""
    tr=[transform(r,rf) for r,rf,y in rows_cfg if y<=2020]
    te=[transform(r,rf) for r,rf,y in rows_cfg if y>=2021]
    al=[transform(r,rf) for r,rf,y in rows_cfg]
    return al,tr,te

def show(label, rows_cfg, transform):
    al,tr,te=seg(rows_cfg,transform)
    sA,wA,dA=net_and_tail(al); sT,_,_=net_and_tail(tr); sE,_,_=net_and_tail(te)
    print(f"  {label:34s} FULL {sA:+7.1f}R  (train {sT:+6.1f} / test {sE:+6.1f})  worst {wA:+5.1f}R  maxDD {dA:6.1f}R")

for cfgname,R in rows.items():
    print(f"\n================ {cfgname}  ({len(R)} trades) ================")
    show("naked future",              R, lambda r,rf: r - costR(rf,FUT))
    for cap in (1.5,2.0,3.0):
        for hc in (0.08,0.12):
            show(f"future+DOTM  cap -{cap}R, hedge {hc}%", R,
                 (lambda cap,hc: lambda r,rf: max(r,-cap) - costR(rf,FUT) - costR(rf,hc))(cap,hc))
    for sp in (0.15,0.25):
        show(f"DITM  delta.93, cap -1.15R, spread {sp}%", R,
             (lambda sp: lambda r,rf: max(0.93*r,-1.15) - costR(rf,sp))(sp))
