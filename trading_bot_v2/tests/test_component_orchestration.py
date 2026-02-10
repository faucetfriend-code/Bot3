"""
Integration tests for component orchestration and WebSocket authority.

Tests the Phase 2 coordinator pattern and component communication.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from trading_bot_v2.component_interfaces import (
    ExecutionInterface,
    RiskInterface,
    GridInterface,
    RegimeInterface,
    StrategyInterface,
    DatabaseInterface,
)
from trading_bot_v2.event_system import EventBus, EventType, Event
from trading_bot_v2.component_registry import ComponentRegistry


class TestComponentOrchestration:
    """Test component orchestration and communication."""

    @pytest.fixture
    def event_bus(self):
        """Create clean event bus for testing."""
        bus = EventBus()
        bus.clear_history()
        return bus

    @pytest.fixture
    def component_registry(self):
        """Create clean component registry for testing."""
        registry = ComponentRegistry()
        registry.clear()
        return registry

    @pytest.fixture
    def mock_execution_client(self):
        """Mock execution client implementing ExecutionInterface."""
        client = Mock(spec=ExecutionInterface)
        client.place_order.return_value = {"id": "test_order_123", "status": "placed"}
        client.get_positions.return_value = []
        client.is_healthy.return_value = True
        return client

    @pytest.fixture
    def mock_risk_manager(self):
        """Mock risk manager implementing RiskInterface."""
        rm = Mock(spec=RiskInterface)
        rm.request_capital_allocation.return_value = {
            "approved": True,
            "allocated_amount": 1000.0,
            "approval_id": "test_approval_123",
        }
        rm.validate_position_size.return_value = True
        rm.is_healthy.return_value = True
        return rm

    @pytest.fixture
    def mock_grid_manager(self):
        """Mock grid manager implementing GridInterface."""
        gm = Mock(spec=GridInterface)
        gm.has_active_grid.return_value = False
        gm.is_healthy.return_value = True
        return gm

    def test_event_bus_publish_subscribe(self, event_bus):
        """Test basic event publish/subscribe functionality."""
        received_events = []

        def test_callback(event):
            received_events.append(event)

        # Subscribe to event
        event_bus.subscribe(EventType.SIGNAL_GENERATED, test_callback)

        # Publish event
        test_data = {"signal": "test_signal", "symbol": "SUI"}
        event_bus.publish_event(EventType.SIGNAL_GENERATED, test_data, "test_source")

        # Verify event received
        assert len(received_events) == 1
        assert received_events[0].event_type == EventType.SIGNAL_GENERATED
        assert received_events[0].data == test_data
        assert received_events[0].source == "test_source"

    def test_component_registry_registration(
        self, component_registry, mock_execution_client
    ):
        """Test component registration and retrieval."""
        # Register component
        component_registry.register(
            mock_execution_client, "execution", [ExecutionInterface]
        )

        # Retrieve by type
        retrieved = component_registry.get(type(mock_execution_client))
        assert retrieved is mock_execution_client

        # Retrieve by name
        retrieved_by_name = component_registry.get_by_name("execution")
        assert retrieved_by_name is mock_execution_client

        # Retrieve by interface
        execution_clients = component_registry.get_by_interface(ExecutionInterface)
        assert len(execution_clients) == 1
        assert execution_clients[0] is mock_execution_client

    def test_component_registry_single_interface(
        self, component_registry, mock_execution_client
    ):
        """Test single component retrieval by interface."""
        component_registry.register(
            mock_execution_client, interfaces=[ExecutionInterface]
        )

        single_client = component_registry.get_single_by_interface(ExecutionInterface)
        assert single_client is mock_execution_client

    def test_event_bus_statistics(self, event_bus):
        """Test event bus statistics tracking."""
        # Subscribe to some events
        event_bus.subscribe(EventType.SIGNAL_GENERATED, lambda e: None)
        event_bus.subscribe(EventType.ORDER_PLACED, lambda e: None)

        # Publish some events
        event_bus.publish_event(EventType.SIGNAL_GENERATED, {}, "test")
        event_bus.publish_event(EventType.ORDER_PLACED, {}, "test")

        stats = event_bus.get_stats()
        assert stats["total_subscribers"] == 2
        assert stats["total_events"] == 2
        assert EventType.SIGNAL_GENERATED.value in stats["event_counts"]

    def test_component_health_validation(
        self, component_registry, mock_execution_client
    ):
        """Test component health validation."""
        # Register healthy component
        component_registry.register(mock_execution_client)

        health_status = component_registry.validate_dependencies()
        assert health_status["all_healthy"] == True
        assert len(health_status["healthy"]) == 1

    def test_event_history_management(self, event_bus):
        """Test event history size limits."""
        # Publish many events
        for i in range(1500):  # More than default limit of 1000
            event_bus.publish_event(EventType.SIGNAL_GENERATED, {"id": i}, "test")

        # Check history is capped
        history = event_bus.get_recent_events(2000)
        assert len(history) <= 1000  # Should be capped at max_history

    def test_event_type_filtering(self, event_bus):
        """Test filtering events by type."""
        # Publish different event types
        event_bus.publish_event(EventType.SIGNAL_GENERATED, {"type": "signal"}, "test")
        event_bus.publish_event(EventType.ORDER_PLACED, {"type": "order"}, "test")
        event_bus.publish_event(EventType.SIGNAL_GENERATED, {"type": "signal2"}, "test")

        # Get events by type
        signal_events = event_bus.get_events_by_type(EventType.SIGNAL_GENERATED)
        order_events = event_bus.get_events_by_type(EventType.ORDER_PLACED)

        assert len(signal_events) == 2
        assert len(order_events) == 1
        assert all(e.event_type == EventType.SIGNAL_GENERATED for e in signal_events)
        assert all(e.event_type == EventType.ORDER_PLACED for e in order_events)


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
