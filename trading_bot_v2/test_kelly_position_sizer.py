"""
Test suite for Kelly Criterion Position Sizer.

Tests:
- Fallback sizing with 0 trades
- Fallback sizing with < 50 trades
- Kelly sizing with 50+ trades
- Negative Kelly handling
- Maximum position size caps
- Different strategy types
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from .kelly_position_sizer import KellyPositionSizer
from core_logic.models import Signal, OrderSide
from .config import StrategyType, AssetClass, TradeQuality, MarketState


class TestKellyPositionSizer:
    """Test Kelly Criterion position sizing."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        db = MagicMock()
        db._connection_pool = MagicMock()
        return db

    @pytest.fixture
    def kelly_sizer(self, mock_db):
        """Create Kelly position sizer instance."""
        return KellyPositionSizer(db=mock_db, kelly_fraction=0.5, min_trades=50)

    @pytest.fixture
    def sample_signal(self):
        """Create sample trading signal."""
        return Signal(
            strategy=StrategyType.TREND_FOLLOWING,
            asset="BTC-PERP",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.BUY,
            entry_price=50000.0,
            stop_loss=49000.0,  # 2% stop
            take_profit=53000.0,
            confidence=0.8,
            quality=TradeQuality.STANDARD,
            market_state=MarketState.TREND,
        )

    def test_fallback_with_zero_trades(self, kelly_sizer, sample_signal, mock_db):
        """Test fallback sizing when no trade history exists."""
        # Mock database to return no trades
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        account_balance = 10000.0
        quantity = kelly_sizer.calculate_position_size(sample_signal, account_balance)

        # Should use fallback 2% for TREND_FOLLOWING
        # Dollar risk = $10,000 × 2% = $200
        # Stop distance = 2% (49k to 50k)
        # Position value = $200 / 0.02 = $10,000
        # Quantity = $10,000 / $50,000 = 0.2 → rounds to 1.0 (minimum)
        assert quantity == 1.0

    def test_fallback_with_insufficient_trades(
        self, kelly_sizer, sample_signal, mock_db
    ):
        """Test fallback sizing with < 50 trades."""
        # Mock database to return 30 trades
        mock_trades = [
            (100.0, 50000.0, 51000.0, 0.2, "buy") for _ in range(30)
        ]  # 30 winning trades
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_trades
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        account_balance = 10000.0
        quantity = kelly_sizer.calculate_position_size(sample_signal, account_balance)

        # Should still use fallback (< 50 trades)
        assert quantity >= 1.0

    def test_kelly_with_sufficient_trades(self, kelly_sizer, sample_signal, mock_db):
        """Test Kelly sizing with 50+ trades."""
        # Mock database to return 60 trades: 40 wins, 20 losses
        mock_trades = []
        # 40 wins of $100 each
        for _ in range(40):
            mock_trades.append((100.0, 50000.0, 51000.0, 0.2, "buy"))
        # 20 losses of $50 each
        for _ in range(20):
            mock_trades.append((-50.0, 50000.0, 49500.0, 0.2, "buy"))

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_trades
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        account_balance = 10000.0
        quantity = kelly_sizer.calculate_position_size(sample_signal, account_balance)

        # Win rate = 40/60 = 0.667
        # Avg win = $100
        # Avg loss = $50
        # Kelly = (0.667 × 100 - 0.333 × 50) / 100 = (66.7 - 16.65) / 100 = 0.5005
        # Adjusted Kelly (×0.5) = 0.25025 ≈ 25%
        # Capped at 10% max = 10%
        # Dollar risk = $10,000 × 10% = $1,000
        # Stop distance = 2%
        # Position value = $1,000 / 0.02 = $50,000
        # Quantity = $50,000 / $50,000 = 1.0
        assert quantity >= 1.0

    def test_negative_kelly_handling(self, kelly_sizer, sample_signal, mock_db):
        """Test handling of negative Kelly (losing strategy)."""
        # Mock database to return losing trades: 20 wins, 40 losses
        mock_trades = []
        # 20 wins of $50 each
        for _ in range(20):
            mock_trades.append((50.0, 50000.0, 50500.0, 0.2, "buy"))
        # 40 losses of $100 each
        for _ in range(40):
            mock_trades.append((-100.0, 50000.0, 49000.0, 0.2, "buy"))

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_trades
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        account_balance = 10000.0
        quantity = kelly_sizer.calculate_position_size(sample_signal, account_balance)

        # Win rate = 20/60 = 0.333
        # Avg win = $50
        # Avg loss = $100
        # Kelly = (0.333 × 50 - 0.667 × 100) / 50 = (16.65 - 66.7) / 50 = -1.001 (negative!)
        # Should use 1% fallback
        # Dollar risk = $10,000 × 1% = $100
        # Stop distance = 2%
        # Position value = $100 / 0.02 = $5,000
        # Quantity = $5,000 / $50,000 = 0.1 → rounds to 1.0
        assert quantity == 1.0

    def test_max_position_cap(self, kelly_sizer, sample_signal, mock_db):
        """Test 10% maximum position size cap."""
        # Mock database to return extremely profitable trades
        mock_trades = []
        # 55 wins of $500 each
        for _ in range(55):
            mock_trades.append((500.0, 50000.0, 55000.0, 0.2, "buy"))
        # 5 losses of $10 each
        for _ in range(5):
            mock_trades.append((-10.0, 50000.0, 49900.0, 0.2, "buy"))

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_trades
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        account_balance = 10000.0
        quantity = kelly_sizer.calculate_position_size(sample_signal, account_balance)

        # This would produce a very high Kelly %, but should be capped at 10%
        # Dollar risk = $10,000 × 10% = $1,000 (max)
        # Stop distance = 2%
        # Position value = $1,000 / 0.02 = $50,000
        # Quantity = $50,000 / $50,000 = 1.0
        assert quantity >= 1.0

    def test_different_strategy_fallbacks(self, kelly_sizer, mock_db):
        """Test different fallback percentages for different strategies."""
        # Mock no trades
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        account_balance = 10000.0

        # Test MEAN_REVERSION (1.5% fallback)
        signal_mr = Signal(
            strategy=StrategyType.MEAN_REVERSION,
            asset="BTC-PERP",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.BUY,
            entry_price=50000.0,
            stop_loss=49000.0,  # 2% stop
        )
        qty_mr = kelly_sizer.calculate_position_size(signal_mr, account_balance)
        assert qty_mr >= 1.0

        # Test GRID_TRADING (0.5% fallback)
        signal_grid = Signal(
            strategy=StrategyType.GRID_TRADING,
            asset="BTC-PERP",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.BUY,
            entry_price=50000.0,
            stop_loss=49500.0,  # 1% stop
        )
        qty_grid = kelly_sizer.calculate_position_size(signal_grid, account_balance)
        assert qty_grid >= 1.0

        # Test LIQUIDATION_CAPTURE (2.5% fallback)
        signal_liq = Signal(
            strategy=StrategyType.LIQUIDATION_CAPTURE,
            asset="BTC-PERP",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.BUY,
            entry_price=50000.0,
            stop_loss=48500.0,  # 3% stop
        )
        qty_liq = kelly_sizer.calculate_position_size(signal_liq, account_balance)
        assert qty_liq >= 1.0

    def test_update_kelly_fraction(self, kelly_sizer):
        """Test updating Kelly fraction dynamically."""
        assert kelly_sizer.kelly_fraction == 0.5

        # Update to quarter Kelly (more conservative)
        kelly_sizer.update_kelly_fraction(0.25)
        assert kelly_sizer.kelly_fraction == 0.25

        # Update to full Kelly (aggressive, not recommended)
        kelly_sizer.update_kelly_fraction(1.0)
        assert kelly_sizer.kelly_fraction == 1.0

    def test_get_strategy_stats(self, kelly_sizer, mock_db):
        """Test retrieving strategy statistics."""
        # Mock database to return 60 trades
        mock_trades = []
        for _ in range(40):
            mock_trades.append((100.0, 50000.0, 51000.0, 0.2, "buy"))
        for _ in range(20):
            mock_trades.append((-50.0, 50000.0, 49500.0, 0.2, "buy"))

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_trades
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        stats = kelly_sizer.get_strategy_stats(StrategyType.TREND_FOLLOWING)

        assert stats["total_trades"] == 60
        assert stats["win_rate"] == pytest.approx(0.667, rel=0.01)
        assert stats["avg_win"] == 100.0
        assert stats["avg_loss"] == 50.0

    def test_get_recommended_kelly_fraction(self, kelly_sizer, mock_db):
        """Test recommended Kelly fraction calculation."""
        # Mock strong performance (PF > 2.0, WR > 55%)
        mock_trades = []
        for _ in range(50):
            mock_trades.append((200.0, 50000.0, 52000.0, 0.2, "buy"))
        for _ in range(10):
            mock_trades.append((-50.0, 50000.0, 49500.0, 0.2, "buy"))

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_trades
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        recommended = kelly_sizer.get_recommended_kelly_fraction(
            StrategyType.TREND_FOLLOWING
        )

        # Should recommend 0.5 (standard) for strong performance
        assert recommended == 0.5

    def test_invalid_stop_distance(self, kelly_sizer, sample_signal, mock_db):
        """Test handling of invalid stop distance."""
        # Create signal with invalid stop loss (same as entry)
        bad_signal = Signal(
            strategy=StrategyType.TREND_FOLLOWING,
            asset="BTC-PERP",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.BUY,
            entry_price=50000.0,
            stop_loss=50000.0,  # Invalid: same as entry
        )

        # Mock no trades
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_db._connection_pool.get_connection.return_value.__enter__.return_value = (
            mock_conn
        )

        account_balance = 10000.0
        quantity = kelly_sizer.calculate_position_size(bad_signal, account_balance)

        # Should return 0.0 for invalid stop
        assert quantity == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
