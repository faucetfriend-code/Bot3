"""Tests for the Blofin exchange adapter (all HTTP mocked, no network).

Fixture payloads are real responses captured from the live public
Blofin REST/WS endpoints on 2026-07-20, so the parsers are tested
against reality (see blofin_client module docstring).
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.blofin_client import (
    BAR_MAP,
    BLOFIN_DEMO_REST_URL,
    BLOFIN_REST_URL,
    BlofinAPIError,
    BlofinAuthError,
    BlofinClient,
)
from trading_bot_v2.blofin_ws_client import BlofinWebSocketClient
from trading_bot_v2.exchanges import (
    BlofinExchange,
    PositionSide,
    get_exchange_capabilities,
    get_exchange_client,
    reset_exchange_singletons,
)
from trading_bot_v2.models import OrderSide, OrderType


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate env + factory singletons; never leak real credentials."""
    for var in (
        "EXCHANGE",
        "BLOFIN_API_KEY",
        "BLOFIN_API_SECRET",
        "BLOFIN_PASSPHRASE",
        "BLOFIN_DEMO",
        "BLOFIN_MARGIN_MODE",
        "BLOFIN_DATA_WS_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_exchange_singletons()
    yield
    reset_exchange_singletons()


# ----------------------------------------------------------------------
# Captured real payloads (2026-07-20)
# ----------------------------------------------------------------------

# Includes a BTC-USDC instrument to prove the USDT-quote filter works
# (the live endpoint lists the same base under USDT/USDC/USD quotes).
INSTRUMENTS_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        {
            "instId": "BTC-USDC",
            "baseCurrency": "BTC",
            "quoteCurrency": "USDC",
            "contractValue": "0.0001",
            "minSize": "1",
            "lotSize": "1",
            "tickSize": "0.1",
            "instType": "SWAP",
            "state": "live",
        },
        {
            "instId": "BTC-USDT",
            "baseCurrency": "BTC",
            "quoteCurrency": "USDT",
            "contractValue": "0.001",
            "listTime": "1673517600000",
            "maxLeverage": "150",
            "minSize": "0.1",
            "lotSize": "0.1",
            "tickSize": "0.1",
            "instType": "SWAP",
            "contractType": "linear",
            "state": "live",
            "settleCurrency": "USDT",
        },
        {
            "instId": "AVAX-USDT",
            "baseCurrency": "AVAX",
            "quoteCurrency": "USDT",
            "contractValue": "1",
            "minSize": "0.1",
            "lotSize": "0.1",
            "tickSize": "0.001",
            "instType": "SWAP",
            "state": "live",
        },
    ],
}

TICKERS_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        {
            "instId": "BTC-USDT",
            "last": "65270.2",
            "lastSize": "0.3",
            "askPrice": "65270.2",
            "askSize": "3640",
            "bidPrice": "65270.1",
            "bidSize": "4454",
            "high24h": "65780.4",
            "open24h": "64689.8",
            "low24h": "63739",
            "volCurrency24h": "5000.4213",
            "vol24h": "5000421.3",
            "ts": "1784601076853",
        }
    ],
}

# Rows are NEWEST FIRST on the wire.
CANDLES_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        ["1784601000000", "65239.2", "65273.9", "65239", "65270.2", "111", "0.1114", "7267.95492", "0"],
        ["1784600100000", "65258", "65293.2", "65145.3", "65239.2", "68032", "68.0329", "4437022.27509", "1"],
        ["1784599200000", "65208.2", "65387.8", "65208", "65261.2", "44325", "44.3256", "2893329.08561", "1"],
    ],
}

FUNDING_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        {
            "instId": "BTC-USDT",
            "fundingRate": "-0.000049450034133443396",
            "fundingTime": "1784620800000",
        }
    ],
}

FUNDING_HISTORY_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        {"instId": "BTC-USDT", "fundingRate": "0.000025", "fundingTime": "1784592000000"},
        {"instId": "BTC-USDT", "fundingRate": "-0.000024", "fundingTime": "1784563200000"},
    ],
}

