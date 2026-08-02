"""Endpoint tests for the FastAPI control interface.

Safety notes for anyone extending this file:

* ``api_server`` is imported for its module-level ``app`` and
  ``bot_integration`` singleton. Constructing ``BotIntegration`` is inert -
  it only assigns ``None`` placeholders and a lock - so the import does not
  reach the network, a database, or a port.
* ``TestClient`` is deliberately NOT used as a context manager. Starlette
  runs the lifespan/startup handlers only on ``__enter__``, and this app's
  startup handler calls ``BotIntegration.initialize()``, which builds real
  clients. Entering the context would do exactly what these tests must not
  do. ``initialize`` is additionally patched to a no-op as a second guard.
* Every endpoint delegates to the ``bot_integration`` singleton, so the
  tests patch that object's methods rather than the routes themselves.
  Nothing here contacts the running server on port 8000.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from fastapi.testclient import TestClient

from trading_bot_v2 import api_server


TRADES = [
    {
        "id": 1,
        "symbol": "BTC/USD",
        "side": "buy",
        "quantity": 1.0,
        "entry_price": 45000.0,
        "exit_price": 46000.0,
        "pnl": 1000.0,
        "status": "closed",
    },
    {
        "id": 2,
        "symbol": "ETH/USD",
        "side": "sell",
        "quantity": 0.5,
        "entry_price": 3000.0,
        "exit_price": None,
        "pnl": None,
        "status": "open",
    },
]

POSITIONS = [
    {
        "id": 1,
        "symbol": "BTC/USD",
        "side": "long",
        "quantity": 1.0,
        "entry_price": 45000.0,
        "unrealized_pnl": 500.0,
    }
]

STATUS = {
    "is_running": True,
    "positions_count": 0,
    "trades_count": 0,
    "total_pnl": 0.0,
    "account_balance": 0.0,
    "active_grids": 0,
    "current_regime": "unknown",
    "circuit_breaker_triggered": False,
}


@pytest.fixture
def integration():
    """Patch the bot_integration singleton so no real component is built.

    Yields:
        The patched singleton, with every method the routes call replaced
        by a mock returning fixed data.
    """
    target = api_server.bot_integration
    with (
        patch.object(target, "initialize", Mock()),
        patch.object(target, "get_status", Mock(return_value=dict(STATUS))),
        patch.object(
            target,
            "get_trades",
            Mock(side_effect=lambda limit=100, exchange=None: TRADES[:limit]),
        ),
        patch.object(target, "get_positions", Mock(return_value=list(POSITIONS))),
        patch.object(target, "start", AsyncMock()),
        patch.object(target, "stop", AsyncMock()),
        patch.object(api_server, "broadcast_update", AsyncMock()),
    ):
        yield target


@pytest.fixture
def client(integration):
    """Build a TestClient bound to the app without running its lifespan.

    Args:
        integration: The patched bot_integration singleton.

    Returns:
        A TestClient that never triggers startup handlers.
    """
    return TestClient(api_server.app)


class TestAPIServer:
    """Test suite for the API server endpoints."""

    def test_get_root(self, client):
        """GET / serves the HTML dashboard."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<!DOCTYPE html>" in response.text

    def test_get_status(self, client):
        """GET /api/status wraps bot_integration.get_status()."""
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["is_running"] is True
        assert data["data"]["positions_count"] == 0
        assert data["data"]["trades_count"] == 0
        assert data["data"]["total_pnl"] == 0.0

    def test_get_status_propagates_failure(self, client, integration):
        """A failing status call surfaces as a 500 rather than a bad payload."""
        integration.get_status.side_effect = RuntimeError("boom")
        response = client.get("/api/status")
        assert response.status_code == 500

    def test_get_trades(self, client):
        """GET /api/trades returns the trade list."""
        response = client.get("/api/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) == 2
        assert data["data"][0]["symbol"] == "BTC/USD"

    def test_get_trades_with_limit(self, client, integration):
        """The limit query parameter reaches bot_integration.get_trades."""
        response = client.get("/api/trades?limit=1")
        assert response.status_code == 200
        assert len(response.json()["data"]) == 1
        assert integration.get_trades.call_args.kwargs["limit"] == 1

    def test_get_trades_with_exchange_filter(self, client, integration):
        """The exchange query parameter reaches bot_integration.get_trades."""
        response = client.get("/api/trades?exchange=blofin")
        assert response.status_code == 200
        assert integration.get_trades.call_args.kwargs["exchange"] == "blofin"

    def test_get_positions(self, client):
        """GET /api/positions returns the position list."""
        response = client.get("/api/positions")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["symbol"] == "BTC/USD"

    def test_start_bot(self, client, integration):
        """POST /api/bot/start awaits BotIntegration.start()."""
        response = client.post("/api/bot/start")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Bot started successfully"
        integration.start.assert_awaited_once()

    def test_stop_bot(self, client, integration):
        """POST /api/bot/stop awaits BotIntegration.stop()."""
        response = client.post("/api/bot/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Bot stopped successfully"
        integration.stop.assert_awaited_once()

    def test_start_bot_failure_is_a_500(self, client, integration):
        """A failing start surfaces as a 500, not a success payload."""
        integration.start.side_effect = RuntimeError("cannot start")
        response = client.post("/api/bot/start")
        assert response.status_code == 500

    def test_invalid_endpoint(self, client):
        """An unknown route returns 404."""
        assert client.get("/api/invalid").status_code == 404


class TestBotIntegrationGuards:
    """Tests for the real singleton's start/stop guards.

    These call the genuine coroutines rather than mocks, so they must only
    exercise paths that return before ``initialize()`` is reached.
    """

    @pytest.mark.asyncio
    async def test_start_is_a_noop_when_already_running(self):
        """start() returns early when the bot is already running.

        The early return happens inside the lock, before initialize() is
        reached, so no real component is constructed.
        """
        target = api_server.bot_integration
        with patch.object(target, "initialize", Mock()) as init:
            original = target._is_running
            target._is_running = True
            try:
                await target.start()
            finally:
                target._is_running = original
            init.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_is_a_noop_when_not_running(self):
        """stop() returns early when the bot is not running."""
        target = api_server.bot_integration
        bot = Mock()
        with patch.object(target, "trading_bot", bot):
            original = target._is_running
            target._is_running = False
            try:
                await target.stop()
            finally:
                target._is_running = original
            bot.stop.assert_not_called()
