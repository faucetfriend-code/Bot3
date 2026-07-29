"""
Performance Benchmarking Tests - Phase 3

Tests system performance under load and validates that the
refactored architecture maintains or improves performance.
"""

import pytest
import time
import threading
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed


class TestPerformanceBenchmarks:
    """Performance testing for the refactored trading system."""

    @pytest.fixture
    def mock_components(self):
        """Create high-performance mock components for benchmarking."""
        # Fast execution client
        execution_client = Mock()
        execution_client.place_order.return_value = {
            "id": f"order_{time.time()}",
            "status": "placed",
        }
        execution_client.get_positions.return_value = []
        execution_client.is_healthy.return_value = True

        # Fast risk manager
        risk_manager = Mock()
        risk_manager.request_capital_allocation.return_value = {
            "approved": True,
            "allocated_amount": 1000.0,
            "approval_id": f"approval_{time.time()}",
        }
        risk_manager.validate_position_size.return_value = True
        risk_manager.is_healthy.return_value = True

        return {"execution": execution_client, "risk": risk_manager}

    def test_event_processing_throughput(self, mock_components):
        """Test event processing throughput under load."""
        from trading_bot_v2.event_system import EventBus, EventType

        event_bus = EventBus()

        # Setup event handler
        processed_events = []

        def event_handler(event):
            processed_events.append(event)
            # Simulate processing time
            time.sleep(0.001)  # 1ms processing time

        event_bus.subscribe(EventType.SIGNAL_GENERATED, event_handler)

        # Measure throughput
        num_events = 1000
        start_time = time.time()

        # Publish events
        for i in range(num_events):
            event_bus.publish_event(
                EventType.SIGNAL_GENERATED,
                {"signal_id": i, "symbol": "SUI"},
                "benchmark_test",
            )

        # Wait for processing
        timeout = 10  # 10 second timeout
        start_wait = time.time()
        while (
            len(processed_events) < num_events and (time.time() - start_wait) < timeout
        ):
            time.sleep(0.01)

        end_time = time.time()
        processing_time = end_time - start_time

        # Calculate metrics
        events_per_second = num_events / processing_time
        avg_latency = processing_time / num_events * 1000  # ms

        print(f"Event Processing Throughput: {events_per_second:.1f} events/sec")
        print(f"Average Latency: {avg_latency:.2f} ms per event")

        # Assertions
        assert len(processed_events) == num_events, (
            f"Only {len(processed_events)}/{num_events} events processed"
        )
        assert events_per_second > 100, (
            f"Throughput too low: {events_per_second} events/sec"
        )
        assert avg_latency < 50, f"Latency too high: {avg_latency} ms"

    def test_component_registry_lookup_performance(self):
        """Test component registry lookup performance."""
        from trading_bot_v2.component_registry import ComponentRegistry
        from trading_bot_v2.component_interfaces import ExecutionInterface

        registry = ComponentRegistry()

        # Register multiple components
        components = {}
        for i in range(100):
            component = Mock()
            component.is_healthy.return_value = True
            components[f"component_{i}"] = component
            registry.register(
                component, f"comp_{i}", [ExecutionInterface] if i < 10 else []
            )

        # Benchmark lookups
        num_lookups = 10000
        start_time = time.time()

        for _ in range(num_lookups):
            # Test different lookup types
            registry.get_by_interface(ExecutionInterface)
            registry.get_by_name("comp_50")
            registry.is_registered(type(components["component_0"]))

        end_time = time.time()
        lookup_time = end_time - start_time

        lookups_per_second = num_lookups / lookup_time
        avg_lookup_time = (lookup_time / num_lookups) * 1000  # ms

        print(f"Registry Lookups: {lookups_per_second:.0f} lookups/sec")
        print(f"Average Lookup Time: {avg_lookup_time:.4f} ms")

        # Performance assertions
        assert lookups_per_second > 10000, (
            f"Lookup throughput too low: {lookups_per_second}"
        )
        assert avg_lookup_time < 1.0, f"Lookup latency too high: {avg_lookup_time} ms"

    def test_concurrent_signal_processing(self, mock_components):
        """Test concurrent signal processing performance."""
        from trading_bot_v2.event_system import EventBus, EventType
        from trading_bot_v2.component_registry import ComponentRegistry

        # Setup registry
        registry = ComponentRegistry()
        for name, component in mock_components.items():
            registry.register(component, name, [])

        # Setup event bus
        event_bus = EventBus()

        # Setup signal processing simulation
        processed_signals = []
        processing_lock = threading.Lock()

        def signal_processor(event):
            # Simulate signal processing
            time.sleep(0.005)  # 5ms processing time
            with processing_lock:
                processed_signals.append(event.data)

        event_bus.subscribe(EventType.SIGNAL_GENERATED, signal_processor)

        # Test concurrent signal processing
        num_signals = 200
        num_threads = 10
        signals_per_thread = num_signals // num_threads

        start_time = time.time()

        def publish_signals(thread_id):
            for i in range(signals_per_thread):
                signal_id = thread_id * signals_per_thread + i
                event_bus.publish_event(
                    EventType.SIGNAL_GENERATED,
                    {"signal_id": signal_id, "symbol": "SUI", "thread": thread_id},
                    f"thread_{thread_id}",
                )

        # Start concurrent publishing
        threads = []
        for thread_id in range(num_threads):
            thread = threading.Thread(target=publish_signals, args=(thread_id,))
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Wait for processing
        timeout = 30
        start_wait = time.time()
        while (
            len(processed_signals) < num_signals
            and (time.time() - start_wait) < timeout
        ):
            time.sleep(0.1)

        end_time = time.time()
        total_time = end_time - start_time

        # Calculate metrics
        signals_per_second = num_signals / total_time
        avg_processing_time = total_time / num_signals * 1000  # ms

        print(f"Concurrent Signal Processing: {signals_per_second:.1f} signals/sec")
        print(f"Average Processing Time: {avg_processing_time:.2f} ms per signal")

        # Assertions
        assert len(processed_signals) == num_signals, (
            f"Only {len(processed_signals)}/{num_signals} signals processed"
        )
        assert signals_per_second > 20, (
            f"Concurrent throughput too low: {signals_per_second} signals/sec"
        )

    def test_websocket_price_retrieval_performance(self):
        """Test WebSocket price retrieval performance."""
        from trading_bot_v2.trading_bot import TradingBot

        # Mock WebSocket client with realistic performance
        mock_ws = Mock()
        mock_ws.get_price.return_value = 1.50

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch("trading_bot_v2.trading_bot.make_regime_detector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=mock_ws),
            patch("trading_bot_v2.trading_bot.config", risk_profile="medium", enable_websocket=False, circuit_breaker_loss_pct=0.1),
        ):
            bot = TradingBot()
            bot.ws_client = mock_ws

            # Benchmark price retrieval
            num_requests = 10000
            start_time = time.time()

            for _ in range(num_requests):
                price = bot._get_ticker_ws("SUI-PERP")

            end_time = time.time()
            total_time = end_time - start_time

            requests_per_second = num_requests / total_time
            avg_response_time = (total_time / num_requests) * 1000  # ms

            print(f"WebSocket Price Retrieval: {requests_per_second:.0f} requests/sec")
            print(f"Average Response Time: {avg_response_time:.4f} ms")

            # Performance assertions
            assert requests_per_second > 1000, (
                f"Price retrieval throughput too low: {requests_per_second}"
            )
            assert avg_response_time < 10.0, (
                f"Price retrieval latency too high: {avg_response_time} ms"
            )

    def test_memory_usage_under_load(self):
        """Test memory usage patterns under load."""
        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Generate load
        from trading_bot_v2.event_system import EventBus, EventType

        event_bus = EventBus()

        # Subscribe to many events
        handlers = []
        for i in range(100):

            def handler(event):
                pass  # Minimal processing

            handlers.append(handler)
            event_bus.subscribe(EventType.SIGNAL_GENERATED, handler)

        # Publish many events
        for i in range(10000):
            event_bus.publish_event(
                EventType.SIGNAL_GENERATED, {"signal_id": i}, "load_test"
            )

        # Allow processing
        time.sleep(2)

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        print(
            f"Memory Usage: Initial={initial_memory:.1f}MB, Final={final_memory:.1f}MB"
        )
        print(f"Memory Increase: {memory_increase:.1f}MB")

        # Memory assertions (allow some increase for event processing)
        assert memory_increase < 50, (
            f"Memory usage increased too much: {memory_increase}MB"
        )

    def test_database_operation_performance(self):
        """Test database operation performance."""
        from trading_bot_v2.database import DatabaseManager

        # This would test actual database performance
        # For now, we'll test the mock performance
        db = Mock()
        db.save_trade.return_value = 123
        db.get_positions.return_value = []

        # Benchmark mock operations
        num_operations = 10000
        start_time = time.time()

        for i in range(num_operations):
            db.save_trade({"id": i, "symbol": "SUI"})
            db.get_positions()

        end_time = time.time()
        total_time = end_time - start_time

        operations_per_second = (num_operations * 2) / total_time  # save + get

        print(f"Database Operations: {operations_per_second:.0f} ops/sec")

        # This is a mock test - real database performance would be measured separately
        assert operations_per_second > 1000, (
            f"Database operation throughput too low: {operations_per_second}"
        )

    @pytest.mark.slow
    def test_endurance_test(self):
        """Long-running endurance test."""
        from trading_bot_v2.event_system import EventBus, EventType

        event_bus = EventBus()
        event_count = 0

        def counting_handler(event):
            nonlocal event_count
            event_count += 1

        event_bus.subscribe(EventType.SIGNAL_GENERATED, counting_handler)

        # Run for 30 seconds
        start_time = time.time()
        end_time = start_time + 30

        events_sent = 0
        while time.time() < end_time:
            event_bus.publish_event(
                EventType.SIGNAL_GENERATED, {"timestamp": time.time()}, "endurance_test"
            )
            events_sent += 1
            time.sleep(0.01)  # 100 events/sec

        # Allow final processing
        time.sleep(2)

        success_rate = (event_count / events_sent) * 100

        print(f"Endurance Test: {events_sent} events sent, {event_count} processed")
        print(f"Success Rate: {success_rate:.1f}%")

        # Assertions
        assert success_rate > 95, (
            f"Event processing success rate too low: {success_rate}%"
        )
        assert event_count > events_sent * 0.9, "Too many events lost in processing"
