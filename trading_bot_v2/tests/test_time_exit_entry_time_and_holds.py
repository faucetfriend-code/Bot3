"""Entry time on the positions row, and the max holds the backtest honours.

Change A: every writer of ``positions.opened_at`` stores a timezone-aware
UTC ISO-8601 time, set once when the row is created.  The loop sync used to
store the literal ``now`` when the venue payload had no timestamp, which
left the max-hold time exit inactive for that row.

Change B: the live time exit enforces the max hold of every strategy whose
signals carry ``indicators["time_exit_hours"]`` (the only max hold the
backtest engine reads): session_range_breakout and vwap_pullback from their
configured value, calendar_flow from the per-signal value stored on the
positions row at entry.

Real here: ``DatabaseManager`` on a temp SQLite file, ``TradingBot``'s own
sync / entry-fill / tagging methods (bound onto a stand-in),
``PositionReconciler``, ``BotIntegration.sync_positions``,
``MigratedPositionManager``, ``BacktestEngine`` on a ``SimulatedExchange``,
the real strategies.  The exchange client is spec'd on ``PacificaClient``.
The manager's clock is shifted instead of sleeping.
"""

import asyncio
import sqlite3
import time as real_time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock, create_autospec

import pytest

import trading_bot_v2.database as db_module
import trading_bot_v2.migrated_position_manager as mpm_module
from trading_bot_v2 import api_server
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.config import AssetClass, StrategyType
from trading_bot_v2.database import normalize_opened_at
from trading_bot_v2.event_system import EventBus
from trading_bot_v2.history.closed_trades import build_close_key
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.order_result import OrderResult
from trading_bot_v2.pacifica_client import PacificaClient
from trading_bot_v2.position_reconciler import PositionReconciler
from trading_bot_v2.strategies.calendar_flow import CalendarFlowStrategy
from trading_bot_v2.strategies.session_range_breakout import (
    SessionRangeBreakoutStrategy,
)
from trading_bot_v2.strategies.vwap_pullback import VWAPPullbackStrategy
from trading_bot_v2.strategy_manager import StrategyManager
from trading_bot_v2.tests.test_calendar_flow import make_4h
from trading_bot_v2.tests.test_execution_policy import build_engine
from trading_bot_v2.tests import test_regime_and_time_exits as exits
from trading_bot_v2.tests.test_regime_and_time_exits import (
    SYMBOL,
    Rig,
    set_position_column,
)
from trading_bot_v2.trading_bot import TradingBot, _breaker_block_reason

HOUR = 3600.0

# The fixtures of the PR #22 test module (real temp-SQLite DatabaseManager,
# loguru capture, the scenario rig), made visible to this module.
dbm = exits.dbm
log_lines = exits.log_lines
rig = exits.rig


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class Clock:
    """Stands in for the ``time`` module inside the manager; shiftable."""

    def __init__(self) -> None:
        self.offset = 0.0

    def time(self) -> float:
        return real_time.time() + self.offset

    def advance(self, hours: float) -> None:
        self.offset += hours * HOUR

    def __getattr__(self, name: str) -> Any:
        return getattr(real_time, name)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    shifted = Clock()
    monkeypatch.setattr(mpm_module, "time", shifted)
    return shifted


def parse_utc(value: Any) -> datetime:
    """Parse a stored value; fail unless it is a tz-aware UTC ISO time."""
    assert isinstance(value, str), value
    stamp = datetime.fromisoformat(value)
    assert stamp.tzinfo is not None, value
    assert stamp.utcoffset() == timedelta(0), value
    return stamp


def is_recent(stamp: datetime, seconds: float = 30.0) -> bool:
    return abs((datetime.now(timezone.utc) - stamp).total_seconds()) < seconds


def venue_position(**extra: Any) -> Dict[str, Any]:
    """A Pacifica position payload; no timestamp unless one is passed."""
    payload = {"symbol": SYMBOL, "side": "bid", "amount": "2.0", "entry_price": "100"}
    payload.update(extra)
    return payload


def bind(bot: SimpleNamespace, *names: str) -> SimpleNamespace:
    for name in names:
        setattr(bot, name, getattr(TradingBot, name).__get__(bot))
    return bot


def loop_sync_bot(database: Any, payload: List[Dict[str, Any]]) -> SimpleNamespace:
    """The trading loop's position sync against a PacificaClient-spec client."""
    client = create_autospec(PacificaClient, instance=True)
    client.get_positions.return_value = payload
    bot = SimpleNamespace(
        db=database,
        client=client,
        _get_ticker_ws=lambda symbol: {"last": 100.0},
        _current_price_for=lambda symbol: 100.0,
        _maybe_run_full_reconciliation=lambda exchange_positions: None,
    )
    return bind(bot, "_update_positions", "_close_stale_local_positions")


