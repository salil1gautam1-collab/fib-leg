"""Broker adapters + the futures/option hedge builder (design §6, §6.5).

A confirmed signal becomes a TWO-LEG order: future (direction) + protective
option (put for long / call for short), strike anchored at the 0.618 SL.
Fyers/Dhan are India-only -> US stays alert-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import HedgeConfig
from ..models import Side, Signal


@dataclass
class OrderLeg:
    instrument: str          # e.g. RELIANCE25JANFUT or RELIANCE25JAN2800PE
    side: str                # BUY | SELL
    qty_lots: int
    kind: str                # FUT | OPT


@dataclass
class HedgedOrder:
    symbol: str
    legs: list[OrderLeg]
    sl_price: float


def build_hedged_order(signal: Signal, lot_qty: int, hcfg: HedgeConfig) -> HedgedOrder:
    """Future leg + protective option at the SL strike (design §6.5)."""
    fut_side = "BUY" if signal.side is Side.LONG else "SELL"
    opt_kind = "PE" if signal.side is Side.LONG else "CE"   # put hedges long, call hedges short
    fut = OrderLeg(f"{signal.symbol}-FUT", fut_side, lot_qty, "FUT")
    opt = OrderLeg(f"{signal.symbol}-{round(signal.sl)}{opt_kind}", "BUY", lot_qty, "OPT")
    return HedgedOrder(signal.symbol, [fut, opt], signal.sl)


class Broker(Protocol):
    def place(self, order: HedgedOrder) -> str: ...
    def close(self, symbol: str) -> None: ...        # exits BOTH legs (design decision #9)
    def positions(self) -> list: ...


class PaperBroker:
    """In-memory fills for paper mode + backtest parity."""

    def __init__(self) -> None:
        self.open: dict[str, HedgedOrder] = {}

    def place(self, order: HedgedOrder) -> str:
        self.open[order.symbol] = order
        return f"paper-{order.symbol}"

    def close(self, symbol: str) -> None:
        self.open.pop(symbol, None)

    def positions(self) -> list:
        return list(self.open.values())


class FyersBroker:
    """fyers-apiv3 adapter — OAuth, bracket order per leg. TODO: implement."""
    def place(self, order: HedgedOrder) -> str:
        raise NotImplementedError("Wire fyers-apiv3 here.")
    def close(self, symbol: str) -> None:
        raise NotImplementedError
    def positions(self) -> list:
        raise NotImplementedError


class DhanBroker:
    """dhanhq adapter. TODO: implement."""
    def place(self, order: HedgedOrder) -> str:
        raise NotImplementedError("Wire dhanhq here.")
    def close(self, symbol: str) -> None:
        raise NotImplementedError
    def positions(self) -> list:
        raise NotImplementedError
