"""Generates docs/backtest.json — the full-history backtest the app's History>Backtest
tab slices client-side (any year range, ⭐ Best vs All).

Runs the VALIDATED config (2H · book382 + 0.618 re-anchor · zone entry · 0.786 close SL
· lock-at-B · 5m trigger) over the local 1-min universe, stamps every trade with its
market-context verdict (fibleg.context: VIX / sector / regime / R:R), and writes a
compact per-trade list: {s: symbol, sd: L/S, y/m/d: entry date, r: realized R,
rf: risk fraction, c: ctx-pass 0/1}.

Run:  python gen_backtest.py "fibleg/data/Stocks_data"   (~12 min)"""
import json, sys, time

import scan
from fibleg.config import StrategyConfig
from fibleg.context import MarketContext
from fibleg.data import feeds
from fibleg.models import Side

DIR = sys.argv[1] if len(sys.argv) > 1 else "fibleg/data/Stocks_data"

def cfg():
    c = StrategyConfig(); c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry = c.nested_entry = c.zone_respect = c.zone_pin_respect = True
    c.zone_frac = 0.05
    c.book_reanchor_ratio = 0.618
    c.targets = (0.95, 1.272, 1.618); c.target_fractions = (1/3, 1/3, 1/3)
    c.entry_dependent_targets = True; c.trail_sl_after_targets = True; c.sl_lock_at_t1 = True
    return c

print("loading market context (Nifty/VIX/sectors)...", flush=True)
mc = MarketContext.load()
tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
out, t0 = [], time.time()
for i, tk in enumerate(tickers):
    try:
        bars = feeds.csv_dir_series(DIR, tk)
    except Exception as e:  # noqa: BLE001
        print("skip", tk, e, flush=True); continue
    eng, _ = scan._run_tf({tk: bars}, True, 120, cfg(), "book382", 1, 5)
    for t in eng[tk].trades:
        lg = t.leg
        if lg is None or not t.entry or t.entry_ts is None:
            continue
        rf = abs(t.entry - lg.retracement(0.786)) / t.entry
        if rf <= 0:
            continue
        is_long = t.side is Side.LONG
        fl = mc.flags(tk, t.entry_ts, is_long, t.entry, lg.retracement(0.786),
                      lg.extension(1.272))
        out.append({"s": tk, "sd": "L" if is_long else "S",
                    "y": t.entry_ts.year, "m": t.entry_ts.month, "d": t.entry_ts.day,
                    "r": round(t.realized_r, 3), "rf": round(rf, 4),
                    "c": 1 if fl["pass"] else 0})
    if (i + 1) % 25 == 0 or i + 1 == len(tickers):
        print(f"  [{i+1}/{len(tickers)}] ({time.time()-t0:.0f}s)", flush=True)

out.sort(key=lambda t: (t["y"], t["m"], t["d"]))
payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "config": "2H · book382+0.618 · zone 0.5-0.618 · SL 0.786 close · lock-at-B · 5m trigger",
           "cost_pct": 0.15, "dotm_cap_r": -1.5,
           "year_min": out[0]["y"] if out else None, "year_max": out[-1]["y"] if out else None,
           "trades": out}
open("docs/backtest.json", "w").write(json.dumps(payload, separators=(",", ":")))
best = [t for t in out if t["c"]]
print(f"wrote docs/backtest.json: {len(out)} trades {payload['year_min']}-{payload['year_max']}, "
      f"{len(best)} ctx-pass, {round(100*sum(1 for t in out if t['r']>0)/len(out))}% win")
