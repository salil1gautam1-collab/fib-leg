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
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# HARD guard against a hung network call freezing the whole session loop: the Fyers SDK
# data calls (optionchain/quotes) take no timeout, so a slow/rate-limited response can
# block forever. A global socket timeout turns any stuck read into a caught exception
# instead of a stall (the scanner degrades gracefully; the next 15m iteration retries).
socket.setdefaulttimeout(45)

from fibleg.backtest import driver, engine
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import FibLeg, SetupState, Side

LIVE = (SetupState.WAITING_PULLBACK, SetupState.ARMED,
        SetupState.SIGNALED, SetupState.IN_TRADE)

DEFAULT_SYMBOLS = ["RELIANCE.NS", "INFY.NS", "TCS.NS", "HDFCBANK.NS",
                   "ICICIBANK.NS", "SBIN.NS", "^NSEI", "^NSEBANK"]

# the LEVEL PAPER BOOKS (Scalper/Defense) trade the MOST LIQUID F&O stocks — the
# ones with tradeable option spreads — ranked from Fyers by real daily traded value
# (build_universe.py -> docs/book_universe.json, refreshed periodically). Falls back
# to a small static list if the ranking file is missing (owner: 'not fixed at 43').
_STATIC_BOOK_SYMBOLS = [
    "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS", "BHARTIARTL.NS", "BAJFINANCE.NS",
    "MARUTI.NS", "M&M.NS", "TATASTEEL.NS", "HINDALCO.NS", "HCLTECH.NS",
    "WIPRO.NS", "COALINDIA.NS", "ADANIENT.NS", "ADANIPORTS.NS", "NTPC.NS",
    "ONGC.NS", "VEDL.NS", "CIPLA.NS", "GRASIM.NS", "HDFCLIFE.NS",
]


def _load_book_symbols():
    """Top liquid F&O stocks from the Fyers-ranked file; static list if absent."""
    try:
        u = json.loads(Path("docs/book_universe.json").read_text())
        top = [s if s.endswith(".NS") else f"{s}.NS" for s in u.get("top", [])]
        if top:
            return top
    except Exception:  # noqa: BLE001
        pass
    return _STATIC_BOOK_SYMBOLS


BOOK_EXTRA_SYMBOLS = _load_book_symbols()

CHART_BARS = 350   # candles per symbol per TF (each TF emits its own, aligned to pivots)


_CFG = StrategyConfig()
_CSV_FILE = ""          # set from --csv-file when --source csv
_FYERS_FELL_BACK = False  # set True if a --source fyers run had to drop to yfinance


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
DEFAULT_TF = "120"                     # 2H — validated best on the 11-yr/115-stock walk-forward
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
    c.book_reanchor_ratio = 0.618   # book leg-end rule: hold the leg through shallow pullbacks,
                                    # re-anchor only on a 0.618 retrace (11yr: +105R -> +169R)
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
        try:
            from fibleg.data import fyers_feed
            client = fyers_feed.get_client()             # headless daily login via TOTP
            base5 = {s: fyers_feed.fyers_series(client, s, "5m", days) for s in symbols}
            if any(base5.values()):
                return base5, True, 5
            raise RuntimeError("fyers returned no data")
        except Exception as e:  # noqa: BLE001
            # never let a Fyers hiccup blank the feed — fall back to yfinance
            print(f"fyers unavailable ({str(e)[:120]}) — falling back to yfinance")
            global _FYERS_FELL_BACK
            _FYERS_FELL_BACK = True
            base5 = {s: feeds.yfinance_series(s, period="60d", interval="5m") for s in symbols}
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


def _annotate_context(by_tf: dict):
    """Stamp every setup/leg/history item with the market-context verdict (fibleg.context):
    VIX calm? sector with the trade? Nifty not in whipsaw? projected R:R >= 1?
    -> item["ctx"] = {regime, vix_hi, sector_ok, rr, pass}. Returns the current market
    snapshot for the app header, or None if benchmarks were unreachable (fail-safe:
    items then carry ctx=None and the app treats them as ungated)."""
    try:
        from fibleg.context import MarketContext
        mc = MarketContext.load()
        if not mc.mkt and not mc.vix:
            print("context: no benchmark data — skipping annotation")
            return None
    except Exception as e:  # noqa: BLE001
        print("context load failed:", e)
        return None
    now = datetime.now()

    def ann(item: dict) -> None:
        try:
            tg = item.get("targets") or []
            t2 = tg[1] if len(tg) > 1 else (tg[-1] if tg else None)
            raw = item.get("entry_ts") or item.get("ts")
            ts = datetime.fromisoformat(raw) if raw else now
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            item["ctx"] = mc.flags(item["symbol"], ts, item.get("side") == "long",
                                   item["entry"], item["sl"], t2)
        except Exception:  # noqa: BLE001
            item["ctx"] = None

    for tf in by_tf.values():
        for m in tf["byMethod"].values():
            for grp in ("byExec", "byConf"):
                for lists in m.get(grp, {}).values():
                    for key in ("watchlist", "all_legs", "history"):
                        for it in lists.get(key, []):
                            ann(it)
    return {"regime": mc.regime(now), "vix_hi": mc.vix_elevated(now)}


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


