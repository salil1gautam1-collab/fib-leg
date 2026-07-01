"""Dual-timeframe replay driver (design §1).

Merges the 1H setup stream and the 15m trigger stream into one time-ordered
event sequence and feeds each bar to the right engine entry point. Bars are
keyed by their CLOSE time so a 1H bar is processed AFTER its constituent 15m
bars (no lookahead): at an equal close time the 15m trigger runs before the 1H
setup update.
"""
from __future__ import annotations

from datetime import timedelta

from ..config import StrategyConfig
from ..models import Bar
from ..strategy.fib_leg import FibLegEngine

_SETUP = timedelta(minutes=15)   # resampled setup bar's ts = its last 15m bar's open
_TRIG = timedelta(minutes=15)


def run_dual(symbol: str, bars_1h: list[Bar], bars_15m: list[Bar],
             cfg: StrategyConfig | None = None, method: str = "adaptive") -> FibLegEngine:
    eng = FibLegEngine(symbol, cfg, method)
    events = []
    for b in bars_1h:
        events.append((b.ts + _SETUP, 1, "setup", b))   # priority 1 = after triggers
    for b in bars_15m:
        events.append((b.ts + _TRIG, 0, "trig", b))      # priority 0 = first
    events.sort(key=lambda e: (e[0], e[1]))
    for _, _, kind, b in events:
        if kind == "setup":
            eng.on_setup_bar(b)
        else:
            eng.on_trigger_bar(b)
    return eng


def run_dual_universe(series: dict[str, tuple[list[Bar], list[Bar]]],
                      cfg: StrategyConfig | None = None,
                      method: str = "adaptive") -> dict[str, FibLegEngine]:
    return {sym: run_dual(sym, h1, m15, cfg, method) for sym, (h1, m15) in series.items()}