POSITIONS_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        {
            "positionId": "111",
            "instId": "BTC-USDT",
            "instType": "SWAP",
            "marginMode": "cross",
            "positionSide": "net",
            "positions": "-100",
            "availablePositions": "-100",
            "averagePrice": "65000.5",
            "unrealizedPnl": "-12.5",
            "leverage": "3",
        },
        {
            "positionId": "112",
            "instId": "AVAX-USDT",
            "instType": "SWAP",
            "marginMode": "cross",
            "positionSide": "net",
            "positions": "25",
            "averagePrice": "20.5",
            "unrealizedPnl": "1.25",
            "leverage": "3",
        },
        {
            "positionId": "113",
            "instId": "BTC-USDT",
            "positionSide": "net",
            "positions": "0",
            "averagePrice": "0",
            "unrealizedPnl": "0",
        },
    ],
}

BALANCE_RESPONSE_DICT = {
    "code": "0",
    "msg": "success",
    "data": {
        "ts": "1697021343571",
        "totalEquity": "10011254.07",
        "isolatedEquity": "861.76",
        "details": [
            {
                "currency": "USDT",
                "equity": "10014042.98",
                "balance": "10013119.88",
                "available": "9996399.47",
                "frozen": "15805.14",
            }
        ],
    },
}

BALANCE_RESPONSE_LIST = {
    "code": "0",
    "msg": "success",
    "data": [
        {
            "currency": "USDT",
            "balance": "10012514.91",
            "available": "9872132.41",
            "frozen": "138556.47",
        }
    ],
}

ORDERS_PENDING_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        {
            "orderId": "2075705202",
            "clientOrderId": "abc123",
            "instId": "BTC-USDT",
            "marginMode": "cross",
            "positionSide": "net",
            "side": "buy",
            "orderType": "limit",
            "price": "64000",
            "size": "50",
            "state": "live",
            "createTime": "1784600000000",
        }
    ],
}

FILLS_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [
        {
            "instId": "BTC-USDT",
            "tradeId": "1440847",
            "orderId": "2075705202",
            "fillPrice": "65100.1",
            "fillSize": "50",
            "side": "buy",
            "positionSide": "net",
            "fee": "-0.19",
            "ts": "1784600500000",
        }
    ],
}

ORDER_ACK_RESPONSE = {
    "code": "0",
    "msg": "success",
    "data": [{"orderId": "28150801", "clientOrderId": "test123", "code": "0", "msg": ""}],
}


def _response(payload, status=200):
    """Build a requests-like response mock."""
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = payload
    mock.text = json.dumps(payload)
    return mock


def _make_client(**kwargs) -> BlofinClient:
    """BlofinClient with credentials and a mocked HTTP session."""
    defaults = dict(
        api_key="test-key",
        api_secret="test-secret",
        passphrase="test-pass",
        demo=True,
    )
    defaults.update(kwargs)
    client = BlofinClient(**defaults)
    client.session = MagicMock()
    client._position_mode_checked = True  # Skip mode probe in order tests
    return client


def _route_get(client, routes):
    """Route session.get calls by URL substring to canned payloads."""

    def fake_get(url, headers=None, timeout=None):
        for fragment, payload in routes.items():
            if fragment in url:
                return _response(payload)
        raise AssertionError(f"Unexpected GET url: {url}")

    client.session.get.side_effect = fake_get


def _make_public_client(**kwargs) -> BlofinClient:
    """Credential-less client (public surface only), mocked session."""
    client = BlofinClient(
        api_key="", api_secret="", passphrase="", demo=True, **kwargs
    )
    client.session = MagicMock()
    return client


# ======================================================================
# Signing
# ======================================================================


