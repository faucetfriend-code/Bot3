"""T4: exchange-side stop-loss protection.

The 2026-09-08 live-readiness audit found that ``signal.stop_loss`` sizes
every position (risk_manager) and is refined (execution_layer) but is
NEVER sent to an exchange; only migrated positions got a software
trailing stop, in memory, lost on restart.  These tests pin the desired
behaviour at the wire, adapter, entry-path, database and grid layers:

* Blofin entry body carries ``slTriggerPrice`` / ``slOrderPrice="-1"`` /
  ``slTriggerPriceType="last"`` when a stop is given, nothing otherwise;
* a standalone TP/SL order is reduce-only on the correct close side;
* the amend cascade (amend-order -> amend-tpsl -> cancel-tpsl + re-place)
  takes each branch and never raises;
* ``cancel_all_orders`` keeps TP/SL rows unless ``include_stops=True``;
* an entry that fills but cannot be protected is closed reduce-only under
  the default policy and kept (state ``missing``) under ``local``;
* the positions table round-trips the new columns, on a fresh database
  and on one created before the columns existed;
* adapters without venue-stop support fabricate no stop id;
* the orphan-grid heuristic never counts TP/SL rows.

No network: the Blofin HTTP session is a fake that records the outgoing
JSON body per endpoint path.
"""

import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

import trading_bot_v2.database as db_mod
from trading_bot_v2.blofin_client import BlofinClient
from trading_bot_v2.config import AssetClass, StrategyType
from trading_bot_v2.database import DatabaseManager, _ConnectionWrapper
from trading_bot_v2.exchanges.base import ExchangeCapabilities, ExchangeClient
from trading_bot_v2.exchanges.blofin import BlofinExchange
from trading_bot_v2.exchanges.pacifica import PacificaExchange
from trading_bot_v2.grid_lifecycle_manager import (
    GridLifecycleManager,
    is_protective_order,
)
from trading_bot_v2.models import OrderSide, OrderType, Signal
from trading_bot_v2.order_result import OrderResult
from trading_bot_v2.trading_bot import TradingBot
from trading_bot_v2.venue_stops import (
    STOP_STATE_ATTACHED,
    STOP_STATE_MISSING,
    STOP_STATE_STANDALONE,
    STOP_STATE_UNSUPPORTED,
)


class UnsupportedExchange(PacificaExchange):
    """Exercise generic fallback independently of Pacifica capabilities."""

    @classmethod
    def capabilities(cls):
        return replace(PacificaExchange.capabilities(), supports_venue_stops=False)

    install_stop = ExchangeClient.install_stop
    amend_stop = ExchangeClient.amend_stop
    cancel_stop = ExchangeClient.cancel_stop
    list_stops = ExchangeClient.list_stops


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

ORDER_PATH = "/api/v1/trade/order"
AMEND_ORDER = "/api/v1/trade/amend-order"
TPSL_ORDER = "/api/v1/trade/order-tpsl"
TPSL_AMEND = "/api/v1/trade/amend-tpsl"
TPSL_CANCEL = "/api/v1/trade/cancel-tpsl"
CANCEL_BATCH = "/api/v1/trade/cancel-batch-orders"

OK_ORDER = {
    "code": "0",
    "msg": "",
    "data": [{"orderId": "9001", "code": "0", "msg": ""}],
}
REJECT_TOP = {"code": "152401", "msg": "order not found", "data": []}
REJECT_ROW = {
    "code": "0",
    "msg": "",
    "data": [{"code": "152402", "msg": "state invalid"}],
}


def _ok_tpsl(tpsl_id: str) -> Dict[str, Any]:
    return {
        "code": "0",
        "msg": "",
        "data": [{"tpslId": tpsl_id, "code": "0", "msg": ""}],
    }


def _tpsl_row(
    tpsl_id: str = "t1", side: str = "sell", sl: str = "95"
) -> Dict[str, Any]:
    return {
        "tpslId": tpsl_id,
        "instId": "BTC-USDT",
        "positionSide": "net",
        "side": side,
        "size": "10",
        "slTriggerPrice": sl,
        "slOrderPrice": "-1",
        "state": "live",
    }


