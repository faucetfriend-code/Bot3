"""T2: exits are reduce-only and sized from the exchange's current position.

The 2026-09-08 live-readiness audit showed every close path sending the
locally remembered quantity as an ordinary opposite-side order, with no
``reduceOnly`` on the Blofin wire body (probe "T2: shared Blofin order
path emits neither reduceOnly nor attached stop").  If the exchange stop
or a manual action had already reduced the position, that order opened
reverse exposure.  These tests pin the desired behaviour:

* entries send ``reduceOnly="false"``, exits send ``reduceOnly="true"``
  (Blofin body) and ``reduce_only=True`` (Pacifica payload);
* a close of an already-flat position sends NO order;
* a close after a partial external reduction sends only the remainder.

No network: the Blofin HTTP session is a fake that records the outgoing
JSON body; the position manager gets an in-memory fake client.
"""

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.blofin_client import BlofinClient
from trading_bot_v2.exchanges.blofin import BlofinExchange
from trading_bot_v2.exchanges.pacifica import PacificaExchange
from trading_bot_v2.exit_sizing import plan_close_quantity, remaining_exchange_quantity
from trading_bot_v2.migrated_position_manager import MigratedPositionManager
from trading_bot_v2.models import OrderSide, OrderType
from trading_bot_v2.pacifica_client import PacificaClient
from trading_bot_v2.trading_bot import TradingBot

INSTRUMENTS = {
    "code": "0",
    "data": [
        {
            "instId": "BTC-USDT",
            "baseCurrency": "BTC",
            "quoteCurrency": "USDT",
            "instType": "SWAP",
            "contractValue": "0.001",
            "lotSize": "1",
            "minSize": "1",
            "tickSize": "0.1",
        }
    ],
}

ORDER_ACK = {
    "code": "0",
    "msg": "",
    "data": [{"orderId": "9001", "clientOrderId": "", "code": "0", "msg": ""}],
}


def _response(payload: Dict[str, Any], status: int = 200) -> MagicMock:
    """Build a requests-like response mock."""
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = payload
    mock.text = json.dumps(payload)
    return mock


def _blofin_with_capture(routes: Optional[Dict[str, Any]] = None):
    """Return (client, sent_bodies) with a fake HTTP session.

    Args:
        routes: Optional GET url-fragment -> payload overrides.

    Returns:
        Tuple of the client and the list of JSON bodies POSTed.
    """
    client = BlofinClient(api_key="k", api_secret="s", passphrase="p", demo=True)
    client.session = MagicMock()
    client._position_mode_checked = True
    get_routes = {"/market/instruments": INSTRUMENTS}
    get_routes.update(routes or {})
    sent: List[Dict[str, Any]] = []

    def fake_get(url, headers=None, timeout=None):
        for fragment, payload in get_routes.items():
            if fragment in url:
                return _response(payload)
        raise AssertionError(f"Unexpected GET url: {url}")

    def fake_post(url, headers=None, data=None, timeout=None):
        sent.append(json.loads(data))
        return _response(ORDER_ACK)

    client.session.get.side_effect = fake_get
    client.session.post.side_effect = fake_post
    return client, sent


class FakeClient:
    """Native-client stand-in: records orders, serves configurable positions.

    ``positions`` is a list of lists consumed one ``get_positions`` call
    at a time (the last one repeats), so a test can script "before the
    order" and "after the order" snapshots.
    """

    def __init__(self, positions: List[List[Dict[str, Any]]], ack=None):
        self._positions = list(positions)
        self.calls: List[Dict[str, Any]] = []
        self.ack = ack or {"success": True, "data": {"order_id": "77"}}

    def get_positions(self) -> List[Dict[str, Any]]:
        """Return the next scripted snapshot (repeats the last one)."""
        if len(self._positions) > 1:
            return self._positions.pop(0)
        return self._positions[0] if self._positions else []

    def place_order(self, symbol, side, quantity, order_type, price=None, **kwargs):
        """Record the order and return the configured ack."""
        self.calls.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                "price": price,
                **kwargs,
            }
        )
        if isinstance(self.ack, Exception):
            raise self.ack
        return self.ack


class FakeRiskManager:
    """Minimal migrated-position registry."""

    def __init__(self, positions: List[Dict[str, Any]]):
        self.positions = [dict(p) for p in positions]
        self.unregistered: List[tuple] = []

    def get_migrated_positions(self, symbol=None):
        return [dict(p) for p in self.positions]

    def has_migrated_positions(self, symbol=None):
        return bool(self.positions)

    def unregister_migrated_position(self, symbol, side, qty=None):
        self.unregistered.append((symbol, side, qty))
        self.positions = [
            p
            for p in self.positions
            if not (p["symbol"] == symbol and p["side"] == side)
        ]
        return True


