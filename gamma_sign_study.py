"""TEST 22 — THE SIGN STUDY: is gamma's core assumption backwards for NSE?
Owner-driven root-cause hunt 2026-07-28. Every map assumed "dealers long calls, short
puts" (US convention). If the sign is wrong for India's writer-heavy market, "sticky"
(positive) regimes are actually amplifying and walls are anti-magnets — which is what
era-2's carnage looked like (median favorable move 0.24R in approved weather).

Data: every gamma_map.json snapshot in git history (~30-min cadence since 07-06).
For each symbol snapshot pair (same day, consecutive maps):
  A. WALL-PULL: did spot move TOWARD the top wall over the next interval?
     Under the CURRENT sign, positive regime should pull toward (>50%).
  B. DAMPING: |return| next interval — positive regime should be SMALLER.
  C. FLIP-SIDE: above-flip (positive world) vs below — same tests.
Verdict logic: if positive-labeled states show anti-pull and larger moves, the sign is
inverted for NSE. READ-ONLY research. Run inside the repo: python gamma_sign_study.py
"""
import json, statistics, subprocess, sys
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
shas = subprocess.run(
    ["git", "log", "--format=%H", "--since", "2026-07-05", "--", "docs/gamma_map.json"],
    capture_output=True, text=True, check=True).stdout.split()
print(f"map snapshots in history: {len(shas)}", flush=True)
maps = []
for sha in shas:
    try:
        raw = subprocess.run(["git", "show", f"{sha}:docs/gamma_map.json"],
                             capture_output=True, text=True, check=True).stdout
        m = json.loads(raw)
        maps.append((datetime.fromisoformat(m["generated"]), m["maps"]))
    except Exception:  # noqa: BLE001
        continue
maps.sort(key=lambda x: x[0])
print(f"parsed: {len(maps)}", flush=True)

# per-symbol chronological series
series = defaultdict(list)
for ts, mm in maps:
    for sym, g in mm.items():
        if g.get("spot") and g.get("walls"):
            series[sym].append((ts, g))

pairs = []          # (regime, above_flip, toward_wall, absret)
for sym, rows in series.items():
    for i in range(1, len(rows)):
        (t0, g0), (t1, g1) = rows[i - 1], rows[i]
        if t0.date() != t1.date():
            continue
        dt = (t1 - t0).total_seconds() / 60
        if not (10 <= dt <= 90):
            continue
        s0, s1 = g0["spot"], g1["spot"]
        wall = g0["walls"][0]["strike"]
        if wall == s0:
            continue
        toward = (s1 - s0) * (wall - s0) > 0
        absret = abs(s1 - s0) / s0 * 100
        above = g0.get("flip") is not None and s0 > g0["flip"]
        pairs.append((g0.get("regime"), above, toward, absret, sym.startswith("^")))

print(f"consecutive same-day pairs: {len(pairs)}")

def report(name, sel):
    sub = [p for p in pairs if sel(p)]
    if len(sub) < 100:
        print(f"  {name:28} n={len(sub)} (too few)")
        return
    tw = 100 * sum(1 for p in sub if p[2]) / len(sub)
    med = statistics.median(p[3] for p in sub)
    print(f"  {name:28} n={len(sub):6} · toward-wall {tw:4.1f}% · median |ret| {med:.3f}%")

print("\n--- A/B by REGIME label (current sign: positive = sticky = pull + damp) ---")
report("regime POSITIVE (labeled sticky)", lambda p: p[0] == "positive")
report("regime NEGATIVE (labeled runs)", lambda p: p[0] == "negative")
print("--- by FLIP side ---")
report("above flip (labeled sticky world)", lambda p: p[1])
report("below flip (labeled runs world)", lambda p: not p[1])
print("--- indices only ---")
report("indices, positive", lambda p: p[4] and p[0] == "positive")
report("indices, negative", lambda p: p[4] and p[0] == "negative")
print("--- stocks only ---")
report("stocks, positive", lambda p: not p[4] and p[0] == "positive")
report("stocks, negative", lambda p: not p[4] and p[0] == "negative")

print("""
READING: current sign predicts positive-label rows show toward-wall > 50% and SMALLER
median |ret| than negative-label rows. If positive rows show toward-wall <= 50% and/or
LARGER moves, the assumption is inverted for NSE. If labels show no separation at all,
the GEX model itself carries no signal here (retirement evidence).""")
