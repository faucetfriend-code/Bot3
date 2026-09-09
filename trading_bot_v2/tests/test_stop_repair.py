"""T4 (continued): stop repair sweep, trailing coordination, local enforcement.

* the repair sweep (startup / hourly reconciliation / after cancel-all)
  re-installs a venue stop for any open position lacking one from the
  stored price, skips protected ones, and never stacks a stop when the
  venue cannot be listed;
* the migrated-position trailing stop mirrors onto the venue only when
  the move clears VENUE_STOP_MIN_MOVE_PCT, keeps the last good level on
  failure, and falls back to local after VENUE_STOP_MAX_AMEND_FAILURES;
* local enforcement closes an unprotected position (state missing /
  unsupported) reduce-only once price crosses the stored stop;
* the two env vars the previous agent read in-module now live in config
  with the env fallback preserved.

No network, no live database (conftest isolates DATABASE_PATH).
"""

import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from trading_bot_v2 import config as config_mod
from trading_bot_v2 import migrated_position_manager as mpm_mod
from trading_bot_v2 import trading_bot as tb_mod
from trading_bot_v2.exchanges.base import ExchangeCapabilities
from trading_bot_v2.exchanges.pacifica import PacificaExchange
from trading_bot_v2.migrated_position_manager import MigratedPositionManager
from trading_bot_v2.order_result import OrderResult
from trading_bot_v2.trading_bot import TradingBot
from trading_bot_v2.venue_stops import (
    STOP_STATE_ATTACHED,
    STOP_STATE_MISSING,
    STOP_STATE_STANDALONE,
    find_stop_row,
    stop_hit,
)

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


def _accepted(order_id: str = "t-new", method: str = "") -> OrderResult:
    raw = {"data": {"method": method}} if method else {}
    return OrderResult(
        success=True, accepted=True, order_id=order_id, status="accepted", raw=raw
    )


def _rejected(error: str = "refused") -> OrderResult:
    return OrderResult(status="rejected", error=error)


class StopExchange:
    """Adapter stand-in with venue stops."""

    def __init__(
        self, stops: Optional[Dict[str, Any]] = None, install=None, amend=None
    ):
        self.stops = stops or {}
        self.install_result = install or _accepted()
        self.amend_result = amend or _accepted("t1", "amend-tpsl")
        self.installs: List[tuple] = []
        self.amends: List[Dict[str, Any]] = []
        self.orders: List[Dict[str, Any]] = []

    def capabilities(self):
        return STOP_CAPS

    def list_stops(self, symbol):
        rows = self.stops.get(symbol, [])
        if isinstance(rows, Exception):
            raise rows
        return rows

    def install_stop(self, symbol, side, quantity, stop_price):
        self.installs.append((symbol, side, quantity, stop_price))
        return self.install_result

    def amend_stop(
        self, symbol, side, new_stop_price, entry_order_id=None, stop_id=None
    ):
        self.amends.append(
            {
                "symbol": symbol,
                "side": side,
                "price": new_stop_price,
                "entry_order_id": entry_order_id,
                "stop_id": stop_id,
            }
        )
        return self.amend_result

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"success": True, "data": {"order_id": "c1"}}


class FakeClient:
    """Native-client stand-in serving positions and recording orders."""

    def __init__(self, positions: List[Dict[str, Any]]):
        self.positions = positions
        self.calls: List[Dict[str, Any]] = []

    def get_positions(self):
        return [dict(p) for p in self.positions]

    def place_order(self, symbol, side, quantity, order_type, price=None, **kwargs):
        self.calls.append(
            {"symbol": symbol, "side": side, "quantity": quantity, **kwargs}
        )
        return {"success": True, "data": {"order_id": "c1"}}


class FakeDb:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows
        self.protection_writes: List[tuple] = []
        self.closed: List[tuple] = []

    def get_positions(self):
        return [dict(r) for r in self.rows]

    def get_position(self, symbol, side):
        for row in self.rows:
            if row["symbol"] == symbol and row["side"] == side:
                return dict(row)
        return None

    def record_position_protection(
        self, symbol, side, fields, quantity=None, entry_price=None
    ):
        self.protection_writes.append(
            (symbol, side, dict(fields), quantity, entry_price)
        )
        return True

    def close_position(self, symbol, side, exit_price=None):
        self.closed.append((symbol, side, exit_price))


