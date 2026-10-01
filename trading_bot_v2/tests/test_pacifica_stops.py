"""Offline Pacifica protection contract tests; every request/signature is mocked."""

from unittest.mock import Mock, patch
import uuid

import pytest
import requests
import requests_mock

from trading_bot_v2.exchanges.base import PositionSide
from trading_bot_v2.exchanges.pacifica import PacificaExchange
from trading_bot_v2.pacifica_client import PacificaClient
from trading_bot_v2.venue_stops import find_stop_row

URL = "https://test-api.pacifica.fi/api/v1"


@pytest.fixture(autouse=True)
def stop_journal(tmp_path, monkeypatch):
    """Each test uses a disposable durable journal, never the real bot database."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stop-journal.db"))
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")


@pytest.fixture
def client():
    with (
        patch("trading_bot_v2.pacifica_client.Keypair") as keys,
        patch(
            "trading_bot_v2.pacifica_client.sign_message",
            return_value=("mock-message", "mock-signature"),
        ),
    ):
        keys.from_base58_string.return_value.pubkey.return_value = "mock-agent"
        yield PacificaClient("mock-key", "mock-account", testnet=True)


@pytest.fixture
def rest():
    client = Mock(spec=PacificaClient)
    client.account_public_key = "mock-account"
    client.prepare_stop.side_effect = lambda symbol, side, price: (symbol, str(price))
    client.get_protection_orders.return_value = []
    client.get_protection_positions.return_value = [
        {"symbol": "BTC", "side": "bid", "amount": "1"}
    ]
    client.create_protective_stop.return_value = {"order_id": 123}
    return client


def stop_row(order_id=123, side="ask", price="95", **extra):
    return {
        "order_id": order_id,
        "symbol": "BTC",
        "side": side,
        "order_type": "stop_loss_market",
        "stop_price": price,
        "reduce_only": True,
        **extra,
    }


@pytest.mark.parametrize("side,expected", [("buy", "95.1"), ("sell", "95.0")])
def test_trigger_precision_and_symbol_case(client, side, expected):
    with requests_mock.Mocker() as http:
        http.get(
            URL + "/info",
            json={"success": True, "data": [{"symbol": "kBONK", "tick_size": "0.1"}]},
        )
        assert client.prepare_stop("KBONK-PERP", side, 95.04) == ("kBONK", expected)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_invalid_stop_never_submits(client, bad):
    client.get_markets = Mock(return_value=[{"symbol": "BTC", "tick_size": "0.1"}])
    client.session.post = Mock()
    with pytest.raises(ValueError):
        client.place_order("BTC", "buy", 1, "market", stop_loss=bad)
    client.session.post.assert_not_called()


@pytest.mark.parametrize(
    "kind,path", [("market", "/orders/create_market"), ("limit", "/orders/create")]
)
def test_attached_stop_wire_and_agent_auth(client, kind, path):
    client.get_markets = Mock(return_value=[{"symbol": "BTC", "tick_size": "0.1"}])
    cid = str(uuid.uuid4())
    with requests_mock.Mocker() as http:
        http.post(URL + path, json={"order_id": 12})
        ack = client.place_order(
            "BTC", "buy", 1, kind, price=100, client_order_id=cid, stop_loss=95.04
        )
        body = http.last_request.json()
        assert body["stop_loss"]["stop_price"] == "95.1"
        assert "limit_price" not in body["stop_loss"]
        assert "amount" not in body["stop_loss"]
        assert body["stop_loss"]["trigger_price_type"] == "last_trade_price"
        assert body["agent_wallet"] == "mock-agent"
        assert body["account"] == "mock-account"
        assert ack == {"success": True, "data": {"order_id": 12}}


def test_attached_entry_timeout_is_not_replayed(client):
    client.get_markets = Mock(return_value=[{"symbol": "BTC", "tick_size": "1"}])
    with requests_mock.Mocker() as http:
        route = http.post(URL + "/orders/create_market", exc=requests.Timeout)
        with pytest.raises(requests.Timeout):
            client.place_order("BTC", "buy", 1, "market", stop_loss=95)
        assert route.call_count == 1


def test_reduce_only_entry_cannot_attach_stop(client):
    with pytest.raises(ValueError):
        client.place_order("BTC", "sell", 1, "market", reduce_only=True, stop_loss=95)


def test_standalone_full_position_market_stop_wire(client):
    cid = str(uuid.uuid4())
    with requests_mock.Mocker() as http:
        http.post(URL + "/orders/stop/create", json={"order_id": 123})
        client.create_protective_stop("BTC", "ask", "95.1", cid)
        body = http.last_request.json()
        assert body["side"] == "ask" and body["reduce_only"] is True
        assert body["stop_order"] == {
            "stop_price": "95.1",
            "client_order_id": cid,
            "trigger_price_type": "last_trade_price",
        }


@pytest.mark.parametrize(
    "side,wire", [(PositionSide.LONG, "ask"), (PositionSide.SHORT, "bid")]
)
def test_install_close_side_and_real_id(rest, side, wire):
    result = PacificaExchange(rest).install_stop("BTC", side, 1, 95)
    assert result.accepted and result.order_id == "123"
    assert rest.create_protective_stop.call_args.args[1] == wire


@pytest.mark.parametrize(
    "ack",
    [
        {"success": True},
        {"success": True, "data": {}},
        {"success": True, "status": "success"},
        "success",
        {"order_id": 0},
        {"order_id": "oops"},
    ],
)
def test_missing_or_invalid_id_is_unknown(rest, ack):
    rest.create_protective_stop.return_value = ack
    result = PacificaExchange(rest).install_stop("BTC", "long", 1, 95)
    assert not result.accepted and result.status == "unknown"


def test_timeout_reconciles_exact_client_id_without_resubmit(rest):
    rows = []
    rest.get_protection_orders.side_effect = lambda: rows

    def submit(symbol, side, trigger, cid):
        rows.append(stop_row(client_order_id=cid))
        raise requests.Timeout()

    rest.create_protective_stop.side_effect = submit
    exchange = PacificaExchange(rest)
    assert exchange.install_stop("BTC", "long", 1, 95).order_id == "123"
    assert exchange.install_stop("BTC", "long", 1, 95).accepted
    assert rest.create_protective_stop.call_count == 1


def test_unresolved_timeout_blocks_repeat_submission(rest):
    rest.create_protective_stop.side_effect = requests.Timeout()
    exchange = PacificaExchange(rest)
    first = exchange.install_stop("BTC", "long", 1, 95)
    second = exchange.install_stop("BTC", "long", 1, 95)
    assert not first.accepted and not second.accepted
    assert first.client_order_id == second.client_order_id
    assert rest.create_protective_stop.call_count == 1


def test_recovery_filters_take_profit_nonreduce_and_partial_stops(rest):
    rest.get_protection_orders.return_value = [
        stop_row(1, order_type="take_profit_market"),
        stop_row(2, reduce_only=False),
        stop_row(3, initial_amount="0.5"),
        stop_row(4, initial_amount="1"),
    ]
    rows = PacificaExchange(rest).list_stops("BTC")
    assert [r["tpsl_id"] for r in rows] == ["4"]
    assert find_stop_row(rows, "long")["sl_trigger_price"] == 95
    assert find_stop_row(rows, "short") is None


@pytest.mark.parametrize(
    "body",
    [
        {"success": False, "data": []},
        {"success": True, "data": None},
        {"success": True, "data": [None]},
        {},
    ],
)
def test_listing_errors_never_look_empty(client, body):
    with requests_mock.Mocker() as http:
        http.get(URL + "/orders", json=body)
        with pytest.raises(ValueError):
            client.get_protection_orders()


def test_failed_listing_does_not_install(rest):
    rest.get_protection_orders.side_effect = requests.ConnectionError()
    assert not PacificaExchange(rest).install_stop("BTC", "long", 1, 95).accepted
    rest.create_protective_stop.assert_not_called()


def test_amend_creates_and_verifies_before_cancel(rest):
    rows = [stop_row(1)]
    events = []
    rest.get_protection_orders.side_effect = lambda: list(rows)

    def submit(symbol, side, trigger, cid):
        events.append("create")
        rows.append(stop_row(2, price=trigger, client_order_id=cid))
        return {"order_id": 2}

    rest.create_protective_stop.side_effect = submit
    rest.cancel_protective_stop.side_effect = lambda *args: (
        events.append("cancel") or {"success": True}
    )
    result = PacificaExchange(rest).amend_stop("BTC", "long", 97, stop_id="1")
    assert result.accepted and result.order_id == "2"
    assert events == ["create", "cancel"]
    rest.cancel_protective_stop.assert_called_once_with("BTC", "1")


def test_amend_keeps_old_stop_when_new_not_visible(rest):
    rest.get_protection_orders.return_value = [stop_row(1)]
    result = PacificaExchange(rest).amend_stop("BTC", "long", 97, stop_id="1")
    assert not result.accepted
    rest.cancel_protective_stop.assert_not_called()


def test_cancel_stop_wire_and_default_cancel_all_preserves_protection(client):
    with requests_mock.Mocker() as http:
        http.post(URL + "/orders/stop/cancel", json={"success": True})
        assert PacificaExchange(client).cancel_stop("BTC", "123").accepted
        assert http.last_request.json()["order_id"] == 123
        http.post(URL + "/orders/cancel_all", json={"cancelled_count": 1})
        client.cancel_all_orders("kBONK")
        assert http.last_request.json()["exclude_reduce_only"] is True
        assert http.last_request.json()["symbol"] == "kBONK"
        client.cancel_all_orders("kBONK", include_stops=True)
        assert http.last_request.json()["exclude_reduce_only"] is False


def test_stop_timeout_wire_is_not_retried(client):
    with requests_mock.Mocker() as http:
        route = http.post(URL + "/orders/stop/create", exc=requests.Timeout)
        with pytest.raises(requests.Timeout):
            client.create_protective_stop("BTC", "ask", "95", str(uuid.uuid4()))
        assert route.call_count == 1


def test_cancel_id_alone_is_not_confirmation(rest):
    rest.cancel_protective_stop.return_value = {"order_id": 123}
    assert not PacificaExchange(rest).cancel_stop("BTC", "123").accepted


def test_restart_recovers_existing_stop_without_post(rest):
    rest.get_protection_orders.return_value = [stop_row(123, initial_amount="1")]
    recreated = PacificaExchange(rest)
    result = recreated.install_stop("BTC", "long", 1, 95)
    assert result.accepted and result.order_id == "123"
    rest.create_protective_stop.assert_not_called()


def test_restart_with_unresolved_post_and_empty_list_never_resubmits(rest):
    rest.create_protective_stop.side_effect = requests.Timeout()
    first = PacificaExchange(rest).install_stop("BTC", "long", 1, 95)
    fresh = PacificaExchange(rest).install_stop("BTC", "long", 1, 95)
    assert first.status == fresh.status == "unknown"
    assert first.client_order_id == fresh.client_order_id
    assert rest.create_protective_stop.call_count == 1


def test_journal_failure_blocks_post(rest, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing" / "journal.db"))
    assert not PacificaExchange(rest).install_stop("BTC", "long", 1, 95).accepted
    rest.create_protective_stop.assert_not_called()


@pytest.mark.parametrize("path", [":memory:", "", "   "])
def test_memory_journal_blocks_post(rest, monkeypatch, path):
    monkeypatch.setenv("DATABASE_PATH", path)
    assert not PacificaExchange(rest).install_stop("BTC", "long", 1, 95).accepted
    rest.create_protective_stop.assert_not_called()


def test_pending_attempt_blocks_different_trigger_after_restart(rest):
    rest.create_protective_stop.side_effect = requests.Timeout()
    first = PacificaExchange(rest).install_stop("BTC", "long", 1, 95)
    second = PacificaExchange(rest).install_stop("BTC", "long", 1, 97)
    assert first.client_order_id == second.client_order_id
    assert not second.accepted
    assert rest.create_protective_stop.call_count == 1