def _response(payload: Dict[str, Any], status: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = payload
    mock.text = json.dumps(payload)
    return mock


def _blofin(
    get_routes: Optional[Dict[str, Any]] = None,
    post_routes: Optional[Dict[str, Any]] = None,
) -> Tuple[BlofinClient, List[Tuple[str, Any]]]:
    """Return (client, sent) with a fake session.

    ``post_routes`` maps an exact API path to a payload, a list of
    payloads consumed in order (last one repeats) or an Exception to
    raise.  ``sent`` records (path, body) for every POST.
    """
    client = BlofinClient(api_key="k", api_secret="s", passphrase="p", demo=True)
    client.session = MagicMock()
    client._position_mode_checked = True
    gets = {"/market/instruments": INSTRUMENTS}
    gets.update(get_routes or {})
    posts = dict(post_routes or {})
    sent: List[Tuple[str, Any]] = []

    def fake_get(url, headers=None, timeout=None):
        for fragment, payload in gets.items():
            if fragment in url:
                return _response(payload)
        raise AssertionError(f"Unexpected GET url: {url}")

    def fake_post(url, headers=None, data=None, timeout=None):
        path = "/api/v1" + url.split("/api/v1", 1)[1]
        sent.append((path, json.loads(data)))
        payload = posts.get(path, OK_ORDER)
        if isinstance(payload, list):
            payload = payload.pop(0) if len(payload) > 1 else payload[0]
        if isinstance(payload, Exception):
            raise payload
        return _response(payload)

    client.session.get.side_effect = fake_get
    client.session.post.side_effect = fake_post
    return client, sent


def _paths(sent: List[Tuple[str, Any]]) -> List[str]:
    return [path for path, _ in sent]


# ======================================================================
# Blofin wire: entry attach
# ======================================================================


class TestBlofinEntryStopAttach:
    def test_entry_carries_sl_fields_when_stop_given(self):
        client, sent = _blofin()
        client.place_order("BTC", "buy", 0.01, "market", sl_trigger_price=95.0)
        body = sent[0][1]
        assert sent[0][0] == ORDER_PATH
        assert body["slTriggerPrice"] == "95"
        assert body["slOrderPrice"] == "-1"
        assert body["slTriggerPriceType"] == "last"
        assert body["reduceOnly"] == "false"
        assert "tpTriggerPrice" not in body

    def test_entry_has_no_sl_fields_without_stop(self):
        client, sent = _blofin()
        client.place_order("BTC", "buy", 0.01, "market")
        body = sent[0][1]
        for key in ("slTriggerPrice", "slOrderPrice", "slTriggerPriceType"):
            assert key not in body

    def test_stop_is_rounded_to_tick(self):
        client, sent = _blofin()
        client.place_order("BTC", "sell", 0.01, "market", sl_trigger_price=105.04)
        assert sent[0][1]["slTriggerPrice"] == "105"

    def test_optional_tp_attached_alongside(self):
        client, sent = _blofin()
        client.place_order(
            "BTC", "buy", 0.01, "market", sl_trigger_price=95.0, tp_trigger_price=110.0
        )
        body = sent[0][1]
        assert body["tpTriggerPrice"] == "110" and body["tpOrderPrice"] == "-1"


# ======================================================================
# Blofin wire: standalone TP/SL
# ======================================================================


class TestBlofinStandaloneTpsl:
    def test_long_stop_is_reduce_only_sell(self):
        client, sent = _blofin(post_routes={TPSL_ORDER: _ok_tpsl("t1")})
        result = client.place_tpsl("BTC", "long", 0.01, 95.0)
        path, body = sent[0]
        assert path == TPSL_ORDER
        assert body["reduceOnly"] == "true"
        assert body["side"] == "sell"
        assert body["positionSide"] == "net"
        assert body["size"] == "10"
        assert body["slTriggerPrice"] == "95"
        assert body["slOrderPrice"] == "-1"
        assert result["success"] is True
        assert result["data"]["tpsl_id"] == "t1"
        assert result["data"]["order_id"] == "t1"

    def test_short_stop_is_reduce_only_buy_from_entry_side(self):
        client, sent = _blofin(post_routes={TPSL_ORDER: _ok_tpsl("t2")})
        client.place_tpsl("BTC", "sell", 0.01, 105.0)  # entry side "sell" = short
        assert sent[0][1]["side"] == "buy"
        assert sent[0][1]["slTriggerPrice"] == "105"

    def test_hedge_mode_targets_position_side(self):
        client, sent = _blofin(post_routes={TPSL_ORDER: _ok_tpsl("t3")})
        client._position_mode = "long_short_mode"
        client.place_tpsl("BTC", "long", 0.01, 95.0)
        assert sent[0][1]["positionSide"] == "long"

    def test_rejection_is_reported_not_raised(self):
        client, _ = _blofin(post_routes={TPSL_ORDER: REJECT_ROW})
        result = client.place_tpsl("BTC", "long", 0.01, 95.0)
        assert result["success"] is False
        assert "152402" in result["error"]
        assert result["data"]["tpsl_id"] is None


# ======================================================================
# Blofin wire: amend cascade
# ======================================================================


class TestBlofinAmendCascade:
    def test_step1_amend_order_success(self):
        client, sent = _blofin(post_routes={AMEND_ORDER: OK_ORDER})
        result = client.amend_stop("BTC", "9001", 96.0, "buy", tpsl_id="t1")
        assert _paths(sent) == [AMEND_ORDER]
        body = sent[0][1]
        assert body == {
            "instId": "BTC-USDT",
            "orderId": "9001",
            "newSlTriggerPrice": "96",
            "newSlOrderPrice": "-1",
        }
        assert result["success"] is True
        assert result["data"]["method"] == "amend-order"
        assert result["data"]["tpsl_id"] == "t1"
        assert result["data"]["stop_price"] == 96.0

    def test_step1_rejected_falls_to_amend_tpsl(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-tpsl-pending": {"code": "0", "data": [_tpsl_row()]}
            },
            post_routes={AMEND_ORDER: REJECT_TOP, TPSL_AMEND: _ok_tpsl("t1")},
        )
        result = client.amend_stop("BTC", "9001", 96.0, "long")
        assert _paths(sent) == [AMEND_ORDER, TPSL_AMEND]
        assert sent[1][1] == {
            "instId": "BTC-USDT",
            "tpslId": "t1",
            "newSlTriggerPrice": "96",
            "newSlOrderPrice": "-1",
        }
        assert result["success"] is True
        assert result["data"]["method"] == "amend-tpsl"
        assert result["data"]["tpsl_id"] == "t1"

    def test_both_rejected_cancels_array_body_and_replaces(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-tpsl-pending": {"code": "0", "data": [_tpsl_row()]}
            },
            post_routes={
                AMEND_ORDER: REJECT_TOP,
                TPSL_AMEND: REJECT_ROW,
                TPSL_CANCEL: _ok_tpsl("t1"),
                TPSL_ORDER: _ok_tpsl("t2"),
            },
        )
        result = client.amend_stop("BTC", "9001", 96.0, "long", tpsl_id="t1")
        assert _paths(sent) == [AMEND_ORDER, TPSL_AMEND, TPSL_CANCEL, TPSL_ORDER]
        assert sent[2][1] == [{"instId": "BTC-USDT", "tpslId": "t1"}]  # JSON array
        replacement = sent[3][1]
        assert replacement["reduceOnly"] == "true"
        assert replacement["side"] == "sell"
        assert replacement["positionSide"] == "net"
        assert replacement["size"] == "10"
        assert replacement["slTriggerPrice"] == "96"
        assert result["success"] is True
        assert result["data"]["tpsl_id"] == "t2"
        assert result["data"]["method"] == "cancel-replace"

    def test_all_steps_fail_returns_failure_without_raising(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-tpsl-pending": {"code": "0", "data": [_tpsl_row()]}
            },
            post_routes={
                AMEND_ORDER: REJECT_TOP,
                TPSL_AMEND: REJECT_ROW,
                TPSL_CANCEL: REJECT_TOP,
                TPSL_ORDER: REJECT_ROW,
            },
        )
        result = client.amend_stop("BTC", "9001", 96.0, "long")
        assert result["success"] is False
        assert "152402" in result["error"]
        assert _paths(sent) == [AMEND_ORDER, TPSL_AMEND, TPSL_CANCEL, TPSL_ORDER]

    def test_transport_exception_is_swallowed(self):
        client, _ = _blofin(post_routes={AMEND_ORDER: ConnectionError("down")})
        client.session.get.side_effect = ConnectionError("down")
        result = client.amend_stop("BTC", "9001", 96.0, "long")
        assert result["success"] is False
        assert "down" in result["error"]

    def test_no_pending_row_places_fresh_stop_sized_from_position(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-tpsl-pending": {"code": "0", "data": []},
                "/account/positions": {
                    "code": "0",
                    "data": [
                        {
                            "instId": "BTC-USDT",
                            "positions": "10",
                            "positionSide": "net",
                            "averagePrice": "100",
                            "unrealizedPnl": "0",
                        }
                    ],
                },
            },
            post_routes={TPSL_ORDER: _ok_tpsl("t9")},
        )
        result = client.amend_stop("BTC", None, 96.0, "long")
        assert _paths(sent) == [TPSL_ORDER]
        assert sent[0][1]["size"] == "10"
        assert sent[0][1]["side"] == "sell"
        assert result["data"]["tpsl_id"] == "t9"

    def test_no_pending_row_and_no_position_is_failure(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-tpsl-pending": {"code": "0", "data": []},
                "/account/positions": {"code": "0", "data": []},
            },
        )
        result = client.amend_stop("BTC", None, 96.0, "long")
        assert result["success"] is False and sent == []

    def test_pending_rows_normalized(self):
        client, _ = _blofin(
            get_routes={
                "/trade/orders-tpsl-pending": {"code": "0", "data": [_tpsl_row()]}
            }
        )
        rows = client.get_pending_tpsl("BTC")
        assert rows[0]["tpsl_id"] == "t1"
        assert rows[0]["symbol"] == "BTC"
        assert rows[0]["sl_trigger_price"] == pytest.approx(95.0)
        assert rows[0]["size"] == pytest.approx(0.010)
        assert rows[0]["reduce_only"] is True and rows[0]["order_type"] == "tpsl"

    def test_cancel_tpsl_sends_array_body(self):
        client, sent = _blofin(post_routes={TPSL_CANCEL: _ok_tpsl("t1")})
        result = client.cancel_tpsl("BTC", "t1")
        assert sent[0] == (TPSL_CANCEL, [{"instId": "BTC-USDT", "tpslId": "t1"}])
        assert result["success"] is True


