"""Audit the fib leg drawing on real data: is the origin a true swing extreme, is
the leg END the actual highest-high / lowest-low, and do the levels compute right?

    python verify_fib.py "<csv>"
"""
import sys

from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import FibLeg, Side
from fibleg.strategy.fib_leg import FibLegEngine

csv = sys.argv[1]
cfg = StrategyConfig()


def audit(sym, tf_min, method):
    base = feeds.csv_multi(csv, [sym])[sym]
    bars = feeds.resample(base, tf_min)          # 1m -> tf
    eng = FibLegEngine(sym, cfg, method)
    for b in bars:
        eng.on_setup_bar(b)
    cl = eng.current_leg()
    if not cl:
        print("%-12s %dm %-7s: no leg" % (sym, tf_min, method))
        return
    o, e, side = cl
    up = side is Side.LONG
    leg = FibLeg(side, o.index, e.index, o.price, e.price)
    seg = bars[o.index:e.index + 1]              # bars spanning the leg
    real_hi = max(b.high for b in seg)
    real_lo = min(b.low for b in seg)
    # correctness checks
    if up:
        origin_ok = abs(o.price - real_lo) < 0.01     # origin should be the lowest low
        end_ok = abs(e.price - real_hi) < 0.01        # end should be the highest high
    else:
        origin_ok = abs(o.price - real_hi) < 0.01
        end_ok = abs(e.price - real_lo) < 0.01
    print("%-12s %dm %-7s  %.2f -> %.2f  %s" % (sym, tf_min, method, o.price, e.price,
                                                "long" if up else "short"))
    print("     leg spans %d bars (idx %d..%d, %s .. %s)" % (
        len(seg), o.index, e.index, seg[0].ts.date(), seg[-1].ts.date()))
    print("     origin == %s of the leg? %s   (origin %.2f, real %s %.2f)" % (
        "lowest low" if up else "highest high", "YES" if origin_ok else "NO",
        o.price, "low" if up else "high", real_lo if up else real_hi))
    print("     end    == %s of the leg? %s   (end %.2f, real %s %.2f)" % (
        "highest high" if up else "lowest low", "YES" if end_ok else "NO",
        e.price, "high" if up else "low", real_hi if up else real_lo))
    print("     levels: 0.382 entry %.2f | 0.5 %.2f | 0.618 %.2f | 0.786 SL %.2f | T1(1.0) %.2f | 1.272 %.2f" % (
        leg.retracement(0.382), leg.retracement(0.5), leg.retracement(0.618),
        leg.retracement(0.786), leg.extension(1.0), leg.extension(1.272)))
    print()


for sym in ("HDFCBANK.NS", "RELIANCE.NS", "INFY.NS"):
    for tf in (120, 240):
        audit(sym, tf, "book")
