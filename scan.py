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

CHART_BARS = 420   # 1H bars per symbol (≈ full 60d lookback so history trades are in view)


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


DETECT_TFS = (2, 3, 4)   # leg-detection timeframes (× 1H): 2H, 3H, 4H


def _fetch(source: str, symbols: list[str], days: int):
    """Returns (raw, chart_bars, dual). raw = engine-input bars per symbol;
    chart_bars = 1H bars for display."""
    if source in ("dhan", "yf"):
        if source == "dhan":
            from fibleg.data import dhan_feed
            client = dhan_feed.get_client()
            raw = {s: dhan_feed.dhan_dual(client, s, days, days) for s in symbols}
        else:
            raw = {s: feeds.yfinance_dual(s) for s in symbols}
        return raw, {s: raw[s][0] for s in symbols}, True
    sbars = {s: feeds.synthetic_series(1500, seed=i + 1) for i, s in enumerate(symbols)}
    return sbars, sbars, False


def _engines(raw, dual: bool, factor: int, cfg):
    """Run the engine for every symbol with the leg detected on factor×1H."""
    if dual:
        series = {s: (feeds.resample(h1, factor), m15) for s, (h1, m15) in raw.items()}
        return driver.run_dual_universe(series, cfg)
    eng_bars = {s: feeds.resample(b, factor) for s, b in raw.items()}
    return engine.run_universe(eng_bars, cfg)


def _lists(engines, charts: dict) -> dict:
    """watchlist / all_legs / history / stats / pivots for one detection TF."""
    watchlist, history, pivots, all_legs = [], [], {}, []
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
            history.append(item)
        if sym in charts and charts[sym]:
            pivots[sym] = _zigzag(eng, charts[sym][0]["time"])
    history.sort(key=lambda h: h["ts"], reverse=True)
    history = history[:50]
    wins = [h for h in history if h["points"] > 0]
    stats = {"trades": len(history), "wins": len(wins),
             "win_rate": round(len(wins) / len(history), 3) if history else 0,
             "net_points": round(sum(h["points"] for h in history), 2)}
    return {"watchlist": sorted(watchlist, key=lambda w: w["symbol"]),
            "all_legs": sorted(all_legs, key=lambda w: w["symbol"]),
            "history": history, "stats": stats, "pivots": pivots}


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
    raw, chart_bars, dual = _fetch(args.source, args.symbols, args.days)
    charts = {s: _chart_bars(b) for s, b in chart_bars.items() if b}

    by_tf = {}
    for f in DETECT_TFS:                        # compute legs for EVERY timeframe
        by_tf[str(f)] = _lists(_engines(raw, dual, f, cfg), charts)

    default = str(cfg.setup_factor) if str(cfg.setup_factor) in by_tf else str(DETECT_TFS[-1])
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "symbols": args.symbols,
        "default_tf": default,                  # which TF the app shows by default
        "detect_tfs": [str(f) for f in DETECT_TFS],
        "charts": charts,
        "byTF": by_tf,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    d = by_tf[default]
    print(f"wrote {out}: TFs={list(by_tf)} | default {default}H "
          f"{len(d['watchlist'])} setups, {len(d['all_legs'])} legs, {len(charts)} charts")
    maybe_telegram(d["watchlist"][:5])


if __name__ == "__main__":
    main()
