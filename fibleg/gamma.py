"""Dealer gamma map from a Fyers option chain: the zero-gamma FLIP level, the biggest
gamma WALLS, and the regime (positive = "bowl / pin", negative = "hill / squeeze").

CORE ASSUMPTION (stamped on the output, per owner ruling): dealers are LONG calls and
SHORT puts. So per-strike dealer gamma = +gamma*callOI - gamma*putOI. If the gamma engine
makes no money in forward paper trading, THIS assumption (or gamma itself) is what failed.

There is no historical option OI, so this is computed live and used FORWARD only — the
🎲 Gamma paper engine's own equity curve is how we learn whether gamma pays.
"""
from __future__ import annotations

import math

_SQRT2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _bs_gamma(S: float, K: float, T: float, sigma: float) -> float:
    """Black-Scholes gamma (r=0). Peaks at-the-money; identical for a call and a put."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    st = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / st
    return _norm_pdf(d1) / (S * st)


def _bs_price(S: float, K: float, T: float, sigma: float, is_call: bool) -> float:
    if sigma <= 0 or T <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    st = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / st
    d2 = d1 - st
    nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2)))
    nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2)))
    return (S * nd1 - K * nd2) if is_call else (K * (1 - nd2) - S * (1 - nd1))


def _atm_iv(chain: dict, T: float) -> float:
    """One at-the-money implied vol, backed out of the ATM option LTP by bisection.
    Falls back to 0.25 if it can't. A single sigma is enough to SHAPE gamma across
    strikes for finding walls + the flip (Fyers' chain gives no IV of its own)."""
    S = chain["spot"]
    strikes = chain["strikes"]
    atm = min(strikes, key=lambda k: abs(k - S))
    rec = strikes[atm]
    is_call = bool(rec.get("ce_ltp"))
    price = rec.get("ce_ltp") if is_call else rec.get("pe_ltp")
    if not price or price <= 0 or T <= 0:
        return 0.25
    lo, hi = 0.01, 3.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if _bs_price(S, atm, T, mid, is_call) > price:
            hi = mid
        else:
            lo = mid
    iv = 0.5 * (lo + hi)
    return iv if 0.02 < iv < 2.5 else 0.25


def build_map(chain: dict, iv_hint: float | None = None) -> dict | None:
    """chain from fyers_feed.option_chain -> gamma map, or None if unusable.

    Returns {spot, flip, regime, net_gex, sigma, expiry_days, walls[], assumption}.
    regime "positive" = above flip = bowl/pin world; "negative" = below flip = hill/squeeze.
    """
    if not chain or not chain.get("strikes") or not chain.get("spot"):
        return None
    S = float(chain["spot"])
    T = (chain.get("expiry_days") or 7) / 365.0
    sigma = iv_hint if (iv_hint and iv_hint > 0) else _atm_iv(chain, T)
    strikes = chain["strikes"]
    Ks = sorted(strikes)
    if len(Ks) < 5:
        return None

    # per-strike hedging magnitude (gamma * total OI) at the current spot -> the WALLS
    walls = []
    for K in Ks:
        rec = strikes[K]
        coi, poi = rec.get("ce_oi", 0.0), rec.get("pe_oi", 0.0)
        g = _bs_gamma(S, K, T, sigma)
        walls.append({"strike": K, "strength": g * (coi + poi),
                      "call_oi": coi, "put_oi": poi})

    # zero-gamma FLIP: spot where net dealer gamma (long calls - short puts) crosses 0
    def net_gex(spot: float) -> float:
        tot = 0.0
        for K in Ks:
            rec = strikes[K]
            g = _bs_gamma(spot, K, T, sigma)
            tot += g * rec.get("ce_oi", 0.0) - g * rec.get("pe_oi", 0.0)
        return tot

    lo, hi, steps = Ks[0], Ks[-1], 60
    flip, prev_s, prev_v = None, lo, net_gex(lo)
    for i in range(1, steps + 1):
        s = lo + (hi - lo) * i / steps
        v = net_gex(s)
        if prev_v == 0.0 or (prev_v < 0) != (v < 0):
            flip = s if v == prev_v else prev_s + (s - prev_s) * (0 - prev_v) / (v - prev_v)
            break
        prev_s, prev_v = s, v

    net_now = net_gex(S)
    walls.sort(key=lambda w: -w["strength"])
    top = [{"strike": w["strike"], "strength": round(w["strength"], 4),
            "call_oi": int(w["call_oi"]), "put_oi": int(w["put_oi"])}
           for w in walls[:5] if w["strength"] > 0]
    return {
        "spot": round(S, 2),
        "flip": round(flip, 2) if flip is not None else None,
        "regime": "positive" if net_now >= 0 else "negative",
        "net_gex": round(net_now, 4),
        "sigma": round(sigma, 4),
        "expiry_days": chain.get("expiry_days"),
        "walls": top,
        "assumption": "dealers long calls, short puts",
    }
