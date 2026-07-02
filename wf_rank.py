"""Does RANKING trades find a thicker edge? Score every 2H trade (0.618, lock-B, ALL setups)
by EX-ANTE factors — projected R:R, index (Nifty) trend alignment, leg size, stop tightness,
M/W, confluence — and test whether high-ranked trades have materially higher realized R
per trade (which is what would justify taking only the best few). No look-ahead: all factors
known at entry; realized_r is the outcome we're predicting."""
import sys, time, bisect, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import Side

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
N = len(tickers)

# --- Nifty daily trend (index alignment) ---
idx_dates, idx_up = [], []
try:
    import yfinance as yf
    nf = yf.download("^NSEI", start="2014-06-01", end="2026-08-01", interval="1d", progress=False)
    closes = nf["Close"].squeeze().tolist(); dts = [d.to_pydatetime() for d in nf.index]
    sma = [None]*len(closes)
    for i in range(len(closes)):
        if i>=50: sma[i]=sum(closes[i-50:i])/50
    for i in range(len(closes)):
        if sma[i] is not None:
            idx_dates.append(dts[i]); idx_up.append(closes[i] > sma[i])
    print(f"Nifty trend loaded: {len(idx_dates)} days", flush=True)
except Exception as e:
    print("Nifty fetch failed, index_align disabled:", e, flush=True)

def index_up_at(ts):
    if not idx_dates: return None
    j = bisect.bisect_right(idx_dates, ts) - 1
    return idx_up[j] if j >= 0 else None

def cfg():
    c=StrategyConfig(); c.entry_ratio,c.sl_ratio=0.5,0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.book_reanchor_ratio=0.618
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3)
    c.entry_dependent_targets=True; c.trail_sl_after_targets=True; c.sl_lock_at_t1=True
    return c

T=[]   # per trade dict
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    eng,_=scan._run_tf({tk:bars},True,120,cfg(),"book382",1,5); e=eng[tk]
    for t in e.trades:
        lg=t.leg
        if lg is None or not t.entry or t.entry_ts is None: continue
        stop=lg.retracement(0.786); rf=abs(t.entry-stop)/t.entry
        if rf<=0: continue
        rr=abs(lg.extension(1.272)-t.entry)/max(1e-9,abs(t.entry-stop))
        iu=index_up_at(t.entry_ts)
        ialign = None if iu is None else ((t.side is Side.LONG)==iu)
        T.append(dict(r=t.realized_r, rf=rf, rr=rr, legpct=lg.rng/t.entry,
                      mw=e.mw_confirmed(lg), conf=e.confluence_leg(lg), ialign=ialign, side=t.side))
    if (i+1)%25==0 or i+1==N: print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)",flush=True)

def avg(x): return sum(x)/len(x) if x else 0.0
allr=[d["r"] for d in T]
print(f"\n\n===== {len(T)} trades · overall avg R/trade = {avg(allr):+.4f} =====")

def quintiles(key, name):
    S=sorted(T, key=lambda d: d[key]); n=len(S); q=n//5
    print(f"\n-- by {name} (low -> high), avg R/trade per quintile:")
    for k in range(5):
        seg=S[k*q:(k+1)*q] if k<4 else S[4*q:]
        rs=[d["r"] for d in seg]
        print(f"    Q{k+1} ({name} {S[k*q][key]:+.3f}..): avg R {avg(rs):+.4f}  (netR {sum(rs):+.0f} / {len(rs)})")
for key,name in [("rr","projected R:R"),("legpct","leg size %"),("rf","stop width (rf)")]:
    quintiles(key,name)

# boolean factors
for key,name in [("mw","M/W"),("conf","confluence A+"),("ialign","index-aligned")]:
    yes=[d["r"] for d in T if d[key] is True]; no=[d["r"] for d in T if d[key] is False]
    print(f"\n-- {name}: YES avg R {avg(yes):+.4f} ({len(yes)}) | NO avg R {avg(no):+.4f} ({len(no)})")

# composite rank (percentile-rank each numeric+bool factor, sum)
def prank(key):
    vals=sorted(d[key] for d in T if isinstance(d[key],(int,float)))
    return lambda v: bisect.bisect_left(vals,v)/max(1,len(vals))
pr_rr, pr_lp = prank("rr"), prank("legpct")
for d in T:
    d["score"]= pr_rr(d["rr"]) + pr_lp(d["legpct"]) + (0.5 if d["mw"] else 0) + (0.5 if d["conf"] else 0) + (0.5 if d["ialign"] else 0) - (0.5 if d["ialign"] is False else 0)
S=sorted(T,key=lambda d:-d["score"])
print("\n-- COMPOSITE rank (best first): avg R/trade of the top slice:")
for pct in (0.05,0.10,0.25,0.50,1.0):
    seg=S[:max(1,int(len(S)*pct))]; rs=[d["r"] for d in seg]
    print(f"    top {int(pct*100):3d}%: avg R {avg(rs):+.4f}  (netR {sum(rs):+.0f} / {len(rs)} trades)")
