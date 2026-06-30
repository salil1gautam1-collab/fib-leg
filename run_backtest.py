#!/usr/bin/env python3
"""Offline-runnable backtest entry point.

    python run_backtest.py                 # synthetic data, no installs needed
    python run_backtest.py --yf RELIANCE.NS # real data (needs: pip install yfinance)

Prints the per-symbol signal log and an aggregate R-multiple report.
"""
from __future__ import annotations

import argparse
import sys

# Windows consoles default to cp1252; the report uses box-drawing chars.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

from fibleg.backtest import driver, engine, report
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import Signal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yf", nargs="*", help="Yahoo tickers (else synthetic)")
    ap.add_argument("--fyers", nargs="*", help="Fyers tickers (needs fyers_login.py)")
    ap.add_argument("--dhan", nargs="*", help="Dhan tickers (needs ~/.fibleg/dhan.json)")
    ap.add_argument("--days", type=int, default=365, help="Fyers history lookback (days)")
    ap.add_argument("--dual", action="store_true",
                    help="dual timeframe: 1H setup + 15m trigger")
    ap.add_argument("--bars", type=int, default=1500, help="synthetic bar count")
    ap.add_argument("--entry", type=float, default=0.5, help="entry retracement ratio")
    ap.add_argument("--sl", type=float, default=0.618, help="stop retracement ratio")
    args = ap.parse_args()

    cfg = StrategyConfig(entry_ratio=args.entry, sl_ratio=args.sl)

    if args.fyers:
        from fibleg.data import fyers_feed
        print(f"Mode: FYERS {'DUAL' if args.dual else '1H'}  ({args.days}d history)")
        client = fyers_feed.get_client()
        if args.dual:
            series = {s: fyers_feed.fyers_dual(client, s, args.days, args.days)
                      for s in args.fyers}
            engines = driver.run_dual_universe(series, cfg)
        else:
            engines = engine.run_universe(
                {s: fyers_feed.fyers_series(client, s, "60m", args.days) for s in args.fyers}, cfg)
    elif args.dhan:
        from fibleg.data import dhan_feed
        print(f"Mode: DHAN {'DUAL' if args.dual else '1H'}  ({args.days}d history)")
        client = dhan_feed.get_client()
        if args.dual:
            series = {s: dhan_feed.dhan_dual(client, s, args.days, args.days)
                      for s in args.dhan}
            engines = driver.run_dual_universe(series, cfg)
        else:
            engines = engine.run_universe(
                {s: dhan_feed.dhan_series(client, s, "60m", args.days) for s in args.dhan}, cfg)
    elif args.yf and args.dual:
        print("Mode: DUAL timeframe (1H setup / 15m trigger)")
        series = {sym: feeds.yfinance_dual(sym) for sym in args.yf}
        engines = driver.run_dual_universe(series, cfg)
    elif args.yf:
        engines = engine.run_universe(
            {sym: feeds.yfinance_series(sym) for sym in args.yf}, cfg)
    else:
        engines = engine.run_universe({
            "SYNTH-A": feeds.synthetic_series(args.bars, seed=7),
            "SYNTH-B": feeds.synthetic_series(args.bars, seed=42),
            "SYNTH-C": feeds.synthetic_series(args.bars, seed=99),
        }, cfg)

    print(f"\nStrategy: entry={cfg.entry_ratio:g} sl={cfg.sl_ratio:g} "
          f"thresh={cfg.leg_reversal_thresh:g} atr_mult={cfg.atr_mult:g}\n")
    for sym, eng in engines.items():
        sigs: list[Signal] = eng.signals
        print(f"[{sym}] pivots={len(eng.pivots)} signals={len(sigs)} trades={len(eng.trades)}")
        for s in sigs[:5]:
            print(f"    {s.ts:%Y-%m-%d %H:%M} {s.side.value:<5} "
                  f"entry={s.entry:.2f} sl={s.sl:.2f} "
                  f"targets={[round(t, 2) for t in s.targets]}  ({s.note})")
        if len(sigs) > 5:
            print(f"    ... +{len(sigs) - 5} more signals")

    rep = report.summarize(engine.all_trades(engines))
    print("\n" + rep.render())


if __name__ == "__main__":
    main()
