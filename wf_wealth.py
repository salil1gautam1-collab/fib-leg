"""45m vs 2H P&L + WEALTH simulation on the finalized system (0.618 legs, lock-B,
Future+DOTM). Orders all trades by date, compounds equity (reinvest), net of 0.15%
round-trip cost, DOTM gap-cap at -1.5R. Reports trades/freq/netR + growth multiple,
CAGR, max drawdown at 0.5% and 1% risk-per-trade. ASSUMES own capital (no margin
interest); 0.15% = brokerage+STT+exchange+GST+slippage+DOTM theta/spread."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
N = len(tickers)
COST_PCT, DOTM_CAP, YEARS, START = 0.15, 1.5, 11.3, 200000
FS = [0.005, 0.01]

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
                if rf>0: rows[(tf,k)].append((t.entry_ts, t.realized_r, rf))
    if (i+1)%25==0 or i+1==N: print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)",flush=True)

def netR(R): return sum(r-(COST_PCT/100)/rf for (_,r,rf) in R)
def compound(R,f):
    eq=peak=1.0; mdd=0.0
    for (_,r,rf) in sorted(R,key=lambda x:x[0]):
        nr=max(r,-DOTM_CAP)-(COST_PCT/100)/rf
        eq*=(1+f*nr); peak=max(peak,eq); mdd=min(mdd,eq/peak-1)
    return eq, eq**(1/YEARS)-1, mdd

for tf in TFS:
    for k in CFG:
        R=rows[(tf,k)]; w=sum(1 for (_,r,_) in R if r>0)
        print(f"\n===== {tf}m · {k} =====")
        print(f"  {netR(R):+.0f}R net(0.15%) · {len(R)} trades · {round(100*w/len(R)) if R else 0}% win · {len(R)/YEARS:.0f}/yr · ~{len(R)/YEARS/250:.1f}/trading-day")
        for f in FS:
            eq,cagr,mdd=compound(R,f)
            print(f"  reinvest {f*100:.1f}%/trade -> {eq:7.1f}x  CAGR {cagr*100:3.0f}%  maxDD {mdd*100:3.0f}%   Rs{START:,} -> Rs{int(START*eq):,}")
