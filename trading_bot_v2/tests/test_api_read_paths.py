"""Regression tests for three API read/sync paths that used to fail silently.

* ``GET /api/grids`` returned ``[]`` whenever a grid was active, because
  ``BotIntegration.get_grids`` called ``.items()`` on a list.
* ``POST /api/positions/sync`` called a ``DatabaseManager`` method that does
  not exist and ``close_position`` without its ``side``; every item errored
  and the result still said ``success``.
* ``GET /api/debug/event-history`` sliced a ``collections.deque``.

Safety notes (same rules as test_api_server.py): ``TestClient`` is never
entered as a context manager, so the app's startup handler does not run and
no real client is built. The venue is represented by a fake that only has
``get_positions``; any attempt to place or cancel an order would raise
``AttributeError`` and fail the test. The database is the session's
throwaway SQLite file from conftest.py.
"""

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from trading_bot_v2 import api_server
from trading_bot_v2 import database as db_mod
from trading_bot_v2.database import DatabaseManager
from trading_bot_v2.event_system import EventBus, EventType
from trading_bot_v2.grid_lifecycle_manager import (
    GridLifecycleManager,
    GridMetrics,
    GridState,
)

LOOPBACK_PEER = ("127.0.0.1", 50000)

#: Shaped exactly like GridLifecycleManager.get_grid_status() output.
GRID_STATUS: Dict[str, Any] = {
    "symbol": "BTC",
    "state": "active",
    "grid_capital": 500.0,
    "emergency_stop": 58000.0,
    "metrics": {
        "total_buy_fills": 3,
        "total_sell_fills": 2,
        "total_buy_quantity": 0.03,
        "total_sell_quantity": 0.02,
        "avg_buy_price": 60000.0,
        "avg_sell_price": 60500.0,
        "net_position": 0.01,
        "total_fees": 0.12,
        "realized_pnl": 9.5,
        "unrealized_pnl": -1.25,
        "total_pnl": 8.25,
        "completed_round_trips": 2,
    },
    "synced_from_exchange": False,
}


class ReadOnlyVenue:
    """Venue fake exposing position reads only.

    It deliberately has no order methods: a sync that tried to place or
    cancel anything would hit AttributeError.
    """

    def __init__(self, positions: List[Dict[str, Any]]) -> None:
        self._positions = positions
        self.calls = 0

    def get_positions(self) -> List[Dict[str, Any]]:
        """Return the canned open positions."""
        self.calls += 1
        return list(self._positions)


def _delete_all_positions() -> None:
    """Empty the positions table of the throwaway test database."""
    with db_mod.get_db_connection() as conn:
        conn.execute("DELETE FROM positions")
        conn.commit()
    db_mod._data_cache.invalidate("positions_all")


@pytest.fixture
def database():
    """Yield a real DatabaseManager on the session's temp SQLite file."""
    manager = DatabaseManager()
    _delete_all_positions()
    yield manager
    _delete_all_positions()


@pytest.fixture
def client():
    """Build a loopback TestClient without running the app lifespan."""
    target = api_server.bot_integration
    with (
        patch.object(target, "initialize", Mock()),
        patch.object(api_server, "broadcast_update", AsyncMock()),
        patch.object(api_server.config, "api_token", None),
        patch.object(api_server.config, "api_host", "127.0.0.1"),
    ):
        yield TestClient(api_server.app, client=LOOPBACK_PEER)


def _live(symbol: str, side: str, size: float, entry: float) -> Dict[str, Any]:
    """Build a venue position in the wire spelling the mapper accepts."""
    return {
        "symbol": symbol,
        "side": side,
        "amount": size,
        "entry_price": entry,
        "mark_price": entry,
        "created_at": "2026-10-01T00:00:00+00:00",
    }


def _stored(symbol: str, side: str, quantity: float = 1.0) -> Dict[str, Any]:
    """Build a row for DatabaseManager.save_position."""
    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "entry_price": 100.0,
        "current_price": 100.0,
        "unrealized_pnl": 0.0,
        "asset_class": "perpetual",
        "opened_at": "2026-09-30T00:00:00+00:00",
    }


class TestGrids:
    """GET /api/grids must list active grids."""

    def test_route_returns_the_active_grid(self, client):
        """One status dict from the manager becomes one grid in the response."""
        manager = Mock()
        manager.get_all_active_grids.return_value = [dict(GRID_STATUS)]

        with patch.object(api_server.bot_integration, "grid_manager", manager):
            response = client.get("/api/grids")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        grid = body["data"][0]
        assert grid["symbol"] == "BTC"
        assert grid["state"] == "active"
        # Keys interface.html reads in updateGrids().
        assert grid["status"] == "active"
        assert grid["total_buy_fills"] == 3
        assert grid["total_sell_fills"] == 2
        assert grid["net_position"] == 0.01
        assert grid["realized_pnl"] == 9.5
        assert grid["total_pnl"] == 8.25
        assert grid["completed_round_trips"] == 2
        assert grid["filled_orders"] == 5
        assert grid["grid_capital"] == 500.0
        assert grid["emergency_stop"] == 58000.0

    def test_no_active_grids_is_an_empty_list(self):
        """An empty manager result stays an empty list."""
        integration = api_server.BotIntegration()
        manager = Mock()
        manager.get_all_active_grids.return_value = []
        integration.grid_manager = manager

        assert integration.get_grids() == []

    def test_maps_the_real_get_grid_status_output(self):
        """The mapping follows the real manager's status dict, not a guess."""
        manager = GridLifecycleManager.__new__(GridLifecycleManager)
        manager._grids = {
            "ETH": {
                "state": GridState.ACTIVE,
                "grid_capital": 250.0,
                "emergency_stop": 2900.0,
            },
            "SOL": {"state": GridState.CLOSED, "grid_capital": 10.0},
        }
        manager._metrics = {
            "ETH": GridMetrics(
                total_buy_fills=4,
                total_sell_fills=1,
                realized_pnl=2.0,
                unrealized_pnl=0.5,
                net_position=0.3,
                completed_round_trips=1,
            )
        }
        integration = api_server.BotIntegration()
        integration.grid_manager = manager

        grids = integration.get_grids()

        assert [g["symbol"] for g in grids] == ["ETH"]
        grid = grids[0]
        assert grid["status"] == "active"
        assert grid["total_buy_fills"] == 4
        assert grid["total_sell_fills"] == 1
        assert grid["realized_pnl"] == 2.0
        assert grid["total_pnl"] == 2.5
        assert grid["net_position"] == 0.3
        assert grid["completed_round_trips"] == 1
        assert grid["grid_capital"] == 250.0


