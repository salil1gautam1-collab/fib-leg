"""🎲 Gamma paper engine — the 5th book. Trades the live dealer-gamma map (gamma_map.json),
own ledger (paper_gamma.json), own account, same forced sizing + tripwires as the others.

Two modes, chosen automatically by the flip level (they can't conflict — a name is either
above or below its flip):
  • PIN  (positive regime / above flip / "bowl"): rest limits ~1.5 ATR either side of the
    dominant wall; a touch fades BACK toward the wall (target = wall, stop ~1 ATR beyond).
  • SQUEEZE (negative regime / below flip / "hill"): momentum — a break of the nearest wall
    in the move's direction, target the next wall, stop back across the break.

FORWARD-ONLY: there is no historical gamma map, so the engine can't replay history. It
starts from its first run (records `last_ts`) and only fills on 5m bars AFTER that — its
own forward equity curve is the whole answer to "does gamma make money". Every trade is
tagged `pin` or `squeeze`; the assumption (dealers long calls / short puts) is stamped on
each fill, so if it loses we know exactly what failed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .gamma import _bs_price
from .models import Bar  # noqa: F401  (type clarity)

# FULL-COVERAGE paper sizing (owner, 2026-07-10 final: "paper should cover 100% of the
# trades; capital decisions come later with the data — let the paper live its life"):
# ₹10L per option engine at 1% = ₹10,000/trade, which fits 1 real lot on 98.3% of signals
# (everything observed except LODHA-class freaks at ₹21-24k/lot — those route to the
# shadow book via the lot-too-big gate, still fully traded and scored there).
START_CAPITAL = 1_000_000.0
TARGET_CAPITAL = START_CAPITAL           # existing ledgers re-base once, recorded
RISK_PCT, CAP_PCT = 0.01, 0.06           # 1%/trade · 6 concurrent max
COST_R = 0.05
TRIP_HALF_DD, TRIP_HALT_DD = 0.20, 0.30
STRETCH_ATR = 1.5          # limit rests this far from the wall
STOP_ATR = 1.0             # stop this far BEYOND the entry (away from the wall)
PIN_WINDOW = 75            # PINS trade on 5m bars → 75 = ~1 session before a time-exit
SQ_WINDOW = 50             # SQUEEZE trades on 15m bars → 50 = ~2 sessions before a time-exit
SQ_TF_MIN = 15             # squeeze runs on 15m candles (a 15m CLOSE confirms the break) —
#                            pins stay on 5m; owner's call: "pins at 5 mins, squeeze at 15 mins"
ASSUMPTION = "dealers long calls, short puts"
# owner's profit-ladder (2026-07-08): once a trade reaches its target (the wall for pins, the
# next wall for a squeeze) we don't just book it — we let it run and LOCK a rising floor as the
# peak (MFE) climbs, so runners keep more. Applies to BOTH modes now (owner go, 2026-07-08).
# (peak R reached, floor R locked); highest first. Before the target, the hard −1R stop applies.
RATCHET = [(3.5, 3.0), (3.0, 2.0), (2.5, 1.8)]
# PROPORTIONAL LOCK above the ladder (owner go, 2026-07-08, after the Nifty crash-day pin
# peaked ~20R while the top rung locked only 3R): beyond a 4R peak the floor tracks the peak
# itself — keep 70% of it — so a monster runner can't fall back to +3R. The named rungs below
# 4R stay exactly as the owner set them.
PROP_FROM, PROP_KEEP = 4.0, 0.70
# INTRADAY ONLY (owner, 2026-07-08: "we don't want gamma trades to stay beyond 3:30"): the
# pinning force resets overnight (OI shifts, walls recompute next morning) and we hold
# near-expiry options — overnight theta+gap is risk without a thesis. Square off on the
# 15:25 IST bar (booked at its close, locked floor still protects); no NEW fills from 15:10.
EOD_MIN = 15 * 60 + 25          # 15:25 IST — force-square bar
NO_ENTRY_MIN = 15 * 60 + 10     # 15:10 IST — stop taking new fills
# BEHAVIORAL runs detector (owner + 2026-07-08 lesson): the structural gamma regime said
# "sticky" through a 330-pt crash because the flip FELL WITH the market — squeeze never
# armed on the most obvious runs day imaginable. The market also counts as runs when it
# BEHAVES like runs; each trigger is recorded so the data can judge them separately.
RUN_MOVE_PCT = 0.010            # Nifty ±1% from today's open = runs
RUN_RANGE_X = 1.8               # today's range ≥ 1.8× the prior-10-day average = runs
RUN_VIX_MOVE = 0.006            # "VIX high" counts only WITH ≥0.6% move (VIX alone = expectation)
# TWO-WAY with hysteresis (owner: "adapt quickly to market conditions", 2026-07-09): move-based
# triggers RELEASE at HALF their trip level once on — so a sharp dip that fully V-recovers
# hands the afternoon back to sticky (decisively both ways), while a move hovering at the
# threshold can't toggle the gate every scan. The RANGE trigger needs no hysteresis: a day's
# range can never shrink, so a genuinely wild day stays runs all session on its own merits.
RUN_RELEASE = 0.5               # release threshold = trip × this, while behavioral runs is on
DAY_BREAK_R = -5.0              # WEATHER-TABLE bundle (owner go 2026-07-15, "change and
#                                 record"): once the REAL book is down 5R on the day, no
#                                 more real fills until tomorrow (shadow keeps trading —
#                                 skip "day-breaker"). The anti-streak circuit breaker.
COOLDOWN_MIN = 60
OPEN_MIN = 9 * 60 + 30          # OPENING-BATCH gate (owner go 2026-07-17, "make the
#                                 change now"): fills on bars before 09:30 -> shadow.
#                                 Evidence: 212 recorded early fills at -0.363R/trade
#                                 (25% win) vs -0.089R/trade after 09:30 - the wall map
#                                 is priced off an unsettled opening auction; 7 same-bar
#                                 stops on 07-16 and 3 more on 07-17, all 09:20-09:25.
#                                 Extends the overnight-order logic 15 minutes: at 09:20
#                                 today's map isn't BORN yet. Shadow-scored; revertable.               # after a stop-out: same stock+mode+direction can't re-enter the
#                                 REAL book for this long (blocked -> shadow, skip "cooldown") —
#                                 owner 2026-07-09, after SWIGGY stopped 09:30 and refilled the
#                                 identical order 09:35 and stopped again. Winners don't trigger it.


def _iso(ts) -> str:
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def _atr(bars, period: int = 14) -> float:
    """Simple ATR on the tail of the 5m bars."""
    seq = bars[-(period + 1):]
    if len(seq) < 2:
        return 0.0
    trs = []
    for i in range(1, len(seq)):
        h, l, pc = seq[i].high, seq[i].low, seq[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _resample(bars, mins: int = SQ_TF_MIN):
    """Aggregate 5m bars into `mins`-minute candles, clock-aligned (robust to gaps). The still-
    forming final candle is dropped, so a squeeze only ever acts on a CLOSED 15m bar — that is
    the whole point of the 15m gate (a real 15m close beyond the wall, not a 5m wick through it)."""
    per = max(1, mins // 5)
    groups, cur, key = [], [], None
    for b in bars:
        k = (b.ts.toordinal() if hasattr(b.ts, "toordinal") else 0,
             (b.ts.hour * 60 + b.ts.minute) // mins)
        if key is not None and k != key:
            groups.append(cur); cur = []
        cur.append(b); key = k
    if cur:
        groups.append(cur)
    out = []
    for i, g in enumerate(groups):
        if i == len(groups) - 1 and len(g) < per:
            break                                     # drop the still-forming final candle
        out.append(Bar(ts=g[-1].ts, open=g[0].open,
                       high=max(x.high for x in g), low=min(x.low for x in g),
                       close=g[-1].close, volume=sum((x.volume or 0) for x in g)))
    return out


def _bars_for(pos: dict, bars):
    """The bars a position is judged on: 5m for pins, 15m for squeeze."""
    return _resample(bars, SQ_TF_MIN) if pos.get("mode") == "squeeze" else bars


def _behavioral_runs(bars, mctx=None, held=False) -> list[str]:
    """Is the broad market BEHAVING like runs right now? Returns the list of tripped
    triggers (empty = calm). Price triggers come straight off the Nifty 5m bars; the
    VIX/trend triggers use the same market snapshot shown in the app's header.
    held=True (behavioral runs was on at the last scan) applies the hysteresis release
    thresholds, so the verdict can flip back to sticky only on a DECISIVE calming."""
    if not bars:
        return []
    days: dict = {}
    for b in bars:                                   # group the 5m bars by session day
        days.setdefault(b.ts.date(), []).append(b)
    dates = sorted(days)
    tb = days[dates[-1]]
    day_open = tb[0].open
    move = abs(tb[-1].close - day_open) / day_open if day_open else 0.0
    rng = max(b.high for b in tb) - min(b.low for b in tb)
    via = []
    m_thr = RUN_MOVE_PCT * (RUN_RELEASE if held else 1.0)
    v_thr = RUN_VIX_MOVE * (RUN_RELEASE if held else 1.0)
    if move >= m_thr:
        via.append(f"move {move * 100:.1f}%")
    prior = [max(b.high for b in days[d]) - min(b.low for b in days[d]) for d in dates[:-1]][-10:]
    if len(prior) >= 3:
        avg = sum(prior) / len(prior)
        if avg > 0 and rng >= RUN_RANGE_X * avg:     # naturally sticky — a range can't shrink
            via.append(f"range {rng / avg:.1f}x")
    if mctx:
        if mctx.get("vix_hi") and move >= v_thr:
            via.append("vix+move")
        if mctx.get("regime") in ("UPT", "DNT"):     # a real trend; SDW/WHP do NOT count
            via.append(f"trend {mctx['regime']}")
    return via


def _orders(sym: str, gmap: dict, atr: float, px: float) -> list[dict]:
    """The armed gamma orders for one underlying at the current price. BOTH modes always
    arm — the fill-time gates decide which BOOK a fill lands in (real vs shadow), so every
    gate carries a scorecard. (Squeeze in a sticky market goes to shadow: 0/11 on the first
    sticky market — but that evidence predates the 15m-close redesign, so it must keep
    being measured, not silently discarded.)"""
    walls = gmap.get("walls") or []
    flip = gmap.get("flip")
    if not walls or flip is None or atr <= 0:
        return []
    wall = walls[0]["strike"]
    out = []
    if gmap.get("regime") == "positive":                     # PIN / bowl — fade to the wall
        # long limit below the wall (armed if price is above it, can dip in)
        e_lo = round(wall - STRETCH_ATR * atr, 2)
        if px > e_lo:
            out.append({"mode": "pin", "d": 1, "entry": e_lo, "tgt": round(wall, 2),
                        "stop": round(e_lo - STOP_ATR * atr, 2), "wall": wall,
                        "window": PIN_WINDOW})
        # short limit above the wall (armed if price is below it, can pop in)
        e_hi = round(wall + STRETCH_ATR * atr, 2)
        if px < e_hi:
            out.append({"mode": "pin", "d": -1, "entry": e_hi, "tgt": round(wall, 2),
                        "stop": round(e_hi + STOP_ATR * atr, 2), "wall": wall,
                        "window": PIN_WINDOW})
    else:                                                     # SQUEEZE / hill — break of the wall
        below = [w["strike"] for w in walls if w["strike"] < px]
        above = [w["strike"] for w in walls if w["strike"] > px]
        if below:                                            # break DOWN through nearest wall
            brk = max(below)
            tgt = max([b for b in below if b < brk], default=round(brk - 2 * atr, 2))
            out.append({"mode": "squeeze", "d": -1, "entry": round(brk - 0.1 * atr, 2),
                        "tgt": round(tgt, 2), "stop": round(brk + STOP_ATR * atr, 2),
                        "wall": brk, "window": SQ_WINDOW})
        if above:                                            # break UP through nearest wall
            brk = min(above)
            tgt = min([a for a in above if a > brk], default=round(brk + 2 * atr, 2))
            out.append({"mode": "squeeze", "d": 1, "entry": round(brk + 0.1 * atr, 2),
                        "tgt": round(tgt, 2), "stop": round(brk - STOP_ATR * atr, 2),
                        "wall": brk, "window": SQ_WINDOW})
    # keep only sane geometry (target and stop on opposite sides of entry, real risk)
    good = []
    for o in out:
        risk = abs(o["entry"] - o["stop"])
        rew = abs(o["tgt"] - o["entry"])
        if risk > 0 and rew > 0 and ((o["tgt"] - o["entry"]) * o["d"] > 0):
            o["dte"] = gmap.get("expiry_days")   # days to expiry — the pull is strongest near 0
            good.append(o)
    return good


def _pick_option(chain: dict, d: int) -> dict | None:
    """From a live option chain pick the slightly-ITM option to trade — a CALL for a long
    bet, a PUT for a short — and grab its real ask (buy) + bid. None if unusable."""
    if not chain or not chain.get("strikes") or not chain.get("spot"):
        return None
    S = chain["spot"]
    strikes = chain["strikes"]
    Ks = sorted(strikes)
    if d == 1:                                   # long -> slightly-ITM CALL (strike just below spot)
        cand = [k for k in Ks if k <= S] or Ks
        K, pref, typ = max(cand), "ce", "CE"
    else:                                        # short -> slightly-ITM PUT (strike just above spot)
        cand = [k for k in Ks if k >= S] or Ks
        K, pref, typ = min(cand), "pe", "PE"
    rec = strikes.get(K) or {}
    sym = rec.get(pref + "_sym")
    ask = rec.get(pref + "_ask") or rec.get(pref + "_ltp")
    bid = rec.get(pref + "_bid") or rec.get(pref + "_ltp")
    if not sym or not ask:
        return None
    return {"opt_sym": sym, "opt_type": typ, "opt_strike": K,
            "opt_entry": round(float(ask), 2),           # we BUY at the ask
            "opt_cur": round(float(bid or ask), 2)}      # current sellable (bid)


def _walk(pos: dict, bars):
    """Bar-by-bar exit with the owner's trailing profit-ladder, for BOTH modes: hard −1R stop
    until the target is reached (the wall for a pin, the next wall for a squeeze), then lock a
    rising floor (target→its R, then 2.5R peak→1.8R, 3R→2R, 3.5R→3R) and ride until price
    retraces to that floor. Pins walk 5m bars; squeeze walks 15m (caller resamples).
    Returns (r, exit_ts, reason, mfe) — or (None, None, None, mfe) if still open."""
    d, entry, stop, tgt = pos["d"], pos["entry"], pos["stop"], pos["tgt"]
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0, None, "bad-risk", 0.0
    wall_r = abs(tgt - entry) / risk          # the target in R (~1.5 pins; next-wall R for squeeze)
    ets = datetime.fromisoformat(pos["ts"])
    is_pin = pos.get("mode") == "pin"
    k = 0
    mfe = 0.0
    floor = None                              # locked profit floor (R), ratchets up with the peak
    for b in bars:
        if b.ts < ets:
            continue
        if b.ts == ets:
            # the FILL bar itself: a pin's limit filled INTRABAR, so the same bar can also take
            # out the stop (intrabar ordering is unknowable) → assume the worst, as real money
            # must. (Squeeze enters AT the close of this bar, so its fill bar can't stop it.)
            if is_pin and ((b.low <= stop) if d == 1 else (b.high >= stop)):
                return -1.0, b.ts, "stop", 0.0
            continue
        k += 1
        # gap honesty: if the bar OPENS beyond the stop/floor (overnight gap), a real stop
        # order fills at the open — book THAT price, not the level we wished for
        open_r = (b.open - entry) / risk if d == 1 else (entry - b.open) / risk
        fav = (b.high - entry) / risk if d == 1 else (entry - b.low) / risk        # best R this bar
        adverse = (b.low - entry) / risk if d == 1 else (entry - b.high) / risk    # worst R this bar
        if fav > mfe:
            mfe = fav
        # exit-check against the floor locked on PRIOR bars first (a floor set THIS bar only
        # takes effect next bar — else the target-touch bar's own dip would exit us instantly)
        if floor is None:                                           # no profit locked → hard stop
            if (b.low <= stop) if d == 1 else (b.high >= stop):
                if ((b.open < stop) if d == 1 else (b.open > stop)):
                    return round(open_r, 3), b.ts, "gap-stop", round(mfe, 3)
                return -1.0, b.ts, "stop", round(mfe, 3)
        elif adverse <= floor:                                      # retraced to the trailing floor
            if open_r < floor:                                      # gapped THROUGH the locked floor
                return round(open_r, 3), b.ts, "gap-trail", round(mfe, 3)
            return floor, b.ts, ("breakeven" if floor == 0.0 else
                                 "target" if abs(floor - wall_r) < 1e-6 else "trail"), round(mfe, 3)
        nf = next((lk for thr, lk in RATCHET if mfe >= thr), None)   # then ratchet up for next bar
        if mfe >= PROP_FROM:                                        # monster runner → floor rides the peak
            nf = max(nf or 0.0, round(mfe * PROP_KEEP, 3))
        if mfe >= wall_r:                                           # reached the target → lock ≥ its R
            nf = max(nf, wall_r) if nf is not None else wall_r      # (a far squeeze target isn't under-locked)
        if nf is not None and (floor is None or nf > floor):
            floor = nf
        if pos.get("be") is not None and floor is None and mfe >= pos["be"]:
            floor = 0.0                       # STUDY twins only: breakeven rung (owner question
            #                                   2026-07-10 — does BE pay at gamma's wall?)
        if (b.ts.hour * 60 + b.ts.minute) >= EOD_MIN:        # 15:25 IST — square off, no overnights
            r = (b.close - entry) / risk if d == 1 else (entry - b.close) / risk
            if floor is not None:
                r = max(r, floor)             # a locked floor is never given back at the close
            return round(r, 3), b.ts, "eod", round(mfe, 3)
        if k >= pos["window"]:
            r = (b.close - entry) / risk if d == 1 else (entry - b.close) / risk
            if floor is not None:
                r = max(r, floor)             # a locked floor is never given back at the time-exit
            return round(r, 3), b.ts, "time", round(mfe, 3)
    return None, None, None, round(mfe, 3)     # still open — report the running peak so far


def _manage(st: dict, base: dict) -> None:
    for lst_key, closed_key in (("open", "closed"), ("shadow_open", "shadow_closed")):
        keep = []
        for pos in st.get(lst_key, []):
            bars = base.get(pos["sym"])
            res = _walk(pos, _bars_for(pos, bars)) if bars else None
            if res is None:                          # no bars for this symbol yet
                keep.append(pos)
                continue
            r, xts, reason, mfe = res
            if reason is None:                       # still open — record the running peak, keep it
                pos["potential_r"] = mfe
                keep.append(pos)
                continue
            r -= COST_R
            pos.update({"exit_ts": _iso(xts) if xts else None, "r": round(r, 3),
                        "reason": reason, "potential_r": mfe})
            if pos.get("opt_cur") is not None:           # book the option exit at its last real bid
                pos["opt_exit"] = pos["opt_cur"]
                if pos.get("opt_risk"):                  # REAL option R: what the money actually did
                    pos["opt_r"] = round((pos["opt_exit"] - pos["opt_entry"]) / pos["opt_risk"], 3)
            if lst_key == "open":
                st["realized"] += pos["risk_rs"] * r
            st[closed_key].append(pos)
        st[lst_key] = keep


def _load(path: Path, latest_ts) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            pass
    # fresh start — forward only, so nothing before now counts
    return {"started": _iso(latest_ts), "last_ts": _iso(latest_ts),
            "capital": START_CAPITAL, "realized": 0.0, "peak": START_CAPITAL,
            "dd": 0.0, "open": [], "closed": [], "shadow_open": [], "shadow_closed": []}


def run(base: dict, maps: dict, out_dir, chain_fn=None, quote_fn=None, lots=None,
        mctx=None) -> list[dict]:
    """Advance the gamma book one scan. Returns the armed orders (for the Resting tab).
    chain_fn(symbol)->option chain and quote_fn([opt_syms])->{sym:{bid,ask,ltp}} let it book
    each trade at the REAL Fyers call/put price (fill=ask, exit=last bid); None = skip (yf).
    lots = {sym: real NSE F&O lot size} so each fill records whether ≥1 real lot fits.
    mctx = the app-header market snapshot {regime, vix_hi} for the behavioral runs triggers."""
    out_dir = Path(out_dir)
    path = out_dir / "paper_gamma.json"
    latest = None
    for b in base.values():
        if b and (latest is None or b[-1].ts > latest):
            latest = b[-1].ts
    if latest is None:
        return []
    st = _load(path, latest)
    if st["capital"] != TARGET_CAPITAL:                      # one-time recorded re-base
        st.setdefault("capital_adds", []).append(
            {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "amount": round(TARGET_CAPITAL - st["capital"]),
             "why": "full-coverage paper sizing: 10L @ 1% (owner, 2026-07-10)"})
        st["capital"] = TARGET_CAPITAL
        st["peak"] = st["capital"] + st.get("realized", 0.0)   # a re-base resets the peak
        st["dd"] = 0.0                                         # — else the tripwire fires
        st.pop("halted", None)                                 #   against a dead era
    # one-time repair (2026-07-13): the 10L re-base shipped WITHOUT the peak reset above,
    # so the 30% tripwire fired against the 20L-era peak and halted the book at the open.
    # A peak that exceeds capital by more than anything realized could have contributed
    # is impossible — re-anchor it and lift the artifact halt. (Remove after 2026-07-20.)
    if st.get("peak", 0) - st["capital"] > abs(st.get("realized", 0.0)) + 200_000:
        st["peak"] = st["capital"] + st.get("realized", 0.0)
        st["dd"] = 0.0
        st.pop("halted", None)

    # re-price OPEN option positions at the live bid, so an exit this scan books a real price
    if quote_fn:
        osyms = [p.get("opt_sym") for p in st.get("open", []) if p.get("opt_sym")]
        q = quote_fn(osyms) if osyms else {}
        for p in st.get("open", []):
            info = q.get(p.get("opt_sym"))
            px = info and (info.get("bid") or info.get("ltp"))
            if px:
                p["opt_cur"] = round(float(px), 2)

    _manage(st, base)                                        # 1) advance open positions

    # backfill potential_r on closed trades that predate the feature — the 5m bars covering
    # their entry→exit are still in the window, so we can re-measure the max favourable move
    for pos in st.get("closed", []):
        if pos.get("potential_r") is None:
            bars = base.get(pos["sym"])
            if bars:
                res = _walk(pos, _bars_for(pos, bars))
                if res is not None:
                    pos["potential_r"] = res[3]

    equity = st["capital"] + st["realized"]                  # 2) tripwire
    st["peak"] = max(st.get("peak", st["capital"]), equity)
    dd = 1.0 - equity / st["peak"] if st["peak"] > 0 else 0.0
    st["dd"] = round(dd, 4)
    if dd >= TRIP_HALT_DD and not st.get("halted"):
        st["halted"] = _iso(datetime.now(timezone.utc))
        st["halted_why"] = (f"30% drawdown tripwire: equity ₹{equity:,.0f} vs peak "
                            f"₹{st['peak']:,.0f} (dd {dd*100:.1f}%)")

    last_ts = datetime.fromisoformat(st["last_ts"])          # 3) armed orders + fresh fills
    # market-runs switch: squeeze fires only when the BROAD market (Nifty) is itself in
    # runs — STRUCTURAL (below its gamma flip) or BEHAVIORAL (it's moving like runs:
    # ±1% day move / range blowout / VIX-high + movement / a real trend). TWO-WAY with
    # hysteresis: once on, move-based triggers release only at HALF their trip level,
    # so runs turns off on a decisive V-recovery but never chatters at the threshold.
    nifty = (maps or {}).get("^NSEI") or (maps or {}).get("NIFTY") or {}
    struct_runs = nifty.get("regime") == "negative"
    nbars = base.get("^NSEI") or base.get("NIFTY") or []
    today = str(nbars[-1].ts.date()) if nbars else None
    prev = st.get("beh_runs") or {}
    held = bool(prev.get("on")) and prev.get("date") == today   # hysteresis is same-day only
    beh_via = _behavioral_runs(nbars, mctx, held=held)
    st["beh_runs"] = {"date": today, "on": bool(beh_via)}
    st.pop("runs_latch", None)                                  # superseded by hysteresis
    market_runs = struct_runs or bool(beh_via)
    st["market_regime"] = "runs" if market_runs else "sticky"
    st["runs_via"] = (["gamma-flip"] if struct_runs else []) + beh_via
    # which WAY is the market running? Lets the data separate pins WITH the run (a short
    # pin in a falling market — yesterday's +20.6R Nifty) from pins AGAINST it (longs
    # fading a crash — steamrolled). The eventual pin gate, if any, is likely directional.
    tb = [b for b in nbars if b.ts.date() == nbars[-1].ts.date()] if nbars else []
    if tb and tb[0].open:                                    # today's move — the status strip
        st["day_move_pct"] = round(abs(tb[-1].close - tb[0].open) / tb[0].open * 100, 2)
    if market_runs and tb:
        st["market_dir"] = "down" if tb[-1].close < tb[0].open else "up"
    else:
        st["market_dir"] = None
    # 3a) FILLS come from the PREVIOUS scan's armed orders — true resting-order semantics,
    # exactly what a real broker would be holding when these bars printed. (Filling against
    # orders re-pegged to the CURRENT price is adverse selection: a break that STICKS moves
    # the re-pegged order away and only the fakeouts would ever fill.)
    # orders DIE at the close like positions do (owner go, 2026-07-09): the thesis resets
    # overnight (map recomputes, we hold nothing overnight), so yesterday's resting orders
    # must not gap-fill into the opening chaos — the 09:15 massacre AND the 09:15 windfalls
    # both belong to a dead map. A fill on a bar from a LATER session than the one the
    # orders were pegged in is tagged stale and routed to the shadow book (its own ⛔
    # scorecard tells us what starting fresh each morning saves or costs).
    fills = []
    for o in st.get("armed") or []:
        bars = base.get(o["sym"])
        if not bars:
            continue
        if o.get("mode") == "squeeze":                       # break CONFIRMED by a 15m CLOSE
            for b in _resample(bars, SQ_TF_MIN):
                if b.ts <= last_ts:
                    continue
                if (b.ts.hour * 60 + b.ts.minute) >= NO_ENTRY_MIN:   # too close to the bell
                    continue
                if (b.close >= o["entry"]) if o["d"] == 1 else (b.close <= o["entry"]):
                    # honesty: you only KNOW the break once the candle closes, so the real
                    # entry is that CLOSE — never the level itself. If the close already ran
                    # too far (past target, or reward < half the risk), it's unchaseable:
                    # a real trader passes — skip and count it.
                    real_e = round(b.close, 2)
                    risk_d = abs(real_e - o["stop"])
                    rew_d = (o["tgt"] - real_e) * o["d"]
                    if rew_d <= 0 or (risk_d > 0 and rew_d / risk_d < 0.5):
                        st["chase_skips"] = st.get("chase_skips", 0) + 1
                        break
                    fills.append({**o, "armed_entry": o["entry"], "entry": real_e,
                                  "entry_kind": "15m-close", "ts": b.ts,
                                  "stale": b.ts.date() != last_ts.date(),
                                  "window": o.get("window") or SQ_WINDOW})
                    break
        else:                                                # pin LIMIT — fill on a 5m touch
            for b in bars:
                if b.ts <= last_ts:
                    continue
                if (b.ts.hour * 60 + b.ts.minute) >= NO_ENTRY_MIN:   # too close to the bell
                    continue
                if (b.low <= o["entry"]) if o["d"] == 1 else (b.high >= o["entry"]):
                    fills.append({**o, "entry_kind": "limit-touch", "ts": b.ts,
                                  "stale": b.ts.date() != last_ts.date(),
                                  "window": o.get("window") or PIN_WINDOW})
                    break

    # 3b) re-peg the armed orders for the NEXT scan from the fresh map (cancel/replace,
    # like re-pegging real resting orders once per scan)
    armed = []
    for sym, gmap in (maps or {}).items():
        bars = base.get(sym)
        if not bars or len(bars) < 60:
            continue
        atr = _atr(bars)
        px = bars[-1].close
        for o in _orders(sym, gmap, atr, px):
            armed.append({**o, "sym": sym, "eng": "gamma", "price": round(px, 2)})

    fills.sort(key=lambda e: e["ts"])                        # 4) apply forced sizing gates
    for ev in fills:
        equity = st["capital"] + st["realized"]
        halved = st.get("dd", 0) >= TRIP_HALF_DD
        risk = equity * RISK_PCT * (0.5 if halved else 1.0)
        pos = {"sym": ev["sym"], "eng": "gamma", "mode": ev["mode"], "d": ev["d"],
               "entry": ev["entry"], "stop": ev["stop"], "tgt": ev["tgt"],
               "wall": ev["wall"], "window": ev["window"], "ts": _iso(ev["ts"]),
               "dte": ev.get("dte"), "mkt": st.get("market_regime"),   # market regime at entry
               "mkt_via": st.get("runs_via") or None,       # WHICH trigger said runs (data judges each)
               "mkt_dir": st.get("market_dir"),             # which way the run points (aligned vs counter)
               "entry_kind": ev.get("entry_kind"),
               "risk_rs": round(risk), "assumption": ASSUMPTION}
        if ev.get("armed_entry") is not None:
            pos["armed_entry"] = ev["armed_entry"]           # the level; entry = the real close
        if chain_fn:                                         # grab the REAL call/put + entry ask
            try:
                opt = _pick_option(chain_fn(ev["sym"]), ev["d"])
                if opt:
                    pos.update(opt)
                    # the option's REAL risk denominator: premium paid minus the option's
                    # Black-Scholes value if the underlying hits the stop — a winner never
                    # shows us that price, so it must be modeled, not observed
                    g = (maps or {}).get(ev["sym"]) or {}
                    sig = g.get("sigma")
                    if sig and pos.get("opt_strike") and ev.get("dte") is not None:
                        T = max(float(ev["dte"]), 0.25) / 365.0
                        v_stop = _bs_price(ev["stop"], pos["opt_strike"], T, sig,
                                           pos.get("opt_type") == "CE")
                        orisk = pos["opt_entry"] - v_stop
                        pos["opt_risk"] = round(max(orisk, 0.05 * pos["opt_entry"]), 2)
            except Exception:  # noqa: BLE001
                pass
        lot = (lots or {}).get(ev["sym"])                    # can real money take this trade?
        if lot:
            pos["lot_size"] = lot
            per_share = pos.get("opt_risk") or abs(ev["entry"] - ev["stop"])
            pos["lot_basis"] = "opt" if pos.get("opt_risk") else "und"
            lot_risk = lot * per_share                       # ₹ risked by ONE real lot
            pos["lot_risk"] = round(lot_risk)
            pos["lots"] = int(risk // lot_risk) if lot_risk > 0 else 0
        if halved:
            pos["half_risk"] = True
        # COUNTER-RUN PIN GATE (owner, 2026-07-09: "change now, record, revert later if
        # needed"): on a runs day, a pin FIGHTING the run (long into a crash / short into
        # a melt-up) goes to the SHADOW book instead of the real one — it still fills,
        # walks and closes there, so the shadow's net R tells us exactly what the gate
        # saved (or cost) and the gate is reversible with evidence. Aligned pins and all
        # calm-day pins trade live. Evidence base: 2026-07-08 counter pins steamrolled;
        # the aligned Nifty short made +20.6R.
        counter_run = (pos["mode"] == "pin" and st.get("market_regime") == "runs"
                       and st.get("market_dir") is not None
                       and ((pos["d"] == 1) == (st["market_dir"] == "down")))
        # WEATHER TABLE (owner go 2026-07-15): sticky trades enter the TRADE book ONLY in
        # quiet sideways weather — a running mood (even aligned; 3 straight losing
        # sessions) or a high VIX (nervous chop, the sideways+high-VIX killer) routes
        # them to the shadow book, each under its own tag so each rule is scored.
        runs_aligned = (pos["mode"] == "pin" and not counter_run
                       and st.get("market_regime") == "runs")
        vix_high = (pos["mode"] == "pin" and st.get("market_regime") != "runs"
                    and bool((mctx or {}).get("vix_hi")))
        # circuit breaker: the trade book's booked R today
        _today = ev["ts"].date().isoformat()
        day_r = sum(t.get("r") or 0 for t in st["closed"]
                    if (t.get("exit_ts") or "").startswith(_today))
        day_broken = day_r <= DAY_BREAK_R
        # raw weather stamp (study: low/normal/high VIX bands, regime splits)
        if mctx:
            pos["vix_hi"] = mctx.get("vix_hi")
            pos["hdr_regime"] = mctx.get("regime")
            if mctx.get("vix") is not None:
                pos["vix"] = mctx["vix"]
                pos["vix_avg"] = mctx.get("vix_avg")
        # the mirror gate (owner, 2026-07-09: "why not a shadow book for both?"): a running
        # trade born in a STICKY market goes to shadow — its 0/11 evidence predates the
        # 15m-close redesign, so this gate must keep earning its place on a scorecard too
        sq_sticky = pos["mode"] == "squeeze" and st.get("market_regime") != "runs"
        # cooldown: did this exact bet (stock+mode+direction) just STOP out? Re-entering the
        # identical order minutes later is doubling down on a thesis the market just rejected.
        cooled = False
        for c in reversed(st["closed"][-50:]):               # recent real-book closes only
            if (c.get("sym") == pos["sym"] and c.get("mode") == pos["mode"]
                    and c.get("d") == pos["d"] and c.get("reason") in ("stop", "gap-stop")
                    and c.get("exit_ts")):
                dt = (ev["ts"] - datetime.fromisoformat(c["exit_ts"])).total_seconds() / 60.0
                if 0 <= dt < COOLDOWN_MIN:
                    cooled = True
                break                                        # newest matching close decides
        open_risk = sum(p["risk_rs"] for p in st["open"])
        if st.get("halted"):
            pos["skip"] = "tripwire-halt"; st["shadow_open"].append(pos)
        elif pos.get("lot_risk") and pos["lot_risk"] > risk:
            # one lot risks more than the whole trade budget — real money could not take
            # this (LODHA-class freaks). The shadow book keeps trading + scoring it.
            pos["skip"] = "lot-too-big"; st["shadow_open"].append(pos)
        elif ev.get("stale"):                                # yesterday's leftover order
            pos["skip"] = "overnight-order"; st["shadow_open"].append(pos)
        elif (ev["ts"].hour * 60 + ev["ts"].minute) < OPEN_MIN:
            pos["skip"] = "opening-batch"; st["shadow_open"].append(pos)
        elif cooled:
            pos["skip"] = "cooldown"; st["shadow_open"].append(pos)
        elif day_broken:
            pos["skip"] = "day-breaker"; st["shadow_open"].append(pos)
        elif counter_run:
            pos["skip"] = "counter-run"; st["shadow_open"].append(pos)
        elif runs_aligned:
            pos["skip"] = "runs-aligned"; st["shadow_open"].append(pos)
        elif vix_high:
            pos["skip"] = "vix-high"; st["shadow_open"].append(pos)
        elif sq_sticky:
            pos["skip"] = "market-sticky"; st["shadow_open"].append(pos)
        elif any(p["sym"] == ev["sym"] for p in st["open"]):
            pos["skip"] = "stock-busy"; st["shadow_open"].append(pos)
        elif open_risk + pos["risk_rs"] > equity * CAP_PCT:
            pos["skip"] = "risk-cap"; st["shadow_open"].append(pos)
        else:
            st["open"].append(pos)
            if pos["mode"] == "pin":
                # 🧪 BE-study twin (owner, 2026-07-10): identical trade + a breakeven rung
                # at +0.75R, raced in the shadow book against its real sibling — gamma's
                # own forward answer to "does breakeven pay at the wall?"
                st["shadow_open"].append({**pos, "skip": "study-be75", "be": 0.75})

    st["last_ts"] = _iso(latest)                             # 5) advance the forward cursor
    for k in ("closed", "shadow_closed"):
        st[k] = st[k][-2000:]
    # gamma's OWN armed-order list lives here (its own tab), NOT in the shared Resting tab
    armed.sort(key=lambda o: abs((o.get("price") or 0) - o["entry"]) / (o.get("price") or 1))
    st["armed"] = [{"sym": o["sym"], "mode": o["mode"], "d": o["d"], "entry": o["entry"],
                    "stop": o["stop"], "tgt": o["tgt"], "wall": o.get("wall"),
                    "window": o.get("window"), "dte": o.get("dte"),
                    "price": o.get("price")} for o in armed[:200]]
    st["equity"] = round(st["capital"] + st["realized"])
    st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st["assumption"] = ASSUMPTION
    path.write_text(json.dumps(st, separators=(",", ":")))
    print(f"paper_gamma: equity {st['equity']} · open {len(st['open'])} · "
          f"closed {len(st['closed'])} · armed {len(armed)}")
    return armed
