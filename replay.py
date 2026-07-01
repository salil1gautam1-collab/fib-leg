"""Replay a stock bar-by-bar (2H) and log the leg AS IT FORMS — so you can cross-check
against TradingView: is the fib drawn where you'd draw it, using only past data?
Also runs a no-look-ahead check: the leg recorded live == re-running truncated to that bar."""
import sys, scan
from fibleg.strategy.fib_leg import FibLegEngine
from fibleg.config import StrategyConfig
from fibleg.data import feeds

DIR="C:/Salil Claude/fib-leg/fibleg/data/Stocks_data"
stock = sys.argv[1] if len(sys.argv)>1 else "TCS"
year  = int(sys.argv[2]) if len(sys.argv)>2 else 2015

def cfg():
    c=StrategyConfig(); c.entry_ratio,c.sl_ratio=0.5,0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.require_mw=True; c.entry_dependent_targets=True
    c.targets=(0.95,1.272,1.618); c.target_fractions=(1/3,1/3,1/3); return c

bars1m = feeds.csv_dir_series(DIR, stock)
setup = feeds.resample(bars1m, 120)   # 2H bars
print(f"{stock}: {len(setup)} 2H bars, {setup[0].ts.date()} -> {setup[-1].ts.date()}\n")

eng = FibLegEngine(stock, cfg(), "book382")
live_legs=[]   # (index, leg_sig) recorded during the forward pass
def sig(cl):
    if cl is None: return None
    o,e,d = cl
    return (d, round(o.price,2), o.index, round(e.price,2), e.index)

prev=None; mo=None
for i,b in enumerate(setup):
    eng.on_setup_bar(b)
    cl = eng.current_leg(); s=sig(cl)
    live_legs.append(s)
    if b.ts.year != year: prev=s; mo=b.ts.month; continue
    # log a monthly snapshot + any direction flip within the year
    flip = (prev and s and prev[0]!=s[0])
    newmo = (b.ts.month != mo)
    if (newmo or flip) and cl is not None:
        o,e,d=cl; dirn="UP" if d==1 else "DOWN"
        tag = " <== FLIP (new impulse)" if flip else ""
        print(f"  {b.ts.date()}  px={b.close:7.1f}  leg {dirn:4s}: {round(o.price,1)} ({o.ts.date()}) -> {round(e.price,1)} ({e.ts.date()}){tag}")
    prev=s; mo=b.ts.month

# --- no-look-ahead check: re-run truncated to a few sample bars ---
print("\nNO-LOOK-AHEAD CHECK (live leg == truncated re-run):")
idxs=[i for i,b in enumerate(setup) if b.ts.year==year][::40][:6]
ok=True
for T in idxs:
    e2=FibLegEngine(stock,cfg(),"book382")
    for b in setup[:T+1]: e2.on_setup_bar(b)
    match = sig(e2.current_leg())==live_legs[T]
    ok = ok and match
    print(f"   bar {T} ({setup[T].ts.date()}): {'MATCH' if match else 'MISMATCH — LOOK-AHEAD!'}")
print("  =>", "PASS — leg uses only past data" if ok else "FAIL — repaint detected")
