"""Generates docs/backtest_book.json — the Book's yearly record, per engine +
combined, vs Pocket alone (as-was and longs-only), for the app's Backtest tab.

Engines simulated at DEPLOYED config (updated 2026-07-16 to the live rules):
  Scalper: stocks LONGS ONLY — 1H@0.618 with the REVERSE-FIB LADDER exit (test 13b)
           AND the ADAPTIVE WEATHER GATE (test 16b: 252d trailing window, ±0.05R/trade
           hysteresis, walk-forward causal, starts OFF) · 1H/2H@0.786 30m struct
           (ladder excluded — clock) + Gem (index 2H@0.886 BOTH sides, struct —
           ladder rejected, test 13b)
  Defense: 1H@0.786 · 1H/2H@0.886, longs only, 10d holds, LADDER exits (test 13)
Weather (0.618 gate) mirrors TEST 16: runs-day = UPT/DNT regime or >=1% day move or
range >=1.8x prior-10d or (VIX>20d-avg and >=0.6% move); vix-high = VIX > 20d avg AND
> 15 (the v142 floor). Needs yfinance daily ^NSEI/^INDIAVIX. NOTE: gate applies after
the one-per-stock lock (slightly conservative — live, a benched fill frees its slot).
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


RUNG_F = (0.382, 0.5, 0.618, 0.786, 0.886)


def sim_ladder(b1, j, d, entry, stop, top, cap):
    """Reverse-fib ladder (tests 13/13b): rung touched = floor from next bar."""
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


def build_weather():
    """Daily hostile-weather map, identical to TEST 16's classifier."""
    import yfinance as yf
    from fibleg.indicators.trend import AdxStreamer
    from fibleg.models import Bar
    wx = {}
    nd = yf.download("^NSEI", start="2014-06-01", interval="1d",
                     progress=False, auto_adjust=False)
    vd = yf.download("^INDIAVIX", start="2014-06-01", interval="1d",
                     progress=False, auto_adjust=False)
    n = {c[0] if isinstance(c, tuple) else c: nd[c].squeeze() for c in nd.columns}
    vals = list(vd["Close"].squeeze().dropna().items())
    vix, vavg = {}, {}
    for i, (ts, c) in enumerate(vals):
        prior = [float(x[1]) for x in vals[max(0, i - 19):i + 1]]
        vix[ts.date()] = float(c)
        vavg[ts.date()] = sum(prior) / len(prior)
    adx = AdxStreamer(14)
    closes, ranges = [], []
    for i, ts in enumerate(n["Close"].dropna().index):
        o, h, l, c = (float(n[k][ts]) for k in ("Open", "High", "Low", "Close"))
        av = adx.update(Bar(ts.to_pydatetime(), o, h, l, c))
        closes.append(c)
        day = ts.date()
        move = abs(c - o) / o if o else 0.0
        rng = h - l
        reg = None
        if i >= 50:
            up = c > sum(closes[i - 50:i]) / 50
            reg = ("UPT" if up else "DNT") if av >= 25 else ("SDW" if av < 20 else "WHP")
        vhi_old = day in vix and vix[day] > vavg[day]
        prior10 = ranges[-10:]
        rblow = (len(prior10) >= 3 and sum(prior10) / len(prior10) > 0
                 and rng >= 1.8 * (sum(prior10) / len(prior10)))
        ranges.append(rng)
        vx = vix.get(day)
        wx[day] = ((reg in ("UPT", "DNT")) or move >= 0.010 or rblow
                   or (vhi_old and move >= 0.006)
                   or (vhi_old and vx is not None and vx > 15.0))
    return wx


