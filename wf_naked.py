"""Naked option vs Future+DOTM on the SAME trades (2H, 0.618, lock-B). For each trade we
buy the directional option at entry and reprice it at exit via Black-Scholes using the
trade's ACTUAL move (realized_r x risk_frac) and hold time -> theta/delta/convexity all
fall out. Tests ATM / ITM / deep-ITM. Reports edge in future-R units (P&L per notional)
AND avg return-on-premium (capital deployed = premium), vs the future baseline.
Assumptions: IV, monthly-ish tenor, r=0 (call/put symmetric at ATM), per-strike spread cost."""
import sys, time, math, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
N = len(tickers)
IV, TENOR = 0.32, 45/365.0     # ~32% IV, ~45-day option

def ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S,K,T,sig):
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

rows={k:[] for k in CFG}   # (realized_r, rf, hold_days)
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

# strikes as call-moneyness (K relative to entry spot=1): ATM=1.0, ITM below spot, deep ITM lower
STRIKES={"ATM (K=1.00)":(1.00,0.06), "ITM (K=0.95)":(0.95,0.10), "deep-ITM (K=0.88)":(0.88,0.22)}
def opt_metrics(R, K, cost):
    onetR=0.0; owins=0; roi=[]
    for (r,rf,hd) in R:
        fav=r*rf                                  # favorable underlying move (signed)
        C0=bs_call(1.0,K,TENOR,IV)                # premium at entry
        T1=max(0.0,TENOR-hd/365.0)
        C1=bs_call(1.0+fav,K,T1,IV)               # value at exit
        pnl=C1-C0-cost/100.0                       # % of notional, net of spread cost
        onetR += pnl/rf                            # in future-R units (1R = rf of notional)
        if pnl>0: owins+=1
        roi.append(pnl/C0)                         # return on premium (capital deployed)
    return onetR, 100*owins/len(R), 100*sum(roi)/len(roi)

for k in CFG:
    R=rows[k]
    futR=sum(r-(0.15/100)/rf for (r,rf,_) in R)
    fw=sum(1 for (r,_,_) in R if r>0)
    print(f"\n===== 2H · lock-B · {k}  ({len(R)} trades) =====")
    print(f"  FUTURE+DOTM (ref): {futR:+.0f}R net(0.15%) · {round(100*fw/len(R))}% win")
    for sk,(K,cost) in STRIKES.items():
        onetR,owin,roi=opt_metrics(R,K,cost)
        print(f"  NAKED {sk:18s}: {onetR:+.0f}R equiv · {owin:.0f}% win · avg {roi:+.0f}% return-on-premium/trade")
