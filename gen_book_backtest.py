"""Generates docs/backtest_book.json — the Book's yearly record, per engine +
combined, vs Pocket alone (as-was and longs-only), for the app's Backtest tab.

Engines simulated at DEPLOYED config (2026-07-05 rulings):
  Scalper: stocks LONGS ONLY (1H@0.618 375m · 1H/2H@0.786 30m) + Gem (index
           2H@0.886 BOTH sides, 375m) — touch entries, tight stops, level targets,
           overnight allowed, one position per stock
  Defense: 1H@0.786->top · 1H@0.886->.618 · 2H@0.886->.618, longs only, 10d holds
Pocket lines come from docs/backtest_120.json (⭐ best-context, lock-at-B):
  as-was (longs+shorts) and longs-only. Level books netted at 0.05R costs.
Run:  python gen_book_backtest.py "fibleg/data/Stocks_data"   (~20 min)"""
import bisect, json, sys, time
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
RUNGS = {0.618: (0.5, 0.382, 0.236, 0.0), 0.786: (0.618, 0.5, 0.382),
         0.886: (0.786, 0.618, 0.5)}
CUSH_STK = {(60, 0.618): .0031, (60, 0.786): .0039, (60, 0.886): .0042,
            (120, 0.618): .0031, (120, 0.786): .0041, (120, 0.886): .0045}
CUSH_IDX = {(120, 0.886): .0025}
CUSH886 = {60: .0042, 120: .0045}
SCALP_STK = {(60, 0.618): 375, (60, 0.786): 30, (120, 0.786): 30}   # longs only
SCALP_IDX = {(120, 0.886): 375}                                     # Gem, both sides
DEEP_STK = {(60, 0.786): (0.0, 3750), (60, 0.886): (0.618, 3750),
            (120, 0.886): (0.618, 3750)}                            # longs only
COST = 0.05


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


tickers = list(feeds.csv_dir_symbols(DIR))
fills, t0 = [], time.time()
for ti, tk in enumerate(tickers):
    try:
        b1 = feeds.csv_dir_series(DIR, tk)
    except Exception as e:  # noqa: BLE001
        print("skip", tk, e, flush=True); continue
    is_idx = "NIFTY" in tk.upper()
    cush = CUSH_IDX if is_idx else CUSH_STK
    m1_ts = [b.ts for b in b1]
    for TF in TFS:
        if is_idx and TF == 60:
            continue
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
                        c886 = CUSH886[TF] if not is_idx else .0025
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
                        if (TF, L) not in cush:
                            continue
                        ca = cush[(TF, L)] * level
                        stop = level - ca if d == 1 else level + ca
                        risk = abs(level - stop)
                        if is_idx:
                            if (TF, L) in SCALP_IDX:      # the Gem — both sides
                                tgt = None
                                for t in RUNGS[L]:
                                    rp = fib["e"] - t * fib["rng"] * d
                                    if abs(rp - level) >= 2 * risk:
                                        tgt = rp
                                        break
                                o = sim(b1, j + 1, d, level, stop, tgt, SCALP_IDX[(TF, L)])
                                if o is not None:
                                    fills.append({"tk": tk, "book": "SCALP", "y": bar.ts.year,
                                                  "ts": bar.ts, "xts": b1[o[1]].ts, "r": o[0]})
                            continue
                        if (TF, L) in SCALP_STK and d == 1:
                            tgt = None
                            for t in RUNGS[L]:
                                rp = fib["e"] - t * fib["rng"] * d
                                if abs(rp - level) >= 2 * risk:
                                    tgt = rp
                                    break
                            o = sim(b1, j + 1, d, level, stop, tgt, SCALP_STK[(TF, L)])
                            if o is not None:
                                fills.append({"tk": tk, "book": "SCALP", "y": bar.ts.year,
                                              "ts": bar.ts, "xts": b1[o[1]].ts, "r": o[0]})
                        if (TF, L) in DEEP_STK and d == 1:
                            tl, cap = DEEP_STK[(TF, L)]
                            tgt = fib["e"] - tl * fib["rng"] * d
                            o = sim(b1, j + 1, d, level, stop, tgt, cap)
                            if o is not None:
                                fills.append({"tk": tk, "book": "DEEP", "y": bar.ts.year,
                                              "ts": bar.ts, "xts": b1[o[1]].ts, "r": o[0]})
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
        print(f"  [{ti+1}/{len(tickers)}] {len(fills)} fills ({time.time()-t0:.0f}s)",
              flush=True)


def one_per_stock(pool):
    pool = sorted(pool, key=lambda f: f["ts"])
    open_until, taken = {}, []
    for f in pool:
        ou = open_until.get(f["tk"])
        if ou is not None and f["ts"] < ou:
            continue
        taken.append(f)
        open_until[f["tk"]] = f["xts"]
    return taken


def yearly(pool):
    byy = defaultdict(float)
    for f in pool:
        byy[f["y"]] += f["r"] - COST
    return {str(y): round(v, 1) for y, v in sorted(byy.items())}


scalp = yearly(one_per_stock([f for f in fills if f["book"] == "SCALP"]))
deep = yearly(one_per_stock([f for f in fills if f["book"] == "DEEP"]))

# Pocket lines from the published 2H backtest (⭐ best-context, lock-at-B)
bt = json.load(open("docs/backtest_120.json"))
pk_old, pk_long = defaultdict(float), defaultdict(float)
for t in bt["exits"]["lockb"]:
    if not t["f"] & 1:
        continue
    pk_old[t["y"]] += t["r"]
    if t["sd"] == "L":
        pk_long[t["y"]] += t["r"]
pocket_old = {str(y): round(v, 1) for y, v in sorted(pk_old.items())}
pocket = {str(y): round(v, 1) for y, v in sorted(pk_long.items())}

years = sorted(set(pocket) | set(scalp) | set(deep))
combo = {y: round(pocket.get(y, 0) + scalp.get(y, 0) + deep.get(y, 0), 1) for y in years}

payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "note": ("Yearly net R. Pocket = ⭐ best-context 2H lock-at-B (engine R, "
                    "pre-cost). Scalper/Defense = deployed config, 0.05R costs, one "
                    "position per stock. Book = Pocket-longs + Scalper + Defense."),
           "years": years,
           "engines": {"pocket_old": pocket_old, "pocket": pocket,
                       "scalper": scalp, "defense": deep, "book": combo}}
open("docs/backtest_book.json", "w").write(json.dumps(payload, separators=(",", ":")))
print("\nwrote docs/backtest_book.json")
for y in years:
    print(f"  {y}: old {pocket_old.get(y,0):+7.1f} · pocket {pocket.get(y,0):+7.1f} · "
          f"scalp {scalp.get(y,0):+7.1f} · defense {deep.get(y,0):+7.1f} · "
          f"BOOK {combo.get(y,0):+7.1f}")