def _manager(client, risk) -> MigratedPositionManager:
    """Build a manager with DB writes stubbed."""
    manager = MigratedPositionManager(client, risk, regime_detector=None)
    manager._update_db_position = MagicMock()
    return manager


# ======================================================================
# Blofin wire body
# ======================================================================


class TestBlofinReduceOnlyBody:
    def test_entry_sends_reduce_only_false_string(self):
        client, sent = _blofin_with_capture()
        client.place_order("BTC", "buy", 0.01, "market")
        body = sent[0]
        assert body["reduceOnly"] == "false"
        assert body["positionSide"] == "net"
        assert body["side"] == "buy"
        assert body["size"] == "10"  # 0.01 BTC / 0.001 contractValue
        assert len(body["clientOrderId"]) <= 32

    def test_exit_sends_reduce_only_true_string(self):
        """Inverse of audit probe T2: the exit body carries reduceOnly."""
        client, sent = _blofin_with_capture()
        client.place_order("BTC", "sell", 0.01, "market", reduce_only=True)
        assert "reduceOnly" in sent[0]
        assert sent[0]["reduceOnly"] == "true"
        assert sent[0]["positionSide"] == "net"

    def test_caller_client_order_id_is_sent_verbatim(self):
        client, sent = _blofin_with_capture()
        client.place_order("BTC", "buy", 0.01, "market", client_order_id="abc123def456")
        assert sent[0]["clientOrderId"] == "abc123def456"

    def test_hedge_mode_targets_opposite_side_for_reduce_only(self):
        client, sent = _blofin_with_capture()
        client._position_mode = "long_short_mode"
        client.place_order("BTC", "buy", 0.01, "market")
        client.place_order("BTC", "sell", 0.01, "market", reduce_only=True)
        client.place_order("BTC", "buy", 0.01, "market", reduce_only=True)
        assert sent[0]["positionSide"] == "long"  # entry opens a long
        assert sent[1]["positionSide"] == "long"  # sell closes the long
        assert sent[2]["positionSide"] == "short"  # buy closes a short

    def test_net_mode_probe_records_mode(self):
        client, _ = _blofin_with_capture(
            {
                "/account/position-mode": {
                    "code": "0",
                    "data": {"positionMode": "long_short_mode"},
                }
            }
        )
        client._position_mode_checked = False
        client.session.post.side_effect = None
        client.session.post.return_value = _response({"code": "0", "data": {}})
        client.ensure_net_position_mode()
        assert client._position_mode == "net_mode"


class TestBlofinAdapterForwarding:
    def test_adapter_forwards_reduce_only_only_when_set(self):
        rest = MagicMock()
        rest.place_order.return_value = {"success": True, "data": {"order_id": "1"}}
        exchange = BlofinExchange(rest_client=rest)
        exchange.place_order("BTC", OrderSide.BUY, 0.5, OrderType.MARKET)
        assert "reduce_only" not in rest.place_order.call_args.kwargs
        exchange.place_order(
            "BTC", OrderSide.SELL, 0.5, OrderType.MARKET, reduce_only=True
        )
        assert rest.place_order.call_args.kwargs["reduce_only"] is True

    def test_place_order_result_normalizes_ack(self):
        rest = MagicMock()
        rest.place_order.return_value = {"success": True, "data": {"order_id": "42"}}
        exchange = BlofinExchange(rest_client=rest)
        result = exchange.place_order_result(
            "BTC", OrderSide.SELL, 0.5, reduce_only=True, client_order_id="cid1"
        )
        assert result.accepted is True
        assert result.order_id == "42"
        assert result.client_order_id == "cid1"


# ======================================================================
# Pacifica payload
# ======================================================================


