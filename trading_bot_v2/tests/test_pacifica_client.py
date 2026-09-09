"""Tests for the Pacifica REST client.

All HTTP is intercepted by requests_mock, and ``sign_message`` is patched,
so nothing here contacts the exchange or touches a real key. The endpoint
paths asserted below are the ones the client actually calls today:
``/account``, ``/positions``, ``/orders/create``, ``/orders/cancel``,
``/prices`` and ``/info``.
"""

import pytest
import requests_mock
from unittest.mock import Mock, patch

from trading_bot_v2.pacifica_client import PacificaClient

TESTNET_URL = "https://test-api.pacifica.fi/api/v1"
MAINNET_URL = "https://api.pacifica.fi/api/v1"


@pytest.fixture
def signing():
    """Replace Ed25519 signing so no real key material is needed.

    Yields:
        The patched sign_message mock.
    """
    with (
        patch("trading_bot_v2.pacifica_client.Keypair") as keypair,
        patch(
            "trading_bot_v2.pacifica_client.sign_message",
            return_value=("message", "signature"),
        ) as sign,
    ):
        keypair.from_base58_string.return_value = Mock()
        yield sign


@pytest.fixture
def client(signing):
    """Build a testnet client with signing stubbed out.

    Args:
        signing: The patched sign_message fixture.

    Returns:
        A PacificaClient pointed at the testnet base URL.
    """
    return PacificaClient(
        agent_wallet_private_key="test_private_key",
        account_public_key="test_public_key",
        testnet=True,
    )


@pytest.fixture
def mainnet_client(signing):
    """Build a mainnet client with signing stubbed out.

    Args:
        signing: The patched sign_message fixture.

    Returns:
        A PacificaClient pointed at the mainnet base URL.
    """
    return PacificaClient(
        agent_wallet_private_key="test_private_key",
        account_public_key="test_public_key",
        testnet=False,
    )


def test_url_selection_testnet(client):
    """Testnet URL is selected when testnet=True."""
    assert client.base_url == TESTNET_URL


def test_url_selection_mainnet(mainnet_client):
    """Mainnet URL is selected when testnet=False."""
    assert mainnet_client.base_url == MAINNET_URL


def test_get_balance(client):
    """get_balance unwraps the 'data' envelope from GET /account."""
    with requests_mock.Mocker() as m:
        m.get(f"{TESTNET_URL}/account", json={"data": {"balance": 1000.0}})
        assert client.get_balance() == {"balance": 1000.0}


def test_get_balance_signs_the_request(client, signing):
    """get_balance sends the signature as a query parameter."""
    with requests_mock.Mocker() as m:
        m.get(f"{TESTNET_URL}/account", json={"data": {}})
        client.get_balance()
        signing.assert_called_once()
        assert m.last_request.qs["signature"] == ["signature"]
        assert m.last_request.qs["account"] == ["test_public_key"]


def test_get_positions(client):
    """get_positions returns the list inside the 'data' envelope."""
    positions = [{"symbol": "BTC", "quantity": 1.0}]
    with requests_mock.Mocker() as m:
        m.get(f"{TESTNET_URL}/positions", json={"data": positions})
        assert client.get_positions() == positions


def test_place_limit_order(client, signing):
    """A limit order posts to /orders/create with Pacifica's side names."""
    with requests_mock.Mocker() as m:
        m.post(f"{TESTNET_URL}/orders/create", json={"order_id": "12345"})
        result = client.place_order("BTC", "buy", 1.0, "limit", 50000.0)
        assert result == {"order_id": "12345"}
        signing.assert_called_once()
        body = m.last_request.json()
        # Pacifica uses bid/ask, not buy/sell, and amount is a string.
        assert body["side"] == "bid"
        assert body["amount"] == "1.0"
        assert body["price"] == "50000.0"


def test_place_market_order_uses_the_market_endpoint(client):
    """A market order posts to /orders/create_market instead."""
    with requests_mock.Mocker() as m:
        m.post(f"{TESTNET_URL}/orders/create_market", json={"order_id": "9"})
        assert client.place_order("BTC", "sell", 2.0, "market") == {"order_id": "9"}
        assert m.last_request.json()["side"] == "ask"


def test_limit_order_without_price_is_rejected(client):
    """A limit order with no price raises before any request is made."""
    with pytest.raises(ValueError, match="Price is required for limit orders"):
        client.place_order("BTC", "buy", 1.0, "limit")


def test_unsupported_order_type_is_rejected(client):
    """An unknown order type raises."""
    with pytest.raises(ValueError, match="Unsupported order type"):
        client.place_order("BTC", "buy", 1.0, "iceberg")


def test_cancel_order(client, signing):
    """cancel_order posts to /orders/cancel with a numeric order id."""
    with requests_mock.Mocker() as m:
        m.post(f"{TESTNET_URL}/orders/cancel", json={"status": "cancelled"})
        assert client.cancel_order("BTC", "12345") == {"status": "cancelled"}
        signing.assert_called_once()
        # Numeric ids are coerced to int; the API rejects strings here.
        assert m.last_request.json()["order_id"] == 12345


def test_string_success_response_is_normalised(client):
    """The API's bare "success" string becomes a dict.

    Pacifica's order endpoints intermittently return the JSON string
    "success" instead of an order object; callers must still get a dict.
    """
    with requests_mock.Mocker() as m:
        m.post(f"{TESTNET_URL}/orders/create", json="success")
        result = client.place_order("BTC", "buy", 1.0, "limit", 50000.0)
        assert result["success"] is True
        assert result["data"] == {}


def test_get_market_data(client):
    """get_market_data reads the first entry of data.prices from /prices."""
    with requests_mock.Mocker() as m:
        m.get(
            f"{TESTNET_URL}/prices",
            json={"data": {"prices": [{"symbol": "BTC", "price": 50000.0}]}},
        )
        assert client.get_market_data("BTC") == {"symbol": "BTC", "price": 50000.0}


def test_get_market_data_without_prices_returns_empty(client):
    """An empty price list yields an empty dict rather than an IndexError."""
    with requests_mock.Mocker() as m:
        m.get(f"{TESTNET_URL}/prices", json={"data": {"prices": []}})
        assert client.get_market_data("BTC") == {}


def test_get_markets(client):
    """get_markets reads the market list from /info."""
    markets = [{"symbol": "BTC"}, {"symbol": "ETH"}]
    with requests_mock.Mocker() as m:
        m.get(f"{TESTNET_URL}/info", json={"data": markets})
        assert client.get_markets() == markets


@pytest.mark.parametrize(
    "status_code,expected_error",
    [
        (401, "Authentication failed"),
        (404, "API error: Error message"),
        (500, "API error: Error message"),
    ],
)
def test_error_handling(client, status_code, expected_error):
    """HTTP failures surface as ValueError with a descriptive message.

    429 is deliberately excluded: it raises RateLimitError and is retried
    by tenacity with exponential backoff, so asserting it here would add
    roughly 30 seconds of real sleeping to the default suite.
    """
    with requests_mock.Mocker() as m:
        m.get(f"{TESTNET_URL}/account", status_code=status_code, text="Error message")
        with pytest.raises(ValueError, match=expected_error):
            client.get_balance()