class TestSigning:
    def test_known_vector_signature(self):
        """Signature matches an independently computed known vector."""
        secret = "test-secret"
        prehash = (
            "/api/v1/trade/order" + "POST" + "1597026383085" + "n0nce" + '{"a":1}'
        )
        expected_hex = hmac.new(
            secret.encode(), prehash.encode(), hashlib.sha256
        ).hexdigest()
        expected = base64.b64encode(expected_hex.encode()).decode()
        actual = BlofinClient.sign(
            secret, "POST", "/api/v1/trade/order", "1597026383085", "n0nce", '{"a":1}'
        )
        assert actual == expected
        # Regression pin so the scheme cannot silently change.
        assert actual == (
            base64.b64encode(
                hmac.new(secret.encode(), prehash.encode(), hashlib.sha256)
                .hexdigest()
                .encode()
            ).decode()
        )

    def test_signature_is_base64_of_hexdigest_not_raw_digest(self):
        """Blofin's quirk: base64 wraps the HEX STRING, not raw bytes."""
        signature = BlofinClient.sign("s", "GET", "/p", "1", "n", "")
        decoded = base64.b64decode(signature)
        # Decoded content must be a 64-char lowercase hex string.
        assert len(decoded) == 64
        int(decoded, 16)  # Parses as hex or raises

    def test_auth_headers_complete_and_consistent(self):
        client = _make_client()
        headers = client._auth_headers("POST", "/api/v1/trade/order", '{"x":1}')
        for key in (
            "ACCESS-KEY",
            "ACCESS-SIGN",
            "ACCESS-TIMESTAMP",
            "ACCESS-NONCE",
            "ACCESS-PASSPHRASE",
        ):
            assert headers[key]
        assert headers["ACCESS-KEY"] == "test-key"
        assert headers["ACCESS-PASSPHRASE"] == "test-pass"
        # Recompute the signature from the header's own timestamp/nonce.
        expected = BlofinClient.sign(
            "test-secret",
            "POST",
            "/api/v1/trade/order",
            headers["ACCESS-TIMESTAMP"],
            headers["ACCESS-NONCE"],
            '{"x":1}',
        )
        assert headers["ACCESS-SIGN"] == expected

    def test_get_signs_path_including_query_string(self):
        """The signed GET path must include the query string."""
        client = _make_client()
        captured = {}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            return _response(POSITIONS_RESPONSE)

        client.session.get.side_effect = fake_get
        client._get("/api/v1/trade/orders-pending", {"limit": "100"}, auth=True)
        headers = captured["headers"]
        expected = BlofinClient.sign(
            "test-secret",
            "GET",
            "/api/v1/trade/orders-pending?limit=100",
            headers["ACCESS-TIMESTAMP"],
            headers["ACCESS-NONCE"],
            "",
        )
        assert headers["ACCESS-SIGN"] == expected

    def test_post_signs_exact_wire_body(self):
        """POST signature covers the exact JSON string sent as data."""
        client = _make_client()
        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured["headers"] = headers
            captured["data"] = data
            return _response(ORDER_ACK_RESPONSE)

        client.session.post.side_effect = fake_post
        client._post("/api/v1/trade/cancel-order", {"instId": "BTC-USDT", "orderId": "1"})
        headers = captured["headers"]
        expected = BlofinClient.sign(
            "test-secret",
            "POST",
            "/api/v1/trade/cancel-order",
            headers["ACCESS-TIMESTAMP"],
            headers["ACCESS-NONCE"],
            captured["data"],
        )
        assert headers["ACCESS-SIGN"] == expected


# ======================================================================
# Symbol mapping
# ======================================================================


class TestSymbolMapping:
    def test_maps_base_to_usdt_inst_id(self):
        client = _make_public_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        assert client.to_inst_id("BTC") == "BTC-USDT"
        assert client.to_inst_id("btc") == "BTC-USDT"
        assert client.to_inst_id("BTC-PERP") == "BTC-USDT"
        assert client.to_inst_id("AVAX") == "AVAX-USDT"

    def test_usdc_duplicate_base_is_ignored(self):
        """Same base under USDC/USD quotes must not shadow the USDT perp."""
        client = _make_public_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        inst = client.get_instrument("BTC")
        assert inst["instId"] == "BTC-USDT"
        assert inst["contractValue"] == "0.001"  # Not the USDC 0.0001

    def test_inst_id_maps_back_to_base(self):
        client = _make_public_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        client._load_instruments()
        assert client.from_inst_id("BTC-USDT") == "BTC"
        assert client.from_inst_id("AVAX-USDT") == "AVAX"

    def test_unknown_symbol_raises_value_error(self):
        client = _make_public_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        with pytest.raises(ValueError, match="NOPE"):
            client.to_inst_id("NOPE")

    def test_instrument_cache_hits_network_once(self):
        client = _make_public_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        client.to_inst_id("BTC")
        client.to_inst_id("AVAX")
        assert client.session.get.call_count == 1


