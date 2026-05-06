import pytest
import time
import threading
from unittest.mock import patch, Mock, MagicMock
from .trading_bot import TradingBot
from .config import config


@pytest.fixture
def mock_client():
    """Fixture to create a mocked PacificaClient."""
    client = Mock()
    client.get_positions.return_value = []
    client.get_markets.return_value = [{"symbol": "BTC/USD"}, {"symbol": "ETH/USD"}]
    client.place_order.return_value = {"order_id": "123"}
    return client


@pytest.fixture
def mock_config():
    """Fixture to create a mocked config."""
    config_mock = Mock()
    config_mock.pacifica_private_key = "test_private_key"
    config_mock.pacifica_public_key = "test_public_key"
    config_mock.testnet = True
    config_mock.max_positions = 5
    return config_mock


@pytest.fixture
def bot(mock_config, mock_client):
    """Fixture to create a TradingBot instance with mocks."""
    with patch("trading_bot_v2.trading_bot.config", mock_config):
        return TradingBot(db=Mock(), client=mock_client)


class TestTradingBot:
    """Test suite for TradingBot class."""

    def test_init_success(self, bot, mock_config, mock_client):
        """Test successful initialization."""
        assert bot.config == mock_config
        assert bot.client == mock_client
        assert bot._running_event.is_set() is False
        assert bot.positions == []
        assert bot.trades == []
        assert bot.thread is None

    def test_init_missing_private_key(self, monkeypatch):
        """Test initialization fails without private key."""
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "test_public_key")
        # Don't set private key
        with pytest.raises(ValueError, match="Pacifica keys not set in config"):
            TradingBot()

    def test_init_missing_public_key(self, monkeypatch):
        """Test initialization fails without public key."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "test_private_key")
        # Don't set public key
        with pytest.raises(ValueError, match="Pacifica keys not set in config"):
            TradingBot()

    def test_get_status(self, bot):
        """Test get_status returns correct dict."""
        bot.positions = [{"pnl": 100.0}, {"pnl": -50.0}]
        bot.trades = [
            {"symbol": "BTC/USD", "side": "buy", "quantity": 1.0, "order": {"id": 1}}
        ]
        status = bot.get_status()
        assert status["is_running"] is False
        assert status["positions_count"] == 2
        assert status["recent_trades"] == bot.trades
        assert status["total_pnl"] == 50.0

    def test_start_stop(self, bot):
        """Test start and stop functionality."""
        with patch("trading_bot.time.sleep", side_effect=lambda x: None):
            bot.start()
            assert bot._running_event.is_set() is True
            assert bot.thread is not None
            assert bot.thread.is_alive()

            # Let it run briefly
            time.sleep(0.1)
            bot.stop()
            assert bot._running_event.is_set() is False
            # Thread should join
            bot.thread.join(timeout=1)
            assert not bot.thread.is_alive()

    def test_start_already_running(self, bot):
        """Test starting when already running does nothing."""
        with patch("trading_bot.time.sleep", side_effect=lambda x: None):
            bot.start()
            assert bot._running_event.is_set() is True
            initial_thread = bot.thread
            bot.start()  # Should not create new thread
            assert bot.thread == initial_thread
            bot.stop()

    def test_update_positions(self, bot, mock_client):
        """Test _update_positions updates positions and adds pnl."""
        mock_positions = [{"symbol": "BTC/USD", "quantity": 1.0}]
        mock_client.get_positions.return_value = mock_positions
        bot._update_positions()
        assert bot.positions == mock_positions
        assert bot.positions[0]["pnl"] == 0.0

    def test_update_positions_existing_pnl(self, bot, mock_client):
        """Test _update_positions preserves existing pnl."""
        mock_positions = [{"symbol": "BTC/USD", "quantity": 1.0, "pnl": 200.0}]
        mock_client.get_positions.return_value = mock_positions
        bot._update_positions()
        assert bot.positions[0]["pnl"] == 200.0

    # Removed (2026-05-02): tests for legacy _check_signals / _execute_signal.
    # Those bot methods were deleted along with the corresponding production
    # code path. Active signal flow is event-driven via SIGNAL_GENERATED →
    # _handle_signal_generated → _coordinate_signal_execution. See
    # tests/test_signal_routing.py for the current coverage.

    @patch("trading_bot.logging.warning")
    def test_monitor_risk_warning(self, mock_warning, bot):
        """Test _monitor_risk logs warning when pnl < -1000."""
        bot.positions = [{"pnl": -600}, {"pnl": -500}]
        bot._monitor_risk()
        mock_warning.assert_called_once_with("Total PnL loss exceeds $1000: -1100")

    @patch("trading_bot.logging.warning")
    def test_monitor_risk_no_warning(self, mock_warning, bot):
        """Test _monitor_risk does not log when pnl >= -1000."""
        bot.positions = [{"pnl": -500}, {"pnl": -400}]
        bot._monitor_risk()
        mock_warning.assert_not_called()
