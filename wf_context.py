"""MARKET-CONTEXT test: does aligning trades with (1) market regime [Nifty trend + ADX
=> uptrend/downtrend/SIDEWAYS], (2) sector trend, (3) sentiment [India VIX] improve the
edge and — crucially — CUT the drawdown and clean up the losing years? Index/sector/VIX
fetched LIVE from yfinance (benchmarks, not from Stocks_data). 2H/0.618/lockb/ALL trades.
No look-ahead: every context flag uses data up to the trade's entry date."""
import sys, time, bisect, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import Side, Bar
from fibleg.indicators.trend import AdxStreamer

DIR = sys.argv[1]
import yfinance as yf

def fetch(ticker):
    try:
        df = yf.download(ticker, start="2014-06-01", end="2026-08-01", interval="1d", progress=False)
        if df is None or df.empty: print("  empty:",ticker,flush=True); return None
        def col(n):
            s=df[n]
            if hasattr(s,"columns"): s=s.iloc[:,0]
            return s.to_numpy().flatten().tolist()
        dts=[d.to_pydatetime() for d in df.index]
        return dts, col("Open"), col("High"), col("Low"), col("Close")
    except Exception as e:
        print("  fetch fail:",ticker,e,flush=True); return None

# ---- market regime from Nifty: direction (50d SMA) + ADX(14) ----
print("fetching benchmarks...",flush=True)
nf = fetch("^NSEI"); MKT=[]
if nf:
    dts,o,h,l,c=nf; adx=AdxStreamer(14)
    for i in range(len(c)):
        a=adx.update(Bar(dts[i],o[i],h[i],l[i],c[i]))
        if i<50: continue
        up = c[i] > sum(c[i-50:i])/50
        state = ("UPT" if up else "DNT") if a>=25 else ("SDW" if a<20 else ("UPTw" if up else "DNTw"))
        MKT.append((dts[i], state, up))
    print(f"  market regime: {len(MKT)} days",flush=True)
MKT_D=[m[0] for m in MKT]
def mkt_at(ts):
    j=bisect.bisect_right(MKT_D,ts)-1
    return MKT[j][1] if j>=0 else None
def mkt_up_at(ts):
    j=bisect.bisect_right(MKT_D,ts)-1
    return MKT[j][2] if j>=0 else None

# ---- India VIX ----
vx=fetch("^INDIAVIX"); VIX=[]
if vx:
    dts,o,h,l,c=vx
    for i in range(len(c)):
        s=sum(c[max(0,i-19):i+1])/min(20,i+1)
        VIX.append((dts[i], c[i]>s))
    print(f"  VIX: {len(VIX)} days",flush=True)
VIX_D=[v[0] for v in VIX]
def vix_hi_at(ts):
    if not VIX: return None
    j=bisect.bisect_right(VIX_D,ts)-1
    return VIX[j][1] if j>=0 else None

# ---- sector indices + stock->sector map (best effort) ----
SEC_TK={"BANK":"^NSEBANK","IT":"^CNXIT","AUTO":"^CNXAUTO","FMCG":"^CNXFMCG","PHARMA":"^CNXPHARMA",
        "METAL":"^CNXMETAL","ENERGY":"^CNXENERGY","FIN":"^CNXFIN","REALTY":"^CNXREALTY","PSE":"^CNXPSE"}
SEC={}
for s,tk in SEC_TK.items():
    d=fetch(tk)
    if not d: continue
    dts,o,h,l,c=d; arr=[]
    for i in range(len(c)):
        if i<50: continue
        arr.append((dts[i], c[i]>sum(c[i-50:i])/50))
    SEC[s]=(arr,[a[0] for a in arr])
def sec_up_at(sector,ts):
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
T=[]; t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    eng,_=scan._run_tf({tk:bars},True,120,cfg(),"book382",1,5)
    for t in eng[tk].trades:
        lg=t.leg
        if lg is None or not t.entry or t.entry_ts is None: continue
        rf=abs(t.entry-lg.retracement(0.786))/t.entry
        if rf<=0: continue
        lng=t.side is Side.LONG; st=mkt_at(t.entry_ts); mu=mkt_up_at(t.entry_ts)
        sec=S2S.get(tk.upper()); su=sec_up_at(sec,t.entry_ts) if sec else None
        T.append(dict(r=t.realized_r, rf=rf, yr=t.entry_ts.year, lng=lng, mkt=st, mkt_up=mu,
                      vix_hi=vix_hi_at(t.entry_ts), sec_up=su, sec=sec))
    if (i+1)%25==0 or i+1==N: print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)",flush=True)

