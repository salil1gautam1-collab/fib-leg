#!/usr/bin/env python3
"""Stateless scan run — the job GitHub Actions fires on a schedule.

Pulls recent history, replays the fib-leg engine per symbol, and writes a
dashboard feed to docs/signals.json:
  - watchlist : symbols with a LIVE setup (leg locked, pulling back / armed)
  - recent    : recently triggered signals (the alert feed)
  - charts    : recent OHLC per symbol so the app can draw the chart + fib levels

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
from fibleg.models import FibLeg, SetupState, Side

LIVE = (SetupState.WAITING_PULLBACK, SetupState.ARMED,
        SetupState.SIGNALED, SetupState.IN_TRADE)

DEFAULT_SYMBOLS = ["RELIANCE.NS", "INFY.NS", "TCS.NS", "HDFCBANK.NS",
                   "ICICIBANK.NS", "SBIN.NS", "^NSEI", "^NSEBANK"]

CHART_BARS = 200   # bars sent to the app per symbol for the chart


_CFG = StrategyConfig()


def _watch_item(sym: str, eng) -> dict | None:
    s = eng.active
    if not s or s.state not in LIVE:
        return None
    # show the strategy's stable levels from the leg (not the live execution
    # values, which mutate on fill / SL-to-breakeven and look confusing)
    return {
        "symbol": sym,
        "side": s.side.value,
        "state": s.state.value,
        "entry": round(s.leg.retracement(_CFG.entry_ratio), 2),
        "sl": round(s.leg.retracement(_CFG.sl_ratio), 2),
        "targets": [round(t.price, 2) for t in s.targets],
        "leg": {"start": round(s.leg.start_price, 2), "end": round(s.leg.end_price, 2)},
        "htf": eng.htf_confirms(s.leg),       # 4H double-check: is the impulse also a 4H swing?
    }


def _chart_bars(bars) -> list[dict]:
    """Last N bars as Lightweight-Charts rows (unique, ascending time)."""
    by_time: dict[int, dict] = {}
    for b in bars[-CHART_BARS:]:
        t = int(b.ts.timestamp())
        by_time[t] = {"time": t, "open": round(b.open, 2), "high": round(b.high, 2),
                      "low": round(b.low, 2), "close": round(b.close, 2)}
    return [by_time[k] for k in sorted(by_time)]


def _leg_dict(sym: str, side: Side, start_price: float, end_price: float, eng) -> dict:
    """Fib levels for an arbitrary leg (used by the batch 'all legs' view)."""
    leg = FibLeg(side, 0, 0, start_price, end_price)
    return {
        "symbol": sym,
        "side": side.value,
        "entry": round(leg.retracement(_CFG.entry_ratio), 2),
        "sl": round(leg.retracement(_CFG.sl_ratio), 2),
        "targets": [round(leg.extension(x), 2) for x in _CFG.targets],
        "leg": {"start": round(start_price, 2), "end": round(end_price, 2)},
        "htf": eng.htf_confirms(leg),
    }


def _zigzag(eng, first_ts: int) -> list[dict]:
    """Confirmed ZigZag pivots within the chart window, as line-series points."""
    pts = {}
    for p in eng.pivots:
        t = int(p.ts.timestamp())
        if t >= first_ts:
            pts[t] = {"time": t, "value": round(p.price, 2)}
    return [pts[k] for k in sorted(pts)]


def _build(source: str, symbols: list[str], days: int):
    """Returns (engines, setup_tf_bars_per_symbol)."""
    cfg = StrategyConfig()
    if source == "dhan":
        from fibleg.data import dhan_feed
        client = dhan_feed.get_client()
        series = {s: dhan_feed.dhan_dual(client, s, days, days) for s in symbols}
        return driver.run_dual_universe(series, cfg), {s: series[s][0] for s in symbols}
    if source == "yf":
        series = {s: feeds.yfinance_dual(s) for s in symbols}
        return driver.run_dual_universe(series, cfg), {s: series[s][0] for s in symbols}
    sbars = {s: feeds.synthetic_series(1500, seed=i + 1) for i, s in enumerate(symbols)}
    return engine.run_universe(sbars, cfg), sbars


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

    engines, tf_bars = _build(args.source, args.symbols, args.days)

    watchlist, history, charts, pivots, all_legs = [], [], {}, {}, []
    for sym, eng in engines.items():
        w = _watch_item(sym, eng)
        if w:
            watchlist.append(w)
        cl = eng.current_leg()                 # current leg for EVERY symbol (batch view)
        if cl:
            all_legs.append(_leg_dict(sym, cl[2], cl[0], cl[1], eng))
        for t in eng.trades[-10:]:
            # result follows the net P&L: a trade that banked partial targets then
            # stopped at breakeven is still a winner ("target"), not a "stop".
            result = "target" if t.realized_points > 0 else ("flat" if t.realized_points == 0 else "stop")
            item = {
                "symbol": sym, "side": t.side.value,
                "entry": round(t.entry, 2), "sl": round(t.sl, 2),
                "result": result,
                "exit": t.exit_reason,                   # "targets" | "sl" (raw)
                "points": t.realized_points,            # net price points (signed)
                "r": t.realized_r,                       # R-multiple
                "ts": t.exit_ts.isoformat() if t.exit_ts else "",
            }
            if t.leg is not None:                        # full fib for drawing on the chart
                item["leg"] = {"start": round(t.leg.start_price, 2),
                               "end": round(t.leg.end_price, 2)}
                item["entry"] = round(t.leg.retracement(_CFG.entry_ratio), 2)
                item["sl"] = round(t.leg.retracement(_CFG.sl_ratio), 2)
                item["targets"] = [round(t.leg.extension(x), 2) for x in _CFG.targets]
            history.append(item)
        if sym in tf_bars and tf_bars[sym]:
            cb = _chart_bars(tf_bars[sym])
            charts[sym] = cb
            if cb:
                pivots[sym] = _zigzag(eng, cb[0]["time"])
    history.sort(key=lambda h: h["ts"], reverse=True)
    history = history[:50]

    wins = [h for h in history if h["points"] > 0]
    stats = {
        "trades": len(history),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(history), 3) if history else 0,
        "net_points": round(sum(h["points"] for h in history), 2),
    }

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "symbols": args.symbols,
        "watchlist": sorted(watchlist, key=lambda w: w["symbol"]),
        "all_legs": sorted(all_legs, key=lambda w: w["symbol"]),
        "history": history,
        "stats": stats,
        "charts": charts,
        "pivots": pivots,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out}: {len(watchlist)} live setups, {len(history)} history, "
          f"{len(charts)} charts | net {stats['net_points']} pts, "
          f"{stats['win_rate']:.0%} win")

    maybe_telegram(watchlist[:5])


if __name__ == "__main__":
    main()
