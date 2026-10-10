"""Stop-loss placement and venue stop protection.

``test_venue_stops.py`` pins the Blofin wire format and the main entry
paths.  This file covers what it leaves out:

* the ``venue_stops`` vocabulary helpers and their boundaries;
* ``_signal_stop_price`` (what counts as "a stop");
* ``_ensure_entry_protection`` edge cases: stopless signals, zero fill
  quantity, attached rows without a trigger price;
* ``_apply_stop_failure_policy`` with a close that cannot be sent;
* ``_persist_protection`` against a database writer that fails;
* ``_stored_stop_price`` candidate precedence;
* ``_enforce_local_stops`` - the loop check that is the ONLY protection
  on venues without server-side stops (Pacifica).

No network, no live database.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from order_risk_fakes import FakeExchange, FakeRawClient, make_bot, make_signal, raiser
from trading_bot_v2.exchanges.base import PositionSide
from trading_bot_v2.models import OrderSide
from trading_bot_v2.order_result import OrderResult
from trading_bot_v2.risk_manager import RiskManager
from trading_bot_v2.trading_bot import TradingBot, _signal_stop_price
from trading_bot_v2.venue_stops import (
    STOP_STATE_ATTACHED,
    STOP_STATE_MISSING,
    STOP_STATE_STANDALONE,
    STOP_STATE_UNSUPPORTED,
    close_order_side,
    find_stop_row,
    position_side_lower,
    stop_hit,
    stop_move_pct,
    venue_stops_supported,
)

# ======================================================================
# venue_stops helpers
# ======================================================================


class TestSideVocabulary:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("long", "long"),
            ("LONG", "long"),
            ("buy", "long"),
            ("bid", "long"),
            (OrderSide.BUY, "long"),
            (PositionSide.LONG, "long"),
            ("short", "short"),
            ("SELL", "short"),
            ("ask", "short"),
            (OrderSide.SELL, "short"),
            (PositionSide.SHORT, "short"),
            ("anything-else", "long"),
        ],
    )
    def test_position_side_lower(self, raw: Any, expected: str) -> None:
        assert position_side_lower(raw) == expected

    def test_close_order_side_is_the_opposite(self) -> None:
        assert close_order_side("long") == "sell"
        assert close_order_side(PositionSide.SHORT) == "buy"
        assert close_order_side(OrderSide.BUY) == "sell"


class TestStopHit:
    @pytest.mark.parametrize(
        ("side", "stop", "price", "hit"),
        [
            ("long", 95.0, 94.0, True),
            ("long", 95.0, 95.0, True),
            ("long", 95.0, 95.01, False),
            ("short", 105.0, 106.0, True),
            ("short", 105.0, 105.0, True),
            ("short", 105.0, 104.99, False),
            ("long", "95", "90", True),
        ],
    )
    def test_crossing_is_inclusive(
        self, side: str, stop: Any, price: Any, hit: bool
    ) -> None:
        assert stop_hit(side, stop, price) is hit

    @pytest.mark.parametrize(
        ("stop", "price"), [(None, 90.0), (0.0, 90.0), (95.0, None), (95.0, 0.0)]
    )
    def test_missing_stop_or_price_never_hits(self, stop: Any, price: Any) -> None:
        assert stop_hit("long", stop, price) is False


class TestFindStopRow:
    def test_empty_or_none_rows(self) -> None:
        assert find_stop_row([], "long") is None
        assert find_stop_row(None, "long") is None

    def test_net_row_matched_on_close_side(self) -> None:
        rows = [{"tpsl_id": "a", "side": "buy"}, {"tpsl_id": "b", "side": "sell"}]
        assert find_stop_row(rows, "long")["tpsl_id"] == "b"
        assert find_stop_row(rows, "short")["tpsl_id"] == "a"

    def test_explicit_position_side_filters_out_the_other_side(self) -> None:
        rows = [{"tpsl_id": "a", "position_side": "short", "side": "sell"}]
        assert find_stop_row(rows, "long") is None
        rows = [{"tpsl_id": "a", "position_side": "long", "side": "sell"}]
        assert find_stop_row(rows, "long")["tpsl_id"] == "a"

    def test_row_without_side_matches_any_side(self) -> None:
        rows = [{"tpsl_id": "a"}]
        assert find_stop_row(rows, "short")["tpsl_id"] == "a"

    def test_stop_loss_rows_win_over_take_profit_only_rows(self) -> None:
        rows = [
            {"tpsl_id": "tp", "side": "sell", "tp_trigger_price": 120.0},
            {"tpsl_id": "sl", "side": "sell", "sl_trigger_price": 95.0},
        ]
        assert find_stop_row(rows, "long")["tpsl_id"] == "sl"

    def test_non_dict_rows_are_skipped(self) -> None:
        rows = ["garbage", None, {"tpsl_id": "a", "side": "sell"}]
        assert find_stop_row(rows, "long")["tpsl_id"] == "a"


class TestStopMovePct:
    def test_no_previous_level_is_infinite(self) -> None:
        assert stop_move_pct(None, 95.0) == float("inf")
        assert stop_move_pct(0.0, 95.0) == float("inf")

    def test_percentage_is_absolute(self) -> None:
        assert stop_move_pct(100.0, 101.0) == pytest.approx(1.0)
        assert stop_move_pct(100.0, 99.0) == pytest.approx(1.0)
        assert stop_move_pct(100.0, 100.0) == 0.0


class TestVenueStopsSupported:
    def test_true_only_when_the_capability_says_so(self) -> None:
        assert venue_stops_supported(FakeExchange(supports_venue_stops=True)) is True
        assert venue_stops_supported(FakeExchange()) is False

    def test_missing_or_broken_capabilities_are_unsupported(self) -> None:
        assert venue_stops_supported(SimpleNamespace()) is False
        assert venue_stops_supported(SimpleNamespace(capabilities=42)) is False
        broken = SimpleNamespace(capabilities=raiser(RuntimeError("x")))
        assert venue_stops_supported(broken) is False
        no_flag = SimpleNamespace(capabilities=lambda: SimpleNamespace())
        assert venue_stops_supported(no_flag) is False


class TestSignalStopPrice:
    @pytest.mark.parametrize(
        ("stop", "expected"),
        [(95.0, 95.0), ("95", 95.0), (None, None), (0.0, None), (-1.0, None)],
    )
    def test_positive_numbers_only(self, stop: Any, expected: Any) -> None:
        assert _signal_stop_price(SimpleNamespace(stop_loss=stop)) == expected

    def test_garbage_and_missing_attribute_are_none(self) -> None:
        assert _signal_stop_price(SimpleNamespace(stop_loss="abc")) is None
        assert _signal_stop_price(SimpleNamespace()) is None


# ======================================================================
# Entry protection edge cases
# ======================================================================


def _fill(qty: float = 1.0) -> OrderResult:
    return OrderResult.from_fill_lookup("o1", "filled", qty, 100.0, qty)


class TestEnsureEntryProtection:
    def test_stopless_signal_on_unsupported_venue_records_no_price(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        record = bot._ensure_entry_protection(
            make_signal(stop_loss=None), "BTC", _fill(), 100.0
        )
        assert record["venue_stop_state"] == STOP_STATE_UNSUPPORTED
        assert record["venue_stop_price"] is None
        assert exchange.orders == [] and exchange.installs == []

    def test_stopless_signal_on_supported_venue_applies_the_policy(self) -> None:
        exchange = FakeExchange(supports_venue_stops=True, stops=[])
        bot = make_bot(exchange, raw_client=FakeRawClient([]))
        record = bot._ensure_entry_protection(
            make_signal(stop_loss=None), "BTC", _fill(), 100.0
        )
        assert record["venue_stop_state"] == STOP_STATE_MISSING
        assert exchange.installs == []
        # policy close: exchange already flat -> counted as closed, no order
        assert record["closed_by_policy"] is True and exchange.orders == []

    def test_zero_fill_quantity_installs_nothing(self) -> None:
        exchange = FakeExchange(supports_venue_stops=True, stops=[])
        bot = make_bot(exchange, policy="local")
        record = bot._ensure_entry_protection(make_signal(), "BTC", _fill(0.0), 100.0)
        assert record["venue_stop_state"] == STOP_STATE_MISSING
        assert exchange.installs == [] and "closed_by_policy" not in record

    def test_attached_row_without_trigger_uses_the_signal_stop(self) -> None:
        row = {"tpsl_id": "t1", "side": "sell"}
        exchange = FakeExchange(supports_venue_stops=True, stops=[row])
        bot = make_bot(exchange)
        record = bot._ensure_entry_protection(
            make_signal(stop_loss=95.0), "BTC", _fill(), 100.0
        )
        assert record["venue_stop_state"] == STOP_STATE_ATTACHED
        assert record["venue_stop_id"] == "t1"
        assert record["venue_stop_price"] == 95.0

    def test_short_entry_looks_for_a_buy_side_row(self) -> None:
        rows = [{"tpsl_id": "sell-row", "side": "sell"}]
        exchange = FakeExchange(supports_venue_stops=True, stops=rows)
        bot = make_bot(exchange)
        record = bot._ensure_entry_protection(
            make_signal(side=OrderSide.SELL, stop_loss=105.0), "BTC", _fill(), 100.0
        )
        # the only row closes a long, so a standalone stop is installed
        assert record["venue_stop_state"] == STOP_STATE_STANDALONE
        assert exchange.installs == [("BTC", "SHORT", 1.0, 105.0)]
        assert ("BTC", "SHORT") in bot._protection_records

    def test_record_is_a_copy(self) -> None:
        bot = make_bot(FakeExchange())
        record = bot._ensure_entry_protection(make_signal(), "BTC", _fill(), 100.0)
        record["venue_stop_state"] = "tampered"
        assert (
            bot._protection_records[("BTC", "LONG")]["venue_stop_state"] != "tampered"
        )


class TestStopFailurePolicy:
    def test_close_policy_with_rejected_close_keeps_position(self) -> None:
        exchange = FakeExchange(ack={"success": False, "error": "no"})
        bot = make_bot(exchange, raw_client=FakeRawClient(RuntimeError("x")))
        record: Dict[str, Any] = {}
        bot._apply_stop_failure_policy("BTC", "LONG", 2.0, 95.0, record, "why")
        assert record["venue_stop_state"] == STOP_STATE_MISSING
        assert record["closed_by_policy"] is False
        assert exchange.orders[0]["reduce_only"] is True

    def test_close_policy_with_zero_quantity_sends_nothing(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        record: Dict[str, Any] = {}
        bot._apply_stop_failure_policy("BTC", "LONG", 0.0, 95.0, record, "why")
        assert record["closed_by_policy"] is False and exchange.orders == []

    def test_local_policy_never_closes(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange, policy="local")
        record: Dict[str, Any] = {}
        bot._apply_stop_failure_policy("BTC", "LONG", 2.0, 95.0, record, "why")
        assert record == {"venue_stop_state": STOP_STATE_MISSING}
        assert exchange.orders == []

    def test_missing_policy_attribute_defaults_to_close(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange, raw_client=FakeRawClient(RuntimeError("x")))
        del bot._venue_stop_policy
        record: Dict[str, Any] = {}
        bot._apply_stop_failure_policy("BTC", "LONG", 2.0, 95.0, record, "why")
        assert record["closed_by_policy"] is True and len(exchange.orders) == 1


class TestPersistProtection:
    def test_memory_mirror_is_created_when_absent(self) -> None:
        bot = make_bot(FakeExchange())
        del bot._protection_records
        bot._persist_protection("BTC", "LONG", {"venue_stop_state": "x"})
        assert bot._protection_records[("BTC", "LONG")] == {"venue_stop_state": "x"}

    def test_database_writer_receives_the_record(self) -> None:
        writes: List[Any] = []
        bot = make_bot(FakeExchange())
        bot.db = SimpleNamespace(
            record_position_protection=lambda *a, **kw: writes.append((a, kw))
        )
        bot._persist_protection("BTC", "LONG", {"k": 1}, quantity=2.0, entry_price=9.0)
        assert writes == [
            (("BTC", "LONG", {"k": 1}), {"quantity": 2.0, "entry_price": 9.0})
        ]

    def test_database_failure_keeps_the_memory_record(self) -> None:
        bot = make_bot(FakeExchange())
        bot.db = SimpleNamespace(record_position_protection=raiser(OSError("disk")))
        bot._persist_protection("BTC", "LONG", {"k": 1})
        assert bot._protection_records[("BTC", "LONG")] == {"k": 1}


class TestStoredStopPrice:
    def _bot(self) -> Any:
        bot = make_bot(FakeExchange())
        bot._stored_stop_price = TradingBot._stored_stop_price.__get__(bot)
        return bot

    def test_database_columns_come_first(self) -> None:
        bot = self._bot()
        bot._protection_records[("BTC", "LONG")] = {"venue_stop_price": 80.0}
        row = {"venue_stop_price": None, "stop_loss": "0", "stop_price": 91.0}
        assert bot._stored_stop_price("BTC", "LONG", row) == 91.0

    def test_memory_then_trailing_state(self) -> None:
        bot = self._bot()
        bot.migrated_position_manager = SimpleNamespace(
            _trailing_stops={"BTC": {"long": 88.0}}
        )
        assert bot._stored_stop_price("BTC", "LONG", None) == 88.0
        bot._protection_records[("BTC", "LONG")] = {"venue_stop_price": 90.0}
        assert bot._stored_stop_price("BTC", "LONG", {}) == 90.0

    def test_garbage_candidates_are_skipped(self) -> None:
        bot = self._bot()
        row = {"venue_stop_price": "abc", "stop_loss": -5, "stop_price": None}
        assert bot._stored_stop_price("BTC", "LONG", row) is None


# ======================================================================
# Local stop enforcement
# ======================================================================


def _record(state: str = STOP_STATE_MISSING, price: Any = 95.0, qty: float = 2.0):
    return {
        "entry_order_id": "o1",
        "venue_stop_id": None,
        "venue_stop_price": price,
        "venue_stop_state": state,
        "quantity": qty,
    }


LONG_ROW = {"symbol": "BTC", "side": "long", "amount": "2", "entry_price": "100"}


class TestEnforceLocalStops:
    def _bot(self, price: float, record: Dict[str, Any], **kw: Any) -> Any:
        exchange = FakeExchange()
        bot = make_bot(
            exchange,
            raw_client=FakeRawClient([LONG_ROW]),
            ws_prices={"BTC": price},
            **kw,
        )
        bot._protection_records[("BTC", "LONG")] = record
        return bot, exchange

    def test_crossed_missing_stop_closes_reduce_only_and_forgets_record(self) -> None:
        bot, exchange = self._bot(94.0, _record())
        assert bot._enforce_local_stops() == 1
        order = exchange.orders[0]
        assert order["side"] is OrderSide.SELL and order["reduce_only"] is True
        assert order["quantity"] == 2.0
        assert ("BTC", "LONG") not in bot._protection_records

    def test_price_exactly_at_stop_closes(self) -> None:
        bot, exchange = self._bot(95.0, _record(STOP_STATE_UNSUPPORTED))
        assert bot._enforce_local_stops() == 1 and len(exchange.orders) == 1

    def test_price_above_stop_does_nothing(self) -> None:
        bot, exchange = self._bot(95.01, _record())
        assert bot._enforce_local_stops() == 0 and exchange.orders == []
        assert ("BTC", "LONG") in bot._protection_records

    @pytest.mark.parametrize("state", [STOP_STATE_ATTACHED, STOP_STATE_STANDALONE])
    def test_venue_protected_positions_are_not_touched(self, state: str) -> None:
        bot, exchange = self._bot(50.0, _record(state))
        assert bot._enforce_local_stops() == 0 and exchange.orders == []

    @pytest.mark.parametrize("price", [None, 0.0])
    def test_record_without_a_stop_is_skipped(self, price: Any) -> None:
        bot, exchange = self._bot(50.0, _record(price=price))
        assert bot._enforce_local_stops() == 0 and exchange.orders == []

    def test_unknown_price_is_skipped(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange, raw_client=FakeRawClient([LONG_ROW]))
        bot._protection_records[("BTC", "LONG")] = _record()
        assert bot._enforce_local_stops() == 0 and exchange.orders == []

    def test_zero_recorded_quantity_falls_back_to_the_exchange(self) -> None:
        bot, exchange = self._bot(90.0, _record(qty=0.0))
        assert bot._enforce_local_stops() == 1
        assert exchange.orders[0]["quantity"] == 2.0

    def test_unparseable_quantity_falls_back_to_the_exchange(self) -> None:
        bot, exchange = self._bot(90.0, _record(qty="n/a"))
        assert bot._enforce_local_stops() == 1
        assert exchange.orders[0]["quantity"] == 2.0

    def test_short_position_closes_with_a_buy(self) -> None:
        exchange = FakeExchange()
        short_row = dict(LONG_ROW, side="short")
        bot = make_bot(
            exchange, raw_client=FakeRawClient([short_row]), ws_prices={"BTC": 106.0}
        )
        bot._protection_records[("BTC", "SHORT")] = _record(price=105.0)
        assert bot._enforce_local_stops() == 1
        assert exchange.orders[0]["side"] is OrderSide.BUY

    def test_migrated_positions_are_left_to_their_manager(self) -> None:
        rm = RiskManager()
        rm.register_migrated_position(
            "BTC", {"side": "long", "qty": 2.0, "entry_price": 100.0, "has_stop": True}
        )
        bot, exchange = self._bot(50.0, _record(), risk_manager=rm)
        assert bot._enforce_local_stops() == 0 and exchange.orders == []

    def test_rejected_close_keeps_the_record(self) -> None:
        exchange = FakeExchange(ack={"success": False, "error": "no"})
        bot = make_bot(
            exchange, raw_client=FakeRawClient([LONG_ROW]), ws_prices={"BTC": 90.0}
        )
        bot._protection_records[("BTC", "LONG")] = _record()
        assert bot._enforce_local_stops() == 0
        assert ("BTC", "LONG") in bot._protection_records

    def test_database_rows_take_precedence_over_memory(self) -> None:
        bot, exchange = self._bot(90.0, _record(STOP_STATE_ATTACHED))
        closed: List[Any] = []
        bot.db = SimpleNamespace(
            get_positions=lambda: [
                {"symbol": "BTC", "side": "LONG", "quantity": 1.0, **_record(qty=1.0)}
            ],
            close_position=lambda *a, **kw: closed.append((a, kw)),
        )
        assert bot._enforce_local_stops() == 1
        assert exchange.orders[0]["quantity"] == 1.0
        assert closed == [(("BTC", "LONG"), {"exit_price": 90.0})]

    def test_database_failure_falls_back_to_memory(self) -> None:
        bot, exchange = self._bot(90.0, _record())
        bot.db = SimpleNamespace(get_positions=raiser(OSError("locked")))
        assert bot._enforce_local_stops() == 1

    def test_database_close_failure_is_swallowed(self) -> None:
        bot, exchange = self._bot(90.0, _record())
        bot.db = SimpleNamespace(
            get_positions=lambda: [], close_position=raiser(OSError("locked"))
        )
        assert bot._enforce_local_stops() == 1
        assert ("BTC", "LONG") not in bot._protection_records
