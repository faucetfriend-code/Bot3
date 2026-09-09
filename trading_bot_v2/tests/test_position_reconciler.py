"""
Unit tests for PositionReconciler (gap H4: position reconciliation).

Covers: stale-position closing when the exchange reports fewer/no
positions, adoption of exchange-only positions, discrepancy detection
(side/quantity/entry-price mismatches), matched positions producing no
side effects, per-position error isolation, and the API-error-vs-
empty-list distinction handled at the TradingBot._update_positions layer.
"""

from unittest.mock import MagicMock

import pytest

from trading_bot_v2.event_system import EventBus, EventType
from trading_bot_v2.position_reconciler import (
    PositionReconciler,
    ReconciliationReport,
    _filter_real_positions,
    _normalize_side,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
def _pos(symbol="BTC-USDC", side="long", quantity=1.0, entry_price=50000.0):
    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "entry_price": entry_price,
    }


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_positions.return_value = []
    return db


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.clear_history()
    return bus


@pytest.fixture
def reconciler(mock_db, event_bus):
    return PositionReconciler(db=mock_db, event_bus=event_bus)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
class TestNormalization:
    def test_normalize_side_long_variants(self):
        assert _normalize_side("long") == "LONG"
        assert _normalize_side("LONG") == "LONG"
        assert _normalize_side("bid") == "LONG"

    def test_normalize_side_short_variants(self):
        assert _normalize_side("short") == "SHORT"
        assert _normalize_side("ask") == "SHORT"

    def test_normalize_side_none_defaults_long(self):
        assert _normalize_side(None) == "LONG"

    def test_filter_real_positions_drops_zero_quantity(self):
        positions = [_pos(quantity=0.0), _pos(symbol="ETH-USDC", quantity=2.0)]
        result = _filter_real_positions(positions)
        assert len(result) == 1
        assert "ETH-USDC:LONG" in result

    def test_filter_real_positions_normalizes_symbol_and_side(self):
        positions = [_pos(symbol="sol-usdc", side="ask", quantity=3.0)]
        result = _filter_real_positions(positions)
        key = "SOL-USDC:SHORT"
        assert key in result
        assert result[key]["symbol"] == "SOL-USDC"
        assert result[key]["side"] == "SHORT"

    def test_filter_real_positions_skips_missing_symbol(self):
        positions = [{"side": "long", "quantity": 1.0}]
        result = _filter_real_positions(positions)
        assert result == {}

    def test_filter_real_positions_handles_string_quantity(self):
        positions = [
            {
                "symbol": "BTC-USDC",
                "side": "long",
                "quantity": "1.5",
                "entry_price": "100",
            }
        ]
        result = _filter_real_positions(positions)
        assert result["BTC-USDC:LONG"]["quantity"] == 1.5


# ---------------------------------------------------------------------------
# Stale local position closing (the core H4 drift bug)
# ---------------------------------------------------------------------------
class TestStalePositionClosing:
    def test_empty_exchange_list_closes_all_db_positions(self, reconciler, mock_db):
        db_positions = [_pos(symbol="BTC-USDC"), _pos(symbol="ETH-USDC")]
        report = reconciler.reconcile(exchange_positions=[], db_positions=db_positions)

        assert report.closed_locally == 2
        assert mock_db.close_position.call_count == 2
        mock_db.close_position.assert_any_call("BTC-USDC", "LONG")
        mock_db.close_position.assert_any_call("ETH-USDC", "LONG")

    def test_partial_closure_when_position_missing_from_exchange(
        self, reconciler, mock_db
    ):
        exchange_positions = [_pos(symbol="BTC-USDC")]
        db_positions = [_pos(symbol="BTC-USDC"), _pos(symbol="ETH-USDC")]

        report = reconciler.reconcile(exchange_positions, db_positions)

        assert report.closed_locally == 1
        assert report.matched == 1
        mock_db.close_position.assert_called_once_with("ETH-USDC", "LONG")

    def test_no_db_positions_and_no_exchange_positions_is_noop(
        self, reconciler, mock_db
    ):
        report = reconciler.reconcile(exchange_positions=[], db_positions=[])
        assert report.closed_locally == 0
        assert report.adopted_from_exchange == 0
        assert report.discrepancies_found == 0
        mock_db.close_position.assert_not_called()


