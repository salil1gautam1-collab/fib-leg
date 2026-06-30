"""Fib-leg math + engine smoke test."""
from fibleg.backtest import engine, report
from fibleg.config import StrategyConfig
from fibleg.data import feeds
from fibleg.models import FibLeg, Side


def test_fibleg_retracement_and_extension_long():
    leg = FibLeg(Side.LONG, 0, 10, 100.0, 110.0)
    assert abs(leg.retracement(0.5) - 105.0) < 1e-9
    assert abs(leg.retracement(0.618) - 103.82) < 1e-9
    assert abs(leg.extension(1.0) - 110.0) < 1e-9
    assert abs(leg.extension(1.618) - 116.18) < 1e-9


def test_fibleg_math_short():
    leg = FibLeg(Side.SHORT, 0, 10, 110.0, 100.0)
    assert abs(leg.retracement(0.5) - 105.0) < 1e-9
    assert abs(leg.extension(1.0) - 100.0) < 1e-9
    assert abs(leg.extension(1.618) - 93.82) < 1e-9


def test_dual_timeframe_driver_runs():
    from datetime import timedelta
    from fibleg.backtest import driver
    bars_1h = feeds.synthetic_series(400, seed=11, step=timedelta(hours=1))
    bars_15m = feeds.synthetic_series(1600, seed=11, step=timedelta(minutes=15))
    eng = driver.run_dual("DUAL", bars_1h, bars_15m, StrategyConfig())
    assert len(eng.pivots) > 5            # 1H stream drives leg detection
    for s in eng.signals:                  # signals stay internally consistent
        if s.side.value == "long":
            assert s.entry > s.sl
        else:
            assert s.entry < s.sl


def test_engine_runs_on_synthetic():
    bars = feeds.synthetic_series(1500, seed=7)
    eng = engine.run("SYNTH", bars, StrategyConfig())
    assert len(eng.pivots) > 5, "ZigZag should find multiple legs"
    # every signal must be internally consistent (long: entry above sl)
    for s in eng.signals:
        if s.side.value == "long":
            assert s.entry > s.sl
        else:
            assert s.entry < s.sl
    # report renders without error regardless of trade count
    rep = report.summarize(eng.trades)
    assert "Backtest report" in rep.render()
    assert rep.trades == len(eng.trades)
