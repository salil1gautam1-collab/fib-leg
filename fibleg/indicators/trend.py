"""Streaming trend / chop indicators for setup-time gates (per symbol):
EMA (trend direction), Wilder ADX (trend strength / chop), rolling mean (volume)."""
from __future__ import annotations

from collections import deque

from ..models import Bar


class EmaStreamer:
    """Exponential moving average, one value at a time."""

    def __init__(self, period: int) -> None:
        self.k = 2.0 / (period + 1)
        self.ema: float | None = None

    def update(self, x: float) -> float:
        self.ema = x if self.ema is None else x * self.k + self.ema * (1 - self.k)
        return self.ema


class AdxStreamer:
    """Wilder's Average Directional Index — trend STRENGTH (direction-agnostic).
    ADX > ~25 = trending, < ~20 = range/chop. Returns 0 until warmed up."""

    def __init__(self, period: int = 14) -> None:
        self.p = period
        self._ph = self._pl = self._pc = None
        self._tr = self._pdm = self._ndm = None
        self.adx: float | None = None
        self._dx_sum = 0.0
        self._dx_n = 0

    def update(self, bar: Bar) -> float:
        if self._ph is None:
            self._ph, self._pl, self._pc = bar.high, bar.low, bar.close
            return 0.0
        up = bar.high - self._ph
        dn = self._pl - bar.low
        pdm = up if (up > dn and up > 0) else 0.0
        ndm = dn if (dn > up and dn > 0) else 0.0
        tr = max(bar.high - bar.low, abs(bar.high - self._pc), abs(bar.low - self._pc))
        self._ph, self._pl, self._pc = bar.high, bar.low, bar.close
        if self._tr is None:
            self._tr, self._pdm, self._ndm = tr, pdm, ndm
            return 0.0
        # Wilder smoothing (recursive)
        self._tr = self._tr - self._tr / self.p + tr
        self._pdm = self._pdm - self._pdm / self.p + pdm
        self._ndm = self._ndm - self._ndm / self.p + ndm
        if self._tr == 0:
            return self.adx or 0.0
        pdi = 100 * self._pdm / self._tr
        ndi = 100 * self._ndm / self._tr
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0.0
        if self.adx is None:
            self._dx_sum += dx
            self._dx_n += 1
            if self._dx_n >= self.p:
                self.adx = self._dx_sum / self._dx_n
            return self.adx or 0.0
        self.adx = (self.adx * (self.p - 1) + dx) / self.p
        return self.adx


class RollingMean:
    """Mean of the last `n` values (used for volume baseline)."""

    def __init__(self, n: int) -> None:
        self.n = n
        self._q: deque[float] = deque()
        self._s = 0.0

    def update(self, x: float) -> float:
        self._q.append(x)
        self._s += x
        if len(self._q) > self.n:
            self._s -= self._q.popleft()
        return self._s / len(self._q)
