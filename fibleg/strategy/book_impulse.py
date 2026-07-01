"""Book-method impulse tracker — the TradeWisely Ch.4 leg rule as a state machine.

An alternative to the adaptive ZigZag, selectable in Settings so the user can
A/B which draws the leg better. It follows the author's own words (Ch.4):

  - Draw from the impulse's lowest low (incl. wick) -> the running high. In the
    book's convention level 0 = the HIGH, so 0.236 sits 23.6% BELOW it.
  - Keep dragging the top to each NEW high (extension / continuation).
  - 0.236 is measured from the TRUE origin, so it RISES as the high extends -- a
    shallow pullback can't lock the leg early. When price closes below 0.236 the
    impulse has ENDED (0.382 confirms) -> `locked` True; now we look for the
    0.5/0.618 continuation entry.
  - The trend only REVERSES when a retracement FAILS: price closes beyond 0.786
    (past the entry zone, into SL territory). Then the extreme becomes the origin
    of the new leg in the opposite direction. Until then a fresh push to a new
    high is just an extension of the same impulse (continuation from 0.5/0.618).

0.786 is the reversal trigger on purpose: it's the same level the strategy uses
for its stop, and it fixes the naive version's flaw (flipping on any dip below a
stale origin, which lost the trend). Mirror everything for a down-impulse.
"""
from __future__ import annotations

from ..models import Bar, Pivot, PivotType

UP, DOWN = 1, -1


class BookImpulse:
    def __init__(self, end_ratio: float = 0.236, reverse_ratio: float = 0.786,
                 on_close: bool = True) -> None:
        self.end_ratio = end_ratio          # 0.236 -> impulse ends (leg top fixed)
        self.reverse_ratio = reverse_ratio  # 0.786 -> retrace failed, trend flips
        self.on_close = on_close
        self._init = False
        self.dir = UP
        self._o_i, self._o_p, self._o_ts = 0, 0.0, None   # origin = leg start
        self._e_i, self._e_p, self._e_ts = 0, 0.0, None   # extreme = leg end
        self.locked = False                 # has 0.236 been broken (impulse ended)?

    def update(self, index: int, bar: Bar) -> None:
        if not self._init:
            self._init = True
            self.dir = UP
            self._o_i, self._o_p, self._o_ts = index, bar.low, bar.ts
            self._e_i, self._e_p, self._e_ts = index, bar.high, bar.ts
            return

        px_down = bar.close if self.on_close else bar.low
        px_up = bar.close if self.on_close else bar.high

        if self.dir == UP:
            rng = self._e_p - self._o_p
            # retracement failed past 0.786 -> uptrend reverses; the high we made
            # becomes the origin of the new down-impulse.
            if rng > 0 and px_down < self._e_p - self.reverse_ratio * rng:
                self._flip(DOWN, index, bar)
                return
            # drag the top to each new high (extension / continuation)
            if bar.high > self._e_p:
                self._e_i, self._e_p, self._e_ts = index, bar.high, bar.ts
                self.locked = False
            # 0.236 from the TRUE origin broken down -> impulse ended
            rng = self._e_p - self._o_p
            if rng > 0 and px_down < self._e_p - self.end_ratio * rng:
                self.locked = True
        else:  # DOWN (mirror)
            rng = self._o_p - self._e_p
            if rng > 0 and px_up > self._e_p + self.reverse_ratio * rng:
                self._flip(UP, index, bar)
                return
            if bar.low < self._e_p:
                self._e_i, self._e_p, self._e_ts = index, bar.low, bar.ts
                self.locked = False
            rng = self._o_p - self._e_p
            if rng > 0 and px_up > self._e_p + self.end_ratio * rng:
                self.locked = True

    def _flip(self, new_dir: int, index: int, bar: Bar) -> None:
        # extreme (the old leg's end) becomes the new leg's origin
        self._o_i, self._o_p, self._o_ts = self._e_i, self._e_p, self._e_ts
        self.dir = new_dir
        px = bar.low if new_dir == UP else bar.high  # start tracking the new extreme
        # for an UP flip we now track a high; for DOWN, a low
        if new_dir == UP:
            self._e_i, self._e_p, self._e_ts = index, bar.high, bar.ts
        else:
            self._e_i, self._e_p, self._e_ts = index, bar.low, bar.ts
        self.locked = False

    def current_leg(self) -> tuple[Pivot, Pivot, int] | None:
        if not self._init or self._o_p == self._e_p:
            return None
        ok = PivotType.LOW if self.dir == UP else PivotType.HIGH
        ek = PivotType.HIGH if self.dir == UP else PivotType.LOW
        origin = Pivot(self._o_i, self._o_ts, self._o_p, ok)
        extreme = Pivot(self._e_i, self._e_ts, self._e_p, ek)
        return origin, extreme, self.dir


def track(bars: list[Bar], end_ratio: float = 0.236,
          reverse_ratio: float = 0.786, on_close: bool = True) -> BookImpulse:
    bi = BookImpulse(end_ratio, reverse_ratio, on_close)
    for i, b in enumerate(bars):
        bi.update(i, b)
    return bi