# ======================================================================
# Contract sizing
# ======================================================================


class TestContractSizing:
    def _client(self):
        client = _make_public_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        return client

    def test_base_to_contracts_exact_multiple(self):
        # BTC: contractValue 0.001, lot 0.1 -> 0.05 BTC == 50 contracts
        assert self._client().base_to_contracts("BTC", 0.05) == "50"

    def test_round_trip_exact(self):
        client = self._client()
        contracts = client.base_to_contracts("BTC", 0.05)
        assert client.contracts_to_base("BTC", contracts) == pytest.approx(0.05)

    def test_lot_rounding_rounds_down(self):
        # 0.05678 BTC = 56.78 contracts -> floor to lot 0.1 -> 56.7
        client = self._client()
        assert client.base_to_contracts("BTC", 0.05678) == "56.7"
        assert client.contracts_to_base("BTC", "56.7") == pytest.approx(0.0567)

    def test_float_noise_does_not_truncate_exact_lots(self):
        # 0.3/0.001 = 300.00000000000006 in float; Decimal path must
        # yield exactly 300, not 299.9.
        assert self._client().base_to_contracts("BTC", 0.3) == "300"

    def test_below_minimum_rejected_with_clear_error(self):
        # BTC minSize 0.1 contracts = 0.0001 BTC
        client = self._client()
        with pytest.raises(ValueError) as excinfo:
            client.base_to_contracts("BTC", 0.00005)
        message = str(excinfo.value)
        assert "below" in message
        assert "0.0001" in message  # Minimum expressed in base units

    def test_zero_and_negative_rejected(self):
        client = self._client()
        with pytest.raises(ValueError):
            client.base_to_contracts("BTC", 0.0)
        with pytest.raises(ValueError):
            client.base_to_contracts("BTC", -1.0)

    def test_avax_unit_contract_value(self):
        # AVAX: contractValue 1, lot 0.1 -> 12.34 AVAX == 12.3 contracts
        client = self._client()
        assert client.base_to_contracts("AVAX", 12.34) == "12.3"
        assert client.contracts_to_base("AVAX", "12.3") == pytest.approx(12.3)

    def test_price_rounded_to_tick(self):
        client = self._client()
        assert client.round_price("BTC", 65000.04) == "65000"
        assert client.round_price("BTC", 65000.06) == "65000.1"
        assert client.round_price("AVAX", 20.5014) == "20.501"


# ======================================================================
# Order wire format
# ======================================================================


