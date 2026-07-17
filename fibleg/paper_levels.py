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

FORCED sizing per book — never overridden: 1% of that book's ₹4L equity per trade
(the live plan, owner 2026-07-10) · one position per symbol within the book · total
open risk <= 6% · the stop never moves · 0.05R cost charged on every close. Fills refused by the rules become
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
# FULL-COVERAGE paper sizing (owner, 2026-07-10): each book ₹10L @ 1%/trade = a uniform
# ₹10,000 per R across the option engines (fits ~all real lots); 6 concurrent max.
RISK_PCT, CAP_PCT = 0.01, 0.06
START_CAPITAL = 1_000_000.0
BOOKS = (("SCALP", "paper_levels.json"), ("DEEP", "paper_defense.json"))
# tripwires — the strategy's own fib levels (owner doctrine): drawdown from the
# equity peak. At the 0.618 (20% DD) new fills risk HALF. At the 0.886 (30% DD)
# the book HALTS: new fills go to shadow only, and it NEVER un-halts itself —
# only the owner can, by removing "halted" from the state file.
TRIP_HALF_DD, TRIP_HALT_DD = 0.20, 0.30
# BENCH (owner go 2026-07-17, tests 20/20b/20c): the 1H@0.618's trade-book line is
# suspended while the window-drift fill defect is fixed - live took 47 fortnight fills
# where canonical mechanics took 12 (+7.9R vs -35.2R on identical Fyers bars). Every
# 0.618 fill still trades in the SHADOW book (tag: benched-0618) so the fix has a
# before/after. The 0.786s stay live (win rates match their backtests: 43% vs ~42%).
# Revert = set False. Re-audition against the honest 1m line (+796R) after the fix.
BENCH_0618 = True


def _iso(ts) -> str:
    return ts.isoformat(timespec="seconds")


def _fill_events(bars5, tf: int, is_idx: bool, resting: list | None = None) -> list[dict]:
    """Replay the finalized-fib lifecycle; emit resting-order fill events for BOTH
    books (tagged), chronological. If `resting` is given, ALSO append the orders that
    are ARMED as of the latest bar (an active fib's traded levels not yet touched, with
    price still on the approach side) — the live "what's waiting at the broker" list."""
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
                                 "sig": sig, "born": prev.ts,
                                 "o_ts": origin.ts, "top_ts": extreme.ts})
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
                                    "window": window,
                                    # the exact leg this fill fired from, so the chart can
                                    # draw the fib (origin -> top, anchored in time, + labelled
                                    # ratio lines)
                                    "origin": round(fib["sig"][1], 2),
                                    "top": round(fib["e"], 2),
                                    "origin_ts": fib["o_ts"], "top_ts": fib["top_ts"],
                                    "lv": {str(k): round(v, 2) for k, v in fib["lv"].items()}})
                # death checks — every 5m close; wicks never break
                dead = (bar.close < fib["die"]) if d == 1 else (bar.close > fib["die"])
                if not dead:
                    dead = (bar.close > fib["e"]) if d == 1 else (bar.close < fib["e"])
                if dead:
                    was = fib["active"]
                    fibs.remove(fib)
                    if was and fibs:
                        max(fibs, key=lambda f: f["born"])["active"] = True
    # armed orders as of the latest bar: an active fib's traded levels not yet touched,
    # with price still on the approach side (waiting to be reached) — same stop/target math
    # as a fill, so this IS the order you'd rest at the broker.
    if resting is not None and fibs and bars5:
        last = bars5[-1].close
        for fib in fibs:
            if not fib["active"]:
                continue
            d = fib["d"]
            for L in LEVELS:
                if L in fib["consumed"]:
                    continue
                level = fib["lv"][L]
                if (last <= level) if d == 1 else (last >= level):   # already reached/passed
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
                    if tgt_kind == "struct":
                        tgt = None
                        for t in RUNGS[L]:
                            rp = fib["e"] - t * fib["rng"] * d
                            if abs(rp - level) >= 2 * risk:
                                tgt = rp
                                break
                    else:
                        tgt = fib["e"] - tgt_kind * fib["rng"] * d
                        if abs(tgt - level) < 2 * risk:
                            tgt = None
                    if tgt is None:
                        continue
                    resting.append({
                        "book": book, "tf": tf, "lvl": L, "d": d, "entry": round(level, 2),
                        "stop": round(stop, 2), "tgt": round(tgt, 2),
                        "origin": round(fib["sig"][1], 2), "top": round(fib["e"], 2),
                        "origin_ts": _iso(fib["o_ts"]), "top_ts": _iso(fib["top_ts"]),
                        "lv": {str(k): round(v, 2) for k, v in fib["lv"].items()},
                        "price": round(last, 2)})
    return out


