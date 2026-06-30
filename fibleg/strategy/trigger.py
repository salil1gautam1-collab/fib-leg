"""15m price-action entry trigger (design §1 / §9 decision #2).

The setup arms at the 0.5 zone; the actual entry needs a bullish-reversal
candle (long) and is taken as a STOP above that candle's high (mirror short).

NOTE (scaffold): the production system drops to the 15m timeframe here. This
first version evaluates the proxy on whatever timeframe the engine is fed
(1H in the offline backtest). Swapping in a true 15m series is a data-feed
change, not a logic change — the predicate below is timeframe-agnostic.
"""
from __future__ import annotations

from ..models import Bar, Side


def is_reversal(side: Side, bar: Bar, prev: Bar | None) -> bool:
    """A minimal, codified bullish/bearish reversal candle."""
    if side is Side.LONG:
        bullish = bar.close > bar.open
        engulf = prev is not None and bar.close >= prev.high
        hammer = (bar.close - bar.low) > 2 * abs(bar.close - bar.open)
        return bullish and (engulf or hammer)
    else:
        bearish = bar.close < bar.open
        engulf = prev is not None and bar.close <= prev.low
        star = (bar.high - bar.close) > 2 * abs(bar.close - bar.open)
        return bearish and (engulf or star)


def trigger_price(side: Side, bar: Bar) -> float:
    """Stop-entry level: above the trigger candle's high (long) / below low (short)."""
    return bar.high if side is Side.LONG else bar.low
