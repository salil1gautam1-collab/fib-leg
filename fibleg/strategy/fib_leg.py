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
from .pivots import ZigZag, dominant_impulses


class FibLegEngine:
    def __init__(self, symbol: str, cfg: StrategyConfig | None = None) -> None:
        self.symbol = symbol
        self.cfg = cfg or StrategyConfig()
        self.zz = ZigZag(self.cfg.leg_reversal_thresh, self.cfg.atr_mult)
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
        work = list(self.pivots)
        prov = self.zz.provisional_pivot()
        if prov is not None and (not work or prov.index > work[-1].index):
            work.append(prov)
        imps = dominant_impulses(work)
        if not imps:
            return None
        start, end, d = imps[-1]
        if start.price == end.price:
            return None
        return start, end, (Side.LONG if d == 1 else Side.SHORT)

    def mw_confirmed(self, leg: FibLeg) -> bool:
        """TradeWisely Ch.4: the impulse top is an M (double-top) / the bottom is a
        W (double-bottom), with the neckline beyond the 0.236 level — confirms the
        impulse has actually topped/bottomed, not just a single-bar spike."""
        rng = leg.rng
        if rng <= 0:
            return False
        tol = 0.012 * abs(leg.end_price)                 # the two peaks within ~1.2%
        near = [p for p in self.pivots if p.index >= leg.start_index]
        if leg.side is Side.LONG:
            tops = [p for p in near if p.kind is PivotType.HIGH
                    and abs(p.price - leg.end_price) <= tol]
            if len(tops) < 2:
                return False
            lvl = leg.end_price - 0.236 * rng            # neckline must dip below 0.236
            return any(p.kind is PivotType.LOW and p.price < lvl
                       and p.index >= tops[0].index for p in near)
        bottoms = [p for p in near if p.kind is PivotType.LOW
                   and abs(p.price - leg.end_price) <= tol]
        if len(bottoms) < 2:
            return False
        lvl = leg.end_price + 0.236 * rng
        return any(p.kind is PivotType.HIGH and p.price > lvl
                   and p.index >= bottoms[0].index for p in near)

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
        self._update_leg()                       # every bar: track the live impulse

    def _update_leg(self) -> None:
        # the active leg = the current DOMINANT market-structure impulse, anchored
        # at the real trend-change extreme. The provisional (live) extreme is added
        # so the leg END tracks the latest high/low before a pullback confirms it.
        work = list(self.pivots)
        prov = self.zz.provisional_pivot()
        if prov is not None and (not work or prov.index > work[-1].index):
            work.append(prov)
        imps = dominant_impulses(work)
        if not imps:
            return
        start, end, d = imps[-1]
        if start.price == end.price:
            return
        side = Side.LONG if d == 1 else Side.SHORT

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
        leg = FibLeg(side, start.index, end.index, start.price, end.price)
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
                    s.state = SetupState.IN_TRADE

        elif s.state is SetupState.IN_TRADE:
            out += self._manage(bar)

        return out

    def _manage(self, bar: Bar) -> list[object]:
        s = self.active
        assert s is not None and s.entry_fill is not None
        long = s.side is Side.LONG
        risk = s.entry_risk or 1e-9   # frozen at entry; immune to SL-to-BE moves

        sl_hit = bar.low <= s.sl_price if long else bar.high >= s.sl_price
        if sl_hit:
            r = (s.sl_price - s.entry_fill) / risk if long else (s.entry_fill - s.sl_price) / risk
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
                   reason, exit_ts=bar.ts, realized_points=points, leg=s.leg)
        self.trades.append(tr)
        self.active = None
        return tr