# ======================================================================
# Blofin wire: cancel-all keeps stops by default
# ======================================================================

PENDING_ORDERS = {"code": "0", "data": [{"instId": "BTC-USDT", "orderId": "1"}]}
PENDING_TPSL = {"code": "0", "data": [_tpsl_row("t1")]}


class TestBlofinCancelAllStops:
    def test_default_leaves_tpsl_rows(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-pending": PENDING_ORDERS,
                "/trade/orders-tpsl-pending": PENDING_TPSL,
            },
            post_routes={CANCEL_BATCH: {"code": "0", "data": []}},
        )
        result = client.cancel_all_orders()
        assert _paths(sent) == [CANCEL_BATCH]
        assert result == {"success": True, "data": {"cancelled": 1}}

    def test_include_stops_cancels_tpsl_rows_too(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-pending": PENDING_ORDERS,
                "/trade/orders-tpsl-pending": PENDING_TPSL,
            },
            post_routes={
                CANCEL_BATCH: {"code": "0", "data": []},
                TPSL_CANCEL: _ok_tpsl("t1"),
            },
        )
        result = client.cancel_all_orders(include_stops=True)
        assert _paths(sent) == [CANCEL_BATCH, TPSL_CANCEL]
        assert sent[1][1] == [{"instId": "BTC-USDT", "tpslId": "t1"}]
        assert result["data"] == {"cancelled": 1, "stops_cancelled": 1}

    def test_no_orders_but_include_stops_still_cancels_stops(self):
        client, sent = _blofin(
            get_routes={
                "/trade/orders-pending": {"code": "0", "data": []},
                "/trade/orders-tpsl-pending": PENDING_TPSL,
            },
            post_routes={TPSL_CANCEL: _ok_tpsl("t1")},
        )
        result = client.cancel_all_orders(include_stops=True)
        assert _paths(sent) == [TPSL_CANCEL]
        assert result["data"]["stops_cancelled"] == 1


