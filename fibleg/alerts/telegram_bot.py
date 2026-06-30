"""Telegram alerts with inline confirm-to-trade buttons (design §4).

Needs `python-telegram-bot`. Structured here; wiring left as the next build step.
Alert types: SETUP (with chart + Confirm buttons), SL-HIT, TARGET-HIT.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Signal


@dataclass
class TelegramConfig:
    token: str = ""
    chat_id: str = ""


def format_setup(signal: Signal, tv_link: str, hedge_note: str = "") -> str:
    """The text body of a setup alert (mirrors the §4 example)."""
    arrow = "🟢 LONG" if signal.side.value == "long" else "🔴 SHORT"
    tgts = " / ".join(f"{t:.2f}" for t in signal.targets)
    lines = [
        f"{arrow} SETUP — {signal.symbol}",
        f"Entry : {signal.entry:.2f}",
        f"SL    : {signal.sl:.2f}",
        f"Target: {tgts}",
        f"Trigger: {signal.note}",
    ]
    if hedge_note:
        lines.append(f"Hedge : {hedge_note}")
    lines.append(f"🔗 {tv_link}")
    return "\n".join(lines)


class TelegramAlerter:
    """Thin wrapper. send_setup() pushes text+chart+buttons; the button callback
    routes to execution.brokers (confirm-to-trade, both F&O legs)."""

    def __init__(self, cfg: TelegramConfig) -> None:
        self.cfg = cfg

    def send_setup(self, signal: Signal, png_path: str | None, tv_link: str) -> None:
        # TODO: from telegram import InlineKeyboardButton/Markup; bot.send_photo(...)
        raise NotImplementedError("Wire python-telegram-bot here.")

    def send_sl_hit(self, symbol: str, price: float) -> None:
        raise NotImplementedError

    def send_target_hit(self, symbol: str, idx: int, price: float) -> None:
        raise NotImplementedError