def adaptive_0618(pool, wx):
    """TEST 16b walk-forward gate on the 0.618 fills: bench hostile fills while
    their trailing 252d record < -0.05R/trade, re-admit above +0.05 (causal)."""
    from datetime import timedelta
    for f in pool:
        f["hostile"] = f.get("c") == "0618" and wx.get(f["ts"].date(), False)
    host = sorted([f for f in pool if f["hostile"]], key=lambda f: f["xts"])
    closed, hi, gate_on, benched = [], 0, False, set()
    for f in sorted(pool, key=lambda f: f["ts"]):
        while hi < len(host) and host[hi]["xts"] < f["ts"]:
            closed.append((host[hi]["xts"], host[hi]["r"] - COST))
            hi += 1
        if not f["hostile"]:
            continue
        cutoff = f["ts"] - timedelta(days=252)     # the DEPLOYED scalper window
        recent = [r for xts, r in closed if xts >= cutoff]
        if len(recent) >= 30:
            rpt = sum(recent) / len(recent)
            if not gate_on and rpt < -0.05:
                gate_on = True
            elif gate_on and rpt > 0.05:
                gate_on = False
        if gate_on:
            benched.add(id(f))
    return [f for f in pool if id(f) not in benched]


from pathlib import Path as _P
_CACHE = _P(r"C:\Users\Admin\AppData\Local\Temp\claude\C--Salil-Claude"
            r"\5effca38-4699-441b-8bbd-0a4f831411d9\scratchpad\book_fills_cache.json")

tickers = list(feeds.csv_dir_symbols(DIR))
fills, t0 = [], time.time()
for ti, tk in enumerate([] if _CACHE.exists() else tickers):
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
                            if (TF, L) == (60, 0.618):    # LIVE exit: the ladder
                                o = sim_ladder(b1, j + 1, d, level, stop, fib["e"],
                                               SCALP_STK[(TF, L)])
                                if o is not None:
                                    fills.append({"tk": tk, "book": "SCALP", "c": "0618",
                                                  "y": bar.ts.year, "ts": bar.ts,
                                                  "xts": b1[o[1]].ts, "r": o[0]})
                            else:                          # 30-min 0.786: clock beats ladder
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
                            o = sim_ladder(b1, j + 1, d, level, stop, fib["e"], cap)
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


from datetime import datetime as _dt
if _CACHE.exists() and not fills:
    for f in json.loads(_CACHE.read_text()):
        f["ts"] = _dt.fromisoformat(f["ts"]); f["xts"] = _dt.fromisoformat(f["xts"])
        fills.append(f)
    print(f"loaded {len(fills)} fills from cache", flush=True)
elif fills and not _CACHE.exists():
    _CACHE.write_text(json.dumps([{**f, "ts": f["ts"].isoformat(),
                                   "xts": f["xts"].isoformat()} for f in fills]))
    print("fill cache written", flush=True)


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


wx = build_weather()
scalp_pool = one_per_stock([f for f in fills if f["book"] == "SCALP"])
scalp_ungated = yearly(scalp_pool)
# BENCH_0618 (v148, 2026-07-17): the 0.618 is shadow-only pending re-audition, so the
# TRADE-BOOK scalper line = 0.786 scalps + Gem only; the benched 0.618 (canonical,
# adaptive-gated) is kept as its own reference line.
# lock computed on the 0618-free pool: live, a benched fill goes to shadow and
# FREES its one-per-stock slot for the 0.786s
# GEM SPLIT (owner ask 2026-08-04: per-engine deployed math, Gem visible alone)
scalp_live_pool = one_per_stock([f for f in fills if f["book"] == "SCALP"
                                 and f.get("c") != "0618"
                                 and "NIFTY" not in f["tk"].upper()])
gem_pool = one_per_stock([f for f in fills if f["book"] == "SCALP"
                          and "NIFTY" in f["tk"].upper()])
scalp = yearly(scalp_live_pool)
gem = yearly(gem_pool)
scalp_benched_0618 = yearly(adaptive_0618(scalp_pool, wx))
for y in list(scalp_benched_0618):
    scalp_benched_0618[y] = round(scalp_benched_0618[y] - scalp.get(y, 0), 1)
