"""
End-to-end smoke tests for the backtest engine on real parquet data.

Covers: a full BacktestEngine.run() over real BTC-USDC candles, the
exclusion of non-backtestable overlay strategies (orderbook_imbalance),
and the 1m execution-coverage guard for windows outside the local 1m
store.

Uses the real parquet store in trading_bot_v2/backtesting/data (no
network: DATA_AUTODOWNLOAD is forced off for every test).
"""

from pathlib import Path

import pytest

from trading_bot_v2.backtesting.engine import (
    NON_BACKTESTABLE_STRATEGIES,
    BacktestEngine,
)
from trading_bot_v2.backtesting.performance import BacktestResult

DATA_DIR = Path(__file__).resolve().parents[1] / "backtesting" / "data"

SYMBOL = "BTC-USDC"

# Skip the whole module gracefully when the parquet store is absent
# (fresh clones without the data drop).
pytestmark = pytest.mark.skipif(
    not (DATA_DIR / f"{SYMBOL}_1m.parquet").exists(),
    reason=f"real candle data not present in {DATA_DIR}",
)


@pytest.fixture(autouse=True)
def offline_data(monkeypatch):
    """Force offline mode and point the engine at the real data store."""
    from trading_bot_v2.config import config

    monkeypatch.setenv("DATA_AUTODOWNLOAD", "false")
    monkeypatch.setattr(config, "backtest_data_dir", str(DATA_DIR))
    monkeypatch.setattr(config, "backtest_hedge_mode", False, raising=False)


@pytest.fixture
def warning_log():
    """Capture loguru WARNING+ records (engine logs via loguru, which
    pytest's caplog does not intercept without a bridge)."""
    from loguru import logger as loguru_logger

    messages = []
    sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level="WARNING")
    yield messages
    loguru_logger.remove(sink_id)


class TestBacktestSmoke:
    def test_short_window_all_strategies_completes(self):
        """A few days of real BTC data replays through the full pipeline."""
        engine = BacktestEngine()
        result = engine.run(
            start="2024-03-01",
            end="2024-03-07",
            symbol=SYMBOL,
            initial_capital=10000.0,
        )
        assert isinstance(result, BacktestResult)
        assert result.symbol == SYMBOL
        assert result.start == "2024-03-01"
        assert result.end == "2024-03-07"
        assert result.final_equity > 0
        # Engine processed bars past the ~4.8-day 4h-regime warmup:
        # equity snapshots are recorded every 60 candles after warmup.
        assert len(result.equity_curve) >= 3
        # Signals cannot be guaranteed in a short window (the first
        # ~4.8 days are 4h-regime warmup), so only structure is checked.
        assert result.total_trades >= 0
        assert isinstance(result.trade_log, list)

    def test_longer_window_single_strategy(self):
        """Three weeks, single strategy: engine stays stable over a
        longer replay (duration-capped by the small window)."""
        engine = BacktestEngine()
        result = engine.run(
            start="2024-03-01",
            end="2024-03-21",
            symbol=SYMBOL,
            initial_capital=10000.0,
            strategy_filter="mean_reversion",
        )
        assert isinstance(result, BacktestResult)
        assert result.final_equity > 0
        assert len(result.equity_curve) > 20
        # Only MeanReversion may appear in the per-strategy breakdown.
        for strategy_name in result.by_strategy:
            assert strategy_name in ("MeanReversion", "mean_reversion")


class TestNonBacktestableExclusion:
    @pytest.mark.parametrize("strategy_key", sorted(NON_BACKTESTABLE_STRATEGIES))
    def test_overlay_is_excluded_with_warning(self, strategy_key, warning_log):
        """Requesting a non-backtestable overlay warns and runs nothing."""
        engine = BacktestEngine()
        result = engine.run(
            start="2024-03-01",
            end="2024-03-03",
            symbol=SYMBOL,
            initial_capital=10000.0,
            strategy_filter=strategy_key,
        )
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 0
        assert result.trade_log == []
        assert result.by_strategy == {}
        joined = "\n".join(warning_log)
        assert "not backtestable" in joined

    def test_all_strategy_run_emits_exclusion_warnings(self, warning_log):
        """A no-filter run force-disables the L2 overlay and says so."""
        engine = BacktestEngine()
        result = engine.run(
            start="2024-03-01",
            end="2024-03-03",
            symbol=SYMBOL,
            initial_capital=10000.0,
        )
        assert isinstance(result, BacktestResult)
        assert "OrderBookImbalance" not in result.by_strategy
        joined = "\n".join(warning_log)
        assert "OrderBookImbalance" in joined
        assert "not backtestable" in joined


class TestCoverageGuard:
    def test_window_outside_1m_coverage_raises(self):
        """BTC 1m data starts 2018-01-01; a 2015 window must be refused."""
        engine = BacktestEngine()
        with pytest.raises(ValueError, match="1m candle data"):
            engine.run(
                start="2015-01-01",
                end="2015-01-14",
                symbol=SYMBOL,
                initial_capital=10000.0,
            )

    def test_error_names_backfill_command(self):
        engine = BacktestEngine()
        with pytest.raises(ValueError, match="data_manager"):
            engine.run(
                start="2015-01-01",
                end="2015-01-14",
                symbol=SYMBOL,
                initial_capital=10000.0,
            )
