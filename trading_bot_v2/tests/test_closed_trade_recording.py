"""Closed-trade recording: one row per close, strategy-tagged, idempotent.

Covers ``history.record_closed_trade`` (the single writer) and every live
close path that calls it:

- local stop enforcement (``TradingBot._enforce_local_stops``)
- position found gone at the loop sync
  (``TradingBot._close_stale_local_positions``): venue stop fill, venue
  take profit, liquidation, manual close on the venue
- position found gone at the full reconciliation (``PositionReconciler``)
- position found gone at ``POST /api/positions/sync``
- migrated position manager closes: trailing stop, time exit, trend
  reversal, regime exit, partial take profit, already-flat, retries

Everything runs against a REAL ``DatabaseManager`` on a temp SQLite file.
The venue is a scripted fake; the fakes used by the sync paths have no
order methods at all, so a recording step that tried to send or cancel
anything would raise AttributeError and fail the test.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock

import pytest

from trading_bot_v2 import api_server
from trading_bot_v2.config import StrategyType
from trading_bot_v2.history import (
    MIGRATED_STRATEGY_TAG,
    UNKNOWN_STRATEGY_TAG,
    TradeStore,
    canonical_strategy_tag,
    current_network,
    record_closed_trade,
    record_position_gone,
)
from trading_bot_v2.history.closed_trades import is_strategy_tag
from trading_bot_v2.kelly_position_sizer import KELLY_OK, KellyPositionSizer
from trading_bot_v2.migrated_position_manager import MigratedPositionManager
from trading_bot_v2.models import AssetClass, OrderSide, Signal
from trading_bot_v2.order_result import OrderResult
from trading_bot_v2.position_reconciler import PositionReconciler
from trading_bot_v2.trading_bot import TradingBot

OPENED_AT = "2026-10-01T08:00:00+00:00"


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------


@pytest.fixture
def db_mod() -> Iterator[Any]:
    """Point trading_bot_v2.database at a fresh temp SQLite file."""
    import trading_bot_v2.database as module

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    saved = (module._active_backend, module.DATABASE_PATH, module._connection_pool)
    saved_env = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = tmp.name
    module._active_backend = "sqlite"
    module.DATABASE_PATH = tmp.name
    pool = module.ConnectionPool(max_connections=2)
    module._connection_pool = pool
    module._data_cache.invalidate("positions_all")
    yield module
    pool.close_all()
    module._active_backend, module.DATABASE_PATH, module._connection_pool = saved
    module._data_cache.invalidate("positions_all")
    if saved_env is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = saved_env
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


@pytest.fixture
def dbm(db_mod: Any) -> Any:
    """A real DatabaseManager over the temp file (schema initialised)."""
    return db_mod.DatabaseManager()


def _open(
    dbm: Any,
    symbol: str = "BTC",
    side: str = "LONG",
    quantity: float = 2.0,
    entry: float = 100.0,
    current: float = 100.0,
    strategy: Optional[str] = "mean_reversion",
    order_id: Optional[str] = "entry-1",
    stop_state: Optional[str] = None,
    stop_price: Optional[float] = None,
) -> None:
    """Create an open positions row the way the live path leaves it."""
    data: Dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "entry_price": entry,
        "current_price": current,
        "opened_at": OPENED_AT,
        "funding_pnl": 0.25,
        "asset_class": "perpetual",
    }
    if order_id is not None:
        data["entry_order_id"] = order_id
    if stop_state is not None:
        data["venue_stop_state"] = stop_state
        data["venue_stop_price"] = stop_price
    dbm.save_position(data)
    if strategy is not None:
        assert dbm.tag_position_owner(symbol, side, strategy, "ranging_calm")


def _rows(dbm: Any, **filters: Any) -> List[Dict[str, Any]]:
    """Closed-trade rows, oldest first."""
    rows = TradeStore(db=dbm).get_closed_trades(**filters)
    return sorted(rows, key=lambda row: row["id"])


def _bind(bot: SimpleNamespace, *names: str) -> SimpleNamespace:
    """Bind TradingBot methods onto a duck-typed stand-in."""
    for name in names:
        setattr(bot, name, getattr(TradingBot, name).__get__(bot))
    return bot


class ScriptedClient:
    """Venue fake: scripted position snapshots and order acks."""

    def __init__(self, positions: List[List[Dict[str, Any]]], acks: List[Any]):
        self._positions = list(positions)
        self._acks = list(acks)
        self.orders: List[Dict[str, Any]] = []

    def get_positions(self) -> List[Dict[str, Any]]:
        if len(self._positions) > 1:
            return self._positions.pop(0)
        return self._positions[0] if self._positions else []

    def place_order(
        self, symbol: str, side: str, quantity: float, order_type: str, **kw: Any
    ) -> Any:
        self.orders.append({"symbol": symbol, "side": side, "quantity": quantity})
        ack = self._acks.pop(0) if len(self._acks) > 1 else self._acks[0]
        if isinstance(ack, Exception):
            raise ack
        return ack


class FakeRiskManager:
    """Minimal migrated-position registry."""

    def __init__(self, positions: List[Dict[str, Any]]):
        self.positions = [dict(p) for p in positions]
        self.unregistered: List[Any] = []

    def get_migrated_positions(self, symbol: Any = None) -> List[Dict[str, Any]]:
        return [dict(p) for p in self.positions]

    def has_migrated_positions(self, symbol: Any = None) -> bool:
        return bool(self.positions)

    def unregister_migrated_position(
        self, symbol: str, side: str, qty: Any = None
    ) -> bool:
        self.unregistered.append((symbol, side, qty))
        self.positions = []
        return True


def _venue(side: str, amount: float) -> List[Dict[str, Any]]:
    return [{"symbol": "BTC", "side": side, "amount": str(amount)}]


def _ack(order_id: str, filled: Optional[float] = None, price: float = 0.0) -> Any:
    data: Dict[str, Any] = {"order_id": order_id}
    if filled is not None:
        data["filled_quantity"] = filled
        data["avg_fill_price"] = price
    return {"success": True, "data": data}


def _manager(
    dbm: Any, client: Any, pos: Dict[str, Any], price: float
) -> MigratedPositionManager:
    manager = MigratedPositionManager(
        client,
        FakeRiskManager([pos]),
        regime_detector=None,
        db=dbm,
        price_lookup=lambda symbol: {"last": price},
    )
    manager._update_db_position = MagicMock()
    return manager


def _stop_bot(dbm: Any, price: float, close_ok: bool = True) -> SimpleNamespace:
    """Stand-in for local stop enforcement; the close order is a stub."""
    bot = SimpleNamespace(db=dbm, _protection_records={}, closes=[])

    def emergency_close(symbol: str, side: str, quantity: float) -> bool:
        bot.closes.append((symbol, side, quantity))
        return close_ok

    bot._emergency_close_position = emergency_close
    bot._current_price_for = lambda symbol: price
    return _bind(
        bot,
        "_enforce_local_stops",
        "_protection_rows",
        "_is_migrated_symbol",
        "_mark_position_closed",
        "_exchange_quantity_for",
        "_close_stale_local_positions",
    )


def _sync_bot(dbm: Any, price: Optional[float]) -> SimpleNamespace:
    """Stand-in for the loop sync; it has no client and no order method."""
    bot = SimpleNamespace(db=dbm)
    bot._current_price_for = lambda symbol: price
    return _bind(bot, "_close_stale_local_positions")


# ----------------------------------------------------------------------
# The canonical tag
# ----------------------------------------------------------------------


class TestCanonicalTag:
    @pytest.mark.parametrize(
        "value",
        [
            StrategyType.MEAN_REVERSION,
            "mean_reversion",
            "MEAN_REVERSION",
            "Mean Reversion",
            "meanreversion",
            " mean-reversion ",
        ],
    )
    def test_every_spelling_gives_the_enum_value(self, value: Any) -> None:
        assert canonical_strategy_tag(value) == "mean_reversion"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_blank_is_unknown(self, value: Any) -> None:
        assert canonical_strategy_tag(value) == UNKNOWN_STRATEGY_TAG

    def test_unknown_and_migrated_are_not_strategy_tags(self) -> None:
        assert not is_strategy_tag(UNKNOWN_STRATEGY_TAG)
        assert not is_strategy_tag(MIGRATED_STRATEGY_TAG)
        assert all(is_strategy_tag(member.value) for member in StrategyType)

    def test_network_reads_testnet_default(self) -> None:
        assert current_network("pacifica") in ("testnet", "mainnet")
        assert current_network("blofin") == "testnet"


# ----------------------------------------------------------------------
# Close path: local stop enforcement
# ----------------------------------------------------------------------


class TestLocalStopEnforcement:
    @pytest.mark.parametrize(
        "side,stop,price,expected_pnl",
        [("LONG", 95.0, 94.0, -12.0), ("SHORT", 105.0, 107.0, -14.0)],
    )
    def test_one_row_with_tag_reason_and_sign(
        self, dbm: Any, side: str, stop: float, price: float, expected_pnl: float
    ) -> None:
        _open(dbm, side=side, stop_state="missing", stop_price=stop)
        bot = _stop_bot(dbm, price)

        assert bot._enforce_local_stops() == 1

        rows = _rows(dbm)
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy"] == "mean_reversion"
        assert row["side"] == side.lower()
        assert row["close_reason"] == "local_stop"
        assert row["quantity"] == 2.0
        assert row["entry_price"] == 100.0 and row["exit_price"] == price
        assert row["pnl"] == pytest.approx(expected_pnl)
        # The trigger price is not the market fill.
        assert row["exit_price_source"] == "estimate"
        assert row["regime"] == "ranging_calm"
        assert row["funding_pnl"] == pytest.approx(0.25)
        assert row["commission"] is None
        assert dbm.get_position("BTC", side) is None

    def test_sync_pass_after_the_stop_adds_nothing(self, dbm: Any) -> None:
        _open(dbm, stop_state="missing", stop_price=95.0)
        bot = _stop_bot(dbm, 94.0)
        bot._enforce_local_stops()
        bot._close_stale_local_positions(exchange_positions=[])
        bot._enforce_local_stops()
        assert len(_rows(dbm)) == 1

    def test_rejected_close_records_nothing(self, dbm: Any) -> None:
        _open(dbm, stop_state="missing", stop_price=95.0)
        bot = _stop_bot(dbm, 94.0, close_ok=False)
        assert bot._enforce_local_stops() == 0
        assert _rows(dbm) == []
        assert dbm.get_position("BTC", "LONG") is not None

    def test_failed_row_delete_then_sync_does_not_double_count(
        self, dbm: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stop records, the row delete fails, the sync finds it again."""
        _open(dbm, stop_state="missing", stop_price=95.0)
        bot = _stop_bot(dbm, 94.0)
        real_close = dbm.close_position
        monkeypatch.setattr(
            dbm, "close_position", MagicMock(side_effect=OSError("locked"))
        )
        assert bot._enforce_local_stops() == 1
        assert dbm.get_position("BTC", "LONG") is not None
        monkeypatch.setattr(dbm, "close_position", real_close)

        bot._close_stale_local_positions(exchange_positions=[])

        rows = _rows(dbm)
        assert len(rows) == 1 and rows[0]["close_reason"] == "local_stop"
        assert dbm.get_position("BTC", "LONG") is None


