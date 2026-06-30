#!/usr/bin/env python3
"""Stateless scan run — the job GitHub Actions fires on a schedule.

Pulls recent history, replays the fib-leg engine per symbol, and writes a
dashboard feed to docs/signals.json:
  - watchlist : symbols with a LIVE setup (leg locked, pulling back / armed)
  - recent    : recently triggered signals (the alert feed)

Sources:
    python scan.py                         # synthetic (offline demo)
    python scan.py --source yf             # yfinance (US + NSE .NS, ~60d)
    python scan.py --source dhan           # Dhan (needs ~/.fibleg/dhan.json)

Telegram push is optional (set TELEGRAM_TOKEN + TELEGRAM_CHAT env vars).
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from fibleg.backtest import driver, engine
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import SetupState

LIVE = (SetupState.WAITING_PULLBACK, SetupState.ARMED,
        SetupState.SIGNALED, SetupState.IN_TRADE)

DEFAULT_SYMBOLS = ["RELIANCE.NS", "INFY.NS", "TCS.NS", "HDFCBANK.NS",
                   "ICICIBANK.NS", "SBIN.NS", "^NSEI", "^NSEBANK"]


def _watch_item(sym: str, eng) -> dict | None:
    s = eng.active
    if not s or s.state not in LIVE:
        return None
    return {
        "symbol": sym,
        "side": s.side.value,
        "state": s.state.value,
        "entry": round(s.entry_price, 2),
        "sl": round(s.sl_price, 2),
        "targets": [round(t.price, 2) for t in s.targets],
        "leg": {"start": round(s.leg.start_price, 2), "end": round(s.leg.end_price, 2)},
    }


def _build_engines(source: str, symbols: list[str], days: int):
    if source == "dhan":
        from fibleg.data import dhan_feed
        client = dhan_feed.get_client()
        series = {s: dhan_feed.dhan_dual(client, s, days, days) for s in symbols}
        return driver.run_dual_universe(series, StrategyConfig())
    if source == "yf":
        series = {s: feeds.yfinance_dual(s) for s in symbols}
        return driver.run_dual_universe(series, StrategyConfig())
    # synthetic offline demo
    return engine.run_universe(
        {s: feeds.synthetic_series(1500, seed=i + 1) for i, s in enumerate(symbols)},
        StrategyConfig())


def maybe_telegram(new_signals: list[dict]) -> None:
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT")
    if not (token and chat and new_signals):
        return
    import urllib.parse
    import urllib.request
    for sig in new_signals:
        text = (f"{'🟢 LONG' if sig['side'] == 'long' else '🔴 SHORT'} {sig['symbol']}\n"
                f"entry {sig['entry']}  sl {sig['sl']}\n"
                f"targets {sig['targets']}\n{sig.get('note', '')}")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
        except Exception as e:  # noqa: BLE001
            print(f"telegram failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["synthetic", "yf", "dhan"], default="synthetic")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--out", default="docs/signals.json")
    args = ap.parse_args()

    engines = _build_engines(args.source, args.symbols, args.days)

    watchlist, recent = [], []
    for sym, eng in engines.items():
        w = _watch_item(sym, eng)
        if w:
            watchlist.append(w)
        for s in eng.signals[-5:]:
            recent.append({
                "symbol": sym, "side": s.side.value,
                "entry": round(s.entry, 2), "sl": round(s.sl, 2),
                "targets": [round(t, 2) for t in s.targets],
                "ts": s.ts.isoformat(), "note": s.note,
            })
    recent.sort(key=lambda r: r["ts"], reverse=True)
    recent = recent[:40]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "symbols": args.symbols,
        "watchlist": sorted(watchlist, key=lambda w: w["symbol"]),
        "recent": recent,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out}: {len(watchlist)} live setups, {len(recent)} recent signals")

    # alert on the freshest signals (the cron dedupes via git: only new commits push)
    maybe_telegram(recent[:5])


if __name__ == "__main__":
    main()
