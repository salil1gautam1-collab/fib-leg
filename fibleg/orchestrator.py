"""Live scanner orchestrator (design §2, §3 sharded scanning).

Skeleton of the live loop: keep a FibLegEngine per symbol, feed it fresh bars,
route emitted Signals to Telegram, and watch IN_TRADE setups for SL/target.

Branching (design §5):
  - HOT branch (ARMED / IN_TRADE): fast loop (1-5 min) — trigger + SL watch.
  - COLD branch (IDLE / WAITING): slow loop on 1H close — discovery scan.
Both read a shared candle cache so the fast loop never re-hits the broker API.

This is the wiring target after the core engine is validated by the backtest.
"""
from __future__ import annotations

from .config import SystemConfig
from .data.universe import Symbol
from .models import Signal, Trade
from .strategy.fib_leg import FibLegEngine


class Orchestrator:
    def __init__(self, symbols: list[Symbol], cfg: SystemConfig | None = None) -> None:
        self.cfg = cfg or SystemConfig()
        self.symbols = symbols
        self.engines: dict[str, FibLegEngine] = {
            s.ticker: FibLegEngine(s.ticker, self.cfg.strategy) for s in symbols
        }

    def hot_symbols(self) -> list[str]:
        """Symbols needing the fast loop (armed setup or open trade)."""
        from .models import SetupState
        hot = []
        for sym, e in self.engines.items():
            if e.active and e.active.state in (
                SetupState.ARMED, SetupState.SIGNALED, SetupState.IN_TRADE):
                hot.append(sym)
        return hot

    def on_bar(self, symbol: str, bar) -> list[object]:
        """Feed one bar; return emitted events (Signals / Trades) to be alerted."""
        events = self.engines[symbol].on_bar(bar)
        for ev in events:
            if isinstance(ev, Signal):
                self._on_signal(ev)
            elif isinstance(ev, Trade):
                self._on_trade(ev)
        return events

    def _on_signal(self, sig: Signal) -> None:
        # TODO: render chart, push Telegram setup alert w/ confirm buttons
        ...

    def _on_trade(self, tr: Trade) -> None:
        # TODO: push SL-HIT / TARGET-HIT alert; if live, close both F&O legs
        ...

    # run loop (APScheduler-driven, session-aware) — TODO
    def run(self) -> None:
        raise NotImplementedError("Wire live feeds + scheduler (design §2/§3).")
