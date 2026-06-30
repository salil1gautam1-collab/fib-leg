"""Adaptive threshold ZigZag — the careful, dynamic swing detector (design §1.5).

Why not fixed N-bar pivots: they confirm N bars late, so the fib would lag and
the TradingView drawing would disagree with the Python alert. Instead one
adaptive engine drives every anchor (breakout reference, fib start, fib end).

A swing high confirms the instant price pulls back more than
    threshold = max(leg_reversal_thresh * current_leg_range, atr_mult * ATR)
from the running high (mirror for lows). The `leg_reversal_thresh` IS the
strategy's leg-lock rule (0.382); the ATR term is just a noise floor.

The still-forming final leg is *provisional* — `provisional_leg()` exposes the
running extreme so the chart can draw it (dashed) and "stretch" it live, but the
strategy never acts on it until `update()` returns a confirmed Pivot.
"""
from __future__ import annotations

from typing import Optional

from ..models import Bar, Pivot, PivotType


class ZigZag:
    def __init__(self, leg_reversal_thresh: float = 0.382, atr_mult: float = 0.3) -> None:
        self.thresh = leg_reversal_thresh
        self.atr_mult = atr_mult
        self.pivots: list[Pivot] = []

        self._init = False
        self._trend = 1                       # +1 = tracking a high, -1 = tracking a low
        self._anchor_idx = 0                  # last confirmed pivot (opposite kind)
        self._anchor_price = 0.0
        self._ext_idx = 0                     # provisional running extreme = next pivot
        self._ext_price = 0.0
        self._ext_ts = None

    def update(self, index: int, bar: Bar, atr: float) -> Optional[Pivot]:
        """Advance one bar. Returns a Pivot the moment one is confirmed, else None."""
        if not self._init:
            # Bootstrap: assume an up-leg anchored at the first low. Self-corrects
            # within the first one or two confirmations.
            self._init = True
            self._trend = 1
            self._anchor_idx, self._anchor_price = index, bar.low
            self._ext_idx, self._ext_price, self._ext_ts = index, bar.high, bar.ts
            return None

        # A bar that EXTENDS the leg cannot also confirm its reversal — the
        # pullback must come on a later bar, else a wide range bar self-triggers.
        if self._trend == 1:
            if bar.high > self._ext_price:
                self._ext_price, self._ext_idx, self._ext_ts = bar.high, index, bar.ts
            else:
                leg = self._ext_price - self._anchor_price
                thr = max(self.thresh * leg, self.atr_mult * atr)
                if leg > 0 and (self._ext_price - bar.low) >= thr:
                    return self._flip(PivotType.HIGH, index, bar)
        else:
            if bar.low < self._ext_price:
                self._ext_price, self._ext_idx, self._ext_ts = bar.low, index, bar.ts
            else:
                leg = self._anchor_price - self._ext_price
                thr = max(self.thresh * leg, self.atr_mult * atr)
                if leg > 0 and (bar.high - self._ext_price) >= thr:
                    return self._flip(PivotType.LOW, index, bar)
        return None

    def _flip(self, kind: PivotType, index: int, bar: Bar) -> Pivot:
        piv = Pivot(self._ext_idx, self._ext_ts, self._ext_price, kind)
        self.pivots.append(piv)
        # the confirmed extreme becomes the new anchor; start tracking the other side
        self._anchor_idx, self._anchor_price = self._ext_idx, self._ext_price
        if kind is PivotType.HIGH:
            self._trend = -1
            self._ext_idx, self._ext_price, self._ext_ts = index, bar.low, bar.ts
        else:
            self._trend = 1
            self._ext_idx, self._ext_price, self._ext_ts = index, bar.high, bar.ts
        return piv

    @property
    def trend(self) -> int:
        """Current swing direction: +1 = in an up-move (tracking a high), -1 = down."""
        return self._trend

    def last_leg(self) -> tuple[float, float] | None:
        """(start_price, end_price) of the last CONFIRMED leg, or None."""
        if len(self.pivots) < 2:
            return None
        return self.pivots[-2].price, self.pivots[-1].price

    def provisional_leg(self) -> tuple[int, float, int, float]:
        """(anchor_idx, anchor_price, ext_idx, ext_price) of the live, unlocked
        leg — what the chart draws dashed and stretches."""
        return self._anchor_idx, self._anchor_price, self._ext_idx, self._ext_price


def dominant_impulses(pivots: list[Pivot]) -> list[tuple[Pivot, Pivot, int]]:
    """Segment ZigZag pivots into market-structure IMPULSES (design: the user's
    "fib start at the trend-change extreme").

    A trend reverses ONLY on confirmed structure — an up-trend needs a Higher-High
    AND a Higher-Low; a down-trend needs a Lower-High AND a Lower-Low. A single
    shallow pullback (one counter pivot) stays PART of the impulse, so the leg is
    anchored from the real trend-change low/high to the peak, not fragmented.

    Returns [(start_pivot, end_pivot, direction)], direction +1 up / -1 down.
    The LAST entry is the current dominant impulse.
    """
    if len(pivots) < 2:
        return []
    out: list[tuple[Pivot, Pivot, int]] = []
    d = 1 if pivots[1].price > pivots[0].price else -1
    origin = pivots[0]
    ext = pivots[0]
    prev_high: Pivot | None = None
    prev_low: Pivot | None = None
    higher_low = lower_high = False
    for p in pivots:
        if p.kind is PivotType.HIGH:
            hh = prev_high is not None and p.price > prev_high.price
            lh = prev_high is not None and p.price < prev_high.price
            if d == 1:
                if p.price > ext.price:
                    ext = p                      # extend the up-impulse peak
                if lh:
                    lower_high = True            # arm a potential down-reversal
            elif hh and higher_low:              # HH + HL confirms up-reversal
                out.append((origin, ext, -1))
                d, origin, ext = 1, ext, p
                higher_low = lower_high = False
            prev_high = p
        else:
            hl = prev_low is not None and p.price > prev_low.price
            ll = prev_low is not None and p.price < prev_low.price
            if d == -1:
                if p.price < ext.price:
                    ext = p                      # extend the down-impulse trough
                if hl:
                    higher_low = True            # arm a potential up-reversal
            elif ll and lower_high:              # LH + LL confirms down-reversal
                out.append((origin, ext, 1))
                d, origin, ext = -1, ext, p
                higher_low = lower_high = False
            prev_low = p
    out.append((origin, ext, d))
    return out


def compute_pivots(bars: list[Bar], leg_reversal_thresh: float = 0.382,
                   atr_mult: float = 0.3, atr_period: int = 14) -> list[Pivot]:
    """Batch helper — used by tests and the Pine-parity fixture."""
    from ..indicators.atr import AtrStreamer

    zz = ZigZag(leg_reversal_thresh, atr_mult)
    atr_s = AtrStreamer(atr_period)
    for i, b in enumerate(bars):
        zz.update(i, b, atr_s.update(b))
    return zz.pivots