def entry_bot(database: Any, grid_active: bool = False) -> SimpleNamespace:
    """The entry-fill recorder; the protection step creates the row."""
    bot = SimpleNamespace(
        db=database,
        signal_logger=MagicMock(),
        event_bus=MagicMock(),
        grid_lifecycle=SimpleNamespace(has_active_grid=lambda symbol: grid_active),
        market_regime=SimpleNamespace(
            get_current_regime=lambda symbol: SimpleNamespace(value="ranging_calm")
        ),
    )

    def protect(signal: Signal, symbol: str, fill: OrderResult, price: float) -> None:
        database.record_position_protection(
            symbol,
            "LONG" if signal.side == OrderSide.BUY else "SHORT",
            {"entry_order_id": fill.order_id, "venue_stop_state": "attached"},
            quantity=fill.filled_quantity,
            entry_price=price,
        )

    bot._ensure_entry_protection = protect
    return bind(bot, "_record_entry_fill", "_tag_position_owner")


def fill(order_id: str = "entry-1") -> OrderResult:
    return OrderResult(
        success=True,
        accepted=True,
        order_id=order_id,
        status="filled",
        filled_quantity=2.0,
        avg_fill_price=100.0,
    )


def hand_signal(strategy: StrategyType, hours: Optional[float]) -> Signal:
    indicators: Dict[str, Any] = {}
    if hours is not None:
        indicators["time_exit_hours"] = hours
    return Signal(
        strategy=strategy,
        asset=SYMBOL,
        asset_class=AssetClass.BTC_ETH,
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        quantity=2.0,
        confidence=0.9,
        indicators=indicators,
    )


def enter(rig: Rig, signal: Signal, order_id: str = "entry-1") -> Dict[str, Any]:
    """Record an entry fill the way the bot does and open it on the venue."""
    entry_bot(rig.dbm)._record_entry_fill(
        signal, SYMBOL, 2.0, 200.0, fill(order_id), False
    )
    rig.venue.quantities[(SYMBOL, "long")] = 2.0
    row = rig.dbm.get_position(SYMBOL, "LONG")
    assert row is not None
    return dict(row)


def restart(rig: Rig) -> Rig:
    """New DatabaseManager, manager and client on the same database file."""
    db_module._data_cache.invalidate("positions_all")
    fresh = Rig(db_module.DatabaseManager())
    fresh.venue.quantities = dict(rig.venue.quantities)
    return fresh


#: (strategy type, canonical tag, hold carried by the entry signal)
ENFORCED = [
    pytest.param(
        StrategyType.SESSION_RANGE_BREAKOUT, "session_range_breakout", 4.0, id="orb"
    ),
    pytest.param(StrategyType.VWAP_PULLBACK, "vwap_pullback", 24.0, id="vwap_pullback"),
    pytest.param(StrategyType.CALENDAR_FLOW, "calendar_flow", 72.0, id="calendar_flow"),
]


# ----------------------------------------------------------------------
# Change A: opened_at
# ----------------------------------------------------------------------


class TestNormalizeOpenedAt:
    NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize(
        "value",
        [None, "now", "", " ", "garbage", 0, -5, 5, "5", True, float("inf")],
    )
    def test_unreadable_or_implausible_becomes_first_seen(self, value: Any) -> None:
        assert normalize_opened_at(value, now=self.NOW) == self.NOW.isoformat()

    def test_future_time_becomes_first_seen(self) -> None:
        future = self.NOW + timedelta(minutes=1)
        assert normalize_opened_at(future, now=self.NOW) == self.NOW.isoformat()

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("2026-10-10T08:00:00+00:00", "2026-10-10T08:00:00+00:00"),
            ("2026-10-10T08:00:00Z", "2026-10-10T08:00:00+00:00"),
            ("2026-10-10 08:00:00", "2026-10-10T08:00:00+00:00"),
            ("2026-10-10T10:00:00+02:00", "2026-10-10T08:00:00+00:00"),
            (1791619200, "2026-10-10T08:00:00+00:00"),
            (1791619200000, "2026-10-10T08:00:00+00:00"),
            ("1791619200000", "2026-10-10T08:00:00+00:00"),
            (
                datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc),
                "2026-10-10T08:00:00+00:00",
            ),
        ],
    )
    def test_readable_past_time_is_kept_as_utc(self, value: Any, expected: str) -> None:
        assert normalize_opened_at(value, now=self.NOW) == expected

    def test_default_clock_is_utc_now(self) -> None:
        assert is_recent(parse_utc(normalize_opened_at("now")))


