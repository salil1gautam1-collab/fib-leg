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


def run_dual(symbol: str, setup_bars: list[Bar], trig_bars: list[Bar],
             cfg: StrategyConfig | None = None, method: str = "adaptive",
             base_min: int = 15) -> FibLegEngine:
    """Merge the setup stream (leg detection) and the trigger stream (entry + SL/exit
    on a close beyond the level) into one time-ordered sequence. Both streams are
    resampled from the same base, so each bar's close = its last base bar's ts +
    base_min; a setup bar (priority 1) runs AFTER its constituent trigger bars
    (priority 0) at an equal close time — no lookahead."""
    off = timedelta(minutes=base_min)
    eng = FibLegEngine(symbol, cfg, method)
    events = []
    for b in setup_bars:
        events.append((b.ts + off, 1, "setup", b))
    for b in trig_bars:
        events.append((b.ts + off, 0, "trig", b))
    events.sort(key=lambda e: (e[0], e[1]))
    for _, _, kind, b in events:
        if kind == "setup":
            eng.on_setup_bar(b)
        else:
            eng.on_trigger_bar(b)
    return eng


def run_dual_universe(series: dict[str, tuple[list[Bar], list[Bar]]],
                      cfg: StrategyConfig | None = None,
                      method: str = "adaptive",
                      base_min: int = 15) -> dict[str, FibLegEngine]:
    return {sym: run_dual(sym, su, tr, cfg, method, base_min)
            for sym, (su, tr) in series.items()}