def _bind(bot: SimpleNamespace, names) -> SimpleNamespace:
    for name in names:
        setattr(bot, name, getattr(TradingBot, name).__get__(bot))
    return bot


REPAIR_METHODS = (
    "_repair_venue_stops",
    "_repair_one_position",
    "_list_venue_stops",
    "_verify_venue_stop",
    "_protection_rows",
    "_stored_stop_price",
    "_persist_protection",
)

BTC_ROW = {
    "tpsl_id": "t1",
    "side": "sell",
    "position_side": "net",
    "sl_trigger_price": 95.0,
}


def _repair_bot(exchange, client, db) -> SimpleNamespace:
    bot = SimpleNamespace(
        exchange=exchange,
        client=client,
        db=db,
        risk_manager=None,
        migrated_position_manager=None,
        _protection_records={},
    )
    return _bind(bot, REPAIR_METHODS)


# ======================================================================
# Repair sweep
# ======================================================================


class TestRepairSweep:
    def test_reinstalls_missing_stop_and_skips_protected(self):
        exchange = StopExchange(stops={"BTC": [BTC_ROW], "ETH": []})
        client = FakeClient(
            [
                {
                    "symbol": "BTC",
                    "side": "long",
                    "quantity": 1.0,
                    "entry_price": 100.0,
                },
                {
                    "symbol": "ETH",
                    "side": "short",
                    "quantity": 2.0,
                    "entry_price": 50.0,
                },
            ]
        )
        db = FakeDb(
            [
                {
                    "symbol": "BTC",
                    "side": "LONG",
                    "quantity": 1.0,
                    "venue_stop_state": STOP_STATE_ATTACHED,
                    "venue_stop_id": "t1",
                    "venue_stop_price": 95.0,
                    "entry_order_id": "9001",
                },
                {
                    "symbol": "ETH",
                    "side": "SHORT",
                    "quantity": 2.0,
                    "venue_stop_state": STOP_STATE_MISSING,
                    "venue_stop_id": None,
                    "venue_stop_price": 55.0,
                    "entry_order_id": "9002",
                },
            ]
        )
        bot = _repair_bot(exchange, client, db)

        summary = bot._repair_venue_stops("test")

        assert exchange.installs == [("ETH", "SHORT", 2.0, 55.0)]
        assert summary["protected"] == 1 and summary["installed"] == 1
        assert summary["failed"] == 0 and summary["skipped"] == 0
        eth = [w for w in db.protection_writes if w[0] == "ETH"][0]
        assert eth[2]["venue_stop_state"] == STOP_STATE_STANDALONE
        assert eth[2]["venue_stop_id"] == "t-new"
        assert eth[2]["entry_order_id"] == "9002"
        btc = [w for w in db.protection_writes if w[0] == "BTC"][0]
        assert btc[2]["venue_stop_state"] == STOP_STATE_ATTACHED  # kept as-is
        assert btc[2]["venue_stop_id"] == "t1"

    def test_no_stored_stop_is_skipped_with_error(self, caplog):
        exchange = StopExchange(stops={"ETH": []})
        client = FakeClient([{"symbol": "ETH", "side": "long", "quantity": 1.0}])
        bot = _repair_bot(exchange, client, FakeDb([]))
        summary = bot._repair_venue_stops("test")
        assert exchange.installs == []
        assert summary["skipped"] == 1

    def test_unreachable_venue_never_stacks_a_stop(self):
        exchange = StopExchange(stops={"BTC": ConnectionError("down")})
        client = FakeClient([{"symbol": "BTC", "side": "long", "quantity": 1.0}])
        db = FakeDb(
            [
                {
                    "symbol": "BTC",
                    "side": "LONG",
                    "venue_stop_price": 95.0,
                    "venue_stop_state": STOP_STATE_MISSING,
                }
            ]
        )
        bot = _repair_bot(exchange, client, db)
        summary = bot._repair_venue_stops("test")
        assert exchange.installs == [] and summary["failed"] == 1

    def test_install_rejection_marks_missing(self):
        exchange = StopExchange(stops={"BTC": []}, install=_rejected("nope"))
        client = FakeClient([{"symbol": "BTC", "side": "long", "quantity": 1.0}])
        db = FakeDb(
            [
                {
                    "symbol": "BTC",
                    "side": "LONG",
                    "venue_stop_price": 95.0,
                    "venue_stop_state": STOP_STATE_STANDALONE,
                    "venue_stop_id": "old",
                }
            ]
        )
        bot = _repair_bot(exchange, client, db)
        summary = bot._repair_venue_stops("test")
        assert summary["failed"] == 1
        write = db.protection_writes[-1][2]
        assert (
            write["venue_stop_state"] == STOP_STATE_MISSING
            and write["venue_stop_id"] is None
        )

    def test_stored_stop_falls_back_to_trailing_level(self):
        exchange = StopExchange(stops={"SOL": []})
        client = FakeClient([{"symbol": "SOL", "side": "short", "quantity": 3.0}])
        bot = _repair_bot(exchange, client, FakeDb([]))
        bot.migrated_position_manager = SimpleNamespace(
            _trailing_stops={"SOL": {"short": 12.5}}
        )
        bot._repair_venue_stops("test")
        assert exchange.installs == [("SOL", "SHORT", 3.0, 12.5)]

    def test_unsupported_exchange_is_a_no_op(self):
        client = FakeClient([{"symbol": "BTC", "side": "long", "quantity": 1.0}])
        bot = _repair_bot(PacificaExchange(rest_client=client), client, FakeDb([]))
        summary = bot._repair_venue_stops("test")
        assert summary.get("unsupported") is True and client.calls == []

    def test_positions_unavailable_returns_error_without_raising(self):
        client = MagicMock()
        client.get_positions.side_effect = TimeoutError("probe")
        bot = _repair_bot(StopExchange(), client, FakeDb([]))
        summary = bot._repair_venue_stops("test")
        assert "error" in summary