class TestEveryWriterStoresUtc:
    def _stored(self, database: Any, side: str = "LONG") -> datetime:
        row = database.get_position(SYMBOL, side)
        assert row is not None
        return parse_utc(row["opened_at"])

    @pytest.mark.parametrize("value", ["now", "garbage", None, 0])
    def test_save_position_insert(self, dbm: Any, value: Any) -> None:
        dbm.save_position(
            {
                "symbol": SYMBOL,
                "side": "LONG",
                "quantity": 2.0,
                "entry_price": 100.0,
                "current_price": 100.0,
                "opened_at": value,
            }
        )
        assert is_recent(self._stored(dbm))

    def test_save_position_insert_without_the_key(self, dbm: Any) -> None:
        dbm.save_position(
            {
                "symbol": SYMBOL,
                "side": "LONG",
                "quantity": 2.0,
                "entry_price": 100.0,
                "current_price": 100.0,
            }
        )
        assert is_recent(self._stored(dbm))

    def test_entry_fill_path(self, dbm: Any) -> None:
        entry_bot(dbm)._record_entry_fill(
            hand_signal(StrategyType.MEAN_REVERSION, None),
            SYMBOL,
            2.0,
            200.0,
            fill(),
            False,
        )
        assert is_recent(self._stored(dbm))

    def test_loop_sync_without_a_venue_timestamp_stores_first_seen(
        self, dbm: Any
    ) -> None:
        bot = loop_sync_bot(dbm, [venue_position()])

        bot._update_positions()

        assert is_recent(self._stored(dbm))
        assert dbm.get_position(SYMBOL, "LONG")["opened_at"] != "now"

    def test_loop_sync_hands_the_database_a_utc_time(self) -> None:
        """The caller itself, independent of save_position's normalisation."""
        database = MagicMock()
        database.get_positions.return_value = []

        loop_sync_bot(database, [venue_position()])._update_positions()

        sent = database.save_position.call_args.args[0]["opened_at"]
        assert sent != "now" and is_recent(parse_utc(sent))

    def test_loop_sync_prefers_the_venue_open_time(self, dbm: Any) -> None:
        opened = datetime.now(timezone.utc) - timedelta(hours=7)
        millis = int(opened.timestamp() * 1000)
        bot = loop_sync_bot(dbm, [venue_position(created_at=millis)])

        bot._update_positions()

        stored = self._stored(dbm)
        assert abs((stored - opened).total_seconds()) < 0.002

    def test_loop_sync_ignores_a_future_venue_time(self, dbm: Any) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=1)
        bot = loop_sync_bot(
            dbm, [venue_position(created_at=int(future.timestamp() * 1000))]
        )

        bot._update_positions()

        assert is_recent(self._stored(dbm))

    def test_reconciler_adoption(self, dbm: Any) -> None:
        report = PositionReconciler(dbm, EventBus()).reconcile(
            exchange_positions=[venue_position()], db_positions=[]
        )

        assert report.adopted_from_exchange == 1
        assert is_recent(self._stored(dbm))

    def test_api_positions_sync(self, dbm: Any) -> None:
        client = create_autospec(PacificaClient, instance=True)
        client.get_positions.return_value = [venue_position()]
        integration = api_server.BotIntegration()
        integration.database = dbm
        integration.pacifica_client = client

        result = asyncio.run(integration.sync_positions())

        assert result["errors"] == [] and result["synced_count"] == 1
        assert is_recent(self._stored(dbm))

    def test_api_positions_sync_with_a_venue_epoch(self, dbm: Any) -> None:
        opened = datetime.now(timezone.utc) - timedelta(hours=3)
        client = create_autospec(PacificaClient, instance=True)
        client.get_positions.return_value = [
            venue_position(created_at=int(opened.timestamp() * 1000))
        ]
        integration = api_server.BotIntegration()
        integration.database = dbm
        integration.pacifica_client = client

        asyncio.run(integration.sync_positions())

        assert abs((self._stored(dbm) - opened).total_seconds()) < 0.002

    def test_json_backup_restore_of_a_legacy_value(self, dbm: Any) -> None:
        """The restore path calls save_position with whatever the backup holds."""
        dbm.save_position(
            {
                "symbol": SYMBOL,
                "side": "SHORT",
                "quantity": 1.0,
                "entry_price": 100.0,
                "current_price": 100.0,
                "opened_at": "now",
            }
        )
        assert is_recent(self._stored(dbm, "SHORT"))