class TestPacificaReduceOnly:
    def _client(self):
        client = PacificaClient.__new__(PacificaClient)
        client._make_signed_request = MagicMock(
            return_value={"success": True, "data": {"order_id": "5"}}
        )
        return client

    def test_entry_payload_reduce_only_false(self):
        client = self._client()
        client.place_order("BTC", "buy", 1.0, "market")
        payload = client._make_signed_request.call_args[0][1]
        assert payload["reduce_only"] is False
        assert payload["side"] == "bid"
        assert payload["amount"] == "1.0"

    def test_exit_payload_reduce_only_true(self):
        client = self._client()
        client.place_order(
            "BTC", "sell", 1.0, "market", reduce_only=True, client_order_id="cid-x"
        )
        payload = client._make_signed_request.call_args[0][1]
        assert payload["reduce_only"] is True
        assert payload["side"] == "ask"
        assert payload["client_order_id"] == "cid-x"

    def test_adapter_forwards_reduce_only_on_exits_only(self):
        rest = MagicMock()
        rest.place_order.return_value = {"success": True, "data": {"order_id": "1"}}
        exchange = PacificaExchange(rest_client=rest)
        exchange.place_order("BTC", OrderSide.BUY, 1.0, OrderType.MARKET)
        rest.place_order.assert_called_once_with(
            symbol="BTC", side="buy", quantity=1.0, order_type="market"
        )
        rest.reset_mock()
        exchange.place_order(
            "BTC", OrderSide.SELL, 1.0, OrderType.MARKET, reduce_only=True
        )
        assert rest.place_order.call_args.kwargs["reduce_only"] is True
        assert rest.place_order.call_args.kwargs["side"] == "sell"


# ======================================================================
# Exit sizing helper
# ======================================================================


class TestExitSizing:
    def test_remaining_quantity_sums_matching_side_only(self):
        client = FakeClient(
            [
                [
                    {"symbol": "BTC", "side": "long", "amount": "1.5"},
                    {"symbol": "BTC", "side": "short", "amount": "0.2"},
                    {"symbol": "ETH", "side": "long", "amount": "9"},
                ]
            ]
        )
        assert remaining_exchange_quantity(client, "BTC", "long") == pytest.approx(1.5)
        assert remaining_exchange_quantity(client, "BTC", "SHORT") == pytest.approx(0.2)
        assert remaining_exchange_quantity(client, "SOL", "long") == 0.0

    def test_unknown_when_fetch_fails_or_unparseable(self):
        failing = MagicMock()
        failing.get_positions.side_effect = TimeoutError("probe")
        assert remaining_exchange_quantity(failing, "BTC", "long") is None
        bare = MagicMock()  # get_positions returns a MagicMock, not a list
        assert remaining_exchange_quantity(bare, "BTC", "long") is None
        plan = plan_close_quantity(bare, "BTC", "long", 2.0)
        assert plan.quantity == 2.0 and plan.exchange_quantity is None
        assert plan.already_flat is False

    def test_plan_clamps_and_detects_flat(self):
        client = FakeClient([[{"symbol": "BTC", "side": "long", "amount": "0.5"}]])
        plan = plan_close_quantity(client, "BTC", "long", 2.0)
        assert plan.quantity == pytest.approx(0.5) and plan.clamped is True
        flat = plan_close_quantity(FakeClient([[]]), "BTC", "long", 2.0)
        assert flat.already_flat is True and flat.quantity == 0.0

    def test_accepts_adapter_dataclass_positions(self):
        exchange = PacificaExchange(
            rest_client=FakeClient([[{"symbol": "BTC", "side": "long", "amount": "3"}]])
        )
        assert remaining_exchange_quantity(exchange, "BTC", "long") == pytest.approx(
            3.0
        )


# ======================================================================
# MigratedPositionManager exits
# ======================================================================