deep_pool = one_per_stock([f for f in fills if f["book"] == "DEEP"])
deep = yearly(deep_pool)
# what-if line (owner ask 2026-07-16, test 17 follow-up): Defense under a fixed
# quiet-sideways-only weather gate — printed for comparison, NOT published/deployed
deep_quiet = yearly([f for f in deep_pool if not wx.get(f["ts"].date(), False)])


def adaptive_deep(pool, wxm, on_thr, off_thr, window_days=252):
    """TEST 18 (owner design 2026-07-16, 'sleeping module'): quiet Defense trades always
    in the trade book; the hostile-weather slice STARTS ASLEEP and wakes only when its
    own trailing 252d shadow record clears off_thr — walk-forward causal, like 16b."""
    from datetime import timedelta
    host = sorted([f for f in pool if wxm.get(f["ts"].date(), False)],
                  key=lambda f: f["xts"])
    closed, hi, gate_on, taken = [], 0, True, []   # gate_on=True = module asleep
    flips = 0
    for f in sorted(pool, key=lambda f: f["ts"]):
        while hi < len(host) and host[hi]["xts"] < f["ts"]:
            closed.append((host[hi]["xts"], host[hi]["r"] - COST))
            hi += 1
        if not wxm.get(f["ts"].date(), False):
            taken.append(f)
            continue
        cutoff = f["ts"] - timedelta(days=window_days)
        recent = [r for xts, r in closed if xts >= cutoff]
        if len(recent) >= 30:
            rpt = sum(recent) / len(recent)
            if gate_on and rpt > off_thr:
                gate_on = False; flips += 1
            elif not gate_on and rpt < on_thr:
                gate_on = True; flips += 1
        if not gate_on:
            taken.append(f)
    return taken, flips


AD_GRID = {}
for _wd in (63, 126, 252):
    for _nm, _on, _off in (("sym", -0.05, 0.05), ("asym", 0.0, 0.10)):
        _tk2, _fl = adaptive_deep(deep_pool, wx, _on, _off, window_days=_wd)
        AD_GRID[(_wd, _nm)] = (yearly(_tk2), _fl)
deep_adaptive_sym, fl_sym = AD_GRID[(252, "sym")]
deep_adaptive_asym, fl_asym = AD_GRID[(252, "asym")]

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
# DEFENSE GATE DEPLOYED 2026-08-04: the deployed Defense line IS the quiet-only one
deep_gated_pool = [f for f in deep_pool if not wx.get(f["ts"].date(), False)]
# GEM RETIRED 2026-08-04: excluded from the Book (kept as reference column)
combo = {y: round(pocket.get(y, 0) + scalp.get(y, 0)
                  + deep_quiet.get(y, 0), 1) for y in years}
RUPEE_PER_R = 8000                     # deployed clean-slate sizing: 8L @ 1%, every engine
book_rs = {y: round(combo[y] * RUPEE_PER_R) for y in years}

# COMPOUNDED (owner ask 2026-08-04: "did you increase the size? lets have a column for
# size"): the deployed engines risk 1% of RUNNING equity, so the honest deployed math
# compounds. Each book starts at 8L; eq *= (1 + 0.01*(r-cost)) per trade in sequence.
# NOT simulated: the tripwires (live books halve risk at -20% dd and HALT at -30%),
# so red years here overstate what a deployed book would actually ride.
def _compound(pool):
    e, yr = 800_000.0, {}
    for f in sorted(pool, key=lambda x: x["ts"]):
        e *= (1 + 0.01 * (f["r"] - COST))
        yr[f["ts"].year] = e
    out, ee = {}, 800_000.0
    for y in range(2015, 2027):
        ee = yr.get(y, ee)
        out[str(y)] = round(ee)
    return out

ceq = {"scalper": _compound(scalp_live_pool),
       "defense": _compound(deep_gated_pool)}
pe, pyr = 800_000.0, {}
for t in sorted((t for t in bt["exits"]["lockb"] if t["f"] & 1 and t["sd"] == "L"),
                key=lambda t: t["y"]):
    pe *= (1 + 0.01 * t["r"])
    pyr[t["y"]] = pe
