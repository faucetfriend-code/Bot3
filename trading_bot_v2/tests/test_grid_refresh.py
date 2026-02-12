"""
Tests for Grid Refresh/Recenter Feature

This module tests the intelligent grid refresh logic that allows grids
to adapt to new market conditions when high-conviction signals arrive
with significant price drift.
"""

import pytest
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading_bot_v2.trading_bot import TradingBot
from trading_bot_v2.models import Signal, OrderSide, SignalQuality, MarketState
from trading_bot_v2.config import StrategyType


class TestGridRefreshFeature:
    """Test suite for grid refresh/recenter feature."""

    @pytest.fixture
    def mock_trading_bot(self):
        """Create a trading bot with mocked dependencies."""
        with patch('trading_bot_v2.trading_bot.DatabaseManager') as mock_db, \
             patch('trading_bot_v2.trading_bot.PacificaClient') as mock_client, \
             patch('trading_bot_v2.trading_bot.RiskManager') as mock_risk, \
             patch('trading_bot_v2.trading_bot.SignalLogger'), \
             patch('trading_bot_v2.trading_bot.MultiTimeframeFetcher'), \
             patch('trading_bot_v2.trading_bot.MarketRegimeDetector'), \
             patch('trading_bot_v2.trading_bot.StrategyManager'), \
             patch('trading_bot_v2.trading_bot.GridLifecycleManager'), \
             patch('trading_bot_v2.trading_bot.MigratedPositionManager'), \
             patch('trading_bot_v2.trading_bot.get_event_bus'), \
             patch('trading_bot_v2.trading_bot.get_component_registry'):

            mock_db_instance = MagicMock()
            mock_client_instance = MagicMock()
            mock_risk_instance = MagicMock()
            mock_grid_lifecycle = MagicMock()

            mock_db.return_value = mock_db_instance
            mock_client.return_value = mock_client_instance
            mock_risk.return_value = mock_risk_instance

            bot = TradingBot(
                db=mock_db_instance,
                client=mock_client_instance,
                risk_manager=mock_risk_instance,
            )
            bot.grid_lifecycle = mock_grid_lifecycle
            bot.multi_tf_fetcher = MagicMock()
            bot.signal_logger = MagicMock()

            yield bot

    @pytest.fixture
    def sample_grid_signal(self):
        """Create a sample grid trading signal."""
        return Signal(
            asset="BTC",
            side=OrderSide.BUY,
            strategy=StrategyType.GRID_TRADING,
            entry_price=50000.0,
            stop_loss=48000.0,
            confidence=0.75,
            quality=SignalQuality.HIGH,
            market_state=MarketState.RANGING_CALM,
            grid_levels=8,
            spacing=0.004,
        )

    def test_grid_refresh_constants_defined(self, mock_trading_bot):
        """Test that grid refresh constants are properly defined."""
        bot = mock_trading_bot

        assert hasattr(bot, 'GRID_REFRESH_MIN_ATR_DRIFT')
        assert hasattr(bot, 'GRID_REFRESH_MIN_CONFIDENCE')
        assert hasattr(bot, 'GRID_REFRESH_MIN_CONF_IMPROVE')
        assert hasattr(bot, 'GRID_REFRESH_COOLDOWN_MINUTES')

        assert bot.GRID_REFRESH_MIN_ATR_DRIFT == 1.8
        assert bot.GRID_REFRESH_MIN_CONFIDENCE == 0.72
        assert bot.GRID_REFRESH_MIN_CONF_IMPROVE == 0.08
        assert bot.GRID_REFRESH_COOLDOWN_MINUTES == 45

    def test_handle_active_grid_signal_no_center(self, mock_trading_bot, sample_grid_signal):
        """Test handling when grid has no center price."""
        bot = mock_trading_bot
        signal = sample_grid_signal

        # Setup: Grid is active but no center price
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = None

        bot._handle_active_grid_signal("BTC", signal)

        # Should log rejection
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_handle_active_grid_signal_atr_unavailable(self, mock_trading_bot, sample_grid_signal):
        """Test handling when ATR calculation fails."""
        bot = mock_trading_bot
        signal = sample_grid_signal

        # Setup
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {"BTC": {}}

        # ATR returns None
        with patch.object(bot, '_calculate_atr_for_symbol', return_value=None):
            bot._handle_active_grid_signal("BTC", signal)

        # Should log rejection due to ATR unavailable
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_handle_active_grid_signal_insufficient_drift(self, mock_trading_bot, sample_grid_signal):
        """Test that signals with small drift are skipped."""
        bot = mock_trading_bot
        signal = sample_grid_signal
        signal.entry_price = 50500.0  # Small drift from center

        # Setup
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {
            "BTC": {
                "signal_confidence": 0.70,
                "regime_on_creation": "RANGING_CALM",
            }
        }
        bot.grid_lifecycle.get_last_refresh_time.return_value = None

        # ATR is 500, so drift is 500/500 = 1.0x (below 1.8x threshold)
        with patch.object(bot, '_calculate_atr_for_symbol', return_value=500.0):
            bot._handle_active_grid_signal("BTC", signal)

        # Should log rejection due to insufficient drift
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_handle_active_grid_signal_low_confidence(self, mock_trading_bot, sample_grid_signal):
        """Test that signals with low confidence are skipped."""
        bot = mock_trading_bot
        signal = sample_grid_signal
        signal.confidence = 0.65  # Below 0.72 threshold
        signal.entry_price = 60000.0  # Large drift

        # Setup
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {
            "BTC": {
                "signal_confidence": 0.70,
                "regime_on_creation": "RANGING_CALM",
            }
        }
        bot.grid_lifecycle.get_last_refresh_time.return_value = None

        # ATR is 1000, so drift is 10000/1000 = 10x (above threshold)
        with patch.object(bot, '_calculate_atr_for_symbol', return_value=1000.0):
            bot._handle_active_grid_signal("BTC", signal)

        # Should log rejection due to low confidence
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_handle_active_grid_signal_insufficient_improvement(self, mock_trading_bot, sample_grid_signal):
        """Test that signals without sufficient confidence improvement are skipped."""
        bot = mock_trading_bot
        signal = sample_grid_signal
        signal.confidence = 0.72  # Meets minimum but not improvement
        signal.entry_price = 60000.0  # Large drift

        # Setup: Current confidence is already high
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {
            "BTC": {
                "signal_confidence": 0.70,  # New signal is only 0.02 better
                "regime_on_creation": "RANGING_CALM",
            }
        }
        bot.grid_lifecycle.get_last_refresh_time.return_value = None

        # ATR is 1000, so drift is 10000/1000 = 10x (above threshold)
        with patch.object(bot, '_calculate_atr_for_symbol', return_value=1000.0):
            bot._handle_active_grid_signal("BTC", signal)

        # Should log rejection due to insufficient improvement
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_handle_active_grid_signal_cooldown_active(self, mock_trading_bot, sample_grid_signal):
        """Test that signals during cooldown are skipped."""
        bot = mock_trading_bot
        signal = sample_grid_signal
        signal.confidence = 0.80  # High confidence
        signal.entry_price = 60000.0  # Large drift (10x ATR)

        # Setup
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {
            "BTC": {
                "signal_confidence": 0.70,
                "regime_on_creation": "RANGING_CALM",
            }
        }
        # Recent refresh (10 minutes ago)
        bot.grid_lifecycle.get_last_refresh_time.return_value = datetime.now() - timedelta(minutes=10)

        # ATR is 1000, so drift is 10000/1000 = 10x (above threshold)
        with patch.object(bot, '_calculate_atr_for_symbol', return_value=1000.0):
            bot._handle_active_grid_signal("BTC", signal)

        # Should log rejection due to cooldown
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_handle_active_grid_signal_regime_mismatch(self, mock_trading_bot, sample_grid_signal):
        """Test that signals with regime mismatch are skipped."""
        bot = mock_trading_bot
        signal = sample_grid_signal
        signal.confidence = 0.80  # High confidence
        signal.entry_price = 60000.0  # Large drift (10x ATR)
        signal.market_state = MarketState.TRENDING_STRONG  # Different regime

        # Setup
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {
            "BTC": {
                "signal_confidence": 0.70,
                "regime_on_creation": "RANGING_CALM",  # Grid was created in ranging
            }
        }
        bot.grid_lifecycle.get_last_refresh_time.return_value = None

        # ATR is 1000, so drift is 10000/1000 = 10x (above threshold)
        with patch.object(bot, '_calculate_atr_for_symbol', return_value=1000.0):
            bot._handle_active_grid_signal("BTC", signal)

        # Should log rejection due to regime mismatch
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_handle_active_grid_signal_successful_refresh(self, mock_trading_bot, sample_grid_signal):
        """Test successful grid refresh when all conditions are met."""
        bot = mock_trading_bot
        signal = sample_grid_signal
        signal.confidence = 0.80  # High confidence (0.10 improvement)
        signal.entry_price = 60000.0  # Large drift (10x ATR)

        # Setup
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {
            "BTC": {
                "signal_confidence": 0.70,
                "regime_on_creation": "RANGING_CALM",
            }
        }
        bot.grid_lifecycle.get_last_refresh_time.return_value = None
        bot.grid_lifecycle.recenter_grid.return_value = True

        # ATR is 1000, so drift is 10000/1000 = 10x (above threshold)
        with patch.object(bot, '_calculate_atr_for_symbol', return_value=1000.0):
            bot._handle_active_grid_signal("BTC", signal)

        # Should call recenter_grid
        bot.grid_lifecycle.recenter_grid.assert_called_once()

        # Should log successful execution
        bot.signal_logger.log_signal_executed.assert_called_once()

    def test_handle_active_grid_signal_refresh_failure(self, mock_trading_bot, sample_grid_signal):
        """Test handling when recenter_grid fails."""
        bot = mock_trading_bot
        signal = sample_grid_signal
        signal.confidence = 0.80
        signal.entry_price = 60000.0

        # Setup
        bot.grid_lifecycle.has_active_grid.return_value = True
        bot.grid_lifecycle.get_grid_center.return_value = 50000.0
        bot.grid_lifecycle._grids = {
            "BTC": {
                "signal_confidence": 0.70,
                "regime_on_creation": "RANGING_CALM",
            }
        }
        bot.grid_lifecycle.get_last_refresh_time.return_value = None
        bot.grid_lifecycle.recenter_grid.return_value = False  # Recenter fails

        with patch.object(bot, '_calculate_atr_for_symbol', return_value=1000.0):
            bot._handle_active_grid_signal("BTC", signal)

        # Should call recenter_grid but log failure
        bot.grid_lifecycle.recenter_grid.assert_called_once()
        bot.signal_logger.log_signal_failed.assert_called_once()


