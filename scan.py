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
from datetime import datetime, timedelta
from pathlib import Path

from fibleg.backtest import driver, engine
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import FibLeg, SetupState, Side

LIVE = (SetupState.WAITING_PULLBACK, SetupState.ARMED,
        SetupState.SIGNALED, SetupState.IN_TRADE)

DEFAULT_SYMBOLS = ["RELIANCE.NS", "INFY.NS", "TCS.NS", "HDFCBANK.NS",
                   "ICICIBANK.NS", "SBIN.NS", "^NSEI", "^NSEBANK"]

CHART_BARS = 350   # candles per symbol per TF (each TF emits its own, aligned to pivots)


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
        "leg": {"start": round(s.leg.start_price, 2), "end": round(s.leg.end_price, 2),
                "start_ts": s.leg.start_ts.isoformat() if s.leg.start_ts else "",
                "end_ts": s.leg.end_ts.isoformat() if s.leg.end_ts else ""},
        "htf": eng.htf_confirms(s.leg),       # 4H double-check: is the impulse also a 4H swing?
        "mw": eng.mw_confirmed(s.leg),        # M/W structure confirmed at top/bottom
    }


def _chart_bars(bars) -> list[dict]:
    """Last N bars as Lightweight-Charts rows (unique, ascending time)."""
    by_time: dict[int, dict] = {}
    for b in bars[-CHART_BARS:]:
        t = int(b.ts.timestamp())
        by_time[t] = {"time": t, "open": round(b.open, 2), "high": round(b.high, 2),
                      "low": round(b.low, 2), "close": round(b.close, 2)}
    return [by_time[k] for k in sorted(by_time)]


def _leg_dict(sym: str, side: Side, start, end, eng) -> dict:
    """Fib levels for an arbitrary leg (used by the batch 'all legs' view)."""
    leg = FibLeg(side, start.index, end.index, start.price, end.price, start.ts, end.ts)
    return {
        "symbol": sym,
        "side": side.value,
        "entry": round(leg.retracement(_CFG.entry_ratio), 2),
        "sl": round(leg.retracement(_CFG.sl_ratio), 2),
        "targets": [round(leg.extension(x), 2) for x in _CFG.targets],
        "leg": {"start": round(start.price, 2), "end": round(end.price, 2),
                "start_ts": start.ts.isoformat() if start.ts else "",
                "end_ts": end.ts.isoformat() if end.ts else ""},
        "htf": eng.htf_confirms(leg),
        "mw": eng.mw_confirmed(leg),        # M (double-top) / W (double-bottom) confirmed
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
    """Deprecated single-TF builder (kept for reference)."""
    raise NotImplementedError


DETECT_TFS = (45, 60, 120, 180, 240)   # leg-detection timeframes in MINUTES


def _fetch(source: str, symbols: list[str], days: int):
    """Returns (base15, dual) — 15m base bars per symbol. Every timeframe is a
    multiple of 15m, so all of 45m/1H/2H/3H/4H resample cleanly from this."""
    if source in ("dhan", "yf"):
        if source == "dhan":
            from fibleg.data import dhan_feed
            client = dhan_feed.get_client()
            raw = {s: dhan_feed.dhan_dual(client, s, days, days) for s in symbols}
        else:
            raw = {s: feeds.yfinance_dual(s) for s in symbols}
        return {s: raw[s][1] for s in symbols}, True          # the 15m bars
    sb = {s: feeds.synthetic_series(3000, seed=i + 1, step=timedelta(minutes=15))
          for i, s in enumerate(symbols)}
    return sb, False


def _run_tf(base15, dual: bool, tf_min: int, cfg):
    """Run every symbol with the leg detected on tf_min (resampled from 15m);
    15m is the entry-trigger TF. Returns (engines, setup_bars_at_this_tf)."""
    factor = tf_min // 15
    setup = {s: feeds.resample(b, factor) for s, b in base15.items()}
    if dual:
        engines = driver.run_dual_universe({s: (setup[s], base15[s]) for s in setup}, cfg)
    else:
        engines = engine.run_universe(setup, cfg)
    return engines, setup


def _lists(engines, setup: dict) -> dict:
    """watchlist / all_legs / history / stats / pivots / charts for one TF.
    Charts are this TF's own candles so the zigzag aligns exactly."""
    watchlist, history, pivots, all_legs, charts = [], [], {}, [], {}
    for sym, eng in engines.items():
        w = _watch_item(sym, eng)
        if w:
            watchlist.append(w)
        cl = eng.current_leg()
        if cl:
            all_legs.append(_leg_dict(sym, cl[2], cl[0], cl[1], eng))
        for t in eng.trades[-10:]:
            result = "target" if t.realized_points > 0 else ("flat" if t.realized_points == 0 else "stop")
            item = {"symbol": sym, "side": t.side.value,
                    "entry": round(t.entry, 2), "sl": round(t.sl, 2),
                    "result": result, "exit": t.exit_reason,
                    "points": t.realized_points, "r": t.realized_r,
                    "entry_ts": t.entry_ts.isoformat() if t.entry_ts else "",
                    "ts": t.exit_ts.isoformat() if t.exit_ts else ""}
            if t.leg is not None:
                item["leg"] = {"start": round(t.leg.start_price, 2),
                               "end": round(t.leg.end_price, 2),
                               "start_ts": t.leg.start_ts.isoformat() if t.leg.start_ts else "",
                               "end_ts": t.leg.end_ts.isoformat() if t.leg.end_ts else ""}
                item["entry"] = round(t.leg.retracement(_CFG.entry_ratio), 2)
                item["sl"] = round(t.leg.retracement(_CFG.sl_ratio), 2)
                item["targets"] = [round(t.leg.extension(x), 2) for x in _CFG.targets]
                item["mw"] = eng.mw_confirmed(t.leg)     # was the trade's leg M/W-confirmed?
                item["htf"] = eng.htf_confirms(t.leg)
            history.append(item)
        if sym in setup and setup[sym]:
            cb = _chart_bars(setup[sym])
            charts[sym] = cb
            if cb:
                pivots[sym] = _zigzag(eng, cb[0]["time"])
    history.sort(key=lambda h: h["ts"], reverse=True)
    history = history[:50]
    wins = [h for h in history if h["points"] > 0]
    stats = {"trades": len(history), "wins": len(wins),
             "win_rate": round(len(wins) / len(history), 3) if history else 0,
             "net_points": round(sum(h["points"] for h in history), 2)}
    return {"watchlist": sorted(watchlist, key=lambda w: w["symbol"]),
            "all_legs": sorted(all_legs, key=lambda w: w["symbol"]),
            "history": history, "stats": stats, "pivots": pivots, "charts": charts}


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

    cfg = StrategyConfig()
    base15, dual = _fetch(args.source, args.symbols, args.days)

    by_tf = {}
    for tf in DETECT_TFS:                        # compute legs for EVERY timeframe (minutes)
        engines, setup = _run_tf(base15, dual, tf, cfg)
        by_tf[str(tf)] = _lists(engines, setup)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "symbols": args.symbols,
        "default_tf": "240",                     # 4H by default
        "detect_tfs": [str(f) for f in DETECT_TFS],
        "byTF": by_tf,                           # charts live inside each TF now
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    d = by_tf["240"]
    print(f"wrote {out}: TFs(min)={list(by_tf)} | default 4H "
          f"{len(d['watchlist'])} setups, {len(d['all_legs'])} legs, {len(d['charts'])} charts")
    maybe_telegram(d["watchlist"][:5])


if __name__ == "__main__":
    main()
