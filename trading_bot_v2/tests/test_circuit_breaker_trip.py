"""Circuit breaker trip behaviour (follow-up to logic audit A3).

Owner decision: a trip BLOCKS ALL NEW ENTRIES but does not stop the
trading loop, nothing is flattened, and the breaker stays tripped until
an operator resets it.  These tests were written first and fail against
the branch as it stood, where a trip called ``stop()`` and so ended local
stop enforcement on venues without server-side stops.

No network, no live database; the exchange is a MagicMock stand-in and
conftest points the supervisor state file at a temp path.

Covered:
* after a trip the loop keeps iterating and still enforces local stops;
* every path that opens or adds exposure is blocked while tripped;
* reduce-only closes, stop installs and inventory-closing grid legs stay
  allowed;
* a trip is idempotent (one log / event / persisted record per trip);
* a trip cancels resting entry orders (grid levels) on every venue shape,
  keeps protective and inventory-closing orders, retries failed cancels on
  later iterations and stops calling the venue once nothing is left;
* the manual reset clears it and entries resume;
* neither a loop restart, a process restart nor a supervisor resume
  clears it;
* both status payloads show the state, trip time and reason;
* the reset control needs the same token as the other operator controls.
"""

import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from trading_bot_v2 import api_server, supervisor_control
from trading_bot_v2.config import AssetClass, MarketState, StrategyType, TradeQuality
from trading_bot_v2.grid_lifecycle_manager import (
    GridLifecycleManager,
    GridMetrics,
    GridState,
)
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.trading_bot import TradingBot

TOKEN = "correct-horse-battery-staple"
RESET_ROUTE = "/api/bot/circuit-breaker/reset"
LOSING_LONG = {"symbol": "BTC", "side": "long", "quantity": 1.0, "unrealized_pnl": -150}
ACK = {"success": True, "data": {"order_id": "o-1"}}
NON_GRID_STRATEGIES = [s for s in StrategyType if s != StrategyType.GRID_TRADING]


