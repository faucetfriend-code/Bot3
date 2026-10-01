"""Offline regressions for replay lifecycle and metric consumers."""

import copy
from datetime import datetime, timedelta
import math
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec

import pytest

from trading_bot_v2.backtesting import engine as engine_module
from trading_bot_v2.backtesting import run_backtest
from trading_bot_v2.backtesting.engine import BacktestEngine
from trading_bot_v2.backtesting.optimization_adapter import OptimizationAdapter
from trading_bot_v2.backtesting.performance import PerformanceTracker
from trading_bot_v2.backtesting.walk_forward import WalkForwardAnalyzer
from trading_bot_v2.config import config
from trading_bot_v2.models import OrderSide
from trading_bot_v2.tests.test_backtest_safety_regressions import (
    SYMBOL,
    bar,
    exchange,
    signal,
)


def finish(tracker, equity):
    return tracker.finalise(equity, [], SYMBOL, "2024-01-01", "2024-02-01")


@pytest.mark.parametrize("snapshots", [[], [1000], [1000, 1100]])
def test_terminal_equity_is_in_drawdown(snapshots):
    tracker = PerformanceTracker(1000)
    for i, equity in enumerate(snapshots):
        tracker.record_snapshot(f"2024-01-01T0{i}:00:00", equity, {})
    peak = max([1000, *snapshots])
    assert finish(tracker, 800).max_drawdown_pct == pytest.approx(
        (peak - 800) / peak * 100
    )


def test_irregular_risk_metrics_use_elapsed_time_residuals():
    tracker = PerformanceTracker(1000)
    for hour, equity in [(0, 1000), (1, 1020), (4, 999.6)]:
        tracker.record_snapshot(f"2024-01-01T0{hour}:00:00", equity, {})
    result = finish(tracker, 999.6)
    # Returns +2%, -2% over 1h, 3h have zero drift despite unequal spacing.
    assert result.sharpe_ratio == pytest.approx(0, abs=1e-10)
    tracker = PerformanceTracker(1000)
    for hour, equity in [(0, 1000), (1, 1030), (4, 1019.7)]:
        tracker.record_snapshot(f"2024-01-01T0{hour}:00:00", equity, {})
    result = finish(tracker, 1019.7)
    drift = (0.03 - 0.01) / 4
    variance = ((0.03 - drift) ** 2 + (-0.01 - drift * 3) ** 2) / 4
    assert result.sharpe_ratio == pytest.approx(
        drift / math.sqrt(variance) * math.sqrt(8760)
    )
    assert result.sortino_ratio == pytest.approx(
        drift / math.sqrt(0.01**2 / 3) * math.sqrt(8760)
    )


def test_zero_equity_does_not_divide_by_zero():
    tracker = PerformanceTracker(1000)
    for hour, equity in [(0, 1000), (1, 0), (2, 0)]:
        tracker.record_snapshot(f"2024-01-01T0{hour}:00:00", equity, {})
    result = finish(tracker, 0)
    assert result.max_drawdown_pct == 100
    assert math.isfinite(result.sharpe_ratio)


def replay(monkeypatch, prices, entry):
    cfg = copy.copy(config)
    for name in (
        "backtest_slippage_pct",
        "backtest_taker_fee_pct",
        "backtest_maker_fee_pct",
        "backtest_funding_hourly_pct",
    ):
        setattr(cfg, name, 0.0)
    engine = BacktestEngine(override_config=cfg)
    monkeypatch.setattr(engine, "_resolve_funding_schedule", lambda *a: None)
    monkeypatch.setattr(engine, "_check_data_coverage", lambda *a: None)
    monkeypatch.setattr(
        engine_module,
        "SimulatedExchange",
        lambda **kw: exchange(kw["initial_capital"], fee=0),
    )
    manager = MagicMock()
    manager.strategies = {}
    manager.generate_signals_for_market.side_effect = [[entry]] + [[]] * (
        len(prices) - 1
    )
    monkeypatch.setattr(engine_module, "StrategyManager", lambda **kw: manager)
    monkeypatch.setattr(engine_module.CostModel, "apply", lambda *a: None)
    start = datetime(2024, 1, 1)

    def candles(tf, *args, **kwargs):
        values = prices if tf == "5m" else [100] * 40
        times = [start + timedelta(minutes=5 * i) for i in range(len(values))]
        if tf != "5m":
            times = [start - timedelta(hours=4 * (41 - i)) for i in range(40)]
        return {
            "timestamp": [t.isoformat() for t in times],
            **{key: list(values) for key in ("open", "high", "low", "close")},
            "volume": [1000] * len(values),
        }

    loader = SimpleNamespace(get_candles=candles)
    monkeypatch.setattr(engine_module, "BacktestDataLoader", lambda **kw: loader)
    return engine.run("2024-01-01", "2024-01-02", SYMBOL, 1000)


