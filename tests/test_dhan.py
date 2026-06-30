"""Dhan feed — pure normalization (no network / no credentials)."""
from fibleg.data.dhan_feed import normalize


def test_normalize():
    assert normalize("RELIANCE.NS") == "RELIANCE"
    assert normalize("infy.ns") == "INFY"
    assert normalize("^NSEI") == "NSE:NIFTY50-INDEX"
    assert normalize("BANKNIFTY") == "NSE:NIFTYBANK-INDEX"
    assert normalize("TCS") == "TCS"
