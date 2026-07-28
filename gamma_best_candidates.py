"""TEST 23 — BEST-CANDIDATES SCAN (gamma last chance, owner 2026-07-28: "maybe we are
over-trading instead of best candidates"). Every recorded pin fill joined to its exact
map; expectancy per PRE-REGISTERED candidate filter. If a filter clears +0.10R/trade
on real n, it defines Gamma 2.0's subset for the Sep-1 rebirth bar.
DERIVED FROM the ML pilot's join machinery:
Owner go 2026-07-16 ("You can test it yes") after the stop autopsy showed 59% of
gamma's stops die instantly — wall SELECTION, not stop width, is the lever.

Dataset: every historical gamma map published since 2026-07-06 lives in git history
(docs/gamma_map.json, ~200+ snapshots). Every pin fill in the live ledger (trade book
+ shadow book, BE-twins excluded) is a labeled sample: join fill -> the map in force
at fill time -> the entered wall's features. Label: did the trade make money?

HONEST FRAMING (printed with results): ~1,200 fills across only ~9 trading days —
day-level clustering makes the effective sample far smaller than n suggests. This is
a PILOT to rank candidate features and set up the forward study (features are now
worth stamping on fills); nothing deploys from this without more days of data.
READ-ONLY research — touches nothing live.
Run:  python gamma_wall_ml.py            (inside the repo)
"""
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

# ---------- 1) historical maps out of git ----------
shas = subprocess.run(
    ["git", "log", "--format=%H", "--since", "2026-07-05", "--", "docs/gamma_map.json"],
    capture_output=True, text=True, check=True).stdout.split()
maps_by_ts = []
for sha in shas:
    try:
        raw = subprocess.run(["git", "show", f"{sha}:docs/gamma_map.json"],
                             capture_output=True, text=True, check=True).stdout
        m = json.loads(raw)
        maps_by_ts.append((datetime.fromisoformat(m["generated"]), m["maps"]))
    except Exception:  # noqa: BLE001
        continue
maps_by_ts.sort(key=lambda x: x[0])
print(f"map snapshots recovered from git: {len(maps_by_ts)}")

# ---------- 2) labeled fills ----------
st = json.load(open("docs/paper_gamma.json"))
fills = []
for lst in ("closed", "shadow_closed", "open", "shadow_open"):
    for t in st.get(lst, []):
        if t.get("mode") != "pin" or t.get("r") is None:
            continue
        if t.get("skip") == "study-be75":          # duplicate of its real sibling
            continue
        fills.append(t)
print(f"labeled pin fills: {len(fills)}")

# ---------- 3) join fill -> map in force -> wall features ----------
gen_ts = [ts for ts, _ in maps_by_ts]
rows = []
miss_map = miss_wall = 0
for t in fills:
    fts = datetime.fromisoformat(t["ts"])
    if fts.tzinfo is None:
        fts = fts.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    import bisect
    j = bisect.bisect_right(gen_ts, fts.astimezone(timezone.utc)) - 1
    if j < 0 or (fts.astimezone(timezone.utc) - gen_ts[j]) > timedelta(minutes=45):
        miss_map += 1
        continue
    g = maps_by_ts[j][1].get(t["sym"])
    if not g or not g.get("walls"):
        miss_map += 1
        continue
    walls = g["walls"]
    tol = max(0.002 * (g.get("spot") or 1), 1.0)
    wall = min(walls, key=lambda w: abs(w["strike"] - t["wall"]))
    if abs(wall["strike"] - t["wall"]) > max(tol, 0.01 * (g.get("spot") or 1)):
        miss_wall += 1
        continue
    tot = sum(w["strength"] for w in walls) or 1.0
    rank = sorted(walls, key=lambda w: -w["strength"]).index(wall)
    spot = g.get("spot") or t["entry"]
    coi, poi = wall.get("call_oi") or 0, wall.get("put_oi") or 0
    rows.append({
        "r": t["r"], "win": 1 if t["r"] > 0 else 0,
        "died": 1 if (t.get("reason") in ("stop", "gap-stop") and t["r"] <= -0.9) else 0,
        "day": fts.date().isoformat(),
        # features
        "wall_rank": rank,
        "wall_share": wall["strength"] / tot,
        "n_walls": len(walls),
        "oi_skew": (coi - poi) / (coi + poi) if (coi + poi) else 0.0,
        "dist_flip_pct": abs(t["entry"] - (g.get("flip") or spot)) / spot * 100,
        "spot_wall_pct": abs(spot - wall["strike"]) / spot * 100,
        "net_gex_sign": 1 if (g.get("net_gex") or 0) > 0 else -1,
        "sigma": g.get("sigma") or 0.0,
        "dte": t.get("dte") or g.get("expiry_days") or 0,
        "is_long": 1 if t["d"] == 1 else 0,
        "hour": fts.hour + fts.minute / 60.0,
        "mkt_runs": 1 if t.get("mkt") == "runs" else 0,
        "is_index": 1 if t["sym"].startswith("^") else 0,
    })
