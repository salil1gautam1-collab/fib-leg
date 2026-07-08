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

START_CAPITAL = 2_000_000.0   # sized so 0.25% risk (₹5,000) fits ≥1 REAL lot of most liquid names
# existing ledgers started at ₹4.5L get a one-time recorded top-up to this (owner, 2026-07-08:
# "if you need to increase the capital allocated, do that") — real lots wouldn't fit in ₹1,125.
TOPUP_TO = START_CAPITAL
RISK_PCT, CAP_PCT = 0.0025, 0.015
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


def _orders(sym: str, gmap: dict, atr: float, px: float, market_runs: bool = False) -> list[dict]:
    """The armed gamma orders for one underlying at the current price. SQUEEZE (runs) fires
    ONLY when market_runs is True — i.e. the broad market (Nifty) is itself in runs. In a calm
    market a single stock below its flip just gives false breakouts (0/11 on the first calm
    day), so we sit runs out until the whole market is genuinely in momentum mode."""
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
    elif market_runs:                                         # SQUEEZE / hill — ONLY on a market-runs day
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
            return floor, b.ts, ("target" if abs(floor - wall_r) < 1e-6 else "trail"), round(mfe, 3)
        nf = next((lk for thr, lk in RATCHET if mfe >= thr), None)   # then ratchet up for next bar
        if mfe >= PROP_FROM:                                        # monster runner → floor rides the peak
            nf = max(nf or 0.0, round(mfe * PROP_KEEP, 3))
        if mfe >= wall_r:                                           # reached the target → lock ≥ its R
            nf = max(nf, wall_r) if nf is not None else wall_r      # (a far squeeze target isn't under-locked)
        if nf is not None and (floor is None or nf > floor):
            floor = nf
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
    if st["capital"] < TOPUP_TO:                             # one-time recorded capital top-up
        st.setdefault("capital_adds", []).append(
            {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "amount": round(TOPUP_TO - st["capital"]),
             "why": "real lot sizes need a bigger base (owner call 2026-07-08)"})
        st["capital"] = TOPUP_TO

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
    # 3a) FILLS come from the PREVIOUS scan's armed orders — true resting-order semantics,
    # exactly what a real broker would be holding when these bars printed. (Filling against
    # orders re-pegged to the CURRENT price is adverse selection: a break that STICKS moves
    # the re-pegged order away and only the fakeouts would ever fill.)
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
        for o in _orders(sym, gmap, atr, px, market_runs):
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
        open_risk = sum(p["risk_rs"] for p in st["open"])
        if st.get("halted"):
            pos["skip"] = "tripwire-halt"; st["shadow_open"].append(pos)
        elif any(p["sym"] == ev["sym"] for p in st["open"]):
            pos["skip"] = "stock-busy"; st["shadow_open"].append(pos)
        elif open_risk + pos["risk_rs"] > equity * CAP_PCT:
            pos["skip"] = "risk-cap"; st["shadow_open"].append(pos)
        else:
            st["open"].append(pos)

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
