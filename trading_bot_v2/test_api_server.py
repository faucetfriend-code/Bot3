import pytest
from unittest.mock import patch, Mock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def mock_db():
    """Fixture to create a mocked Database."""

    class MockDB:
        def get_trades(self, limit=100):
            return [
                {
                    "id": 1,
                    "symbol": "BTC/USD",
                    "side": "buy",
                    "quantity": 1.0,
                    "entry_price": 45000.0,
                    "exit_price": 46000.0,
                    "entry_time": "2024-01-01T10:00:00",
                    "exit_time": "2024-01-01T11:00:00",
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
                    "entry_time": "2024-01-01T12:00:00",
                    "exit_time": None,
                    "pnl": None,
                    "status": "open",
                },
            ][:limit]

        def get_positions(self):
            return [
                {
                    "id": 1,
                    "symbol": "BTC/USD",
                    "side": "long",
                    "quantity": 1.0,
                    "entry_price": 45000.0,
                    "current_price": 45500.0,
                    "unrealized_pnl": 500.0,
                    "opened_at": "2024-01-01T10:00:00",
                    "updated_at": "2024-01-01T10:30:00",
                }
            ]

    return MockDB()


@pytest.fixture
def mock_bot():
    """Fixture to create a mocked TradingBot."""
    mock_bot = Mock()
    mock_bot.get_status.return_value = {
        "is_running": True,
        "positions_count": 0,
        "trades_count": 0,
        "total_pnl": 0.0,
    }
    # Use simple data to avoid recursion
    mock_bot.trades = []
    mock_bot.positions = []
    mock_bot._running_event = Mock()
    mock_bot._running_event.is_set.return_value = True
    mock_bot.start = Mock()
    mock_bot.stop = Mock()
    mock_bot.client = Mock()
    mock_bot.client.get_markets.return_value = [
        {"symbol": "BTC/USD", "price": 50000},
        {"symbol": "ETH/USD", "price": 3000},
    ]
    return mock_bot


@pytest.fixture
def client(mock_db, mock_bot):
    """Fixture to create a TestClient for the FastAPI app."""
    import sys
    from types import SimpleNamespace

    mock_config_obj = SimpleNamespace()
    mock_config_obj.pacifica_private_key = "test_private"
    mock_config_obj.pacifica_public_key = "test_public"
    mock_config_obj.testnet = True
    mock_config_obj.log_level = "INFO"
    mock_config_module = SimpleNamespace()
    mock_config_module.config = mock_config_obj
    with (
        patch.dict(
            "sys.modules",
            {
                "database": Mock(Database=Mock(return_value=mock_db)),
                "config": mock_config_module,
                "pacifica_client": Mock(PacificaClient=Mock()),
            },
        ),
        patch("trading_bot.TradingBot", return_value=mock_bot),
        patch("api_server.bot", mock_bot),
    ):
        from api_server import app

        return TestClient(app)


@pytest.fixture
def mock_db():
    """Fixture to create a mocked Database."""
    return Mock()


class TestAPIServer:
    """Test suite for API server endpoints."""

    def test_get_root(self, client):
        """Test GET / endpoint serves HTML interface."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Interface.html has content
        assert "<!DOCTYPE html>" in response.text

    def test_get_status(self, mock_bot, client):
        """Test GET /api/status returns correct JSON."""
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert data["data"]["bot_running"] is True
        assert data["data"]["positions_count"] == 0  # bot.positions is empty
        assert data["data"]["trades_count"] == 0  # bot.trades is empty
        assert data["data"]["total_pnl"] == 0.0

    def test_get_trades(self, mock_bot, client):
        """Test GET /api/trades returns recent trades."""
        response = client.get("/api/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        # Should return database mock data
        assert len(data["data"]) == 2
        assert data["data"][0]["symbol"] == "BTC/USD"

    def test_get_trades_with_limit(self, mock_bot, client):
        """Test GET /api/trades with limit parameter."""
        response = client.get("/api/trades?limit=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

    def test_get_positions(self, mock_bot, client):
        """Test GET /api/positions returns positions."""
        response = client.get("/api/positions")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert len(data["data"]) == 1

    def test_start_bot_success(self, mock_bot, client):
        """Test POST /api/bot/start starts the bot."""
        mock_bot._running_event.is_set.return_value = False
        response = client.post("/api/bot/start")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Bot started"
        mock_bot.start.assert_called_once()

    def test_start_bot_already_running(self, mock_bot, client):
        """Test POST /api/bot/start when bot is already running."""
        mock_bot._running_event.is_set.return_value = True
        response = client.post("/api/bot/start")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "Bot already running"
        mock_bot.start.assert_not_called()

    def test_stop_bot(self, mock_bot, client):
        """Test POST /api/bot/stop stops the bot."""
        response = client.post("/api/bot/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Bot stopped"
        mock_bot.stop.assert_called_once()

    def test_get_markets_success(self, mock_bot, client):
        """Test GET /api/markets returns markets data."""
        response = client.get("/api/markets")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert len(data["data"]) == 2
        assert data["data"][0]["symbol"] == "BTC/USD"

    def test_get_markets_error(self, mock_bot, client):
        """Test GET /api/markets handles exceptions."""
        mock_bot.client.get_markets.side_effect = Exception("API error")
        response = client.get("/api/markets")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "API error"

    def test_cors_headers(self, client):
        """Test CORS middleware adds correct headers."""
        response = client.get(
            "/api/status", headers={"Origin": "http://localhost:3000"}
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        assert response.headers["access-control-allow-origin"] == "*"

    def test_invalid_endpoint(self, client):
        """Test invalid endpoint returns 404."""
        response = client.get("/api/invalid")
        assert response.status_code == 404