print(f"joined samples: {len(rows)} (no-map {miss_map} · wall-mismatch {miss_wall})")
if len(rows) < 100:
    raise SystemExit("too few joined samples — abort")

# ---------- pre-registered candidate filters ----------
def hour_of(r): return r["hour"]
FILTERS = [
    ("ALL fills (baseline)",        lambda r: True),
    ("near flip (<1%)",             lambda r: r["dist_flip_pct"] < 1.0),
    ("near flip (<2%)",             lambda r: r["dist_flip_pct"] < 2.0),
    ("strong wall (share>=0.40)",   lambda r: r["wall_share"] >= 0.40),
    ("dte 5-9",                     lambda r: 5 <= r["dte"] <= 9),
    ("hour 11:00-14:00",            lambda r: 11 <= r["hour"] < 14),
    ("index only",                  lambda r: r["is_index"] == 1),
    ("stock only",                  lambda r: r["is_index"] == 0),
    ("GOLD: near-flip<2% + hr11-14",lambda r: r["dist_flip_pct"] < 2.0 and 11 <= r["hour"] < 14),
    ("GOLD2: nf<2% + strongwall",   lambda r: r["dist_flip_pct"] < 2.0 and r["wall_share"] >= 0.35),
    ("GOLD3: nf<1% + dte5-9",       lambda r: r["dist_flip_pct"] < 1.0 and 5 <= r["dte"] <= 9),
]
import statistics as _st
print("\n===== CANDIDATE-FILTER EXPECTANCY (all recorded pin fills, both books) =====")
print(f"{'filter':32} {'n':>5} {'R/trade':>8} {'win%':>6} {'netR':>8}")
for name, fn in FILTERS:
    sub=[r for r in rows if fn(r)]
    if not sub: print(f"{name:32}  none"); continue
    rs=[r['r'] for r in sub]
    print(f"{name:32} {len(sub):5} {sum(rs)/len(rs):+8.3f} {100*sum(1 for x in rs if x>0)/len(rs):5.1f}% {sum(rs):+8.1f}")
print("\nBAR: a filter needs >= +0.10R/trade on n>=100 here to be a Gamma 2.0 candidate;")
print("then it must REPRODUCE at >= +0.10R/trade on >=30 forward shadow fills by Sep 1.")
import sys as _sys
_sys.exit(0)
FEATS = ["wall_rank", "wall_share", "n_walls", "oi_skew", "dist_flip_pct",
         "spot_wall_pct", "net_gex_sign", "sigma", "dte", "is_long", "hour",
         "mkt_runs", "is_index"]
X = np.array([[r[f] for f in FEATS] for r in rows])
y_win = np.array([r["win"] for r in rows])
y_die = np.array([r["died"] for r in rows])
groups = np.array([r["day"] for r in rows])
rs = np.array([r["r"] for r in rows])
print(f"base rates: win {y_win.mean()*100:.1f}% · instant-death {y_die.mean()*100:.1f}%"
      f" · mean R {rs.mean():+.3f} · days {len(set(groups))}")

