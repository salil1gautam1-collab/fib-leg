"""REDESIGN suite — answers three things at once:
1. TF sweep 60/120/180/240m with the finalized engine (0.618, lock-B) x context gates
   (low-VIX + sector-aligned + not-whipsaw). 3H/4H were never compounded before.
2. Refinement combos on the best TF: projected-R:R floor, stop-width band (rf 2-6% was
   the sweet spot in wf_rank), and both.
3. RUPEE fixed-lot sim: a real small account — 1 lot = 1 concurrent position, add a lot
   per +start_capital of equity (the user's own rule), margin as ruin floor. Answers
   "do we really need 20-40L for 1 lot?" in rupees, not R.
Assumptions printed with results: lot value Rs7.5L, hedged margin Rs1.3L, cost 0.15%
round-trip of notional, DOTM caps loss at -1.5R."""
import sys, time, bisect, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import Side, Bar
from fibleg.indicators.trend import AdxStreamer

DIR = sys.argv[1]
import yfinance as yf

def fetch(tk):
    try:
        df = yf.download(tk, start="2014-06-01", end="2026-08-01", interval="1d", progress=False)
        if df is None or df.empty: return None
        def col(n):
            s=df[n]
            if hasattr(s,"columns"): s=s.iloc[:,0]
            return s.to_numpy().flatten().tolist()
        return [d.to_pydatetime() for d in df.index], col("Open"), col("High"), col("Low"), col("Close")
    except Exception as e:
        print("  fetch fail:",tk,e,flush=True); return None

print("fetching benchmarks...",flush=True)
nf=fetch("^NSEI"); MKT=[]
if nf:
    dts,o,h,l,c=nf; adx=AdxStreamer(14)
    for i in range(len(c)):
        a=adx.update(Bar(dts[i],o[i],h[i],l[i],c[i]))
        if i<50: continue
        up=c[i]>sum(c[i-50:i])/50
        MKT.append((dts[i], ("UPT" if up else "DNT") if a>=25 else ("SDW" if a<20 else "WHP")))
MKT_D=[m[0] for m in MKT]
def mkt_at(ts):
    j=bisect.bisect_right(MKT_D,ts)-1
    return MKT[j][1] if j>=0 else None
vx=fetch("^INDIAVIX"); VIX=[]
if vx:
    dts,o,h,l,c=vx
    for i in range(len(c)):
        VIX.append((dts[i], c[i]>sum(c[max(0,i-19):i+1])/min(20,i+1)))
VIX_D=[v[0] for v in VIX]
def vix_hi(ts):
    if not VIX: return None
    j=bisect.bisect_right(VIX_D,ts)-1
    return VIX[j][1] if j>=0 else None
SEC_TK={"BANK":"^NSEBANK","IT":"^CNXIT","AUTO":"^CNXAUTO","FMCG":"^CNXFMCG","PHARMA":"^CNXPHARMA",
        "METAL":"^CNXMETAL","ENERGY":"^CNXENERGY","FIN":"^CNXFIN","REALTY":"^CNXREALTY","PSE":"^CNXPSE"}
SEC={}
for s,tk in SEC_TK.items():
    d=fetch(tk)
    if not d: continue
    dts,o,h,l,c=d; arr=[(dts[i], c[i]>sum(c[i-50:i])/50) for i in range(50,len(c))]
    SEC[s]=(arr,[a[0] for a in arr])
def sec_up(sector,ts):
    if sector not in SEC: return None
    arr,ds=SEC[sector]; j=bisect.bisect_right(ds,ts)-1
    return arr[j][1] if j>=0 else None
