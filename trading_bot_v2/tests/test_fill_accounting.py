"""T6: entries are recorded from actual fills, never from the request.

Before this change ``_execute_standard_signal_coordinated`` treated the
exchange ack (which for Blofin carries only an order id) as a fill and
recorded ``filled_price=signal.entry_price`` and ``filled_quantity=
requested quantity``.  Desired behaviour:

* a client order id is generated and persisted BEFORE transmission;
* after an accepted ack the exchange is queried for fills and the
  executed quantity + VWAP are recorded;
* not yet filled -> recorded as PENDING and completed on a later cycle;
* delayed, partial and rejected-after-accept outcomes are distinguishable
  in the recorded signal.
"""

import json
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.blofin_client import BlofinClient
from trading_bot_v2.config import AssetClass, StrategyType
from trading_bot_v2.exchanges.blofin import BlofinExchange
from trading_bot_v2.exchanges.pacifica import PacificaExchange
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.order_result import OrderResult, OrderResultStatus
from trading_bot_v2.trading_bot import TradingBot


def _signal(side: OrderSide = OrderSide.BUY, price: float = 100.0) -> Signal:
    return Signal(
        strategy=list(StrategyType)[0],
        asset="BTC",
        asset_class=list(AssetClass)[0],
        side=side,
        entry_price=price,
        stop_loss=price * 0.95,
        take_profit=price * 1.1,
    )


class RecordingLogger:
    """SignalLogger stand-in that records every call in order."""

    def __init__(self, events: List[tuple]):
        self.events = events
        self.pending: List[Dict[str, Any]] = []
        self.executed: List[Dict[str, Any]] = []
        self.failed: List[Dict[str, Any]] = []
        self.rejected: List[Dict[str, Any]] = []

    def log_signal_pending(self, **kw):
        self.events.append(("pending", kw.get("client_order_id")))
        self.pending.append(kw)
        return kw

    def log_signal_executed(self, **kw):
        self.events.append(("executed", kw.get("order_id")))
        self.executed.append(kw)
        return kw

    def log_signal_failed(self, **kw):
        self.events.append(("failed", kw.get("error")))
        self.failed.append(kw)
        return kw

    def log_signal_rejected(self, **kw):
        self.rejected.append(kw)
        return kw


class FakeExchange:
    """Adapter stand-in: records place_order, serves scripted fill lookups."""

    def __init__(self, events: List[tuple], ack: Any, fills: List[OrderResult]):
        self.events = events
        self.ack = ack
        self.fills = list(fills)
        self.orders: List[Dict[str, Any]] = []
        self.lookups: List[Dict[str, Any]] = []

    def place_order(self, **kwargs):
        self.events.append(("place_order", kwargs.get("client_order_id")))
        self.orders.append(kwargs)
        if isinstance(self.ack, Exception):
            raise self.ack
        return self.ack

    def get_order_fill(self, symbol, order_id=None, client_order_id=None, requested_quantity=None):
        self.lookups.append(
            {"symbol": symbol, "order_id": order_id, "client_order_id": client_order_id}
        )
        if len(self.fills) > 1:
            return self.fills.pop(0)
        return self.fills[0]


def _bot(exchange: FakeExchange, logger: RecordingLogger, max_lookups: int = 10):
    """Duck-typed bot with the T6 methods bound onto it."""
    bot = SimpleNamespace(
        exchange=exchange,
        client=MagicMock(),
        signal_logger=logger,
        event_bus=MagicMock(),
        execution_layer=SimpleNamespace(refine_entry=lambda signal, symbol: signal),
        _pending_entries={},
        _entry_fill_max_lookups=max_lookups,
    )
    for name in (
        "_execute_standard_signal_coordinated",
        "_lookup_entry_fill",
        "_record_entry_outcome",
        "_record_entry_fill",
        "_record_entry_rejected",
        "_complete_pending_entries",
    ):
        setattr(bot, name, getattr(TradingBot, name).__get__(bot))
    return bot


def _fill(state: str, qty: float, price, requested: float = 1.0) -> OrderResult:
    return OrderResult.from_fill_lookup("9001", state, qty, price, requested, "cid")


ACCEPTED_ACK = {"success": True, "data": {"order_id": "9001", "price": 100.0}}
ALLOCATION = {"allocated_amount": 100.0}  # 1.0 BTC at entry 100


# ======================================================================
# Client order id persisted before transmit
# ======================================================================


class TestClientOrderId:
    def test_persisted_before_request_and_sent_verbatim(self):
        events: List[tuple] = []
        exchange = FakeExchange(events, ACCEPTED_ACK, [_fill("filled", 1.0, 100.0)])
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)

        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)

        assert events[0][0] == "pending"
        assert events[1][0] == "place_order"
        cid = events[0][1]
        assert cid and len(cid) <= 32
        assert exchange.orders[0]["client_order_id"] == cid
        assert "reduce_only" not in exchange.orders[0]  # entries are never reduce-only

    def test_rejected_ack_records_failed_with_client_order_id(self):
        events: List[tuple] = []
        exchange = FakeExchange(
            events, {"success": False, "error": "bad size", "data": {}}, [_fill("unknown", 0.0, None)]
        )
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)

        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)

        assert logger.executed == []
        assert logger.failed[0]["error"] == "bad size"
        assert "client_order_id=" in logger.failed[0]["notes"]
        assert exchange.lookups == []  # nothing to look up


