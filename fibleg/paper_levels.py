"""Level-trade PAPER agent — the audition (started 2026-07-03).

Trades the backtested trio + index gem with RESTING ORDERS at finalized-fib levels
under the owner's exact lifecycle (a TF close through .382 finalizes the fib and
freezes the levels; origin re-anchors only past .618 with the 2x-TF tiebreaker;
the fib dies on a 5m close past .886-cushion or its own high; wicks never count):

  stocks : 1H@0.618 (level target, 375-min window) · 1H@0.786 + 2H@0.786 (30-min)
  indices: 2H@0.886 (level target, 375-min window)
  Overnight holds ALLOWED (the backtested edge lives there).

FORCED sizing — never overridden: 0.25% of equity risked per trade · ONE position
per symbol, first come first served · total open risk <= 1.5% of equity · the stop
never moves · 0.05R cost charged on every close. Fills refused by the rules become
SHADOW positions, managed identically and ledgered separately, so "first-come vs
2H-priority" (owner's question, 2026-07-03) can be answered from data later.

State lives in docs/paper_levels.json, committed by the cloud loop each run —
multi-device, append-only, survives the rolling data window."""
from __future__ import annotations

import bisect
import json
from datetime import datetime, timezone
from pathlib import Path

from .data import feeds
from .indicators.atr import AtrStreamer
from .models import PivotType
from .strategy.book_impulse import BookImpulse
from .strategy.pivots import ZigZag

MIN_LEG_ATR = 5.0
LEVELS = (0.5, 0.618, 0.786, 0.886)
RUNGS = {0.5: (0.382, 0.236, 0.0), 0.618: (0.5, 0.382, 0.236, 0.0),
         0.786: (0.618, 0.5, 0.382), 0.886: (0.786, 0.618, 0.5)}
STK = {(60, 0.618): 75, (60, 0.786): 6, (120, 0.786): 6}    # combo -> window (5m bars)
IDX = {(120, 0.886): 75}
CUSH_STK = {(60, 0.618): .0031, (60, 0.786): .0039, (60, 0.886): .0042,
            (120, 0.618): .0031, (120, 0.786): .0041, (120, 0.886): .0045}
CUSH_IDX = {(60, 0.618): .0020, (60, 0.786): .0018, (60, 0.886): .0012,
            (120, 0.618): .0020, (120, 0.786): .0018, (120, 0.886): .0025}
COST_R = 0.05
RISK_PCT, CAP_PCT = 0.0025, 0.015
START_CAPITAL = 450_000.0


def _iso(ts) -> str:
    return ts.isoformat(timespec="seconds")