class TestSyncPositions:
    """BotIntegration.sync_positions against a real DatabaseManager."""

    @pytest.mark.asyncio
    async def test_persists_live_and_closes_stale(self, database):
        """Live rows are upserted, stale rows are closed by their own side."""
        database.save_position(_stored("ETH", "SHORT", quantity=2.0))
        database.save_position(_stored("BTC", "LONG", quantity=0.1))
        venue = ReadOnlyVenue(
            [
                _live("BTC", "bid", 0.5, 60000.0),
                _live("SOL", "ask", 3.0, 150.0),
                _live("DOGE", "bid", 0.0, 0.1),
            ]
        )
        integration = api_server.BotIntegration()
        integration.database = database
        integration.pacifica_client = venue  # type: ignore[assignment]

        result = await integration.sync_positions()

        assert result["errors"] == []
        assert result["success"] is True
        assert result["synced_count"] == 2
        assert result["updated_count"] == 1
        assert result["inserted_count"] == 1
        assert result["closed_count"] == 1
        assert venue.calls == 1

        btc = database.get_position("BTC", "LONG")
        assert btc is not None
        assert btc["quantity"] == 0.5
        assert btc["entry_price"] == 60000.0
        sol = database.get_position("SOL", "SHORT")
        assert sol is not None
        assert sol["quantity"] == 3.0
        assert database.get_position("ETH", "SHORT") is None
        rows = {(p["symbol"], p["side"]) for p in database.get_positions()}
        assert rows == {("BTC", "LONG"), ("SOL", "SHORT")}

    @pytest.mark.asyncio
    async def test_side_flip_closes_the_old_side(self, database):
        """A LONG row is closed when the venue now reports a SHORT."""
        database.save_position(_stored("BTC", "LONG"))
        integration = api_server.BotIntegration()
        integration.database = database
        integration.pacifica_client = ReadOnlyVenue(  # type: ignore[assignment]
            [_live("BTC", "ask", 0.2, 61000.0)]
        )

        result = await integration.sync_positions()

        assert result["success"] is True
        assert result["inserted_count"] == 1
        assert result["updated_count"] == 0
        assert result["closed_count"] == 1
        assert database.get_position("BTC", "LONG") is None
        assert database.get_position("BTC", "SHORT") is not None

    @pytest.mark.asyncio
    async def test_success_is_false_when_an_item_errors(self, database, monkeypatch):
        """A failed close is reported and flips success, good items still land."""
        database.save_position(_stored("ETH", "SHORT"))
        integration = api_server.BotIntegration()
        integration.database = database
        integration.pacifica_client = ReadOnlyVenue(  # type: ignore[assignment]
            [_live("BTC", "bid", 0.5, 60000.0)]
        )
        closed_with: List[Any] = []

        def failing_close(symbol: str, side: str, exit_price: Any = None) -> None:
            closed_with.append((symbol, side))
            raise RuntimeError("database is locked")

        monkeypatch.setattr(database, "close_position", failing_close)

        result = await integration.sync_positions()

        assert closed_with == [("ETH", "SHORT")]
        assert result["success"] is False
        assert result["errors"] == ["Error closing ETH: database is locked"]
        assert result["synced_count"] == 1
        assert result["inserted_count"] == 1
        assert result["closed_count"] == 0
        assert database.get_position("BTC", "LONG") is not None
        assert database.get_position("ETH", "SHORT") is not None

    @pytest.mark.asyncio
    async def test_route_reports_failure(self, client):
        """POST /api/positions/sync passes the method's success flag through."""
        failed = {"success": False, "synced_count": 0, "errors": ["boom"]}
        with patch.object(
            api_server.bot_integration,
            "sync_positions",
            AsyncMock(return_value=failed),
        ):
            response = client.post("/api/positions/sync")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["data"]["errors"] == ["boom"]


class TestEventHistory:
    """GET /api/debug/event-history must read the deque-backed history."""

    def test_returns_the_last_twenty_events(self, client):
        """25 published events yield the newest 20, oldest first."""
        bus = EventBus()
        for i in range(25):
            bus.publish_event(EventType.SIGNAL_GENERATED, {f"k{i}": i}, "test")
        bot = SimpleNamespace(event_bus=bus)

        with patch.object(api_server.bot_integration, "trading_bot", bot):
            response = client.get("/api/debug/event-history")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True, body
        assert body["history_window"] == 25
        events = body["recent_events"]
        assert len(events) == 20
        assert [e["data_keys"] for e in events] == [[f"k{i}"] for i in range(5, 25)]
        assert events[0]["type"] == EventType.SIGNAL_GENERATED.value
        assert events[0]["source"] == "test"