def _walk(pos: dict, bars5) -> tuple[float, object, str] | None:
    d, entry, stop, tgt = pos["d"], pos["entry"], pos["stop"], pos["tgt"]
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0, None, "bad-risk"
    ets = datetime.fromisoformat(pos["ts"])
    k = 0
    for b in bars5:
        if b.ts < ets:
            continue
        if b.ts == ets:
            # the FILL bar itself: the limit filled INTRABAR, so the same bar can also take
            # out the stop (ordering unknowable) → assume the worst, as real money must
            if (b.low <= stop) if d == 1 else (b.high >= stop):
                return -1.0, b.ts, "stop"
            continue
        k += 1
        if (b.low <= stop) if d == 1 else (b.high >= stop):
            # gap honesty: a bar OPENING beyond the stop (overnight gap) fills a real stop
            # order at the OPEN — book that price, not the level we wished for. These books
            # hold overnight with tight cushions, so gap fills ARE the real loss tail.
            if (b.open < stop) if d == 1 else (b.open > stop):
                gr = (b.open - entry) / risk if d == 1 else (entry - b.open) / risk
                return round(gr, 3), b.ts, "gap-stop"
            return -1.0, b.ts, "stop"
        if (b.high >= tgt) if d == 1 else (b.low <= tgt):
            return abs(tgt - entry) / risk, b.ts, "target"
        if k >= pos["window"]:
            r = (b.close - entry) / risk if d == 1 else (entry - b.close) / risk
            return r, b.ts, "time"
    return None


def _walk_ladder(pos: dict, bars5) -> tuple[float, object, str] | None:
    """The DEEP book's reverse-fib LADDER exit (owner go 2026-07-09, test 13:
    +824R vs +727R over 11.3y with HALF the drawdown, 125R vs 234R). The bounce
    of the retracement leg (leg top -> entry) has rungs at .382/.5/.618/.786/.886;
    a rung TOUCHED becomes the floor from the NEXT bar; a rung BROKEN is a
    promotion; through .886 the move rides to the 10-session cap with the .886
    rung as the floor. Exit = retrace to the floor (-1R before the first rung).
    Gaps book the OPEN. Falls back to None-eligible only via _manage's guard."""
    d, entry, stop = pos["d"], pos["entry"], pos["stop"]
    top = pos.get("top")
    risk = abs(entry - stop)
    fall = (top - entry) * d if top is not None else 0.0
    if risk <= 0 or fall <= 0:
        return _walk(pos, bars5)                 # no leg captured — old fixed-target walk
    rungs = [entry + d * f * fall for f in (0.382, 0.5, 0.618, 0.786, 0.886)]
    ets = datetime.fromisoformat(pos["ts"])
    k = 0
    floor = None
    for b in bars5:
        if b.ts < ets:
            continue
        if b.ts == ets:                          # fill bar: same-bar stop-out check
            if (b.low <= stop) if d == 1 else (b.high >= stop):
                return -1.0, b.ts, "stop"
            continue
        k += 1
        if floor is None:
            if (b.low <= stop) if d == 1 else (b.high >= stop):
                if (b.open < stop) if d == 1 else (b.open > stop):
                    gr = (b.open - entry) / risk if d == 1 else (entry - b.open) / risk
                    return round(gr, 3), b.ts, "gap-stop"
                return -1.0, b.ts, "stop"
        elif (b.low <= floor) if d == 1 else (b.high >= floor):
            if (b.open < floor) if d == 1 else (b.open > floor):     # gapped through the rung
                gr = (b.open - entry) / risk if d == 1 else (entry - b.open) / risk
                return round(gr, 3), b.ts, "gap-rung"
            return round(abs(floor - entry) / risk, 3), b.ts, "rung"
        for rp in rungs:                          # promote AFTER the exit check
            if ((b.high >= rp) if d == 1 else (b.low <= rp)) \
                    and (floor is None or (rp > floor if d == 1 else rp < floor)):
                floor = rp
        if k >= pos["window"]:
            r = (b.close - entry) / risk if d == 1 else (entry - b.close) / risk
            if floor is not None:
                r = max(r, abs(floor - entry) / risk)
            return round(r, 3), b.ts, "time"
    return None


