"""Level-trade PAPER agents — two cloud books, one fib lifecycle (owner-approved).

Both books rest orders at finalized-fib levels under the owner's exact lifecycle
(a TF close through .382 finalizes the fib and freezes the levels; origin
re-anchors only past .618 with the 2x-TF tiebreaker; the fib dies on a 5m close
past .886-cushion or its own high; wicks never count).

SCALPER book (docs/paper_levels.json — the audition, live since 2026-07-03):
  stocks LONGS ONLY (owner ruling 2026-07-05): 1H@0.618 level-tgt 375m ·
  1H@0.786 + 2H@0.786 level-tgt 30m
  index BOTH SIDES (the Gem): Nifty/BankNifty 2H@0.886 level-tgt 375m
DEFENSE book (docs/paper_defense.json — the deep trio, longs only):
  1H@0.786 -> leg top · 1H@0.886 -> .618 · 2H@0.886 -> .618, hold up to
  10 sessions, tight stops. Overnight allowed everywhere (the edge lives there).

Books are INDEPENDENT (owner ruling): the same 1H@0.786 touch may legitimately
fill both — the Scalper leaves in 30 minutes, Defense holds for days. Such deep
fills are tagged "collision" so the double-risk moments stay measurable.

FORCED sizing per book — never overridden: 0.25% of that book's equity per trade ·
one position per symbol within the book · total open risk <= 1.5% · the stop never
moves · 0.05R cost charged on every close. Fills refused by the rules become
SHADOW positions, managed identically, ledgered separately."""
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
# combo tables: (tf, lvl) -> (target, window in 5m bars, longs_only)
#   target "struct" = nearest level giving >= 2x risk · a float = that fib ratio
SCALP_STK = {(60, 0.618): ("struct", 75, True), (60, 0.786): ("struct", 6, True),
             (120, 0.786): ("struct", 6, True)}
SCALP_IDX = {(120, 0.886): ("struct", 75, False)}          # the Gem: both sides
DEEP_STK = {(60, 0.786): (0.0, 750, True), (60, 0.886): (0.618, 750, True),
            (120, 0.886): (0.618, 750, True)}              # 750 bars = 10 sessions
CUSH_STK = {(60, 0.618): .0031, (60, 0.786): .0039, (60, 0.886): .0042,
            (120, 0.618): .0031, (120, 0.786): .0041, (120, 0.886): .0045}
CUSH_IDX = {(60, 0.618): .0020, (60, 0.786): .0018, (60, 0.886): .0012,
            (120, 0.618): .0020, (120, 0.786): .0018, (120, 0.886): .0025}
COST_R = 0.05
RISK_PCT, CAP_PCT = 0.0025, 0.015
START_CAPITAL = 450_000.0
BOOKS = (("SCALP", "paper_levels.json"), ("DEEP", "paper_defense.json"))
# tripwires — the strategy's own fib levels (owner doctrine): drawdown from the
# equity peak. At the 0.618 (20% DD) new fills risk HALF. At the 0.886 (30% DD)
# the book HALTS: new fills go to shadow only, and it NEVER un-halts itself —
# only the owner can, by removing "halted" from the state file.
TRIP_HALF_DD, TRIP_HALT_DD = 0.20, 0.30


def _iso(ts) -> str:
    return ts.isoformat(timespec="seconds")


def _fill_events(bars5, tf: int, is_idx: bool) -> list[dict]:
    """Replay the finalized-fib lifecycle; emit resting-order fill events for BOTH
    books (tagged), chronological."""
    f2 = tf // 5
    b2 = feeds.resample(bars5, f2)
    if len(b2) < 60:
        return []
    b2h = feeds.resample(bars5, f2 * 2)
    cush = CUSH_IDX if is_idx else CUSH_STK
    combos = []
    if is_idx:
        combos.append(("SCALP", SCALP_IDX))
    else:
        combos.append(("SCALP", SCALP_STK))
        combos.append(("DEEP", DEEP_STK))
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
                    if not (fib["active"] and approach):
                        continue
                    if not any((tf, L) in table for _, table in combos):
                        continue
                    ca = cush[(tf, L)] * level
                    stop = level - ca if d == 1 else level + ca
                    risk = abs(level - stop)
                    if risk <= 0:
                        continue
                    for book, table in combos:
                        spec = table.get((tf, L))
                        if spec is None:
                            continue
                        tgt_kind, window, longs_only = spec
                        if longs_only and d != 1:
                            continue
                        if tgt_kind == "struct":       # nearest level >= 2x risk
                            tgt = None
                            for t in RUNGS[L]:
                                rp = fib["e"] - t * fib["rng"] * d
                                if abs(rp - level) >= 2 * risk:
                                    tgt = rp
                                    break
                        else:                          # fixed fib-ratio target
                            tgt = fib["e"] - tgt_kind * fib["rng"] * d
                            if abs(tgt - level) < 2 * risk:
                                tgt = None
                        if tgt is None:
                            continue
                        out.append({"book": book,
                                    "key": f"{tf}|{L}|{d}|{fib['sig'][1]}|{fib['sig'][2]}",
                                    "tf": tf, "lvl": L, "d": d, "ts": bar.ts,
                                    "entry": level, "stop": stop, "tgt": tgt,
                                    "window": window})
                # death checks — every 5m close; wicks never break
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