class TestUpdateNeverMovesOpenedAt:
    def test_every_update_path_leaves_it_alone(self, dbm: Any) -> None:
        original = "2026-10-01T00:00:00+00:00"
        loop_sync_bot(dbm, [venue_position(created_at=original)])._update_positions()
        assert dbm.get_position(SYMBOL, "LONG")["opened_at"] == original

        # Loop sync again: a different venue time, then none at all.
        loop_sync_bot(
            dbm, [venue_position(created_at="2026-10-05T00:00:00+00:00")]
        )._update_positions()
        loop_sync_bot(dbm, [venue_position(amount="3.0")])._update_positions()
        # Reconciler correction (quantity mismatch -> save_position).
        db_module._data_cache.invalidate("positions_all")
        report = PositionReconciler(dbm, EventBus()).reconcile(
            exchange_positions=[venue_position(amount="5.0")],
            db_positions=dbm.get_positions(),
        )
        assert report.discrepancies_found == 1
        # Entry fill on the existing row (protection update + owner tag).
        entry_bot(dbm)._record_entry_fill(
            hand_signal(StrategyType.CALENDAR_FLOW, 72.0),
            SYMBOL,
            2.0,
            200.0,
            fill(),
            False,
        )
        # Direct upsert carrying another time.
        dbm.save_position(
            {
                "symbol": SYMBOL,
                "side": "LONG",
                "quantity": 6.0,
                "entry_price": 100.0,
                "current_price": 100.0,
                "opened_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        row = dbm.get_position(SYMBOL, "LONG")
        assert row["opened_at"] == original
        assert row["quantity"] == 6.0 and row["strategy"] == "calendar_flow"

    def test_update_does_not_repair_a_legacy_value_with_a_guess(self, dbm: Any) -> None:
        loop_sync_bot(dbm, [venue_position()])._update_positions()
        set_position_column(SYMBOL, "LONG", "opened_at", "now")

        loop_sync_bot(dbm, [venue_position()])._update_positions()

        assert dbm.get_position(SYMBOL, "LONG")["opened_at"] == "now"


class TestSyncCreatedRowBecomesEligible:
    def test_time_exit_runs_max_hold_after_first_seen(
        self, rig: Rig, clock: Clock
    ) -> None:
        """Delayed fill: the sync creates the row before the fill is recorded."""
        loop_sync_bot(rig.dbm, [venue_position()])._update_positions()
        row = enter(rig, hand_signal(StrategyType.SESSION_RANGE_BREAKOUT, 4.0))
        assert row["strategy"] == "session_range_breakout"
        first_seen = parse_utc(row["opened_at"])
        assert is_recent(first_seen)

        clock.advance(3.9)
        assert rig.manage()["positions_closed"] == 0
        assert rig.untouched()

        clock.advance(0.2)  # 4.1h after first seen
        assert rig.manage()["positions_closed"] == 1
        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "time_exit"

    def test_before_the_change_the_same_row_was_never_eligible(
        self, rig: Rig, clock: Clock, log_lines: List[str]
    ) -> None:
        """A row still holding the literal is left alone, however old."""
        loop_sync_bot(rig.dbm, [venue_position()])._update_positions()
        enter(rig, hand_signal(StrategyType.SESSION_RANGE_BREAKOUT, 4.0))
        set_position_column(SYMBOL, "LONG", "opened_at", "now")

        clock.advance(5000.0)
        rig.manage()
        rig.manage()
        restart(rig).manage()

        assert rig.untouched()
        assert (
            len([m for m in log_lines if "TIME EXIT inactive" in m]) == 2
        )  # 1/process


class TestLegacyRowClears:
    def test_a_new_position_on_the_symbol_gets_a_real_time(self, dbm: Any) -> None:
        loop_sync_bot(dbm, [venue_position()])._update_positions()
        set_position_column(SYMBOL, "LONG", "opened_at", "now")

        loop_sync_bot(dbm, [])._update_positions()  # venue flat -> row closed
        assert dbm.get_position(SYMBOL, "LONG") is None
        loop_sync_bot(dbm, [venue_position()])._update_positions()

        assert is_recent(parse_utc(dbm.get_position(SYMBOL, "LONG")["opened_at"]))


class TestCloseKeyStability:
    @staticmethod
    def _key(database: Any) -> str:
        row = database.get_position(SYMBOL, "LONG")
        return build_close_key(
            "pacifica", "testnet", "sub_1", SYMBOL, "long", row, None, 100.0, "final"
        )

    def test_key_is_stable_across_a_restart(self, dbm: Any) -> None:
        loop_sync_bot(dbm, [venue_position()])._update_positions()
        before = self._key(dbm)

        db_module._data_cache.invalidate("positions_all")
        restarted = db_module.DatabaseManager()
        with db_module.get_db_connection() as conn:
            assert db_module._ensure_closed_trade_schema(conn) == []
        for _ in range(3):
            loop_sync_bot(restarted, [venue_position()])._update_positions()

        assert self._key(restarted) == before
        assert "|now|" not in before

    def test_legacy_row_key_is_unchanged_by_this_change(self, dbm: Any) -> None:
        loop_sync_bot(dbm, [venue_position()])._update_positions()
        set_position_column(SYMBOL, "LONG", "opened_at", "now")
        before = self._key(dbm)
        assert "|now|" in before  # the key such a row has always had

        loop_sync_bot(
            db_module.DatabaseManager(), [venue_position()]
        )._update_positions()

        assert self._key(dbm) == before

    def test_recorded_close_keeps_its_key(self, rig: Rig, clock: Clock) -> None:
        enter(rig, hand_signal(StrategyType.SESSION_RANGE_BREAKOUT, 4.0))
        clock.advance(4.1)
        rig.manage()
        recorded = self._trade_keys()
        assert len(recorded) == 1

        fresh = restart(rig)
        loop_sync_bot(fresh.dbm, [])._update_positions()  # row closed locally
        loop_sync_bot(fresh.dbm, [venue_position()])._update_positions()
        fresh.manage()

        assert self._trade_keys() == recorded

    @staticmethod
    def _trade_keys() -> List[str]:
        with db_module.get_db_connection() as conn:
            rows = conn.execute("SELECT close_key FROM trades ORDER BY id").fetchall()
        return [str(row[0]) for row in rows]


# ----------------------------------------------------------------------
# Change B: max holds the backtest honours
# ----------------------------------------------------------------------


class TestHoldIsStoredAtEntry:
    def test_per_signal_hold_is_written_with_the_tag(self, rig: Rig) -> None:
        row = enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, 72.5))

        assert row["strategy"] == "calendar_flow"
        assert row["max_hold_hours"] == 72.5
        assert is_recent(parse_utc(row["max_hold_from"]))

    def test_signal_without_a_hold_stores_none(self, rig: Rig) -> None:
        row = enter(rig, hand_signal(StrategyType.MEAN_REVERSION, None))

        assert row["strategy"] == "mean_reversion"
        assert row["max_hold_hours"] is None and row["max_hold_from"] is None

    @pytest.mark.parametrize("bad", [0, -3.0, float("nan"), float("inf"), "x", True])
    def test_unusable_hold_is_not_stored(self, dbm: Any, bad: Any) -> None:
        loop_sync_bot(dbm, [venue_position()])._update_positions()

        assert dbm.tag_position_owner(SYMBOL, "LONG", "calendar_flow", None, bad)

        row = dbm.get_position(SYMBOL, "LONG")
        assert row["strategy"] == "calendar_flow"
        assert row["max_hold_hours"] is None and row["max_hold_from"] is None

    def test_grid_active_entry_is_unknown_and_stores_no_hold(self, dbm: Any) -> None:
        entry_bot(dbm, grid_active=True)._record_entry_fill(
            hand_signal(StrategyType.CALENDAR_FLOW, 72.0),
            SYMBOL,
            2.0,
            200.0,
            fill(),
            False,
        )

        row = dbm.get_position(SYMBOL, "LONG")
        assert row["strategy"] == "unknown"
        assert row["max_hold_hours"] is None and row["max_hold_from"] is None

    @pytest.mark.parametrize("second_hours", [10.0, 200.0])
    def test_second_entry_by_the_same_strategy_keeps_the_first_hold(
        self, rig: Rig, second_hours: float
    ) -> None:
        """The backtest engine also keeps the first entry's time exit."""
        first = enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, 72.0))

        second = enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, second_hours), "e2")

        assert second["strategy"] == "calendar_flow"
        assert second["max_hold_hours"] == 72.0
        assert second["max_hold_from"] == first["max_hold_from"]
        assert second["opened_at"] == first["opened_at"]

    def test_second_entry_does_not_give_an_owned_row_a_hold(self, rig: Rig) -> None:
        """A row tagged before the columns existed keeps no hold."""
        rig.open(strategy="calendar_flow", age_hours=100.0)

        row = enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, 5.0), "e2")

        assert row["strategy"] == "calendar_flow"
        assert row["max_hold_hours"] is None and row["max_hold_from"] is None

    def test_entry_by_another_strategy_makes_it_unknown(
        self, rig: Rig, clock: Clock
    ) -> None:
        enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, 72.0))
        row = enter(rig, hand_signal(StrategyType.VWAP_PULLBACK, 24.0), "e2")
        assert row["strategy"] == "unknown"

        clock.advance(500.0)
        rig.manage()

        assert rig.untouched()


