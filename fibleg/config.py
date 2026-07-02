"""Strategy + system configuration.

Plain dataclasses so the core engine runs with zero third-party deps.
Values mirror the toggles documented in fib-leg-scanner-design.md §1.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategyConfig:
    # --- timeframe the fib leg is detected on ---
    setup_factor: int = 4                # detect the impulse on 4H (4 * 1H bars).
                                         # 4H structure is cleaner than 1H so the leg
                                         # matches what a trader draws. 1 = stay on 1H.

    # --- adaptive ZigZag / fib-leg detection (TradeWisely Ch.4) ---
    leg_reversal_thresh: float = 0.236   # impulse ENDS when price retraces 0.236 from the
                                         # high (measured vs the full leg); top extends on
                                         # new highs until then (0.382 confirms the end)
    atr_mult: float = 1.5                # ATR noise floor: a reversal must exceed
                                         # max(0.382*leg, atr_mult*ATR) to lock — keeps
                                         # micro-pullbacks from fragmenting the impulse
    atr_period: int = 14
    min_leg_atr: float = 5.0             # a leg must span >= this * ATR to be a tradeable
                                         # setup (anchors fib to MAJOR trend swings only)

    # --- higher-timeframe double-check (your "verify on 2H/3H/4H") ---
    # the impulse is "HTF-confirmed" if it shows up as a same-direction swing on
    # ANY of these higher timeframes (multiples of the 1H setup TF). Used as a
    # confidence badge, not a hard filter.
    htf_factors: tuple[int, ...] = (2, 3, 4)

    # --- entry / stop toggles (design §1) ---
    entry_ratio: float = 0.5             # 0.5 | 0.618 (golden pocket) | 0.382
    sl_ratio: float = 0.786              # stop level: 0.618 | 0.786 | 1.0 (leg start)
    sl_on_close: bool = True             # stop only when a 15m (trigger-TF) candle CLOSES
                                         # beyond the level — not on an intrabar wick

    # --- targets / exits ---
    targets: tuple[float, ...] = (1.0, 1.272, 1.618)
    target_fractions: tuple[float, ...] = (1 / 3, 1 / 3, 1 / 3)
    move_sl_to_be_after_tp1: bool = True     # after T1 hits, pull the stop to breakeven
    sl_lock_at_t1: bool = False              # ABCD "safe": after T1, lock the stop AT the T1 price
                                             # (not breakeven) so a failed continuation keeps the B
                                             # profit instead of giving it back
    trail_sl_after_targets: bool = False     # after each FURTHER target hits, ratchet the stop up
                                             # to the PREVIOUS target (T2->T1, T3->T2) — lets the
                                             # runner ride while locking in the profit already banked
    entry_dependent_targets: bool = False    # TradeWisely/harmonic BC projection: the final target
                                             # extension = 1 / retracement-depth of the ACTUAL entry
                                             # (0.5->2.0, 0.618->1.618, 0.786->1.272). Deeper entry =
                                             # nearer target, shallow = further. Scale out at the
                                             # strong levels below it. Mapped to the impulse-leg extn.

    # --- trade lifecycle ---
    signal_expiry_bars: int = 8          # cancel an un-filled SIGNALED setup after N bars
    require_breakout: bool = True        # leg must break the prior pivot in its direction
    min_risk_frac: float = 0.05          # reject signals whose entry->stop is < this * leg range
    require_confluence: bool = False     # A+ filter: only take a setup when a prior broken
                                         # swing high/low sits in the 0.5-0.618 entry band;
                                         # entry = the zone, SL = 0.786 close (hedge covers it)
    zone_entry: bool = False             # entry is ALWAYS the 0.5-0.618 zone + 0.786 stop (never
                                         # the fixed entry/sl toggles). Anchor to the mountain when
                                         # one sits in the zone (A+), else fall back to the plain
                                         # fib zone. Unlike require_confluence it NEVER skips a
                                         # no-mountain setup — the app just flags it "no mountain".
    nested_entry: bool = False           # refine the entry: once the zone is respected, redraw
                                         # a fib on the trigger-TF bounce and enter at its 0.5
                                         # (the fractal nested-fib entry). SL/target stay on the
                                         # detection TF.
    zone_respect: bool = False           # confluence refine: treat the mountain as a ZONE (band),
                                         # require price to trade INTO it then CLOSE back out (held
                                         # as S/R) before arming; if a close goes THROUGH the zone
                                         # the level failed -> skip the trade. Fewer, higher-quality
                                         # entries. Needs require_confluence. SL stays on the
                                         # detection-TF leg (0.786 close).
    zone_frac: float = 0.03              # SR-zone half-width as a fraction of the leg range
                                         # (mountain +/- zone_frac*rng). Wider = looser respect.
    zone_pin_respect: bool = False       # ALSO accept a respect via a big rejection PIN on the
                                         # detection-TF candle: a large wick spears into the zone
                                         # and CLOSES back out (a hammer/shooting star), CONFIRMED
                                         # by a bigger next candle in the trade direction. Then drop
                                         # to 5m to enter. Complements the close-in-then-close-out rule.
    conf_band_lo: float = 0.40           # S/R confluence search band (retracement) — WIDER than
    conf_band_hi: float = 0.72           # the 0.5-0.618 zone so a mountain/valley sitting just
                                         # outside it, or a slightly-off fib, still counts. Both
                                         # prior swing highs AND lows count as S/R (a level is a
                                         # level). Having no S/R near the zone is genuinely rare.
    require_mw: bool = False             # hard gate: only open a setup whose ORIGIN carries the
                                         # M/W reversal (W->long, M->short). Loss-cutter filter.
    reversal_pin: bool = False           # widen the require_mw gate: also accept an origin that
                                         # shows a SOLID pin bar (hammer/shooting star) even without
                                         # the M/W double structure -> gate = (M/W OR pin). Recovers
                                         # clean single-candle reversals M/W would miss.
    require_htf: bool = False            # hard gate: only open a setup that also shows as a
                                         # same-direction swing on a higher TF (2H/3H/4H).
    require_ew: bool = False             # hard gate: only open a setup whose impulse subdivides
                                         # into a clean 5-wave Elliott structure (rare, strict).
    # --- trend / anti-chop confluence gates (per symbol, computed on the detection TF) ---
    require_ema_trend: bool = False      # long only if fast EMA > slow EMA AND slow EMA rising
    ema_fast: int = 50                   # (mirror for short); a flat/against slow EMA = skip.
    ema_slow: int = 200
    require_adx: bool = False            # only trade when ADX >= adx_min (trending); skip chop
    adx_period: int = 14
    adx_min: float = 25.0
    require_volume: bool = False         # only trade when recent volume is expanding vs baseline
    vol_fast: int = 10                   # (short vol avg) >= vol_mult * (long vol avg)
    vol_slow: int = 50
    vol_mult: float = 1.0

    # --- risk ---
    risk_per_trade: float = 0.01         # 1% of equity at the stop


@dataclass
class HedgeConfig:
    """Futures + protective option (design §6.5). Used by the executor; the
    core strategy/backtest is instrument-agnostic."""
    enabled: bool = True
    option_strike_anchor: str = "sl"     # place hedge strike at the 0.618 SL level
    index_expiry: str = "weekly"
    stock_expiry: str = "current_month"


@dataclass
class SystemConfig:
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    hedge: HedgeConfig = field(default_factory=HedgeConfig)
    setup_tf: str = "60m"
    trigger_tf: str = "15m"
