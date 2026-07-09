"""TEST 13b — does the reverse-fib LADDER help the SESSION-LENGTH combos too?
Owner ask 2026-07-09 ("can we do it for scalper/gem/pocket if the rule applies?").
Scope — only where there's climbing room inside the EXISTING time cap (no hold
changes; the 30-min 0.786 scalps are excluded — the clock exits before rung 1;
Pocket is excluded — its lock-at-B exit already beat every alternative tested):
  SCALP 1H@0.618  · stocks, LONGS only, cap 375 x 1m (~1 session)  [live combo]
  GEM   2H@0.886  · NIFTY 50 / NIFTY BANK, BOTH sides, cap 375 x 1m [live combo]
Variants on identical touches: BASE (nearest orig-fib rung >=2R, the live exit)
vs LADDER (bounce rungs .382/.5/.618/.786/.886 of the retracement leg; rung
touched = floor from next bar; rung broken = promotion; time-cap close floored).
READ-ONLY research — touches nothing live.
Run:  python swing_deep13b_books.py "fibleg/data/Stocks_data"
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
LEVELS = (0.5, 0.618, 0.786, 0.886)
YEARS = 11.3
RUNGS = {0.618: (0.5, 0.382, 0.236, 0.0), 0.886: (0.786, 0.618, 0.5)}
RUNG_F = (0.382, 0.5, 0.618, 0.786, 0.886)
# combo -> (cushion pct, cap 1m bars, longs_only) — mirrors the LIVE book settings
STK = {(60, 0.618): (.0031, 375, True)}
IDX = {(120, 0.886): (.0012, 375, False)}
CUSH886 = {60: .0042, 120: .0045}


def sim(b1, j, d, entry, stop, tgt, cap):
    risk = abs(entry - stop)
    if risk <= 0 or tgt is None or abs(tgt - entry) < 2 * risk:
        return None
    kend = min(j + cap, len(b1))
    for k in range(j, kend):
        b = b1[k]
        if (b.low <= stop) if d == 1 else (b.high >= stop):
            return -1.0, k
        if (b.high >= tgt) if d == 1 else (b.low <= tgt):
            return abs(tgt - entry) / risk, k
    b = b1[kend - 1]
    return ((b.close - entry) if d == 1 else (entry - b.close)) / risk, kend - 1


def sim_ladder(b1, j, d, entry, stop, top, cap):
    """Reverse-fib ladder, both directions. top = the leg extreme the bounce heads to."""
    risk = abs(entry - stop)
    fall = (top - entry) * d
    if risk <= 0 or fall <= 0:
        return None
    rungs = [entry + d * f * fall for f in RUNG_F]
    if abs(rungs[0] - entry) < 2 * risk:
        return None
    kend = min(j + cap, len(b1))
    floor = None
    for k in range(j, kend):
        b = b1[k]
        if floor is None:
            if (b.low <= stop) if d == 1 else (b.high >= stop):
                return -1.0, k
        elif (b.low <= floor) if d == 1 else (b.high >= floor):
            return abs(floor - entry) / risk, k
        for rp in rungs:
            hit = (b.high >= rp) if d == 1 else (b.low <= rp)
            better = floor is None or (rp > floor if d == 1 else rp < floor)
            if hit and better:
                floor = rp
    b = b1[kend - 1]
    r = ((b.close - entry) if d == 1 else (entry - b.close)) / risk
    if floor is not None:
        r = max(r, abs(floor - entry) / risk)
    return r, kend - 1


def run_symbol(tk, b1, combos, tag, events):
    m1_ts = [b.ts for b in b1]
    for (TF, L), (cushp, cap, longs_only) in combos.items():
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
                        lv = {LL: (extreme.price - LL * rng if d == 1
                                   else extreme.price + LL * rng) for LL in LEVELS}
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
                    for LL in LEVELS:
                        if LL in fib["consumed"]:
                            continue
                        level = fib["lv"][LL]
                        hit = bar.low <= level if d == 1 else bar.high >= level
                        if not hit:
                            continue
                        approach = (prevc > level and bar.open > level) if d == 1 \
                            else (prevc < level and bar.open < level)
                        fib["consumed"].add(LL)
                        if not (fib["active"] and approach and j + 400 < len(b1)):
                            continue
                        if LL != L or (longs_only and d != 1):
                            continue
                        ca = cushp * level
                        stop = level - ca if d == 1 else level + ca
                        tgt = None                          # BASE: nearest rung >= 2R
                        risk = abs(level - stop)
                        for t in RUNGS[L]:
                            rp = fib["e"] - t * fib["rng"] * d
                            if abs(rp - level) >= 2 * risk:
                                tgt = rp
                                break
                        base = sim(b1, j + 1, d, level, stop, tgt, cap)
                        lad = sim_ladder(b1, j + 1, d, level, stop, fib["e"], cap)
                        events.append({"tk": tk, "tag": tag, "tf": TF, "lvl": L,
                                       "d": d, "y": bar.ts.year, "ts": bar.ts,
                                       "BASE": (None if base is None else
                                                {"r": base[0], "xts": m1_ts[base[1]]}),
                                       "LADDER": (None if lad is None else
                                                  {"r": lad[0], "xts": m1_ts[lad[1]]})})
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


events, t0 = [], time.time()
stk_tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
idx_tickers = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" in s.upper()]
for ti, tk in enumerate(stk_tickers):
    try:
        b1 = feeds.csv_dir_series(DIR, tk)
    except Exception as e:  # noqa: BLE001
        print("skip", tk, e, flush=True); continue
    run_symbol(tk, b1, STK, "SCALP-0618", events)
    if (ti + 1) % 25 == 0 or ti + 1 == len(stk_tickers):
        print(f"  stk [{ti+1}/{len(stk_tickers)}] {len(events)} events "
              f"({time.time()-t0:.0f}s)", flush=True)
for tk in idx_tickers:
    try:
        b1 = feeds.csv_dir_series(DIR, tk)
    except Exception as e:  # noqa: BLE001
        print("skip", tk, e, flush=True); continue
    run_symbol(tk, b1, IDX, "GEM-0886", events)
    print(f"  idx {tk}: {len(events)} events total", flush=True)

print(f"\n===== {len(events)} touch events =====")


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


for tag in ("SCALP-0618", "GEM-0886"):
    pool = [e for e in events if e["tag"] == tag]
    print(f"\n===== {tag} =====")
    for vn in ("BASE", "LADDER"):
        taken = one_per_stock(pool, vn)
        n = len(taken)
        wins = sum(1 for e in taken if e[vn]["r"] > 0)
        tot5 = sum(e[vn]["r"] for e in taken) - 0.05 * n
        tot10 = sum(e[vn]["r"] for e in taken) - 0.10 * n
        byy = defaultdict(float)
        for e in taken:
            byy[e["y"]] += e[vn]["r"] - 0.05
        neg = sum(1 for y in byy if byy[y] < 0)
        eq = peak = dd = 0.0
        for e in sorted(taken, key=lambda e: e[vn]["xts"]):
            eq += e[vn]["r"] - 0.05
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        print(f"  {vn}: n={n} · win {100*wins/max(n,1):.0f}% · net@.05 {tot5:+.0f}R "
              f"({tot5/YEARS:+.0f}R/yr) · net@.10 {tot10:+.0f}R · maxDD {dd:.0f}R · "
              f"losing yrs {neg}")
    matched = [e for e in pool if e["BASE"] is not None and e["LADDER"] is not None]
    if matched:
        for vn in ("BASE", "LADDER"):
            tot = sum(e[vn]["r"] - 0.05 for e in matched)
            print(f"    matched({len(matched)}) {vn}: {tot:+.0f}R "
                  f"({tot/len(matched):+.3f}R/touch)")
