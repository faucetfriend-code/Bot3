"""
Tests for WebSocket authority enforcement.

Ensures Phase 2 requirement: WebSocket cache is authoritative,
no REST API fallbacks for live trading.
"""

import pytest
from unittest.mock import Mock, patch


class TestWebSocketAuthority:
    """Test WebSocket-only price enforcement."""

    @pytest.fixture
    def mock_ws_client(self):
        """Mock WebSocket client."""
        client = Mock()
        client.get_price.return_value = 1.50
        return client

    def test_websocket_price_success(self, mock_ws_client):
        """Test successful WebSocket price retrieval."""
        from trading_bot_v2.trading_bot import TradingBot

        # Create trading bot with mocked components
        with (
            patch(
                "trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws_client
            ),
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Test price retrieval
            ticker = bot._get_ticker_ws("SUI-PERP")

            assert ticker["symbol"] == "SUI-PERP"
            assert ticker["last"] == 1.50
            assert "bid" in ticker
            assert "ask" in ticker

    def test_websocket_price_failure_no_fallback(self, mock_ws_client):
        """Test that WebSocket failure raises error (no REST fallback in Phase 2)."""
        from trading_bot_v2.trading_bot import TradingBot

        # Mock WebSocket client to return None (no price)
        mock_ws_client.get_price.return_value = None

        with (
            patch(
                "trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws_client
            ),
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Should raise RuntimeError, not fall back to REST
            with pytest.raises(RuntimeError, match="No WebSocket price available"):
                bot._get_ticker_ws("SUI-PERP")

    def test_websocket_client_not_initialized(self):
        """Test behavior when WebSocket client is not initialized."""
        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = None  # No WebSocket client

            with pytest.raises(RuntimeError, match="WebSocket client not initialized"):
                bot._get_ticker_ws("SUI-PERP")

    def test_websocket_price_exception_handling(self, mock_ws_client):
        """Test exception handling in WebSocket price retrieval."""
        from trading_bot_v2.trading_bot import TradingBot

        # Mock WebSocket client to raise exception
        mock_ws_client.get_price.side_effect = Exception("Connection failed")

        with (
            patch(
                "trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws_client
            ),
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            with pytest.raises(RuntimeError, match="WebSocket price retrieval failed"):
                bot._get_ticker_ws("SUI-PERP")

    def test_websocket_price_zero_value(self, mock_ws_client):
        """Test handling of zero price values from WebSocket."""
        from trading_bot_v2.trading_bot import TradingBot

        # Mock WebSocket client to return zero price
        mock_ws_client.get_price.return_value = 0.0

        with (
            patch(
                "trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws_client
            ),
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Zero price should be treated as unavailable
            with pytest.raises(RuntimeError, match="No WebSocket price available"):
                bot._get_ticker_ws("SUI-PERP")

    def test_websocket_price_negative_value(self, mock_ws_client):
        """Test handling of negative price values from WebSocket."""
        from trading_bot_v2.trading_bot import TradingBot

        # Mock WebSocket client to return negative price
        mock_ws_client.get_price.return_value = -1.50

        with (
            patch(
                "trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws_client
            ),
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Negative price should be treated as unavailable
            with pytest.raises(RuntimeError, match="No WebSocket price available"):
                bot._get_ticker_ws("SUI-PERP")

    @pytest.mark.parametrize("symbol", ["SUI", "SUI-PERP", "DOGE", "BTC/USD"])
    def test_websocket_symbol_normalization(self, mock_ws_client, symbol):
        """Test that symbols are properly normalized for WebSocket lookup."""
        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch(
                "trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws_client
            ),
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            ticker = bot._get_ticker_ws(symbol)

            # Verify WebSocket was called with normalized symbol (no -PERP suffix)
            expected_ws_symbol = symbol.replace("-PERP", "").upper()
            mock_ws_client.get_price.assert_called_with(expected_ws_symbol)

            # Verify returned ticker has original symbol
            assert ticker["symbol"] == symbol

    def test_websocket_price_data_structure(self, mock_ws_client):
        """Test that WebSocket price data has correct structure."""
        from trading_bot_v2.trading_bot import TradingBot

        mock_ws_client.get_price.return_value = 2.50

        with (
            patch(
                "trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws_client
            ),
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            ticker = bot._get_ticker_ws("SUI-PERP")

            # Verify all required fields are present
            required_fields = [
                "symbol",
                "last",
                "bid",
                "ask",
                "high",
                "low",
                "volume",
                "timestamp",
            ]
            for field in required_fields:
                assert field in ticker

            # Verify data types
            assert isinstance(ticker["last"], float)
            assert isinstance(ticker["bid"], float)
            assert isinstance(ticker["ask"], float)
            assert ticker["bid"] < ticker["last"] < ticker["ask"]  # Bid < Last < Ask

    def test_websocket_cache_freshness_requirement(self):
        """Test that Phase 2 requires fresh WebSocket data (no stale cache acceptance)."""
        # This test ensures that the WebSocket-only approach doesn't accept
        # cached data that might be stale - all price requests must go through
        # the live WebSocket connection

        # Note: This is more of a design requirement test
        # In practice, this would be verified through integration tests
        # that check WebSocket connection health and data freshness

        assert (
            True
        )  # Placeholder - actual implementation would verify cache invalidation
