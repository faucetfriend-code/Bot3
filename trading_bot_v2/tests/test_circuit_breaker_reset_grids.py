"""A manual circuit breaker reset clears the grid state a trip stripped.

Owner decision: after a trip has cancelled a grid's entry orders the grid
is left registered ACTIVE with nothing resting, which blocks a new grid on
that symbol.  The manual reset must clear that leftover state (through
``GridLifecycleManager.clear_grid``, the call behind
``POST /api/grids/{symbol}/clear``) so the normal placement path can build
a fresh grid.  The reset itself sends no order and no cancel.

No network, no live database; the exchange is a MagicMock stand-in and
conftest points the supervisor state file at a temp path.

Covered:
* reset clears a stripped grid, places nothing, cancels nothing more;
* a grid the sweep did not touch survives the reset;
* a new grid can be placed on the symbol afterwards through the normal
  path;
* a stripped grid that still manages an open position is NOT cleared (its
  software emergency stop lives in the grid state), is reported, and is
  cleared by a later reset once flat;
* a venue read error or a failing clear never raises and is reported;
* after a restart (no in-memory record of the sweep) reset clears ACTIVE
  grids with no live entry orders and no position, and nothing else;
* the reset response and both status payloads name the symbols.
"""

import threading
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from trading_bot_v2 import api_server
from trading_bot_v2.config import AssetClass, MarketState, StrategyType, TradeQuality
from trading_bot_v2.grid_lifecycle_manager import GridLifecycleManager, GridState
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.trading_bot import TradingBot

TOKEN = "correct-horse-battery-staple"
RESET_ROUTE = "/api/bot/circuit-breaker/reset"
ACK = {"success": True, "data": {"order_id": "new-1"}}
LONG_BTC = [{"symbol": "BTC", "side": "bid", "amount": "0.5"}]

#: Three BTC grid levels plus a venue stop that is not a grid order.
BTC_LEVELS = [
    {"order_id": 1, "symbol": "BTC", "side": "bid", "price": "990", "amount": "0.5"},
    {"order_id": 2, "symbol": "BTC", "side": "ask", "price": "1010", "amount": "0.5"},
    {"order_id": 3, "symbol": "BTC", "side": "ask", "price": "1020", "amount": "0.5"},
]
BTC_STOP = {
    "order_id": 9,
    "symbol": "BTC",
    "side": "ask",
    "order_type": "stop_market",
    "reduce_only": True,
    "stop_price": "900",
}


def _grid_signal() -> Signal:
    """A fully flagged grid signal for BTC."""
    return Signal(
        strategy=StrategyType.GRID_TRADING,
        asset="BTC",
        asset_class=AssetClass.PERPETUAL,
        side=OrderSide.BUY,
        entry_price=1000.0,
        stop_loss=950.0,
        take_profit=1100.0,
        confidence=0.8,
        quality=TradeQuality.STANDARD,
        market_state=MarketState.RANGE,
        volume_confirmation=True,
        multi_timeframe_alignment=True,
        support_resistance_valid=True,
        rrr_meets_minimum=True,
        liquidation_buffer_safe=True,
        account_risk_ok=True,
        margin_drawdown_ok=True,
        forbidden_conditions_clear=True,
    )


class _Venue:
    """A mocked venue whose order book shrinks as cancels are accepted."""

    def __init__(self, orders, positions=None) -> None:
        self.orders = list(orders)
        self.positions = list(positions or [])
        self.client = MagicMock()
        self.client.get_orders.side_effect = lambda: list(self.orders)
        self.client.get_positions.side_effect = lambda: list(self.positions)
        self.client.cancel_order.side_effect = self._cancel
        self.client.place_order.return_value = ACK

    def _cancel(self, symbol, order_id):
        self.orders = [o for o in self.orders if str(o["order_id"]) != str(order_id)]
        return {"success": True}


def _grid(order_ids=None) -> dict:
    """ACTIVE grid state with an emergency stop."""
    return {
        "state": GridState.ACTIVE,
        "grid_spacing": 10.0,
        "emergency_stop": 900.0,
        "order_ids": set(order_ids) if order_ids is not None else None,
    }