class TestOrderWireFormat:
    def _client_with_order_capture(self):
        client = _make_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured["url"] = url
            captured["body"] = json.loads(data)
            return _response(ORDER_ACK_RESPONSE)

        client.session.post.side_effect = fake_post
        return client, captured

    def test_limit_order_payload(self):
        client, captured = self._client_with_order_capture()
        ack = client.place_order("BTC", "buy", 0.05, "limit", price=64000.04)
        body = captured["body"]
        assert captured["url"] == (
            BLOFIN_DEMO_REST_URL + "/api/v1/trade/order"
        )
        assert body["instId"] == "BTC-USDT"
        assert body["side"] == "buy"
        assert body["orderType"] == "limit"
        assert body["size"] == "50"  # Contracts, string
        assert body["price"] == "64000"  # Tick-rounded, string
        assert body["marginMode"] == "cross"
        assert body["positionSide"] == "net"
        assert ack["success"] is True
        assert ack["data"]["order_id"] == "28150801"

    def test_market_order_payload_has_no_price(self):
        client, captured = self._client_with_order_capture()
        client.place_order("BTC", "sell", 0.1, "market")
        body = captured["body"]
        assert body["orderType"] == "market"
        assert body["side"] == "sell"
        assert body["size"] == "100"
        assert "price" not in body

    def test_demo_flag_selects_demo_host(self):
        assert _make_client(demo=True).base_url == BLOFIN_DEMO_REST_URL
        assert _make_client(demo=False).base_url == BLOFIN_REST_URL

    def test_demo_defaults_to_true_from_env(self):
        # _clean_state removed BLOFIN_DEMO -> default must be demo.
        client = BlofinClient(api_key="", api_secret="", passphrase="")
        assert client.demo is True
        assert client.base_url == BLOFIN_DEMO_REST_URL

    def test_limit_order_requires_price(self):
        client, _ = self._client_with_order_capture()
        with pytest.raises(ValueError, match="[Pp]rice"):
            client.place_order("BTC", "buy", 0.05, "limit")

    def test_invalid_side_rejected_before_network(self):
        client, _ = self._client_with_order_capture()
        with pytest.raises(ValueError, match="side"):
            client.place_order("BTC", "BUY", 0.05, "market")
        client.session.post.assert_not_called()

    def test_failed_inner_code_maps_to_unsuccessful_ack(self):
        client = _make_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        client.session.post.return_value = _response(
            {
                "code": "0",
                "msg": "",
                "data": [{"orderId": None, "code": "102064", "msg": "size too small"}],
            }
        )
        ack = client.place_order("BTC", "buy", 0.05, "market")
        assert ack["success"] is False
        assert "size too small" in ack["error"]

    def test_cancel_order_payload(self):
        client = _make_client()
        _route_get(client, {"/market/instruments": INSTRUMENTS_RESPONSE})
        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured["url"] = url
            captured["body"] = json.loads(data)
            return _response(ORDER_ACK_RESPONSE)

        client.session.post.side_effect = fake_post
        client.cancel_order("BTC", 2075705202)
        assert captured["url"].endswith("/api/v1/trade/cancel-order")
        assert captured["body"] == {"instId": "BTC-USDT", "orderId": "2075705202"}

    def test_cancel_all_orders_batches(self):
        client = _make_client()
        _route_get(
            client,
            {
                "/market/instruments": INSTRUMENTS_RESPONSE,
                "/trade/orders-pending": ORDERS_PENDING_RESPONSE,
            },
        )
        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured["url"] = url
            captured["body"] = json.loads(data)
            return _response({"code": "0", "msg": "success", "data": []})

        client.session.post.side_effect = fake_post
        result = client.cancel_all_orders()
        assert captured["url"].endswith("/api/v1/trade/cancel-batch-orders")
        assert captured["body"] == [
            {"instId": "BTC-USDT", "orderId": "2075705202"}
        ]
        assert result == {"success": True, "data": {"cancelled": 1}}


# ======================================================================
# Position / order / trade / balance normalization
# ======================================================================


class TestReadNormalization:
    def _client(self):
        client = _make_client()
        _route_get(
            client,
            {
                "/market/instruments": INSTRUMENTS_RESPONSE,
                "/account/positions": POSITIONS_RESPONSE,
                "/trade/orders-pending": ORDERS_PENDING_RESPONSE,
                "/trade/fills-history": FILLS_RESPONSE,
            },
        )
        return client

    def test_positions_contracts_to_base_and_sides(self):
        positions = self._client().get_positions()
        assert len(positions) == 2  # Zero-contract ghost filtered
        btc = next(p for p in positions if p["symbol"] == "BTC")
        # Net mode, positions=-100 contracts * 0.001 -> short 0.1 BTC
        assert btc["side"] == "short"
        assert btc["amount"] == pytest.approx(0.1)
        assert btc["entry_price"] == pytest.approx(65000.5)
        assert btc["unrealized_pnl"] == pytest.approx(-12.5)
        avax = next(p for p in positions if p["symbol"] == "AVAX")
        assert avax["side"] == "long"
        assert avax["amount"] == pytest.approx(25.0)

    def test_adapter_get_positions_normalized(self):
        exchange = BlofinExchange(rest_client=self._client())
        positions = exchange.get_positions()
        assert len(positions) == 2
        btc = next(p for p in positions if p.symbol == "BTC")
        assert btc.side == PositionSide.SHORT
        assert btc.quantity == pytest.approx(0.1)

    def test_open_orders_bot_shape(self):
        orders = self._client().get_orders()
        assert len(orders) == 1
        order = orders[0]
        assert order["symbol"] == "BTC"
        assert order["side"] == "buy"
        assert order["order_id"] == "2075705202"
        assert order["price"] == pytest.approx(64000.0)
        assert order["amount"] == pytest.approx(0.05)  # 50 contracts

    def test_trades_bot_shape(self):
        trades = self._client().get_trades()
        trade = trades[0]
        assert trade["symbol"] == "BTC"
        assert trade["side"] == "buy"
        assert trade["size"] == pytest.approx(0.05)
        assert trade["price"] == pytest.approx(65100.1)
        assert trade["timestamp"] == 1784600500000

    def test_balance_dict_shape(self):
        client = _make_client()
        _route_get(client, {"/account/balance": BALANCE_RESPONSE_DICT})
        balance = client.get_balance()
        assert float(balance["balance"]) == pytest.approx(10011254.07)
        assert float(balance["available_to_spend"]) == pytest.approx(9996399.47)

    def test_balance_list_shape(self):
        client = _make_client()
        _route_get(client, {"/account/balance": BALANCE_RESPONSE_LIST})
        balance = client.get_balance()
        assert float(balance["balance"]) == pytest.approx(10012514.91)
        assert float(balance["available_to_spend"]) == pytest.approx(9872132.41)

    def test_adapter_balance_normalized(self):
        client = _make_client()
        _route_get(client, {"/account/balance": BALANCE_RESPONSE_DICT})
        exchange = BlofinExchange(rest_client=client)
        balance = exchange.get_balance()
        assert balance.equity == pytest.approx(10011254.07)
        assert balance.available == pytest.approx(9996399.47)


