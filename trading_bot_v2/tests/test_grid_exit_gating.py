"""Grid force-exit and against-trend close gating.

Adversarial review finding: ``GridLifecycleManager._force_exit`` (the
EMERGENCY_STOP flatten) and the against-trend close in ``_partial_exit``
ignored the ``place_order`` acknowledgment and cleared risk / grid / DB
state regardless, and one exception aborted the whole position loop.

Desired behaviour, mirrored from ``migrated_position_manager``:

* every position is closed reduce-only, sized from the exchange
  (``plan_close_quantity``); an already-flat position sends no order;
* a rejection / exception on one position never aborts the others;
* ``on_grid_emergency_exit`` / ``del _grids[symbol]`` /
  ``delete_grid_state`` happen ONLY when every position is confirmed
  flat; otherwise grid state is kept and a pending-flatten marker is set
  that ``monitor_grids`` retries, capped by ``GRID_FLATTEN_MAX_ATTEMPTS``;
* hitting the cap escalates once: ERROR log, ``CLOSE_ESCALATED`` event,
  best-effort Telegram alert;
* against-trend: the DB "closed" write and ``closed_positions`` entry
  happen only on confirmed executed quantity.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from trading_bot_v2 import grid_lifecycle_manager as glm
from trading_bot_v2.event_system import Event, EventType, get_event_bus
from trading_bot_v2.grid_lifecycle_manager import GridLifecycleManager, GridState
from trading_bot_v2.order_result import OrderResult
from trading_bot_v2.trading_bot import TradingBot

SYMBOL = "SUI"
OK_ACK = {"success": True, "data": {"order_id": "ok"}}
REJECT_ACK = {"success": False, "error": "insufficient margin", "data": {}}


def _pos(side: str, amount: float) -> Dict[str, Any]:
    return {"symbol": SYMBOL, "side": side, "amount": amount}


class FakeExchange:
    """Client stand-in: a mutable position book plus scripted acks.

    ``acks`` are consumed one per ``place_order`` call (the last one
    repeats).  An accepted ack reduces the book by the closed quantity,
    so the manager's post-order re-read sees the fill the way the venue
    would report it.  ``snapshots`` overrides the book for the first N
    ``get_positions`` calls.
    """

    def __init__(
        self,
        positions: List[Dict[str, Any]],
        acks: Optional[List[Any]] = None,
        snapshots: Optional[List[List[Dict[str, Any]]]] = None,
    ) -> None:
        self.book = [dict(p) for p in positions]
        self.acks = list(acks or [OK_ACK])
        self.snapshots = [list(s) for s in (snapshots or [])]
        self.orders: List[Dict[str, Any]] = []
        self.cancelled: List[str] = []
        self.get_positions_error: Optional[Exception] = None

    def get_positions(self) -> List[Dict[str, Any]]:
        if self.get_positions_error is not None:
            raise self.get_positions_error
        if self.snapshots:
            return [dict(p) for p in self.snapshots.pop(0)]
        return [dict(p) for p in self.book]

    def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        self.cancelled.append(symbol)
        return {"success": True}

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        return []

    def place_order(
        self, symbol, side, quantity, order_type, price=None, **kwargs
    ) -> Any:
        self.orders.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                **kwargs,
            }
        )
        ack = self.acks.pop(0) if len(self.acks) > 1 else self.acks[0]
        if isinstance(ack, Exception):
            raise ack
        if OrderResult.from_ack(ack).accepted:
            self._reduce(symbol, "long" if side == "sell" else "short", quantity)
        return ack

    def _reduce(self, symbol: str, position_side: str, quantity: float) -> None:
        remaining = float(quantity)
        for pos in self.book:
            if pos["symbol"] != symbol or pos["side"] != position_side:
                continue
            if remaining <= 0:
                break
            take = min(remaining, float(pos["amount"]))
            pos["amount"] = float(pos["amount"]) - take
            remaining -= take
        self.book = [p for p in self.book if float(p["amount"]) > 0]

    def held(self, position_side: str) -> float:
        return sum(float(p["amount"]) for p in self.book if p["side"] == position_side)


def _risk() -> MagicMock:
    risk = MagicMock()
    risk.grid_exposure = {}
    return risk


def _manager(client: FakeExchange, risk: Optional[MagicMock] = None):
    manager = GridLifecycleManager(client, risk or _risk())
    manager.register_new_grid(SYMBOL, 1000.0, 1.5)
    manager.delete_grid_state = MagicMock()
    manager._update_position_status = MagicMock()
    return manager


def _grid_retained(manager: GridLifecycleManager) -> bool:
    return (
        SYMBOL in manager._grids
        and manager._grids[SYMBOL]["state"] == GridState.EMERGENCY_EXIT
        and SYMBOL in manager.get_pending_flattens()
        and not manager.risk_manager.on_grid_emergency_exit.called
        and not manager.delete_grid_state.called
    )


def _grid_cleared(manager: GridLifecycleManager) -> bool:
    return (
        SYMBOL not in manager._grids
        and SYMBOL not in manager.get_pending_flattens()
        and manager.risk_manager.on_grid_emergency_exit.call_count == 1
        and manager.delete_grid_state.call_count == 1
    )


@pytest.fixture
def escalations():
    """Subscribe a collector to CLOSE_ESCALATED on the global bus."""
    received: List[Event] = []

    def handler(event: Event) -> None:
        received.append(event)

    bus = get_event_bus()
    bus.subscribe(EventType.CLOSE_ESCALATED, handler)
    try:
        yield received
    finally:
        bus.unsubscribe(EventType.CLOSE_ESCALATED, handler)


# ======================================================================
# CLOSE_ESCALATED event member
# ======================================================================


class TestCloseEscalatedEvent:
    def test_member_exists(self):
        assert EventType.CLOSE_ESCALATED.value == "close_escalated"
        assert EventType("close_escalated") is EventType.CLOSE_ESCALATED

    def test_publish_reaches_subscriber(self, escalations):
        get_event_bus().publish_event(
            EventType.CLOSE_ESCALATED, {"symbol": SYMBOL}, "test"
        )
        assert len(escalations) == 1
        assert escalations[0].event_type is EventType.CLOSE_ESCALATED
        assert escalations[0].data == {"symbol": SYMBOL}


# ======================================================================
# _force_exit gating
# ======================================================================


class TestForceExitGating:
    def test_second_position_rejected_keeps_grid_and_marks_pending(self):
        client = FakeExchange(
            [_pos("long", 1.0), _pos("short", 2.0), _pos("long", 3.0)],
            acks=[OK_ACK, REJECT_ACK, OK_ACK],
        )
        manager = _manager(client)

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert result["flat"] is False
        assert [p["qty"] for p in result["positions_closed"]] == [1.0, 3.0]
        assert len(result["positions_pending"]) == 1
        assert result["positions_pending"][0]["side"] == "short"
        assert result["positions_pending"][0]["status"] == "rejected"
        assert len(result["errors"]) == 1
        assert len(client.orders) == 3
        assert all(o["reduce_only"] is True for o in client.orders)
        assert client.held("long") == 0.0 and client.held("short") == 2.0
        assert _grid_retained(manager)
        marker = manager.get_pending_flattens()[SYMBOL]
        assert marker["attempts"] == 1 and marker["reason"] == "EMERGENCY_STOP"
        assert marker["escalated"] is False

    def test_all_confirmed_clears_exposure_and_grid(self):
        client = FakeExchange([_pos("long", 1.0), _pos("short", 2.0)])
        manager = _manager(client)

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert result["flat"] is True
        assert len(result["positions_closed"]) == 2
        assert result["positions_pending"] == [] and result["errors"] == []
        assert client.book == []
        assert _grid_cleared(manager)
        manager.risk_manager.on_grid_emergency_exit.assert_called_once_with(SYMBOL)
        manager.delete_grid_state.assert_called_once_with(SYMBOL)

    def test_ack_fills_confirm_without_position_reread(self):
        filled = {"success": True, "data": {"order_id": "f", "filled_quantity": 2.0}}
        client = FakeExchange([_pos("long", 2.0)], acks=[filled])
        manager = _manager(client)

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert result["flat"] is True
        assert result["positions_closed"][0]["executed"] == 2.0
        assert _grid_cleared(manager)

    def test_exception_on_one_position_does_not_abort_others(self):
        client = FakeExchange(
            [_pos("long", 1.0), _pos("short", 2.0), _pos("long", 3.0)],
            acks=[OK_ACK, RuntimeError("timeout"), OK_ACK],
        )
        manager = _manager(client)

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert len(client.orders) == 3
        assert [p["qty"] for p in result["positions_closed"]] == [1.0, 3.0]
        pending = result["positions_pending"]
        assert len(pending) == 1 and pending[0]["status"] == "error"
        assert "timeout" in pending[0]["detail"]
        assert _grid_retained(manager)

    def test_already_flat_position_sends_no_order(self):
        # Listed at snapshot time, gone by the time the close is sized.
        client = FakeExchange([], snapshots=[[_pos("long", 1.0)]])
        manager = _manager(client)

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert client.orders == []
        assert result["flat"] is True
        assert result["positions_closed"][0]["status"] == "flat"
        assert _grid_cleared(manager)

    def test_zero_quantity_ghost_sends_no_order(self):
        client = FakeExchange([_pos("long", 0.0)])
        manager = _manager(client)

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert client.orders == []
        assert result["flat"] is True and result["positions_closed"] == []
        assert _grid_cleared(manager)

    def test_get_positions_failure_marks_pending_without_orders(self):
        client = FakeExchange([_pos("long", 1.0)])
        client.get_positions_error = RuntimeError("exchange down")
        manager = _manager(client)

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert client.orders == []
        assert result["flat"] is False
        assert any("exchange down" in e for e in result["errors"])
        assert _grid_retained(manager)

    def test_unconfirmed_fill_is_not_accounted(self):
        # Accepted, no fills in the ack, and the re-read fails -> unknown.
        client = FakeExchange([_pos("long", 1.0)])
        manager = _manager(client)
        reads = {"n": 0}
        real_get = client.get_positions

        def flaky_get_positions():
            reads["n"] += 1
            if reads["n"] >= 3:  # 1: snapshot, 2: sizing, 3: confirmation
                raise RuntimeError("read timeout")
            return real_get()

        client.get_positions = flaky_get_positions

        result = manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        assert result["flat"] is False
        assert result["positions_pending"][0]["status"] == "unconfirmed"
        assert _grid_retained(manager)

    def test_emergency_stop_entry_point_routes_through_gating(self):
        client = FakeExchange([_pos("long", 1.0)], acks=[REJECT_ACK])
        manager = _manager(client)

        manager.on_emergency_stop_triggered(SYMBOL)

        assert client.cancelled == [SYMBOL]
        assert client.orders[0]["reduce_only"] is True
        assert _grid_retained(manager)


# ======================================================================
# Retry, cap and escalation
# ======================================================================


class TestPendingFlattenRetry:
    def test_later_retry_success_clears_marker(self):
        client = FakeExchange([_pos("long", 1.0)], acks=[REJECT_ACK, OK_ACK])
        manager = _manager(client)

        manager._force_exit(SYMBOL, "EMERGENCY_STOP")
        assert _grid_retained(manager)

        assert manager.retry_pending_flattens() == 1
        assert _grid_cleared(manager)
        assert client.held("long") == 0.0

    def test_monitor_grids_retries_pending_flattens(self):
        client = FakeExchange([_pos("long", 1.0)], acks=[REJECT_ACK, OK_ACK])
        manager = _manager(client)
        manager._force_exit(SYMBOL, "EMERGENCY_STOP")

        results = manager.monitor_grids({SYMBOL: 1.0})

        assert results["flatten_retries"] == 1
        assert _grid_cleared(manager)

    def test_attempt_cap_respected_and_escalated_once(self, monkeypatch, escalations):
        telegram_calls: List[tuple] = []
        monkeypatch.setattr(
            glm,
            "_send_telegram_error_alert",
            lambda *args: telegram_calls.append(args) or True,
        )
        client = FakeExchange([_pos("long", 1.0)], acks=[REJECT_ACK])
        manager = _manager(client)
        manager._max_flatten_attempts = 2

        manager._force_exit(SYMBOL, "EMERGENCY_STOP")  # attempt 1
        assert manager.retry_pending_flattens() == 1  # attempt 2 (cap)
        assert manager.retry_pending_flattens() == 0  # escalate, no order
        assert manager.retry_pending_flattens() == 0  # already escalated

        assert len(client.orders) == 2
        marker = manager.get_pending_flattens()[SYMBOL]
        assert marker["attempts"] == 2 and marker["escalated"] is True
        assert _grid_retained(manager)

        assert len(escalations) == 1
        event = escalations[0]
        assert event.source == "GridLifecycleManager"
        assert event.data["symbol"] == SYMBOL
        assert event.data["source_kind"] == "grid_flatten"
        assert event.data["attempts"] == 2
        assert event.data["errors"]

        assert len(telegram_calls) == 1
        error_type, message, context = telegram_calls[0]
        assert error_type == "close_escalated"
        assert SYMBOL in message and "retry cap" in message
        assert "grid_flatten" in context

    def test_telegram_failure_never_raises(self, monkeypatch, escalations):
        def boom(*args):
            raise RuntimeError("telegram down")

        monkeypatch.setattr(glm, "_send_telegram_error_alert", boom)
        client = FakeExchange([_pos("long", 1.0)], acks=[REJECT_ACK])
        manager = _manager(client)
        manager._max_flatten_attempts = 1

        manager._force_exit(SYMBOL, "EMERGENCY_STOP")
        manager.retry_pending_flattens()

        assert manager.get_pending_flattens()[SYMBOL]["escalated"] is True
        assert len(escalations) == 1

    def test_event_bus_failure_still_sends_telegram(self, monkeypatch):
        telegram_calls: List[tuple] = []
        monkeypatch.setattr(
            glm,
            "_send_telegram_error_alert",
            lambda *args: telegram_calls.append(args) or True,
        )
        bus = get_event_bus()
        monkeypatch.setattr(
            bus, "publish_event", MagicMock(side_effect=RuntimeError("bus"))
        )
        client = FakeExchange([_pos("long", 1.0)], acks=[REJECT_ACK])
        manager = _manager(client)
        manager._max_flatten_attempts = 1

        manager._force_exit(SYMBOL, "EMERGENCY_STOP")
        manager.retry_pending_flattens()

        assert len(telegram_calls) == 1

    def test_max_attempts_env(self, monkeypatch):
        monkeypatch.setenv("GRID_FLATTEN_MAX_ATTEMPTS", "3")
        assert (
            GridLifecycleManager(FakeExchange([]), _risk())._max_flatten_attempts == 3
        )
        monkeypatch.setenv("GRID_FLATTEN_MAX_ATTEMPTS", "0")
        assert (
            GridLifecycleManager(FakeExchange([]), _risk())._max_flatten_attempts == 1
        )
        monkeypatch.setenv("GRID_FLATTEN_MAX_ATTEMPTS", "junk")
        assert (
            GridLifecycleManager(FakeExchange([]), _risk())._max_flatten_attempts == 5
        )
        monkeypatch.delenv("GRID_FLATTEN_MAX_ATTEMPTS")
        assert (
            GridLifecycleManager(FakeExchange([]), _risk())._max_flatten_attempts == 5
        )


# ======================================================================
# TradingBot keeps driving the retry
# ======================================================================


class TestBotMonitorDrivesRetry:
    @staticmethod
    def _bot(lifecycle: MagicMock) -> SimpleNamespace:
        return SimpleNamespace(
            grid_lifecycle=lifecycle, _get_ticker_ws=lambda symbol: {"last": 1.0}
        )

    def test_pending_flatten_without_active_grid_still_monitored(self):
        lifecycle = MagicMock()
        lifecycle._grids = {}
        lifecycle.get_pending_flattens.return_value = {SYMBOL: {"attempts": 1}}
        lifecycle.monitor_grids.return_value = {"new_fills": 0, "alerts": []}

        TradingBot._monitor_grids(self._bot(lifecycle))

        lifecycle.monitor_grids.assert_called_once()

    def test_nothing_to_do_skips_monitor(self):
        lifecycle = MagicMock()
        lifecycle._grids = {}
        lifecycle.get_pending_flattens.return_value = {}

        TradingBot._monitor_grids(self._bot(lifecycle))

        lifecycle.monitor_grids.assert_not_called()


# ======================================================================
# Against-trend close in _partial_exit
# ======================================================================


class TestAgainstTrendClose:
    def test_rejection_leaves_status_unchanged(self):
        client = FakeExchange([_pos("short", 2.0)], acks=[REJECT_ACK])
        manager = _manager(client)

        result = manager._partial_exit(SYMBOL, "REGIME_CHANGE", "up")

        assert result["success"] is False
        assert result["closed_positions"] == []
        assert result["errors"]
        assert client.orders[0]["reduce_only"] is True
        assert client.held("short") == 2.0
        manager._update_position_status.assert_not_called()
        manager.risk_manager.on_grid_emergency_exit.assert_not_called()
        assert SYMBOL in manager._grids

    def test_exception_leaves_status_unchanged(self):
        client = FakeExchange([_pos("short", 2.0)], acks=[RuntimeError("timeout")])
        manager = _manager(client)

        result = manager._partial_exit(SYMBOL, "REGIME_CHANGE", "up")

        assert result["success"] is False
        assert result["closed_positions"] == []
        manager._update_position_status.assert_not_called()
        assert SYMBOL in manager._grids

    def test_confirmed_close_is_recorded(self):
        client = FakeExchange([_pos("short", 2.0)])
        manager = _manager(client)

        result = manager._partial_exit(SYMBOL, "REGIME_CHANGE", "up")

        assert result["success"] is True
        assert len(result["closed_positions"]) == 1
        closed = result["closed_positions"][0]
        assert closed["side"] == "short" and closed["executed"] == 2.0
        assert client.orders[0]["side"] == "buy"
        assert client.orders[0]["reduce_only"] is True
        manager._update_position_status.assert_called_once_with(
            SYMBOL, "short", 2.0, "closed", "against_trend"
        )
        assert client.held("short") == 0.0

    def test_with_trend_position_is_kept_not_closed(self):
        client = FakeExchange([_pos("long", 2.0)])
        manager = _manager(client)

        result = manager._partial_exit(SYMBOL, "REGIME_CHANGE", "up")

        assert client.orders == []
        assert result["success"] is True
        assert len(result["kept_positions"]) == 1
        manager._update_position_status.assert_called_once_with(
            SYMBOL, "long", 2.0, "migrated", "trend_aligned"
        )