def test_replay_records_intermediate_drawdown_and_terminal_bar(monkeypatch):
    entry = signal(OrderSide.BUY, 100)
    entry.stop_loss = None
    entry.take_profit = None
    result = replay(monkeypatch, [100, 50, 100], entry)
    assert [s["equity"] for s in result.equity_curve] == [1000, 1000, 950, 1000]
    assert result.max_drawdown_pct == pytest.approx(5)
    assert result.equity_curve[-1]["timestamp"] == "2024-01-01T00:15:00"


def test_entry_touch_on_expiry_bar_does_not_fill(monkeypatch):
    entry = signal(OrderSide.BUY, 90)
    entry.indicators = {"entry_ttl_candles": 1}
    result = replay(monkeypatch, [100, 90], entry)
    assert result.trade_log == []


def test_pending_time_exit_persists_and_ages_from_fill():
    ex = exchange(fee=0)
    engine = BacktestEngine()
    engine._sim_dt = datetime(2024, 1, 1)
    entry = signal(OrderSide.BUY, 90)
    entry.indicators = {"time_exit_hours": 1}
    assert engine._execute_signal(entry, ex, 0)
    engine._apply_time_exits(ex, datetime(2024, 1, 1, 2))
    assert SYMBOL in engine._position_time_exit
    ex.advance(bar(90), "2024-01-01T03:00:00")
    engine._sync_position_tracking(ex, 36)
    assert engine._position_time_exit[SYMBOL]["open_ts"] == datetime(2024, 1, 1, 3)
    engine._apply_time_exits(ex, datetime(2024, 1, 1, 3, 55))
    assert SYMBOL in ex._positions
    engine._apply_time_exits(ex, datetime(2024, 1, 1, 4))
    assert SYMBOL not in ex._positions


def test_expired_pyramid_add_preserves_existing_protection():
    ex = exchange(fee=0)
    engine = BacktestEngine()
    engine._max_pyramid_entries = 3
    engine._pyramid_min_spacing = 0
    assert engine._execute_signal(signal(OrderSide.BUY, 100), ex, 0)
    entry = signal(OrderSide.BUY, 90)
    entry.indicators = {"entry_ttl_candles": 1}
    assert engine._execute_signal(entry, ex, 1)
    engine._expire_stale_entries(ex, 2)
    ex.advance(bar(90), "2024-01-01T00:15:00")
    assert ex._positions[SYMBOL].quantity == 1
    ex.advance(bar(79), "2024-01-01T00:20:00")
    assert SYMBOL not in ex._positions
    assert len(ex.trade_log) == 2


def test_walk_forward_cli_forwards_strategy(monkeypatch):
    analyzer = create_autospec(WalkForwardAnalyzer, instance=True)
    analyzer.run.return_value = []
    monkeypatch.setattr(run_backtest, "WalkForwardAnalyzer", lambda engine: analyzer)
    monkeypatch.setattr(run_backtest, "BacktestEngine", MagicMock())
    monkeypatch.setattr(
        "sys.argv", ["backtest", "--walk-forward", "--strategy", "MeanReversion"]
    )
    run_backtest.main()
    assert analyzer.run.call_args.kwargs["strategy"] == "MeanReversion"


def test_regime_optimizer_counts_breakevens_and_scores_net_fees():
    adapter = OptimizationAdapter()
    trades = [
        {"pnl": 0, "net_pnl": -2, "closed_qty": 1, "regime": "calm"},
        {"pnl": 1, "net_pnl": -1, "closed_qty": 1, "regime": "calm"},
        {"pnl": 0, "net_pnl": 0, "closed_qty": 0, "regime": "calm"},
    ]
    selected = adapter.get_regime_trades(SimpleNamespace(trade_log=trades), "calm")
    assert len(selected) == 2
    assert adapter.calculate_objective_from_trades(
        selected, "profit_factor", 1000, "2024-01-01", "2025-01-01"
    ) == pytest.approx(-0.06)