# ======================================================================
# Trailing coordination (MigratedPositionManager)
# ======================================================================


class FakeRiskManager:
    def __init__(self, positions):
        self.positions = positions

    def get_migrated_positions(self, symbol=None):
        return [dict(p) for p in self.positions]

    def has_migrated_positions(self, symbol=None):
        return bool(self.positions)

    def update_migrated_stop(self, symbol, side, new_stop):
        return True


def _manager(exchange, db=None, **env) -> MigratedPositionManager:
    with patch.dict(os.environ, {k: str(v) for k, v in env.items()}):
        manager = MigratedPositionManager(
            FakeClient([]),
            FakeRiskManager([]),
            regime_detector=None,
            venue_exchange_resolver=lambda: exchange,
            db=db,
        )
    manager._update_db_position = MagicMock()
    return manager


POS = {"symbol": "BTC", "side": "long", "qty": 2.0, "entry_price": 100.0}


class TestTrailingCoordination:
    def test_move_below_threshold_does_not_amend(self):
        exchange = StopExchange()
        manager = _manager(exchange, VENUE_STOP_MIN_MOVE_PCT="0.1")
        manager._venue_stops[("BTC", "long")] = {
            "stop_id": "t1",
            "price": 100.0,
            "entry_order_id": "9001",
            "state": STOP_STATE_ATTACHED,
            "failures": 0,
            "fallback_local": False,
        }
        manager._trailing_stops = {"BTC": {"long": 100.05}}  # 0.05% < 0.1%
        assert manager._sync_venue_stop("BTC", POS) is False
        assert exchange.amends == []

    def test_move_above_threshold_amends_and_writes_back(self):
        exchange = StopExchange(amend=_accepted("t1b", "amend-tpsl"))
        db = FakeDb([])
        manager = _manager(exchange, db=db, VENUE_STOP_MIN_MOVE_PCT="0.1")
        manager._venue_stops[("BTC", "long")] = {
            "stop_id": "t1",
            "price": 100.0,
            "entry_order_id": "9001",
            "state": STOP_STATE_ATTACHED,
            "failures": 0,
            "fallback_local": False,
        }
        manager._trailing_stops = {"BTC": {"long": 101.0}}  # 1% > 0.1%
        assert manager._sync_venue_stop("BTC", POS) is True
        assert exchange.amends == [
            {
                "symbol": "BTC",
                "side": "long",
                "price": 101.0,
                "entry_order_id": "9001",
                "stop_id": "t1",
            }
        ]
        state = manager._venue_stops[("BTC", "long")]
        assert state["stop_id"] == "t1b" and state["price"] == 101.0
        assert db.protection_writes[-1][:2] == ("BTC", "LONG")
        written = db.protection_writes[-1][2]
        assert written["venue_stop_id"] == "t1b"
        assert written["venue_stop_price"] == 101.0
        assert written["venue_stop_state"] == STOP_STATE_STANDALONE

    def test_amend_order_success_keeps_attached_state(self):
        exchange = StopExchange(amend=_accepted("t1", "amend-order"))
        db = FakeDb([])
        manager = _manager(exchange, db=db)
        manager._venue_stops[("BTC", "long")] = {
            "stop_id": "t1",
            "price": 100.0,
            "entry_order_id": "9001",
            "state": STOP_STATE_ATTACHED,
            "failures": 0,
            "fallback_local": False,
        }
        manager._trailing_stops = {"BTC": {"long": 102.0}}
        manager._sync_venue_stop("BTC", POS)
        assert db.protection_writes[-1][2]["venue_stop_state"] == STOP_STATE_ATTACHED

    def test_failure_cap_falls_back_to_local(self):
        exchange = StopExchange(amend=_rejected("venue says no"))
        db = FakeDb([])
        manager = _manager(exchange, db=db, VENUE_STOP_MAX_AMEND_FAILURES="3")
        manager._venue_stops[("BTC", "long")] = {
            "stop_id": "t1",
            "price": 100.0,
            "entry_order_id": None,
            "state": STOP_STATE_STANDALONE,
            "failures": 0,
            "fallback_local": False,
        }
        manager._trailing_stops = {"BTC": {"long": 101.0}}
        for _ in range(3):
            assert manager._sync_venue_stop("BTC", POS) is False
        state = manager._venue_stops[("BTC", "long")]
        assert state["fallback_local"] is True
        assert state["price"] == 100.0  # last known good venue level kept
        assert db.protection_writes[-1][2] == {"venue_stop_state": STOP_STATE_MISSING}
        manager._sync_venue_stop("BTC", POS)  # fourth call: no more venue traffic
        assert len(exchange.amends) == 3

    def test_no_known_venue_stop_installs_one(self):
        exchange = StopExchange(install=_accepted("t-fresh"))
        manager = _manager(exchange, db=FakeDb([]))
        manager._trailing_stops = {"BTC": {"long": 98.0}}
        assert manager._sync_venue_stop("BTC", POS) is True
        assert exchange.installs == [("BTC", "long", 2.0, 98.0)]
        assert manager._venue_stops[("BTC", "long")]["stop_id"] == "t-fresh"

    def test_venue_stop_loaded_from_db_row(self):
        exchange = StopExchange(amend=_accepted("t7", "amend-tpsl"))
        db = FakeDb(
            [
                {
                    "symbol": "BTC",
                    "side": "LONG",
                    "venue_stop_state": STOP_STATE_STANDALONE,
                    "venue_stop_id": "t7",
                    "venue_stop_price": 90.0,
                    "entry_order_id": "e7",
                }
            ]
        )
        manager = _manager(exchange, db=db)
        manager._trailing_stops = {"BTC": {"long": 98.0}}
        manager._sync_venue_stop("BTC", POS)
        assert exchange.amends[0]["stop_id"] == "t7"
        assert exchange.amends[0]["entry_order_id"] == "e7"

    def test_no_resolver_or_unsupported_is_local_only(self):
        manager = MigratedPositionManager(FakeClient([]), FakeRiskManager([]), None)
        manager._trailing_stops = {"BTC": {"long": 98.0}}
        assert manager._sync_venue_stop("BTC", POS) is False
        pacifica = MigratedPositionManager(
            FakeClient([]),
            FakeRiskManager([]),
            None,
            venue_exchange_resolver=lambda: PacificaExchange(
                rest_client=FakeClient([])
            ),
        )
        pacifica._trailing_stops = {"BTC": {"long": 98.0}}
        assert pacifica._sync_venue_stop("BTC", POS) is False

    def test_manage_positions_syncs_after_trailing_update(self):
        exchange = StopExchange()
        manager = _manager(exchange)
        manager.risk_manager = FakeRiskManager([POS])
        manager._check_time_exit = MagicMock(return_value=False)
        manager._get_market_data = MagicMock(return_value=None)
        manager._check_trend_reversal = MagicMock(return_value=False)
        manager._check_stop_hit = MagicMock(return_value=False)
        manager._update_trailing_stop = MagicMock(return_value=True)
        manager._check_take_profit = MagicMock(return_value=False)
        manager._sync_venue_stop = MagicMock(return_value=True)
        result = manager.manage_positions(current_prices={"BTC": 105.0})
        assert result["stops_updated"] == 1
        manager._sync_venue_stop.assert_called_once_with("BTC", POS)

    def test_finalize_close_drops_venue_mirror(self):
        manager = _manager(StopExchange())
        manager._venue_stops[("BTC", "long")] = {"stop_id": "t1"}
        manager._finalize_close("BTC", "long", 2.0, "trailing_stop")
        assert ("BTC", "long") not in manager._venue_stops


