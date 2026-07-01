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
        self.method = method              # 'adaptive' | 'book' (0.236) | 'book382' (0.382)
        # 'book382' keeps the fib active until 0.382 is broken (looser lock -> the
        # impulse rides deeper pullbacks): a wider swing + impulse-end threshold.
        thresh = 0.382 if method == "book382" else self.cfg.leg_reversal_thresh
        self.zz = ZigZag(thresh, self.cfg.atr_mult)
        # parallel book-method tracker (cheap; only consulted for a 'book*' method)
        self._book = BookImpulse(thresh, self.cfg.sl_ratio, self.cfg.sl_on_close)
        self.atr = AtrStreamer(self.cfg.atr_period)
        # parallel higher-timeframe ZigZags (2H/3H/4H…) for the impulse double-check
        self._htf = {
            f: {"zz": ZigZag(thresh, self.cfg.atr_mult),
                "atr": AtrStreamer(self.cfg.atr_period), "bucket": [], "i": -1}
            for f in self.cfg.htf_factors
        }
        self.pivots: list[Pivot] = []
        self._setup_bars: list[Bar] = []   # detection-TF OHLC, indexed by _si (for pin-bar PA)
        self.active: Optional[Setup] = None
        self.trades: list[Trade] = []
        self._retired: set = set()        # legs already traded — don't re-enter the same failed impulse
        self.signals: list[Signal] = []
        self._si = -1                     # setup-bar index (1H)
        self._ti = -1                     # trigger-bar index (15m)
        self._prev_trig: Bar | None = None
        self._atr = 0.0                   # latest setup-TF ATR (for leg significance)
        self._n_lo = self._n_hi = None    # nested-fib bounce leg on the trigger TF
        self._n_bounced = False
        self._dbg: list = []              # ARM/FILL trace (diagnostics)

    # -- public entry points ---------------------------------------------
    def on_setup_bar(self, bar: Bar) -> list[object]:
        """Detection-TF (4H/3H/2H…) bar: advance leg detection, open setups, and judge
        the zone-respect (pullback) — the respect is a DETECTION-TF close event."""
        self._ingest(bar)
        return self._advance_pullback(bar)

    def on_trigger_bar(self, bar: Bar) -> list[object]:
        """5m/15m bar: nested-fib entry (once ARMED) + trade management (5m close stop)."""
        self._ti += 1
        out = self._advance(bar, self._prev_trig)
        self._prev_trig = bar
        return out

    def on_bar(self, bar: Bar) -> list[object]:
        """Single-timeframe convenience (synthetic backtest + tests)."""
        self._ingest(bar)
        out = self._advance_pullback(bar)
        self._ti += 1
        out += self._advance(bar, self._prev_trig)
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

    @staticmethod
    def _is_pin(c: Bar, bullish: bool) -> bool:
        """A SOLID single-candle rejection (pin bar / hammer / shooting star):
        the rejection wick is >= 2/3 of the range, the body <= 1/3, and the
        opposite wick is tiny (<= 15%). Deliberately strict — only the cleanest
        reversal candles qualify (user: 'just the best and most solid')."""
        rng = c.high - c.low
        if rng <= 0:
            return False
        body = abs(c.close - c.open)
        upper = c.high - max(c.open, c.close)
        lower = min(c.open, c.close) - c.low
        if body > 0.33 * rng:
            return False
        if bullish:                                   # hammer at a swing LOW
            return lower >= 0.66 * rng and upper <= 0.15 * rng
        return upper >= 0.66 * rng and lower <= 0.15 * rng   # shooting star at a swing HIGH

    def pin_bar_confirmed(self, leg: FibLeg) -> bool:
        """The REVERSAL that starts this impulse shows a solid pin bar at the ORIGIN
        candle (detection TF): a bullish hammer at the origin low for a LONG, a
        bearish shooting star at the origin high for a SHORT. A single-candle
        alternative to M/W so we don't miss clean rejections that lack the double
        top/bottom structure. Checks the origin bar and the one right after (the
        rejection sometimes closes on the next candle)."""
        idx = leg.start_index
        bullish = leg.side is Side.LONG
        for i in (idx, idx + 1):
            if 0 <= i < len(self._setup_bars) and self._is_pin(self._setup_bars[i], bullish):
                return True
        return False

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
        self._setup_bars.append(bar)         # keep detection-TF OHLC for origin-candle PA
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
        # feed the book tracker the latest confirmed swing low/high so it flips only
        # on a break of structure (the "break the previous low first" rule) and
        # re-anchors the origin to the swing that started the move
        slow = next((p for p in reversed(self.pivots) if p.kind is PivotType.LOW), None)
        shigh = next((p for p in reversed(self.pivots) if p.kind is PivotType.HIGH), None)
        self._book.update(self._si, bar, slow, shigh)
        self._update_leg()                       # every bar: track the live impulse
        self._update_leg()                       # every bar: track the live impulse

    @staticmethod
    def _leg_sig(side: Side, start_price: float, end_price: float) -> tuple:
        return (side, round(start_price, 2), round(end_price, 2))

    def _impulse(self) -> tuple[Pivot, Pivot, int] | None:
        """The current dominant impulse (start, end, dir) under the SELECTED leg
        method: 'adaptive' = ZigZag + market-structure dominant_impulses (default);
        'book'/'book382' = the TradeWisely break-of-structure tracker (0.236 / 0.382)."""
        if self.method in ("book", "book382"):
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

    def _confluence_mountain(self, end_price: float, end_index: int, rng: float,
                             side: Side) -> float | None:
        """A prior swing high OR low (an S/R level) sitting near the 0.5-0.618 zone —
        scanned over a WIDER band (conf_band_lo..hi, default 0.40-0.72) so a level just
        outside the zone, or a slightly-off fib, still counts. Both kinds count: old
        resistance AND old support are S/R. Returns the price of the one price meets
        FIRST on the pullback (shallowest), or None (rare)."""
        if rng <= 0:
            return None
        blo, bhi = self.cfg.conf_band_lo, self.cfg.conf_band_hi
        if side is Side.LONG:
            lo, hi = end_price - bhi * rng, end_price - blo * rng
            m = [p.price for p in self.pivots if p.index < end_index and lo <= p.price <= hi]
            return max(m) if m else None      # highest = shallowest = first level hit
        lo, hi = end_price + blo * rng, end_price + bhi * rng
        m = [p.price for p in self.pivots if p.index < end_index and lo <= p.price <= hi]
        return min(m) if m else None

    def confluence(self, start: Pivot, end: Pivot, side: Side) -> bool:
        return self._confluence_mountain(end.price, end.index,
                                         abs(end.price - start.price), side) is not None

    def confluence_leg(self, leg: FibLeg) -> bool:
        """A+ flag for an arbitrary leg (used to tag watchlist/history items)."""
        return self._confluence_mountain(leg.end_price, leg.end_index, leg.rng, leg.side) is not None

    def _confluence_levels(self, end_price: float, end_index: int, rng: float, side: Side):
        """(entry, sl) for a confluence setup: entry = the 0.5-0.618 ZONE (reported at
        0.5; the 5m nested fib refines the exact fill within it), SL = 0.786 close
        (the future+option hedge covers the wider stop). None if no mountain in zone."""
        if self._confluence_mountain(end_price, end_index, rng, side) is None:
            return None
        if side is Side.LONG:
            return end_price - 0.5 * rng, end_price - 0.786 * rng
        return end_price + 0.5 * rng, end_price + 0.786 * rng

    def confluence_setup(self, start: Pivot, end: Pivot, side: Side):
        return self._confluence_levels(end.price, end.index,
                                       abs(end.price - start.price), side)

    def confluence_setup_leg(self, leg: FibLeg):
        """Dynamic (entry, sl) for an arbitrary leg — used to show the real confluence
        levels in the app lists."""
        return self._confluence_levels(leg.end_price, leg.end_index, leg.rng, leg.side)

    def confluence_zone_leg(self, leg: FibLeg):
        """(mountain, zone_lo, zone_hi) for an arbitrary leg — the S/R zone the app
        draws on the chart. zone = mountain +/- zone_frac*leg. None if not A+."""
        mtn = self._confluence_mountain(leg.end_price, leg.end_index, leg.rng, leg.side)
        if mtn is None:
            return None
        h = self.cfg.zone_frac * leg.rng
        return mtn, mtn - h, mtn + h

    def _open_setup(self, side: Side, start: Pivot, end: Pivot) -> None:
        leg = FibLeg(side, start.index, end.index, start.price, end.price, start.ts, end.ts)
        # only MAJOR impulses become setups — filters out micro-legs so the fib
        # anchors at real trend-change extremes (your point: not every breakout)
        if self._atr > 0 and leg.rng < self.cfg.min_leg_atr * self._atr:
            return
        # Confirmation gates (loss-cutters) — skip the setup unless the leg carries
        # the required structure. Off by default; each is an independent hard filter.
        if self.cfg.require_mw:
            ok = self.mw_confirmed(leg)
            if not ok and self.cfg.reversal_pin:
                ok = self.pin_bar_confirmed(leg)      # M/W OR a solid pin bar at the origin
            if not ok:
                return
        if self.cfg.require_htf and not self.htf_confirms(leg):
            return
        if self.cfg.require_ew and not self.ew_confirmed(leg):
            return
        # Confluence mode: entry + SL are driven by the mountain (dynamic), not the
        # fixed toggles — entry AT the mountain, SL below the next fib (0.618/0.786).
        mtn = None
        if self.cfg.require_confluence or self.cfg.zone_entry:
            # entry is ALWAYS the 0.5-0.618 zone + 0.786 stop. Anchor to the mountain
            # when one sits in the zone (A+); otherwise fall back to the plain fib zone.
            cs = self.confluence_setup(start, end, side)
            if cs is not None:
                entry, sl = cs
                mtn = self._confluence_mountain(end.price, end.index, leg.rng, side)
            elif self.cfg.zone_entry:
                entry, sl = leg.retracement(0.5), leg.retracement(0.786)   # no mountain -> plain zone
            else:
                return                        # require_confluence + no mountain -> skip
        else:
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
                                created_index=self._si, conf_mtn=mtn)

    def _nested_trigger(self, bar: Bar, long: bool, leg_rng: float) -> float | None:
        """Fractal entry on the trigger TF: the zone was respected, so track the
        bounce (origin low/high -> bounce extreme), and once a real bounce has formed
        (>= min_risk_frac of the leg), return the nested-fib 0.5 the moment price pulls
        back to it. Re-anchors deeper if the pullback extends; resets if the bounce
        fails (breaks its origin). Returns the entry price, else None."""
        min_bounce = self.cfg.min_risk_frac * leg_rng
        if long:
            if self._n_lo is None:                      # first bar in the zone
                self._n_lo, self._n_hi, self._n_bounced = bar.low, bar.high, False
                return None
            self._n_hi = max(self._n_hi, bar.high)
            if not self._n_bounced:
                if bar.low < self._n_lo:                # deeper pullback -> re-anchor origin
                    self._n_lo, self._n_hi = bar.low, bar.high
                if self._n_hi - self._n_lo >= min_bounce:
                    self._n_bounced = True
                return None
            if bar.low < self._n_lo:                    # bounce failed -> reset
                self._n_lo, self._n_hi, self._n_bounced = bar.low, bar.high, False
                return None
            entry = self._n_hi - 0.5 * (self._n_hi - self._n_lo)   # nested 0.5
            return entry if bar.low <= entry else None
        # SHORT mirror
        if self._n_hi is None:
            self._n_hi, self._n_lo, self._n_bounced = bar.high, bar.low, False
            return None
        self._n_lo = min(self._n_lo, bar.low)
        if not self._n_bounced:
            if bar.high > self._n_hi:
                self._n_hi, self._n_lo = bar.high, bar.low
            if self._n_hi - self._n_lo >= min_bounce:
                self._n_bounced = True
            return None
        if bar.high > self._n_hi:
            self._n_hi, self._n_lo, self._n_bounced = bar.high, bar.low, False
            return None
        entry = self._n_lo + 0.5 * (self._n_hi - self._n_lo)
        return entry if bar.high >= entry else None

    def _pin_respect(self, s: "Setup", bar: Bar, z_lo: float, z_hi: float, long: bool) -> bool:
        """Case B respect: a big rejection PIN on the detection-TF candle (a hammer/
        shooting star whose wick spears INTO the zone and CLOSES back out the way it
        came), CONFIRMED by a BIGGER next candle in the trade direction. Returns True
        on the confirming bar; otherwise remembers the pin for one bar."""
        if s.pending_pin is not None:                    # confirm a pin from the prior bar
            pc, ph, pl = s.pending_pin
            s.pending_pin = None
            bigger = (bar.high - bar.low) > (ph - pl)
            if bigger and (bar.close > pc if long else bar.close < pc):
                return True
        if long:                                         # a fresh rejection pin at the zone?
            if bar.low <= z_hi and bar.close > z_hi and self._is_pin(bar, True):
                s.pending_pin = (bar.close, bar.high, bar.low)
        else:
            if bar.high >= z_lo and bar.close < z_lo and self._is_pin(bar, False):
                s.pending_pin = (bar.close, bar.high, bar.low)
        return False

    # -- pullback / zone-respect (runs on the DETECTION timeframe) --------
    def _advance_pullback(self, bar: Bar) -> list[object]:
        """The zone-respect is judged on the SELECTED detection timeframe (4H/3H/2H/…),
        not the trigger TF: a detection-TF candle must be ACCEPTED into the zone (CLOSE
        inside it) and then a later detection-TF candle must CLOSE back out the way it
        came. Only then do we arm and drop to the 5m trigger for the nested-fib entry."""
        s = self.active
        if s is None or s.state is not SetupState.WAITING_PULLBACK:
            return []
        long = s.side is Side.LONG
        leg_end = s.leg.end_price
        reclaimed = bar.high >= leg_end if long else bar.low <= leg_end
        if reclaimed:
            s.state = SetupState.INVALID
            self.active = None
            return []
        if self.cfg.zone_respect and (s.conf_mtn is not None or self.cfg.zone_entry):
            if s.conf_mtn is not None:
                h = self.cfg.zone_frac * s.leg.rng
                z_lo, z_hi = s.conf_mtn - h, s.conf_mtn + h
            else:
                a, b = s.leg.retracement(0.5), s.leg.retracement(0.618)
                z_lo, z_hi = (a, b) if a < b else (b, a)
            # genuine respect: CLOSE accepted inside the zone, then CLOSE back out the top
            # (long) / bottom (short). A single wick-and-close-through does NOT count.
            if long:
                if bar.close < z_lo:                     # closed THROUGH the bottom -> broke, dead
                    s.state = SetupState.INVALID
                    self.active = None
                    return []
                if bar.close <= z_hi:                    # CLOSED inside the zone -> accepted
                    s.zone_touched = True
                reached = s.zone_touched and bar.close > z_hi
                if not reached and self.cfg.zone_pin_respect:
                    reached = self._pin_respect(s, bar, z_lo, z_hi, True)
            else:
                if bar.close > z_hi:                     # closed THROUGH the top -> broke, dead
                    s.state = SetupState.INVALID
                    self.active = None
                    return []
                if bar.close >= z_lo:                    # CLOSED inside the zone -> accepted
                    s.zone_touched = True
                reached = s.zone_touched and bar.close < z_lo
                if not reached and self.cfg.zone_pin_respect:
                    reached = self._pin_respect(s, bar, z_lo, z_hi, False)
        else:
            reached = bar.low <= s.entry_price if long else bar.high >= s.entry_price
        if reached:
            if self._dbg is not None and self.cfg.zone_respect:
                self._dbg.append(("ARM", bar.ts, round(bar.close, 2),
                                  round(z_hi, 2), round(z_lo, 2), s.conf_mtn))
            s.state = SetupState.ARMED
            self._n_lo = self._n_hi = None   # start nested-fib tracking fresh
            self._n_bounced = False
        return []

    # -- state machine (ARMED onward runs on the trigger timeframe) -------
    def _advance(self, bar: Bar, prev: Bar | None) -> list[object]:
        s = self.active
        if s is None:
            return []
        out: list[object] = []
        long = s.side is Side.LONG
        leg_end = s.leg.end_price

        # WAITING_PULLBACK (the zone-respect) now runs on the DETECTION timeframe in
        # _advance_pullback(); the trigger-TF machine picks up once the setup is ARMED.
        if s.state is SetupState.ARMED:
            reclaimed = bar.high >= leg_end if long else bar.low <= leg_end
            closed_beyond_sl = bar.close < s.sl_price if long else bar.close > s.sl_price
            if reclaimed or closed_beyond_sl:
                s.state = SetupState.INVALID
                self.active = None
                return out
            if self.cfg.nested_entry:
                # nested-fib entry: the zone is respected, so redraw a fib on the
                # trigger-TF bounce and fill at its 0.5 (fills right here — price is
                # at the nested level now). SL/target stay on the detection TF.
                trig = self._nested_trigger(bar, long, s.leg.rng)
                if trig is not None:
                    has_room = trig < leg_end if long else trig > leg_end
                    risk_ok = abs(trig - s.sl_price) >= self.cfg.min_risk_frac * s.leg.rng
                    if has_room and risk_ok:
                        s.trigger_price = s.entry_fill = s.entry_price = trig
                        s.entry_risk = abs(trig - s.sl_price)
                        s.entry_index = self._ti
                        s.entry_ts = bar.ts
                        s.state = SetupState.IN_TRADE
                        if getattr(self, "_dbg", None) is not None:
                            self._dbg.append(("FILL", bar.ts, round(trig, 2),
                                              round(s.leg.start_price, 2), round(s.leg.end_price, 2)))
                        sig = Signal(self.symbol, s.side, s.leg, trig, s.sl_price,
                                     [t.price for t in s.targets], bar.ts,
                                     note="nested-fib 0.5 entry")
                        self.signals.append(sig)
                        out.append(sig)
            elif trigger.is_reversal(s.side, bar, prev):
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

        for i, t in enumerate(s.targets):
            if t.hit:
                continue
            reached = bar.high >= t.price if long else bar.low <= t.price
            if reached:
                t.hit = True
                r = (t.price - s.entry_fill) / risk if long else (s.entry_fill - t.price) / risk
                s.realized_r += t.fraction * r
                s.remaining -= t.fraction
                if i == 0:
                    if self.cfg.move_sl_to_be_after_tp1:
                        s.sl_price = s.entry_fill              # T1 -> breakeven
                elif self.cfg.trail_sl_after_targets:
                    s.sl_price = s.targets[i - 1].price        # ratchet: T2->T1, T3->T2 (lock profit)
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
