"""Fyers feed — pure-function tests (no network / no credentials)."""
from fibleg.data.fyers_feed import _resolution, to_fyers_symbol


def test_symbol_mapping():
    assert to_fyers_symbol("RELIANCE.NS") == "NSE:RELIANCE-EQ"
    assert to_fyers_symbol("infy.ns") == "NSE:INFY-EQ"
    assert to_fyers_symbol("^NSEI") == "NSE:NIFTY50-INDEX"
    assert to_fyers_symbol("^NSEBANK") == "NSE:NIFTYBANK-INDEX"
    assert to_fyers_symbol("BANKNIFTY") == "NSE:NIFTYBANK-INDEX"
    assert to_fyers_symbol("NSE:SBIN-EQ") == "NSE:SBIN-EQ"   # passthrough
    assert to_fyers_symbol("TCS") == "NSE:TCS-EQ"


def test_resolution_mapping():
    assert _resolution("60m") == "60"
    assert _resolution("1h") == "60"
    assert _resolution("15m") == "15"
    assert _resolution("D") == "D"
