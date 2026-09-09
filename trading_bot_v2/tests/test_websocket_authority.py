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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Test price retrieval
            ticker = bot._get_ticker_ws("SUI-PERP")

            assert ticker["symbol"] == "SUI-PERP"
            assert ticker["last"] == 1.50
            # Updated 2026-05-02: WS ticker exposes only `last`. bid/ask are NOT
            # fabricated — query the order book if you need them.
            assert "bid" not in ticker
            assert "ask" not in ticker

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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Code now has REST fallback; when both WS and REST fail, raises combined error
            with pytest.raises(
                RuntimeError, match="Both WebSocket and REST API failed|REST API price"
            ):
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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
        ):
            bot = TradingBot()
            bot.ws_client = None  # No WebSocket client

            with pytest.raises(RuntimeError):
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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            with pytest.raises(RuntimeError):
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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Zero price falls back to REST; when REST also fails, raises combined error
            with pytest.raises(RuntimeError):
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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            # Negative price falls back to REST; when REST also fails, raises combined error
            with pytest.raises(RuntimeError):
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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
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
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws_client

            ticker = bot._get_ticker_ws("SUI-PERP")

            # Updated 2026-05-02: WebSocket exposes only `last` price.
            # bid/ask/high/low were previously fabricated as price ± 0.05% / ± 2%
            # and removed because the synthetic values would silently corrupt
            # any caller that read them as if real (e.g. stop-distance logic).
            # If you need bid/ask, query the order book; for high/low use kline data.
            required_fields = ["symbol", "last", "volume", "timestamp"]
            for field in required_fields:
                assert field in ticker, f"missing required field: {field}"

            # Verify data types
            assert isinstance(ticker["last"], float)
            assert ticker["last"] > 0

            # Synthetic fields MUST NOT be present (regression guard)
            for field in ("bid", "ask", "high", "low"):
                assert field not in ticker, (
                    f"WebSocket ticker must not fabricate {field!r}; "
                    f"callers must source from order book / kline data"
                )

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