out, ee = {}, 800_000.0
for y in range(2015, 2027):
    ee = pyr.get(y, ee)
    out[str(y)] = round(ee)
ceq["pocket"] = out
book_eq = {str(y): sum(ceq[k][str(y)] for k in ceq) for y in range(2015, 2027)}
prev = 3 * 800_000.0                    # Gem retired: three compounding books
book_cpl = {}
for y in range(2015, 2027):
    book_cpl[str(y)] = round(book_eq[str(y)] - prev)
    prev = book_eq[str(y)]

payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "note": ("Yearly net R at the LIVE 2026-08-04 rulebook (clean-slate era): "
                    "Scalper = 0.786 scalps + Gem (0.618 RETIRED; its canonical line "
                    "kept as scalper_benched_0618). Defense = ladders, as-is (thrice-"
                    "confirmed). Pocket = star-context 2H lock-at-B. 0.05R costs, one "
                    "position per stock. Uniform sizing 8L/engine = Rs8,000 per R. "
                    "Gamma has no backtest (forward-only; 2.0 live since 2026-08-03)."),
           "years": years,
           "data_through": max(f["ts"] for f in scalp_pool + deep_pool).strftime("%Y-%m-%d"),
           "rupee_per_r": RUPEE_PER_R,
           "engines": {"pocket_old": pocket_old, "pocket": pocket,
                       "scalper": scalp, "gem": gem,
                       "scalper_ungated": scalp_ungated,
                       "scalper_benched_0618": scalp_benched_0618,
                       "defense": deep_quiet, "defense_ungated": deep,
                       "book": combo, "book_rupees": book_rs,
                       "book_eq_compounded": book_eq,
                       "book_pl_compounded": book_cpl,
                       "eq_pocket": ceq["pocket"], "eq_scalper": ceq["scalper"],
                       "eq_defense": ceq["defense"]}}
open("docs/backtest_book.json", "w").write(json.dumps(payload, separators=(",", ":")))
print("\nwrote docs/backtest_book.json")
for y in years:
    print(f"  {y}: old {pocket_old.get(y,0):+7.1f} · pocket {pocket.get(y,0):+7.1f} · "
          f"scalp {scalp.get(y,0):+7.1f} · defense {deep.get(y,0):+7.1f} · "
          f"BOOK {combo.get(y,0):+7.1f}")

print("\nWHAT-IF (not deployed): Defense under a quiet-sideways-only weather gate")
for y in years:
    bq = round(pocket.get(y, 0) + scalp.get(y, 0) + deep_quiet.get(y, 0), 1)
    print(f"  {y}: defense {deep.get(y,0):+7.1f} -> {deep_quiet.get(y,0):+7.1f} · "
          f"BOOK {combo.get(y,0):+7.1f} -> {bq:+7.1f}")

print("\nTEST 18 — sleeping-module Defense, window grid (starts asleep)")
hdr = " | ".join(f"{wd}d-{nm}" for wd in (63, 126, 252) for nm in ("sym", "asym"))
print(f"  year:   as-is |  fixed | {hdr}")
for y in years:
    cells = " | ".join(f"{AD_GRID[(wd, nm)][0].get(y, 0):+7.1f}"
                       for wd in (63, 126, 252) for nm in ("sym", "asym"))
    print(f"  {y}: {deep.get(y,0):+7.1f} | {deep_quiet.get(y,0):+7.1f} | {cells}")
print(f"  TOTAL as-is {sum(deep.values()):+.1f} | fixed {sum(deep_quiet.values()):+.1f} | "
      + " | ".join(f"{wd}d-{nm} {sum(AD_GRID[(wd, nm)][0].values()):+.1f} "
                   f"(flips {AD_GRID[(wd, nm)][1]})"
                   for wd in (63, 126, 252) for nm in ("sym", "asym")))
