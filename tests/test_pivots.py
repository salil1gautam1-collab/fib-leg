"""ZigZag pivot detection — the careful, dynamic leg logic (design §1.5)."""
from datetime import datetime, timedelta

from fibleg.models import Bar, PivotType
from fibleg.strategy.pivots import compute_pivots


def _bars(seq):
    """seq = list of (high, low, close); open = prev close."""
    out = []
    t = datetime(2024, 1, 1, 9, 0)
    prev = seq[0][2]
    for h, l, c in seq:
        out.append(Bar(t, prev, h, l, c, 1000))
        prev = c
        t += timedelta(hours=1)
    return out


def test_locks_high_after_382_pullback():
    # rise 100 -> 110 (leg = 10), then a 60% pullback (>38.2%) must lock the high
    seq = [
        (100, 100, 100), (102, 100, 102), (104, 102, 104), (106, 104, 106),
        (108, 106, 108), (110, 108, 110),          # running high = 110
        (109, 104, 105),                            # pulls back 6 of 10 -> lock
    ]
    pivots = compute_pivots(_bars(seq))
    assert pivots, "expected at least one confirmed pivot"
    assert pivots[0].kind is PivotType.HIGH
    assert abs(pivots[0].price - 110) < 1e-6
    assert pivots[0].index == 5


def test_shallow_pullback_does_not_lock():
    # only a 20% pullback (<38.2%) -> the leg keeps stretching, no pivot yet
    seq = [
        (100, 100, 100), (104, 100, 104), (108, 104, 108), (110, 108, 110),
        (110, 108, 109),                            # pulls back 2 of 10 = 20%
    ]
    pivots = compute_pivots(_bars(seq))
    assert pivots == [], "shallow pullback must not confirm a pivot"


def test_alternation_high_then_low():
    seq = [
        (100, 100, 100), (105, 100, 105), (110, 105, 110),   # up-leg
        (109, 103, 104),                                      # lock HIGH @110
        (104, 100, 101),                                      # down continues
        (103, 99, 100), (108, 99, 107),                       # bounce -> lock LOW
    ]
    pivots = compute_pivots(_bars(seq))
    kinds = [p.kind for p in pivots]
    assert kinds[0] is PivotType.HIGH
    assert PivotType.LOW in kinds
    # strict alternation
    for a, b in zip(kinds, kinds[1:]):
        assert a is not b
