"""Replay backtest — drives the SAME FibLegEngine used live (design §7)."""
from __future__ import annotations

from ..config import StrategyConfig
from ..models import Bar, Trade
from ..strategy.fib_leg import FibLegEngine


def run(symbol: str, bars: list[Bar], cfg: StrategyConfig | None = None) -> FibLegEngine:
    eng = FibLegEngine(symbol, cfg)
    for b in bars:
        eng.on_bar(b)
    return eng


def run_universe(series: dict[str, list[Bar]],
                 cfg: StrategyConfig | None = None) -> dict[str, FibLegEngine]:
    return {sym: run(sym, bars, cfg) for sym, bars in series.items()}


def all_trades(engines: dict[str, FibLegEngine]) -> list[Trade]:
    out: list[Trade] = []
    for e in engines.values():
        out += e.trades
    return out
