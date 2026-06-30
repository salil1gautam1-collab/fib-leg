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
from .pivots import ZigZag


class FibLegEngine:
    def __init__(self, symbol: str, cfg: StrategyConfig | None = None) -> None:
        self.symbol = symbol
        self.cfg = cfg or StrategyConfig()
        self.zz = ZigZag(self.cfg.leg_reversal_thresh, self.cfg.atr_mult)
        self.atr = AtrStreamer(self.cfg.atr_period)
        self.pivots: list[Pivot] = []
        self.active: Optional[Setup] = None
        self.trades: list[Trade] = []
        self.signals: list[Signal] = []
        self._si = -1                     # setup-bar index (1H)
        self._ti = -1                     # trigger-bar index (15m)
        self._prev_trig: Bar | None = None

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

    # -- leg / setup construction ----------------------------------------
    def _ingest(self, bar: Bar) -> None:
        self._si += 1
        a = self.atr.update(bar)
        piv = self.zz.update(self._si, bar, a)
        if piv is not None:
            self.pivots.append(piv)
            self._on_pivot(piv)

    def _on_pivot(self, piv: Pivot) -> None:
        highs = [p for p in self.pivots if p.kind is PivotType.HIGH]
        lows = [p for p in self.pivots if p.kind is PivotType.LOW]

        if piv.kind is PivotType.HIGH and lows:
            start = lows[-1]                                  # origin swing low
            prior = highs[-2] if len(highs) >= 2 else None    # previous swing high
            broke = (not self.cfg.require_breakout) or (prior is None) or (piv.price > prior.price)
            if broke and piv.price > start.price:
                self._open_setup(Side.LONG, start, piv)

        elif piv.kind is PivotType.LOW and highs:
            start = highs[-1]
            prior = lows[-2] if len(lows) >= 2 else None
            broke = (not self.cfg.require_breakout) or (prior is None) or (piv.price < prior.price)
            if broke and piv.price < start.price:
                self._open_setup(Side.SHORT, start, piv)

    def _open_setup(self, side: Side, start: Pivot, end: Pivot) -> None:
        leg = FibLeg(side, start.index, end.index, start.price, end.price)
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
                   reason, exit_ts=bar.ts, realized_points=points)
        self.trades.append(tr)
        self.active = None
        return tr