def _uses_ladder(book: str, pos: dict) -> bool:
    """Which positions exit via the reverse-fib ladder (owner go 2026-07-09):
    the whole DEEP trio (test 13: +824R vs +727R, DD halved), and the SCALP book's
    1H@0.618 combo (test 13b: +796R vs +609R on identical fills — its ~1-session
    window has room to climb). The 30-min 0.786 scalps and the Gem keep their
    fixed exits (13b: no time to climb / ladder rejected -29R vs +4R)."""
    if book == "DEEP":
        return True
    return book == "SCALP" and pos.get("tf") == 60 and pos.get("lvl") == 0.618


def _manage(st: dict, base: dict, book: str = "") -> None:
    for lst_key, closed_key, real in (("open", "closed", True),
                                      ("shadow_open", "shadow_closed", False)):
        still = []
        for pos in st[lst_key]:
            walk_fn = _walk_ladder if _uses_ladder(book, pos) else _walk
            res = walk_fn(pos, base.get(pos["sym"]) or [])
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
        # a corrupt state file must NOT silently wipe a live book (P&L, open positions,
        # the owner-only HALT flag) — try the previous run's backup before resetting
        try:
            bak = path.with_suffix(".json.bak")
            if bak.exists():
                st = json.loads(bak.read_text())
                print(f"paper_levels: {path.name} corrupt — recovered from .bak")
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


def _apply_unlock(st: dict, out_dir: Path, engine: str) -> None:
    """Owner unlock via request file — same race-free mechanism as paper_gamma."""
    if not st.get("halted"):
        return
    try:
        req = json.loads((Path(out_dir) / "unlock_request.json").read_text())
    except Exception:  # noqa: BLE001
        return
    r = req.get(engine)
    if not r or r.get("ts", "") <= str(st["halted"]):
        return
    equity = st["capital"] + st.get("realized", 0.0)
    st.setdefault("unlocks", []).append(
        {"ts": _iso(datetime.now(timezone.utc)),
         "was_halted_since": st.pop("halted"),
         "why_halted": st.pop("halted_why", None),
         "unlock_reason": r.get("reason"),
         "equity_at_unlock": round(equity), "old_peak": round(st.get("peak", 0))})
    st["peak"] = equity
    st["dd"] = 0.0
    try:
        print(f"paper_{engine}: UNLOCKED by owner request - {r.get('reason', '')[:80]}")
    except Exception:  # noqa: BLE001
        pass                                     # console encoding must never break a scan


def _weather(base, mctx):
    """Is the market hostile for mean-reversion scalps RIGHT NOW? Mirrors the TEST 16
    classifier that found the era break (hostile 0.618s: +332R 2015-22, -129R 2023-26):
    the broad market behaving like runs (gamma's own behavioral detector: ±1% move /
    range blowout / VIX+move / real trend) OR the floored VIX-high flag."""
    from .paper_gamma import _behavioral_runs
    nbars = base.get("^NSEI") or base.get("NIFTY 50") or base.get("NIFTY") or []
    via = _behavioral_runs(nbars, mctx) if nbars else []
    return bool(via) or bool((mctx or {}).get("vix_hi")), via


def _weather_stance(st, latest):
    """ADAPTIVE weather gate for the SCALP 1H@0.618 (owner go 2026-07-16, "we will
    implement the suggestions just discussed"; validated by TEST 16b walk-forward:
    252d window + ±0.05R/trade hysteresis beat BOTH fixed stances, +825R vs +799R/+467R,
    1 losing year in 12, 11 flips in 11y). The gate reads its own scorecard: trailing
    252 days of hostile-tagged 0.618 closes (BOTH books — blocked trades keep trading
    in shadow, so the evidence never stops accumulating). R/trade < -0.05 -> gate ON
    (hostile fills benched to shadow); > +0.05 -> gate OFF (re-admitted). Under 30
    samples it holds its stance. SEEDED ON at birth: the backtest's trailing window
    (2025-26 hostile 0.618s) is firmly negative."""
    g = st.setdefault("weather_gate", {
        "on": True, "seeded": "2026-07-16",
        "why": "test 16b: hostile 0.618s -129R since 2023; adaptive 252d/±0.05 beat both fixed stances"})
    try:
        cutoff = (latest - __import__("datetime").timedelta(days=252)).isoformat()
    except Exception:  # noqa: BLE001
        return g
    rs = [t["r"] for t in list(st.get("closed", [])) + list(st.get("shadow_closed", []))
          if t.get("tf") == 60 and t.get("lvl") == 0.618 and t.get("hostile")
          and t.get("r") is not None and (t.get("exit_ts") or "") >= cutoff]
    if len(rs) >= 30:
        rpt = sum(rs) / len(rs)
        g["trailing_rpt"], g["trailing_n"] = round(rpt, 4), len(rs)
        if g["on"] and rpt > 0.05:
            g["on"] = False; g["flipped"] = _iso(datetime.now(timezone.utc))
        elif not g["on"] and rpt < -0.05:
            g["on"] = True; g["flipped"] = _iso(datetime.now(timezone.utc))
    return g


