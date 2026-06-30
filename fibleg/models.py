"""Core domain models — pure stdlib, no third-party deps.

These types flow through the whole system: data feeds emit Bars, the ZigZag
emits Pivots, the strategy builds FibLegs/Setups, and the backtest/alerts
consume Signals and Trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def opp(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG


class PivotType(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Pivot:
    index: int
    ts: datetime
    price: float
    kind: PivotType


@dataclass(frozen=True)
class FibLeg:
    """A locked impulse leg. For a LONG setup start=swing low (fib 0.0),
    end=swing high (fib 1.0). For SHORT it's mirrored (start=high, end=low)."""
    side: Side
    start_index: int
    end_index: int
    start_price: float
    end_price: float
    start_ts: Optional[datetime] = None    # candle time of the leg start (fib anchor)
    end_ts: Optional[datetime] = None

    @property
    def rng(self) -> float:
        return abs(self.end_price - self.start_price)

    def retracement(self, ratio: float) -> float:
        """Price of a `ratio` pullback from the leg end (0.5 -> midpoint)."""
        if self.side is Side.LONG:
            return self.end_price - ratio * self.rng
        return self.end_price + ratio * self.rng

    def extension(self, ratio: float) -> float:
        """Target price at `ratio` of the leg measured from the start.
        1.0 == leg end (top for long), 1.618 == projection beyond it."""
        if self.side is Side.LONG:
            return self.start_price + ratio * self.rng
        return self.start_price - ratio * self.rng


class SetupState(str, Enum):
    WAITING_PULLBACK = "waiting_pullback"   # leg locked, waiting for price to reach 0.5
    ARMED = "armed"                         # at 0.5, watching for the trigger candle
    SIGNALED = "signaled"                   # trigger set (buy/sell stop), alert sent
    IN_TRADE = "in_trade"                   # entry filled
    CLOSED = "closed"
    INVALID = "invalid"                     # closed beyond 0.618 before triggering


@dataclass
class Target:
    ratio: float
    price: float
    fraction: float
    hit: bool = False


@dataclass
class Setup:
    symbol: str
    side: Side
    leg: FibLeg
    entry_price: float
    sl_price: float
    targets: list[Target] = field(default_factory=list)
    state: SetupState = SetupState.WAITING_PULLBACK
    created_index: int = 0

    # populated as the trade progresses
    trigger_price: Optional[float] = None   # buy/sell stop above/below the trigger candle
    entry_fill: Optional[float] = None
    entry_index: Optional[int] = None
    entry_ts: Optional[datetime] = None  # when the entry filled (for the history chart)
    entry_risk: float = 0.0              # |entry - sl| frozen at fill (R denominator)
    signaled_age: int = 0                # trigger-bars since SIGNALED (for expiry)
    remaining: float = 1.0
    realized_r: float = 0.0
    exit_index: Optional[int] = None
    exit_reason: str = ""

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.sl_price)


@dataclass
class Signal:
    """Emitted when a setup is ready to act on — this is what the Telegram
    alert and the (semi-auto) executor consume."""
    symbol: str
    side: Side
    leg: FibLeg
    entry: float
    sl: float
    targets: list[float]
    ts: datetime
    note: str = ""


@dataclass
class Trade:
    """A completed trade, for the backtest report + history feed."""
    symbol: str
    side: Side
    entry: float
    sl: float
    entry_index: int
    exit_index: int
    realized_r: float
    exit_reason: str                       # "targets" (all TPs hit) | "sl"
    entry_ts: Optional[datetime] = None
    exit_ts: Optional[datetime] = None
    realized_points: float = 0.0           # net price points made (signed)
    leg: Optional["FibLeg"] = None         # the impulse leg, for drawing the fib later