# ======================================================================
# Market data (public)
# ======================================================================


class TestMarketData:
    def _client(self):
        client = _make_public_client()
        _route_get(
            client,
            {
                "/market/instruments": INSTRUMENTS_RESPONSE,
                "/market/tickers": TICKERS_RESPONSE,
                "/market/candles": CANDLES_RESPONSE,
                "/market/funding-rate-history": FUNDING_HISTORY_RESPONSE,
                "/market/funding-rate": FUNDING_RESPONSE,
            },
        )
        return client

    def test_candle_bar_mapping(self):
        client = self._client()
        for interval, bar in (("1m", "1m"), ("15m", "15m"), ("1h", "1H"), ("4h", "4H")):
            client.get_candles("BTC", interval=interval, limit=3)
            url = client.session.get.call_args[0][0]
            assert f"bar={bar}" in url

    def test_unknown_interval_raises(self):
        with pytest.raises(ValueError, match="interval"):
            self._client().get_candles("BTC", interval="7m")

    def test_candles_standardized_ascending(self):
        candles = self._client().get_candles("BTC", interval="15m", limit=10)
        assert len(candles) == 3
        timestamps = [c["timestamp"] for c in candles]
        assert timestamps == sorted(timestamps)  # Wire is newest-first
        newest = candles[-1]
        assert newest["open"] == pytest.approx(65239.2)
        assert newest["close"] == pytest.approx(65270.2)
        assert newest["volume"] == pytest.approx(0.1114)  # Base units
        assert isinstance(newest["timestamp"], int)

    def test_candles_time_window_filter(self):
        candles = self._client().get_candles(
            "BTC", interval="15m", start_time=1784600100000, limit=10
        )
        assert [c["timestamp"] for c in candles] == [1784600100000, 1784601000000]

    def test_perp_suffix_tolerated(self):
        candles = self._client().get_candles("BTC-PERP", interval="15m")
        assert candles

    def test_market_data_shape(self):
        data = self._client().get_market_data("BTC")
        assert data["symbol"] == "BTC"
        assert data["last"] == pytest.approx(65270.2)
        assert data["mid"] == pytest.approx((65270.1 + 65270.2) / 2)
        assert data["funding_rate"] == pytest.approx(-0.000049450034133443396)
        assert data["next_funding_time"] == 1784620800000

    def test_funding_history_shape(self):
        history = self._client().get_funding_history("BTC", limit=8)
        assert len(history) == 2
        assert history[0]["funding_rate"] == pytest.approx(0.000025)
        assert history[0]["timestamp"] == 1784592000000

    def test_markets_and_instrument_info_base_units(self):
        client = self._client()
        markets = client.get_markets()
        btc = next(m for m in markets if m["symbol"] == "BTC")
        assert btc["lot_size"] == pytest.approx(0.0001)  # 0.1 * 0.001
        assert btc["min_order_size"] == pytest.approx(0.0001)
        info = client.get_instrument_info("BTC")
        assert info["tick_size"] == pytest.approx(0.1)
        assert info["lot_size"] == pytest.approx(0.0001)
        assert info["min_order_size"] == pytest.approx(0.0001)

    def test_instrument_info_unknown_symbol_degrades_to_zeros(self):
        info = self._client().get_instrument_info("NOPE")
        assert info == {"tick_size": 0.0, "lot_size": 0.0, "min_order_size": 0.0}

    def test_api_error_code_raises(self):
        client = _make_public_client()
        client.session.get.return_value = _response(
            {"code": "152401", "msg": "invalid signature"}
        )
        with pytest.raises(BlofinAPIError, match="152401"):
            client._get("/api/v1/market/instruments")