# ======================================================================
# Fill accounting
# ======================================================================


class TestFillAccounting:
    def test_full_fill_recorded_from_fills_not_request(self):
        events: List[tuple] = []
        exchange = FakeExchange(events, ACCEPTED_ACK, [_fill("filled", 1.0, 100.7)])
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)

        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)

        rec = logger.executed[0]
        assert rec["filled_price"] == pytest.approx(100.7)  # VWAP, not signal 100.0
        assert rec["filled_quantity"] == pytest.approx(1.0)
        assert rec["execution_result"] == "success"
        assert rec["order_id"] == "9001"
        assert bot._pending_entries == {}
        event = bot.event_bus.publish_event.call_args.kwargs["data"]
        assert event["price"] == pytest.approx(100.7)
        assert event["fill_status"] == "filled"

    def test_partial_fill_recorded_with_actual_qty_and_vwap(self):
        events: List[tuple] = []
        exchange = FakeExchange(events, ACCEPTED_ACK, [_fill("partially_filled", 0.6, 101.5)])
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)

        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)

        rec = logger.executed[0]
        assert rec["filled_quantity"] == pytest.approx(0.6)
        assert rec["filled_price"] == pytest.approx(101.5)
        assert rec["execution_result"] == "partial_fill"
        assert "requested 1.000000" in rec["notes"]

    def test_delayed_fill_recorded_pending_then_completed(self):
        events: List[tuple] = []
        exchange = FakeExchange(
            events,
            ACCEPTED_ACK,
            [_fill("live", 0.0, None), _fill("filled", 1.0, 99.9)],
        )
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)

        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)

        assert logger.executed == []  # ack is NOT a fill
        assert len(logger.pending) == 2  # submitted, then accepted-awaiting-fill
        assert logger.pending[1]["order_id"] == "9001"
        assert len(bot._pending_entries) == 1

        bot._complete_pending_entries()

        assert bot._pending_entries == {}
        rec = logger.executed[0]
        assert rec["execution_result"] == "delayed_fill"
        assert rec["filled_price"] == pytest.approx(99.9)
        assert rec["filled_quantity"] == pytest.approx(1.0)

    def test_delayed_partial_fill_is_distinguishable(self):
        events: List[tuple] = []
        exchange = FakeExchange(
            events,
            ACCEPTED_ACK,
            [_fill("live", 0.0, None), _fill("partially_filled", 0.3, 100.2)],
        )
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        bot._complete_pending_entries()
        assert logger.executed[0]["execution_result"] == "delayed_partial_fill"
        assert logger.executed[0]["filled_quantity"] == pytest.approx(0.3)

    def test_rejected_after_accept_recorded_as_rejected(self):
        events: List[tuple] = []
        exchange = FakeExchange(events, ACCEPTED_ACK, [_fill("canceled", 0.0, None)])
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)

        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)

        assert logger.executed == []
        assert logger.failed[0]["error"].startswith("rejected_after_accept")
        assert "order_id=9001" in logger.failed[0]["notes"]
        assert bot._pending_entries == {}

    def test_pending_then_rejected_after_accept(self):
        events: List[tuple] = []
        exchange = FakeExchange(
            events, ACCEPTED_ACK, [_fill("live", 0.0, None), _fill("canceled", 0.0, None)]
        )
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        bot._complete_pending_entries()
        assert logger.executed == []
        assert logger.failed[0]["error"].startswith("rejected_after_accept")

    def test_unconfirmed_after_lookup_cap_recorded_failed(self):
        events: List[tuple] = []
        exchange = FakeExchange(events, ACCEPTED_ACK, [_fill("", 0.0, None)])  # unknown
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger, max_lookups=3)

        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        bot._complete_pending_entries()
        assert len(bot._pending_entries) == 1
        bot._complete_pending_entries()

        assert bot._pending_entries == {}
        assert logger.executed == []
        assert logger.failed[0]["error"].startswith("fill_unconfirmed")

    def test_fill_lookup_exception_is_unknown_not_a_fill(self):
        events: List[tuple] = []
        exchange = FakeExchange(events, ACCEPTED_ACK, [_fill("filled", 1.0, 100.0)])
        exchange.get_order_fill = MagicMock(side_effect=ConnectionError("down"))
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert logger.executed == []
        assert len(bot._pending_entries) == 1

    def test_bare_success_ack_without_order_id_goes_pending(self):
        events: List[tuple] = []
        exchange = FakeExchange(
            events, {"success": True, "data": {}, "status": "success"}, [_fill("", 0.0, None)]
        )
        logger = RecordingLogger(events)
        bot = _bot(exchange, logger)
        bot._execute_standard_signal_coordinated(_signal(), ALLOCATION)
        assert logger.executed == []
        assert len(bot._pending_entries) == 1
        lookup = exchange.lookups[0]
        assert lookup["order_id"] is None and lookup["client_order_id"]