def _emit_book_charts(books_base: dict, out_dir, deep: bool = False) -> None:
    """docs/book_charts.json — recent 5m candles + current 2H fib levels for every book
    symbol. Written EVERY scan so the 5m chart is live (small file). When deep=True, ALSO
    writes docs/book_charts_htf.json with the 1H/2H deep-history series — only ~twice/hr,
    since higher TFs gain a candle just every 1-2 hours and the deep history is the bulk
    of the bytes (keeps the frequently-committed file small)."""
    from fibleg.data import feeds as _f
    from fibleg.indicators.atr import AtrStreamer
    from fibleg.models import PivotType
    from fibleg.strategy.book_impulse import BookImpulse
    from fibleg.strategy.pivots import ZigZag
    LV = (0.5, 0.618, 0.786, 0.886)
    out, htf = {}, {}
    for sym, b1 in books_base.items():
        if not b1 or len(b1) < 60:
            continue
        base_min = int((b1[1].ts - b1[0].ts).total_seconds()) // 60 or 5

        def _rows(seq, n):
            return [{"time": int(b.ts.timestamp()), "open": round(b.open, 2),
                     "high": round(b.high, 2), "low": round(b.low, 2),
                     "close": round(b.close, 2)} for b in seq[-n:]]

        bars = _rows(b1, 160)                                        # 5m — ~2 days intraday
        if deep:   # 1H/2H resampled from the full ~60d history — enough to see the leg
            htf[sym] = {"bars60": _rows(_f.resample(b1, max(1, 60 // base_min)), 160),
                        "bars120": _rows(_f.resample(b1, max(1, 120 // base_min)), 110)}
        levels = {}
        try:                                   # current finalized 2H leg -> fib levels
            b2 = _f.resample(b1, 120 // (int((b1[1].ts - b1[0].ts).total_seconds()) // 60 or 5))
            bi = BookImpulse(0.382, 0.786, True, re_anchor_ratio=0.618)
            zz, atr = ZigZag(0.382, 1.5), AtrStreamer()
            for i in range(1, len(b2)):
                p = b2[i - 1]
                a = atr.update(p)
                zz.update(i - 1, p, a)
                lo = next((x for x in reversed(zz.pivots) if x.kind is PivotType.LOW), None)
                hi = next((x for x in reversed(zz.pivots) if x.kind is PivotType.HIGH), None)
                bi.update(i - 1, p, lo, hi)
            cl = bi.current_leg()
            if cl and cl[0].price != cl[1].price:
                o, e, d = cl
                rng = abs(e.price - o.price)
                levels = {str(L): round(e.price - L * rng * d, 2) for L in LV}
                levels["top"] = round(e.price, 2)
                levels["origin"] = round(o.price, 2)
        except Exception:  # noqa: BLE001
            pass
        out[sym] = {"bars": bars, "levels": levels}
    (Path(out_dir) / "book_charts.json").write_text(json.dumps(out, separators=(",", ":")))
    if deep:
        (Path(out_dir) / "book_charts_htf.json").write_text(json.dumps(htf, separators=(",", ":")))
    print(f"book_charts: {len(out)} symbols" + (f" +htf {len(htf)}" if deep else ""))


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

    market_ctx = _annotate_context(by_tf)

    # freshest bar across the universe — lets the app detect holidays/halts:
    # clock says "open" but bars are stale => NSE isn't actually trading.
    try:
        last_bar_epoch = max(int(b[-1].ts.timestamp()) for b in base.values() if b)
    except ValueError:
        last_bar_epoch = None

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),   # tz-aware: browsers in ANY timezone parse the age correctly
        "last_bar_epoch": last_bar_epoch,
        "market_ctx": market_ctx,
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
    # persistent PAPER LOG — append-only, kept by the cloud cron so the paper agent's
    # ledger never loses trades to the rolling history window. Each device's agent
    # slices this by its own start date; nothing depends on the user being online.
    log_path = out.parent / "paper_log.json"
    try:
        plog = json.loads(log_path.read_text()) if log_path.exists() else {"trades": []}
    except Exception:  # noqa: BLE001
        plog = {"trades": []}
    seen = {(t["symbol"], t["entry_ts"]) for t in plog["trades"]}
    conf = by_tf[DEFAULT_TF]["byMethod"][DEFAULT_METHOD]["byConf"][DEFAULT_CONF]
    new_n = 0
    for h in conf["history"]:
        if not h.get("entry_ts") or (h["symbol"], h["entry_ts"]) in seen:
            continue
        # owner ruling 2026-07-05: the swing engine trades LONGS ONLY (shorts lost
        # −61R/11yr, every era). Short setups stay visible in the app as information,
        # but the paper ledger records only the trades the system actually takes.
        if str(h.get("side", "")).lower().startswith("s"):
            continue
        # Pocket is a STOCK swing engine — indices belong to the Gem, not this log
        if str(h.get("symbol", "")).startswith("^"):
            continue
        plog["trades"].append({"symbol": h["symbol"], "side": h["side"],
                               "entry_ts": h["entry_ts"], "exit_ts": h.get("ts"),
                               "entry": h["entry"], "sl": h["sl"], "r": h.get("r"),
                               "ctx_pass": bool((h.get("ctx") or {}).get("pass"))})
        new_n += 1
    if new_n:
        plog["trades"].sort(key=lambda t: t["entry_ts"])
        plog["trades"] = plog["trades"][-5000:]
        log_path.write_text(json.dumps(plog, separators=(",", ":")))
    print(f"paper_log: +{new_n} new, {len(plog['trades'])} total")

    # level paper books (Scalper + Defense) — errors here must never kill the scan
    # feed. The books get a WIDER universe than the swing matrix: extra symbols are
    # fetched here (books-only) so Pocket's runtime is untouched.
    try:
        from fibleg import paper_levels
        books_base = dict(base)
        extra_fetch = None
        if args.source == "yf" or _FYERS_FELL_BACK:
            def extra_fetch(s):
                return feeds.yfinance_series(s, period="60d", interval="5m")
        elif args.source == "fyers":
            from fibleg.data import fyers_feed
            _fc = fyers_feed.get_client()          # reuses today's cached token
            def extra_fetch(s):                    # 20 days = one chunk, fast + rate-friendly
                return fyers_feed.fyers_series(_fc, s, "5m", days=20)
        if extra_fetch is not None:
            for s in BOOK_EXTRA_SYMBOLS:
                if s in books_base:
                    continue
                try:
                    books_base[s] = extra_fetch(s)
                except Exception as fe:  # noqa: BLE001
                    print(f"paper_levels: skip {s} ({fe})")
        paper_levels.run(books_base, out.parent)
        # small 5m+levels file every scan (live chart); deep 1H/2H file ~twice/hr
        _emit_book_charts(books_base, out.parent,
                          deep=(datetime.now(timezone.utc).minute % 30 < 6))
    except Exception as e:  # noqa: BLE001
        print("paper_levels error:", e)

    # add 🏛 Pocket's pending orders to the resting list: its 2H waiting-pullback / armed
    # setups ARE the limit orders resting at the entry zone (ctx-pass longs only). Errors
    # here must never kill the feed.
    try:
        ro_path = out.parent / "resting_orders.json"
        ro = json.loads(ro_path.read_text()) if ro_path.exists() else {"orders": []}
        wl = by_tf[DEFAULT_TF]["byMethod"][DEFAULT_METHOD]["byConf"][DEFAULT_CONF].get("watchlist", [])
        n_pk = 0
        for w in wl:
            if str(w.get("side", "")).lower().startswith("s"):        # longs only
                continue
            if str(w.get("symbol", "")).startswith("^"):              # stocks only (index = Gem)
                continue
            if w.get("state") not in ("waiting_pullback", "armed"):   # pending, not in-trade
                continue
            if not (w.get("ctx") or {}).get("pass"):                  # Pocket takes ctx-pass only
                continue
            leg = w.get("leg") or {}
            entry, stop = w.get("conf_entry", w.get("entry")), w.get("conf_sl", w.get("sl"))
            tgts = w.get("targets") or []
            if None in (entry, stop) or not tgts or leg.get("start") is None or leg.get("end") is None:
                continue
            b = base.get(w["symbol"]) or []
            px = b[-1].close if b else entry
            ro["orders"].append({
                "sym": w["symbol"], "eng": "pocket", "book": "POCKET",
                "tf": int(DEFAULT_TF), "lvl": 0.5, "d": 1,
                "entry": round(entry, 2), "stop": round(stop, 2), "tgt": round(tgts[-1], 2),
                "origin": round(leg["start"], 2), "top": round(leg["end"], 2),
                "origin_ts": leg.get("start_ts"), "top_ts": leg.get("end_ts"),
                "price": round(px, 2)})
            n_pk += 1
        ro["orders"].sort(key=lambda o: abs((o.get("price") or 0) - (o.get("entry") or 0)) / (o.get("price") or 1))
        ro_path.write_text(json.dumps(ro, separators=(",", ":")))
        print(f"resting: +{n_pk} pocket, {len(ro['orders'])} armed total")
    except Exception as e:  # noqa: BLE001
        print("pocket resting append error:", e)

    # 🎲 GAMMA MAP — dealer gamma flip level + walls per underlying, from the live Fyers
    # option chain (index + full liquid universe). OI moves slowly, so recompute ~every
    # 30 min and throttle to respect rate limits. Fyers only; forward-use only (no
    # historical OI). Feeds the 🎲 Gamma engine (built on top once the map is verified).
    if (args.source == "fyers" and not _FYERS_FELL_BACK
            and (os.environ.get("GAMMA_NOW") or datetime.now(timezone.utc).minute % 30 < 6)):
        try:
            from fibleg import gamma as _gamma
            from fibleg.data import fyers_feed as _ff
            _gc = _ff.get_client()
            gsyms = (["^NSEI", "^NSEBANK"] + list(BOOK_EXTRA_SYMBOLS)
                     + [s for s in base if s.endswith(".NS")])
            gmap, gseen = {}, set()
            for gs in gsyms:
                if gs in gseen:
                    continue
                gseen.add(gs)
                try:
                    ch = _ff.option_chain(_gc, gs, strikecount=20)
                    m = _gamma.build_map(ch) if ch else None
                    if m:
                        gmap[gs] = m
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.35)               # stay under Fyers' rate limit
            (out.parent / "gamma_map.json").write_text(json.dumps(
                {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "maps": gmap, "assumption": "dealers long calls, short puts"},
                separators=(",", ":")))
            print(f"gamma_map: {len(gmap)} underlyings")
        except Exception as e:  # noqa: BLE001
            print("gamma_map error:", e)

    # 🎲 GAMMA ENGINE — trade the cached gamma map (pin + squeeze), own ledger. Runs EVERY
    # scan (fills on new bars + walks open positions); the map itself refreshes ~every 30m.
    try:
        from fibleg import paper_gamma
        from fibleg.data import fyers_feed as _ff
        gm_path = out.parent / "gamma_map.json"
        maps = ((json.loads(gm_path.read_text()) or {}).get("maps", {})
                if gm_path.exists() else {})
        gbase = locals().get("books_base") or base
        # real call/put pricing (fill=ask, exit=last bid) from the live Fyers chain — fyers only
        chain_fn = quote_fn = None
        if args.source == "fyers" and not _FYERS_FELL_BACK:
            try:
                _oc = _ff.get_client()
                chain_fn = lambda s: _ff.option_chain(_oc, s, strikecount=20)   # noqa: E731
                quote_fn = lambda syms: _ff.option_quotes(_oc, syms)            # noqa: E731
            except Exception:  # noqa: BLE001
                chain_fn = quote_fn = None
        # gamma keeps its OWN armed orders inside paper_gamma.json (its own 🎲 tab) — it is
        # NOT mixed into the shared 🎯 Resting tab (that stays the 4 validated engines only).
        paper_gamma.run(gbase, maps, out.parent, chain_fn=chain_fn, quote_fn=quote_fn)
    except Exception as e:  # noqa: BLE001
        print("paper_gamma error:", e)

    # alert only the context-PASS LONG setups — the validated "best of the best"
    # (longs-only per owner ruling 2026-07-05; shorts remain visible in the app)
    longs = [w for w in d["watchlist"]
             if not str(w.get("side", "")).lower().startswith("s")]
    best = [w for w in longs if (w.get("ctx") or {}).get("pass")]
    maybe_telegram(best[:5] if best else longs[:2])


if __name__ == "__main__":
    main()