class TestMigratedManagerExits:
    def test_full_close_is_reduce_only_and_sized_from_exchange(self):
        client = FakeClient([[{"symbol": "BTC", "side": "long", "amount": "2"}], []])
        risk = FakeRiskManager(
            [{"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}]
        )
        manager = _manager(client, risk)

        manager.close_position("BTC", risk.positions[0], "trailing_stop")

        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["side"] == "sell"
        assert call["reduce_only"] is True
        assert call["quantity"] == pytest.approx(2.0)
        assert risk.unregistered == [("BTC", "long", 2.0)]
        manager._update_db_position.assert_called_once()
        assert manager.get_pending_closes() == {}

    def test_close_of_already_flat_position_sends_no_order(self):
        client = FakeClient([[]])
        risk = FakeRiskManager(
            [{"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}]
        )
        manager = _manager(client, risk)

        manager.close_position("BTC", risk.positions[0], "trend_reversal")

        assert client.calls == []  # no order -> no reverse exposure
        assert risk.unregistered == [("BTC", "long", 2.0)]  # local state reconciled
        reason = manager._update_db_position.call_args[0][3]
        assert "already_flat" in reason

    def test_close_after_partial_external_reduction_sends_remaining(self):
        client = FakeClient([[{"symbol": "BTC", "side": "long", "amount": "0.5"}], []])
        risk = FakeRiskManager(
            [{"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}]
        )
        manager = _manager(client, risk)

        manager.close_position("BTC", risk.positions[0], "time_exit")

        assert len(client.calls) == 1
        assert client.calls[0]["quantity"] == pytest.approx(0.5)
        assert client.calls[0]["reduce_only"] is True
        assert risk.unregistered == [("BTC", "long", 2.0)]

    def test_short_close_uses_buy_reduce_only(self):
        client = FakeClient([[{"symbol": "ETH", "side": "short", "amount": "4"}], []])
        risk = FakeRiskManager(
            [{"symbol": "ETH", "side": "short", "qty": 4.0, "entry_price": 10.0}]
        )
        manager = _manager(client, risk)
        manager.close_position("ETH", risk.positions[0], "manual")
        assert client.calls[0]["side"] == "buy"
        assert client.calls[0]["reduce_only"] is True

    def test_partial_take_profit_is_reduce_only_and_clamped(self):
        # Exchange only holds 0.6 of the local 2.0 -> the 1.0 partial is
        # clamped to 0.6, then the exchange shows it filled (flat after).
        client = FakeClient([[{"symbol": "BTC", "side": "long", "amount": "0.6"}], []])
        risk = FakeRiskManager(
            [{"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}]
        )
        manager = _manager(client, risk)
        manager._take_profits["BTC"] = {
            "long": {"target": 105.0, "partial_taken": False}
        }

        taken = manager._check_take_profit(
            "BTC", risk.positions[0], current_price=106.0
        )

        assert taken is True
        assert client.calls[0]["reduce_only"] is True
        assert client.calls[0]["quantity"] == pytest.approx(0.6)
        assert manager._take_profits["BTC"]["long"]["partial_taken"] is True

    def test_partial_take_profit_on_flat_position_sends_nothing(self):
        client = FakeClient([[]])
        risk = FakeRiskManager(
            [{"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}]
        )
        manager = _manager(client, risk)
        manager._take_profits["BTC"] = {
            "long": {"target": 105.0, "partial_taken": False}
        }

        taken = manager._check_take_profit(
            "BTC", risk.positions[0], current_price=106.0
        )

        assert taken is False
        assert client.calls == []
        assert manager._take_profits["BTC"]["long"]["partial_taken"] is False


# ======================================================================
# TradingBot emergency close
# ======================================================================


def _bot_stand_in(client) -> SimpleNamespace:
    """Duck-typed bot carrying just what _emergency_close_position needs."""
    bot = SimpleNamespace(client=client, exchange=PacificaExchange(rest_client=client))
    bot._emergency_close_position = TradingBot._emergency_close_position.__get__(bot)
    return bot


class TestEmergencyClose:
    def test_emergency_close_is_reduce_only_and_clamped(self):
        client = FakeClient([[{"symbol": "BTC", "side": "long", "amount": "1.5"}]])
        bot = _bot_stand_in(client)
        bot._emergency_close_position("BTC", "LONG", 2.0)
        assert len(client.calls) == 1
        assert client.calls[0]["side"] == "sell"
        assert client.calls[0]["quantity"] == pytest.approx(1.5)
        assert client.calls[0]["reduce_only"] is True

    def test_emergency_close_of_flat_position_sends_no_order(self):
        client = FakeClient([[]])
        bot = _bot_stand_in(client)
        bot._emergency_close_position("BTC", "LONG", 2.0)
        assert client.calls == []

    def test_emergency_close_short_uses_buy(self):
        client = FakeClient([[{"symbol": "BTC", "side": "short", "amount": "1"}]])
        bot = _bot_stand_in(client)
        bot._emergency_close_position("BTC", "SHORT", 1.0)
        assert client.calls[0]["side"] == "buy"
        assert client.calls[0]["reduce_only"] is True

    def test_emergency_close_unknown_exchange_state_still_reduce_only(self):
        # Position fetch fails -> unknown -> local qty, but reduce-only so the
        # exchange enforces the clamp instead of opening reverse exposure.
        client = FakeClient([[]])
        client.get_positions = MagicMock(side_effect=TimeoutError("probe"))
        bot = _bot_stand_in(client)
        bot._emergency_close_position("BTC", "LONG", 2.0)
        assert client.calls[0]["quantity"] == pytest.approx(2.0)
        assert client.calls[0]["reduce_only"] is True
