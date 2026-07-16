"""TEST 19 — the TWO-KEY GATE: FII positioning lead-lag + shadow confirmation.
Owner design 2026-07-16: "recorded data gives a clue, the shadow book gives the
confirmation, then we open the gate." Two questions, answered in order:
  A. LEAD-LAG: does FII positioning stress (NSE participant-wise OI archive) LEAD our
     hostile-weather days by 1-3 days, beyond weather's own persistence?
  B. MODULE: does a two-key sleeping Defense (Key 1 = stress armed recently, Key 2 =
     trailing shadow record positive) beat the single-key versions test 18 rejected?
Pre-registered stress signals (no post-hoc picking; verdict needs robustness):
  S1 dump-day:    1d change in FII net index futures, 60d z-score < -1
  S2 dump-trend:  3d cumulative net change, z < -1
  S3 hedge-build: 1d change in FII index PUT LONG OI, z > +1
  S4 short-level: FII net index futures level, z < -1
ARMED (Key 1) = S2 or S3 fired within the last 5 sessions (hedging build or
sustained dumping — chosen before results were seen).
Inputs: scratchpad nse_fao/ archive · book_fills_cache.json (test 18 Defense fills).
READ-ONLY research — touches nothing live.
Run:  python swing_deep19_twokey.py
"""
import json, math, statistics, sys
from collections import defaultdict
from datetime import date, timedelta, datetime
from pathlib import Path

SP = Path(r"C:\Users\Admin\AppData\Local\Temp\claude\C--Salil-Claude"
          r"\5effca38-4699-441b-8bbd-0a4f831411d9\scratchpad")
FAO = SP / "nse_fao"
CACHE = SP / "book_fills_cache.json"
COST = 0.05

# ---------- FII daily features ----------
days = []
for f in sorted(FAO.glob("*.csv")):
    txt = f.read_text(encoding="utf-8", errors="ignore")
    if not txt.strip():
        continue
    for line in txt.splitlines():
        c = [x.strip().strip('"') for x in line.split(",")]
        if c and c[0].upper() == "FII" and len(c) >= 9:
            try:
                days.append({"d": date.fromisoformat(f.stem),
                             "net_fut": float(c[1]) - float(c[2]),
                             "put_long": float(c[6])})
            except ValueError:
                pass
            break
days.sort(key=lambda r: r["d"])
print(f"FII rows parsed: {len(days)} ({days[0]['d']} -> {days[-1]['d']})" if days
      else "NO FII DATA — abort", flush=True)
if len(days) < 500:
    sys.exit(1)

def zseries(vals, win=60):
    out = [None] * len(vals)
    for i in range(len(vals)):
        w = vals[max(0, i - win + 1):i + 1]
        if len(w) >= 20:
            sd = statistics.pstdev(w)
            out[i] = (vals[i] - statistics.fmean(w)) / sd if sd > 0 else 0.0
    return out

net = [r["net_fut"] for r in days]
d1 = [0.0] + [net[i] - net[i - 1] for i in range(1, len(net))]
d3 = [0.0] * 3 + [net[i] - net[i - 3] for i in range(3, len(net))]
pl = [r["put_long"] for r in days]
pl1 = [0.0] + [pl[i] - pl[i - 1] for i in range(1, len(pl))]
z_d1, z_d3, z_pl, z_lvl = zseries(d1), zseries(d3), zseries(pl1), zseries(net, 120)
SIG = {}
for i, r in enumerate(days):
    SIG[r["d"]] = {"S1": z_d1[i] is not None and z_d1[i] < -1,
                   "S2": z_d3[i] is not None and z_d3[i] < -1,
                   "S3": z_pl[i] is not None and z_pl[i] > 1,
                   "S4": z_lvl[i] is not None and z_lvl[i] < -1}
dates_all = [r["d"] for r in days]

# ---------- weather map (identical classifier to tests 16-18) ----------
def build_weather():
    import yfinance as yf
    sys.path.insert(0, r"C:\Salil Claude\fib-leg")
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
        vix[ts.date()] = float(c); vavg[ts.date()] = sum(prior) / len(prior)
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
        vhi = day in vix and vix[day] > vavg[day]
        p10 = ranges[-10:]
        rb = len(p10) >= 3 and sum(p10) / len(p10) > 0 and rng >= 1.8 * (sum(p10) / len(p10))
        ranges.append(rng)
        vx = vix.get(day)
        wx[day] = ((reg in ("UPT", "DNT")) or move >= 0.010 or rb
                   or (vhi and move >= 0.006) or (vhi and vx is not None and vx > 15.0))
    return wx