# ======================================================================
# Adapters
# ======================================================================


class TestBlofinAdapterStops:
    def test_capability_advertised(self):
        assert BlofinExchange.capabilities().supports_venue_stops is True
        assert PacificaExchange.capabilities().supports_venue_stops is True

    def test_stop_loss_forwarded_only_when_set(self):
        rest = MagicMock()
        rest.place_order.return_value = {"success": True, "data": {"order_id": "1"}}
        exchange = BlofinExchange(rest_client=rest)
        exchange.place_order("BTC", OrderSide.BUY, 0.5, OrderType.MARKET)
        assert "sl_trigger_price" not in rest.place_order.call_args.kwargs
        exchange.place_order(
            "BTC", OrderSide.BUY, 0.5, OrderType.MARKET, stop_loss=95.0
        )
        assert rest.place_order.call_args.kwargs["sl_trigger_price"] == 95.0

    def test_install_stop_delegates_and_normalizes(self):
        rest = MagicMock()
        rest.place_tpsl.return_value = {
            "success": True,
            "data": {"order_id": "t1", "tpsl_id": "t1"},
        }
        result = BlofinExchange(rest_client=rest).install_stop("BTC", "LONG", 1.0, 95.0)
        rest.place_tpsl.assert_called_once_with("BTC", "long", 1.0, 95.0)
        assert result.accepted and result.order_id == "t1"

    def test_install_stop_never_raises(self):
        rest = MagicMock()
        rest.place_tpsl.side_effect = ValueError("below minimum")
        result = BlofinExchange(rest_client=rest).install_stop(
            "BTC", OrderSide.BUY, 1.0, 95.0
        )
        assert result.accepted is False and "below minimum" in result.error

    def test_amend_stop_passes_ids_and_side(self):
        rest = MagicMock()
        rest.amend_stop.return_value = {
            "success": True,
            "data": {"order_id": "t1", "method": "amend-tpsl"},
        }
        result = BlofinExchange(rest_client=rest).amend_stop(
            "BTC", "short", 105.0, entry_order_id="9001", stop_id="t1"
        )
        rest.amend_stop.assert_called_once_with(
            "BTC", "9001", 105.0, "short", tpsl_id="t1"
        )
        assert result.accepted and result.order_id == "t1"

    def test_list_and_cancel_delegate(self):
        rest = MagicMock()
        rest.get_pending_tpsl.return_value = [{"tpsl_id": "t1"}]
        rest.cancel_tpsl.return_value = {"success": True, "data": {"order_id": "t1"}}
        exchange = BlofinExchange(rest_client=rest)
        assert exchange.list_stops("BTC") == [{"tpsl_id": "t1"}]
        assert exchange.cancel_stop("BTC", "t1").accepted is True

    def test_list_stops_propagates_transport_failure(self):
        rest = MagicMock()
        rest.get_pending_tpsl.side_effect = ConnectionError("down")
        with pytest.raises(ConnectionError):
            BlofinExchange(rest_client=rest).list_stops("BTC")

    def test_cancel_all_forwards_include_stops_only_when_set(self):
        rest = MagicMock()
        exchange = BlofinExchange(rest_client=rest)
        exchange.cancel_all_orders("BTC")
        assert rest.cancel_all_orders.call_args == ((), {"symbol": "BTC"})
        exchange.cancel_all_orders(None, include_stops=True)
        assert rest.cancel_all_orders.call_args.kwargs["include_stops"] is True


