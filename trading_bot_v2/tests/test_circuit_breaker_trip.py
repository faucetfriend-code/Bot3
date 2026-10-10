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
        "strategy", [StrategyType.GRID_TRADING, StrategyType.FUNDING_ARB]
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