# ======================================================================
# Local stop enforcement
# ======================================================================

ENFORCE_METHODS = (
    "_enforce_local_stops",
    "_protection_rows",
    "_is_migrated_symbol",
    "_current_price_for",
    "_emergency_close_position",
    "_exchange_quantity_for",
    "_mark_position_closed",
)


def _enforce_bot(rows, exchange_positions, price: float, risk_manager=None):
    client = FakeClient(exchange_positions)
    bot = SimpleNamespace(
        client=client,
        exchange=PacificaExchange(rest_client=client),
        db=FakeDb(rows),
        risk_manager=risk_manager,
        _protection_records={},
        _get_ticker_ws=lambda symbol: {"last": price},
    )
    return _bind(bot, ENFORCE_METHODS), client


class TestLocalStopEnforcement:
    def test_unprotected_position_closed_reduce_only_when_stop_crossed(self):
        rows = [
            {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 1.0,
                "venue_stop_price": 95.0,
                "venue_stop_state": STOP_STATE_MISSING,
            }
        ]
        bot, client = _enforce_bot(
            rows, [{"symbol": "BTC", "side": "long", "amount": "1"}], 94.0
        )
        assert bot._enforce_local_stops() == 1
        assert len(client.calls) == 1
        assert client.calls[0]["side"] == "sell"
        assert client.calls[0]["reduce_only"] is True
        assert client.calls[0]["quantity"] == pytest.approx(1.0)
        assert bot.db.closed == [("BTC", "LONG", 94.0)]

    def test_short_unsupported_closes_with_buy(self):
        rows = [
            {
                "symbol": "ETH",
                "side": "SHORT",
                "quantity": 2.0,
                "venue_stop_price": 55.0,
                "venue_stop_state": "unsupported",
            }
        ]
        bot, client = _enforce_bot(
            rows, [{"symbol": "ETH", "side": "short", "amount": "2"}], 56.0
        )
        assert bot._enforce_local_stops() == 1
        assert (
            client.calls[0]["side"] == "buy" and client.calls[0]["reduce_only"] is True
        )

    def test_not_crossed_sends_nothing(self):
        rows = [
            {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 1.0,
                "venue_stop_price": 95.0,
                "venue_stop_state": STOP_STATE_MISSING,
            }
        ]
        bot, client = _enforce_bot(
            rows, [{"symbol": "BTC", "side": "long", "amount": "1"}], 96.0
        )
        assert bot._enforce_local_stops() == 0 and client.calls == []

    def test_venue_protected_position_is_not_checked(self):
        rows = [
            {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 1.0,
                "venue_stop_price": 95.0,
                "venue_stop_state": STOP_STATE_ATTACHED,
            }
        ]
        bot, client = _enforce_bot(
            rows, [{"symbol": "BTC", "side": "long", "amount": "1"}], 90.0
        )
        assert bot._enforce_local_stops() == 0 and client.calls == []

    def test_migrated_position_left_to_its_manager(self):
        rows = [
            {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 1.0,
                "venue_stop_price": 95.0,
                "venue_stop_state": STOP_STATE_MISSING,
            }
        ]
        risk = FakeRiskManager([{"symbol": "BTC", "side": "long", "qty": 1.0}])
        bot, client = _enforce_bot(
            rows, [{"symbol": "BTC", "side": "long", "amount": "1"}], 90.0, risk
        )
        assert bot._enforce_local_stops() == 0 and client.calls == []

    def test_memory_record_without_db_row_uses_exchange_quantity(self):
        bot, client = _enforce_bot(
            [], [{"symbol": "BTC", "side": "long", "amount": "0.7"}], 90.0
        )
        bot._protection_records[("BTC", "LONG")] = {
            "venue_stop_price": 95.0,
            "venue_stop_state": STOP_STATE_MISSING,
        }
        assert bot._enforce_local_stops() == 1
        assert client.calls[0]["quantity"] == pytest.approx(0.7)
        assert ("BTC", "LONG") not in bot._protection_records

    def test_price_unavailable_takes_no_action(self):
        rows = [
            {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 1.0,
                "venue_stop_price": 95.0,
                "venue_stop_state": STOP_STATE_MISSING,
            }
        ]
        bot, client = _enforce_bot(
            rows, [{"symbol": "BTC", "side": "long", "amount": "1"}], 90.0
        )
        bot._get_ticker_ws = MagicMock(side_effect=RuntimeError("ws down"))
        assert bot._enforce_local_stops() == 0 and client.calls == []


