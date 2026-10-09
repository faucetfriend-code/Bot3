"""
Event System - Decoupled component communication.

Provides event-driven communication between components without tight coupling.
Components can publish events and subscribe to events they're interested in.
"""

import logging
import threading
import time
from collections import deque
from typing import Dict, List, Any, Callable, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Standard event types for component communication."""

    # Trading Events
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_EXECUTED = "signal_executed"
    SIGNAL_REJECTED = "signal_rejected"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"

    # Risk Events
    CAPITAL_REQUESTED = "capital_requested"
    CAPITAL_APPROVED = "capital_approved"
    CAPITAL_REJECTED = "capital_rejected"
    RISK_LIMIT_EXCEEDED = "risk_limit_exceeded"

    # Reconciliation Events
    POSITION_DISCREPANCY = "position_discrepancy"
    # A close that reached its retry cap and now needs manual action
    CLOSE_ESCALATED = "close_escalated"

    # Grid Events
    GRID_CREATED = "grid_created"
    GRID_EMERGENCY_STOP = "grid_emergency_stop"
    GRID_REGIME_CHANGE = "grid_regime_change"

    # Market Events
    REGIME_CHANGED = "regime_changed"
    PRICE_UPDATED = "price_updated"

    # System Events
    COMPONENT_HEALTH_CHECK = "component_health_check"
    COMPONENT_FAILURE = "component_failure"
    SYSTEM_SHUTDOWN = "system_shutdown"


class Event:
    """Represents an event in the system."""

    def __init__(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        source: str,
        timestamp: Optional[datetime] = None,
    ):
        """
        Initialize event.

        Args:
            event_type: Type of event
            data: Event payload data
            source: Component that generated the event
            timestamp: Event timestamp (defaults to now)
        """
        self.event_type = event_type
        self.data = data
        self.source = source
        self.timestamp = timestamp or datetime.now()
        self.id = f"{event_type.value}_{int(time.time() * 1000000)}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "data": self.data,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }


class EventBus:
    """
    Central event bus for component communication.

    Components can publish events and subscribe to events of interest.
    Thread-safe for concurrent component operation.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._lock = threading.Lock()
        self._max_history = 1000  # Keep last 1000 events
        self._event_history: deque[Event] = deque(maxlen=self._max_history)
        # Monotonically-increasing counter — never resets, never saturates.
        # Use this (not len(_event_history)) to measure published-event rate;
        # len() permanently returns max_history once the deque is full.
        self._published_count: int = 0

        logger.info("EventBus initialized")

    def subscribe(
        self, event_type: EventType, callback: Callable[[Event], None]
    ) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: Event type to subscribe to
            callback: Function to call when event occurs
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []

            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
                logger.debug(f"Subscribed to {event_type.value}")

    def unsubscribe(
        self, event_type: EventType, callback: Callable[[Event], None]
    ) -> None:
        """
        Unsubscribe from an event type.

        Args:
            event_type: Event type to unsubscribe from
            callback: Callback function to remove
        """
        with self._lock:
            if event_type in self._subscribers:
                if callback in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(callback)
                    logger.debug(f"Unsubscribed from {event_type.value}")

    def publish(self, event: Event) -> None:
        """
        Publish an event to all subscribers.

        Args:
            event: Event to publish
        """
        logger.debug(f"Publishing event: {event.event_type.value} from {event.source}")

        # Store in history (deque auto-evicts oldest when maxlen is reached)
        # Also increment the monotonic counter used for rate measurement.
        with self._lock:
            self._event_history.append(event)
            self._published_count += 1

        # Notify subscribers (outside lock to prevent blocking)
        subscribers = []
        with self._lock:
            if event.event_type in self._subscribers:
                subscribers = self._subscribers[event.event_type].copy()

        for callback in subscribers:
            try:
                callback(event)
            except Exception as e:
                import traceback

                error_info = {
                    "event_type": event.event_type.value,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                    "timestamp": event.timestamp.isoformat(),
                }
                if not hasattr(self, "_callback_errors"):
                    self._callback_errors = []
                self._callback_errors.append(error_info)
                if len(self._callback_errors) > 100:
                    self._callback_errors = self._callback_errors[-100:]
                logger.error(
                    f"Error in event callback for {event.event_type.value}: {e}"
                )

    def publish_event(
        self, event_type: EventType, data: Dict[str, Any], source: str
    ) -> None:
        """
        Convenience method to publish an event.

        Args:
            event_type: Type of event
            data: Event data payload
            source: Source component name
        """
        event = Event(event_type, data, source)
        self.publish(event)

    def get_recent_events(self, limit: int = 50) -> List[Event]:
        """
        Get recent events from history.

        Args:
            limit: Maximum number of events to return

        Returns:
            List of recent events (newest first)
        """
        with self._lock:
            history = list(self._event_history)
        return history[-limit:][::-1]  # newest first

    def get_events_by_type(self, event_type: EventType, limit: int = 50) -> List[Event]:
        """
        Get recent events of a specific type.

        Args:
            event_type: Event type to filter by
            limit: Maximum number of events to return

        Returns:
            List of events of the specified type
        """
        with self._lock:
            history = list(self._event_history)
        matching_events = [e for e in history if e.event_type == event_type]
        return matching_events[-limit:][::-1]

    def clear_history(self) -> None:
        """Clear event history."""
        with self._lock:
            self._event_history.clear()
            logger.info("Event history cleared")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get event bus statistics.

        Returns:
            Dictionary with subscriber counts and event counts
        """
        with self._lock:
            subscriber_counts = {
                et.value: len(callbacks) for et, callbacks in self._subscribers.items()
            }
            history = list(self._event_history)
            published_count = self._published_count

        event_counts: Dict[str, int] = {}
        for event in history:
            key = event.event_type.value
            event_counts[key] = event_counts.get(key, 0) + 1

        return {
            "total_subscribers": sum(subscriber_counts.values()),
            "subscriber_counts": subscriber_counts,
            "total_published": published_count,  # monotonic, never saturates
            "history_window": len(history),  # capped at max_history
            "total_events": len(history),  # kept for back-compat
            "event_counts": event_counts,
            "max_history": self._max_history,
        }


# Global event bus instance
_event_bus = EventBus()


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    return _event_bus
