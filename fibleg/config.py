"""Strategy + system configuration.

Plain dataclasses so the core engine runs with zero third-party deps.
Values mirror the toggles documented in fib-leg-scanner-design.md §1.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategyConfig:
    # --- adaptive ZigZag / fib-leg detection (design §1.5) ---
    leg_reversal_thresh: float = 0.382   # pullback that locks the leg end
    atr_mult: float = 0.3                # ATR floor so micro-noise can't spawn pivots
    atr_period: int = 14

    # --- entry / stop toggles (design §1) ---
    entry_ratio: float = 0.5             # 0.5 | 0.618 (golden pocket) | 0.382
    sl_ratio: float = 0.618              # 0.618 | 0.786 | 1.0 (leg start)

    # --- targets / exits ---
    targets: tuple[float, ...] = (1.0, 1.272, 1.618)
    target_fractions: tuple[float, ...] = (1 / 3, 1 / 3, 1 / 3)
    move_sl_to_be_after_tp1: bool = True

    # --- trade lifecycle ---
    signal_expiry_bars: int = 8          # cancel an un-filled SIGNALED setup after N bars
    require_breakout: bool = True        # leg must break the prior pivot in its direction
    min_risk_frac: float = 0.05          # reject signals whose entry->stop is < this * leg range

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