# ======================================================================
# Helpers and config plumbing
# ======================================================================


class TestHelpersAndConfig:
    def test_stop_hit_semantics(self):
        assert stop_hit("long", 95.0, 95.0) and stop_hit("LONG", 95.0, 94.0)
        assert not stop_hit("long", 95.0, 96.0)
        assert stop_hit("short", 105.0, 105.0) and not stop_hit("short", 105.0, 104.0)
        assert not stop_hit("long", None, 90.0) and not stop_hit("long", 95.0, None)

    def test_find_stop_row_prefers_sl_rows_for_side(self):
        rows = [
            {
                "tpsl_id": "tp",
                "side": "sell",
                "position_side": "net",
                "tp_trigger_price": 120.0,
            },
            {
                "tpsl_id": "sl",
                "side": "sell",
                "position_side": "net",
                "sl_trigger_price": 95.0,
            },
            {
                "tpsl_id": "short-sl",
                "side": "buy",
                "position_side": "net",
                "sl_trigger_price": 130.0,
            },
        ]
        assert find_stop_row(rows, "LONG")["tpsl_id"] == "sl"
        assert find_stop_row(rows, "short")["tpsl_id"] == "short-sl"
        assert find_stop_row([], "long") is None
        hedge = [
            {
                "tpsl_id": "h",
                "side": "sell",
                "position_side": "short",
                "sl_trigger_price": 1.0,
            }
        ]
        assert find_stop_row(hedge, "long") is None

    def test_config_defaults_present(self):
        cfg = config_mod.Config()
        assert cfg.venue_stop_failure_policy == "close"
        assert cfg.venue_stop_min_move_pct == pytest.approx(0.1)
        assert cfg.venue_stop_max_amend_failures == 5
        assert cfg.migrated_close_max_attempts == 5
        assert cfg.entry_fill_max_lookups == 10

    def test_config_reads_env_and_rejects_bad_policy(self, monkeypatch):
        monkeypatch.setenv("VENUE_STOP_FAILURE_POLICY", "LOCAL")
        monkeypatch.setenv("VENUE_STOP_MIN_MOVE_PCT", "0.25")
        monkeypatch.setenv("VENUE_STOP_MAX_AMEND_FAILURES", "2")
        monkeypatch.setenv("MIGRATED_CLOSE_MAX_ATTEMPTS", "7")
        monkeypatch.setenv("ENTRY_FILL_MAX_LOOKUPS", "3")
        cfg = config_mod.Config()
        assert cfg.venue_stop_failure_policy == "local"
        assert cfg.venue_stop_min_move_pct == pytest.approx(0.25)
        assert cfg.venue_stop_max_amend_failures == 2
        assert cfg.migrated_close_max_attempts == 7
        assert cfg.entry_fill_max_lookups == 3
        monkeypatch.setenv("VENUE_STOP_FAILURE_POLICY", "shrug")
        assert config_mod.Config().venue_stop_failure_policy == "close"

    def test_modules_prefer_env_then_config(self, monkeypatch):
        monkeypatch.setenv("MIGRATED_CLOSE_MAX_ATTEMPTS", "9")
        monkeypatch.setenv("ENTRY_FILL_MAX_LOOKUPS", "4")
        assert mpm_mod._max_close_attempts() == 9
        assert tb_mod._entry_fill_max_lookups() == 4
        monkeypatch.delenv("MIGRATED_CLOSE_MAX_ATTEMPTS")
        monkeypatch.delenv("ENTRY_FILL_MAX_LOOKUPS")
        monkeypatch.setattr(
            config_mod.config, "migrated_close_max_attempts", 6, raising=False
        )
        monkeypatch.setattr(
            config_mod.config, "entry_fill_max_lookups", 2, raising=False
        )
        assert mpm_mod._max_close_attempts() == 6
        assert tb_mod._entry_fill_max_lookups() == 2