def _load(path: Path, base: dict):
    try:
        st = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        st = None
    if not st:
        last = None
        for bars in base.values():
            if bars and (last is None or bars[-1].ts > last):
                last = bars[-1].ts
        if last is None:
            return None
        st = {"started": _iso(last), "capital": START_CAPITAL, "realized": 0.0,
              "open": [], "closed": [], "shadow_open": [], "shadow_closed": [],
              "taken_keys": []}
    return st


def run(base: dict, out_dir) -> None:
    out_dir = Path(out_dir)
    states = {}
    for book, fname in BOOKS:
        st = _load(out_dir / fname, base)
        if st is None:
            print("paper_levels: no bars, skipping")
            return
        states[book] = st

    for st in states.values():
        _manage(st, base)                      # 1) advance everything already open

    fills = []                                 # 2) fresh resting-order fills
    for sym, bars5 in base.items():
        if not bars5:
            continue
        is_idx = not sym.endswith(".NS")
        for tf in (60, 120):
            if is_idx and tf == 60:
                continue
            for ev in _fill_events(bars5, tf, is_idx):
                st = states[ev["book"]]
                key = f"{sym}|{ev['key']}"
                if (ev["ts"] <= datetime.fromisoformat(st["started"])
                        or key in st["taken_keys"]):
                    continue
                ev["sym"], ev["fullkey"] = sym, key
                fills.append(ev)
    fills.sort(key=lambda e: (e["ts"], 0 if e["book"] == "SCALP" else 1))

    # tripwire check per book (drawdown from the all-time equity peak)
    for book, _ in BOOKS:
        st = states[book]
        equity = st["capital"] + st["realized"]
        st["peak"] = max(st.get("peak", st["capital"]), equity)
        dd = 1.0 - equity / st["peak"] if st["peak"] > 0 else 0.0
        st["dd"] = round(dd, 4)
        if dd >= TRIP_HALT_DD and not st.get("halted"):
            st["halted"] = _iso(datetime.now(timezone.utc))   # owner-only reset
            print(f"paper_{book.lower()}: *** TRIPWIRE HALT — drawdown "
                  f"{dd*100:.1f}% breached {TRIP_HALT_DD*100:.0f}% ***")

    new_n = {b: 0 for b, _ in BOOKS}
    for ev in fills:                           # 3) forced sizing gates per book
        st = states[ev["book"]]
        st["taken_keys"].append(ev["fullkey"])
        equity = st["capital"] + st["realized"]
        halved = st.get("dd", 0) >= TRIP_HALF_DD      # the book's 0.618: half risk
        risk = equity * RISK_PCT * (0.5 if halved else 1.0)
        pos = {"sym": ev["sym"], "tf": ev["tf"], "lvl": ev["lvl"], "d": ev["d"],
               "entry": round(ev["entry"], 2), "stop": round(ev["stop"], 2),
               "tgt": round(ev["tgt"], 2), "window": ev["window"],
               "ts": _iso(ev["ts"]), "risk_rs": round(risk)}
        if halved:
            pos["half_risk"] = True
        if ev["book"] == "DEEP":               # tag the double-risk moments
            if any(p["sym"] == ev["sym"] for p in states["SCALP"]["open"]):
                pos["collision"] = True
        open_risk = sum(p["risk_rs"] for p in st["open"])
        if st.get("halted"):                   # the book's 0.886: shadow-only
            pos["skip"] = "tripwire-halt"
            st["shadow_open"].append(pos)
        elif any(p["sym"] == ev["sym"] for p in st["open"]):
            pos["skip"] = "stock-busy"
            st["shadow_open"].append(pos)
        elif open_risk + pos["risk_rs"] > equity * CAP_PCT:
            pos["skip"] = "risk-cap"
            st["shadow_open"].append(pos)
        else:
            st["open"].append(pos)
        new_n[ev["book"]] += 1

    for book, fname in BOOKS:                  # 4) same-run resolution + save
        st = states[book]
        _manage(st, base)
        st["taken_keys"] = sorted(set(st["taken_keys"]))[-2000:]
        for k in ("closed", "shadow_closed"):
            st[k] = st[k][-2000:]
        st["equity"] = round(st["capital"] + st["realized"])
        st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (out_dir / fname).write_text(json.dumps(st, separators=(",", ":")))
        print(f"paper_{book.lower()}: equity {st['equity']} · open {len(st['open'])} · "
              f"closed {len(st['closed'])} · shadow {len(st['shadow_open'])}/"
              f"{len(st['shadow_closed'])} · +{new_n[book]} fills this run")