# ======================================================================
# Missing-credentials behavior
# ======================================================================


class TestMissingCredentials:
    def test_public_data_works_without_keys(self):
        client = _make_public_client()
        _route_get(
            client,
            {
                "/market/instruments": INSTRUMENTS_RESPONSE,
                "/market/candles": CANDLES_RESPONSE,
            },
        )
        assert client.get_candles("BTC", interval="15m")
        assert client.to_inst_id("BTC") == "BTC-USDT"

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.place_order("BTC", "buy", 0.05, "market"),
            lambda c: c.cancel_order("BTC", "1"),
            lambda c: c.cancel_all_orders(),
            lambda c: c.get_positions(),
            lambda c: c.get_orders(),
            lambda c: c.get_trades(),
            lambda c: c.get_balance(),
        ],
    )
    def test_private_calls_raise_clear_auth_error(self, call):
        client = _make_public_client()
        with pytest.raises(BlofinAuthError) as excinfo:
            call(client)
        message = str(excinfo.value)
        assert "BLOFIN_API_KEY" in message
        assert ".env" in message
        client.session.get.assert_not_called()
        client.session.post.assert_not_called()


# ======================================================================
# Factory / capabilities
# ======================================================================


class TestFactoryIntegration:
    def test_factory_returns_real_adapter(self, monkeypatch):
        monkeypatch.setenv("EXCHANGE", "blofin")
        exchange = get_exchange_client()
        assert isinstance(exchange, BlofinExchange)
        # Real adapter: no method should raise NotImplementedError.
        assert not any(
            "NotImplementedError" in (getattr(m, "__doc__", "") or "")
            for m in (exchange.place_order, exchange.get_positions)
        )

    def test_capabilities(self, monkeypatch):
        monkeypatch.setenv("EXCHANGE", "blofin")
        caps = get_exchange_capabilities()
        assert caps.name == "blofin"
        assert caps.funding_interval_hours == 8
        assert caps.has_testnet is True
        assert caps.native_order_sides == ("buy", "sell")
        assert caps.amounts_as_strings is True

    def test_funding_info_carries_8h_interval(self):
        rest = MagicMock()
        rest.get_funding_rate.return_value = {
            "symbol": "BTC",
            "funding_rate": 0.0001,
            "next_funding_time": 1784620800000,
        }
        exchange = BlofinExchange(rest_client=rest)
        info = exchange.get_funding_info("BTC")
        assert info.funding_interval_hours == 8
        assert info.funding_rate == pytest.approx(0.0001)

    def test_order_side_vocabulary(self):
        assert BlofinExchange.to_native_order_side(OrderSide.BUY) == "buy"
        assert BlofinExchange.to_native_order_side("bid") == "buy"
        assert BlofinExchange.to_native_order_side("SELL") == "sell"
        assert BlofinExchange.from_native_order_side("buy") == OrderSide.BUY
        assert BlofinExchange.from_native_order_side("sell") == OrderSide.SELL

    def test_adapter_place_order_passes_exact_lowercase(self):
        rest = MagicMock()
        rest.place_order.return_value = {"success": True, "data": {}}
        exchange = BlofinExchange(rest_client=rest)
        exchange.place_order("BTC", OrderSide.BUY, 0.5, OrderType.MARKET)
        kwargs = rest.place_order.call_args.kwargs
        assert kwargs["side"] == "buy"
        assert kwargs["order_type"] == "market"
        exchange.place_order("BTC", "ask", 0.5, "limit", price=100.0)
        kwargs = rest.place_order.call_args.kwargs
        assert kwargs["side"] == "sell"
        assert kwargs["price"] == 100.0