class TestEnforcedHolds:
    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_older_than_hold_is_closed_once(
        self,
        rig: Rig,
        clock: Clock,
        log_lines: List[str],
        strategy: StrategyType,
        tag: str,
        hours: float,
    ) -> None:
        enter(rig, hand_signal(strategy, hours))
        clock.advance(hours + 0.1)

        result = rig.manage()

        assert result["positions_closed"] == 1
        assert len(rig.venue.orders) == 1
        assert rig.venue.orders[0]["reduce_only"] is True
        rows = rig.rows()
        assert len(rows) == 1
        assert rows[0]["close_reason"] == "time_exit"
        assert rows[0]["strategy"] == tag
        assert rows[0]["quantity"] == 2.0
        decisions = [line for line in log_lines if "TIME EXIT decision" in line]
        assert len(decisions) == 1
        assert f"strategy={tag}" in decisions[0]
        assert f"max_hold={hours}h" in decisions[0]

        second = rig.manage()  # second pass after the close does nothing
        assert second["positions_closed"] == 0
        assert len(rig.venue.orders) == 1 and len(rig.rows()) == 1

    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_younger_than_hold_is_untouched(
        self, rig: Rig, clock: Clock, strategy: StrategyType, tag: str, hours: float
    ) -> None:
        enter(rig, hand_signal(strategy, hours))
        clock.advance(hours - 0.1)

        assert rig.manage()["positions_closed"] == 0
        assert rig.untouched()

    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_hold_is_honoured_after_a_restart(
        self, rig: Rig, clock: Clock, strategy: StrategyType, tag: str, hours: float
    ) -> None:
        enter(rig, hand_signal(strategy, hours))

        fresh = restart(rig)
        clock.advance(hours - 0.1)
        assert fresh.manage()["positions_closed"] == 0
        assert fresh.untouched()

        again = restart(fresh)
        clock.advance(0.2)
        assert again.manage()["positions_closed"] == 1
        rows = again.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "time_exit"
        assert rows[0]["strategy"] == tag

    def test_vwap_pullback_hours_are_read_from_the_environment(
        self, dbm: Any, clock: Clock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VWAP_PB_TIME_EXIT_HOURS", "2.5")
        rig = Rig(dbm)
        enter(rig, hand_signal(StrategyType.VWAP_PULLBACK, 2.5))

        clock.advance(2.4)
        assert rig.manage()["positions_closed"] == 0
        clock.advance(0.2)
        assert rig.manage()["positions_closed"] == 1

    def test_existing_vwap_pullback_row_is_measured_from_opened_at(
        self, rig: Rig
    ) -> None:
        """First-deployment case: a row tagged before this change."""
        rig.open(strategy="vwap_pullback", age_hours=24.1)

        assert rig.manage()["positions_closed"] == 1
        assert rig.rows()[0]["close_reason"] == "time_exit"

    def test_existing_vwap_pullback_row_under_24h_is_untouched(self, rig: Rig) -> None:
        rig.open(strategy="vwap_pullback", age_hours=23.9)

        assert rig.manage()["positions_closed"] == 0
        assert rig.untouched()

    def test_calendar_flow_row_without_a_stored_hold_is_untouched(
        self, rig: Rig, clock: Clock, log_lines: List[str]
    ) -> None:
        """First-deployment case: no deadline is guessed for such a row."""
        rig.open(strategy="calendar_flow", age_hours=5000.0)
        clock.advance(5000.0)

        rig.manage()
        rig.manage()

        assert rig.untouched()
        notes = [m for m in log_lines if "TIME EXIT inactive" in m]
        assert len(notes) == 1 and "no max hold stored" in notes[0]

    @pytest.mark.parametrize(
        "column,value",
        [
            ("max_hold_hours", None),
            ("max_hold_from", None),
            ("max_hold_from", "now"),
            ("max_hold_hours", 0),
            ("max_hold_hours", -4.0),
        ],
    )
    def test_calendar_flow_row_with_half_a_hold_is_untouched(
        self, rig: Rig, clock: Clock, column: str, value: Any
    ) -> None:
        enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, 72.0))
        set_position_column(SYMBOL, "LONG", column, value)
        clock.advance(5000.0)

        rig.manage()

        assert rig.untouched()

    def test_calendar_flow_hold_runs_from_the_entry_not_from_opened_at(
        self, rig: Rig, clock: Clock
    ) -> None:
        """An untagged row that predates the entry is not closed early."""
        rig.open(strategy=None, age_hours=1000.0)
        row = enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, 72.0))
        assert row["strategy"] == "calendar_flow"

        clock.advance(71.9)
        assert rig.manage()["positions_closed"] == 0
        clock.advance(0.2)
        assert rig.manage()["positions_closed"] == 1

    def test_registered_calendar_flow_position_uses_the_stored_hold(
        self, rig: Rig, clock: Clock
    ) -> None:
        """Registry times (entry_time / migrated_at) never stand in for it."""
        enter(rig, hand_signal(StrategyType.CALENDAR_FLOW, 72.0))
        rig.risk.register_migrated_position(
            SYMBOL, {"side": "long", "qty": 2.0, "entry_price": 100.0, "has_stop": True}
        )
        rig.risk.migrated_positions[SYMBOL][0]["migrated_at"] = (
            real_time.time() - 500 * HOUR
        )

        assert rig.manager._check_time_exit(SYMBOL, {"side": "long"}) is False
        clock.advance(72.1)
        assert rig.manager._check_time_exit(SYMBOL, {"side": "long"}) is True


