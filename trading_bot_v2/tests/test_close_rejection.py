"""T3: a rejected / ambiguous close never clears local state.

Audit probe "T3: unsuccessful close acknowledgment still unregisters risk
and closes DB tracking" showed ``_close_position`` ignoring the
``place_order`` result: a ``{"success": False}`` per-order rejection
still unregistered the RiskManager position, closed the DB row, and the
partial path still set ``partial_taken``.  Desired behaviour:

* rejection (per-order or top-level) -> position kept, ERROR logged,
  pending-close marker with an attempt counter, no accounting;
* exception / timeout -> pending-close + the reconciler is invoked
  BEFORE any accounting;
* the next ``manage_positions`` cycle retries, capped by
  ``MIGRATED_CLOSE_MAX_ATTEMPTS``;
* accounting happens only for CONFIRMED executed quantity.
"""

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.migrated_position_manager import MigratedPositionManager
from trading_bot_v2.order_result import OrderResult, OrderResultStatus
from trading_bot_v2.trading_bot import TradingBot


class ScriptedClient:
    """Native-client stand-in with scripted acks and position snapshots."""

    def __init__(self, positions: List[List[Dict[str, Any]]], acks: List[Any]):
        self._positions = list(positions)
        self._acks = list(acks)
        self.calls: List[Dict[str, Any]] = []

    def get_positions(self) -> List[Dict[str, Any]]:
        if len(self._positions) > 1:
            return self._positions.pop(0)
        return self._positions[0] if self._positions else []

    def place_order(self, symbol, side, quantity, order_type, price=None, **kwargs):
        self.calls.append(
            {"symbol": symbol, "side": side, "quantity": quantity, **kwargs}
        )
        ack = self._acks.pop(0) if len(self._acks) > 1 else self._acks[0]
        if isinstance(ack, Exception):
            raise ack
        return ack


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


LONG_2 = [{"symbol": "BTC", "side": "long", "amount": "2"}]
POS = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
OK_ACK = {"success": True, "data": {"order_id": "77"}}
REJECT_ACK = {"success": False, "error": "insufficient margin", "data": {}}


def _manager(client, risk, reconcile=None) -> MigratedPositionManager:
    manager = MigratedPositionManager(
        client, risk, regime_detector=None, reconcile_callback=reconcile
    )
    manager._update_db_position = MagicMock()
    return manager


# ======================================================================
# OrderResult normalization
# ======================================================================


class TestOrderResult:
    def test_per_order_rejection_is_not_accepted(self):
        result = OrderResult.from_ack(REJECT_ACK)
        assert result.accepted is False and result.success is False
        assert result.status == OrderResultStatus.REJECTED.value
        assert "margin" in result.error

    def test_bare_success_ack_is_accepted_without_order_id(self):
        result = OrderResult.from_ack(
            {"success": True, "data": {}, "status": "success"}
        )
        assert result.accepted is True and result.order_id is None
        assert result.status == OrderResultStatus.ACCEPTED.value
        assert result.has_fills is False

    def test_success_true_without_id_or_status_is_not_accepted(self):
        result = OrderResult.from_ack({"success": True, "data": {}})
        assert result.accepted is False

    def test_non_dict_is_unknown(self):
        result = OrderResult.from_ack('"success"')
        assert result.accepted is False
        assert result.status == OrderResultStatus.UNKNOWN.value

    def test_fill_lookup_states(self):
        partial = OrderResult.from_fill_lookup("1", "partially_filled", 0.4, 101.0, 1.0)
        assert partial.status == OrderResultStatus.PARTIAL.value and partial.has_fills
        full = OrderResult.from_fill_lookup("1", "live", 1.0, 101.0, 1.0)
        assert full.is_filled
        canceled = OrderResult.from_fill_lookup("1", "canceled", 0.0, None, 1.0)
        assert canceled.rejected and canceled.accepted is False
        live = OrderResult.from_fill_lookup("1", "live", 0.0, None, 1.0)
        assert live.status == OrderResultStatus.ACCEPTED.value
        assert OrderResult.from_fill_lookup("1", "", 0.0, None).status == "unknown"


# ======================================================================
# Rejection keeps state
# ======================================================================