class TestUnsupportedAdapter:
    def test_stop_methods_report_unsupported_and_no_id(self):
        exchange = UnsupportedExchange(rest_client=MagicMock())
        for result in (
            exchange.install_stop("BTC", "long", 1.0, 95.0),
            exchange.amend_stop("BTC", "long", 96.0, entry_order_id="1", stop_id="t"),
            exchange.cancel_stop("BTC", "t"),
        ):
            assert result.accepted is False and result.success is False
            assert result.order_id is None
            assert "unsupported" in result.error
            assert result.raw.get("unsupported") is True
        assert exchange.list_stops("BTC") == []


# ======================================================================
# Entry path: verify / install / failure policy
# ======================================================================


def _signal(
    side: OrderSide = OrderSide.BUY, price: float = 100.0, stop: float = 95.0
) -> Signal:
    return Signal(
        strategy=list(StrategyType)[0],
        asset="BTC",
        asset_class=list(AssetClass)[0],
        side=side,
        entry_price=price,
        stop_loss=stop,
        take_profit=price * 1.1,
    )


class RecordingLogger:
    def __init__(self):
        self.executed: List[Dict[str, Any]] = []
        self.failed: List[Dict[str, Any]] = []

    def log_signal_pending(self, **kw):
        return kw

    def log_signal_executed(self, **kw):
        self.executed.append(kw)
        return kw

    def log_signal_failed(self, **kw):
        self.failed.append(kw)
        return kw

    def log_signal_rejected(self, **kw):
        return kw


