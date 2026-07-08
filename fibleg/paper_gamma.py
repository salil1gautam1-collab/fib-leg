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


def _walk(pos: dict, bars):
    """Touch-based exit on 5m bars after entry (matches the other books)."""
    d, entry, stop, tgt = pos["d"], pos["entry"], pos["stop"], pos["tgt"]
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0, None, "bad-risk", 0.0
    ets = datetime.fromisoformat(pos["ts"])
    k = 0
    mfe = 0.0             # max favourable excursion in R — "how far it COULD have gone".
    booked = None         # we book at the wall (target); mfe keeps tracking PAST it to the
    for b in bars:        # window, so we can see if the fixed 1.5R is leaving money on the table
        if b.ts <= ets:
            continue
        k += 1
        fav = (b.high - entry) / risk if d == 1 else (entry - b.low) / risk
        if fav > mfe:
            mfe = fav
        if booked is None:
            if (b.low <= stop) if d == 1 else (b.high >= stop):
                booked = (-1.0, b.ts, "stop")             # a real stop ends the trade AND the potential
                break
            if (b.high >= tgt) if d == 1 else (b.low <= tgt):
                booked = (abs(tgt - entry) / risk, b.ts, "target")   # keep tracking mfe past the wall
            elif k >= pos["window"]:
                r = (b.close - entry) / risk if d == 1 else (entry - b.close) / risk
                booked = (r, b.ts, "time")
                break
        elif k >= pos["window"]:
            break
    if booked is None:
        return None, None, None, round(mfe, 3)   # still open — report the RUNNING peak so far
    r, xts, reason = booked
    return r, xts, reason, round(mfe, 3)


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


def run(base: dict, maps: dict, out_dir) -> list[dict]:
    """Advance the gamma book one scan. Returns the armed orders (for the Resting tab)."""
    out_dir = Path(out_dir)
    path = out_dir / "paper_gamma.json"
    latest = None
    for b in base.values():
        if b and (latest is None or b[-1].ts > latest):
            latest = b[-1].ts
    if latest is None:
        return []
    st = _load(path, latest)

    _manage(st, base)                                        # 1) advance open positions

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
