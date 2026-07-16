"""TEST 16b — ADAPTIVE weather gate for SCALP 1H@0.618 (walk-forward, causal).
Owner go 2026-07-16 ("we will implement the suggestions just discussed") after test 16
showed a clean era break: hostile-weather trades paid +332R in 2015-2022 and lost -129R
in 2023-2026. Question: does a gate that reads its own trailing scorecard beat BOTH
fixed stances (always-take vs never-take-hostile)?

Rule (strictly causal — decisions use only trades CLOSED before the fill):
  quiet-sideways trades are always taken. Before each hostile-weather fill, look at
  hostile trades closed in the trailing WINDOW; if the gate is currently OFF it flips
  ON (bench hostile) when trailing R/trade < ON_THR; if ON it flips OFF (re-admit)
  when trailing R/trade > OFF_THR. Under 30 closes in window -> keep current stance
  (start OFF: innocent until proven guilty, matching 2015 reality).
Grid (robustness check — the verdict must hold across MOST cells, not one):
  windows 126d / 252d x variants: fast (0/0), symmetric (-0.05/+0.05),
  asymmetric quick-bench-slow-refund (0/+0.10).
READ-ONLY research — touches nothing live.
Run:  python swing_deep16b_adaptive.py "fibleg/data/Stocks_data"
"""
import bisect, json, sys, time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from fibleg.data import feeds
from fibleg.indicators.atr import AtrStreamer
from fibleg.models import PivotType
from fibleg.strategy.book_impulse import BookImpulse
from fibleg.strategy.pivots import ZigZag

DIR = sys.argv[1] if len(sys.argv) > 1 else "fibleg/data/Stocks_data"
MIN_LEG_ATR = 5.0
LEVELS = (0.5, 0.618, 0.786, 0.886)
RUNG_F = (0.382, 0.5, 0.618, 0.786, 0.886)
STK = {(60, 0.618): (.0031, 375, True)}
CUSH886 = {60: .0042}
COST = 0.05
CACHE = Path(r"C:\Users\Admin\AppData\Local\Temp\claude\C--Salil-Claude"
             r"\5effca38-4699-441b-8bbd-0a4f831411d9\scratchpad\test16_trades.json")


def sim_ladder(b1, j, d, entry, stop, top, cap):
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


def run_symbol(tk, b1, combos, events):
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
                        lad = sim_ladder(b1, j + 1, d, level, stop, fib["e"], cap)
                        if lad is not None:
                            events.append({"ts": bar.ts.isoformat(),
                                           "xts": m1_ts[lad[1]].isoformat(),
                                           "r": lad[0] - COST})
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


def build_weather():
    import yfinance as yf
    from fibleg.indicators.trend import AdxStreamer
    from fibleg.models import Bar
    wx = {}
    nd = yf.download("^NSEI", start="2014-06-01", interval="1d",
                     progress=False, auto_adjust=False)
    vd = yf.download("^INDIAVIX", start="2014-06-01", interval="1d",
                     progress=False, auto_adjust=False)
    n = {c[0] if isinstance(c, tuple) else c: nd[c].squeeze() for c in nd.columns}
    v_close = vd["Close"].squeeze().dropna()
    vix, vavg = {}, {}
    vals = list(v_close.items())
    for i, (ts, c) in enumerate(vals):
        prior = [float(x[1]) for x in vals[max(0, i - 19):i + 1]]
        vix[ts.date()] = float(c)
        vavg[ts.date()] = sum(prior) / len(prior)
    adx = AdxStreamer(14)
    closes, ranges = [], []
    dts = list(n["Close"].dropna().index)
    for i, ts in enumerate(dts):
        o, h, l, c = (float(n[k][ts]) for k in ("Open", "High", "Low", "Close"))
        a = adx.update(Bar(ts.to_pydatetime(), o, h, l, c))
        closes.append(c)
        day = ts.date()
        move = abs(c - o) / o if o else 0.0
        rng = h - l
        reg = None
        if i >= 50:
            up = c > sum(closes[i - 50:i]) / 50
            reg = ("UPT" if up else "DNT") if a >= 25 else ("SDW" if a < 20 else "WHP")
        vhi_old = day in vix and vix[day] > vavg[day]
        prior10 = ranges[-10:]
        rblow = len(prior10) >= 3 and sum(prior10) / len(prior10) > 0 \
            and rng >= 1.8 * (sum(prior10) / len(prior10))
        runs = (reg in ("UPT", "DNT")) or move >= 0.010 or rblow \
            or (vhi_old and move >= 0.006)
        ranges.append(rng)
        vx = vix.get(day)
        wx[day] = {"vhi_new": vhi_old and (vx is not None and vx > 15.0), "runs": runs}
    return wx


