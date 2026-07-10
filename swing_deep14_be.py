"""TEST 13 — REVERSE-FIB EXITS for the DEEP (Defense) trio, owner ask 2026-07-09:
"for deep 786/886 entries, draw a reverse fib over the retracement leg (top -> entry)
and exit at its .5/.618 — test which level was most successful and what change it
would have made to our results, for just the defense type of trades."

Same fills as test 12d's DEEP book (fib lifecycle, resting-order touches, tight
test-8 cushions, 10-session cap, LONGS only, one position per stock). Four exit
variants simulated on the SAME touch events:
  BASE   = the validated exits: 1H@0.786 -> leg top · 1H/2H@0.886 -> .618 (orig fib)
  REV38  = entry + 0.382 x (top - entry)   (38.2% bounce of the fall, fixed target)
  REV50  = entry + 0.500 x (top - entry)   (fixed)
  REV618 = entry + 0.618 x (top - entry)   (fixed)
  LADDER = the owner's ACTUAL idea (clarified 2026-07-09): rungs at bounce .382/.5/
           .618/.786/.886 — a rung TOUCHED is locked as the floor; a rung BROKEN is a
           promotion to the next; through .886 "it will fly" (ride to the 10-session
           cap, floor at the .886 rung). Exit = retrace to the highest locked rung,
           or -1R before the first rung. Floor set on a bar takes effect next bar.
The >=2R structural gate applies per variant (a closer target can disqualify a
trade the doctrine would refuse; the ladder gates on its FIRST rung). A MATCHED
subset (fills every variant accepts) is reported for per-fill economics.
READ-ONLY research — touches nothing live.
Run:  python swing_deep14_be.py "fibleg/data/Stocks_data"
"""
import bisect, sys, time
from collections import defaultdict

from fibleg.data import feeds
from fibleg.indicators.atr import AtrStreamer
from fibleg.models import PivotType
from fibleg.strategy.book_impulse import BookImpulse
from fibleg.strategy.pivots import ZigZag

DIR = sys.argv[1] if len(sys.argv) > 1 else "fibleg/data/Stocks_data"
MIN_LEG_ATR = 5.0
TFS = (60, 120)
LEVELS = (0.5, 0.618, 0.786, 0.886)
YEARS = 11.3
CUSH = {(60, 0.786): .0039, (60, 0.886): .0042, (120, 0.886): .0045}
CUSH886 = {60: .0042, 120: .0045}
DEEP = {(60, 0.786): 0.0, (60, 0.886): 0.618, (120, 0.886): 0.618}   # combo -> BASE target level
CAP = 3750                                                            # ~10 sessions of 1m bars
VARIANTS = ("LADDER", "BE50", "BE75", "BE100")
BE_THR = {"BE50": 0.5, "BE75": 0.75, "BE100": 1.0}
RUNG_F = (0.382, 0.5, 0.618, 0.786, 0.886)


def sim(b1, j, entry, stop, tgt, cap):
    """Walk 1m bars from j (LONGS only). Returns (r, exit_index) or None (>=2R gate)."""
    risk = entry - stop
    if risk <= 0 or tgt is None or (tgt - entry) < 2 * risk:
        return None
    kend = min(j + cap, len(b1))
    for k in range(j, kend):
        b = b1[k]
        if b.low <= stop:
            return -1.0, k
        if b.high >= tgt:
            return (tgt - entry) / risk, k
    b = b1[kend - 1]
    return (b.close - entry) / risk, kend - 1


def sim_ladder(b1, j, entry, stop, top, cap, be_r=None):
    """Owner's reverse-fib LADDER (LONGS): floor = highest bounce rung touched so far;
    exit on a retrace to the floor (-1R before the first rung); rungs .382..886 of the
    fall; past .886 ride to the cap with the .886 rung as the floor. A rung touched on
    a bar becomes the floor only from the NEXT bar (the touch bar's own dip must not
    exit us instantly)."""
    risk = entry - stop
    fall = top - entry
    if risk <= 0 or fall <= 0:
        return None
    rungs = [entry + f * fall for f in RUNG_F]
    if (rungs[0] - entry) < 2 * risk:            # doctrine gate on the first rung
        return None
    be_price = entry + be_r * risk if be_r is not None else None
    kend = min(j + cap, len(b1))
    floor = None
    for k in range(j, kend):
        b = b1[k]
        if floor is None:
            if b.low <= stop:
                return -1.0, k
        elif b.low <= floor:
            return (floor - entry) / risk, k
        for rp in rungs:                          # promote AFTER the exit check
            if b.high >= rp and (floor is None or rp > floor):
                floor = rp
        if be_price is not None and floor is None and b.high >= be_price:
            floor = entry                          # breakeven rung: can no longer lose
    b = b1[kend - 1]
    r = (b.close - entry) / risk
    if floor is not None:
        r = max(r, (floor - entry) / risk)
    return r, kend - 1


tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
events, t0 = [], time.time()
for ti, tk in enumerate(tickers):
    try:
        b1 = feeds.csv_dir_series(DIR, tk)
    except Exception as e:  # noqa: BLE001
        print("skip", tk, e, flush=True); continue
    m1_ts = [b.ts for b in b1]
    for TF in TFS:
        b2 = feeds.resample(b1, TF)
        if len(b2) < 60:
            continue
        b2h = feeds.resample(b1, TF * 2)
        bih = BookImpulse(0.382, 0.786, True, re_anchor_ratio=0.618)
        zzh, atrh, pivh, kh = ZigZag(0.382, 1.5), AtrStreamer(), [], 0
        bi = BookImpulse(0.382, 0.786, True, re_anchor_ratio=0.618,
                         htf_keep=lambda: bih.dir == bi.dir and not bih.locked)
        zz, atr = ZigZag(0.382, 1.5), AtrStreamer()
        fibs, seen = [], set()
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
                        c886 = CUSH886[TF]
                        die = lv[0.886] * (1 - c886) if d == 1 else lv[0.886] * (1 + c886)
                        fibs.append({"d": d, "e": extreme.price, "lv": lv, "die": die,
                                     "rng": rng, "consumed": set(), "active": not fibs,
                                     "born": prev.ts})
            if not fibs:
                continue
            j0 = bisect.bisect_right(m1_ts, prev.ts)
            j1 = bisect.bisect_right(m1_ts, b2[i2].ts)
            for j in range(j0, j1):
                bar = b1[j]
                prevc = b1[j - 1].close if j > 0 else bar.open
                is5c = (j + 1 == len(b1)) or (m1_ts[j + 1].minute % 5 == 0) \
                    or (m1_ts[j + 1].date() != m1_ts[j].date())
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
                        if not (fib["active"] and approach and j + 400 < len(b1)):
                            continue
                        if (TF, L) not in DEEP or d != 1:          # DEEP trio, longs only
                            continue
                        ca = CUSH[(TF, L)] * level
                        stop = level - ca
                        # the four exit variants on the SAME touch
                        res = {}
                        res["LADDER"] = sim_ladder(b1, j + 1, level, stop, fib["e"], CAP)
                        for vn, thr in BE_THR.items():
                            res[vn] = sim_ladder(b1, j + 1, level, stop, fib["e"], CAP, be_r=thr)
                        events.append({"tk": tk, "tf": TF, "lvl": L, "y": bar.ts.year,
                                       "ts": bar.ts,
                                       **{vn: (None if r is None else
                                               {"r": r[0], "xts": m1_ts[r[1]]})
                                          for vn, r in res.items()}})
                    if not is5c:
                        continue
                    dead = (bar.close < fib["die"]) if d == 1 else (bar.close > fib["die"])
                    if not dead:
                        dead = (bar.close > fib["e"]) if d == 1 else (bar.close < fib["e"])
                    if dead:
                        was = fib["active"]
                        fibs.remove(fib)
                        if was and fibs:
                            max(fibs, key=lambda f: f["born"])["active"] = True
    if (ti + 1) % 25 == 0 or ti + 1 == len(tickers):
        print(f"  [{ti+1}/{len(tickers)}] {len(events)} deep touch events "
              f"({time.time()-t0:.0f}s)", flush=True)

print(f"\n===== {len(events)} deep touch events =====")


def one_per_stock(pool, vn):
    pool = sorted((e for e in pool if e[vn] is not None), key=lambda e: e["ts"])
    open_until, taken = {}, []
    for e in pool:
        ou = open_until.get(e["tk"])
        if ou is not None and e["ts"] < ou:
            continue
        taken.append(e)
        open_until[e["tk"]] = e[vn]["xts"]
    return taken


def report(vn):
    taken = one_per_stock(events, vn)
    byy = defaultdict(lambda: [0, 0.0])
    wins = 0
    for e in taken:
        r = e[vn]["r"]
        byy[e["y"]][0] += 1
        byy[e["y"]][1] += r - 0.05
        wins += r > 0
    tot5 = sum(e[vn]["r"] for e in taken) - 0.05 * len(taken)
    tot10 = sum(e[vn]["r"] for e in taken) - 0.10 * len(taken)
    eq = peak = dd = 0.0
    neg_years = sum(1 for y in byy if byy[y][1] < 0)
    for e in sorted(taken, key=lambda e: e[vn]["xts"]):
        eq += e[vn]["r"] - 0.05
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    n = len(taken)
    print(f"\n--- {vn} · {n} trades · win {100*wins/max(n,1):.0f}% ---")
    for y in sorted(byy):
        c, a = byy[y]
        print(f"  {y}: n={c:5d} · net@.05 {a:+7.1f}R")
    print(f"  TOTAL net@.05 {tot5:+.0f}R ({tot5/YEARS:+.0f}R/yr) · net@.10 {tot10:+.0f}R "
          f"({tot10/YEARS:+.0f}R/yr) · maxDD {dd:.0f}R · losing yrs {neg_years}")
    # per-combo split
    bycombo = defaultdict(lambda: [0, 0.0])
    for e in taken:
        k = f"{e['tf']//60}H@{e['lvl']}"
        bycombo[k][0] += 1
        bycombo[k][1] += e[vn]["r"] - 0.05
    for k in sorted(bycombo):
        c, a = bycombo[k]
        print(f"    {k}: n={c} · net@.05 {a:+.0f}R ({a/max(c,1):+.3f}R/tr)")
    return tot5


for vn in VARIANTS:
    report(vn)

# matched subset: touches where EVERY variant passes its >=2R gate — per-fill economics
matched = [e for e in events if all(e[vn] is not None for vn in VARIANTS)]
print(f"\n--- MATCHED subset · {len(matched)} touches accepted by all variants ---")
for vn in VARIANTS:
    tot = sum(e[vn]["r"] - 0.05 for e in matched)
    wins = sum(1 for e in matched if e[vn]["r"] > 0)
    print(f"  {vn}: net@.05 {tot:+.0f}R · {tot/max(len(matched),1):+.3f}R/touch · "
          f"win {100*wins/max(len(matched),1):.0f}%")