def _fill_events(bars5, tf: int, is_idx: bool) -> list[dict]:
    """Replay the finalized-fib lifecycle over the 5m window; return resting-order
    fill events for the audition combos, chronological."""
    f2 = tf // 5
    b2 = feeds.resample(bars5, f2)
    if len(b2) < 60:
        return []
    b2h = feeds.resample(bars5, f2 * 2)
    combos = IDX if is_idx else STK
    cush = CUSH_IDX if is_idx else CUSH_STK
    bih = BookImpulse(0.382, 0.786, True, re_anchor_ratio=0.618)
    zzh, atrh, pivh, kh = ZigZag(0.382, 1.5), AtrStreamer(), [], 0
    bi = BookImpulse(0.382, 0.786, True, re_anchor_ratio=0.618,
                     htf_keep=lambda: bih.dir == bi.dir and not bih.locked)
    zz, atr = ZigZag(0.382, 1.5), AtrStreamer()
    m5_ts = [b.ts for b in bars5]
    fibs, seen, out = [], set(), []
    for i2 in range(1, len(b2)):
        prev = b2[i2 - 1]
        while kh < len(b2h) and b2h[kh].ts <= prev.ts:
            hb = b2h[kh]
            ph = zzh.update(kh, hb, atrh.update(hb))
            if ph is not None:
                pivh.append(ph)
            loh = next((p for p in reversed(pivh) if p.kind is PivotType.LOW), None)
            hih = next((p for p in reversed(pivh) if p.kind is PivotType.HIGH), None)
            bih.update(kh, hb, loh, hih)
            kh += 1
        atr_v = atr.update(prev)
        zz.update(i2 - 1, prev, atr_v)
        lo = next((p for p in reversed(zz.pivots) if p.kind is PivotType.LOW), None)
        hi = next((p for p in reversed(zz.pivots) if p.kind is PivotType.HIGH), None)
        bi.update(i2 - 1, prev, lo, hi)
        if i2 >= 50 and bi.locked:
            cl = bi.current_leg()
            if cl is not None:
                origin, extreme, d = cl
                rng = abs(extreme.price - origin.price)
                sig = (d, round(origin.price, 4), round(extreme.price, 4))
                if (rng > 0 and (atr_v <= 0 or rng >= MIN_LEG_ATR * atr_v)
                        and sig not in seen):
                    seen.add(sig)
                    lv = {L: (extreme.price - L * rng if d == 1
                              else extreme.price + L * rng) for L in LEVELS}
                    c886 = cush.get((tf, 0.886), .0025)
                    die = lv[0.886] * (1 - c886) if d == 1 else lv[0.886] * (1 + c886)
                    fibs.append({"d": d, "e": extreme.price, "lv": lv, "die": die,
                                 "rng": rng, "consumed": set(), "active": not fibs,
                                 "sig": sig, "born": prev.ts})
        if not fibs:
            continue
        j0 = bisect.bisect_right(m5_ts, prev.ts)
        j1 = bisect.bisect_right(m5_ts, b2[i2].ts)
        for j in range(j0, j1):
            bar = bars5[j]
            prevc = bars5[j - 1].close if j > 0 else bar.open
            for fib in list(fibs):
                d = fib["d"]
                for L in LEVELS:
                    if L in fib["consumed"]:
                        continue
                    level = fib["lv"][L]
                    hit = bar.low <= level if d == 1 else bar.high >= level
                    if not hit:
                        continue
                    approach = (prevc > level and bar.open > level) if d == 1 \
                        else (prevc < level and bar.open < level)
                    fib["consumed"].add(L)
                    if fib["active"] and approach and (tf, L) in combos:
                        ca = cush[(tf, L)] * level
                        stop = level - ca if d == 1 else level + ca
                        risk = abs(level - stop)
                        tgt = None
                        for t in RUNGS[L]:      # nearest level giving >= 2x risk
                            rp = fib["e"] - t * fib["rng"] * d
                            if abs(rp - level) >= 2 * risk:
                                tgt = rp
                                break
                        if tgt is not None:
                            out.append({"key": f"{tf}|{L}|{d}|{fib['sig'][1]}|{fib['sig'][2]}",
                                        "tf": tf, "lvl": L, "d": d, "ts": bar.ts,
                                        "entry": level, "stop": stop, "tgt": tgt,
                                        "window": combos[(tf, L)]})
                # death checks — every 5m bar close IS a 5m close; wicks never break
                dead = (bar.close < fib["die"]) if d == 1 else (bar.close > fib["die"])
                if not dead:
                    dead = (bar.close > fib["e"]) if d == 1 else (bar.close < fib["e"])
                if dead:
                    was = fib["active"]
                    fibs.remove(fib)
                    if was and fibs:
                        max(fibs, key=lambda f: f["born"])["active"] = True
    return out


def _walk(pos: dict, bars5) -> tuple[float, object, str] | None:
    """Advance an open position along the 5m bars after entry.
    Returns (gross r, exit_ts, reason) once resolved, else None (still open)."""
    d, entry, stop, tgt = pos["d"], pos["entry"], pos["stop"], pos["tgt"]
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0, None, "bad-risk"
    ets = datetime.fromisoformat(pos["ts"])
    k = 0
    for b in bars5:
        if b.ts <= ets:
            continue
        k += 1
        if (b.low <= stop) if d == 1 else (b.high >= stop):
            return -1.0, b.ts, "stop"
        if (b.high >= tgt) if d == 1 else (b.low <= tgt):
            return abs(tgt - entry) / risk, b.ts, "target"
        if k >= pos["window"]:
            r = (b.close - entry) / risk if d == 1 else (entry - b.close) / risk
            return r, b.ts, "time"
    return None


