"""FibLegEngine — the per-symbol state machine (design §1).

Dual-timeframe (design §1 / §4):
  - SETUP timeframe (1H): the ZigZag detects legs and locks levels; setups are
    opened here. Driven by `on_setup_bar`.
  - TRIGGER timeframe (15m): pullback to 0.5, the reversal entry, and all trade
    management (SL / partial targets) happen here. Driven by `on_trigger_bar`.

`on_bar` runs both on a single stream (used by the synthetic backtest + tests).

The leg detection (stretch + 0.382 lock) lives in the ZigZag, so nothing the
engine trades on repaints.
"""
from __future__ import annotations

from typing import Optional

from ..config import StrategyConfig
from ..indicators.atr import AtrStreamer
from ..models import (Bar, FibLeg, Pivot, PivotType, Setup, SetupState, Side,
                      Signal, Target, Trade)
from . import trigger
from .book_impulse import BookImpulse
from .pivots import ZigZag, dominant_impulses


class FibLegEngine:
    def __init__(self, symbol: str, cfg: StrategyConfig | None = None,
                 method: str = "adaptive") -> None:
        self.symbol = symbol
        self.cfg = cfg or StrategyConfig()
        self.method = method              # 'adaptive' (ZigZag) or 'book' (0.236-from-origin)
        self.zz = ZigZag(self.cfg.leg_reversal_thresh, self.cfg.atr_mult)
        # parallel book-method tracker (cheap; only consulted when method=='book')
        self._book = BookImpulse(self.cfg.leg_reversal_thresh, self.cfg.sl_ratio,
                                 self.cfg.sl_on_close)
        self.atr = AtrStreamer(self.cfg.atr_period)
        # parallel higher-timeframe ZigZags (2H/3H/4H…) for the impulse double-check
        self._htf = {
            f: {"zz": ZigZag(self.cfg.leg_reversal_thresh, self.cfg.atr_mult),
                "atr": AtrStreamer(self.cfg.atr_period), "bucket": [], "i": -1}
            for f in self.cfg.htf_factors
        }
        self.pivots: list[Pivot] = []
        self.active: Optional[Setup] = None
        self.trades: list[Trade] = []
        self._retired: set = set()        # legs already traded — don't re-enter the same failed impulse
        self.signals: list[Signal] = []
        self._si = -1                     # setup-bar index (1H)
        self._ti = -1                     # trigger-bar index (15m)
        self._prev_trig: Bar | None = None
        self._atr = 0.0                   # latest setup-TF ATR (for leg significance)

    # -- public entry points ---------------------------------------------
    def on_setup_bar(self, bar: Bar) -> list[object]:
        """1H bar: advance leg detection / open setups only."""
        self._ingest(bar)
        return []

    def on_trigger_bar(self, bar: Bar) -> list[object]:
        """15m bar: advance the trade state machine."""
        self._ti += 1
        out = self._advance(bar, self._prev_trig)
        self._prev_trig = bar
        return out

    def on_bar(self, bar: Bar) -> list[object]:
        """Single-timeframe convenience (synthetic backtest + tests)."""
        self._ingest(bar)
        self._ti += 1
        out = self._advance(bar, self._prev_trig)
        self._prev_trig = bar
        return out

    def current_leg(self) -> tuple[Pivot, Pivot, "Side"] | None:
        """The current dominant impulse (start_pivot, end_pivot, side) for ANY
        symbol — even with no active setup. Used by the batch validation view."""
        r = self._impulse()
        if r is None:
            return None
        start, end, d = r
        if start.price == end.price:
            return None
        return start, end, (Side.LONG if d == 1 else Side.SHORT)

    def mw_confirmed(self, leg: FibLeg) -> bool:
        """TradeWisely Ch.4 — the REVERSAL that starts this impulse. A fresh impulse
        begins where the previous trend flipped: an up-move ends with an M (double-
        TOP) that kicks off a DOWN impulse; a down-move ends with a W (double-BOTTOM)
        that kicks off an UP impulse. So the structure sits at the leg's ORIGIN:
          - LONG  (up impulse)  -> a W (double-bottom) at the origin low
          - SHORT (down impulse) -> an M (double-top)  at the origin high
        Confirms we're drawing the fib on a genuine trend change, not mid-trend."""
        rng = leg.rng
        if rng <= 0:
            return False
        origin = leg.start_price                          # the reversal point
        tol = 0.012 * abs(origin)                          # the two feet within ~1.2%
        near = [p for p in self.pivots if p.index <= leg.end_index]
        if leg.side is Side.LONG:
            # W: >=2 lows at the origin, then a neckline high clearing 0.236 up into the leg
            feet = [p for p in near if p.kind is PivotType.LOW
                    and abs(p.price - origin) <= tol]
            if len(feet) < 2:
                return False
            lvl = origin + 0.236 * rng
            return any(p.kind is PivotType.HIGH and p.price > lvl
                       and p.index >= feet[0].index for p in near)
        # M: >=2 highs at the origin, then a neckline low breaking 0.236 down into the leg
        feet = [p for p in near if p.kind is PivotType.HIGH
                and abs(p.price - origin) <= tol]
        if len(feet) < 2:
            return False
        lvl = origin - 0.236 * rng
        return any(p.kind is PivotType.LOW and p.price < lvl
                   and p.index >= feet[0].index for p in near)

    def ew_confirmed(self, leg: FibLeg) -> bool:
        """Heuristic Elliott-Wave check: does the impulse subdivide into a clean
        5-wave structure (1-up, 2-down, 3-up, 4-down, 5-up) obeying the three hard
        EW rules? Strict on purpose — it fires only on a textbook impulse, so it's
        a high-confidence bonus flag alongside M/W, not a common one.

        Rules: (1) wave 2 doesn't retrace past the origin; (2) wave 3 is not the
        shortest of 1/3/5; (3) wave 4 doesn't overlap wave 1's territory.
        """
        inner = [p for p in self.pivots
                 if leg.start_index < p.index < leg.end_index]
        if len(inner) != 4:                     # 4 inner pivots => exactly 5 waves
            return False
        up = leg.side is Side.LONG
        want = ([PivotType.HIGH, PivotType.LOW, PivotType.HIGH, PivotType.LOW] if up
                else [PivotType.LOW, PivotType.HIGH, PivotType.LOW, PivotType.HIGH])
        if [p.kind for p in inner] != want:
            return False
        o, top = leg.start_price, leg.end_price
        p1, p2, p3, p4 = (p.price for p in inner)
        if up:
            w1, w2, w3, w4, w5 = p1 - o, p1 - p2, p3 - p2, p3 - p4, top - p4
            if p2 <= o or p4 <= p1:             # rule 1 (w2) & rule 3 (w4 overlap)
                return False
        else:
            w1, w2, w3, w4, w5 = o - p1, p2 - p1, p2 - p3, p4 - p3, p4 - top
            if p2 >= o or p4 >= p1:
                return False
        if min(w1, w3, w5) <= 0 or min(w2, w4) <= 0:   # every wave must have length
            return False
        return not (w3 < w1 or w3 < w5)         # rule 2: wave 3 not the shortest

    def htf_confirms(self, leg: FibLeg) -> bool:
        """Does this impulse show up as a same-direction swing on ANY higher
        timeframe (2H/3H/4H)? Your "double-check" — validates the leg is a real
        impulse on the bigger picture, not a 1H-only blip. Counter-trend legs can
        still confirm (it checks for the swing, not HTF trend agreement)."""
        lo, hi = min(leg.start_price, leg.end_price), max(leg.start_price, leg.end_price)
        span = hi - lo
        if span <= 0:
            return False
        want_up = leg.side is Side.LONG
        for h in self._htf.values():
            for o, e, d in dominant_impulses(h["zz"].pivots)[-4:]:
                if (d == 1) != want_up:
                    continue
                i_lo, i_hi = min(o.price, e.price), max(o.price, e.price)
                if max(0.0, min(hi, i_hi) - max(lo, i_lo)) >= 0.5 * span:
                    return True
        return False

    # -- leg / setup construction ----------------------------------------
    def _ingest(self, bar: Bar) -> None:
        self._si += 1
        a = self.atr.update(bar)
        self._atr = a
        # aggregate into each higher timeframe and feed its ZigZag
        for f, h in self._htf.items():
            h["bucket"].append(bar)
            if len(h["bucket"]) >= f:
                g = h["bucket"]
                agg = Bar(g[-1].ts, g[0].open, max(b.high for b in g),
                          min(b.low for b in g), g[-1].close)
                h["bucket"] = []
                h["i"] += 1
                h["zz"].update(h["i"], agg, h["atr"].update(agg))
        piv = self.zz.update(self._si, bar, a)
        if piv is not None:
            self.pivots.append(piv)
        self._book.update(self._si, bar)         # keep the book-method leg current too
        self._update_leg()                       # every bar: track the live impulse

    @staticmethod
    def _leg_sig(side: Side, start_price: float, end_price: float) -> tuple:
        return (side, round(start_price, 2), round(end_price, 2))

    def _impulse(self) -> tuple[Pivot, Pivot, int] | None:
        """The current dominant impulse (start, end, dir) under the SELECTED leg
        method: 'adaptive' = ZigZag + market-structure dominant_impulses (default);
        'book' = the TradeWisely 0.236-from-origin tracker."""
        if self.method == "book":
            return self._book.current_leg()
        # adaptive: market-structure impulse anchored at the real trend-change
        # extreme; the provisional (live) extreme keeps the leg END current.
        work = list(self.pivots)
        prov = self.zz.provisional_pivot()
        if prov is not None and (not work or prov.index > work[-1].index):
            work.append(prov)
        imps = dominant_impulses(work)
        if not imps:
            return None
        return imps[-1]

    def _update_leg(self) -> None:
        r = self._impulse()
        if r is None:
            return
        start, end, d = r
        if start.price == end.price:
            return
        side = Side.LONG if d == 1 else Side.SHORT

        # this exact impulse already produced a trade -> don't re-enter it. A leg
        # that later EXTENDS (new high/low) has a different signature and is allowed.
        if self._leg_sig(side, start.price, end.price) in self._retired:
            return

        # never disturb a setup that has already signalled / filled
        if self.active and self.active.state in (SetupState.SIGNALED, SetupState.IN_TRADE):
            return
        # same leg already active -> nothing to do
        if (self.active and self.active.side is side
                and abs(self.active.leg.start_price - start.price) < 1e-6
                and abs(self.active.leg.end_price - end.price) < 1e-6):
            return
        self._open_setup(side, start, end)

    def _open_setup(self, side: Side, start: Pivot, end: Pivot) -> None:
        leg = FibLeg(side, start.index, end.index, start.price, end.price, start.ts, end.ts)
        # only MAJOR impulses become setups — filters out micro-legs so the fib
        # anchors at real trend-change extremes (your point: not every breakout)
        if self._atr > 0 and leg.rng < self.cfg.min_leg_atr * self._atr:
            return
        entry = leg.retracement(self.cfg.entry_ratio)
        sl = leg.retracement(self.cfg.sl_ratio)
        targets = [
            Target(r, leg.extension(r), f)
            for r, f in zip(self.cfg.targets, self.cfg.target_fractions)
        ]
        # a fresh, stronger leg supersedes a setup that hasn't filled yet
        if self.active and self.active.state in (SetupState.WAITING_PULLBACK, SetupState.ARMED):
            self.active = None
        if self.active is None:
            self.active = Setup(self.symbol, side, leg, entry, sl, targets,
                                created_index=self._si)

    # -- state machine (runs on the trigger timeframe) -------------------
    def _advance(self, bar: Bar, prev: Bar | None) -> list[object]:
        s = self.active
        if s is None:
            return []
        out: list[object] = []
        long = s.side is Side.LONG
        leg_end = s.leg.end_price

        if s.state is SetupState.WAITING_PULLBACK:
            reclaimed = bar.high >= leg_end if long else bar.low <= leg_end
            if reclaimed:
                s.state = SetupState.INVALID
                self.active = None
                return out
            reached = bar.low <= s.entry_price if long else bar.high >= s.entry_price
            if reached:
                s.state = SetupState.ARMED

        elif s.state is SetupState.ARMED:
            reclaimed = bar.high >= leg_end if long else bar.low <= leg_end
            closed_beyond_sl = bar.close < s.sl_price if long else bar.close > s.sl_price
            if reclaimed or closed_beyond_sl:
                s.state = SetupState.INVALID
                self.active = None
                return out
            if trigger.is_reversal(s.side, bar, prev):
                trig = trigger.trigger_price(s.side, bar)
                has_room = trig < leg_end if long else trig > leg_end
                risk_ok = abs(trig - s.sl_price) >= self.cfg.min_risk_frac * s.leg.rng
                if has_room and risk_ok:
                    s.trigger_price = trig
                    s.state = SetupState.SIGNALED
                    sig = Signal(self.symbol, s.side, s.leg, trig, s.sl_price,
                                 [t.price for t in s.targets], bar.ts,
                                 note=f"{self.cfg.entry_ratio:g} pullback + 15m reversal")
                    self.signals.append(sig)
                    out.append(sig)

        elif s.state is SetupState.SIGNALED:
            s.signaled_age += 1
            if s.signaled_age > self.cfg.signal_expiry_bars:
                s.state = SetupState.INVALID
                self.active = None
            else:
                filled = bar.high >= s.trigger_price if long else bar.low <= s.trigger_price
                if filled:
                    s.entry_fill = s.trigger_price
                    s.entry_price = s.trigger_price
                    s.entry_risk = abs(s.entry_price - s.sl_price)   # freeze R denominator
                    s.entry_index = self._ti
                    s.entry_ts = bar.ts
                    s.state = SetupState.IN_TRADE

        elif s.state is SetupState.IN_TRADE:
            out += self._manage(bar)

        return out

    def _manage(self, bar: Bar) -> list[object]:
        s = self.active
        assert s is not None and s.entry_fill is not None
        long = s.side is Side.LONG
        risk = s.entry_risk or 1e-9   # frozen at entry; immune to SL-to-BE moves

        # stop on a 15m CLOSE beyond the level (default) — not an intrabar wick
        if self.cfg.sl_on_close:
            sl_hit = bar.close <= s.sl_price if long else bar.close >= s.sl_price
        else:
            sl_hit = bar.low <= s.sl_price if long else bar.high >= s.sl_price
        if sl_hit:
            exit_px = bar.close if self.cfg.sl_on_close else s.sl_price   # close-stop fills at close
            r = (exit_px - s.entry_fill) / risk if long else (s.entry_fill - exit_px) / risk
            s.realized_r += s.remaining * r
            s.remaining = 0.0
            return [self._close(bar, "sl")]

        for t in s.targets:
            if t.hit:
                continue
            reached = bar.high >= t.price if long else bar.low <= t.price
            if reached:
                t.hit = True
                r = (t.price - s.entry_fill) / risk if long else (s.entry_fill - t.price) / risk
                s.realized_r += t.fraction * r
                s.remaining -= t.fraction
                if t is s.targets[0] and self.cfg.move_sl_to_be_after_tp1:
                    s.sl_price = s.entry_fill
        if s.remaining <= 1e-9:
            return [self._close(bar, "targets")]
        return []

    def _close(self, bar: Bar, reason: str) -> Trade:
        s = self.active
        assert s is not None
        s.state = SetupState.CLOSED
        s.exit_index = self._ti
        s.exit_reason = reason
        points = round(s.realized_r * s.entry_risk, 2)   # R * per-unit risk = net points
        tr = Trade(self.symbol, s.side, s.entry_fill or s.entry_price, s.sl_price,
                   s.entry_index or s.created_index, self._ti, round(s.realized_r, 2),
                   reason, entry_ts=s.entry_ts, exit_ts=bar.ts,
                   realized_points=points, leg=s.leg)
        self.trades.append(tr)
        self._retired.add(self._leg_sig(s.side, s.leg.start_price, s.leg.end_price))
        self.active = None
        return tr