STOP_CAPS = ExchangeCapabilities(
    name="fake",
    funding_interval_hours=8,
    has_testnet=True,
    native_order_sides=("buy", "sell"),
    native_position_sides=("long", "short"),
    amounts_as_strings=True,
    min_order_size_source="x",
    supports_venue_stops=True,
)


class StopExchange:
    """Adapter stand-in with venue stops: records orders, stops, installs."""

    def __init__(
        self, stops: Any = None, install: Optional[OrderResult] = None, supported=True
    ):
        self.stops = stops if stops is not None else []
        self.install_result = install or OrderResult(
            success=True, accepted=True, order_id="t-new", status="accepted"
        )
        self.supported = supported
        self.orders: List[Dict[str, Any]] = []
        self.installs: List[tuple] = []

    def capabilities(self):
        return STOP_CAPS if self.supported else UnsupportedExchange.capabilities()

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {
            "success": True,
            "data": {"order_id": f"o{len(self.orders)}", "price": 100.0},
        }

    def get_order_fill(
        self, symbol, order_id=None, client_order_id=None, requested_quantity=None
    ):
        return OrderResult.from_fill_lookup(
            order_id, "filled", 1.0, 100.0, requested_quantity, client_order_id
        )

    def list_stops(self, symbol):
        if isinstance(self.stops, Exception):
            raise self.stops
        return self.stops

    def install_stop(self, symbol, side, quantity, stop_price):
        self.installs.append((symbol, side, quantity, stop_price))
        return self.install_result


def _bot(exchange, policy: str = "close") -> SimpleNamespace:
    bot = SimpleNamespace(
        exchange=exchange,
        client=MagicMock(),
        db=None,
        risk_manager=None,
        signal_logger=RecordingLogger(),
        event_bus=MagicMock(),
        execution_layer=SimpleNamespace(refine_entry=lambda signal, symbol: signal),
        _pending_entries={},
        _entry_fill_max_lookups=10,
        _venue_stop_policy=policy,
        _protection_records={},
    )
    for name in (
        "_execute_standard_signal_coordinated",
        "_lookup_entry_fill",
        "_record_entry_outcome",
        "_record_entry_fill",
        "_record_entry_rejected",
        "_ensure_entry_protection",
        "_list_venue_stops",
        "_verify_venue_stop",
        "_apply_stop_failure_policy",
        "_persist_protection",
        "_emergency_close_position",
    ):
        setattr(bot, name, getattr(TradingBot, name).__get__(bot))
    return bot


ALLOCATION = {"allocated_amount": 100.0}
ATTACHED_ROW = {
    "tpsl_id": "t1",
    "side": "sell",
    "position_side": "net",
    "sl_trigger_price": 95.0,
}