class TestNoTimeExitWithoutAnOwner:
    @pytest.mark.parametrize("tag", ["unknown", "migrated", None])
    def test_unowned_row_with_a_stored_hold_is_untouched(
        self, rig: Rig, clock: Clock, tag: Optional[str]
    ) -> None:
        rig.open(strategy=None, age_hours=500.0)
        set_position_column(SYMBOL, "LONG", "strategy", tag)
        set_position_column(SYMBOL, "LONG", "max_hold_hours", 1.0)
        set_position_column(
            SYMBOL, "LONG", "max_hold_from", "2026-01-01T00:00:00+00:00"
        )
        clock.advance(500.0)

        rig.manage()

        assert rig.untouched()

    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_grid_owned_symbol_is_untouched(
        self, dbm: Any, clock: Clock, strategy: StrategyType, tag: str, hours: float
    ) -> None:
        rig = Rig(dbm, grid_symbols=(SYMBOL,))
        enter(rig, hand_signal(strategy, hours))
        clock.advance(hours + 1.0)

        rig.manage()

        assert rig.untouched()

    @pytest.mark.parametrize(
        "tag", ["mean_reversion", "ma_crossover", "momentum_scalping", "grid_trading"]
    )
    def test_strategy_the_backtest_does_not_time_limit_is_untouched(
        self, rig: Rig, clock: Clock, tag: str
    ) -> None:
        """Even with a hold on the row, only the three strategies exit."""
        rig.open(strategy=None, age_hours=500.0)
        assert rig.dbm.tag_position_owner(SYMBOL, "LONG", tag, None, max_hold_hours=1.0)
        clock.advance(500.0)

        rig.manage()

        assert rig.untouched()