def avg(x): return sum(x)/len(x) if x else 0.0
def nR(seg,cost=0.15): return sum(d["r"]-(cost/100)/d["rf"] for d in seg)   # net R
def compound(seg,f=0.01):
    eq=peak=1.0; mdd=0.0
    for d in sorted(seg,key=lambda d:(d["yr"],)):
        nr=max(d["r"],-1.5)-(0.15/100)/d["rf"]; eq*=(1+f*nr); peak=max(peak,eq); mdd=min(mdd,eq/peak-1)
    return eq, mdd

print(f"\n\n===== {len(T)} trades · overall avg R {avg([d['r'] for d in T]):+.4f} · net {nR(T):+.0f}R =====")

def aligned(d):   # trade agrees with an ACTIVE market trend
    return (d["mkt"]=="UPT" and d["lng"]) or (d["mkt"]=="DNT" and not d["lng"])
def counter(d):
    return (d["mkt"]=="UPT" and not d["lng"]) or (d["mkt"]=="DNT" and d["lng"])
BUCK={"trend-ALIGNED":[d for d in T if aligned(d)],
      "trend-COUNTER":[d for d in T if counter(d)],
      "SIDEWAYS":[d for d in T if d["mkt"]=="SDW"],
      "weak-trend":[d for d in T if d["mkt"] in ("UPTw","DNTw")]}
print("\n-- MARKET REGIME (avg R/trade, net R, count):")
for k,seg in BUCK.items():
    print(f"    {k:16s}: avg R {avg([d['r'] for d in seg]):+.4f}  net {nR(seg):+6.0f}R  ({len(seg)})")

print("\n-- VIX (India VIX vs its 20d avg):")
for lab,cond in [("elevated",True),("low",False)]:
    seg=[d for d in T if d["vix_hi"] is cond]
    print(f"    VIX {lab:9s}: avg R {avg([d['r'] for d in seg]):+.4f}  net {nR(seg):+6.0f}R  ({len(seg)})")

print("\n-- SECTOR alignment (mapped stocks only):")
sa=[d for d in T if d["sec_up"] is not None and d["lng"]==d["sec_up"]]
sn=[d for d in T if d["sec_up"] is not None and d["lng"]!=d["sec_up"]]
print(f"    sector-ALIGNED : avg R {avg([d['r'] for d in sa]):+.4f}  net {nR(sa):+6.0f}R  ({len(sa)})")
print(f"    sector-COUNTER : avg R {avg([d['r'] for d in sn]):+.4f}  net {nR(sn):+6.0f}R  ({len(sn)})")

# ---- CORRECTED filters: build up the context stack, show return + drawdown of each ----
def secok(d): return d["sec_up"] is None or d["lng"]==d["sec_up"]
def notwhip(d): return d["mkt"] not in ("UPTw","DNTw")     # skip ADX 20-25 whipsaw
def lowvix(d): return d["vix_hi"] is not True
FILTERS={
 "ALL": T,
 "low-VIX": [d for d in T if lowvix(d)],
 "low-VIX + sector-aligned": [d for d in T if lowvix(d) and secok(d)],
 "low-VIX + not-whipsaw": [d for d in T if lowvix(d) and notwhip(d)],
 "low-VIX + sector + not-whipsaw": [d for d in T if lowvix(d) and secok(d) and notwhip(d)],
 "SIDEWAYS + low-VIX + sector": [d for d in T if lowvix(d) and secok(d) and d["mkt"]=="SDW"],
}
print("\n-- CONTEXT FILTER STACK (net R · trades · 1%-compound · maxDD):")
for name,seg in FILTERS.items():
    e,dd=compound(seg)
    print(f"    {name:34s}: {nR(seg):+6.0f}R · {len(seg):5d} tr · {e:5.1f}x · maxDD {dd*100:4.0f}%")
CTX=FILTERS["low-VIX + sector + not-whipsaw"]
print("\n   PER-YEAR net R:      ALL      CONTEXT(low-VIX+sector+not-whipsaw)")
for y in range(2015,2027):
    a=[d for d in T if d["yr"]==y]; b=[d for d in CTX if d["yr"]==y]
    print(f"     {y}:   {nR(a):+8.0f}   {nR(b):+8.0f}")