# ----------------------------------------------------------------------
# Close path: position gone at the loop sync (venue stop / TP / manual)
# ----------------------------------------------------------------------


class TestPositionGoneAtLoopSync:
    @pytest.mark.parametrize(
        "side,price,expected_pnl",
        [
            ("LONG", 110.0, 20.0),
            ("LONG", 96.0, -8.0),
            ("SHORT", 90.0, 20.0),
            ("SHORT", 103.0, -6.0),
        ],
    )
    def test_one_estimated_row(
        self, dbm: Any, side: str, price: float, expected_pnl: float
    ) -> None:
        _open(dbm, side=side, strategy="ma_crossover")
        bot = _sync_bot(dbm, price)

        bot._close_stale_local_positions(exchange_positions=[])

        rows = _rows(dbm)
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy"] == "ma_crossover"
        assert row["side"] == side.lower()
        assert row["close_reason"] == "position_gone_at_sync"
        assert row["pnl"] == pytest.approx(expected_pnl)
        assert row["exit_price"] == price
        assert row["exit_price_source"] == "estimate"
        assert row["network"] == current_network()
        assert row["exchange"] == "pacifica"
        assert row["entry_time"] == OPENED_AT
        assert dbm.get_position("BTC", side) is None

    def test_no_price_falls_back_to_the_last_row_price(self, dbm: Any) -> None:
        _open(dbm, current=104.0)
        _sync_bot(dbm, None)._close_stale_local_positions(exchange_positions=[])
        row = _rows(dbm)[0]
        assert row["exit_price"] == 104.0 and row["pnl"] == pytest.approx(8.0)
        assert row["exit_price_source"] == "estimate"

    def test_repeated_sync_pass_adds_nothing(self, dbm: Any) -> None:
        _open(dbm)
        bot = _sync_bot(dbm, 101.0)
        for _ in range(3):
            bot._close_stale_local_positions(exchange_positions=[])
        assert len(_rows(dbm)) == 1

    def test_still_open_position_is_not_recorded(self, dbm: Any) -> None:
        _open(dbm)
        _sync_bot(dbm, 101.0)._close_stale_local_positions(
            exchange_positions=_venue("bid", 2.0)
        )
        assert _rows(dbm) == []

    def test_unowned_position_is_tagged_unknown(self, dbm: Any) -> None:
        _open(dbm, strategy=None, order_id=None)
        _sync_bot(dbm, 101.0)._close_stale_local_positions(exchange_positions=[])
        assert _rows(dbm)[0]["strategy"] == UNKNOWN_STRATEGY_TAG

    def test_reopened_position_is_a_new_episode(self, dbm: Any) -> None:
        bot = _sync_bot(dbm, 101.0)
        _open(dbm, order_id="entry-1")
        bot._close_stale_local_positions(exchange_positions=[])
        _open(dbm, order_id="entry-2")
        bot._close_stale_local_positions(exchange_positions=[])
        assert len(_rows(dbm)) == 2

    def test_recording_failure_does_not_stop_the_close(
        self, dbm: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _open(dbm)
        monkeypatch.setattr(
            dbm,
            "save_closed_trade",
            MagicMock(side_effect=sqlite3.OperationalError("database is locked")),
        )
        _sync_bot(dbm, 101.0)._close_stale_local_positions(exchange_positions=[])
        assert dbm.get_position("BTC", "LONG") is None
        assert _rows(dbm) == []


# ----------------------------------------------------------------------
# Close path: full reconciliation and POST /api/positions/sync
# ----------------------------------------------------------------------


class TestPositionGoneAtReconcilerAndApiSync:
    @pytest.mark.parametrize("side,expected_pnl", [("LONG", 6.0), ("SHORT", -6.0)])
    def test_reconciler_records_once(
        self, dbm: Any, side: str, expected_pnl: float
    ) -> None:
        _open(dbm, side=side, current=103.0, strategy="vwap_scalping")
        reconciler = PositionReconciler(db=dbm, event_bus=MagicMock())

        report = reconciler.reconcile(
            exchange_positions=[], db_positions=dbm.get_positions()
        )
        reconciler.reconcile(exchange_positions=[], db_positions=dbm.get_positions())

        assert report.closed_locally == 1
        rows = _rows(dbm)
        assert len(rows) == 1
        assert rows[0]["strategy"] == "vwap_scalping"
        assert rows[0]["close_reason"] == "position_gone_at_sync"
        assert rows[0]["pnl"] == pytest.approx(expected_pnl)
        assert rows[0]["exit_price_source"] == "estimate"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("side,expected_pnl", [("LONG", -4.0), ("SHORT", 4.0)])
    async def test_api_sync_records_once(
        self, dbm: Any, side: str, expected_pnl: float
    ) -> None:
        _open(dbm, side=side, current=98.0, strategy="momentum_scalping")
        integration = api_server.BotIntegration()
        integration.database = dbm
        integration.pacifica_client = SimpleNamespace(get_positions=lambda: [])

        first = await integration.sync_positions()
        second = await integration.sync_positions()

        assert first["closed_count"] == 1 and second["closed_count"] == 0
        rows = _rows(dbm)
        assert len(rows) == 1
        assert rows[0]["strategy"] == "momentum_scalping"
        assert rows[0]["side"] == side.lower()
        assert rows[0]["close_reason"] == "position_gone_at_sync"
        assert rows[0]["pnl"] == pytest.approx(expected_pnl)

    @pytest.mark.asyncio
    async def test_api_sync_recording_failure_still_closes(
        self, dbm: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _open(dbm)
        monkeypatch.setattr(
            dbm, "save_closed_trade", MagicMock(side_effect=OSError("disk full"))
        )
        integration = api_server.BotIntegration()
        integration.database = dbm
        integration.pacifica_client = SimpleNamespace(get_positions=lambda: [])
        result = await integration.sync_positions()
        assert result["closed_count"] == 1 and result["errors"] == []
        assert dbm.get_position("BTC", "LONG") is None


# ----------------------------------------------------------------------
# Close path: migrated position manager
# ----------------------------------------------------------------------


class TestMigratedManagerCloses:
    @pytest.mark.parametrize(
        "reason", ["trailing_stop", "time_exit", "trend_reversal", "regime_exit"]
    )
    @pytest.mark.parametrize(
        "side,venue_side,fill,expected_pnl",
        [("long", "long", 108.0, 16.0), ("short", "short", 108.0, -16.0)],
    )
    def test_confirmed_fill_is_recorded_as_a_fill(
        self,
        dbm: Any,
        reason: str,
        side: str,
        venue_side: str,
        fill: float,
        expected_pnl: float,
    ) -> None:
        _open(dbm, side=side.upper(), strategy="mean_reversion")
        pos = {"symbol": "BTC", "side": side, "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient([_venue(venue_side, 2.0)], [_ack("c1", 2.0, fill)])
        manager = _manager(dbm, client, pos, price=999.0)

        manager.close_position("BTC", pos, reason)

        rows = _rows(dbm)
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy"] == "mean_reversion"
        assert row["close_reason"] == reason
        assert row["side"] == side
        assert row["quantity"] == 2.0
        assert row["exit_price"] == fill
        assert row["exit_price_source"] == "fill"
        assert row["pnl"] == pytest.approx(expected_pnl)
        assert len(client.orders) == 1

    def test_close_confirmed_by_position_snapshot_is_an_estimate(
        self, dbm: Any
    ) -> None:
        _open(dbm)
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient([_venue("long", 2.0), []], [_ack("c1")])
        _manager(dbm, client, pos, price=97.0).close_position(
            "BTC", pos, "trailing_stop"
        )
        row = _rows(dbm)[0]
        assert row["exit_price"] == 97.0
        assert row["exit_price_source"] == "estimate"
        assert row["pnl"] == pytest.approx(-6.0)

    def test_regime_exit_uses_the_reviewers_owner_key(self, dbm: Any) -> None:
        """The regime review passes an underscore-free strategy key."""
        _open(dbm, strategy=None, order_id=None)
        pos = {
            "symbol": "BTC",
            "side": "long",
            "qty": 2.0,
            "entry_price": 100.0,
            "strategy": "meanreversion",
        }
        client = ScriptedClient([_venue("long", 2.0)], [_ack("c1", 2.0, 99.0)])
        _manager(dbm, client, pos, 99.0).close_position("BTC", pos, "regime_exit")
        row = _rows(dbm)[0]
        assert row["strategy"] == "mean_reversion"
        assert row["close_reason"] == "regime_exit"

    def test_unowned_migrated_position_is_tagged_migrated(self, dbm: Any) -> None:
        _open(dbm, strategy=None, order_id=None)
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient([_venue("long", 2.0)], [_ack("c1", 2.0, 105.0)])
        _manager(dbm, client, pos, 105.0).close_position("BTC", pos, "time_exit")
        assert _rows(dbm)[0]["strategy"] == MIGRATED_STRATEGY_TAG

    def test_rejected_close_then_retry_gives_one_row(self, dbm: Any) -> None:
        _open(dbm)
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        reject = {"success": False, "error": "insufficient margin", "data": {}}
        client = ScriptedClient([_venue("long", 2.0)], [reject, _ack("c2", 2.0, 101.0)])
        manager = _manager(dbm, client, pos, 101.0)

        manager.close_position("BTC", pos, "trailing_stop")
        assert _rows(dbm) == []
        manager.close_position("BTC", pos, "trailing_stop")

        rows = _rows(dbm)
        assert len(rows) == 1 and rows[0]["quantity"] == 2.0
        assert len(client.orders) == 2

    def test_close_then_loop_sync_gives_one_row(self, dbm: Any) -> None:
        """The manager records; the sync that deletes the row adds nothing."""
        _open(dbm)
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient([_venue("long", 2.0)], [_ack("c1", 2.0, 106.0)])
        _manager(dbm, client, pos, 106.0).close_position("BTC", pos, "trailing_stop")
        assert dbm.get_position("BTC", "LONG") is not None

        _sync_bot(dbm, 50.0)._close_stale_local_positions(exchange_positions=[])

        rows = _rows(dbm)
        assert len(rows) == 1
        assert rows[0]["close_reason"] == "trailing_stop"
        assert rows[0]["exit_price"] == 106.0

    def test_already_flat_is_an_estimate_from_the_row(self, dbm: Any) -> None:
        _open(dbm, current=93.0)
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient([[]], [_ack("never")])
        _manager(dbm, client, pos, 93.0).close_position("BTC", pos, "trailing_stop")
        rows = _rows(dbm)
        assert len(rows) == 1 and client.orders == []
        assert rows[0]["close_reason"] == "trailing_stop:already_flat"
        assert rows[0]["exit_price_source"] == "estimate"
        assert rows[0]["pnl"] == pytest.approx(-14.0)

    def test_partial_take_profit_then_final_close(self, dbm: Any) -> None:
        """Two rows whose quantities sum to the full position."""
        _open(dbm)
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient(
            [_venue("long", 2.0), _venue("long", 1.0)],
            [_ack("tp1", 1.0, 112.0), _ack("c2", 1.0, 104.0)],
        )
        manager = _manager(dbm, client, pos, 104.0)

        assert manager._execute_partial_close("BTC", "long", 1.0, pos) is True
        manager.close_position("BTC", pos, "trailing_stop")

        rows = _rows(dbm)
        assert [row["close_reason"] for row in rows] == [
            "take_profit_partial",
            "trailing_stop",
        ]
        assert [row["quantity"] for row in rows] == [1.0, 1.0]
        assert sum(row["quantity"] for row in rows) == 2.0
        assert [row["pnl"] for row in rows] == [pytest.approx(12.0), pytest.approx(4.0)]
        assert all(row["exit_price_source"] == "fill" for row in rows)
        assert all(row["strategy"] == "mean_reversion" for row in rows)
        # Funding belongs to the episode: only the final row carries it.
        assert rows[0]["funding_pnl"] is None
        assert rows[1]["funding_pnl"] == pytest.approx(0.25)

    def test_partly_executed_full_close_then_retry(self, dbm: Any) -> None:
        _open(dbm)
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient(
            [_venue("long", 2.0), _venue("long", 0.5)],
            [_ack("c1", 1.5, 102.0), _ack("c2", 0.5, 101.0)],
        )
        manager = _manager(dbm, client, pos, 101.0)

        manager.close_position("BTC", pos, "time_exit")
        assert [row["quantity"] for row in _rows(dbm)] == [1.5]
        manager.close_position("BTC", pos, "time_exit")

        rows = _rows(dbm)
        assert [row["quantity"] for row in rows] == [1.5, 0.5]
        assert sum(row["quantity"] for row in rows) == 2.0

    def test_recording_failure_does_not_stop_the_close(
        self, dbm: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _open(dbm)
        monkeypatch.setattr(
            dbm, "save_closed_trade", MagicMock(side_effect=OSError("disk full"))
        )
        pos = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}
        client = ScriptedClient([_venue("long", 2.0)], [_ack("c1", 2.0, 101.0)])
        manager = _manager(dbm, client, pos, 101.0)

        manager.close_position("BTC", pos, "trailing_stop")

        risk: Any = manager.risk_manager
        assert risk.unregistered == [("BTC", "long", 2.0)]
        assert manager.get_pending_closes() == {}
        assert len(client.orders) == 1


# ----------------------------------------------------------------------
# The writer itself
# ----------------------------------------------------------------------


class TestWriter:
    def test_same_close_recorded_twice_is_one_row(self, dbm: Any) -> None:
        _open(dbm)
        first = record_position_gone(dbm, "BTC", "LONG")
        second = record_position_gone(dbm, "BTC", "LONG")
        assert isinstance(first, int) and second is None
        assert len(_rows(dbm)) == 1

    def test_restart_does_not_duplicate(self, db_mod: Any) -> None:
        """A new DatabaseManager (process restart) sees the same key."""
        before = db_mod.DatabaseManager()
        _open(before)
        assert record_position_gone(before, "BTC", "LONG") is not None

        after = db_mod.DatabaseManager()
        assert record_position_gone(after, "BTC", "LONG") is None
        assert len(_rows(after)) == 1

    def test_key_names_the_position_episode(self, dbm: Any, db_mod: Any) -> None:
        _open(dbm)
        row = dbm.get_position("BTC", "LONG")
        record_position_gone(dbm, "BTC", "LONG")
        with db_mod.get_db_connection() as conn:
            key = conn.execute("SELECT close_key FROM trades").fetchone()[0]
        assert key == (
            f"pacifica|{current_network()}|sub_1|BTC|long|"
            f"pos{row['id']}|{row['opened_at']}|entry-1|100.0|final"
        )

    def test_unique_index_backs_the_key(self, dbm: Any, db_mod: Any) -> None:
        _open(dbm)
        record_position_gone(dbm, "BTC", "LONG")
        with db_mod.get_db_connection() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO trades (symbol, asset_class, side, quantity, "
                    "entry_price, entry_time, close_key) "
                    "SELECT symbol, asset_class, side, quantity, entry_price, "
                    "entry_time, close_key FROM trades"
                )

    def test_fees_reduce_pnl_and_are_kept_separately(self, dbm: Any) -> None:
        record_closed_trade(
            dbm,
            symbol="ETH",
            side="short",
            close_reason="manual",
            quantity=1.0,
            entry_price=200.0,
            exit_price=190.0,
            exit_price_is_fill=True,
            fees=0.4,
            funding=-0.1,
            strategy=StrategyType.MA_CROSSOVER,
            network="mainnet",
            exchange="blofin",
        )
        row = _rows(dbm)[0]
        assert row["pnl"] == pytest.approx(9.6)
        assert row["commission"] == pytest.approx(0.4)
        assert row["funding_pnl"] == pytest.approx(-0.1)
        assert row["network"] == "mainnet" and row["exchange"] == "blofin"
        assert row["strategy"] == "ma_crossover"
        assert row["exit_price_source"] == "fill"

    def test_unpriceable_close_is_skipped_without_raising(self, dbm: Any) -> None:
        assert record_position_gone(dbm, "NOPE", "LONG") is None
        assert _rows(dbm) == []

    def test_never_raises_on_a_broken_database(self) -> None:
        broken = SimpleNamespace(
            get_position=MagicMock(side_effect=OSError("no db")),
            save_closed_trade=MagicMock(side_effect=OSError("no db")),
        )
        assert (
            record_closed_trade(
                broken,
                symbol="BTC",
                side="long",
                close_reason="local_stop",
                quantity=1.0,
                entry_price=100.0,
                exit_price=90.0,
            )
            is None
        )


# ----------------------------------------------------------------------
# Owner tagging at entry
# ----------------------------------------------------------------------


def _signal(strategy: StrategyType, side: OrderSide = OrderSide.BUY) -> Signal:
    return Signal(
        strategy=strategy,
        asset="BTC",
        asset_class=AssetClass.BTC_ETH,
        side=side,
        entry_price=100.0,
        stop_loss=95.0 if side == OrderSide.BUY else 105.0,
    )


def _entry_bot(dbm: Any, grid_active: bool = False) -> SimpleNamespace:
    bot = SimpleNamespace(
        db=dbm,
        signal_logger=MagicMock(),
        event_bus=MagicMock(),
        grid_lifecycle=SimpleNamespace(has_active_grid=lambda symbol: grid_active),
        market_regime=SimpleNamespace(
            get_current_regime=lambda symbol: SimpleNamespace(value="trending_strong")
        ),
    )

    def protect(signal: Signal, symbol: str, fill: OrderResult, price: float) -> None:
        side = "LONG" if signal.side == OrderSide.BUY else "SHORT"
        dbm.record_position_protection(
            symbol,
            side,
            {"entry_order_id": fill.order_id, "venue_stop_state": "attached"},
            quantity=fill.filled_quantity,
            entry_price=price,
        )

    bot._ensure_entry_protection = protect
    return _bind(bot, "_record_entry_fill", "_tag_position_owner")


def _fill(order_id: str = "e1") -> OrderResult:
    return OrderResult(
        success=True,
        accepted=True,
        order_id=order_id,
        status="filled",
        filled_quantity=2.0,
        avg_fill_price=100.0,
    )


class TestOwnerTagging:
    @pytest.mark.parametrize(
        "side,stored", [(OrderSide.BUY, "LONG"), (OrderSide.SELL, "SHORT")]
    )
    def test_entry_fill_tags_the_position(
        self, dbm: Any, side: OrderSide, stored: str
    ) -> None:
        bot = _entry_bot(dbm)
        signal = _signal(StrategyType.MEAN_REVERSION, side)
        bot._record_entry_fill(signal, "BTC", 2.0, 200.0, _fill(), False)

        row = dbm.get_position("BTC", stored)
        assert row["strategy"] == "mean_reversion"
        assert row["entry_regime"] == "trending_strong"

        _sync_bot(dbm, 101.0)._close_stale_local_positions(exchange_positions=[])
        closed = _rows(dbm)[0]
        assert closed["strategy"] == "mean_reversion"
        assert closed["regime"] == "trending_strong"

    def test_owner_survives_the_per_loop_upsert(self, dbm: Any) -> None:
        bot = _entry_bot(dbm)
        bot._record_entry_fill(
            _signal(StrategyType.MA_CROSSOVER), "BTC", 2.0, 200.0, _fill(), False
        )
        dbm.save_position(
            {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 2.0,
                "entry_price": 100.0,
                "current_price": 102.0,
                "opened_at": "now",
            }
        )
        assert dbm.get_position("BTC", "LONG")["strategy"] == "ma_crossover"

    def test_second_strategy_on_the_same_position_becomes_unknown(
        self, dbm: Any
    ) -> None:
        bot = _entry_bot(dbm)
        bot._record_entry_fill(
            _signal(StrategyType.MEAN_REVERSION), "BTC", 2.0, 200.0, _fill("e1"), False
        )
        bot._record_entry_fill(
            _signal(StrategyType.MA_CROSSOVER), "BTC", 2.0, 200.0, _fill("e2"), False
        )
        assert dbm.get_position("BTC", "LONG")["strategy"] == UNKNOWN_STRATEGY_TAG

    def test_entry_while_a_grid_is_active_is_unknown(self, dbm: Any) -> None:
        bot = _entry_bot(dbm, grid_active=True)
        bot._record_entry_fill(
            _signal(StrategyType.MEAN_REVERSION), "BTC", 2.0, 200.0, _fill(), False
        )
        assert dbm.get_position("BTC", "LONG")["strategy"] == UNKNOWN_STRATEGY_TAG

    def test_tagging_failure_does_not_break_the_entry(
        self, dbm: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bot = _entry_bot(dbm)
        monkeypatch.setattr(
            dbm, "tag_position_owner", MagicMock(side_effect=OSError("locked"))
        )
        bot._record_entry_fill(
            _signal(StrategyType.MEAN_REVERSION), "BTC", 2.0, 200.0, _fill(), False
        )
        bot.event_bus.publish_event.assert_called_once()


# ----------------------------------------------------------------------
# End to end: the real Kelly sizer reads what the writer wrote
# ----------------------------------------------------------------------


def _write_closes(
    dbm: Any,
    strategy: Optional[str],
    exits: List[float],
    start: datetime,
    network: Optional[str] = None,
) -> None:
    """Open a position and let the loop sync find it gone, once per exit."""
    for index, exit_price in enumerate(exits):
        _open(dbm, strategy=strategy, order_id=f"{strategy}-{start.minute}-{index}")
        row = dbm.get_position("BTC", "LONG")
        record_closed_trade(
            dbm,
            symbol="BTC",
            side="LONG",
            close_reason="position_gone_at_sync",
            position=row,
            exit_price=exit_price,
            exit_time=start + timedelta(minutes=index),
            network=network,
        )
        dbm.close_position("BTC", "LONG")


class TestKellyEndToEnd:
    def test_fifty_recorded_closes_give_kelly_its_history(self, dbm: Any) -> None:
        base = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        # Older ten: all wins (fall outside the 50-trade window).
        _write_closes(dbm, "mean_reversion", [110.0] * 10, base)
        # Latest fifty: 30 wins of +20 (qty 2), 20 losses of -10.
        _write_closes(
            dbm,
            "mean_reversion",
            [110.0] * 30 + [95.0] * 20,
            base + timedelta(days=1),
        )
        # Noise that must not count: unknown owner, another strategy,
        # and mean_reversion rows from the other network.
        _write_closes(dbm, None, [50.0] * 60, base + timedelta(days=2))
        _write_closes(dbm, "ma_crossover", [50.0] * 55, base + timedelta(days=3))
        other = "mainnet" if current_network() == "testnet" else "testnet"
        _write_closes(
            dbm, "mean_reversion", [50.0] * 55, base + timedelta(days=4), other
        )

        sizer = KellyPositionSizer(db=dbm, kelly_fraction=0.5, min_trades=50)
        stats = sizer.get_strategy_stats(StrategyType.MEAN_REVERSION)

        assert stats["total_trades"] == 50
        assert stats["win_rate"] == pytest.approx(0.6)
        assert stats["avg_win"] == pytest.approx(20.0)
        assert stats["avg_loss"] == pytest.approx(10.0)

        evaluation = sizer.evaluate(_signal(StrategyType.MEAN_REVERSION), 10_000.0)
        assert evaluation.status == KELLY_OK
        assert evaluation.total_trades == 50
        assert evaluation.raw_kelly_pct == pytest.approx(0.4)
        assert evaluation.quantity > 0

        # The other strategy sees only its own rows; breakout has none.
        crossover = sizer.get_strategy_stats(StrategyType.MA_CROSSOVER)
        assert crossover["total_trades"] == 50 and crossover["win_rate"] == 0.0
        assert sizer.get_strategy_stats(StrategyType.BREAKOUT)["total_trades"] == 0

        unknown = _rows(dbm, strategy=UNKNOWN_STRATEGY_TAG)
        assert len(unknown) == 60

    def test_fewer_than_fifty_is_still_insufficient(self, dbm: Any) -> None:
        base = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        _write_closes(dbm, "mean_reversion", [110.0] * 49, base)
        _write_closes(dbm, None, [110.0] * 20, base + timedelta(days=1))
        sizer = KellyPositionSizer(db=dbm, kelly_fraction=0.5, min_trades=50)
        evaluation = sizer.evaluate(_signal(StrategyType.MEAN_REVERSION), 10_000.0)
        assert evaluation.status == "insufficient_history"
        assert evaluation.total_trades == 49

    def test_legacy_uppercase_rows_still_count(self, dbm: Any) -> None:
        """Rows tagged with the enum NAME are read, not rewritten."""
        stamp = datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat()
        for _ in range(3):
            dbm.save_trade(
                {
                    "symbol": "SUI",
                    "side": "LONG",
                    "quantity": 1.0,
                    "entry_price": 100.0,
                    "entry_time": stamp,
                    "exit_time": stamp,
                    "pnl": 2.0,
                    "strategy": "MEAN_REVERSION",
                    "status": "closed",
                }
            )
        store = TradeStore(db=dbm)
        assert len(store.get_closed_trades(strategy="mean_reversion")) == 3
        assert len(store.get_closed_trades(strategy="MEAN_REVERSION")) == 3
        assert {t["strategy"] for t in store.get_closed_trades()} == {"MEAN_REVERSION"}
        sizer = KellyPositionSizer(db=dbm, min_trades=50)
        stats = sizer.get_strategy_stats(StrategyType.MEAN_REVERSION)
        assert stats["total_trades"] == 3


# ----------------------------------------------------------------------
# Schema upgrade of an existing database
# ----------------------------------------------------------------------

LEGACY_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    pnl REAL DEFAULT 0,
    commission REAL DEFAULT 0,
    strategy TEXT,
    status TEXT DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
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
);
INSERT INTO trades (symbol, asset_class, side, quantity, entry_price, exit_price,
                    entry_time, exit_time, pnl, strategy, status)
VALUES ('SUI', 'crypto', 'LONG', 3.0, 1.5, 1.6, '2026-01-01T00:00:00',
        '2026-01-02T00:00:00', 0.3, 'MEAN_REVERSION', 'closed');
INSERT INTO positions (symbol, asset_class, side, quantity, entry_price, opened_at)
VALUES ('ETH', 'crypto', 'LONG', 1.0, 2000.0, '2026-01-03T00:00:00');
"""


class TestSchemaUpgrade:
    def test_existing_database_is_upgraded_in_place(self, db_mod: Any) -> None:
        raw = sqlite3.connect(db_mod.DATABASE_PATH)
        raw.executescript(LEGACY_SCHEMA)
        raw.commit()
        raw.close()

        db_mod.init_database()
        db_mod.init_database()  # second run is a no-op

        with db_mod.get_db_connection() as conn:
            trade_cols = db_mod._existing_columns(conn, "trades")
            position_cols = db_mod._existing_columns(conn, "positions")
            assert db_mod._ensure_closed_trade_schema(conn) == []
            legacy = conn.execute(
                "SELECT symbol, quantity, pnl, strategy, status, close_key, network "
                "FROM trades"
            ).fetchall()
        assert {name for name, _ in db_mod.TRADE_CLOSE_COLUMNS} <= trade_cols
        assert {"strategy", "entry_regime"} <= position_cols
        assert [tuple(row) for row in legacy] == [
            ("SUI", 3.0, 0.3, "MEAN_REVERSION", "closed", None, None)
        ]

        # The pre-existing position can be closed and recorded (unknown owner).
        dbm = db_mod.DatabaseManager()
        assert record_position_gone(dbm, "ETH", "LONG") is not None
        rows = _rows(dbm)
        assert [row["strategy"] for row in rows] == ["MEAN_REVERSION", "unknown"]
        assert rows[0]["network"] is None and rows[0]["close_reason"] is None