class TestCalculateATRForSymbol:
    """Test suite for ATR calculation helper."""

    @pytest.fixture
    def mock_trading_bot(self):
        """Create a trading bot with mocked dependencies."""
        with patch('trading_bot_v2.trading_bot.DatabaseManager') as mock_db, \
             patch('trading_bot_v2.trading_bot.PacificaClient') as mock_client, \
             patch('trading_bot_v2.trading_bot.RiskManager') as mock_risk, \
             patch('trading_bot_v2.trading_bot.SignalLogger'), \
             patch('trading_bot_v2.trading_bot.MultiTimeframeFetcher') as mock_fetcher, \
             patch('trading_bot_v2.trading_bot.MarketRegimeDetector'), \
             patch('trading_bot_v2.trading_bot.StrategyManager'), \
             patch('trading_bot_v2.trading_bot.GridLifecycleManager'), \
             patch('trading_bot_v2.trading_bot.MigratedPositionManager'), \
             patch('trading_bot_v2.trading_bot.get_event_bus'), \
             patch('trading_bot_v2.trading_bot.get_component_registry'):

            mock_db_instance = MagicMock()
            mock_client_instance = MagicMock()
            mock_risk_instance = MagicMock()
            mock_fetcher_instance = MagicMock()

            mock_db.return_value = mock_db_instance
            mock_client.return_value = mock_client_instance
            mock_risk.return_value = mock_risk_instance
            mock_fetcher.return_value = mock_fetcher_instance

            bot = TradingBot(
                db=mock_db_instance,
                client=mock_client_instance,
                risk_manager=mock_risk_instance,
            )
            bot.multi_tf_fetcher = mock_fetcher_instance

            yield bot

    def test_calculate_atr_success(self, mock_trading_bot):
        """Test successful ATR calculation."""
        bot = mock_trading_bot

        # Mock candle data
        candles = [
            {"high": 51000.0, "low": 49000.0, "close": 50000.0},
            {"high": 51500.0, "low": 49500.0, "close": 50500.0},
            {"high": 52000.0, "low": 50000.0, "close": 51000.0},
        ] * 10  # 30 candles

        bot.multi_tf_fetcher.get_candles_multi_tf.return_value = {
            "5m": candles
        }

        atr = bot._calculate_atr_for_symbol("BTC", timeframe="5m", period=14)

        assert atr is not None
        assert atr > 0

    def test_calculate_atr_no_data(self, mock_trading_bot):
        """Test ATR calculation when no data available."""
        bot = mock_trading_bot

        bot.multi_tf_fetcher.get_candles_multi_tf.return_value = None

        atr = bot._calculate_atr_for_symbol("BTC", timeframe="5m")

        assert atr is None

    def test_calculate_atr_insufficient_candles(self, mock_trading_bot):
        """Test ATR calculation with insufficient candles."""
        bot = mock_trading_bot

        # Only 5 candles (need at least 15 for period=14)
        candles = [
            {"high": 51000.0, "low": 49000.0, "close": 50000.0},
        ] * 5

        bot.multi_tf_fetcher.get_candles_multi_tf.return_value = {
            "5m": candles
        }

        atr = bot._calculate_atr_for_symbol("BTC", timeframe="5m", period=14)

        assert atr is None
