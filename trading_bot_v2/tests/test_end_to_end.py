"""
End-to-End Integration Tests - Phase 3

Tests complete trading workflows from signal generation to execution.
Validates the entire component-based system integration.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from trading_bot_v2.event_system import EventBus, EventType, get_event_bus
from trading_bot_v2.component_registry import get_component_registry
from trading_bot_v2.component_interfaces import (
    ExecutionInterface,
    RiskInterface,
    GridInterface,
    RegimeInterface,
    StrategyInterface,
    DatabaseInterface,
)


class TestEndToEndTradingFlow:
    """Test complete trading workflows end-to-end."""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Reset global systems before each test."""
        # Clear component registry
        registry = get_component_registry()
        registry.clear()

        # Clear event bus
        event_bus = get_event_bus()
        event_bus.clear_history()

        # Reset any global state
        yield

        # Cleanup after test
        registry.clear()
        event_bus.clear_history()

    @pytest.fixture
    def mock_components(self):
        """Create comprehensive mock components for testing."""
        # Mock execution client
        execution_client = Mock(spec=ExecutionInterface)
        execution_client.place_order.return_value = {
            "id": "test_order_123",
            "status": "placed",
            "price": 1.50,
            "quantity": 100,
        }
        execution_client.get_positions.return_value = []
        execution_client.is_healthy.return_value = True

        # Mock risk manager
        risk_manager = Mock(spec=RiskInterface)
        risk_manager.request_capital_allocation.return_value = {
            "approved": True,
            "allocated_amount": 1000.0,
            "approval_id": "test_approval_123",
        }
        risk_manager.validate_position_size.return_value = True
        risk_manager.is_healthy.return_value = True

        # Mock grid manager
        grid_manager = Mock(spec=GridInterface)
        grid_manager.has_active_grid.return_value = False
        grid_manager.is_healthy.return_value = True

        # Mock regime detector
        regime_detector = Mock(spec=RegimeInterface)
        regime_detector.detect_regime.return_value = "ranging_volatile"
        regime_detector.is_grid_allowed.return_value = True
        regime_detector.get_active_strategies.return_value = ["GridTrading"]
        regime_detector.is_healthy.return_value = True

        # Mock strategy manager
        strategy_manager = Mock(spec=StrategyInterface)
        strategy_manager.generate_signals_for_market.return_value = [
            {
                "asset": "SUI",
                "side": "buy",
                "entry_price": 1.50,
                "stop_loss": 1.35,
                "take_profit": 1.65,
                "strategy": "GridTrading",
                "quantity": 100,
            }
        ]
        strategy_manager.should_skip_signal.return_value = False
        strategy_manager.is_healthy.return_value = True

        # Mock database
        database = Mock(spec=DatabaseInterface)
        database.save_trade.return_value = 123
        database.save_position.return_value = 456
        database.get_positions.return_value = []
        database.get_trades.return_value = []
        database.is_healthy.return_value = True

        return {
            "execution": execution_client,
            "risk": risk_manager,
            "grid": grid_manager,
            "regime": regime_detector,
            "strategy": strategy_manager,
            "database": database,
        }

    def test_complete_signal_to_execution_flow(self, mock_components):
        """Test complete flow: Signal → Approval → Execution → Persistence."""
        # Setup component registry
        registry = get_component_registry()
        for name, component in mock_components.items():
            if name == "regime":
                registry.register(component, "regime_detector", [RegimeInterface])
            else:
                registry.register(component, name, [])

        # Import and setup TradingBot
        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch("trading_bot_v2.trading_bot.MarketRegimeDetector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=None),
        ):
            # Create bot with mocked dependencies
            bot = TradingBot()
            bot.client = mock_components["execution"]
            bot.risk_manager = mock_components["risk"]
            bot.grid_lifecycle = mock_components["grid"]
            bot.strategy_manager = mock_components["strategy"]

            # Setup component registry in bot
            bot.component_registry = registry
            bot.event_bus = get_event_bus()

            # Setup event subscriptions
            bot._setup_event_subscriptions()

            # Simulate signal generation and publishing
            signal_data = {
                "signal": {
                    "asset": "SUI",
                    "side": "buy",
                    "entry_price": 1.50,
                    "stop_loss": 1.35,
                    "take_profit": 1.65,
                    "strategy": "GridTrading",
                    "quantity": 100,
                },
                "market_data": {},
                "current_price": 1.50,
                "symbol": "SUI",
            }

            # Publish signal event
            bot.event_bus.publish_event(EventType.SIGNAL_GENERATED, signal_data, "test")

            # Allow event processing
            time.sleep(0.1)

            # Verify capital allocation was requested
            mock_components["risk"].request_capital_allocation.assert_called()

            # Verify order was placed
            mock_components["execution"].place_order.assert_called_with(
                "SUI", "buy", 100, "market"
            )

            # Verify trade was saved
            mock_components["database"].save_trade.assert_called()

    def test_regime_based_signal_filtering(self, mock_components):
        """Test that signals are filtered based on market regime."""
        # Setup regime detector to disallow grid trading
        mock_components["regime"].is_grid_allowed.return_value = False

        registry = get_component_registry()
        registry.register(
            mock_components["regime"], "regime_detector", [RegimeInterface]
        )

        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch("trading_bot_v2.trading_bot.MarketRegimeDetector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=None),
        ):
            bot = TradingBot()
            bot.component_registry = registry
            bot.event_bus = get_event_bus()
            bot._setup_event_subscriptions()

            # Publish grid signal in regime that doesn't allow grids
            signal_data = {"signal": {"asset": "SUI", "strategy": "GridTrading"}}

            bot.event_bus.publish_event(EventType.SIGNAL_GENERATED, signal_data, "test")
            time.sleep(0.1)

            # Verify signal was rejected (no capital request)
            mock_components["risk"].request_capital_allocation.assert_not_called()

    def test_capital_allocation_rejection_handling(self, mock_components):
        """Test handling of capital allocation rejection."""
        # Setup risk manager to reject allocation
        mock_components["risk"].request_capital_allocation.return_value = {
            "approved": False,
            "reason": "insufficient_funds",
        }

        registry = get_component_registry()
        for name, component in mock_components.items():
            registry.register(component, name, [])

        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch("trading_bot_v2.trading_bot.MarketRegimeDetector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=None),
        ):
            bot = TradingBot()
            bot.component_registry = registry
            bot.event_bus = get_event_bus()
            bot._setup_event_subscriptions()

            # Publish signal
            signal_data = {"signal": {"asset": "SUI", "strategy": "GridTrading"}}
            bot.event_bus.publish_event(EventType.SIGNAL_GENERATED, signal_data, "test")
            time.sleep(0.1)

            # Verify capital was requested but order was not placed
            mock_components["risk"].request_capital_allocation.assert_called()
            mock_components["execution"].place_order.assert_not_called()

    def test_emergency_stop_propagation(self, mock_components):
        """Test emergency stop event propagation through components."""
        registry = get_component_registry()
        for name, component in mock_components.items():
            registry.register(component, name, [])

        event_bus = get_event_bus()

        # Subscribe to emergency stop event
        emergency_events = []

        def emergency_handler(event):
            emergency_events.append(event)

        event_bus.subscribe(EventType.GRID_EMERGENCY_STOP, emergency_handler)

        # Trigger emergency stop
        event_bus.publish_event(
            EventType.GRID_EMERGENCY_STOP,
            {"symbol": "SUI", "reason": "test_emergency"},
            "test_component",
        )

        time.sleep(0.1)

        # Verify emergency event was received
        assert len(emergency_events) == 1
        assert emergency_events[0].data["symbol"] == "SUI"
        assert emergency_events[0].data["reason"] == "test_emergency"

    def test_component_health_monitoring_integration(self, mock_components):
        """Test component health monitoring in integrated system."""
        registry = get_component_registry()
        for name, component in mock_components.items():
            registry.register(component, name, [])

        # All components should report healthy
        health_status = registry.validate_dependencies()

        # Verify all components are healthy
        assert health_status["all_healthy"] == True
        assert len(health_status["healthy"]) == len(mock_components)
        assert len(health_status["unhealthy"]) == 0

    def test_websocket_authority_maintained(self):
        """Test that WebSocket authority is maintained in integrated system."""
        from trading_bot_v2.trading_bot import TradingBot

        # Mock WebSocket client
        mock_ws = Mock()
        mock_ws.get_price.return_value = 1.50

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch("trading_bot_v2.trading_bot.MarketRegimeDetector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws

            # Test price retrieval (should succeed)
            price = bot._get_ticker_ws("SUI-PERP")
            assert price["last"] == 1.50

            # Test failure case (should raise RuntimeError, not fall back to REST)
            mock_ws.get_price.return_value = None

            with pytest.raises(RuntimeError, match="No WebSocket price available"):
                bot._get_ticker_ws("SUI-PERP")

    def test_event_driven_signal_processing(self, mock_components):
        """Test that signal processing is fully event-driven."""
        registry = get_component_registry()
        for name, component in mock_components.items():
            registry.register(component, name, [])

        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch("trading_bot_v2.trading_bot.MarketRegimeDetector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=None),
        ):
            bot = TradingBot()
            bot.component_registry = registry
            bot.event_bus = get_event_bus()
            bot._setup_event_subscriptions()

            # Verify event subscriptions are active
            event_stats = bot.event_bus.get_stats()
            assert event_stats["total_subscribers"] > 0

            # Publish multiple signal types
            signals_published = 0
            for signal_type in ["GridTrading", "MeanReversion"]:
                signal_data = {
                    "signal": {
                        "asset": "SUI",
                        "strategy": signal_type,
                        "entry_price": 1.50,
                    }
                }
                bot.event_bus.publish_event(
                    EventType.SIGNAL_GENERATED, signal_data, "test"
                )
                signals_published += 1

            time.sleep(0.1)

            # Verify signals were processed (capital allocation called)
            assert (
                mock_components["risk"].request_capital_allocation.call_count
                == signals_published
            )

    def test_coordinator_status_reporting(self, mock_components):
        """Test coordinator status reporting functionality."""
        registry = get_component_registry()
        for name, component in mock_components.items():
            registry.register(component, name, [])

        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch("trading_bot_v2.trading_bot.MarketRegimeDetector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=None),
        ):
            bot = TradingBot()
            bot.component_registry = registry
            bot.event_bus = get_event_bus()

            # Get coordinator status
            status = bot.get_coordinator_status()

            # Verify status structure
            assert "coordinator_type" in status
            assert status["coordinator_type"] == "pure_coordinator"
            assert "components_registered" in status
            assert "event_subscriptions" in status
            assert "component_health" in status

            # Verify component count
            assert status["components_registered"] == len(mock_components)