class TestNoActionOnMissingData:
    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_missing_price_defers(
        self, dbm: Any, clock: Clock, strategy: StrategyType, tag: str, hours: float
    ) -> None:
        rig = Rig(dbm, price=None)
        enter(rig, hand_signal(strategy, hours))
        clock.advance(hours + 1.0)

        assert rig.manage()["positions_closed"] == 0
        assert rig.untouched()

    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_unreadable_venue_defers_then_closes(
        self, rig: Rig, clock: Clock, strategy: StrategyType, tag: str, hours: float
    ) -> None:
        enter(rig, hand_signal(strategy, hours))
        clock.advance(hours + 1.0)
        rig.venue.readable = False

        assert rig.manage()["positions_closed"] == 0
        assert rig.venue.orders == [] and rig.rows() == []

        rig.venue.readable = True
        assert rig.manage()["positions_closed"] == 1
        assert len(rig.rows()) == 1

    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_venue_already_flat_sends_nothing(
        self, rig: Rig, clock: Clock, strategy: StrategyType, tag: str, hours: float
    ) -> None:
        """A stop or target that already closed it wins, as in the backtest."""
        enter(rig, hand_signal(strategy, hours))
        rig.venue.quantities[(SYMBOL, "long")] = 0.0
        clock.advance(hours + 1.0)

        assert rig.manage()["positions_closed"] == 0
        assert rig.venue.orders == [] and rig.rows() == []


class TestExitRunsWhileBreakerTripped:
    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_time_exit_runs(
        self, rig: Rig, clock: Clock, strategy: StrategyType, tag: str, hours: float
    ) -> None:
        enter(rig, hand_signal(strategy, hours))
        clock.advance(hours + 0.1)
        bot = SimpleNamespace(
            client=rig.client,
            migrated_position_manager=rig.manager,
            _circuit_breaker_triggered=True,
            _circuit_breaker_reason="portfolio loss",
            _get_ticker_ws=lambda symbol: {"last": rig.venue.price},
        )
        bind(bot, "_manage_migrated_positions")
        assert _breaker_block_reason(bot) is not None  # entries are blocked

        bot._manage_migrated_positions()

        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "time_exit"
        assert rows[0]["strategy"] == tag


# ----------------------------------------------------------------------
# Parity with the backtest engine
# ----------------------------------------------------------------------


def _real_signal(strategy: StrategyType, manager: StrategyManager) -> Signal:
    """A signal carrying the hold the real strategy puts on its signals."""
    if strategy == StrategyType.CALENDAR_FLOW:
        calendar = next(
            s
            for s in manager.strategies.values()
            if isinstance(s, CalendarFlowStrategy)
        )
        # 2024-02-01 00:00 UTC is inside the turn-of-month window that
        # closes 2024-02-04 00:00 UTC: 72h remain.
        calendar._sim_time = datetime(2024, 2, 1, 0, 0)
        produced = calendar.generate_signals(SYMBOL, {"4h": make_4h()}, 100.0)
        assert len(produced) == 1
        signal = produced[0]
        assert signal.indicators["time_exit_hours"] == pytest.approx(72.0)
        signal.quantity = 2.0
        return signal
    wanted = (
        SessionRangeBreakoutStrategy
        if strategy == StrategyType.SESSION_RANGE_BREAKOUT
        else VWAPPullbackStrategy
    )
    instance = next(s for s in manager.strategies.values() if isinstance(s, wanted))
    # Both strategies put self.time_exit_hours on every signal they emit.
    return hand_signal(strategy, instance.time_exit_hours)