def _bot(venue: _Venue, grids: dict) -> TradingBot:
    """A TradingBot shell (no __init__) over a real grid manager."""
    bot = TradingBot.__new__(TradingBot)
    bot.client = venue.client
    bot._exchange = MagicMock()
    bot._exchange.rest_client = bot.client
    bot.exchange.place_order.return_value = ACK
    bot.signal_logger = MagicMock()
    bot.risk_manager = MagicMock()
    bot.hub_publish_func = None
    bot.ws_client = None
    bot.thread = None
    bot._running_event = threading.Event()
    bot._circuit_breaker_triggered = False
    bot._circuit_breaker_tripped_at = None
    bot._circuit_breaker_reason = None
    bot._circuit_breaker_loss_pct = 0.10
    bot._get_account_balance = lambda: 1_000.0
    bot._get_current_exposure = lambda: 0.0
    manager = GridLifecycleManager(client=venue.client, risk_manager=MagicMock())
    manager._grids.update(grids)
    manager.entry_gate = bot._entry_block_reason
    bot.grid_lifecycle = manager
    return bot


def _stripped(positions=None, extra_grids=None) -> tuple:
    """A bot whose trip has swept the BTC grid's entry orders.

    Returns:
        Tuple ``(bot, venue)`` after the real trip path has run.
    """
    venue = _Venue(BTC_LEVELS + [BTC_STOP], positions)
    grids = {"BTC": _grid({"1", "2", "3"})}
    grids.update(extra_grids or {})
    bot = _bot(venue, grids)
    bot._trip_circuit_breaker("test trip", {})
    return bot, venue


def _mutating_calls(venue: _Venue) -> int:
    """Order placements and cancels sent to the venue so far."""
    return (
        venue.client.place_order.call_count
        + venue.client.cancel_order.call_count
        + venue.client.cancel_all_orders.call_count
    )


class TestResetClearsStrippedGrid:
    """The grid a trip stripped is cleared; nothing is sent to the venue."""

    def test_stripped_grid_is_cleared_and_named_in_the_response(self):
        bot, venue = _stripped()
        assert bot.grid_lifecycle.has_active_grid("BTC") is True
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == ["BTC"]
        assert previous["grids_not_cleared"] == {}
        assert previous["grid_clear_errors"] == []
        assert "BTC" not in bot.grid_lifecycle._grids
        assert bot._circuit_breaker_triggered is False

    def test_reset_places_no_order_and_sends_no_cancel(self):
        bot, venue = _stripped()
        before = _mutating_calls(venue)
        assert venue.client.cancel_order.call_count == 3
        bot.reset_circuit_breaker()
        assert _mutating_calls(venue) == before
        venue.client.place_order.assert_not_called()
        bot.exchange.place_order.assert_not_called()

    def test_venue_stop_resting_on_the_symbol_is_left_alone(self):
        """A protective order is not a grid order: kept, and no blocker."""
        bot, venue = _stripped()
        bot.reset_circuit_breaker()
        assert venue.orders == [BTC_STOP]

    def test_clear_goes_through_the_existing_clear_path(self):
        bot, _venue = _stripped()
        with patch.object(
            GridLifecycleManager, "clear_grid", autospec=True, return_value=True
        ) as clear:
            bot.reset_circuit_breaker()
        clear.assert_called_once_with(bot.grid_lifecycle, "BTC")

    def test_legacy_grid_without_tracked_ids_is_cleared(self):
        venue = _Venue(BTC_LEVELS)
        bot = _bot(venue, {"BTC": _grid(None)})
        bot._trip_circuit_breaker("test trip", {})
        assert bot.reset_circuit_breaker()["grids_cleared"] == ["BTC"]

    def test_reset_without_a_grid_manager_reports_nothing(self):
        bot, _venue = _stripped()
        bot.grid_lifecycle = None
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert previous["grid_clear_errors"] == []