wx = build_weather()
tdays = [d for d in dates_all if d in wx]
print(f"joined trading days: {len(tdays)}")

# ---------- A. lead-lag ----------
base1 = statistics.fmean(1 if wx[tdays[i + 1]] else 0 for i in range(len(tdays) - 1))
print(f"\n=== A. LEAD-LAG (base rate: next day hostile {base1*100:.1f}%) ===")
for s in ("S1", "S2", "S3", "S4"):
    for lag in (1, 2, 3):
        hit = [1 if wx[tdays[i + lag]] else 0
               for i in range(len(tdays) - lag) if SIG.get(tdays[i], {}).get(s)]
        if len(hit) > 30:
            print(f"  {s} -> t+{lag}: P(hostile)={statistics.fmean(hit)*100:4.1f}% "
                  f"(n={len(hit)})")
print("  TRANSITION test (the money question): among CALM days t, next-day hostile?")
for s in ("S1", "S2", "S3", "S4"):
    a = [1 if wx[tdays[i + 1]] else 0 for i in range(len(tdays) - 1)
         if not wx[tdays[i]] and SIG.get(tdays[i], {}).get(s)]
    b = [1 if wx[tdays[i + 1]] else 0 for i in range(len(tdays) - 1)
         if not wx[tdays[i]] and not SIG.get(tdays[i], {}).get(s)]
    if len(a) > 20:
        print(f"  {s}: with-stress {statistics.fmean(a)*100:4.1f}% (n={len(a)}) vs "
              f"without {statistics.fmean(b)*100:4.1f}% (n={len(b)})")

# ---------- B. two-key module on the cached Defense fills ----------
fills = [dict(f, ts=datetime.fromisoformat(f["ts"]), xts=datetime.fromisoformat(f["xts"]))
         for f in json.loads(CACHE.read_text()) if f["book"] == "DEEP"]
# one-per-stock lock (same as the baker)
fills.sort(key=lambda f: f["ts"])
open_until, pool = {}, []
for f in fills:
    ou = open_until.get(f["tk"])
    if ou is not None and f["ts"] < ou:
        continue
    pool.append(f); open_until[f["tk"]] = f["xts"]
print(f"\n=== B. TWO-KEY MODULE ({len(pool)} Defense fills after stock-lock) ===")

armed_days = set()
sig_days = sorted(SIG)
for i, dd in enumerate(sig_days):
    if any(SIG[sig_days[j]]["S2"] or SIG[sig_days[j]]["S3"]
           for j in range(max(0, i - 4), i + 1)):
        armed_days.add(dd)
print(f"Key-1 ARMED on {len(armed_days)}/{len(sig_days)} days "
      f"({100*len(armed_days)/len(sig_days):.0f}%)")

def run_module(key1, key2_window, on_thr=-0.05, off_thr=0.05):
    host = sorted([f for f in pool if wx.get(f["ts"].date(), False)], key=lambda f: f["xts"])
    closed, hi, k2_ok, taken = [], 0, False, []
    for f in sorted(pool, key=lambda f: f["ts"]):
        while hi < len(host) and host[hi]["xts"] < f["ts"]:
            closed.append((host[hi]["xts"], host[hi]["r"] - COST)); hi += 1
        if not wx.get(f["ts"].date(), False):
            taken.append(f); continue
        if key2_window:
            cutoff = f["ts"] - timedelta(days=key2_window)
            recent = [r for xts, r in closed if xts >= cutoff]
            if len(recent) >= 30:
                rpt = sum(recent) / len(recent)
                if not k2_ok and rpt > off_thr:
                    k2_ok = True
                elif k2_ok and rpt < on_thr:
                    k2_ok = False
        ok = (not key1 or f["ts"].date() in armed_days) and (not key2_window or k2_ok)
        if ok:
            taken.append(f)
    return taken

def line(name, taken):
    yr = defaultdict(float)
    for f in taken:
        yr[f["ts"].year] += f["r"] - COST
    tot = sum(yr.values()); losing = sum(1 for v in yr.values() if v < 0)
    print(f"  {name:34} {tot:+8.1f}R · losing yrs {losing}/12")
    return yr

allf = line("as-is (always awake)", pool)
line("fixed gate (never wakes)", [f for f in pool if not wx.get(f["ts"].date(), False)])
line("Key1-only (stress-armed)", run_module(True, None))
for w in (63, 126, 252):
    line(f"Key2-only {w}d (test-18 rerun)", run_module(False, w))
for w in (63, 126, 252):
    line(f"TWO-KEY: armed AND {w}d shadow", run_module(True, w))
print("\nRobustness bar: the two-key line must beat BOTH fixed stances across most "
      "windows — one good cell is curve-fitting.")