# ---------- generate or load the trade set ----------
t0 = time.time()
if CACHE.exists():
    events = json.loads(CACHE.read_text())
    print(f"loaded {len(events)} cached trades", flush=True)
else:
    events = []
    stk = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
    for ti, tk in enumerate(stk):
        try:
            b1 = feeds.csv_dir_series(DIR, tk)
        except Exception as e:  # noqa: BLE001
            print("skip", tk, e, flush=True); continue
        run_symbol(tk, b1, STK, events)
        if (ti + 1) % 25 == 0 or ti + 1 == len(stk):
            print(f"  [{ti+1}/{len(stk)}] {len(events)} ({time.time()-t0:.0f}s)", flush=True)
    CACHE.write_text(json.dumps(events))

from datetime import datetime
wx = build_weather()
ev = []
for e in events:
    ts = datetime.fromisoformat(e["ts"])
    w = wx.get(ts.date())
    if w is None:
        continue
    ev.append({"ts": ts, "xts": datetime.fromisoformat(e["xts"]), "r": e["r"],
               "hostile": w["runs"] or w["vhi_new"]})
ev.sort(key=lambda e: e["ts"])
print(f"{len(ev)} trades matched to weather "
      f"({sum(1 for e in ev if e['hostile'])} hostile)", flush=True)


def simulate(window_days, on_thr, off_thr):
    """Walk forward; return (taken trades list, flips, gate_on_periods)."""
    hostile_closed = []          # (exit_ts, r) — every hostile trade, both books
    gate_on = False
    flips = 0
    taken = []
    hi = 0                        # pointer into hostile closes sorted by exit
    hostile_by_exit = sorted([e for e in ev if e["hostile"]], key=lambda e: e["xts"])
    for e in ev:
        while hi < len(hostile_by_exit) and hostile_by_exit[hi]["xts"] < e["ts"]:
            hostile_closed.append((hostile_by_exit[hi]["xts"], hostile_by_exit[hi]["r"]))
            hi += 1
        if not e["hostile"]:
            taken.append(e)
            continue
        cutoff = e["ts"] - timedelta(days=window_days)
        recent = [r for xts, r in hostile_closed if xts >= cutoff]
        if len(recent) >= 30:
            rpt = sum(recent) / len(recent)
            if not gate_on and rpt < on_thr:
                gate_on = True; flips += 1
            elif gate_on and rpt > off_thr:
                gate_on = False; flips += 1
        if not gate_on:
            taken.append(e)
    return taken, flips


def line(name, trades, flips=None):
    net = sum(t["r"] for t in trades)
    yr = defaultdict(float)
    for t in trades:
        yr[t["ts"].year] += t["r"]
    losing = sum(1 for v in yr.values() if v < 0)
    cum = peak = mdd = 0.0
    for t in trades:
        cum += t["r"]; peak = max(peak, cum); mdd = max(mdd, peak - cum)
    f = f" · flips {flips}" if flips is not None else ""
    print(f"  {name:34} net {net:+8.1f}R · n={len(trades):4} · losing yrs "
          f"{losing}/12 · maxDD {mdd:5.1f}R{f}")
    return yr


print("\n===== fixed stances =====")
line("ALL (never gate)", ev)
quiet = [e for e in ev if not e["hostile"]]
line("QUIET-ONLY (always gate)", quiet)

print("\n===== adaptive grid =====")
grids = [("fast 0/0", 0.0, 0.0), ("symmetric ±0.05", -0.05, 0.05),
         ("asym bench@0 refund@+0.10", 0.0, 0.10)]
results = {}
for wd in (126, 252):
    for gname, on_t, off_t in grids:
        taken, flips = simulate(wd, on_t, off_t)
        results[(wd, gname)] = taken
        line(f"{wd}d · {gname}", taken, flips)

print("\n===== per-year: ALL vs QUIET vs best-behaved adaptive (252d asym) =====")
ta = results[(252, "asym bench@0 refund@+0.10")]
ya = defaultdict(float); yq = defaultdict(float); yl = defaultdict(float)
for t in ev: ya[t["ts"].year] += t["r"]
for t in quiet: yq[t["ts"].year] += t["r"]
for t in ta: yl[t["ts"].year] += t["r"]
for y in sorted(ya):
    print(f"  {y}: ALL {ya[y]:+8.1f} · QUIET {yq[y]:+8.1f} · ADAPTIVE {yl[y]:+8.1f}")
print(f"\ndone in {time.time()-t0:.0f}s")