S2S={ "HDFCBANK":"BANK","ICICIBANK":"BANK","AXISBANK":"BANK","KOTAKBANK":"BANK","SBIN":"BANK","INDUSINDBK":"BANK","BANKBARODA":"BANK","PNB":"BANK","FEDERALBNK":"BANK","IDFCFIRSTB":"BANK","AUBANK":"BANK",
      "TCS":"IT","INFY":"IT","WIPRO":"IT","HCLTECH":"IT","TECHM":"IT","LTIM":"IT","MPHASIS":"IT","COFORGE":"IT","PERSISTENT":"IT",
      "MARUTI":"AUTO","M&M":"AUTO","TATAMOTORS":"AUTO","BAJAJ-AUTO":"AUTO","HEROMOTOCO":"AUTO","EICHERMOT":"AUTO","ASHOKLEY":"AUTO","TVSMOTOR":"AUTO","BALKRISIND":"AUTO","MOTHERSON":"AUTO",
      "HINDUNILVR":"FMCG","ITC":"FMCG","NESTLEIND":"FMCG","BRITANNIA":"FMCG","DABUR":"FMCG","GODREJCP":"FMCG","MARICO":"FMCG","COLPAL":"FMCG","TATACONSUM":"FMCG","UBL":"FMCG",
      "SUNPHARMA":"PHARMA","DRREDDY":"PHARMA","CIPLA":"PHARMA","DIVISLAB":"PHARMA","AUROPHARMA":"PHARMA","LUPIN":"PHARMA","BIOCON":"PHARMA","TORNTPHARM":"PHARMA","ALKEM":"PHARMA",
      "TATASTEEL":"METAL","JSWSTEEL":"METAL","HINDALCO":"METAL","VEDL":"METAL","COALINDIA":"METAL","JINDALSTEL":"METAL","NMDC":"METAL","SAIL":"METAL","NATIONALUM":"METAL","APLAPOLLO":"METAL",
      "RELIANCE":"ENERGY","ONGC":"ENERGY","NTPC":"ENERGY","POWERGRID":"ENERGY","BPCL":"ENERGY","IOC":"ENERGY","GAIL":"ENERGY","TATAPOWER":"ENERGY","ADANIGREEN":"ENERGY","ADANIENSOL":"ENERGY",
      "BAJFINANCE":"FIN","BAJAJFINSV":"FIN","SBILIFE":"FIN","HDFCLIFE":"FIN","ICICIPRULI":"FIN","CHOLAFIN":"FIN","MUTHOOTFIN":"FIN","SHRIRAMFIN":"FIN","ICICIGI":"FIN",
      "DLF":"REALTY","GODREJPROP":"REALTY","OBEROIRLTY":"REALTY","PRESTIGE":"REALTY","LODHA":"REALTY","PHOENIXLTD":"REALTY" }

def cfg():
    c=StrategyConfig(); c.entry_ratio,c.sl_ratio=0.5,0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.book_reanchor_ratio=0.618; c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3)
    c.entry_dependent_targets=True; c.trail_sl_after_targets=True; c.sl_lock_at_t1=True; return c

tickers=[s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]; N=len(tickers)
TFS=[60,120,180,240]
TR={tf:[] for tf in TFS}
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    sec=S2S.get(tk.upper())
    for tf in TFS:
        eng,_=scan._run_tf({tk:bars},True,tf,cfg(),"book382",1,5)
        for t in eng[tk].trades:
            lg=t.leg
            if lg is None or not t.entry or t.entry_ts is None or t.exit_ts is None: continue
            rf=abs(t.entry-lg.retracement(0.786))/t.entry
            if rf<=0: continue
            rr=abs(lg.extension(1.272)-t.entry)/max(1e-9,abs(t.entry-lg.retracement(0.786)))
            lng=t.side is Side.LONG; su=sec_up(sec,t.entry_ts) if sec else None
            TR[tf].append(dict(ts=t.entry_ts, xs=t.exit_ts, r=t.realized_r, rf=rf, rr=rr,
                               yr=t.entry_ts.year, mkt=mkt_at(t.entry_ts), vhi=vix_hi(t.entry_ts),
                               secok=(su is None or lng==su)))
    if (i+1)%20==0 or i+1==N: print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)",flush=True)

