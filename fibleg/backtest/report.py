"""Backtest stats. R-multiple based so it's position-size agnostic."""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Trade


@dataclass
class Report:
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_r: float
    avg_r: float
    expectancy_r: float
    max_drawdown_r: float
    profit_factor: float

    def render(self) -> str:
        return (
            "── Backtest report ─────────────────────────────\n"
            f"  trades        : {self.trades}\n"
            f"  win rate      : {self.win_rate:6.1%}  ({self.wins}W / {self.losses}L)\n"
            f"  total R       : {self.total_r:+7.2f}\n"
            f"  expectancy/R  : {self.expectancy_r:+7.3f} per trade\n"
            f"  profit factor : {self.profit_factor:7.2f}\n"
            f"  max DD (R)    : {self.max_drawdown_r:7.2f}\n"
            "────────────────────────────────────────────────"
        )


def summarize(trades: list[Trade]) -> Report:
    n = len(trades)
    if n == 0:
        return Report(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    rs = [t.realized_r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    total = sum(rs)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # equity curve in R for max drawdown
    peak = 0.0
    eq = 0.0
    max_dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    return Report(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / n,
        total_r=total,
        avg_r=total / n,
        expectancy_r=total / n,
        max_drawdown_r=max_dd,
        profit_factor=pf,
    )