class TestEntryProtection:
    def test_entry_order_carries_stop_and_attached_row_is_recorded(self):
        exchange = StopExchange(stops=[ATTACHED_ROW])
        bot = _bot(exchange)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert exchange.orders[0]["stop_loss"] == 95.0
        assert len(exchange.orders) == 1 and exchange.installs == []
        record = bot._protection_records[("BTC", "LONG")]
        assert record["venue_stop_state"] == STOP_STATE_ATTACHED
        assert record["venue_stop_id"] == "t1"
        assert record["venue_stop_price"] == 95.0
        assert record["entry_order_id"] == "o1"
        assert len(bot.signal_logger.executed) == 1

    def test_attach_absent_installs_standalone_once(self):
        exchange = StopExchange(stops=[])
        bot = _bot(exchange)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert exchange.installs == [("BTC", "LONG", 1.0, 95.0)]
        record = bot._protection_records[("BTC", "LONG")]
        assert record["venue_stop_state"] == STOP_STATE_STANDALONE
        assert record["venue_stop_id"] == "t-new"
        assert len(exchange.orders) == 1

    def test_policy_close_flattens_unprotected_position_reduce_only(self):
        rejected = OrderResult(status="rejected", error="tpsl refused")
        exchange = StopExchange(stops=[], install=rejected)
        bot = _bot(exchange, policy="close")
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert len(exchange.installs) == 1  # exactly one retry
        assert len(exchange.orders) == 2
        close = exchange.orders[1]
        assert close["reduce_only"] is True
        assert close["side"] == OrderSide.SELL
        assert close["quantity"] == pytest.approx(1.0)
        record = bot._protection_records[("BTC", "LONG")]
        assert record["venue_stop_state"] == STOP_STATE_MISSING
        assert record["closed_by_policy"] is True

    def test_policy_local_keeps_position_marked_missing(self):
        rejected = OrderResult(status="rejected", error="tpsl refused")
        exchange = StopExchange(stops=[], install=rejected)
        bot = _bot(exchange, policy="local")
        bot._execute_standard_signal_coordinated(_signal(OrderSide.SELL), ALLOCATION)
        assert len(exchange.orders) == 1  # no close
        record = bot._protection_records[("BTC", "SHORT")]
        assert record["venue_stop_state"] == STOP_STATE_MISSING
        assert record["venue_stop_price"] == 95.0
        assert record["venue_stop_id"] is None

    def test_list_failure_is_treated_as_absent_and_installs(self):
        exchange = StopExchange(stops=ConnectionError("down"))
        bot = _bot(exchange)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert len(exchange.installs) == 1
        assert (
            bot._protection_records[("BTC", "LONG")]["venue_stop_state"]
            == STOP_STATE_STANDALONE
        )

    def test_unsupported_exchange_records_unsupported_and_sends_no_stop(self):
        exchange = StopExchange(supported=False)
        bot = _bot(exchange)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert "stop_loss" not in exchange.orders[0]
        assert exchange.installs == [] and len(exchange.orders) == 1
        record = bot._protection_records[("BTC", "LONG")]
        assert record["venue_stop_state"] == STOP_STATE_UNSUPPORTED
        assert record["venue_stop_price"] == 95.0

    def test_protection_failure_never_breaks_fill_accounting(self):
        exchange = StopExchange(stops=[])
        exchange.install_stop = MagicMock(side_effect=RuntimeError("boom"))
        bot = _bot(exchange)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert len(bot.signal_logger.executed) == 1
        assert bot.event_bus.publish_event.called


# ======================================================================
# Database round-trip
# ======================================================================

LEGACY_POSITIONS_SQL = """
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL DEFAULT 0,
    opened_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, symbol, side)
)
"""


class TestPositionsStopColumns:
    def test_fresh_database_round_trips_columns(self, isolate_test_database):
        if isolate_test_database is None:
            pytest.skip("SQLite-only round trip")
        db = DatabaseManager()
        db.clear_positions()
        try:
            assert db.record_position_protection(
                "BTC",
                "LONG",
                {
                    "entry_order_id": "9001",
                    "venue_stop_id": "t1",
                    "venue_stop_price": 95.0,
                    "venue_stop_state": STOP_STATE_ATTACHED,
                },
                quantity=1.0,
                entry_price=100.0,
            )
            row = db.get_position("BTC", "LONG")
            assert row["entry_order_id"] == "9001"
            assert row["venue_stop_id"] == "t1"
            assert row["venue_stop_price"] == pytest.approx(95.0)
            assert row["venue_stop_state"] == STOP_STATE_ATTACHED

            # The per-loop upsert must not clobber the stop columns
            db.save_position(
                {
                    "symbol": "BTC",
                    "side": "LONG",
                    "quantity": 1.0,
                    "entry_price": 100.5,
                    "current_price": 101.0,
                    "unrealized_pnl": 0.5,
                    "opened_at": "now",
                }
            )
            rows = [r for r in db.get_positions() if r["symbol"] == "BTC"]
            assert rows[0]["venue_stop_state"] == STOP_STATE_ATTACHED
            assert rows[0]["entry_price"] == pytest.approx(100.5)

            # Explicit update path (trailing move / fallback)
            db.record_position_protection(
                "BTC",
                "LONG",
                {
                    "venue_stop_price": 97.0,
                    "venue_stop_state": STOP_STATE_MISSING,
                    "venue_stop_id": None,
                },
            )
            row = db.get_position("BTC", "LONG")
            assert row["venue_stop_price"] == pytest.approx(97.0)
            assert row["venue_stop_state"] == STOP_STATE_MISSING
            assert row["venue_stop_id"] is None

            # save_position may carry the columns on insert
            db.save_position(
                {
                    "symbol": "ETH",
                    "side": "SHORT",
                    "quantity": 2.0,
                    "entry_price": 50.0,
                    "current_price": 50.0,
                    "opened_at": "now",
                    "venue_stop_state": STOP_STATE_UNSUPPORTED,
                    "venue_stop_price": 55.0,
                }
            )
            assert (
                db.get_position("ETH", "SHORT")["venue_stop_state"]
                == STOP_STATE_UNSUPPORTED
            )
        finally:
            db.clear_positions()

    def test_existing_database_without_columns_is_upgraded_in_place(self, tmp_path):
        path = tmp_path / "legacy.db"
        raw = sqlite3.connect(str(path))
        raw.row_factory = sqlite3.Row
        raw.execute(LEGACY_POSITIONS_SQL)
        raw.execute(
            "INSERT INTO positions (symbol, asset_class, side, quantity, entry_price, opened_at) "
            "VALUES ('BTC', 'crypto', 'LONG', 1.0, 100.0, 'now')"
        )
        raw.commit()
        wrapper = _ConnectionWrapper(raw, is_postgres=False)

        added = db_mod._ensure_position_stop_columns(wrapper)
        raw.commit()
        assert added == [
            "entry_order_id",
            "venue_stop_id",
            "venue_stop_price",
            "venue_stop_state",
        ]
        columns = {row["name"] for row in raw.execute("PRAGMA table_info(positions)")}
        assert {
            "entry_order_id",
            "venue_stop_id",
            "venue_stop_price",
            "venue_stop_state",
        } <= columns
        row = raw.execute("SELECT * FROM positions").fetchone()
        assert (
            row["symbol"] == "BTC" and row["venue_stop_state"] is None
        )  # data preserved

        # Idempotent: a second run adds nothing and raises nothing
        assert db_mod._ensure_position_stop_columns(wrapper) == []
        raw.close()


