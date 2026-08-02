import json
import pytest
import requests_mock
from unittest.mock import patch, Mock
from .pacifica_client import PacificaClient
from solders.keypair import Keypair


@pytest.fixture
def client():
    """Fixture to create a PacificaClient instance for testing."""
    with patch.object(
        Keypair, "from_base58_string", return_value=Mock()
    ) as mock_from_base58:
        mock_keypair = Mock()
        mock_keypair.pubkey.return_value = Mock()
        mock_keypair.pubkey().__str__ = Mock(return_value="test_agent_public_key")
        mock_from_base58.return_value = mock_keypair
        client = PacificaClient(
            agent_wallet_private_key="test_private_key",
            account_public_key="test_public_key",
            testnet=True,
        )
        return client


@pytest.fixture
def mainnet_client():
    """Fixture for mainnet client."""
    with patch.object(
        Keypair, "from_base58_string", return_value=Mock()
    ) as mock_from_base58:
        mock_keypair = Mock()
        mock_keypair.pubkey.return_value = Mock()
        mock_keypair.pubkey().__str__ = Mock(return_value="test_agent_public_key")
        mock_from_base58.return_value = mock_keypair
        client = PacificaClient(
            agent_wallet_private_key="test_private_key",
            account_public_key="test_public_key",
            testnet=False,
        )
        return client


def test_url_selection_testnet(client):
    """Test that testnet URL is selected when testnet=True."""
    assert client.base_url == "https://test-api.pacifica.fi/api/v1"


def test_url_selection_mainnet(mainnet_client):
    """Test that mainnet URL is selected when testnet=False."""
    assert mainnet_client.base_url == "https://api.pacifica.fi/api/v1"


# Removed test_sign_request as signing method changed


def test_get_balance(client):
    """Test get_balance method with mocked response."""
    mock_response = {"balance": 1000.0}
    with patch("requests.get") as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response
        mock_get.return_value = mock_resp
        result = client.get_balance()
        assert result == mock_response
        # Verify call
        mock_get.assert_called_once_with(
            "https://test-api.pacifica.fi/api/v1/account",
            params={"account": "test_public_key"},
        )


def test_get_positions(client):
    """Test get_positions method with mocked response."""
    mock_response = {"data": [{"symbol": "BTC/USD", "quantity": 1.0}]}
    with requests_mock.Mocker() as m:
        m.get(
            "https://test-api.pacifica.fi/api/v1/positions?account=test_public_key",
            json=mock_response,
        )
        result = client.get_positions()
        assert result == [{"symbol": "BTC/USD", "quantity": 1.0}]


def test_place_order(client):
    """Test place_order method with mocked response."""
    mock_response = {"order_id": "12345"}
    with patch(
        "pacifica_client.sign_message", return_value=("message", "signature")
    ) as mock_sign:
        with requests_mock.Mocker() as m:
            m.post(
                "https://test-api.pacifica.fi/api/v1/orders/create", json=mock_response
            )
            result = client.place_order("BTC/USD", "buy", 1.0, "limit", 50000.0)
            assert result == mock_response
            # Verify sign_message was called
            mock_sign.assert_called_once()


def test_cancel_order(client):
    """Test cancel_order method with mocked response."""
    mock_response = {"status": "cancelled"}
    with patch(
        "pacifica_client.sign_message", return_value=("message", "signature")
    ) as mock_sign:
        with requests_mock.Mocker() as m:
            m.post(
                "https://test-api.pacifica.fi/api/v1/orders/cancel", json=mock_response
            )
            result = client.cancel_order("12345")
            assert result == mock_response
            # Verify sign_message was called
            mock_sign.assert_called_once()


def test_get_market_data(client):
    """Test get_market_data method with mocked response."""
    mock_response = {"price": 50000.0}
    with requests_mock.Mocker() as m:
        m.get("https://test-api.pacifica.fi/api/v1/markets/BTC/USD", json=mock_response)
        result = client.get_market_data("BTC/USD")
        assert result == mock_response


def test_get_markets(client):
    """Test get_markets method with mocked response."""
    mock_response = {"data": [{"symbol": "BTC/USD"}, {"symbol": "ETH/USD"}]}
    with requests_mock.Mocker() as m:
        m.get("https://test-api.pacifica.fi/api/v1/markets", json=mock_response)
        result = client.get_markets()
        assert result == [{"symbol": "BTC/USD"}, {"symbol": "ETH/USD"}]


@pytest.mark.parametrize(
    "status_code,expected_error",
    [
        (401, "Authentication failed"),
        (429, "Rate limit exceeded"),
        (404, "API error: Error message"),
        (500, "API error: Error message"),
    ],
)
def test_error_handling(client, status_code, expected_error):
    """Test error handling for various HTTP status codes."""
    with requests_mock.Mocker() as m:
        m.get(
            "https://test-api.pacifica.fi/api/v1/account?account=test_public_key",
            status_code=status_code,
            text="Error message",
        )
        with pytest.raises(ValueError, match=expected_error):
            client.get_balance()
