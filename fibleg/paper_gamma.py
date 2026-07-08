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

from .models import Bar  # noqa: F401  (type clarity)

START_CAPITAL = 450_000.0
RISK_PCT, CAP_PCT = 0.0025, 0.015
COST_R = 0.05
TRIP_HALF_DD, TRIP_HALT_DD = 0.20, 0.30
STRETCH_ATR = 1.5          # limit rests this far from the wall
STOP_ATR = 1.0             # stop this far BEYOND the entry (away from the wall)
PIN_WINDOW, SQ_WINDOW = 75, 150   # 5m bars held before a time-exit (1 / 2 sessions)
ASSUMPTION = "dealers long calls, short puts"
# owner's profit-ladder (2026-07-08): once a PIN reaches the wall we don't just take 1.5R —
# we let it run and LOCK a rising floor as the peak (MFE) climbs, so runners keep more.
# (peak R reached, floor R locked); highest first. Below the wall, the hard −1R stop applies.
RATCHET = [(3.5, 3.0), (3.0, 2.0), (2.5, 1.8)]


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
    """Bar-by-bar exit. PINS use the owner's trailing profit-ladder: hard −1R stop until the
    wall is reached, then lock a rising floor (wall→1.5R, then 2.5R peak→1.8R, 3R→2R, 3.5R→3R)
    and ride until price retraces to that floor. SQUEEZE keeps the simple target/stop.
    Returns (r, exit_ts, reason, mfe) — or (None, None, None, mfe) if still open."""
    d, entry, stop, tgt = pos["d"], pos["entry"], pos["stop"], pos["tgt"]
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0, None, "bad-risk", 0.0
    wall_r = abs(tgt - entry) / risk          # the wall target in R (~1.5 for pins)
    is_pin = pos.get("mode") == "pin"
    ets = datetime.fromisoformat(pos["ts"])
    k = 0
    mfe = 0.0
    floor = None                              # locked profit floor (R), ratchets up with the peak
    for b in bars:
        if b.ts <= ets:
            continue
        k += 1
        fav = (b.high - entry) / risk if d == 1 else (entry - b.low) / risk        # best R this bar
        adverse = (b.low - entry) / risk if d == 1 else (entry - b.high) / risk    # worst R this bar
        if fav > mfe:
            mfe = fav
        if is_pin:
            # exit-check against the floor locked on PRIOR bars first (a floor set THIS bar only
            # takes effect next bar — else the wall-touch bar's own dip would exit us instantly)
            if floor is None:                                           # no profit locked → hard stop
                if (b.low <= stop) if d == 1 else (b.high >= stop):
                    return -1.0, b.ts, "stop", round(mfe, 3)
            elif adverse <= floor:                                      # retraced to the trailing floor
                return floor, b.ts, ("target" if abs(floor - wall_r) < 1e-6 else "trail"), round(mfe, 3)
            nf = next((lk for thr, lk in RATCHET if mfe >= thr), None)   # then ratchet up for next bar
            if nf is None and mfe >= wall_r:
                nf = wall_r                                              # reached the wall → lock it
            if nf is not None and (floor is None or nf > floor):
                floor = nf
        else:                                                           # SQUEEZE — simple target/stop
            if (b.low <= stop) if d == 1 else (b.high >= stop):
                return -1.0, b.ts, "stop", round(mfe, 3)
            if (b.high >= tgt) if d == 1 else (b.low <= tgt):
                return wall_r, b.ts, "target", round(mfe, 3)
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
            res = _walk(pos, bars) if bars else None
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


def run(base: dict, maps: dict, out_dir, chain_fn=None, quote_fn=None) -> list[dict]:
    """Advance the gamma book one scan. Returns the armed orders (for the Resting tab).
    chain_fn(symbol)->option chain and quote_fn([opt_syms])->{sym:{bid,ask,ltp}} let it book
    each trade at the REAL Fyers call/put price (fill=ask, exit=last bid); None = skip (yf)."""
    out_dir = Path(out_dir)
    path = out_dir / "paper_gamma.json"
    latest = None
    for b in base.values():
        if b and (latest is None or b[-1].ts > latest):
            latest = b[-1].ts
    if latest is None:
        return []
    st = _load(path, latest)

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
                res = _walk(pos, bars)
                if res is not None:
                    pos["potential_r"] = res[3]

    equity = st["capital"] + st["realized"]                  # 2) tripwire
    st["peak"] = max(st.get("peak", st["capital"]), equity)
    dd = 1.0 - equity / st["peak"] if st["peak"] > 0 else 0.0
    st["dd"] = round(dd, 4)
    if dd >= TRIP_HALT_DD and not st.get("halted"):
        st["halted"] = _iso(datetime.now(timezone.utc))

    last_ts = datetime.fromisoformat(st["last_ts"])          # 3) armed orders + fresh fills
    # market-runs switch: squeeze fires only when the BROAD market (Nifty) is itself in runs
    nifty = (maps or {}).get("^NSEI") or (maps or {}).get("NIFTY") or {}
    market_runs = nifty.get("regime") == "negative"
    st["market_regime"] = "runs" if market_runs else "sticky"
    armed, fills = [], []
    for sym, gmap in (maps or {}).items():
        bars = base.get(sym)
        if not bars or len(bars) < 60:
            continue
        atr = _atr(bars)
        px = bars[-1].close
        for o in _orders(sym, gmap, atr, px, market_runs):
            o = {**o, "sym": sym, "eng": "gamma", "price": round(px, 2)}
            armed.append(o)
            for b in bars:                                   # forward: only bars after last_ts
                if b.ts <= last_ts:
                    continue
                hit = (b.low <= o["entry"]) if o["d"] == 1 else (b.high >= o["entry"])
                if hit:
                    fills.append({**o, "ts": b.ts})
                    break

    fills.sort(key=lambda e: e["ts"])                        # 4) apply forced sizing gates
    for ev in fills:
        equity = st["capital"] + st["realized"]
        halved = st.get("dd", 0) >= TRIP_HALF_DD
        risk = equity * RISK_PCT * (0.5 if halved else 1.0)
        pos = {"sym": ev["sym"], "eng": "gamma", "mode": ev["mode"], "d": ev["d"],
               "entry": ev["entry"], "stop": ev["stop"], "tgt": ev["tgt"],
               "wall": ev["wall"], "window": ev["window"], "ts": _iso(ev["ts"]),
               "dte": ev.get("dte"), "mkt": st.get("market_regime"),   # market regime at entry
               "risk_rs": round(risk), "assumption": ASSUMPTION}
        if chain_fn:                                         # grab the REAL call/put + entry ask
            try:
                opt = _pick_option(chain_fn(ev["sym"]), ev["d"])
                if opt:
                    pos.update(opt)
            except Exception:  # noqa: BLE001
                pass
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
                    "dte": o.get("dte"), "price": o.get("price")} for o in armed[:200]]
    st["equity"] = round(st["capital"] + st["realized"])
    st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st["assumption"] = ASSUMPTION
    path.write_text(json.dumps(st, separators=(",", ":")))
    print(f"paper_gamma: equity {st['equity']} · open {len(st['open'])} · "
          f"closed {len(st['closed'])} · armed {len(armed)}")
    return armed
