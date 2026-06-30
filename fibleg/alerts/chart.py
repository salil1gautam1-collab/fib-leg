"""Annotated chart rendering (design §5).

Two layers ship: (1) the Pine indicator draws on your real TradingView chart
(pine/fib_leg.pine) and `tv_deep_link` builds the click-through; (2) this module
renders a TradingView-style PNG to attach inline in the Telegram alert.

Rendering needs `mplfinance`/`matplotlib` (optional). Kept import-light so the
core engine never depends on it.
"""
from __future__ import annotations

from ..models import Signal


def tv_deep_link(symbol: str, market: str) -> str:
    """Deep link to the symbol on TradingView (user adds the Pine indicator once)."""
    prefix = {"NSE": "NSE", "US": "", "FX": "FX"}.get(market, "")
    sym = symbol.replace(".NS", "").replace("^NSEI", "NIFTY")
    tv = f"{prefix}:{sym}" if prefix else sym
    return f"https://www.tradingview.com/chart/?symbol={tv}"


def render_png(symbol: str, bars: list, signal: Signal, out_path: str) -> str:
    """Render candles + fib levels (0.5 entry, 0.618 SL, targets) + labels.
    TODO: implement with mplfinance; draw signal.leg, signal.entry/sl/targets."""
    try:
        import mplfinance  # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install mplfinance to render charts") from e
    raise NotImplementedError("Chart rendering: draw leg + fib levels here.")
