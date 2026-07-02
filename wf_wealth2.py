"""DEFINITIVE wealth sim. 45m & 2H x full/partial/lockb x All & Reversal+trend, on the
finalized 0.618 engine, 115 stocks, 11 yrs. Orders trades by date, compounds equity
(reinvest) net of 0.15% round-trip cost + DOTM gap-cap at -1.5R, across a RANGE of
risk-per-trade levels. Reports growth multiple, CAGR, max drawdown, and yearly equity
(for the withdrawal question). ASSUMES own capital, no borrowed margin."""
import sys, time, scan
from fibleg.config import StrategyConfig
from fibleg.data import feeds

DIR = sys.argv[1]
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
N = len(tickers)
COST, DOTM_CAP, YEARS = 0.15, 1.5, 11.3
FS = [0.01, 0.02, 0.05, 0.10]          # risk-per-trade levels

def make(exit_, filt):
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry=c.nested_entry=c.zone_respect=c.zone_pin_respect=True; c.zone_frac=0.05
    c.book_reanchor_ratio = 0.618
    if exit_ == "full":
        c.targets, c.target_fractions = (0.95,), (1.0,)
    else:
        c.targets, c.target_fractions = (0.95,1.272,1.618), (1/3,1/3,1/3)
        c.entry_dependent_targets = True; c.trail_sl_after_targets = True
        if exit_ == "lockb": c.sl_lock_at_t1 = True
        else: c.move_sl_to_be_after_tp1 = True
    if filt == "rev":
        c.require_mw = True; c.reversal_pin = True; c.require_htf = True
    return c

TFS = [45, 120]; EXITS = ["full", "partial", "lockb"]; FILTS = [("all","All"), ("rev","Rev+trend")]
KEYS = [(tf,ex,fn) for tf in TFS for ex in EXITS for (fk,fn) in FILTS]
CFGS = {(tf,ex,fn): make(ex, fk) for tf in TFS for ex in EXITS for (fk,fn) in FILTS}
rows = {k: [] for k in KEYS}

t0 = time.time()
for i, tk in enumerate(tickers):
    try: bars = feeds.csv_dir_series(DIR, tk)
    except Exception as e: print("skip", tk, e, flush=True); continue
    for k in KEYS:
        eng, _ = scan._run_tf({tk: bars}, True, k[0], CFGS[k], "book382", 1, 5)
        for t in eng[tk].trades:
            if t.leg is None or not t.entry or t.entry_ts is None: continue
            rf = abs(t.entry - t.leg.retracement(0.786)) / t.entry
            if rf > 0: rows[k].append((t.entry_ts, t.realized_r, rf))
    if (i+1) % 20 == 0 or i+1 == N: print(f"  [{i+1}/{N}] ({time.time()-t0:.0f}s)", flush=True)

def netR(R): return sum(r - (COST/100)/rf for (_, r, rf) in R)
def compound(R, f):
    R = sorted(R, key=lambda x: x[0])
    eq = peak = 1.0; mdd = 0.0; yr = {}
    for (ts, r, rf) in R:
        nr = max(r, -DOTM_CAP) - (COST/100)/rf
        eq *= (1 + f*nr)
        if eq <= 0: eq = 1e-9
        peak = max(peak, eq); mdd = min(mdd, eq/peak - 1)
        yr[ts.year] = eq
    return eq, eq**(1/YEARS)-1, mdd, yr

print("\n\n================ RESULTS ================")
best = None
for k in KEYS:
    R = rows[k]; w = sum(1 for (_, r, _) in R if r > 0)
    print(f"\n=== {k[0]}m · {k[1]} · {k[2]} ===")
    print(f"  {netR(R):+.0f}R net · {len(R)} trades · {round(100*w/len(R)) if R else 0}% win · {len(R)/YEARS:.0f}/yr · ~{len(R)/YEARS/250:.1f}/day")
    for f in FS:
        eq, cagr, mdd, _ = compound(R, f)
        tag = " RUIN" if mdd < -0.85 else ""
        print(f"    risk {int(f*100):2d}%/trade -> {eq:10.1f}x  CAGR {cagr*100:4.0f}%  maxDD {mdd*100:4.0f}%{tag}")
        if -0.60 < mdd and (best is None or eq > best[1]):
            best = (k, eq, cagr, mdd, f)

if best:
    k, eq, cagr, mdd, f = best
    print(f"\n\n### BEST survivable (maxDD>-60%): {k[0]}m {k[1]} {k[2]} @ risk {int(f*100)}% -> {eq:.0f}x, CAGR {cagr*100:.0f}%, maxDD {mdd*100:.0f}%")
    _, _, _, yr = compound(rows[k], f)
    print("  yearly equity multiple:", {y: round(v, 1) for y, v in sorted(yr.items())})