class TestUntouchedGridSurvives:
    """Only grids the sweep cancelled entry orders for are cleared."""

    def test_grid_with_nothing_swept_is_left_alone(self):
        bot, _venue = _stripped(extra_grids={"ETH": _grid({"e1"})})
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == ["BTC"]
        assert bot.grid_lifecycle.has_active_grid("ETH") is True
        assert "ETH" not in previous["grids_not_cleared"]

    def test_overlay_order_cancel_does_not_mark_a_tracked_grid(self):
        """The sweep cancelled an order the grid does not own."""
        venue = _Venue(BTC_LEVELS)
        bot = _bot(venue, {"BTC": _grid({"other"})})
        bot._trip_circuit_breaker("test trip", {})
        assert bot.reset_circuit_breaker()["grids_cleared"] == []
        assert bot.grid_lifecycle.has_active_grid("BTC") is True

    def test_grid_whose_cancels_were_rejected_keeps_its_state(self):
        venue = _Venue(BTC_LEVELS)
        venue.client.cancel_order.side_effect = lambda s, oid: {"success": False}
        bot = _bot(venue, {"BTC": _grid({"1", "2", "3"})})
        bot._trip_circuit_breaker("test trip", {})
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert previous["cancels_failing"] == 3
        assert bot.grid_lifecycle._grids["BTC"]["order_ids"] == {"1", "2", "3"}

    def test_partly_stripped_grid_with_live_levels_is_reported_not_cleared(self):
        venue = _Venue(BTC_LEVELS)
        accepted = venue._cancel
        venue.client.cancel_order.side_effect = lambda s, oid: (
            {"success": False} if str(oid) == "3" else accepted(s, oid)
        )
        bot = _bot(venue, {"BTC": _grid({"1", "2", "3"})})
        bot._trip_circuit_breaker("test trip", {})
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert "resting" in previous["grids_not_cleared"]["BTC"]
        assert bot.grid_lifecycle._grids["BTC"]["order_ids"] == {"3"}


class TestNewGridAfterReset:
    """The normal placement path builds a fresh grid once reset has run."""

    def test_new_grid_is_placed_and_registered_after_reset(self):
        bot, venue = _stripped()
        bot._calculate_grid_levels = MagicMock(
            return_value={
                "buy_levels": [{"price": 990.0, "quantity": 0.1}],
                "sell_levels": [{"price": 1010.0, "quantity": 0.1}],
                "grid_spacing": 10.0,
                "current_price": 1000.0,
            }
        )
        signal = _grid_signal()
        bot._execute_grid_signal_coordinated(signal, {"allocated_amount": 100.0})
        bot.exchange.place_order.assert_not_called()

        bot.reset_circuit_breaker()
        bot.exchange.place_order.assert_not_called()

        bot._execute_grid_signal_coordinated(signal, {"allocated_amount": 100.0})
        assert bot.exchange.place_order.call_count == 2
        grid = bot.grid_lifecycle._grids["BTC"]
        assert grid["state"] == GridState.ACTIVE
        assert grid["order_ids"] == {"new-1"}
        assert grid["emergency_stop"] == pytest.approx(990.0 * 0.95)
        assert not grid.get("breaker_stripped")


class TestOpenPositionKeepsStopProtection:
    """A grid still managing inventory is not cleared by the reset."""

    def test_grid_with_an_open_position_is_kept_and_reported(self):
        bot, venue = _stripped(positions=LONG_BTC)
        # Sell 2 only closes the 0.5 long: the sweep kept it resting.
        assert [o["order_id"] for o in venue.orders] == [2, 9]
        before = _mutating_calls(venue)
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert "open position" in previous["grids_not_cleared"]["BTC"]
        assert bot.grid_lifecycle.has_active_grid("BTC") is True
        assert bot.grid_lifecycle._grids["BTC"]["emergency_stop"] == 900.0
        assert [o["order_id"] for o in venue.orders] == [2, 9]
        assert _mutating_calls(venue) == before

    def test_emergency_stop_still_fires_for_the_kept_grid(self):
        bot, _venue = _stripped(positions=LONG_BTC)
        bot.reset_circuit_breaker()
        manager = bot.grid_lifecycle
        with patch.object(manager, "_force_exit") as force_exit:
            manager._check_emergency_stop("BTC", 950.0)
            force_exit.assert_not_called()
            manager._check_emergency_stop("BTC", 899.0)
        force_exit.assert_called_once_with("BTC", reason="EMERGENCY_STOP")

    def test_kept_grid_is_shown_in_the_status_payload(self):
        bot, _venue = _stripped(positions=LONG_BTC)
        bot.reset_circuit_breaker()
        status = bot.get_status()
        assert status["circuit_breaker_grids_cleared"] == []
        assert "BTC" in status["circuit_breaker_grids_not_cleared"]

    def test_a_later_reset_clears_it_once_flat(self):
        bot, venue = _stripped(positions=LONG_BTC)
        bot.reset_circuit_breaker()
        venue.positions = []
        venue.orders = [BTC_STOP]
        previous = bot.reset_circuit_breaker()
        assert previous["was_tripped"] is False
        assert previous["grids_cleared"] == ["BTC"]
        assert bot.get_status()["circuit_breaker_grids_not_cleared"] == {}

    def test_flat_grid_with_a_leftover_closing_order_is_not_cleared(self):
        """Clearing would leave that order resting with no grid tracking it."""
        bot, venue = _stripped(positions=LONG_BTC)
        venue.positions = []
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert "resting" in previous["grids_not_cleared"]["BTC"]
        assert [o["order_id"] for o in venue.orders] == [2, 9]

    def test_grid_with_a_pending_flatten_is_not_cleared(self):
        bot, _venue = _stripped()
        bot.grid_lifecycle._pending_flattens["BTC"] = {"attempts": 1}
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert "BTC" in bot.grid_lifecycle._pending_flattens


