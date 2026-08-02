"""Tests for TradingBot construction and status reporting.

Scope note. This file used to assert a TradingBot API that no longer
exists (``bot.positions``, ``bot.trades``, a ``_monitor_risk`` that logged
warnings, a ``get_status`` returning ``positions_count``). Those
attributes and behaviours were removed when TradingBot became a pure
coordinator; ``_monitor_risk`` is now an explicit no-op placeholder. What
survives here is the coverage that still describes live behaviour:

* how ``__init__`` wires injected collaborators, including the
  client-construction branch that reads config,
* the ``get_status`` payload contract, including its failure path.

Position reconciliation (``_update_positions``) is covered in depth by
tests/test_position_reconciler.py and is not duplicated here.
"""

import pytest
from unittest.mock import Mock, patch

from trading_bot_v2.trading_bot import TradingBot


@pytest.fixture
def mock_client():
    """Create a mocked exchange client.

    Returns:
        A Mock standing in for PacificaClient.
    """
    client = Mock()
    client.get_positions.return_value = []
    client.get_markets.return_value = [{"symbol": "BTC/USD"}]
    return client


@pytest.fixture
def bot(mock_client):
    """Build a TradingBot with every external collaborator mocked.

    Args:
        mock_client: The mocked exchange client.

    Returns:
        A TradingBot instance that touches no real service.
    """
    return TradingBot(db=Mock(), client=mock_client)


class TestTradingBotConstruction:
    """Tests for how __init__ wires its collaborators."""

    def test_injected_client_is_used_as_is(self, bot, mock_client):
        """An injected client is stored without being replaced."""
        assert bot.client is mock_client

    def test_starts_idle(self, bot):
        """A freshly built bot is not running and has no loop thread."""
        assert bot.is_running is False
        assert bot.thread is None

    def test_builds_a_client_from_config_when_none_injected(self):
        """__init__ constructs a client from the pacifica_* config keys.

        Regression test. This branch previously read
        ``config.agent_wallet_private_key`` / ``config.account_public_key``
        - the .env variable names rather than the Config attribute names -
        so it raised AttributeError instead of building a client. It went
        unnoticed because api_server always injects a client.
        """
        with patch("trading_bot_v2.trading_bot.PacificaClient") as client_cls:
            with patch("trading_bot_v2.trading_bot.config") as cfg:
                cfg.pacifica_private_key = "private_key_123"
                cfg.pacifica_public_key = "public_key_456"
                cfg.testnet = True
                TradingBot(db=Mock())

        client_cls.assert_called_once_with(
            agent_wallet_private_key="private_key_123",
            account_public_key="public_key_456",
            testnet=True,
        )


class TestGetStatus:
    """Tests for the get_status payload contract."""

    def test_reports_balance_positions_and_risk(self, bot, mock_client):
        """get_status summarises balance, positions, P&L and exposure."""
        mock_client.get_positions.return_value = [
            {
                "symbol": "BTC-PERP",
                "side": "long",
                "quantity": "1.5",
                "entry_price": "45000",
                "unrealized_pnl": "100",
            },
            {
                "symbol": "ETH-PERP",
                "side": "short",
                "quantity": "2",
                "entry_price": "3000",
                "unrealized_pnl": "-40",
            },
        ]
        with (
            patch.object(bot, "_get_account_balance", return_value=1000.0),
            patch.object(bot, "_get_current_exposure", return_value=250.0),
        ):
            status = bot.get_status()

        assert status["is_running"] is False
        assert status["account_balance"] == 1000.0
        assert status["total_positions"] == 2
        assert status["total_unrealized_pnl"] == 60.0
        assert status["total_exposure"] == 250.0
        assert status["risk_percentage"] == 25.0
        assert status["positions"][0]["symbol"] == "BTC-PERP"
        # Exchange strings are coerced to floats for the dashboard.
        assert status["positions"][0]["quantity"] == 1.5

    def test_zero_balance_does_not_divide_by_zero(self, bot):
        """A zero balance yields 0 risk rather than raising."""
        with (
            patch.object(bot, "_get_account_balance", return_value=0.0),
            patch.object(bot, "_get_current_exposure", return_value=10.0),
        ):
            assert bot.get_status()["risk_percentage"] == 0

    def test_failure_is_reported_not_raised(self, bot, mock_client):
        """A client failure returns an error payload instead of raising."""
        mock_client.get_positions.side_effect = RuntimeError("exchange down")
        with patch.object(bot, "_get_account_balance", return_value=1000.0):
            status = bot.get_status()

        assert status["is_running"] is False
        assert "exchange down" in status["error"]
