"""Book-method impulse tracker — TradeWisely Ch.4 leg rule + break-of-structure.

The leg only forms on a genuine trend change, the way the author trades it live:

  - A down-impulse isn't valid until price closes BELOW the previous swing LOW
    (break of structure). Only then does the 0.236 rule apply. An up-impulse needs
    a close ABOVE the previous swing HIGH. (You don't fade a high just because it
    retraced 78.6% — you wait for the prior low to actually break.)
  - When it flips, the origin RE-ANCHORS to the swing that started the move: a new
    down-impulse starts at the last swing HIGH (a lower-high, not a stale old top);
    a new up-impulse at the last swing LOW.
  - Then drag the extreme to each new low/high (the book's "extension move breaks
    the previous low/high"). 0.236 measured from the origin rises/falls as the
    extreme extends; a close back through it marks the impulse ENDED (`locked`),
    and we look for the 0.5/0.618 entry.

The previous swing low/high come in as confirmed ZigZag pivots (from the engine).
The 0.786 retrace is only a fallback for the warm-up before any swing exists.
"""
from __future__ import annotations

from ..models import Bar, Pivot, PivotType

UP, DOWN = 1, -1


class BookImpulse:
    def __init__(self, end_ratio: float = 0.236, reverse_ratio: float = 0.786,
                 on_close: bool = True, re_anchor_ratio: float | None = None,
                 htf_keep=None) -> None:
        self.end_ratio = end_ratio          # close-through here -> impulse ENDED (fib
        #                                     finalized, levels frozen; `locked`)
        # depth a retrace must CLOSE beyond before a NEW high/low re-anchors the origin
        # (book rule: 0.618 — the institutions' double-down zone). A pullback that ends
        # between end_ratio and here is the GRAY zone: origin is kept (trend remains)
        # unless the optional `htf_keep` callable votes otherwise. Defaults to end_ratio
        # (= every ended pullback re-anchors; the pre-book v5 behaviour).
        self.re_anchor_ratio = re_anchor_ratio if re_anchor_ratio is not None else end_ratio
        # gray-zone tiebreaker: callable -> True = the HIGHER timeframe still shows the
        # same impulse running (keep the origin), False = it ended upstairs (re-anchor).
        self.htf_keep = htf_keep
        self.reverse_ratio = reverse_ratio  # 0.786 fallback flip before structure exists
        self.on_close = on_close
        self._init = False
        self.dir = UP
        self._o_i, self._o_p, self._o_ts = 0, 0.0, None   # origin = leg start
        self._e_i, self._e_p, self._e_ts = 0, 0.0, None   # extreme = leg end
        self.locked = False
        self._retr = None   # (index, price, ts) of the retracement pivot since the impulse locked
        self._max_retr = 0.0   # deepest CLOSE-based retrace ratio since the extreme (wicks don't count)

    def _start(self, new_dir: int, origin: Pivot, index: int, bar: Bar) -> None:
        """Begin a fresh impulse anchored at `origin` (the swing that started it)."""
        self.dir = new_dir
        self._o_i, self._o_p, self._o_ts = origin.index, origin.price, origin.ts
        if new_dir == UP:
            self._e_i, self._e_p, self._e_ts = index, bar.high, bar.ts
        else:
            self._e_i, self._e_p, self._e_ts = index, bar.low, bar.ts
        self.locked = False
        self._retr = None
        self._max_retr = 0.0

    def update(self, index: int, bar: Bar,
               swing_low: Pivot | None = None, swing_high: Pivot | None = None) -> None:
        """swing_low/high = most recent CONFIRMED opposite swing pivots (structure)."""
        if not self._init:
            self._init = True
            self.dir = UP
            self._o_i, self._o_p, self._o_ts = index, bar.low, bar.ts
            self._e_i, self._e_p, self._e_ts = index, bar.high, bar.ts
            return

        px_down = bar.close if self.on_close else bar.low
        px_up = bar.close if self.on_close else bar.high

        if self.dir == UP:
            # BREAK OF STRUCTURE down: close below the previous swing low -> the new
            # down-impulse starts at the last swing HIGH (re-anchor the origin there).
            if (swing_low is not None and swing_high is not None
                    and swing_high.price > swing_low.price
                    and px_down < swing_low.price):
                self._start(DOWN, swing_high, index, bar)
                return
            # warm-up fallback (no structure yet): flip on a 0.786 failed retrace
            rng = self._e_p - self._o_p
            if swing_low is None and rng > 0 and px_down < self._e_p - self.reverse_ratio * rng:
                self._start(DOWN, Pivot(self._e_i, self._e_ts, self._e_p, PivotType.HIGH), index, bar)
                return
            if bar.high > self._e_p:                          # new high
                if self.locked and self._retr is not None:
                    # the impulse had ENDED (end_ratio broke). DEEP pullback (closed past
                    # re_anchor_ratio, 0.618) -> FRESH impulse from the retracement low.
                    # GRAY zone (ended but shallower than 0.618): the institutions
                    # defended and price made a new high — the trend remains, KEEP the
                    # origin — unless the higher timeframe says the impulse ended there.
                    deep = self._max_retr >= self.re_anchor_ratio
                    if deep or (self.htf_keep is not None and not self.htf_keep()):
                        self._o_i, self._o_p, self._o_ts = self._retr
                self._e_i, self._e_p, self._e_ts = index, bar.high, bar.ts
                self.locked = False
                self._retr = None
                self._max_retr = 0.0
            rng = self._e_p - self._o_p
            if rng > 0:
                self._max_retr = max(self._max_retr, (self._e_p - px_down) / rng)
                if px_down < self._e_p - self.end_ratio * rng:
                    self.locked = True
            if self.locked and (self._retr is None or bar.low < self._retr[1]):
                self._retr = (index, bar.low, bar.ts)     # track the retracement LOW while ended
        else:  # DOWN (mirror)
            if (swing_high is not None and swing_low is not None
                    and swing_low.price < swing_high.price
                    and px_up > swing_high.price):
                self._start(UP, swing_low, index, bar)
                return
            rng = self._o_p - self._e_p
            if swing_high is None and rng > 0 and px_up > self._e_p + self.reverse_ratio * rng:
                self._start(UP, Pivot(self._e_i, self._e_ts, self._e_p, PivotType.LOW), index, bar)
                return
            if bar.low < self._e_p:                           # new low
                if self.locked and self._retr is not None:
                    deep = self._max_retr >= self.re_anchor_ratio
                    if deep or (self.htf_keep is not None and not self.htf_keep()):
                        self._o_i, self._o_p, self._o_ts = self._retr   # re-anchor to the retracement HIGH
                self._e_i, self._e_p, self._e_ts = index, bar.low, bar.ts
                self.locked = False
                self._retr = None
                self._max_retr = 0.0
            rng = self._o_p - self._e_p
            if rng > 0:
                self._max_retr = max(self._max_retr, (px_up - self._e_p) / rng)
                if px_up > self._e_p + self.end_ratio * rng:
                    self.locked = True
            if self.locked and (self._retr is None or bar.high > self._retr[1]):
                self._retr = (index, bar.high, bar.ts)    # track the retracement HIGH while ended

    def current_leg(self) -> tuple[Pivot, Pivot, int] | None:
        if not self._init or self._o_p == self._e_p:
            return None
        ok = PivotType.LOW if self.dir == UP else PivotType.HIGH
        ek = PivotType.HIGH if self.dir == UP else PivotType.LOW
        origin = Pivot(self._o_i, self._o_ts, self._o_p, ok)
        extreme = Pivot(self._e_i, self._e_ts, self._e_p, ek)
        return origin, extreme, self.dir


def track(bars: list[Bar], end_ratio: float = 0.236,
          reverse_ratio: float = 0.786, on_close: bool = True,
          atr_mult: float = 1.5) -> BookImpulse:
    """Standalone driver — runs a ZigZag alongside to supply the swing structure."""
    from ..indicators.atr import AtrStreamer
    from .pivots import ZigZag

    bi = BookImpulse(end_ratio, reverse_ratio, on_close)
    zz = ZigZag(end_ratio, atr_mult)
    atr = AtrStreamer()
    for i, b in enumerate(bars):
        zz.update(i, b, atr.update(b))
        lo = next((p for p in reversed(zz.pivots) if p.kind is PivotType.LOW), None)
        hi = next((p for p in reversed(zz.pivots) if p.kind is PivotType.HIGH), None)
        bi.update(i, b, lo, hi)
    return bi