# ======================================================================
# Blofin order-state / fills lookups (no network)
# ======================================================================

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


def _response(payload: Dict[str, Any], status: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = payload
    mock.text = json.dumps(payload)
    return mock


def _blofin(routes: Dict[str, Any]) -> BlofinClient:
    client = BlofinClient(api_key="k", api_secret="s", passphrase="p", demo=True)
    client.session = MagicMock()
    client._position_mode_checked = True
    all_routes = {"/market/instruments": INSTRUMENTS}
    all_routes.update(routes)

    def fake_get(url, headers=None, timeout=None):
        for fragment, payload in all_routes.items():
            if fragment in url:
                return _response(payload)
        raise AssertionError(f"Unexpected GET url: {url}")

    client.session.get.side_effect = fake_get
    return client


class TestBlofinFillLookup:
    def test_fills_history_aggregates_vwap_in_base_units(self):
        client = _blofin(
            {
                "/trade/orders-pending": {"code": "0", "data": []},
                "/trade/orders-history": {"code": "0", "data": []},
                "/trade/fills-history": {
                    "code": "0",
                    "data": [
                        {"orderId": "9001", "fillSize": "6", "fillPrice": "100"},
                        {"orderId": "9001", "fillSize": "4", "fillPrice": "110"},
                        {"orderId": "other", "fillSize": "50", "fillPrice": "1"},
                    ],
                },
            }
        )
        filled, vwap = client.get_order_fills("BTC", "9001")
        assert filled == pytest.approx(0.010)  # 10 contracts * 0.001
        assert vwap == pytest.approx(104.0)

        status = client.get_order_status("BTC", "9001")
        assert status["state"] == "filled" and status["source"] == "fills-history"

        result = BlofinExchange(rest_client=client).get_order_fill(
            "BTC", order_id="9001", requested_quantity=0.010
        )
        assert result.is_filled
        assert result.filled_quantity == pytest.approx(0.010)
        assert result.avg_fill_price == pytest.approx(104.0)

    def test_orders_history_partial_row(self):
        client = _blofin(
            {
                "/trade/orders-pending": {"code": "0", "data": []},
                "/trade/orders-history": {
                    "code": "0",
                    "data": [
                        {
                            "orderId": "9001",
                            "clientOrderId": "cid",
                            "state": "partially_filled",
                            "filledSize": "5",
                            "size": "10",
                            "averagePrice": "100.5",
                        }
                    ],
                },
            }
        )
        result = BlofinExchange(rest_client=client).get_order_fill(
            "BTC", order_id="9001", requested_quantity=0.010
        )
        assert result.status == OrderResultStatus.PARTIAL.value
        assert result.filled_quantity == pytest.approx(0.005)
        assert result.avg_fill_price == pytest.approx(100.5)
        assert result.client_order_id == "cid"

    def test_pending_row_matched_by_client_order_id(self):
        client = _blofin(
            {
                "/trade/orders-pending": {
                    "code": "0",
                    "data": [
                        {"orderId": "9002", "clientOrderId": "cid", "state": "live",
                         "filledSize": "0", "size": "10"}
                    ],
                },
            }
        )
        result = BlofinExchange(rest_client=client).get_order_fill(
            "BTC", order_id=None, client_order_id="cid", requested_quantity=0.010
        )
        assert result.status == OrderResultStatus.ACCEPTED.value
        assert result.order_id == "9002" and result.has_fills is False

    def test_canceled_row_is_rejected(self):
        client = _blofin(
            {
                "/trade/orders-pending": {"code": "0", "data": []},
                "/trade/orders-history": {
                    "code": "0",
                    "data": [{"orderId": "9001", "state": "canceled", "filledSize": "0", "size": "10"}],
                },
            }
        )
        result = BlofinExchange(rest_client=client).get_order_fill("BTC", order_id="9001")
        assert result.rejected

    def test_transport_failure_is_unknown(self):
        client = _blofin({})
        client.session.get.side_effect = ConnectionError("down")
        result = BlofinExchange(rest_client=client).get_order_fill("BTC", order_id="9001")
        assert result.status == OrderResultStatus.UNKNOWN.value
        assert result.has_fills is False


class TestPacificaFillLookup:
    def test_matches_history_row_by_order_id(self):
        rest = MagicMock()
        rest.get_trades.return_value = [
            {"order_id": 5, "order_status": "filled", "filled_amount": "0.5",
             "average_filled_price": "101.25"},
        ]
        result = PacificaExchange(rest_client=rest).get_order_fill(
            "BTC", order_id="5", requested_quantity=0.5
        )
        assert result.is_filled
        assert result.avg_fill_price == pytest.approx(101.25)

    def test_not_found_is_unknown(self):
        rest = MagicMock()
        rest.get_trades.return_value = []
        result = PacificaExchange(rest_client=rest).get_order_fill("BTC", order_id="5")
        assert result.status == OrderResultStatus.UNKNOWN.value