class TestRejectedClose:
    def test_rejected_full_close_keeps_risk_and_db_and_marks_pending(self):
        """Inverse of audit probe T3."""
        client = ScriptedClient([LONG_2], [REJECT_ACK])
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)

        manager._close_position("BTC", dict(POS), "probe")

        assert risk.unregistered == []
        manager._update_db_position.assert_not_called()
        assert risk.positions  # still registered
        pending = manager.get_pending_closes()
        assert ("BTC", "long") in pending
        marker = pending[("BTC", "long")]
        assert marker["attempts"] == 1 and marker["kind"] == "full"
        assert marker["ambiguous"] is False
        assert "margin" in marker["last_error"]

    def test_rejected_partial_close_does_not_set_partial_taken(self):
        client = ScriptedClient([LONG_2], [REJECT_ACK])
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)
        manager._take_profits["BTC"] = {
            "long": {"target": 105.0, "partial_taken": False}
        }

        taken = manager._check_take_profit("BTC", dict(POS), current_price=110.0)

        assert taken is False
        assert manager._take_profits["BTC"]["long"]["partial_taken"] is False
        assert risk.unregistered == []
        assert manager.get_pending_closes()[("BTC", "long")]["kind"] == "partial"

    def test_top_level_exception_marks_pending_and_reconciles_first(self):
        reconcile = MagicMock()
        client = ScriptedClient([LONG_2], [TimeoutError("read timed out")])
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk, reconcile=reconcile)

        manager._close_position("BTC", dict(POS), "trailing_stop")

        reconcile.assert_called_once()
        assert risk.unregistered == []
        manager._update_db_position.assert_not_called()
        marker = manager.get_pending_closes()[("BTC", "long")]
        assert marker["ambiguous"] is True
        assert "TimeoutError" in marker["last_error"]

    def test_accepted_but_unconfirmed_fill_is_pending_not_accounted(self):
        # Ack accepted, but the post-order position read fails -> unknown.
        client = ScriptedClient([LONG_2], [OK_ACK])
        reads = iter([LONG_2])

        def flaky_get_positions():
            try:
                return next(reads)
            except StopIteration:
                raise ConnectionError("positions unavailable")

        client.get_positions = flaky_get_positions
        risk = FakeRiskManager([POS])
        reconcile = MagicMock()
        manager = _manager(client, risk, reconcile=reconcile)

        manager._close_position("BTC", dict(POS), "trailing_stop")

        assert risk.unregistered == []
        marker = manager.get_pending_closes()[("BTC", "long")]
        assert marker["accepted"] is True and marker["ambiguous"] is True
        reconcile.assert_called_once()

    def test_partial_execution_accounts_nothing_until_confirmed(self):
        # Order accepted, exchange still shows 1.2 of 2 -> not finalized.
        client = ScriptedClient(
            [LONG_2, [{"symbol": "BTC", "side": "long", "amount": "1.2"}]], [OK_ACK]
        )
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)

        manager._close_position("BTC", dict(POS), "trailing_stop")

        assert risk.unregistered == []
        marker = manager.get_pending_closes()[("BTC", "long")]
        assert marker["qty"] == pytest.approx(1.2)
        assert "partial execution" in marker["last_error"]


# ======================================================================
# Retry on the next cycle
# ======================================================================


