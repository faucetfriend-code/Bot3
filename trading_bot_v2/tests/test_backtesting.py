"""
Tests for the backtesting engine.

Uses synthetic candle data (no real API calls, no disk I/O required).
"""

import pytest
from unittest.mock import patch, MagicMock
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.backtesting.performance import PerformanceTracker, BacktestResult
from trading_bot_v2.backtesting.cost_model import CostModel
from trading_bot_v2.backtesting.walk_forward import WalkForwardAnalyzer


class TestSimulatedExchange:
    def test_market_buy_fills_immediately(self):
        ex = SimulatedExchange(initial_capital=1000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        result = ex.place_order("SUI-USDC", "bid", "1.0", order_type="market")
        assert result["status"] == "success"
        assert len(ex.trade_log) == 1

    def test_limit_buy_fills_on_price_touch(self):
        ex = SimulatedExchange(initial_capital=1000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("SUI-USDC", "bid", "1.0", order_type="limit", price=99.0)
        assert len(ex.trade_log) == 0  # Not filled yet
        ex.advance({"open": 100, "high": 100, "low": 98, "close": 99, "volume": 1000},
                   "2024-01-01T01:00:00")
        assert len(ex.trade_log) == 1  # Filled when low touched 98 < 99

    def test_fees_deducted_on_fill(self):
        ex = SimulatedExchange(initial_capital=1000.0, taker_fee_pct=0.001)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        initial_balance = ex.balance
        ex.place_order("SUI-USDC", "bid", "1.0", order_type="market")
        assert ex.balance < initial_balance  # Fees taken

    def test_balance_sheet_is_consistent(self):
        ex = SimulatedExchange(initial_capital=5000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("SUI-USDC", "bid", "10.0", order_type="market")  # Buy 10 @ 100
        cash_after_buy = ex.balance
        ex.advance({"open": 100, "high": 115, "low": 100, "close": 110, "volume": 5000},
                   "2024-01-01T02:00:00")
        balance_info = ex.get_account_balance()
        total = float(balance_info["balance"])
        # total = cash + unrealised_pnl; price rose so unrealised PnL is positive
        assert total > cash_after_buy  # Long position at 100, price now 110 -> profit


class TestPerformanceTracker:
    def test_positive_return_calculation(self):
        tracker = PerformanceTracker(initial_capital=10000.0)
        for i, eq in enumerate([10000, 10100, 10200, 10500, 11000]):
            tracker.record_snapshot(f"2024-01-0{i+1}T00:00:00", eq, {})
        result = tracker.finalise(
            final_equity=11000.0,
            trade_log=[],
            symbol="SUI-USDC",
            start="2024-01-01",
            end="2024-01-05",
        )
        assert result.total_return_pct == pytest.approx(10.0, abs=0.1)

    def test_max_drawdown_detected(self):
        tracker = PerformanceTracker(initial_capital=10000.0)
        for eq in [10000, 11000, 9000, 10500]:
            tracker.record_snapshot("2024-01-01T00:00:00", eq, {})
        result = tracker.finalise(
            final_equity=10500.0,
            trade_log=[],
            symbol="SUI-USDC",
            start="2024-01-01",
            end="2024-04-01",
        )
        # Peak was 11000, trough was 9000 -> DD = 18.18%
        assert result.max_drawdown_pct == pytest.approx(18.18, abs=0.5)

    def test_sharpe_positive_on_rising_equity(self):
        tracker = PerformanceTracker(initial_capital=10000.0)
        eq = 10000.0
        for i in range(100):
            eq += 10  # Steady gains, no drawdown
            tracker.record_snapshot(f"2024-01-01T{i:02d}:00:00", eq, {})
        result = tracker.finalise(eq, [], "SUI-USDC", "2024-01-01", "2024-04-10")
        assert result.sharpe_ratio > 0


class TestCostModel:
    def test_cost_reduces_take_profit(self):
        model = CostModel(slippage_pct=0.002, taker_fee_pct=0.0006)
        signal = MagicMock()
        signal.take_profit = 105.0
        signal.stop_loss = 98.0
        model.apply(signal, current_price=100.0)
        assert signal.take_profit < 105.0  # Cost drag reduces net target


class TestWalkForwardWindows:
    def test_window_count_correct(self):
        mock_engine = MagicMock()
        mock_engine.cfg.backtest_walk_forward_train_months = 6
        mock_engine.cfg.backtest_walk_forward_test_months = 1
        wf = WalkForwardAnalyzer(mock_engine)
        windows = wf._build_windows("2023-01-01", "2024-12-31", 6, 1)
        assert len(windows) >= 12  # At least 12 monthly test windows in 18 months of test range

    def test_no_look_ahead_bias(self):
        mock_engine = MagicMock()
        mock_engine.cfg.backtest_walk_forward_train_months = 3
        mock_engine.cfg.backtest_walk_forward_test_months = 1
        wf = WalkForwardAnalyzer(mock_engine)
        windows = wf._build_windows("2024-01-01", "2024-12-31", 3, 1)
        for train_start, train_end, test_start, test_end in windows:
            assert train_end < test_start  # Training always ends before test begins