# ======================================================================
# Grid orphan heuristic ignores TP/SL rows
# ======================================================================


def _order(side: str, price: float, **extra) -> Dict[str, Any]:
    return {
        "order_id": f"{side}{price}",
        "symbol": "BTC",
        "side": side,
        "price": price,
        "quantity": 1.0,
        "order_type": "limit",
        **extra,
    }


class TestGridHeuristicIgnoresStops:
    def test_is_protective_order_markers(self):
        assert is_protective_order({"tpsl_id": "t1"})
        assert is_protective_order({"raw": {"tpslId": "t1"}})
        assert is_protective_order({"order_type": "tpsl"})
        assert is_protective_order({"sl_trigger_price": 95.0})
        assert is_protective_order({"reduce_only": True})
        assert is_protective_order({"raw": {"reduceOnly": "true"}})
        assert not is_protective_order(_order("buy", 99.0))
        assert not is_protective_order({"raw": {"reduceOnly": "false"}})

    def test_protected_position_with_tpsl_rows_is_not_adopted_as_grid(self):
        client = MagicMock()
        client.get_orders.return_value = [
            _order("buy", 99.0),
            _order("buy", 98.0),
            _order("buy", 97.0),
            _order("buy", 96.0),
            {
                "order_id": "t1",
                "symbol": "BTC",
                "side": "sell",
                "price": 0.0,
                "quantity": 4.0,
                "order_type": "tpsl",
                "tpsl_id": "t1",
                "reduce_only": True,
            },
            {
                "order_id": "t2",
                "symbol": "BTC",
                "side": "sell",
                "price": 0.0,
                "quantity": 4.0,
                "order_type": "tpsl",
                "raw": {"tpslId": "t2"},
            },
        ]
        glm = GridLifecycleManager(client, MagicMock())
        with patch.object(
            glm, "_readopt_orphaned_grid_from_exchange", return_value=True
        ) as readopt:
            assert glm._sync_grids_from_exchange() == 0
            readopt.assert_not_called()

    def test_real_two_sided_grid_is_still_adopted(self):
        client = MagicMock()
        client.get_orders.return_value = [
            _order("buy", 99.0),
            _order("buy", 98.0),
            _order("buy", 97.0),
            _order("sell", 101.0),
            _order("sell", 102.0),
        ]
        glm = GridLifecycleManager(client, MagicMock())
        with patch.object(
            glm, "_readopt_orphaned_grid_from_exchange", return_value=True
        ) as readopt:
            assert glm._sync_grids_from_exchange() == 1
            readopt.assert_called_once()
