"""Fixed-% option-exit test. For each 2H trade we compute the REAL max favorable excursion
(MFE) from the 1-min bars between entry and exit, reprice the option at that spike (entry
IV, NO crush -> you exited fast), and if it hit +TARGET% we bank +TARGET. Trades that
never spiked run to their normal exit WITH a 20% IV crush (they sat and bled). Tests ATM
& ITM at +50/100/200%. Edge in future-R units vs Future+DOTM +131R and vs hold-to-target."""
import sys, time, math, bisect, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import Side

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
N = len(tickers); TENOR = 45/365.0; IV = 0.35; CRUSH = 0.20

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

rows={k:[] for k in CFG}   # (r, rf, hold_days, mfe_frac, t_mfe_days)
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    ts=[b.ts for b in bars]
    for k,c in CFG.items():
        eng,_=scan._run_tf({tk:bars},True,120,c,"book382",1,5)
        for t in eng[tk].trades:
            if t.leg is None or not t.entry or t.entry_ts is None or t.exit_ts is None: continue
            rf=abs(t.entry - t.leg.retracement(0.786))/t.entry
            if rf<=0: continue
            hd=max(0.25,(t.exit_ts-t.entry_ts).total_seconds()/86400.0)
            lo=bisect.bisect_left(ts,t.entry_ts); hi=bisect.bisect_right(ts,t.exit_ts)
            seg=bars[lo:hi] or bars[lo:lo+1]
            if t.side is Side.LONG:
                pk=max(range(len(seg)), key=lambda j: seg[j].high); mfe=max(0.0,(seg[pk].high-t.entry)/t.entry)
            else:
                pk=min(range(len(seg)), key=lambda j: seg[j].low); mfe=max(0.0,(t.entry-seg[pk].low)/t.entry)
            tmfe=max(0.0,(seg[pk].ts-t.entry_ts).total_seconds()/86400.0)
            rows[k].append((t.realized_r, rf, hd, mfe, tmfe))
    if (i+1)%25==0 or i+1==N: print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)",flush=True)

STRIKES=[("ATM",1.00,0.06),("ITM",0.95,0.10)]
TARGETS=[0.5,1.0,2.0]
def edge(R,K,cost,target):
    tot=0.0; hit=0
    for (r,rf,hd,mfe,tmfe) in R:
        C0=bs(1.0,K,TENOR,IV)
        Cpk=bs(1.0+mfe,K,max(0.0,TENOR-tmfe/365.0),IV)         # peak, exit fast, no crush
        if C0>0 and (Cpk-C0)/C0>=target:
            pnl=C0*target-cost/100.0; hit+=1
        else:
            C1=bs(1.0+r*rf,K,max(0.0,TENOR-hd/365.0),IV*(1-CRUSH))  # sat, crushed
            pnl=C1-C0-cost/100.0
        tot+=pnl/rf
    return tot,100*hit/len(R)
def hold(R,K,cost):   # hold-to-target with crush (the losing version), for reference
    tot=0.0
    for (r,rf,hd,mfe,tmfe) in R:
        C0=bs(1.0,K,TENOR,IV); C1=bs(1.0+r*rf,K,max(0.0,TENOR-hd/365.0),IV*(1-CRUSH))
        tot+=(C1-C0-cost/100.0)/rf
    return tot

for k in CFG:
    R=rows[k]; futR=sum(r-(0.15/100)/rf for (r,rf,_,_,_) in R)
    print(f"\n===== 2H lock-B {k} ({len(R)} trades) — Future+DOTM {futR:+.0f}R · IV {int(IV*100)}% crush {int(CRUSH*100)}% =====")
    for nm,K,cost in STRIKES:
        print(f"  {nm} hold-to-target (crushed): {hold(R,K,cost):+.0f}R")
        for tg in TARGETS:
            e,h=edge(R,K,cost,tg)
            print(f"  {nm} exit +{int(tg*100)}%: {e:+7.0f}R  ({h:.0f}% of trades hit the target)")