class TestVenueErrorsDoNotEscapeReset:
    """Reset always completes; what could not be cleared is reported."""

    def test_unreadable_order_book_is_reported_and_nothing_is_cleared(self):
        bot, venue = _stripped()
        venue.client.get_orders.side_effect = RuntimeError("venue down")
        previous = bot.reset_circuit_breaker()
        assert bot._circuit_breaker_triggered is False
        assert previous["grids_cleared"] == []
        assert "BTC" in previous["grids_not_cleared"]
        assert any("venue down" in e for e in previous["grid_clear_errors"])
        assert bot.grid_lifecycle.has_active_grid("BTC") is True

    def test_unreadable_positions_are_reported_and_nothing_is_cleared(self):
        bot, venue = _stripped()
        venue.client.get_positions.side_effect = RuntimeError("timeout")
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert any("timeout" in e for e in previous["grid_clear_errors"])
        assert bot.grid_lifecycle.has_active_grid("BTC") is True

    def test_failing_clear_is_reported_and_the_other_grid_is_cleared(self):
        venue = _Venue(
            BTC_LEVELS
            + [{"order_id": "e1", "symbol": "ETH", "side": "bid", "price": "5"}]
        )
        bot = _bot(venue, {"BTC": _grid({"1", "2", "3"}), "ETH": _grid({"e1"})})
        bot._trip_circuit_breaker("test trip", {})
        real_clear = GridLifecycleManager.clear_grid

        def clear(manager, symbol):
            if symbol == "BTC":
                raise RuntimeError("db locked")
            return real_clear(manager, symbol)

        with patch.object(GridLifecycleManager, "clear_grid", autospec=True) as mock:
            mock.side_effect = clear
            previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == ["ETH"]
        assert "db locked" in previous["grids_not_cleared"]["BTC"]
        assert any("db locked" in e for e in previous["grid_clear_errors"])
        assert bot._circuit_breaker_triggered is False

    def test_manager_that_raises_is_reported(self):
        bot, _venue = _stripped()
        bot.grid_lifecycle = MagicMock()
        bot.grid_lifecycle.clear_breaker_stripped_grids.side_effect = RuntimeError("x")
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert previous["grid_clear_errors"] == ["x"]
        assert bot._circuit_breaker_triggered is False

    def test_failed_reset_clears_no_grid(self):
        """The persisted record could not be cleared: still tripped."""
        from trading_bot_v2 import supervisor_control

        bot, _venue = _stripped()
        with patch.object(
            supervisor_control.SupervisorControl,
            "set_breaker_state",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError):
                bot.reset_circuit_breaker()
        assert bot._circuit_breaker_triggered is True
        assert bot.grid_lifecycle.has_active_grid("BTC") is True


def _restarted(orders, positions=None, grids=None) -> tuple:
    """A fresh process that restored a persisted trip.

    Returns:
        Tuple ``(bot, venue)``; no grid carries a sweep mark.
    """
    first, _ = _stripped()
    assert first._circuit_breaker_triggered is True
    venue = _Venue(orders, positions)
    bot = _bot(venue, grids if grids is not None else {"BTC": _grid(set())})
    bot._load_circuit_breaker_state()
    assert bot._circuit_breaker_triggered is True
    return bot, venue