def run(base: dict, out_dir, mctx=None, lots=None) -> None:
    out_dir = Path(out_dir)
    states = {}
    for book, fname in BOOKS:
        st = _load(out_dir / fname, base)
        if st is None:
            print("paper_levels: no bars, skipping")
            return
        if st["capital"] != START_CAPITAL:               # one-time recorded re-base
            st.setdefault("capital_adds", []).append(
                {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "amount": round(START_CAPITAL - st["capital"]),
                 "why": "full-coverage paper sizing: 10L @ 1% (owner, 2026-07-10)"})
            st["capital"] = START_CAPITAL
            st["peak"] = st["capital"] + st.get("realized", 0.0)   # re-base resets the peak
            st["dd"] = 0.0
        _apply_unlock(st, out_dir, {"SCALP": "scalper", "DEEP": "defense"}[book])
        states[book] = st

    for book, st in states.items():
        _manage(st, base, book)                # 1) advance everything already open

    fills = []                                 # 2) fresh resting-order fills
    legmap = {}                                # (sym,tf,lvl,entry) -> leg, so positions
    resting_all = []                           #     saved before leg-capture get backfilled
    for sym, bars5 in base.items():            #     + the live armed-order list
        if not bars5:
            continue
        is_idx = not sym.endswith(".NS")
        for tf in (60, 120):
            if is_idx and tf == 60:
                continue
            rest_local = []
            for ev in _fill_events(bars5, tf, is_idx, resting=rest_local):
                st = states[ev["book"]]
                key = f"{sym}|{ev['key']}"
                if ev.get("origin") is not None:
                    legmap[(sym, ev["tf"], ev["lvl"], round(ev["entry"], 2))] = {
                        "origin": ev["origin"], "top": ev["top"],
                        "origin_ts": _iso(ev["origin_ts"]) if ev.get("origin_ts") else None,
                        "top_ts": _iso(ev["top_ts"]) if ev.get("top_ts") else None,
                        "lv": ev.get("lv")}
                if (ev["ts"] <= datetime.fromisoformat(st["started"])
                        or key in st["taken_keys"]):
                    continue
                ev["sym"], ev["fullkey"] = sym, key
                fills.append(ev)
            for ro in rest_local:              # tag the armed orders with sym + engine
                ro["sym"] = sym
                ro["eng"] = ("gem" if is_idx else
                             ("defense" if ro["book"] == "DEEP" else "scalp"))
                resting_all.append(ro)
    fills.sort(key=lambda e: (e["ts"], 0 if e["book"] == "SCALP" else 1))
    # closest-to-fill first (by % distance from current price to the entry level)
    resting_all.sort(key=lambda o: abs(o["price"] - o["entry"]) / (o["price"] or 1))
    (out_dir / "resting_orders.json").write_text(json.dumps(
        {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "orders": resting_all}, separators=(",", ":")))

    # tripwire check per book (drawdown from the all-time equity peak)
    for book, _ in BOOKS:
        st = states[book]
        equity = st["capital"] + st["realized"]
        st["peak"] = max(st.get("peak", st["capital"]), equity)
        dd = 1.0 - equity / st["peak"] if st["peak"] > 0 else 0.0
        st["dd"] = round(dd, 4)
        if dd >= TRIP_HALT_DD and not st.get("halted"):
            st["halted"] = _iso(datetime.now(timezone.utc))   # owner-only reset
            st["halted_why"] = (f"30% drawdown tripwire: equity ₹{equity:,.0f} vs "
                                f"peak ₹{st['peak']:,.0f} (dd {dd*100:.1f}%)")
            print(f"paper_{book.lower()}: *** TRIPWIRE HALT — drawdown "
                  f"{dd*100:.1f}% breached {TRIP_HALT_DD*100:.0f}% ***")

    hostile, runs_via = _weather(base, mctx)
    latest = max((bars[-1].ts for bars in base.values() if bars), default=None)
    gate = _weather_stance(states["SCALP"], latest) if latest else {"on": False}

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
               "origin": ev.get("origin"), "top": ev.get("top"), "lv": ev.get("lv"),
               "origin_ts": _iso(ev["origin_ts"]) if ev.get("origin_ts") else None,
               "top_ts": _iso(ev["top_ts"]) if ev.get("top_ts") else None,
               "ts": _iso(ev["ts"]), "risk_rs": round(risk)}
        # real NSE lot check (owner 2026-07-16, "whatever it takes to know the minimum
        # capital"): same stamp gamma carries — underlying-basis risk of ONE real lot,
        # so the graduation capital-translation runs on recorded data, not estimates
        lot = (lots or {}).get(ev["sym"]) or (lots or {}).get(ev["sym"].replace(".NS", ""))
        if lot:
            per_share = abs(ev["entry"] - ev["stop"])
            pos["lot_size"] = lot
            pos["lot_risk"] = round(lot * per_share)
            pos["lots"] = int(risk // (lot * per_share)) if per_share > 0 else 0
        if halved:
            pos["half_risk"] = True
        is0618 = ev["book"] == "SCALP" and ev["tf"] == 60 and ev["lvl"] == 0.618
        if is0618:                             # weather stamp on EVERY 0.618 fill —
            pos["hostile"] = hostile           # both books feed the gate's own scorecard
            if runs_via:
                pos["runs_via"] = runs_via
            if mctx:
                pos["hdr_regime"] = mctx.get("regime")
                if mctx.get("vix") is not None:
                    pos["vix"], pos["vix_avg"] = mctx["vix"], mctx.get("vix_avg")
        if ev["book"] == "DEEP":               # tag the double-risk moments
            if any(p["sym"] == ev["sym"] for p in states["SCALP"]["open"]):
                pos["collision"] = True
        open_risk = sum(p["risk_rs"] for p in st["open"])
        if st.get("halted"):                   # the book's 0.886: shadow-only
            pos["skip"] = "tripwire-halt"
            st["shadow_open"].append(pos)
        elif is0618 and BENCH_0618:
            pos["skip"] = "benched-0618"
            st["shadow_open"].append(pos)
        elif is0618 and hostile and gate.get("on"):
            # ADAPTIVE WEATHER GATE (test 16b): hostile-weather 0.618s benched to
            # shadow while their own trailing scorecard is negative; the shadow book
            # keeps trading them, and sustained recovery re-admits them automatically.
            pos["skip"] = "hostile-weather"
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
        _manage(st, base, book)
        for lst in ("open", "closed", "shadow_open", "shadow_closed"):   # backfill legs
            for p in st.get(lst, []):
                if p.get("origin") is None:
                    m = legmap.get((p.get("sym"), p.get("tf"), p.get("lvl"),
                                    round(p.get("entry") or 0, 2)))
                    if m:
                        p.update(m)
        # trim CHRONOLOGICALLY (insertion order), not sorted() — a lexicographic trim
        # evicted alphabetically-first symbols regardless of age, and an evicted key's
        # fill (still inside the 60d bar window) would re-book as a duplicate
        st["taken_keys"] = list(dict.fromkeys(st["taken_keys"]))[-2000:]
        for k in ("closed", "shadow_closed"):
            st[k] = st[k][-2000:]
        st["equity"] = round(st["capital"] + st["realized"])
        st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out_path = out_dir / fname
        try:                                   # keep the prior good state as a recovery point
            if out_path.exists():
                out_path.replace(out_path.with_suffix(".json.bak"))
        except Exception:  # noqa: BLE001
            pass
        out_path.write_text(json.dumps(st, separators=(",", ":")))
        print(f"paper_{book.lower()}: equity {st['equity']} · open {len(st['open'])} · "
              f"closed {len(st['closed'])} · shadow {len(st['shadow_open'])}/"
              f"{len(st['shadow_closed'])} · +{new_n[book]} fills this run")