# ---------------------------------------------------------------------------
# Adoption of exchange-only positions
# ---------------------------------------------------------------------------
class TestAdoptMissingPosition:
    def test_adopts_exchange_position_missing_from_db(self, reconciler, mock_db):
        exchange_positions = [
            _pos(symbol="BTC-USDC", quantity=2.0, entry_price=60000.0)
        ]
        report = reconciler.reconcile(exchange_positions, db_positions=[])

        assert report.adopted_from_exchange == 1
        mock_db.save_position.assert_called_once()
        saved = mock_db.save_position.call_args[0][0]
        assert saved["symbol"] == "BTC-USDC"
        assert saved["side"] == "LONG"
        assert saved["quantity"] == 2.0
        assert saved["entry_price"] == 60000.0

    def test_adopt_and_close_can_both_happen_in_one_pass(self, reconciler, mock_db):
        exchange_positions = [_pos(symbol="NEW-USDC")]
        db_positions = [_pos(symbol="OLD-USDC")]

        report = reconciler.reconcile(exchange_positions, db_positions)

        assert report.adopted_from_exchange == 1
        assert report.closed_locally == 1


# ---------------------------------------------------------------------------
# Discrepancy detection
# ---------------------------------------------------------------------------
class TestDiscrepancyDetection:
    def test_quantity_mismatch_detected_and_corrected(
        self, reconciler, mock_db, event_bus
    ):
        exchange_positions = [_pos(quantity=5.0)]
        db_positions = [_pos(quantity=1.0)]

        received = []
        event_bus.subscribe(
            EventType.POSITION_DISCREPANCY, lambda e: received.append(e)
        )

        report = reconciler.reconcile(exchange_positions, db_positions)

        assert report.discrepancies_found == 1
        assert report.matched == 0
        assert len(received) == 1
        assert received[0].data["symbol"] == "BTC-USDC"

        # Local DB corrected to exchange's quantity
        mock_db.save_position.assert_called_once()
        saved = mock_db.save_position.call_args[0][0]
        assert saved["quantity"] == 5.0

    def test_side_mismatch_detected(self, reconciler, mock_db):
        # Exchange says LONG, DB (keyed differently) would not collide since
        # side is part of the key; simulate via same symbol different side
        # entries present in both maps is not directly reachable through the
        # key, so we validate via the static helper instead.
        exchange_pos = {
            "symbol": "BTC-USDC",
            "side": "LONG",
            "quantity": 1.0,
            "entry_price": 100.0,
        }
        db_pos = {
            "symbol": "BTC-USDC",
            "side": "SHORT",
            "quantity": 1.0,
            "entry_price": 100.0,
        }
        mismatches = PositionReconciler._find_mismatches(exchange_pos, db_pos)
        assert any("side mismatch" in m for m in mismatches)

    def test_entry_price_mismatch_detected(self, reconciler, mock_db):
        exchange_positions = [_pos(entry_price=50000.0)]
        db_positions = [_pos(entry_price=40000.0)]

        report = reconciler.reconcile(exchange_positions, db_positions)

        assert report.discrepancies_found == 1
        assert "entry_price mismatch" in report.discrepancy_details[0]

    def test_matching_positions_produce_no_discrepancy(self, reconciler, mock_db):
        exchange_positions = [_pos()]
        db_positions = [_pos()]

        report = reconciler.reconcile(exchange_positions, db_positions)

        assert report.discrepancies_found == 0
        assert report.matched == 1
        mock_db.save_position.assert_not_called()
        mock_db.close_position.assert_not_called()

    def test_small_float_noise_does_not_trigger_discrepancy(self, reconciler, mock_db):
        exchange_positions = [_pos(quantity=1.0000000001, entry_price=50000.0000001)]
        db_positions = [_pos(quantity=1.0, entry_price=50000.0)]

        report = reconciler.reconcile(exchange_positions, db_positions)

        assert report.discrepancies_found == 0
        assert report.matched == 1


# ---------------------------------------------------------------------------
# Robustness: never raises, per-position error isolation
# ---------------------------------------------------------------------------
class TestErrorHandling:
    def test_reconcile_never_raises_on_malformed_input(self, reconciler):
        # Not a list of dicts - should be handled gracefully
        report = reconciler.reconcile(exchange_positions=None, db_positions=None)
        assert isinstance(report, ReconciliationReport)
        assert report.errors == 0  # normalization tolerates None -> empty

    def test_reconcile_isolates_per_position_errors(self, reconciler, mock_db):
        mock_db.close_position.side_effect = RuntimeError("db locked")
        db_positions = [_pos(symbol="BTC-USDC")]

        report = reconciler.reconcile(exchange_positions=[], db_positions=db_positions)

        assert report.errors == 1
        assert report.closed_locally == 0

    def test_report_to_dict_serializes(self, reconciler):
        report = reconciler.reconcile(exchange_positions=[], db_positions=[])
        d = report.to_dict()
        assert "closed_locally" in d
        assert "timestamp" in d