class TestResetAfterRestart:
    """No in-memory record of the sweep: the venue decides what is stripped."""

    def test_grid_with_no_live_entry_orders_is_cleared(self):
        bot, venue = _restarted([BTC_STOP])
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == ["BTC"]
        assert _mutating_calls(venue) == 0

    def test_grid_with_live_entry_orders_is_kept(self):
        """Reset before the first post-restart sweep: the grid is intact."""
        bot, venue = _restarted(BTC_LEVELS, grids={"BTC": _grid({"1", "2", "3"})})
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert "resting" in previous["grids_not_cleared"]["BTC"]
        assert len(venue.orders) == 3
        assert _mutating_calls(venue) == 0

    def test_grid_with_an_open_position_is_kept(self):
        bot, _venue = _restarted([BTC_STOP], positions=LONG_BTC)
        previous = bot.reset_circuit_breaker()
        assert previous["grids_cleared"] == []
        assert "open position" in previous["grids_not_cleared"]["BTC"]
        assert bot.grid_lifecycle._grids["BTC"]["emergency_stop"] == 900.0

    def test_post_restart_sweep_then_reset_clears_the_grid(self):
        bot, venue = _restarted(BTC_LEVELS, grids={"BTC": _grid({"1", "2", "3"})})
        bot._sweep_breaker_entry_orders()
        assert venue.orders == []
        assert bot.reset_circuit_breaker()["grids_cleared"] == ["BTC"]

    def test_fallback_applies_only_to_the_restored_trip(self):
        """A later in-process trip clears marked grids only."""
        bot, _venue = _restarted([], grids={})
        bot.reset_circuit_breaker()
        bot.grid_lifecycle._grids["ETH"] = _grid(set())
        bot._trip_circuit_breaker("second trip", {})
        assert bot.reset_circuit_breaker()["grids_cleared"] == []
        assert bot.grid_lifecycle.has_active_grid("ETH") is True


class TestStatusAndRoute:
    """Operators can read which symbols were cleared."""

    def test_bot_status_names_the_cleared_symbols(self):
        bot, _venue = _stripped()
        assert bot.get_status()["circuit_breaker_grids_cleared"] == []
        bot.reset_circuit_breaker()
        status = bot.get_status()
        assert status["circuit_breaker_grids_cleared"] == ["BTC"]
        assert status["circuit_breaker_grids_not_cleared"] == {}

    def test_a_new_trip_drops_the_previous_reset_report(self):
        bot, _venue = _stripped()
        bot.reset_circuit_breaker()
        bot._trip_circuit_breaker("again", {})
        assert bot.get_status()["circuit_breaker_grids_cleared"] == []

    def test_dashboard_status_names_the_cleared_symbols(self):
        integration = api_server.BotIntegration()
        integration._initialized = True
        bot, _venue = _stripped(positions=LONG_BTC)
        integration.trading_bot = bot
        status = integration.get_status()
        assert status["circuit_breaker_grids_cleared"] == []
        assert status["circuit_breaker_grids_not_cleared"] == {}
        bot.reset_circuit_breaker()
        status = integration.get_status()
        assert "BTC" in status["circuit_breaker_grids_not_cleared"]

    def test_reset_route_returns_the_cleared_symbols(self, monkeypatch):
        bot, venue = _stripped()
        target = api_server.bot_integration
        monkeypatch.setattr(api_server.config, "api_token", TOKEN)
        monkeypatch.setattr(api_server.config, "api_host", "127.0.0.1")
        with (
            patch.object(target, "initialize", Mock()),
            patch.object(target, "get_status", Mock(return_value={})),
            patch.object(target, "trading_bot", bot),
            patch.object(api_server, "broadcast_update", AsyncMock()),
        ):
            client = TestClient(api_server.app, client=("127.0.0.1", 50000))
            response = client.post(RESET_ROUTE, headers={"X-Api-Token": TOKEN})
        assert response.status_code == 200
        state = response.json()["state"]
        assert state["grids_cleared"] == ["BTC"]
        assert state["grids_not_cleared"] == {}
        assert state["grid_clear_errors"] == []
        venue.client.place_order.assert_not_called()

    def test_reset_route_survives_an_unreachable_venue(self, monkeypatch):
        bot, venue = _stripped()
        venue.client.get_orders.side_effect = RuntimeError("venue down")
        target = api_server.bot_integration
        monkeypatch.setattr(api_server.config, "api_token", TOKEN)
        monkeypatch.setattr(api_server.config, "api_host", "127.0.0.1")
        with (
            patch.object(target, "initialize", Mock()),
            patch.object(target, "get_status", Mock(return_value={})),
            patch.object(target, "trading_bot", bot),
            patch.object(api_server, "broadcast_update", AsyncMock()),
        ):
            client = TestClient(api_server.app, client=("127.0.0.1", 50000))
            response = client.post(RESET_ROUTE, headers={"X-Api-Token": TOKEN})
        assert response.status_code == 200
        state = response.json()["state"]
        assert state["was_tripped"] is True
        assert state["grids_cleared"] == []
        assert state["grid_clear_errors"]
