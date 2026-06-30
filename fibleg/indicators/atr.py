"""Average True Range — Wilder smoothing. Streaming + batch."""
from __future__ import annotations

from typing import Iterable

from ..models import Bar


class AtrStreamer:
    """Feed one Bar at a time; get the running ATR back. Used by the live
    ZigZag so the noise floor adapts per symbol/volatility."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev_close: float | None = None
        self._atr: float | None = None
        self._n = 0

    def update(self, bar: Bar) -> float:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._prev_close = bar.close
        self._n += 1
        if self._atr is None:
            self._atr = tr
        elif self._n <= self.period:
            # simple average ramp until we have `period` samples
            self._atr = (self._atr * (self._n - 1) + tr) / self._n
        else:
            self._atr = (self._atr * (self.period - 1) + tr) / self.period
        return self._atr


def atr(bars: Iterable[Bar], period: int = 14) -> list[float]:
    s = AtrStreamer(period)
    return [s.update(b) for b in bars]
