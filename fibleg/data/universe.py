"""Trading universe (design §3).

Production pulls Nifty 50 / Nifty 200 constituents from NSE's official index
CSVs and tags each symbol `hedgeable` from the NSE F&O list. This scaffold
ships a small built-in sample + a hook for the live loaders.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    ticker: str          # internal/Yahoo form, e.g. RELIANCE.NS
    market: str          # NSE | US | FX
    hedgeable: bool      # has liquid F&O (can run future + option)


# A small starter set; replace with the NSE CSV loader for the full Nifty 200.
NIFTY_SAMPLE = [
    Symbol("RELIANCE.NS", "NSE", True),
    Symbol("HDFCBANK.NS", "NSE", True),
    Symbol("INFY.NS", "NSE", True),
    Symbol("TCS.NS", "NSE", True),
    Symbol("ICICIBANK.NS", "NSE", True),
    Symbol("^NSEI", "NSE", True),       # Nifty 50 index
]

US_SAMPLE = [
    Symbol("AAPL", "US", False),
    Symbol("MSFT", "US", False),
    Symbol("SPY", "US", False),
    Symbol("EURUSD=X", "FX", False),
]


def nifty200_from_nse_csv(path: str) -> list[Symbol]:
    """Load the full Nifty 200 from a downloaded NSE constituents CSV.
    TODO: wire the NSE download + F&O-list join (design §3)."""
    raise NotImplementedError("Plug in the NSE CSV loader here.")
