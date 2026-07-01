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
_CSV_FILE = ""          # set from --csv-file when --source csv


def _conf_levels(eng, leg) -> dict:
    """A+ confluence flag + the entry/SL it uses (0.5-0.618 zone, 0.786 stop) + the
    S/R ZONE (mountain +/- zone_frac*leg) the chart draws. Empty when not A+."""
    cs = eng.confluence_setup_leg(leg)
    if cs is None:
        return {"conf": False}
    entry, sl = cs
    out = {"conf": True, "conf_entry": round(entry, 2), "conf_sl": round(sl, 2)}
    z = eng.confluence_zone_leg(leg)
    if z is not None:
        out["conf_mtn"] = round(z[0], 2)
        out["conf_zone_lo"] = round(z[1], 2)
        out["conf_zone_hi"] = round(z[2], 2)
    return out


def _watch_item(sym: str, eng, cfg) -> dict | None:
    s = eng.active
    if not s or s.state not in LIVE:
        return None
    # show the strategy's stable levels from the leg (not the live execution
    # values, which mutate on fill / SL-to-breakeven and look confusing)
    return {
        "symbol": sym,
        "side": s.side.value,
        "state": s.state.value,
        "entry": round(s.leg.retracement(cfg.entry_ratio), 2),
        "sl": round(s.leg.retracement(cfg.sl_ratio), 2),
        "targets": [round(t.price, 2) for t in s.targets],
        "leg": {"start": round(s.leg.start_price, 2), "end": round(s.leg.end_price, 2),
                "start_ts": s.leg.start_ts.isoformat() if s.leg.start_ts else "",
                "end_ts": s.leg.end_ts.isoformat() if s.leg.end_ts else ""},
        "htf": eng.htf_confirms(s.leg),       # 4H double-check: is the impulse also a 4H swing?
        "mw": eng.mw_confirmed(s.leg),        # M/W structure confirmed at top/bottom
        "pin": eng.pin_bar_confirmed(s.leg),  # solid pin bar at the origin candle
        "ew": eng.ew_confirmed(s.leg),        # Elliott 5-wave structure confirmed
        **_conf_levels(eng, s.leg),           # A+ flag + dynamic entry/SL at the mountain
    }


def _chart_bars(bars) -> list[dict]:
    """Last N bars as Lightweight-Charts rows (unique, ascending time)."""
    by_time: dict[int, dict] = {}
    for b in bars[-CHART_BARS:]:
        t = int(b.ts.timestamp())
        by_time[t] = {"time": t, "open": round(b.open, 2), "high": round(b.high, 2),
                      "low": round(b.low, 2), "close": round(b.close, 2)}
    return [by_time[k] for k in sorted(by_time)]