COST=0.15; YEARS=11.3
def nR(seg): return sum(d["r"]-(COST/100)/d["rf"] for d in seg)
def compound(seg,f):
    eq=peak=1.0; mdd=0.0
    for d in sorted(seg,key=lambda d:d["ts"]):
        eq*=(1+f*(max(d["r"],-1.5)-(COST/100)/d["rf"])); peak=max(peak,eq); mdd=min(mdd,eq/peak-1)
    return eq,mdd
def ctx(seg): return [d for d in seg if d["vhi"] is not True and d["secok"] and d["mkt"]!="WHP"]
def show(name,seg):
    if not seg: print(f"    {name:34s}: (no trades)"); return
    e1,d1=compound(seg,0.01); e2,d2=compound(seg,0.02)
    print(f"    {name:34s}: {nR(seg):+6.0f}R · {len(seg):5d} tr · 1%:{e1:5.1f}x DD{d1*100:4.0f}% · 2%:{e2:5.1f}x DD{d2*100:4.0f}%")

print("\n\n================ 1. TF SWEEP (net@0.15% · trades · compound/DD @1% and 2%) ================")
for tf in TFS:
    print(f"\n  --- {tf}m ---")
    show("ALL", TR[tf]); show("CONTEXT (lowVIX+sector+noWhip)", ctx(TR[tf]))

print("\n\n================ 2. REFINEMENTS (on each TF's CONTEXT set) ================")
for tf in TFS:
    C=ctx(TR[tf])
    show(f"{tf}m ctx + R:R>=1.0", [d for d in C if d["rr"]>=1.0])
    show(f"{tf}m ctx + rf 2-6%", [d for d in C if 0.02<=d["rf"]<=0.06])
    show(f"{tf}m ctx + both", [d for d in C if d["rr"]>=1.0 and 0.02<=d["rf"]<=0.06])

print("\n\n================ 3. RUPEE FIXED-LOT SIM (lot Rs7.5L · margin Rs1.3L · cost 0.15% · DOTM cap -1.5R) ================")
print("  Rule: 1 lot = 1 open position; +1 lot per +start_capital of equity; ruin if equity < Rs1.8L.")
def lot_sim(seg, start):
    LOT=750000.0; MARGIN=180000.0
    eq=float(start); peak=eq; mdd=0.0; taken=0; opens=[]
    for d in sorted(seg,key=lambda d:d["ts"]):
        opens=[x for x in opens if x>d["ts"]]
        cap=max(1,int(eq//start))
        if len(opens)>=cap: continue
        if eq<MARGIN: return eq,mdd,taken,True
        eq+=LOT*(max(d["r"],-1.5)*d["rf"]-COST/100)
        peak=max(peak,eq); mdd=min(mdd,eq/peak-1); taken+=1
        opens.append(d["xs"])
    return eq,mdd,taken,False
BEST={}
for tf in TFS:
    C=[d for d in ctx(TR[tf]) if d["rr"]>=1.0]
    BEST[tf]=C
for tf in (120,180,240):
    seg=BEST[tf]
    if not seg: continue
    print(f"\n  -- {tf}m context+R:R (taken serially, capacity-limited):")
    for start in (300000,500000,800000,1200000,2000000,3000000):
        eq,mdd,tk_,ruin=lot_sim(seg,start)
        cagr=((eq/start)**(1/YEARS)-1)*100 if eq>0 else -100
        print(f"     start Rs{start//100000:>3}L -> Rs{eq/100000:7.1f}L · {tk_:4d} trades taken · maxDD {mdd*100:4.0f}% · CAGR {cagr:5.1f}%{'  ** RUIN **' if ruin else ''}")

print("\n\n================ 4. PER-YEAR (best TF context+R:R, net R) ================")
for tf in (120,180,240):
    seg=BEST[tf]
    ys=" ".join(f"{y}:{nR([d for d in seg if d['yr']==y]):+.0f}" for y in range(2015,2027))
    print(f"  {tf}m: {ys}")
