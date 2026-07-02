"""v5 legs, 2H book382, lock-at-B + entry-dep + trail. A/B the NEW per-stock confluence
gates (setup-time, no look-ahead): EMA trend, ADX chop, Volume thrust — singles + combos.
Train/test/full net R at 0.10% cost."""
import sys, time, scan
from fibleg.data import feeds
from fibleg.config import StrategyConfig

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
print(f"{len(tickers)} stocks, 2H book382 v5, lock-at-B — confluence A/B\n", flush=True)

def base():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.trail_sl_after_targets=True; c.entry_dependent_targets=True; c.sl_lock_at_t1=True
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); return c
def cfg(**kw):
    c=base()
    for k,v in kw.items(): setattr(c,k,v)
    return c
CFG = {
    "baseline":       base(),
    "+EMA trend":     cfg(require_ema_trend=True),
    "+ADX>25":        cfg(require_adx=True),
    "+Volume":        cfg(require_volume=True),
    "+EMA+ADX":       cfg(require_ema_trend=True, require_adx=True),
    "+EMA+ADX+Vol":   cfg(require_ema_trend=True, require_adx=True, require_volume=True),
}

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

def netR(R,cost=0.10): return sum(r[0]-(cost/100)/r[1] for r in R)
def line(R):
    if not R: return f"{'-':>10}"
    w=sum(1 for r in R if r[0]>0)
    return f"{netR(R):+8.1f}R/{len(R):5d}/{round(100*w/len(R))}%  (avg {netR(R)/len(R):+.4f})"

print(f"\n{'config':16s} {'FULL':>34}")
for k,R in rows.items():
    tr=[r for r in R if r[2]<=2020]; te=[r for r in R if r[2]>=2021]
    print(f"\n=== {k} ===")
    print(f"  TRAIN 2015-2020: {line(tr)}")
    print(f"  TEST  2021-2026: {line(te)}")
    print(f"  FULL  2015-2026: {line(R)}")
