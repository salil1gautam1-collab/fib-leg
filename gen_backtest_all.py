"""Generates docs/backtest_{tf}.json for EVERY TF x exit combination so the app's
Backtest tab is fully interactive (TF x exit x setup-filter x year range).

Per TF file: {tf, cost_pct, dotm_cap_r, year_min/max, exits: {full|partial|lockb:
[{s,sd,y,m,d,r,rf,f}]}} where f is a bitmask: 1=ctx-pass 2=M/W 4=pin 8=A+ 16=HTF.
Method fixed at the validated book382 + 0.618 re-anchor, trigger fixed at 5m.

Run:  python gen_backtest_all.py "fibleg/data/Stocks_data"   (~2-3 hrs, 15 engine runs)"""
import json, sys, time

import scan
from fibleg.config import StrategyConfig
from fibleg.context import MarketContext
from fibleg.data import feeds
from fibleg.models import Side

DIR = sys.argv[1] if len(sys.argv) > 1 else "fibleg/data/Stocks_data"
TFS = [45, 60, 120, 180, 240]
EXITS = ["full", "partial", "lockb"]

def cfg(exit_):
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry = c.nested_entry = c.zone_respect = c.zone_pin_respect = True
    c.zone_frac = 0.05
    c.book_reanchor_ratio = 0.618
    if exit_ == "full":
        c.targets, c.target_fractions = (0.95,), (1.0,)
    else:
        c.targets, c.target_fractions = (0.95, 1.272, 1.618), (1/3, 1/3, 1/3)
        c.entry_dependent_targets = True; c.trail_sl_after_targets = True
        if exit_ == "lockb": c.sl_lock_at_t1 = True
        else: c.move_sl_to_be_after_tp1 = True
    return c

print("loading market context (Nifty/VIX/sectors)...", flush=True)
mc = MarketContext.load()
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
t0 = time.time()
for tf in TFS:
    exits = {e: [] for e in EXITS}
    for ex in EXITS:
        c = cfg(ex)
        for i, tk in enumerate(tickers):
            try:
                bars = feeds.csv_dir_series(DIR, tk)
            except Exception as err:  # noqa: BLE001
                print("skip", tk, err, flush=True); continue
            eng, _ = scan._run_tf({tk: bars}, True, tf, c, "book382", 1, 5)
            e = eng[tk]
            for t in e.trades:
                lg = t.leg
                if lg is None or not t.entry or t.entry_ts is None:
                    continue
                rf = abs(t.entry - lg.retracement(0.786)) / t.entry
                if rf <= 0:
                    continue
                is_long = t.side is Side.LONG
                fl = mc.flags(tk, t.entry_ts, is_long, t.entry,
                              lg.retracement(0.786), lg.extension(1.272))
                f = (1 if fl["pass"] else 0)
                try:
                    if e.mw_confirmed(lg): f |= 2
                    if e.pin_bar_confirmed(lg): f |= 4
                    if e.confluence_leg(lg): f |= 8
                    if e.htf_confirms(lg): f |= 16
                except Exception:  # noqa: BLE001
                    pass
                exits[ex].append({"s": tk, "sd": "L" if is_long else "S",
                                  "y": t.entry_ts.year, "m": t.entry_ts.month,
                                  "d": t.entry_ts.day, "r": round(t.realized_r, 3),
                                  "rf": round(rf, 4), "f": f})
        exits[ex].sort(key=lambda t: (t["y"], t["m"], t["d"]))
        print(f"  [{tf}m {ex}] {len(exits[ex])} trades ({time.time()-t0:.0f}s)", flush=True)
    ys = [t["y"] for e in EXITS for t in exits[e]]
    payload = {"tf": tf, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "config": f"{tf}m · book382+0.618 · zone 0.5-0.618 · SL 0.786 close · 5m trigger",
               "cost_pct": 0.15, "dotm_cap_r": -1.5,
               "year_min": min(ys) if ys else None, "year_max": max(ys) if ys else None,
               "exits": exits}
    open(f"docs/backtest_{tf}.json", "w").write(json.dumps(payload, separators=(",", ":")))
    print(f"wrote docs/backtest_{tf}.json", flush=True)
print(f"done in {time.time()-t0:.0f}s")