def _leg_dict(sym: str, side: Side, start, end, eng, cfg) -> dict:
    """Fib levels for an arbitrary leg (used by the batch 'all legs' view)."""
    leg = FibLeg(side, start.index, end.index, start.price, end.price, start.ts, end.ts)
    return {
        "symbol": sym,
        "side": side.value,
        "entry": round(leg.retracement(cfg.entry_ratio), 2),
        "sl": round(leg.retracement(cfg.sl_ratio), 2),
        "targets": [round(leg.extension(x), 2) for x in cfg.targets],
        "leg": {"start": round(start.price, 2), "end": round(end.price, 2),
                "start_ts": start.ts.isoformat() if start.ts else "",
                "end_ts": end.ts.isoformat() if end.ts else ""},
        "htf": eng.htf_confirms(leg),
        "mw": eng.mw_confirmed(leg),        # M (double-top) / W (double-bottom) confirmed
        "pin": eng.pin_bar_confirmed(leg),  # solid pin bar at the origin candle
        "ew": eng.ew_confirmed(leg),        # Elliott 5-wave structure confirmed
        **_conf_levels(eng, leg),           # A+ flag + dynamic entry/SL at the mountain
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
DEFAULT_TF = "45"                      # 45m — where the A+ edge lives (+10.4R/265/49% on 501)
METHODS = ("adaptive", "book", "book382")   # adaptive / book 0.236 / book 0.382 (looser lock)
DEFAULT_METHOD = "book382"             # book382 tops the A+ backtest (deeper lock, bigger legs)

# execution profiles A/B'd in Settings — entry level x exit style x trigger TF.
#   entry: 0.5 | 0.618 (the book's golden pocket, called the most important level)
#   exit : full  = square the WHOLE position at the leg top (1.0)
#          partial = scale out 1/3 at 1.0 / 1.272 / 1.618, SL->breakeven after T1
#   trig : the interval a CLOSE beyond 0.786 (SL) / a target must occur on — 5m or
#          15m, independent of the leg-detection timeframe.
#   sl   : the stop retracement — 0.786 (deeper) or 0.618 (tighter). A CLOSE beyond
#          it on the trigger TF triggers the stop.
ENTRIES = (0.382, 0.5, 0.618)          # 0.382 = shallower/earlier entry
EXITS = ("full", "partial")
TRIGGERS = (5, 15)                     # trigger-TF minutes
SLS = (0.618, 0.786)                   # stop retracement level
# key = entry|exit|trigger|sl
EXECS = tuple({"key": f"{e}|{x}|{t}|{s}", "entry": e, "exit": x, "trig": t, "sl": s}
              for e in ENTRIES for x in EXITS for t in TRIGGERS for s in SLS)
DEFAULT_EXEC = "0.5|full|5|0.786"      # best on the sample so far


def _cfg_for(ex: dict) -> StrategyConfig:
    c = StrategyConfig()
    c.entry_ratio = ex["entry"]
    c.sl_ratio = ex["sl"]
    # T1 sits a touch BELOW the leg top (0.95, not 1.0) — bank the win just before
    # the prior-high resistance where price tends to stall (tested: higher win rate).
    if ex["exit"] == "full":
        c.targets = (0.95,)                # square off the ENTIRE position just before the top
        c.target_fractions = (1.0,)
        c.move_sl_to_be_after_tp1 = False
    else:
        c.targets = (0.95, 1.272, 1.618)
        c.target_fractions = (1 / 3, 1 / 3, 1 / 3)
        c.move_sl_to_be_after_tp1 = True
    return c


CONF_TRIGGERS = (5, 15)
CONF_EXITS = ("full", "partial", "lockb")   # square@T1 | let-run+breakeven | let-run+lock-at-B
DEFAULT_CONF = "lockb|5"                # validated best (11yr walk-forward): let-run + lock-at-B


def _conf_cfg(exit_: str) -> StrategyConfig:
    """The A+ 'confluence mode' config — entry = 0.5-0.618 zone, SL = 0.786 (hedge),
    the nested-fib refines the fill. entry/SL are automatic here (not toggles).
    Two validated loss-cutters are baked in (501-stock backtest, 45m book382):
      - zone_respect (5%): the mountain is an S/R ZONE; enter only when price trades
        into it and CLOSES back out (held); skip if a close goes THROUGH it (failed).
      - require_mw: only setups whose ORIGIN carries the M/W reversal (W->long, M->short).
    Together they took 45m -7.4R -> +10.4R, 42% -> 49% win, on fewer (265) trades."""
    c = StrategyConfig()
    c.entry_ratio, c.sl_ratio = 0.5, 0.786
    c.zone_entry = True       # entry is ALWAYS the 0.5-0.618 zone + 0.786 stop; anchor to the
    c.nested_entry = True     # mountain when one sits in it (A+), else the plain fib zone.
    c.zone_respect = True     # NEVER skip a no-mountain setup -> the app flags/labels it.
    c.zone_frac = 0.05
    c.zone_pin_respect = True  # ALSO respect via a big rejection PIN on the detection-TF candle
                               # (wick spears the zone, closes back out) confirmed by a bigger candle.
    # The mountain (conf), M/W and pin are per-setup FLAGS filtered client-side, so this one
    # zone-entry backtest serves every setup-filter mode (All / A+ / M/W / Pin). No hard gates.
    if exit_ == "full":
        c.targets, c.target_fractions, c.move_sl_to_be_after_tp1 = (0.95,), (1.0,), False
    else:
        # "let it run": entry-dependent 1/d targets (harmonic BC projection) + ratcheting trail.
        # 'partial' pulls the stop to breakeven after T1; 'lockb' locks it AT B (the T1 price) so
        # a failed continuation keeps the B profit (validated best: +190R -> +214R walk-forward).
        c.targets, c.target_fractions = (0.95, 1.272, 1.618), (1 / 3, 1 / 3, 1 / 3)
        c.entry_dependent_targets = True
        c.trail_sl_after_targets = True
        if exit_ == "lockb":
            c.sl_lock_at_t1 = True
        else:
            c.move_sl_to_be_after_tp1 = True
    return c


def _fetch(source: str, symbols: list[str], days: int):
    """Returns (base, dual, base_min) — the finest bars per symbol + their interval.
    yfinance/synthetic use a 5-MINUTE base so every detection TF (45/60/120/180/240)
    and both trigger TFs (5m, 15m) resample cleanly from one stream. Dhan only gives
    15m, so there the 5m trigger collapses to 15m."""
    if source == "dhan":
        from fibleg.data import dhan_feed
        client = dhan_feed.get_client()
        # 5-minute base straight from Dhan, `days` back (6-12 months) — same shape
        # as the yfinance path, just far more history for a trustworthy backtest.
        base5 = {s: dhan_feed.dhan_series(client, s, "5m", days) for s in symbols}
        return base5, True, 5
    if source == "yf":
        base5 = {s: feeds.yfinance_series(s, period="60d", interval="5m") for s in symbols}
        return base5, True, 5
    if source == "csv":
        base1 = feeds.csv_multi(_CSV_FILE, symbols)      # 1-minute OHLC from disk
        return base1, True, 1
    if source == "fyers":
        from fibleg.data import fyers_feed
        client = fyers_feed.get_client()                 # uses cached ~/.fibleg token
        base5 = {s: fyers_feed.fyers_series(client, s, "5m", days) for s in symbols}
        return base5, True, 5
    sb = {s: feeds.synthetic_series(3000, seed=i + 1, step=timedelta(minutes=5))
          for i, s in enumerate(symbols)}
    return sb, False, 5


def _run_tf(base, dual: bool, tf_min: int, cfg, method: str,
            base_min: int, trig_min: int):
    """Leg detected on tf_min (resampled from the base), SL/exit checked on the
    trig_min close stream (5m or 15m) — both derived from the same base_min bars.
    Returns (engines, setup)."""
    setup = {s: feeds.resample(b, tf_min // base_min) for s, b in base.items()}
    if dual:
        trig = {s: feeds.resample(b, max(1, trig_min // base_min)) for s, b in base.items()}
        engines = driver.run_dual_universe(
            {s: (setup[s], trig[s]) for s in setup}, cfg, method, base_min)
    else:
        engines = engine.run_universe(setup, cfg, method)
    return engines, setup


def _method_lists(engines, cfg) -> dict:
    """watchlist / all_legs / history / stats for one (TF, method, exec). Charts and
    the ZigZag pivots are method/exec-independent, so they live up at the TF level."""
    watchlist, history, all_legs = [], [], []
    for sym, eng in engines.items():
        w = _watch_item(sym, eng, cfg)
        if w:
            watchlist.append(w)
        cl = eng.current_leg()
        if cl:
            all_legs.append(_leg_dict(sym, cl[2], cl[0], cl[1], eng, cfg))
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
                item["entry"] = round(t.leg.retracement(cfg.entry_ratio), 2)
                item["sl"] = round(t.leg.retracement(cfg.sl_ratio), 2)
                item["targets"] = [round(t.leg.extension(x), 2) for x in cfg.targets]
                item["mw"] = eng.mw_confirmed(t.leg)     # was the trade's leg M/W-confirmed?
                item["pin"] = eng.pin_bar_confirmed(t.leg)  # or a solid origin pin bar?
                item["htf"] = eng.htf_confirms(t.leg)
                item["ew"] = eng.ew_confirmed(t.leg)
                item.update(_conf_levels(eng, t.leg))     # conf flag + entry/SL + S/R zone
            history.append(item)
    history.sort(key=lambda h: h["ts"], reverse=True)
    history = history[:50]
    wins = [h for h in history if h["points"] > 0]
    stats = {"trades": len(history), "wins": len(wins),
             "win_rate": round(len(wins) / len(history), 3) if history else 0,
             "net_points": round(sum(h["points"] for h in history), 2)}
    return {"watchlist": sorted(watchlist, key=lambda w: w["symbol"]),
            "all_legs": sorted(all_legs, key=lambda w: w["symbol"]),
            "history": history, "stats": stats}


def _charts(engines, setup: dict) -> tuple[dict, dict]:
    """(charts, pivots) for one TF — candles + the ZigZag overlay. Both are the
    same across methods (the swing detector runs regardless of the leg method),
    so they're computed once per TF from either engine set."""
    charts, pivots = {}, {}
    for sym, eng in engines.items():
        if sym in setup and setup[sym]:
            cb = _chart_bars(setup[sym])
            charts[sym] = cb
            if cb:
                pivots[sym] = _zigzag(eng, cb[0]["time"])
    return charts, pivots


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
    ap.add_argument("--source", choices=["synthetic", "yf", "dhan", "csv", "fyers"], default="synthetic")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--csv-file", default="")     # multi-ticker 1m OHLC CSV (--source csv)
    ap.add_argument("--out", default="docs/signals.json")
    args = ap.parse_args()

    global _CSV_FILE
    _CSV_FILE = args.csv_file
    base, dual, base_min = _fetch(args.source, args.symbols, args.days)
    exec_cfgs = {ex["key"]: _cfg_for(ex) for ex in EXECS}

    by_tf = {}
    for tf in DETECT_TFS:                        # every timeframe (minutes) ...
        by_method, charts, pivots = {}, None, None
        for method in METHODS:                   # ... under every leg method (A/B) ...
            by_exec = {}
            for ex in EXECS:                     # ... under every entry/exit/trigger profile
                cfg = exec_cfgs[ex["key"]]
                engines, setup = _run_tf(base, dual, tf, cfg, method, base_min, ex["trig"])
                by_exec[ex["key"]] = _method_lists(engines, cfg)
                if charts is None:               # candles+zigzag don't vary — compute once
                    charts, pivots = _charts(engines, setup)
            by_conf = {}                         # A+ confluence mode (confluence + nested entry)
            for exit_ in CONF_EXITS:
                for trig in CONF_TRIGGERS:
                    cfg = _conf_cfg(exit_)
                    engines, _ = _run_tf(base, dual, tf, cfg, method, base_min, trig)
                    by_conf[f"{exit_}|{trig}"] = _method_lists(engines, cfg)
            by_method[method] = {"byExec": by_exec, "byConf": by_conf}
        by_tf[str(tf)] = {"charts": charts, "pivots": pivots, "byMethod": by_method}

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "symbols": args.symbols,
        "default_tf": DEFAULT_TF,
        "detect_tfs": [str(f) for f in DETECT_TFS],
        "methods": list(METHODS),
        "default_method": DEFAULT_METHOD,
        "execs": [ex["key"] for ex in EXECS],
        "default_exec": DEFAULT_EXEC,
        "conf_execs": [f"{x}|{t}" for x in CONF_EXITS for t in CONF_TRIGGERS],
        "default_conf": DEFAULT_CONF,
        "zone_entry": True,   # entry/stop are always the zone (0.5-0.618 / 0.786); app grays the toggles
        "byTF": by_tf,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    d = by_tf[DEFAULT_TF]["byMethod"][DEFAULT_METHOD]["byExec"][DEFAULT_EXEC]
    print(f"wrote {out}: TFs={list(by_tf)} methods={list(METHODS)} execs={[e['key'] for e in EXECS]} | "
          f"default {DEFAULT_TF}m/{DEFAULT_METHOD}/{DEFAULT_EXEC} {len(d['watchlist'])} setups, "
          f"{len(d['all_legs'])} legs, {len(by_tf[DEFAULT_TF]['charts'])} charts")
    maybe_telegram(d["watchlist"][:5])


if __name__ == "__main__":
    main()