def _manage(st: dict, base: dict) -> None:
    for lst_key, closed_key, real in (("open", "closed", True),
                                      ("shadow_open", "shadow_closed", False)):
        still = []
        for pos in st[lst_key]:
            res = _walk(pos, base.get(pos["sym"]) or [])
            if res is None:
                still.append(pos)
                continue
            r, xts, reason = res
            rn = r - COST_R
            pos.update({"exit_ts": _iso(xts) if xts else None,
                        "r": round(rn, 3), "reason": reason,
                        "pnl": round(rn * pos["risk_rs"])})
            st[closed_key].append(pos)
            if real:
                st["realized"] += rn * pos["risk_rs"]
        st[lst_key][:] = still


def run(base: dict, out_dir) -> None:
    path = Path(out_dir) / "paper_levels.json"
    try:
        st = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        st = None
    if not st:
        # audition starts NOW: `started` = freshest bar, so history never backfills
        last = None
        for bars in base.values():
            if bars:
                last = bars[-1].ts if last is None or bars[-1].ts > last else last
        if last is None:
            print("paper_levels: no bars, skipping")
            return
        st = {"started": _iso(last), "capital": START_CAPITAL, "realized": 0.0,
              "open": [], "closed": [], "shadow_open": [], "shadow_closed": [],
              "taken_keys": []}
    taken = set(st["taken_keys"])
    started = datetime.fromisoformat(st["started"])

    _manage(st, base)                      # 1) advance everything already open

    fills = []                             # 2) fresh resting-order fills
    for sym, bars5 in base.items():
        if not bars5:
            continue
        is_idx = not sym.endswith(".NS")
        for tf in (60, 120):
            if is_idx and tf == 60:
                continue                   # index gem is 2H-only
            for ev in _fill_events(bars5, tf, is_idx):
                key = f"{sym}|{ev['key']}"
                if ev["ts"] <= started or key in taken:
                    continue
                ev["sym"], ev["fullkey"] = sym, key
                fills.append(ev)
    fills.sort(key=lambda e: e["ts"])

    equity = st["capital"] + st["realized"]
    for ev in fills:                       # 3) forced sizing gates, chronological
        taken.add(ev["fullkey"])
        pos = {"sym": ev["sym"], "tf": ev["tf"], "lvl": ev["lvl"], "d": ev["d"],
               "entry": round(ev["entry"], 2), "stop": round(ev["stop"], 2),
               "tgt": round(ev["tgt"], 2), "window": ev["window"],
               "ts": _iso(ev["ts"]), "risk_rs": round(equity * RISK_PCT)}
        open_risk = sum(p["risk_rs"] for p in st["open"])
        if any(p["sym"] == ev["sym"] for p in st["open"]):
            pos["skip"] = "stock-busy"
            st["shadow_open"].append(pos)
        elif open_risk + pos["risk_rs"] > equity * CAP_PCT:
            pos["skip"] = "risk-cap"
            st["shadow_open"].append(pos)
        else:
            st["open"].append(pos)

    _manage(st, base)                      # 4) same-run resolution of new fills

    st["taken_keys"] = sorted(taken)[-2000:]
    for k in ("closed", "shadow_closed"):
        st[k] = st[k][-2000:]
    st["equity"] = round(st["capital"] + st["realized"])
    st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(st, separators=(",", ":")))
    print(f"paper_levels: equity {st['equity']} · open {len(st['open'])} · "
          f"closed {len(st['closed'])} · shadow {len(st['shadow_open'])}/"
          f"{len(st['shadow_closed'])} · +{len(fills)} fills this run")
