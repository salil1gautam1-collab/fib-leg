"""TEST 20b — the 11-YEAR BACKTEST AT 5-MINUTE EXECUTION (owner ask 2026-07-17:
"I hope you do the 11 years data backtest also with same time interval").
Same harness as test 20 / 13b, but the historical 1m CSVs are resampled to 5m FIRST,
so execution (fills, stops, ladder rungs, 75-bar window) walks the same granularity
the live engine trades on. Detection (1H/2H bars) is identical either way — this
isolates pure execution-granularity. Compare to the 1m line: +796R/11.3y, ~19% win.
ORIGINAL DOCSTRING FOLLOWS:

Live trade book: 45 trades, 7% win, -33.1R — far below the backtest's quiet-weather
line (+0.36R/trade, 24% win). Question: is the fortnight simply brutal (regime), or
does the live paper engine diverge mechanically from the backtest engine?

Method: run THE BACKTEST'S OWN MECHANICS (test 13b harness: same fib lifecycle, same
cushion, same ladder, cap scaled to 75 five-minute bars = the live window) over the
SAME data source the live scanner trades on (yfinance 5m, 60d) and the SAME dynamic
universe (docs/book_universe.json), then score only fills from 2026-07-06 onward and
compare to the live ledger — with gates and without.
READ-ONLY research — touches nothing live.
Run:  python swing_deep20_fidelity.py
"""
import bisect, json, sys, time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, r"C:\Salil Claude\fib-leg")
from fibleg.data import feeds
from fibleg.indicators.atr import AtrStreamer
from fibleg.models import PivotType
from fibleg.strategy.book_impulse import BookImpulse
from fibleg.strategy.pivots import ZigZag

MIN_LEG_ATR = 5.0
LEVELS = (0.5, 0.618, 0.786, 0.886)
RUNG_F = (0.382, 0.5, 0.618, 0.786, 0.886)
CUSH = .0031                     # the 1H@0.618 cushion, identical live + backtest
CUSH886 = .0042
CAP = 75                         # 75 five-minute bars = 375 minutes = the live window
COST = 0.05
SINCE = "2026-07-06"


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
                return -1.0, k, "stop"
        elif (b.low <= floor) if d == 1 else (b.high >= floor):
            return abs(floor - entry) / risk, k, "rung"
        for rp in rungs:
            hit = (b.high >= rp) if d == 1 else (b.low <= rp)
            better = floor is None or (rp > floor if d == 1 else rp < floor)
            if hit and better:
                floor = rp
    b = b1[kend - 1]
    r = ((b.close - entry) if d == 1 else (entry - b.close)) / risk
    if floor is not None:
        r = max(r, abs(floor - entry) / risk)
    return r, kend - 1, "time"


def run_symbol(tk, b5, events):
    """Test-13b generation verbatim, on 5m bars (TF=60 => resample factor 12)."""
    m_ts = [b.ts for b in b5]
    b2 = feeds.resample(b5, 12)              # 12 x 5m = 1H detection bars
    if len(b2) < 60:
        return
    b2h = feeds.resample(b5, 24)             # 2H referee
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
                    die = lv[0.886] * (1 - CUSH886) if d == 1 else lv[0.886] * (1 + CUSH886)
                    fibs.append({"d": d, "e": extreme.price, "lv": lv, "die": die,
                                 "rng": rng, "consumed": set(), "active": not fibs,
                                 "born": prev.ts})
        if not fibs:
            continue
        j0 = bisect.bisect_right(m_ts, prev.ts)
        j1 = bisect.bisect_right(m_ts, b2[i2].ts)
        for j in range(j0, j1):
            bar = b5[j]
            prevc = b5[j - 1].close if j > 0 else bar.open
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
                    if not (fib["active"] and approach and j + 80 < len(b5)):
                        continue
                    if L != 0.618 or d != 1:
                        continue
                    stop = level - CUSH * level
                    o = sim_ladder(b5, j + 1, d, level, stop, fib["e"], CAP)
                    if o is not None:
                        events.append({"sym": tk, "ts": bar.ts, "entry": round(level, 2),
                                       "r": o[0] - COST, "reason": o[2]})
                # fib death on 5m closes (every bar here IS 5m)
                dead = (bar.close < fib["die"]) if d == 1 else (bar.close > fib["die"])
                if not dead:
                    dead = (bar.close > fib["e"]) if d == 1 else (bar.close < fib["e"])
                if dead:
                    was = fib["active"]
                    fibs.remove(fib)
                    if was and fibs:
                        max(fibs, key=lambda f: f["born"])["active"] = True


# ---------- the 11-year dataset, 1m CSVs resampled to 5m ----------
DIR = sys.argv[1] if len(sys.argv) > 1 else "fibleg/data/Stocks_data"
syms = [s for s in feeds.csv_dir_symbols(DIR) if "NIFTY" not in s.upper()]
print(f"universe: {len(syms)} historical symbols", flush=True)

events, t0 = [], time.time()
for i, s in enumerate(syms):
    try:
        b1 = feeds.csv_dir_series(DIR, s)
        b5 = feeds.resample(b1, 5)          # 1m -> 5m: the live execution granularity
        if len(b5) > 500:
            run_symbol(s, b5, events)
    except Exception as e:  # noqa: BLE001
        print("skip", s, str(e)[:60], flush=True)
    if (i + 1) % 25 == 0 or i + 1 == len(syms):
        print(f"  [{i+1}/{len(syms)}] {len(events)} events ({time.time()-t0:.0f}s)", flush=True)

ev = events
print(f"\ntotal 5m-execution fills (11.3y): {len(ev)}")

# ---------- tables ----------
def table(name, rows, rkey=lambda t: t.get("r")):
    byd = defaultdict(lambda: [0.0, 0])
    for t in rows:
        r = rkey(t)
        if r is None:
            continue
        dd = (t["ts"] if isinstance(t["ts"], str) else t["ts"].isoformat())[:10]
        byd[dd][0] += r
        byd[dd][1] += 1
    print(f"\n--- {name} ---")
    tot = n = w = 0
    for dd in sorted(byd):
        print(f"  {dd}: {byd[dd][0]:+7.2f}R ({byd[dd][1]})")
        tot += byd[dd][0]; n += byd[dd][1]
    rs = [rkey(t) for t in rows if rkey(t) is not None]
    w = sum(1 for r in rs if r > 0)
    print(f"  TOTAL {tot:+.2f}R · {n} trades · win {100*w/max(n,1):.0f}%")

from collections import defaultdict as _dd
byy = _dd(lambda: [0.0, 0, 0])
for e in ev:
    y = e["ts"].year
    byy[y][0] += e["r"]; byy[y][1] += 1; byy[y][2] += 1 if e["r"] > 0 else 0
print("\n--- 11-YEAR 1H@0.618 LADDER AT 5-MINUTE EXECUTION (vs 1m line +796R, ~19% win) ---")
for y in sorted(byy):
    print(f"  {y}: {byy[y][0]:+8.1f}R · n={byy[y][1]:4} · win {100*byy[y][2]/max(byy[y][1],1):4.1f}%")
tot = sum(v[0] for v in byy.values()); n = sum(v[1] for v in byy.values())
w = sum(v[2] for v in byy.values())
print(f"  TOTAL {tot:+.1f}R · {n} trades · win {100*w/max(n,1):.1f}%")
byr = defaultdict(int)
for e in ev:
    byr[e["reason"]] += 1
print("replay exit reasons:", dict(byr))


print("done")