# ---------- 4) interpretable buckets first ----------
def bucket(name, keyf, cuts=None):
    print(f"\n--- R/trade by {name} ---")
    groups2 = defaultdict(list)
    for r in rows:
        v = keyf(r)
        if cuts:
            lab = next((f"<{c}" for c in cuts if v < c), f">={cuts[-1]}")
        else:
            lab = str(v)
        groups2[lab].append(r["r"])
    for lab in sorted(groups2, key=lambda s: (len(s), s)):
        g2 = groups2[lab]
        print(f"  {lab:8} n={len(g2):4} · {np.mean(g2):+.3f}R/trade · win "
              f"{100*np.mean([1 if x>0 else 0 for x in g2]):4.1f}%")

bucket("wall_rank", lambda r: min(r["wall_rank"], 3))
bucket("wall_share", lambda r: r["wall_share"], cuts=[0.25, 0.35, 0.45])
bucket("dist_flip_pct", lambda r: r["dist_flip_pct"], cuts=[0.5, 1.0, 2.0])
bucket("dte", lambda r: r["dte"], cuts=[3, 6, 10])
bucket("oi_skew", lambda r: r["oi_skew"], cuts=[-0.3, 0.0, 0.3])
bucket("hour", lambda r: r["hour"], cuts=[10.5, 12.5, 14])

# ---------- 5) models, day-grouped CV ----------
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

for label, y in (("WIN (r>0)", y_win), ("INSTANT-DEATH", y_die)):
    print(f"\n===== predicting {label} — GroupKFold by day =====")
    for mname, mk in (("logistic", lambda: LogisticRegression(max_iter=2000, C=0.5)),
                      ("gbm-small", lambda: GradientBoostingClassifier(
                          n_estimators=60, max_depth=2, learning_rate=0.08,
                          subsample=0.8, random_state=7))):
        aucs = []
        gkf = GroupKFold(n_splits=min(5, len(set(groups))))
        for tr, te in gkf.split(X, y, groups):
            sc = StandardScaler().fit(X[tr])
            m = mk().fit(sc.transform(X[tr]), y[tr])
            p = m.predict_proba(sc.transform(X[te]))[:, 1]
            if len(set(y[te])) > 1:
                aucs.append(roc_auc_score(y[te], p))
        print(f"  {mname:10} AUC {np.mean(aucs):.3f} ± {np.std(aucs):.3f} "
              f"(chance = 0.500, folds {len(aucs)})")
    sc = StandardScaler().fit(X)
    m = LogisticRegression(max_iter=2000, C=0.5).fit(sc.transform(X), y)
    coef = sorted(zip(FEATS, m.coef_[0]), key=lambda kv: -abs(kv[1]))
    print("  top logistic weights:", ", ".join(f"{k} {v:+.2f}" for k, v in coef[:6]))

# ---------- 6) the money question: top-tercile model score vs rest ----------
print("\n===== economic check: trade only the model's top-scored tercile (day-CV) =====")
gkf = GroupKFold(n_splits=min(5, len(set(groups))))
picked, rest = [], []
for tr, te in gkf.split(X, y_win, groups):
    sc = StandardScaler().fit(X[tr])
    m = GradientBoostingClassifier(n_estimators=60, max_depth=2, learning_rate=0.08,
                                   subsample=0.8, random_state=7)
    m.fit(sc.transform(X[tr]), y_win[tr])
    p = m.predict_proba(sc.transform(X[te]))[:, 1]
    cut = np.quantile(p, 2 / 3)
    for i, idx in enumerate(te):
        (picked if p[i] >= cut else rest).append(rs[idx])
print(f"  top tercile : n={len(picked):4} · {np.mean(picked):+.3f}R/trade · "
      f"win {100*np.mean([1 if x>0 else 0 for x in picked]):.1f}%")
print(f"  bottom 2/3  : n={len(rest):4} · {np.mean(rest):+.3f}R/trade · "
      f"win {100*np.mean([1 if x>0 else 0 for x in rest]):.1f}%")
print("\nCAVEAT: ~9 trading days of data — day-clustered, regime-skewed (mostly the"
      "\nbleed week). Treat as feature-ranking pilot; re-run after 4-6 more weeks.")
