"""Walk-forward on the CURRENT v5 legs (book382, 0.382 re-anchor). Fixed 2H, entry-dep
1/d targets + trail. Tests two exits — lock-at-B (the +214R recipe) vs breakeven — tags
each trade by ENTRY YEAR; reports per-year + train 2015-2020 vs test 2021-2026 net R.
Net of 0.10% hard-stop cost. No M/W filter (raw leg edge)."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
avail = feeds.csv_dir_symbols(DIR)
tickers = [s for s in avail if "NIFTY" not in s.upper()]
print(f"{len(tickers)} stocks, 2H, book382 (v5 legs), pin+close respect, net of 0.10% hard-stop\n", flush=True)

def base():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.trail_sl_after_targets=True; c.entry_dependent_targets=True
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); return c
def cfg_lockb():
    c=base(); c.sl_lock_at_t1=True; return c
def cfg_be():
    c=base(); c.move_sl_to_be_after_tp1=True; return c
CFG = {"lock-at-B": cfg_lockb(), "breakeven": cfg_be()}

rows={k:[] for k in CFG}   # (realized_r, risk_frac, year)
t0=time.time()
for i,tk in enumerate(tickers):
    try: bars=feeds.csv_dir_series(DIR,tk)
    except Exception as e: print("skip",tk,e,flush=True); continue
    for k,c in CFG.items():
        eng,_=scan._run_tf({tk:bars},True,120,c,"book382",1,5)
        e=eng[tk]
        for t in e.trades:
            if t.leg is None or not t.entry or t.entry_ts is None: continue
            rf=abs(t.entry - t.leg.retracement(0.786))/t.entry
            if rf>0: rows[k].append((t.realized_r, rf, t.entry_ts.year))
    if (i+1)%25==0 or i+1==len(tickers): print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)",flush=True)

def netR(R):
    return sum(r[0]-(0.10/100)/r[1] for r in R)
def line(R):
    if not R: return f"{'-':>8}"
    w=sum(1 for r in R if r[0]>0)
    return f"{netR(R):+8.1f}R/{len(R):5d}/{round(100*w/len(R))}%"

years=list(range(2015,2027))
for k,R in rows.items():
    print(f"\n=== {k}  (net of hard-stop costs) ===")
    print("  per year:")
    for y in years:
        yr=[r for r in R if r[2]==y]
        if yr: print(f"     {y}:  {line(yr)}")
    tr=[r for r in R if r[2]<=2020]; te=[r for r in R if r[2]>=2021]
    print(f"  TRAIN 2015-2020: {line(tr)}")
    print(f"  TEST  2021-2026: {line(te)}")
    print(f"  FULL  2015-2026: {line(R)}")
