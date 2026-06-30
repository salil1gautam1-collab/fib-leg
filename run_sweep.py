#!/usr/bin/env python3
"""Parameter sweep over real data (dual timeframe).

    python run_sweep.py --yf RELIANCE.NS INFY.NS ^NSEI

Downloads each symbol's 1H+15m once, then grids over entry / sl / leg-reversal
threshold and ranks by total R. Honest caveat: yfinance caps 15m history at
~60d, so this is a SMALL sample — directional only, not a verdict (design §7).
"""
from __future__ import annotations

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

from fibleg.backtest import driver, report
from fibleg.config import StrategyConfig
from fibleg.data import feeds

ENTRIES = (0.382, 0.5, 0.618)
STOPS = (0.618, 0.786)
THRESHES = (0.382, 0.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yf", nargs="+", required=True, help="Yahoo tickers")
    args = ap.parse_args()

    print(f"Downloading 1H+15m for {len(args.yf)} symbols (60d)…")
    series = {sym: feeds.yfinance_dual(sym) for sym in args.yf}

    rows = []
    for thr in THRESHES:
        for entry in ENTRIES:
            for sl in STOPS:
                if sl <= entry:               # stop must sit beyond the entry level
                    continue
                cfg = StrategyConfig(entry_ratio=entry, sl_ratio=sl, leg_reversal_thresh=thr)
                engines = driver.run_dual_universe(series, cfg)
                trades = [t for e in engines.values() for t in e.trades]
                rep = report.summarize(trades)
                rows.append((thr, entry, sl, rep))

    rows.sort(key=lambda r: r[3].total_r, reverse=True)
    print("\n thr   entry  sl     trades  win%   totalR   exp/R    PF")
    print(" " + "-" * 60)
    for thr, entry, sl, rep in rows:
        print(f" {thr:<5.3f} {entry:<5.3f} {sl:<5.3f} {rep.trades:>6} "
              f"{rep.win_rate:>5.0%} {rep.total_r:>+8.2f} {rep.expectancy_r:>+7.3f} "
              f"{rep.profit_factor:>6.2f}")
    print("\nNote: ~60d / few trades per combo — directional only, not significant.")


if __name__ == "__main__":
    main()