class TestParityWithBacktest:
    @pytest.mark.parametrize("strategy,tag,hours", ENFORCED)
    def test_live_deadline_equals_the_backtest_deadline(
        self,
        dbm: Any,
        clock: Clock,
        monkeypatch: pytest.MonkeyPatch,
        strategy: StrategyType,
        tag: str,
        hours: float,
    ) -> None:
        rig = Rig(
            dbm,
            extra_strategies={
                "enable_calendar_flow": True,
                "enable_vwap_pullback": True,
                "enable_session_range_breakout": True,
            },
        )
        signal = _real_signal(strategy, rig.strategy_manager)
        carried = float(signal.indicators["time_exit_hours"])
        assert carried == pytest.approx(hours)

        # Backtest: the engine tracks the hold from the fill.
        engine = build_engine(monkeypatch)
        exchange = SimulatedExchange(initial_capital=100000.0)
        exchange._current_price = 100.0
        fill_time = datetime(2024, 2, 1, 0, 0)
        exchange._current_timestamp = fill_time.isoformat()
        engine._sim_dt = fill_time
        assert engine._execute_signal(signal, exchange, 0) is True
        info = engine._position_time_exit[SYMBOL]
        assert info["open_ts"] == fill_time
        backtest_hold = timedelta(hours=info["hours"])

        engine._apply_time_exits(exchange, fill_time + backtest_hold - timedelta(1e-9))
        assert SYMBOL in exchange._positions
        engine._apply_time_exits(exchange, fill_time + backtest_hold)
        assert SYMBOL not in exchange._positions

        # Live: the same signal, recorded at its fill.
        before = datetime.now(timezone.utc)
        row = enter(rig, signal)
        after = datetime.now(timezone.utc)
        if tag == "calendar_flow":
            anchor = parse_utc(row["max_hold_from"])
            live_hold = timedelta(hours=row["max_hold_hours"])
        else:
            anchor = parse_utc(row["opened_at"])
            live_hold = timedelta(hours=rig.manager._max_hold_hours[tag])
        assert before <= anchor <= after  # measured from the fill
        assert live_hold == backtest_hold  # same duration

        deadline = (anchor + live_hold).timestamp()
        clock.offset = deadline - 0.5 - real_time.time()
        assert rig.manager._check_time_exit(SYMBOL, {"side": "long"}) is False
        clock.offset = deadline + 0.5 - real_time.time()
        assert rig.manager._check_time_exit(SYMBOL, {"side": "long"}) is True


# ----------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------


class TestHoldColumnsUpgrade:
    def test_existing_sqlite_database_is_upgraded_in_place(self, dbm: Any) -> None:
        loop_sync_bot(dbm, [venue_position()])._update_positions()
        dbm.tag_position_owner(SYMBOL, "LONG", "calendar_flow", "ranging_calm")
        with db_module.get_db_connection() as conn:
            conn.execute("ALTER TABLE positions DROP COLUMN max_hold_hours")
            conn.execute("ALTER TABLE positions DROP COLUMN max_hold_from")
            conn.commit()
            before = db_module._existing_columns(conn, "positions")
            added = db_module._ensure_closed_trade_schema(conn)
            again = db_module._ensure_closed_trade_schema(conn)
            conn.commit()
            after = db_module._existing_columns(conn, "positions")
        db_module._data_cache.invalidate("positions_all")

        assert "max_hold_hours" not in before and "max_hold_from" not in before
        assert added == ["positions.max_hold_hours", "positions.max_hold_from"]
        assert again == []  # safe to re-run
        assert after == before | {"max_hold_hours", "max_hold_from"}
        row = dbm.get_position(SYMBOL, "LONG")
        assert row["strategy"] == "calendar_flow" and row["quantity"] == 2.0
        assert row["max_hold_hours"] is None and row["max_hold_from"] is None

    def test_columns_are_nullable_without_a_default(self, dbm: Any) -> None:
        raw = sqlite3.connect(db_module.DATABASE_PATH)
        try:
            info = {row[1]: row for row in raw.execute("PRAGMA table_info(positions)")}
        finally:
            raw.close()
        for name, kind in (("max_hold_hours", "REAL"), ("max_hold_from", "TEXT")):
            assert info[name][2] == kind
            assert info[name][3] == 0 and info[name][4] is None

    def test_postgres_statements_are_additive_and_guarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No Postgres here: the statements the upgrade would send are checked."""
        statements: List[str] = []

        class Conn:
            def execute(self, sql: str, params: Any = ()) -> Any:
                statements.append(sql)
                columns = [("id",), ("strategy",), ("entry_regime",)]
                return SimpleNamespace(fetchall=lambda: columns)

        monkeypatch.setattr(db_module, "_active_backend", "postgres")

        added = db_module._add_missing_columns(
            Conn(), "positions", db_module.POSITION_OWNER_COLUMNS
        )

        assert added == ["max_hold_hours", "max_hold_from"]
        alters = [sql for sql in statements if sql.startswith("ALTER")]
        assert alters == [
            "ALTER TABLE positions ADD COLUMN IF NOT EXISTS max_hold_hours "
            "DOUBLE PRECISION",
            "ALTER TABLE positions ADD COLUMN IF NOT EXISTS max_hold_from TEXT",
        ]


@pytest.fixture(autouse=True)
def _positions_cache_is_fresh() -> Iterator[None]:
    """get_positions caches for 30s; raw writes in these tests bypass it."""
    db_module._data_cache.invalidate("positions_all")
    yield
    db_module._data_cache.invalidate("positions_all")
