#!/usr/bin/env python3
"""Learn from your manual corrections.

You Export corrections in the app (Settings → Export) — a JSON like:
    {"RELIANCE.NS": {"start": 1253.6, "end": 1344.8}, ...}

This finds, for each corrected symbol, the detection settings (timeframe +
ATR noise floor + min-leg size) whose AUTO leg best matches YOUR leg — then
reports the global settings that match your style across the most symbols.

    python tune.py corrections.json
"""
from __future__ import annotations

import json
import sys

from fibleg.backtest import driver
from fibleg.config import StrategyConfig
from fibleg.data import feeds

TFS = (2, 3, 4)              # detection timeframes
ATR_MULTS = (1.0, 1.5, 2.0)  # noise floors
MIN_LEGS = (3.0, 5.0, 7.0)   # min leg size (×ATR)


def err(leg, want_start, want_end):
    return abs(leg[0] - want_start) / want_start + abs(leg[1] - want_end) / want_end


def main() -> None:
    corrections = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "corrections.json"))
    tally: dict[tuple, int] = {}
    print(f"Tuning against {len(corrections)} corrections…\n")
    for sym, c in corrections.items():
        ws, we = float(c["start"]), float(c["end"])
        try:
            h1, m15 = feeds.yfinance_dual(sym)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym:14} fetch failed: {e}"); continue
        if not h1:
            print(f"  {sym:14} no data"); continue
        best = None
        for f in TFS:
            setup = feeds.resample(h1, f)
            for am in ATR_MULTS:
                for ml in MIN_LEGS:
                    cfg = StrategyConfig(setup_factor=f, atr_mult=am, min_leg_atr=ml)
                    eng = driver.run_dual(sym, setup, m15, cfg)
                    cl = eng.current_leg()
                    if not cl:
                        continue
                    e = err((cl[0], cl[1]), ws, we)
                    if best is None or e < best[0]:
                        best = (e, f, am, ml, (round(cl[0], 1), round(cl[1], 1)))
        if best:
            _, f, am, ml, leg = best
            tally[(f, am, ml)] = tally.get((f, am, ml), 0) + 1
            ok = "✓" if best[0] < 0.03 else "≈" if best[0] < 0.08 else "✗"
            print(f"  {sym:14} you {ws:.0f}->{we:.0f}  best {ok} {f}H atr={am} minleg={ml} "
                  f"-> {leg}  (err {best[0]:.3f})")
        else:
            print(f"  {sym:14} no leg detected at any setting")

    if tally:
        win = max(tally, key=tally.get)
        print(f"\nBest GLOBAL setting: {win[0]}H, atr_mult={win[1]}, min_leg_atr={win[2]} "
              f"(matches {tally[win]}/{len(corrections)} symbols)")
        print("→ set these as defaults in StrategyConfig.")


if __name__ == "__main__":
    main()
