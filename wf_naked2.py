"""Naked-option STRESS test: IV crush + IV sweep. Same 2H/0.618/lock-B trades, but exit
IV = entry IV x (1-crush) -> models the vol drop that bleeds option buyers even when
right. Grid over entry IV {28,35,42%} x crush {0,15,25%}, for ATM and ITM. Edge in
future-R units vs the +131R Future+DOTM baseline. r=0, ~45-day tenor, per-strike spread cost."""
import sys, time, math, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
N = len(tickers); TENOR = 45/365.0

def ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S,K,T,sig):
    if T<=1e-9 or S<=0: return max(0.0,S-K)
    d1=(math.log(S/K)+0.5*sig*sig*T)/(sig*math.sqrt(T)); d2=d1-sig*math.sqrt(T)
    return S*ncdf(d1)-K*ncdf(d2)

def make(filt):
    c=StrategyConfig(); c.entry_ratio,c.sl_ratio=0.5,0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.book_reanchor_ratio=0.618
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3)
    c.entry_dependent_targets=True; c.trail_sl_after_targets=True; c.sl_lock_at_t1=True
    if filt=="rev": c.require_mw=True; c.reversal_pin=True; c.require_htf=True
    return c
CFG={"All":make("all"),"Rev+trend":make("rev")}

rows={k:[] for k in CFG}
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    for k,c in CFG.items():
        eng,_=scan._run_tf({tk:bars},True,120,c,"book382",1,5)
        for t in eng[tk].trades:
            if t.leg is None or not t.entry or t.entry_ts is None or t.exit_ts is None: continue
            rf=abs(t.entry - t.leg.retracement(0.786))/t.entry
            if rf<=0: continue
            hd=max(0.25,(t.exit_ts-t.entry_ts).total_seconds()/86400.0)
            rows[k].append((t.realized_r, rf, hd))
    if (i+1)%25==0 or i+1==N: print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)",flush=True)

IVS=[0.28,0.35,0.42]; CRUSH=[0.0,0.15,0.25]
STRIKES=[("ATM",1.00,0.06),("ITM",0.95,0.10)]
def onetR(R,K,cost,iv,cr):
    ivx=iv*(1-cr); tot=0.0
    for (r,rf,hd) in R:
        C0=bs(1.0,K,TENOR,iv); C1=bs(1.0+r*rf,K,max(0.0,TENOR-hd/365.0),ivx)
        tot+=(C1-C0-cost/100.0)/rf
    return tot

for k in CFG:
    R=rows[k]; futR=sum(r-(0.15/100)/rf for (r,rf,_) in R)
    print(f"\n===== 2H lock-B {k} ({len(R)} trades) — Future+DOTM ref {futR:+.0f}R =====")
    for nm,K,cost in STRIKES:
        print(f"  NAKED {nm} (K={K}):        crush 0%   crush 15%  crush 25%")
        for iv in IVS:
            vals=[onetR(R,K,cost,iv,cr) for cr in CRUSH]
            print(f"    entry IV {int(iv*100)}%:     " + "  ".join(f"{v:+7.0f}R" for v in vals))