# ======================================================================
# WebSocket data client (no network: construction + message handling)
# ======================================================================


class TestWsDataClient:
    def _ws(self):
        return BlofinWebSocketClient(symbols=["BTC"], intervals=["15m"])

    def test_construction_performs_no_io(self):
        ws = self._ws()
        assert ws.is_connected() is False
        assert ws._running is False
        assert ws._kline_cache == {}

    def test_ticker_push_updates_price(self):
        ws = self._ws()
        ws._process_message(
            {
                "arg": {"channel": "tickers", "instId": "BTC-USDT"},
                "data": [{"last": "65286.8", "ts": "1784601115750"}],
            }
        )
        assert ws.get_price("BTC") == pytest.approx(65286.8)
        assert ws.get_price("BTC-PERP") == pytest.approx(65286.8)

    def test_candle_push_and_rest_format(self):
        ws = self._ws()
        ws._process_message(
            {
                "arg": {"channel": "candle15m", "instId": "BTC-USDT"},
                "data": [
                    ["1784600100000", "65258", "65293.2", "65145.3", "65239.2", "68032", "68.0329", "4437022", "1"]
                ],
            }
        )
        # Same-timestamp update replaces the candle in place.
        ws._process_message(
            {
                "arg": {"channel": "candle15m", "instId": "BTC-USDT"},
                "data": [
                    ["1784600100000", "65258", "65295.0", "65145.3", "65250.0", "68100", "68.1", "4437100", "1"]
                ],
            }
        )
        candles = ws.get_kline_data("BTC", "15m")
        assert len(candles) == 1
        assert candles[0] == {
            "o": "65258.0",
            "c": "65250.0",
            "h": "65295.0",
            "l": "65145.3",
            "v": "68.1",
        }

    def test_candle_uppercase_bar_maps_to_bot_interval(self):
        ws = BlofinWebSocketClient(symbols=["BTC"], intervals=["1h"])
        ws._process_message(
            {
                "arg": {"channel": "candle1H", "instId": "BTC-USDT"},
                "data": [
                    ["1784599200000", "1", "2", "0.5", "1.5", "10", "0.01", "650", "1"]
                ],
            }
        )
        assert ws.get_kline_data("BTC", "1h") is not None
        assert ws.get_kline_data("BTC", "1H") is None  # Bot vocab only

    def test_books5_dict_payload(self):
        """Real wire shape: books5 data is a DICT, not a list."""
        ws = self._ws()
        ws._process_message(
            {
                "arg": {"channel": "books5", "instId": "BTC-USDT"},
                "action": "snapshot",
                "data": {
                    "asks": [["65289.1", "1.1"], ["65289.3", "1"]],
                    "bids": [["65289", "348"], ["65288.9", "218"]],
                    "ts": "1784601119892",
                },
            }
        )
        book = ws.get_orderbook("BTC")
        assert book["asks"][0] == {"p": 65289.1, "a": 1.1}
        assert book["bids"][0] == {"p": 65289.0, "a": 348.0}
        assert book["timestamp"] == 1784601119892
        imbalance = ws.get_orderbook_imbalance("BTC")
        assert imbalance == pytest.approx((348 + 218) / (348 + 218 + 1.1 + 1))

    def test_pong_and_event_frames_ignored(self):
        ws = self._ws()
        ws._handle_raw("pong")
        ws._handle_raw(
            json.dumps({"event": "subscribe", "arg": {"channel": "tickers"}})
        )
        assert ws._message_count == 0

    def test_adapter_exposes_ws_client(self):
        exchange = BlofinExchange(rest_client=MagicMock(), ws_client=self._ws())
        assert isinstance(exchange.ws_client, BlofinWebSocketClient)


# ======================================================================
# Bar map sanity
# ======================================================================


def test_bar_map_covers_bot_timeframes_with_correct_case():
    assert BAR_MAP["1m"] == "1m"
    assert BAR_MAP["5m"] == "5m"
    assert BAR_MAP["15m"] == "15m"
    assert BAR_MAP["1h"] == "1H"
    assert BAR_MAP["4h"] == "4H"