def _signal(strategy: StrategyType = StrategyType.MEAN_REVERSION) -> Signal:
    """A fully flagged BUY signal for ``strategy``."""
    return Signal(
        strategy=strategy,
        asset="BTC",
        asset_class=AssetClass.PERPETUAL,
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
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


def _bot(positions=None, balance: float = 1_000.0) -> TradingBot:
    """A TradingBot shell (no __init__) with mocked collaborators."""
    bot = TradingBot.__new__(TradingBot)
    bot.client = MagicMock()
    bot.client.get_positions.return_value = positions or []
    # ``exchange`` is a property over a cached adapter keyed on the client.
    bot._exchange = MagicMock()
    bot._exchange.rest_client = bot.client
    bot.exchange.place_order.return_value = ACK
    bot.signal_logger = MagicMock()
    bot.execution_layer = MagicMock()
    bot.execution_layer.refine_entry.side_effect = lambda signal, symbol: signal
    bot.risk_manager = MagicMock()
    bot.risk_manager.get_position_size.return_value = 1.0
    bot.risk_manager.request_capital_allocation.return_value = {
        "approved": True,
        "allocated_amount": 100.0,
    }
    bot.hub_publish_func = None
    bot.ws_client = None
    bot.thread = None
    bot._running_event = threading.Event()
    bot._circuit_breaker_triggered = False
    bot._circuit_breaker_tripped_at = None
    bot._circuit_breaker_reason = None
    bot._circuit_breaker_loss_pct = 0.10
    bot._get_account_balance = lambda: balance
    bot._get_current_exposure = lambda: 0.0
    bot._record_entry_outcome = MagicMock()
    return bot


def _tripped_bot(**kwargs) -> TradingBot:
    """A shell whose breaker has been tripped through the real trip path."""
    bot = _bot(**kwargs)
    bot._trip_circuit_breaker("test trip", {})
    return bot


class TestLoopKeepsRunning:
    """A trip must not end the loop that enforces local stops."""

    def _loop_bot(self, monkeypatch, iterations: int) -> TradingBot:
        bot = _bot(positions=[LOSING_LONG])
        bot._running_event.set()
        bot._startup_stop_repair_done = True
        bot.event_bus = SimpleNamespace(_published_count=0)
        for step in (
            "_sync_existing_grids",
            "_update_positions",
            "_complete_pending_entries",
            "_enforce_local_stops",
            "_monitor_grids",
            "_manage_migrated_positions",
            "_generate_and_publish_signals",
        ):
            setattr(bot, step, MagicMock())

        def fake_sleep(_seconds):
            if getattr(bot, "_loop_iteration", 0) >= iterations:
                bot._running_event.clear()

        monkeypatch.setattr("trading_bot_v2.trading_bot.time.sleep", fake_sleep)
        return bot

    def test_loop_continues_and_enforces_local_stops_after_trip(self, monkeypatch):
        bot = self._loop_bot(monkeypatch, iterations=3)
        bot._trading_loop()
        assert bot._circuit_breaker_triggered is True
        assert bot._loop_iteration == 3
        assert bot._enforce_local_stops.call_count == 3
        assert bot._update_positions.call_count == 3
        assert bot._complete_pending_entries.call_count == 3
        assert bot._monitor_grids.call_count == 3
        assert bot._manage_migrated_positions.call_count == 3

    def test_trip_leaves_the_bot_running(self):
        bot = _bot(positions=[LOSING_LONG])
        bot._running_event.set()
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is True
        assert bot.is_running is True


class TestEntriesBlockedWhileTripped:
    """Every path that opens or adds exposure is gated on the breaker."""

    def test_signal_validation_rejects(self):
        bot = _tripped_bot()
        assert bot._should_execute_signal(_signal()) is False
        reason = bot.signal_logger.log_signal_rejected.call_args.kwargs["reason"]
        assert "Circuit breaker" in reason

    @pytest.mark.parametrize("strategy", NON_GRID_STRATEGIES, ids=lambda s: s.name)
    def test_standard_entry_sends_no_order(self, strategy):
        bot = _tripped_bot()
        bot._execute_standard_signal_coordinated(
            _signal(strategy), {"allocated_amount": 100.0}
        )
        bot.exchange.place_order.assert_not_called()
        bot.signal_logger.log_signal_rejected.assert_called_once()

    def test_grid_placement_sends_no_order(self):
        bot = _tripped_bot()
        bot._calculate_grid_levels = MagicMock(
            return_value={
                "buy_levels": [{"price": 99.0, "quantity": 0.1}],
                "sell_levels": [{"price": 101.0, "quantity": 0.1}],
            }
        )
        result = bot._place_grid_orders(
            _signal(StrategyType.GRID_TRADING), {"allocated_amount": 100.0}
        )
        assert result["success"] is False
        assert "Circuit breaker" in result["error"]
        bot.exchange.place_order.assert_not_called()

    @pytest.mark.parametrize(
        "strategy", [StrategyType.GRID_TRADING, StrategyType.LIQUIDATION_CAPTURE]
    )
    def test_coordination_without_validation_sends_no_order(self, strategy):
        """The debug route calls _coordinate_signal_execution directly."""
        bot = _tripped_bot()
        bot.grid_lifecycle = MagicMock()
        bot.grid_lifecycle.has_active_grid.return_value = False
        bot._coordinate_signal_execution(_signal(strategy))
        bot.exchange.place_order.assert_not_called()
        bot.grid_lifecycle.register_new_grid.assert_not_called()

    def test_legacy_grid_execution_sends_no_order(self):
        bot = _tripped_bot()
        bot._calculate_position_size = MagicMock(return_value=100.0)
        assert bot._execute_grid_signal(_signal(StrategyType.GRID_TRADING)) is None
        bot._calculate_position_size.assert_not_called()
        bot.client.place_order.assert_not_called()


def _grid_manager(net_position: float, gate) -> GridLifecycleManager:
    """A grid manager with one ACTIVE BTC grid and the given entry gate."""
    manager = GridLifecycleManager(client=MagicMock(), risk_manager=MagicMock())
    manager.client.place_order.return_value = {"id": "c-1"}
    manager._grids["BTC"] = {
        "state": GridState.ACTIVE,
        "grid_spacing": 10.0,
        "order_ids": None,
    }
    manager._metrics["BTC"] = GridMetrics(net_position=net_position)
    manager.entry_gate = gate
    return manager


class TestGridRearmingWhileTripped:
    """Replenish may close grid inventory but never re-arm an entry."""

    def test_buy_counter_after_sell_fill_is_blocked(self):
        manager = _grid_manager(net_position=0.0, gate=lambda: "tripped")
        manager._replenish_order("BTC", "SELL", 1_000.0, 0.5)
        manager.client.place_order.assert_not_called()

    def test_sell_counter_that_adds_to_a_short_is_blocked(self):
        manager = _grid_manager(net_position=-1.0, gate=lambda: "tripped")
        manager._replenish_order("BTC", "BUY", 1_000.0, 0.5)
        manager.client.place_order.assert_not_called()

    def test_sell_counter_closing_long_inventory_is_allowed(self):
        manager = _grid_manager(net_position=0.5, gate=lambda: "tripped")
        manager._replenish_order("BTC", "BUY", 1_000.0, 0.5)
        manager.client.place_order.assert_called_once_with(
            "BTC", "sell", 0.5, "limit", 1_010.0
        )

    def test_replenish_is_unchanged_when_not_tripped(self):
        manager = _grid_manager(net_position=0.0, gate=lambda: None)
        manager._replenish_order("BTC", "SELL", 1_000.0, 0.5)
        manager.client.place_order.assert_called_once_with(
            "BTC", "buy", 0.5, "limit", 990.0
        )

    def test_recenter_is_blocked(self):
        manager = _grid_manager(net_position=0.0, gate=lambda: "tripped")
        manager.get_grid_center = MagicMock(return_value=1_000.0)
        assert manager.recenter_grid("BTC", 1_050.0) is False
        manager.get_grid_center.assert_not_called()
        manager.client.cancel_order.assert_not_called()
        manager.client.place_order.assert_not_called()

    def test_bot_gate_reports_the_breaker(self):
        bot = _bot()
        assert bot._entry_block_reason() is None
        bot._trip_circuit_breaker("test trip", {})
        assert "test trip" in bot._entry_block_reason()


class TestProtectiveActionsStillAllowed:
    """Closing and stop orders are never gated by the breaker."""

    def test_reduce_only_close_is_sent(self):
        bot = _tripped_bot()
        plan = SimpleNamespace(already_flat=False, clamped=False, quantity=1.0)
        with patch("trading_bot_v2.trading_bot.plan_close_quantity", return_value=plan):
            assert bot._emergency_close_position("BTC", "LONG", 1.0) is True
        assert bot.exchange.place_order.call_args.kwargs["reduce_only"] is True

    def test_stop_install_is_sent(self):
        bot = _tripped_bot()
        bot._verify_venue_stop = MagicMock(return_value=None)
        bot._persist_protection = MagicMock()
        bot.exchange.install_stop.return_value = SimpleNamespace(
            accepted=True, order_id="s-1", error=None
        )
        fill = SimpleNamespace(order_id="o-1", filled_quantity=1.0)
        with patch(
            "trading_bot_v2.trading_bot.venue_stops_supported", return_value=True
        ):
            bot._ensure_entry_protection(_signal(), "BTC", fill, 100.0)
        bot.exchange.install_stop.assert_called_once_with("BTC", "LONG", 1.0, 95.0)


class TestTripIsIdempotent:
    """Log, event and persisted record fire once per trip, not per loop."""

    def test_repeated_risk_checks_publish_one_event(self):
        bot = _bot(positions=[LOSING_LONG])
        events = []
        bot.hub_publish_func = events.append
        bot._monitor_risk()
        tripped_at = bot._circuit_breaker_tripped_at
        with patch.object(
            supervisor_control.SupervisorControl, "set_breaker_state"
        ) as persist:
            bot._monitor_risk()
            bot._monitor_risk()
        assert [e["type"] for e in events] == ["circuit_breaker"]
        assert events[0]["balance"] == 1_000.0
        assert bot._circuit_breaker_tripped_at == tripped_at
        persist.assert_not_called()

    def test_trip_reports_whether_it_fired(self):
        bot = _bot()
        assert bot._trip_circuit_breaker("first", {}) is True
        assert bot._trip_circuit_breaker("second", {}) is False
        assert bot._circuit_breaker_reason == "first"


class TestManualReset:
    """Only reset_circuit_breaker clears a trip."""

    def test_reset_clears_state_and_entries_resume(self):
        bot = _tripped_bot()
        events = []
        bot.hub_publish_func = events.append
        previous = bot.reset_circuit_breaker()
        assert previous["was_tripped"] is True
        assert previous["reason"] == "test trip"
        assert bot._circuit_breaker_triggered is False
        assert bot._circuit_breaker_tripped_at is None
        assert bot._circuit_breaker_reason is None
        assert [e["type"] for e in events] == ["circuit_breaker_reset"]
        assert bot._should_execute_signal(_signal()) is True
        bot._execute_standard_signal_coordinated(_signal(), {"allocated_amount": 100.0})
        bot.exchange.place_order.assert_called_once()

    def test_loop_restart_without_reset_stays_tripped(self):
        bot = _tripped_bot()
        bot._trading_loop = MagicMock()
        bot.start()
        bot.stop()
        bot.start()
        bot.stop()
        assert bot._circuit_breaker_triggered is True
        assert bot._should_execute_signal(_signal()) is False

    def test_process_restart_without_reset_stays_tripped(self):
        first = _tripped_bot()
        restarted = _bot()
        restarted._load_circuit_breaker_state()
        assert restarted._circuit_breaker_triggered is True
        assert restarted._circuit_breaker_reason == "test trip"
        assert (
            restarted._circuit_breaker_tripped_at == first._circuit_breaker_tripped_at
        )

    def test_reset_survives_a_process_restart(self):
        _tripped_bot().reset_circuit_breaker()
        restarted = _bot()
        restarted._load_circuit_breaker_state()
        assert restarted._circuit_breaker_triggered is False

    def test_failed_reset_leaves_the_breaker_tripped(self):
        bot = _tripped_bot()
        with patch.object(
            supervisor_control.SupervisorControl,
            "set_breaker_state",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError):
                bot.reset_circuit_breaker()
        assert bot._circuit_breaker_triggered is True

    def test_unpersisted_trip_still_blocks_in_memory(self):
        bot = _bot()
        with patch.object(
            supervisor_control.SupervisorControl,
            "set_breaker_state",
            side_effect=OSError("disk full"),
        ):
            assert bot._trip_circuit_breaker("test trip", {}) is True
        assert bot._should_execute_signal(_signal()) is False


class TestBreakerRecordInSupervisorState:
    """The trip shares the pause file but is not a pause."""

    def test_supervisor_pause_and_resume_do_not_clear_the_trip(self):
        _tripped_bot()
        control = supervisor_control.get_supervisor_control()
        assert control.is_paused() is False
        control.pause("FOMC")
        assert control.breaker_state()["reason"] == "test trip"
        control.resume()
        assert control.breaker_state()["tripped"] is True

    def test_expired_pause_does_not_clear_the_trip(self):
        _tripped_bot()
        control = supervisor_control.get_supervisor_control()
        control.pause("short", until_ts="2000-01-01T00:00:00+00:00")
        assert control.is_paused() is False
        assert control.breaker_state()["tripped"] is True

    def test_trip_and_reset_keep_an_existing_pause(self):
        control = supervisor_control.get_supervisor_control()
        control.pause("FOMC")
        _tripped_bot().reset_circuit_breaker()
        assert control.is_paused() is True
        assert control.status()["raw_state"]["reason"] == "FOMC"
        assert control.breaker_state() == {}

    def test_missing_state_file_means_not_tripped(self):
        assert supervisor_control.get_supervisor_control().breaker_state() == {}

    def test_unreadable_state_fails_closed(self):
        supervisor_control._PAUSE_FILE.write_text("{not json", encoding="utf-8")
        restarted = _bot()
        restarted._load_circuit_breaker_state()
        assert restarted._circuit_breaker_triggered is True

    def test_record_is_stored_under_its_own_key(self):
        bot = _tripped_bot()
        state = json.loads(supervisor_control._PAUSE_FILE.read_text(encoding="utf-8"))
        assert state["paused"] is False
        assert state["circuit_breaker"] == {
            "tripped": True,
            "tripped_at": bot._circuit_breaker_tripped_at,
            "reason": "test trip",
        }


class TestStatusPayload:
    """The dashboard reads the state, trip time and reason from status."""

    def test_bot_status_shows_the_trip(self):
        bot = _tripped_bot()
        status = bot.get_status()
        assert status["circuit_breaker_triggered"] is True
        assert status["circuit_breaker_tripped_at"] == bot._circuit_breaker_tripped_at
        assert status["circuit_breaker_reason"] == "test trip"

    def test_dashboard_status_shows_the_trip(self):
        integration = api_server.BotIntegration()
        integration._initialized = True
        integration.trading_bot = _tripped_bot()
        status = integration.get_status()
        assert status["circuit_breaker_triggered"] is True
        assert (
            status["circuit_breaker_tripped_at"]
            == integration.trading_bot._circuit_breaker_tripped_at
        )
        assert status["circuit_breaker_reason"] == "test trip"

    def test_dashboard_status_defaults_when_not_tripped(self):
        integration = api_server.BotIntegration()
        integration._initialized = True
        integration.trading_bot = _bot()
        status = integration.get_status()
        assert status["circuit_breaker_triggered"] is False
        assert status["circuit_breaker_tripped_at"] is None
        assert status["circuit_breaker_reason"] is None


@pytest.fixture
def integration():
    """Patch the bot_integration singleton around a tripped bot shell.

    Yields:
        The patched singleton; ``trading_bot`` is a tripped shell.
    """
    target = api_server.bot_integration
    with (
        patch.object(target, "initialize", Mock()),
        patch.object(target, "get_status", Mock(return_value={"is_running": True})),
        patch.object(target, "trading_bot", _tripped_bot()),
        patch.object(api_server, "broadcast_update", AsyncMock()),
    ):
        yield target


@pytest.fixture
def client(integration, monkeypatch):
    """A loopback TestClient (lifespan not run) with API_TOKEN configured."""
    monkeypatch.setattr(api_server.config, "api_token", TOKEN)
    monkeypatch.setattr(api_server.config, "api_host", "127.0.0.1")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


class TestResetControl:
    """POST /api/bot/circuit-breaker/reset: same auth as start/stop/pause."""

    def test_reset_route_shares_the_operator_auth_dependency(self):
        deps = {}
        for route in api_server.app.routes:
            path = getattr(route, "path", None)
            if path in (RESET_ROUTE, "/api/bot/stop", "/api/supervisor/resume"):
                deps[path] = [d.call for d in route.dependant.dependencies]
        assert deps[RESET_ROUTE] == deps["/api/bot/stop"]
        assert deps[RESET_ROUTE] == deps["/api/supervisor/resume"]
        assert api_server.require_api_token in deps[RESET_ROUTE]

    def test_anonymous_reset_is_401_and_leaves_the_trip(self, client, integration):
        assert client.post(RESET_ROUTE).status_code == 401
        assert client.post("/api/bot/stop").status_code == 401
        assert integration.trading_bot._circuit_breaker_triggered is True

    def test_wrong_token_is_401(self, client, integration):
        response = client.post(RESET_ROUTE, headers={"X-Api-Token": "nope"})
        assert response.status_code == 401
        assert integration.trading_bot._circuit_breaker_triggered is True

    def test_token_reset_clears_the_trip(self, client, integration):
        response = client.post(RESET_ROUTE, headers={"X-Api-Token": TOKEN})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["state"]["was_tripped"] is True
        assert integration.trading_bot._circuit_breaker_triggered is False
        api_server.broadcast_update.assert_awaited_once()

    def test_reset_is_not_a_get(self, client):
        assert client.get(RESET_ROUTE, headers={"X-Api-Token": TOKEN}).status_code in (
            404,
            405,
        )

    def test_reset_without_a_bot_is_503(self, client, integration):
        integration.trading_bot = None
        response = client.post(RESET_ROUTE, headers={"X-Api-Token": TOKEN})
        assert response.status_code == 503

    def test_starting_the_bot_does_not_clear_the_trip(self, client, integration):
        with patch.object(integration, "start", AsyncMock()):
            response = client.post("/api/bot/start", headers={"X-Api-Token": TOKEN})
        assert response.status_code == 200
        assert integration.trading_bot._circuit_breaker_triggered is True


# ----------------------------------------------------------------------
# Resting entry orders are cancelled on a trip
# ----------------------------------------------------------------------

#: Pacifica open-order rows: wire sides bid/ask, integer ids, string amounts.
PACIFICA_ORDERS = [
    {"order_id": 1, "symbol": "BTC", "side": "bid", "price": "990", "amount": "0.5"},
    {"order_id": 2, "symbol": "BTC", "side": "ask", "price": "1010", "amount": "0.5"},
    {"order_id": 3, "symbol": "BTC", "side": "ask", "price": "1020", "amount": "0.5"},
    {
        "order_id": 9,
        "symbol": "BTC",
        "side": "ask",
        "order_type": "stop_market",
        "reduce_only": True,
        "stop_price": "900",
    },
]

#: Blofin open-order rows: buy/sell, string ids, float quantity, raw payload.
BLOFIN_ORDERS = [
    {
        "order_id": "b1",
        "symbol": "ETH",
        "side": "buy",
        "order_type": "limit",
        "price": 1990.0,
        "quantity": 0.2,
        "raw": {"reduceOnly": "false"},
    },
    {
        "order_id": "b2",
        "symbol": "ETH",
        "side": "buy",
        "order_type": "limit",
        "price": 1980.0,
        "quantity": 0.2,
        "raw": {"reduceOnly": "false"},
    },
    {
        "order_id": "s1",
        "symbol": "ETH",
        "side": "sell",
        "order_type": "limit",
        "price": 2010.0,
        "quantity": 0.2,
        "raw": {"reduceOnly": "false"},
    },
    {
        "order_id": "tp",
        "symbol": "ETH",
        "side": "buy",
        "order_type": "limit",
        "price": 1900.0,
        "quantity": 0.2,
        "raw": {"reduceOnly": "true"},
    },
]


def _sweep_manager(orders, positions=None) -> GridLifecycleManager:
    """A grid manager over a mocked client reporting ``orders`` / ``positions``."""
    manager = GridLifecycleManager(client=MagicMock(), risk_manager=MagicMock())
    manager.client.get_orders.return_value = orders
    manager.client.get_positions.return_value = positions or []
    manager.client.cancel_order.return_value = {"success": True}
    return manager


def _cancelled_ids(manager: GridLifecycleManager) -> list:
    """Order ids passed to cancel_order, in call order."""
    return [call.args[1] for call in manager.client.cancel_order.call_args_list]


class TestEntryOrderCancellation:
    """GridLifecycleManager.cancel_entry_orders: what goes and what stays."""

    def test_flat_pacifica_book_loses_every_grid_level_but_not_the_stop(self):
        manager = _sweep_manager(PACIFICA_ORDERS)
        result = manager.cancel_entry_orders()
        assert sorted(_cancelled_ids(manager)) == ["1", "2", "3"]
        assert result == {"cancelled": 3, "failed": 0, "kept": 0}

    def test_long_inventory_keeps_the_nearest_sell_that_closes_it(self):
        long_btc = [{"symbol": "BTC", "side": "bid", "amount": "0.5"}]
        manager = _sweep_manager(PACIFICA_ORDERS, long_btc)
        result = manager.cancel_entry_orders()
        # Buy 1 adds to the long; sell 3 would open a short once sell 2
        # has sold the 0.5 held.  Sell 2 only closes inventory: kept.
        assert sorted(_cancelled_ids(manager)) == ["1", "3"]
        assert result == {"cancelled": 2, "failed": 0, "kept": 1}

    def test_short_inventory_on_blofin_keeps_the_nearest_buy(self):
        short_eth = [{"symbol": "ETH", "side": "short", "quantity": 0.2}]
        manager = _sweep_manager(BLOFIN_ORDERS, short_eth)
        result = manager.cancel_entry_orders()
        # b1 (nearest buy) closes the 0.2 short; b2 would flip long; s1
        # adds to the short; "tp" is reduce-only and never touched.
        assert sorted(_cancelled_ids(manager)) == ["b2", "s1"]
        assert result == {"cancelled": 2, "failed": 0, "kept": 1}

    def test_flat_blofin_book_keeps_only_the_reduce_only_order(self):
        manager = _sweep_manager(BLOFIN_ORDERS)
        manager.cancel_entry_orders()
        assert sorted(_cancelled_ids(manager)) == ["b1", "b2", "s1"]

    def test_closing_order_larger_than_the_inventory_is_cancelled(self):
        long_btc = [{"symbol": "BTC", "side": "long", "quantity": 0.3}]
        manager = _sweep_manager(PACIFICA_ORDERS, long_btc)
        manager.cancel_entry_orders()
        assert sorted(_cancelled_ids(manager)) == ["1", "2", "3"]

    def test_cancel_is_sent_per_symbol_across_venues_shapes(self):
        manager = _sweep_manager(PACIFICA_ORDERS + BLOFIN_ORDERS)
        manager.cancel_entry_orders()
        symbols = {c.args[0] for c in manager.client.cancel_order.call_args_list}
        assert symbols == {"BTC", "ETH"}
        assert manager.client.cancel_order.call_count == 6

    def test_one_failing_cancel_does_not_stop_the_others(self):
        manager = _sweep_manager(PACIFICA_ORDERS)

        def cancel(symbol, order_id):
            if order_id == "1":
                raise RuntimeError("venue timeout")
            if order_id == "2":
                return {"success": False, "error": "rejected"}
            return {"success": True}

        manager.client.cancel_order.side_effect = cancel
        result = manager.cancel_entry_orders()
        assert sorted(_cancelled_ids(manager)) == ["1", "2", "3"]
        assert result == {"cancelled": 1, "failed": 2, "kept": 0}

    def test_unreadable_order_book_counts_as_failing_and_cancels_nothing(self):
        manager = _sweep_manager(PACIFICA_ORDERS)
        manager.client.get_orders.side_effect = RuntimeError("down")
        assert manager.cancel_entry_orders() == {"cancelled": 0, "failed": 1, "kept": 0}
        manager.client.cancel_order.assert_not_called()

    def test_unreadable_positions_cancel_nothing_rather_than_guess(self):
        manager = _sweep_manager(PACIFICA_ORDERS)
        manager.client.get_positions.side_effect = RuntimeError("down")
        assert manager.cancel_entry_orders()["failed"] == 1
        manager.client.cancel_order.assert_not_called()

    def test_cancelled_levels_leave_the_grid_bookkeeping(self):
        manager = _sweep_manager(PACIFICA_ORDERS)
        manager._grids["BTC"] = {
            "state": GridState.ACTIVE,
            "grid_spacing": 10.0,
            "order_ids": {"1", "2", "3"},
        }
        manager.client.cancel_order.side_effect = lambda s, oid: {"success": oid != "3"}
        manager.cancel_entry_orders()
        assert manager._grids["BTC"]["order_ids"] == {"3"}
        # The grid stays ACTIVE: its emergency stop still guards inventory
        # and it keeps blocking a duplicate grid until it is cleared.
        assert manager.has_active_grid("BTC") is True

    def test_empty_book_makes_no_cancel_calls(self):
        manager = _sweep_manager([])
        assert manager.cancel_entry_orders() == {"cancelled": 0, "failed": 0, "kept": 0}
        manager.client.cancel_order.assert_not_called()


def _sweep_bot(*results) -> TradingBot:
    """A shell whose grid manager returns ``results`` from successive sweeps."""
    bot = _bot(positions=[LOSING_LONG])
    bot.grid_lifecycle = MagicMock()
    bot.grid_lifecycle.cancel_entry_orders.side_effect = list(results)
    return bot


CLEAN = {"cancelled": 0, "failed": 0, "kept": 0}


class TestTripCancelsRestingEntries:
    """The bot sweeps on a trip and keeps sweeping until nothing is left."""

    def test_trip_sweeps_immediately_and_reports_the_count(self):
        bot = _sweep_bot({"cancelled": 4, "failed": 0, "kept": 0})
        bot._monitor_risk()
        bot.grid_lifecycle.cancel_entry_orders.assert_called_once()
        status = bot.get_status()
        assert status["circuit_breaker_orders_cancelled"] == 4
        assert status["circuit_breaker_cancels_failing"] == 0

    def test_failed_cancel_is_retried_until_clean_then_calls_stop(self):
        bot = _sweep_bot(
            {"cancelled": 3, "failed": 1, "kept": 0},
            {"cancelled": 0, "failed": 1, "kept": 0},
            {"cancelled": 1, "failed": 0, "kept": 0},
        )
        bot._monitor_risk()
        assert bot.get_status()["circuit_breaker_cancels_failing"] == 1
        bot._sweep_breaker_entry_orders()
        assert bot.get_status()["circuit_breaker_cancels_failing"] == 1
        bot._sweep_breaker_entry_orders()
        status = bot.get_status()
        assert status["circuit_breaker_orders_cancelled"] == 4
        assert status["circuit_breaker_cancels_failing"] == 0
        for _ in range(5):
            bot._sweep_breaker_entry_orders()
            bot._monitor_risk()
        assert bot.grid_lifecycle.cancel_entry_orders.call_count == 3

    def test_kept_closing_orders_are_rechecked_each_iteration(self):
        """A kept sell becomes an entry if a stop closes the inventory."""
        bot = _sweep_bot({"cancelled": 2, "failed": 0, "kept": 1}, CLEAN)
        bot._monitor_risk()
        bot._sweep_breaker_entry_orders()
        bot._sweep_breaker_entry_orders()
        assert bot.grid_lifecycle.cancel_entry_orders.call_count == 2

    def test_sweep_error_does_not_escape_the_trip_or_lose_the_event(self):
        bot = _sweep_bot(RuntimeError("boom"), CLEAN)
        events = []
        bot.hub_publish_func = events.append
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is True
        assert [e["type"] for e in events] == ["circuit_breaker"]
        assert bot.get_status()["circuit_breaker_cancels_failing"] == 1
        bot._sweep_breaker_entry_orders()
        assert bot.get_status()["circuit_breaker_cancels_failing"] == 0

    def test_loop_keeps_running_and_retries_while_cancels_fail(self, monkeypatch):
        bot = TestLoopKeepsRunning()._loop_bot(monkeypatch, iterations=3)
        bot.grid_lifecycle = MagicMock()
        bot.grid_lifecycle.cancel_entry_orders.side_effect = RuntimeError("boom")
        bot._trading_loop()
        assert bot._loop_iteration == 3
        assert bot._enforce_local_stops.call_count == 3
        # once from the trip (end of iteration 1), then iterations 2 and 3
        assert bot.grid_lifecycle.cancel_entry_orders.call_count == 3

    def test_no_sweep_while_not_tripped(self):
        bot = _sweep_bot(CLEAN)
        bot.client.get_positions.return_value = []
        bot._monitor_risk()
        bot._sweep_breaker_entry_orders()
        bot.grid_lifecycle.cancel_entry_orders.assert_not_called()

    def test_restored_trip_sweeps_on_the_first_iteration_after_restart(self):
        _tripped_bot()
        restarted = _sweep_bot({"cancelled": 2, "failed": 0, "kept": 0})
        restarted._load_circuit_breaker_state()
        restarted.grid_lifecycle.cancel_entry_orders.assert_not_called()
        restarted._sweep_breaker_entry_orders()
        restarted._sweep_breaker_entry_orders()
        restarted.grid_lifecycle.cancel_entry_orders.assert_called_once()
        assert restarted.get_status()["circuit_breaker_orders_cancelled"] == 2

    def test_reset_stops_the_sweep_and_clears_the_counters(self):
        bot = _sweep_bot({"cancelled": 3, "failed": 1, "kept": 0})
        bot._monitor_risk()
        bot.client.get_positions.return_value = []
        previous = bot.reset_circuit_breaker()
        assert previous["orders_cancelled"] == 3
        bot._sweep_breaker_entry_orders()
        bot.grid_lifecycle.cancel_entry_orders.assert_called_once()
        status = bot.get_status()
        assert status["circuit_breaker_orders_cancelled"] == 0
        assert status["circuit_breaker_cancels_failing"] == 0

    def test_dashboard_status_carries_the_cancel_counters(self):
        integration = api_server.BotIntegration()
        integration._initialized = True
        integration.trading_bot = _sweep_bot({"cancelled": 3, "failed": 2, "kept": 0})
        integration.trading_bot._monitor_risk()
        status = integration.get_status()
        assert status["circuit_breaker_orders_cancelled"] == 3
        assert status["circuit_breaker_cancels_failing"] == 2


class TestGridAfterSweepAndReset:
    """A stripped grid is not re-armed while tripped and resumes normally."""

    def _stripped_manager(self, gate) -> GridLifecycleManager:
        manager = _sweep_manager(PACIFICA_ORDERS)
        manager._grids["BTC"] = {
            "state": GridState.ACTIVE,
            "grid_spacing": 10.0,
            "order_ids": {"1", "2", "3"},
        }
        manager._metrics["BTC"] = GridMetrics()
        manager.entry_gate = gate
        manager.cancel_entry_orders()
        manager.client.place_order.return_value = {"id": "c-1"}
        return manager

    def test_cancelled_levels_are_not_replenished_while_tripped(self):
        manager = self._stripped_manager(lambda: "tripped")
        assert manager._grids["BTC"]["order_ids"] == set()
        manager._replenish_order("BTC", "SELL", 1_000.0, 0.5)
        manager.client.place_order.assert_not_called()

    def test_replenish_works_again_after_reset(self):
        manager = self._stripped_manager(lambda: None)
        manager._replenish_order("BTC", "SELL", 1_000.0, 0.5)
        manager.client.place_order.assert_called_once()

    def test_new_grid_signal_is_refused_until_the_stripped_grid_is_cleared(self):
        manager = self._stripped_manager(lambda: None)
        bot = _bot()
        bot.grid_lifecycle = manager
        bot._place_grid_orders = MagicMock()
        signal = _signal(StrategyType.GRID_TRADING)
        bot._execute_grid_signal_coordinated(signal, {"allocated_amount": 100.0})
        bot._place_grid_orders.assert_not_called()
        manager.clear_grid("BTC")
        bot._place_grid_orders.return_value = {"success": False, "error": "x"}
        bot._execute_grid_signal_coordinated(signal, {"allocated_amount": 100.0})
        bot._place_grid_orders.assert_called_once()
