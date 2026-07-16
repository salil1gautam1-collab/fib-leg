"""TEST 17 — WEATHER SPLIT for the DEFENSE deep trio (live ladder rules).
Owner go 2026-07-16 ("can't you see how much it would hurt the good days if we put
something to cut the number of bad days?") — the shelved item 15, woken early by the
owner. Question: do the multi-day deep-level runners only arrive in certain weather,
and what would a weather gate COST the great years vs SAVE the red ones?

Method: identical touch generation + LADDER exit as TEST 13b (the live rules), then each
trade is stamped with its ENTRY DAY's weather and the results are split:
  regime      SDW / WHP / UPT / DNT           (Nifty daily ADX, same rule as fibleg.context)
  vix_old     India VIX close > trailing-20d avg  (the original relative flag)
  vix_new     vix_old AND close > 15              (the v142 floored flag)
  runs-day    UPT/DNT regime OR |day move| >= 1% OR range >= 1.8x prior-10d avg
              OR (vix_old AND move >= 0.6%)       (EOD mirror of gamma's runs detector)
  vix band    <13 / 13-15 / 15-18 / >18
READ-ONLY research — touches nothing live.
Run:  python swing_deep16_weather.py "fibleg/data/Stocks_data"
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
RUNG_F = (0.382, 0.5, 0.618, 0.786, 0.886)
STK = {(60, 0.786): (.0039, 3750, True),     # the LIVE Defense trio (ladder, 10-session cap)
       (60, 0.886): (.0042, 3750, True),
       (120, 0.886): (.0045, 3750, True)}
CUSH886 = {60: .0042, 120: .0045}
COST = 0.05


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
                            events.append({"tk": tk, "ts": bar.ts, "y": bar.ts.year,
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


# ---------- daily weather table (Nifty + India VIX, 2014 -> now) ----------
def build_weather():
    import yfinance as yf
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
    from fibleg.indicators.trend import AdxStreamer
    from fibleg.models import Bar
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
        wx[day] = {"reg": reg, "vix": vx, "vhi_old": vhi_old,
                   "vhi_new": vhi_old and (vx is not None and vx > 15.0),
                   "runs": runs}
    return wx


def cellrep(title, groups):
    print(f"\n--- {title} ---")
    for name, rs in groups:
        if not rs:
            print(f"  {name:26} n=0"); continue
        net = sum(rs); win = 100 * sum(1 for r in rs if r > 0) / len(rs)
        print(f"  {name:26} n={len(rs):5} · net {net:+8.1f}R · {net/len(rs):+.3f}R/trade · win {win:4.1f}%")


events, t0 = [], time.time()
stk = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
for ti, tk in enumerate(stk):
    try:
        b1 = feeds.csv_dir_series(DIR, tk)
    except Exception as e:  # noqa: BLE001
        print("skip", tk, e, flush=True); continue
    run_symbol(tk, b1, STK, events)
    if (ti + 1) % 25 == 0 or ti + 1 == len(stk):
        print(f"  [{ti+1}/{len(stk)}] {len(events)} trades ({time.time()-t0:.0f}s)", flush=True)

print(f"\n===== {len(events)} SCALP 1H@0.618 ladder trades (cost {COST}R) =====")
wx = build_weather()
miss = sum(1 for e in events if e["ts"].date() not in wx)
print(f"weather coverage: {len(events)-miss}/{len(events)} trades matched")
ev = [dict(e, w=wx[e["ts"].date()]) for e in events if e["ts"].date() in wx]

R = lambda sel: [e["r"] for e in ev if sel(e["w"])]
cellrep("Market regime (daily ADX)", [(r, R(lambda w, r=r: w["reg"] == r))
                                      for r in ("SDW", "WHP", "UPT", "DNT")])
cellrep("VIX flag — OLD (rel only)", [("vix HIGH (old)", R(lambda w: w["vhi_old"])),
                                      ("vix calm (old)", R(lambda w: not w["vhi_old"]))])
cellrep("VIX flag — NEW (rel AND >15)", [("vix HIGH (new)", R(lambda w: w["vhi_new"])),
                                         ("vix calm (new)", R(lambda w: not w["vhi_new"]))])
cellrep("Runs-day (EOD mirror of the detector)",
        [("running day", R(lambda w: w["runs"])), ("sideways day", R(lambda w: not w["runs"]))])
cellrep("WEATHER TABLE cells (new VIX def)",
        [("quiet sideways (allowed)", R(lambda w: not w["runs"] and not w["vhi_new"])),
         ("sideways + vix-high", R(lambda w: not w["runs"] and w["vhi_new"])),
         ("running (any vix)", R(lambda w: w["runs"]))])
cellrep("Raw VIX bands", [("< 13", R(lambda w: w["vix"] is not None and w["vix"] < 13)),
                          ("13 - 15", R(lambda w: w["vix"] is not None and 13 <= w["vix"] < 15)),
                          ("15 - 18", R(lambda w: w["vix"] is not None and 15 <= w["vix"] < 18)),
                          ("> 18", R(lambda w: w["vix"] is not None and w["vix"] >= 18))])

print("\n--- per-year: ALL vs QUIET-SIDEWAYS-ONLY (the would-be gate) ---")
years = sorted({e["y"] for e in ev})
tot_all = tot_q = 0.0
for y in years:
    a = [e["r"] for e in ev if e["y"] == y]
    q = [e["r"] for e in ev if e["y"] == y and not e["w"]["runs"] and not e["w"]["vhi_new"]]
    tot_all += sum(a); tot_q += sum(q)
    print(f"  {y}: ALL {sum(a):+8.1f}R ({len(a):4}) · QUIET {sum(q):+8.1f}R ({len(q):4})"
          f" · gated away {sum(a)-sum(q):+7.1f}R")
print(f"  TOTAL: ALL {tot_all:+.1f}R · QUIET {tot_q:+.1f}R "
      f"· the gate would have cost/saved {tot_q-tot_all:+.1f}R")
print(f"\ndone in {time.time()-t0:.0f}s")