class TestPendingCloseRetry:
    def test_retry_next_cycle_then_finalize_on_confirmation(self):
        client = ScriptedClient(
            [LONG_2, LONG_2, []],  # before reject, before retry, after retry
            [REJECT_ACK, OK_ACK],
        )
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)

        manager._close_position("BTC", dict(POS), "trend_reversal")
        assert risk.unregistered == []

        result = manager.manage_positions(current_prices={"BTC": 100.0})

        assert result["pending_closes_retried"] == 1
        assert len(client.calls) == 2
        assert all(c["reduce_only"] is True for c in client.calls)
        assert risk.unregistered == [("BTC", "long", 2.0)]
        manager._update_db_position.assert_called_once()
        assert manager.get_pending_closes() == {}

    def test_retry_uses_exchange_remaining_quantity(self):
        client = ScriptedClient(
            [LONG_2, [{"symbol": "BTC", "side": "long", "amount": "0.7"}], []],
            [REJECT_ACK, OK_ACK],
        )
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)
        manager._close_position("BTC", dict(POS), "trend_reversal")

        manager.manage_positions(current_prices={"BTC": 100.0})

        assert client.calls[1]["quantity"] == pytest.approx(0.7)
        assert risk.unregistered == [("BTC", "long", 2.0)]

    def test_pending_position_is_not_re_evaluated_in_the_loop(self):
        # While a close is pending, the normal stop/trend checks must not
        # run for that position (they would stack a second order).
        client = ScriptedClient([LONG_2], [REJECT_ACK])
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)
        manager._check_stop_hit = MagicMock(return_value=True)
        manager._check_time_exit = MagicMock(return_value=False)
        manager._check_trend_reversal = MagicMock(return_value=False)
        manager._get_market_data = MagicMock(return_value=None)

        manager._close_position("BTC", dict(POS), "trailing_stop")
        result = manager.manage_positions(current_prices={"BTC": 100.0})

        assert result["positions_managed"] == 0
        manager._check_stop_hit.assert_not_called()
        assert len(client.calls) == 2  # original + one retry, nothing stacked

    def test_retry_cap_is_enforced(self, monkeypatch):
        monkeypatch.setenv("MIGRATED_CLOSE_MAX_ATTEMPTS", "2")
        client = ScriptedClient([LONG_2], [REJECT_ACK])
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)
        assert manager._max_close_attempts == 2

        manager._close_position("BTC", dict(POS), "trailing_stop")
        for _ in range(5):
            manager.manage_positions(current_prices={"BTC": 100.0})

        assert len(client.calls) == 2  # initial + exactly one retry
        marker = manager.get_pending_closes()[("BTC", "long")]
        assert marker["attempts"] == 2 and marker["escalated"] is True
        assert risk.positions  # still registered, never cleared
        assert risk.unregistered == []

    def test_partial_retry_sets_partial_taken_only_when_filled(self):
        client = ScriptedClient(
            [LONG_2, LONG_2, [{"symbol": "BTC", "side": "long", "amount": "1"}]],
            [REJECT_ACK, OK_ACK],
        )
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)
        manager._take_profits["BTC"] = {
            "long": {"target": 105.0, "partial_taken": False}
        }
        manager._check_take_profit("BTC", dict(POS), current_price=110.0)
        assert manager._take_profits["BTC"]["long"]["partial_taken"] is False

        manager.manage_positions(current_prices={"BTC": 110.0})

        assert manager._take_profits["BTC"]["long"]["partial_taken"] is True
        assert manager.get_pending_closes() == {}
        assert risk.unregistered == []  # partial keeps the registration

    def test_marker_dropped_when_position_no_longer_registered(self):
        client = ScriptedClient([LONG_2], [REJECT_ACK])
        risk = FakeRiskManager([POS])
        manager = _manager(client, risk)
        manager._close_position("BTC", dict(POS), "trailing_stop")
        risk.positions = []  # e.g. the reconciler removed it
        manager.manage_positions(current_prices={"BTC": 100.0})
        assert manager.get_pending_closes() == {}


# ======================================================================
# Reconcile hook on the bot
# ======================================================================


class TestReconcileHook:
    def test_ambiguous_close_forces_full_reconciliation(self):
        bot = SimpleNamespace(
            client=MagicMock(),
            _last_reconciliation_time=12345.0,
            _maybe_run_full_reconciliation=MagicMock(),
        )
        bot.client.get_positions.return_value = [{"symbol": "BTC"}]
        bot._reconcile_after_ambiguous_close = (
            TradingBot._reconcile_after_ambiguous_close.__get__(bot)
        )

        bot._reconcile_after_ambiguous_close()

        assert bot._last_reconciliation_time == 0.0
        bot._maybe_run_full_reconciliation.assert_called_once_with(
            exchange_positions=[{"symbol": "BTC"}]
        )

    def test_reconcile_hook_survives_position_read_failure(self):
        bot = SimpleNamespace(
            client=MagicMock(),
            _last_reconciliation_time=1.0,
            _maybe_run_full_reconciliation=MagicMock(),
        )
        bot.client.get_positions.side_effect = TimeoutError("probe")
        TradingBot._reconcile_after_ambiguous_close(bot)
        bot._maybe_run_full_reconciliation.assert_not_called()
