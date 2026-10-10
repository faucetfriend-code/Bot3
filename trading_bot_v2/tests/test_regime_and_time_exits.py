"""Regime exit and max-hold time exit on the live inputs.

Both protections existed but could not trigger:

* the regime review looked for open positions in ``trades WHERE
  status='open'``, which nothing writes;
* the time exit looked for a ``strategy`` key on RiskManager's migrated
  registry records, which the registry does not keep, and bot-opened
  positions are not in that registry at all.

Everything here runs against a REAL ``DatabaseManager`` on a temp SQLite
file holding ``positions`` rows tagged the way ``_record_entry_fill``
leaves them, a real ``MigratedPositionManager``, a real ``RiskManager``,
a real ``StrategyManager`` (the activation source of truth) and a real
``MarketRegimeDetector``.  The exchange client is spec'd on the real
``PacificaClient`` and backed by a small stateful venue, so a call the
real client does not have, or a wrong call signature, fails the test.
"""

import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional, Tuple
from unittest.mock import MagicMock, Mock, create_autospec

import pytest
from loguru import logger

from trading_bot_v2.event_system import EventBus, EventType
from trading_bot_v2.grid_lifecycle_manager import GridState
from trading_bot_v2.history import TradeStore
from trading_bot_v2.market_regime import MarketRegime, MarketRegimeDetector
from trading_bot_v2.migrated_position_manager import (
    MigratedPositionManager,
    _epoch_seconds,
)
from trading_bot_v2.pacifica_client import PacificaClient
from trading_bot_v2.regime_position_review import RegimePositionReviewer
from trading_bot_v2.risk_manager import RiskManager
from trading_bot_v2.strategy_manager import StrategyManager
from trading_bot_v2.trading_bot import TradingBot, _breaker_block_reason

SYMBOL = "BTC"
CANDLES = {"close": [100.0] * 200, "high": [101.0] * 200, "low": [99.0] * 200}
ATR_TRAIL = 4.0  # 2x ATR of the candles above (ATR 2.0)


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------


@pytest.fixture
def dbm() -> Iterator[Any]:
    """A real DatabaseManager on a fresh temp SQLite file."""
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
    yield module.DatabaseManager()
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
def log_lines() -> Iterator[List[str]]:
    """Every loguru message emitted during the test."""
    lines: List[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    yield lines
    logger.remove(sink)


class Venue:
    """Stateful exchange behind the PacificaClient-spec'd client."""

    def __init__(self, price: Optional[float]) -> None:
        self.price = price
        self.quantities: Dict[Tuple[str, str], float] = {}
        self.orders: List[Dict[str, Any]] = []
        self.readable = True
        self.reject = False

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.readable:
            raise ConnectionError("venue down")
        return [
            {"symbol": symbol, "side": side, "amount": str(quantity)}
            for (symbol, side), quantity in self.quantities.items()
            if quantity > 0
        ]

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        if self.price is None:
            return {}
        return {"symbol": symbol, "price": self.price}

    def place_order(
        self, symbol: str, side: str, quantity: float, order_type: str, **kw: Any
    ) -> Dict[str, Any]:
        self.orders.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                **kw,
            }
        )
        if self.reject:
            return {"success": False, "error": "insufficient margin", "data": {}}
        key = (symbol, "long" if side == "sell" else "short")
        filled = min(self.quantities.get(key, 0.0), quantity)
        self.quantities[key] = self.quantities.get(key, 0.0) - filled
        return {
            "success": True,
            "data": {
                "order_id": f"close-{len(self.orders)}",
                "filled_quantity": filled,
                "avg_fill_price": self.price,
            },
        }


class Rig:
    """The objects of one scenario, built on real collaborators."""

    def __init__(
        self,
        dbm: Any,
        price: Optional[float] = 90.0,
        trend: str = "down",
        grid_symbols: Tuple[str, ...] = (),
        extra_strategies: Optional[Dict[str, bool]] = None,
    ) -> None:
        self.dbm = dbm
        self.venue = Venue(price)
        self.client = create_autospec(PacificaClient, instance=True)
        self.client.get_positions.side_effect = self.venue.get_positions
        self.client.get_market_data.side_effect = self.venue.get_market_data
        self.client.place_order.side_effect = self.venue.place_order
        self.grid_symbols = set(grid_symbols)
        self.risk = RiskManager()
        self.detector = MarketRegimeDetector()
        # The 4h trend is scripted; everything else on the detector is real.
        self.detector.get_trend_direction = lambda market_data: trend
        fetcher = MagicMock()
        fetcher.get_candles_multi_tf.return_value = {"4h": CANDLES}
        self.manager = MigratedPositionManager(
            self.client,
            self.risk,
            self.detector,
            multi_tf_fetcher=fetcher,
            db=dbm,
            grid_symbol_checker=lambda symbol: symbol in self.grid_symbols,
        )
        flags = {
            "enable_mean_reversion": True,
            "enable_ma_crossover": True,
            "enable_momentum_scalping": True,
            "enable_liquidation_capture": True,
            "enable_session_range_breakout": True,
            "enable_orderbook_imbalance": False,
            "enable_grid_trading": False,
            "enable_vwap_scalping": False,
            "enable_trend_following": False,
            "enable_calendar_flow": False,
            "enable_vwap_pullback": False,
        }
        flags.update(extra_strategies or {})
        self.strategy_manager = StrategyManager(regime_detector=self.detector, **flags)
        self.reviewer = RegimePositionReviewer(
            db=dbm,
            client=self.client,
            regime_detector=self.detector,
            migrated_position_manager=self.manager,
            multi_tf_fetcher=fetcher,
            enabled=True,
            active_strategy_resolver=self.strategy_manager.get_active_strategy_names,
            grid_symbol_checker=lambda symbol: symbol in self.grid_symbols,
        )
        self.bus = EventBus()
        self.bus.subscribe(
            EventType.REGIME_CHANGED, self.reviewer.handle_regime_changed
        )

    def open(
        self,
        strategy: Optional[str] = "mean_reversion",
        side: str = "LONG",
        quantity: float = 2.0,
        entry: float = 100.0,
        age_hours: float = 1.0,
        symbol: str = SYMBOL,
        stop: Optional[float] = None,
        opened_at: Optional[str] = None,
    ) -> None:
        """Create the positions row and the venue position behind it."""
        stamp = datetime.now(timezone.utc) - timedelta(hours=age_hours)
        data: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry,
            "current_price": entry,
            "opened_at": opened_at or stamp.isoformat(),
            "funding_pnl": 0.0,
            "asset_class": "perpetual",
            "entry_order_id": "entry-1",
        }
        if stop is not None:
            data["venue_stop_state"] = "unsupported"
            data["venue_stop_price"] = stop
        self.dbm.save_position(data)
        if strategy is not None:
            assert self.dbm.tag_position_owner(symbol, side, strategy, "ranging_calm")
        self.venue.quantities[(symbol, side.lower())] = quantity

    def regime_change(
        self,
        old: Optional[str] = "ranging_calm",
        new: str = "trending_strong",
        symbol: str = SYMBOL,
    ) -> None:
        """Publish REGIME_CHANGED on the real (synchronous) event bus."""
        data: Dict[str, Any] = {"symbol": symbol, "new_regime": new}
        if old is not None:
            data["old_regime"] = old
        self.bus.publish_event(EventType.REGIME_CHANGED, data, "test")

    def rows(self) -> List[Dict[str, Any]]:
        """Closed-trade rows, oldest first."""
        rows = TradeStore(db=self.dbm).get_closed_trades()
        return sorted(rows, key=lambda row: row["id"])

    def manage(self) -> Dict[str, Any]:
        """One migrated-manager pass with the price the loop would pass."""
        prices = {SYMBOL: self.venue.price} if self.venue.price else {}
        return self.manager.manage_positions(current_prices=prices)

    def untouched(self) -> bool:
        """No order, no closed-trade row, nothing registered, no stop armed."""
        return (
            self.venue.orders == []
            and self.rows() == []
            and self.risk.get_migrated_positions() == []
            and self.manager.get_trailing_stop(SYMBOL, "long") is None
            and self.manager.get_trailing_stop(SYMBOL, "short") is None
        )


@pytest.fixture
def rig(dbm: Any) -> Rig:
    return Rig(dbm)


# ----------------------------------------------------------------------
# Regime exit
# ----------------------------------------------------------------------


class TestRegimeExitCloses:
    def test_inactive_owner_losing_against_trend_is_closed_once(
        self, rig: Rig, log_lines: List[str]
    ) -> None:
        rig.open(strategy="mean_reversion")  # long 2 @ 100, price 90, trend down

        rig.regime_change()

        assert len(rig.venue.orders) == 1
        order = rig.venue.orders[0]
        assert (order["side"], order["quantity"], order["order_type"]) == (
            "sell",
            2.0,
            "market",
        )
        assert order["reduce_only"] is True
        rows = rig.rows()
        assert len(rows) == 1
        assert rows[0]["close_reason"] == "regime_exit"
        assert rows[0]["strategy"] == "mean_reversion"
        assert rows[0]["quantity"] == 2.0
        assert rows[0]["pnl"] == pytest.approx(-20.0)
        assert rows[0]["exit_price_source"] == "fill"
        decisions = [line for line in log_lines if "Regime review decision" in line]
        assert len(decisions) == 1
        for fact in (
            f"symbol={SYMBOL}",
            "strategy=mean_reversion",
            "reason=regime_exit",
            "old_regime=ranging_calm",
            "new_regime=trending_strong",
            "in_profit=False",
            "trend=down",
            "against_trend=True",
        ):
            assert fact in decisions[0]

    def test_short_losing_against_up_trend_is_closed(self, dbm: Any) -> None:
        rig = Rig(dbm, price=110.0, trend="up")
        rig.open(strategy="mean_reversion", side="SHORT")

        rig.regime_change()

        assert [o["side"] for o in rig.venue.orders] == ["buy"]
        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "regime_exit"
        assert rows[0]["side"] == "short"
        assert rows[0]["pnl"] == pytest.approx(-20.0)

    def test_second_change_event_after_the_close_does_nothing(self, rig: Rig) -> None:
        rig.open(strategy="mean_reversion")
        rig.regime_change()
        assert len(rig.venue.orders) == 1 and len(rig.rows()) == 1
        # The positions row is still there until the next loop sync.
        assert rig.dbm.get_position(SYMBOL, "LONG") is not None

        rig.regime_change(old="trending_strong", new="trending_moderate")
        rig.regime_change(old="trending_moderate", new="trending_strong")
        rig.manage()

        assert len(rig.venue.orders) == 1
        assert len(rig.rows()) == 1

    def test_rejected_close_is_retried_by_the_manager_and_recorded_once(
        self, rig: Rig
    ) -> None:
        rig.open(strategy="mean_reversion")
        rig.venue.reject = True

        rig.regime_change()

        assert len(rig.venue.orders) == 1 and rig.rows() == []
        assert (SYMBOL, "long") in rig.manager.get_pending_closes()
        # A second event while the close is pending stacks no order.
        rig.regime_change(old="trending_strong", new="trending_moderate")
        assert len(rig.venue.orders) == 1

        rig.venue.reject = False
        result = rig.manage()

        assert result["pending_closes_retried"] == 1
        assert len(rig.venue.orders) == 2
        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "regime_exit"
        assert rig.manager.get_pending_closes() == {}
        rig.manage()
        assert len(rig.venue.orders) == 2 and len(rig.rows()) == 1


class TestRegimeReviewKeeps:
    def test_losing_but_not_against_trend_is_left_open(
        self, dbm: Any, log_lines: List[str]
    ) -> None:
        """The code's rule: a close needs not-in-profit AND against-trend."""
        rig = Rig(dbm, price=90.0, trend="up")
        rig.open(strategy="mean_reversion")

        rig.regime_change()

        assert rig.untouched()
        assert any("reason=not_in_profit_not_against_trend" in m for m in log_lines)

    def test_in_profit_against_trend_is_kept_with_trailing_stop(
        self, dbm: Any, log_lines: List[str]
    ) -> None:
        rig = Rig(dbm, price=110.0, trend="down")
        rig.open(strategy="mean_reversion")

        rig.regime_change()

        assert rig.venue.orders == [] and rig.rows() == []
        assert rig.manager.get_trailing_stop(SYMBOL, "long") == pytest.approx(
            110.0 - ATR_TRAIL
        )
        tracked = rig.risk.get_migrated_positions(SYMBOL)
        assert len(tracked) == 1 and tracked[0]["side"] == "long"
        # Against the trend at registration: no trend is stored, so a flip
        # to "up" can never be read as a reversal against this long.
        assert tracked[0]["trend_direction"] is None
        assert any(
            "action=arm_trailing_stop" in m and "in_profit=True" in m for m in log_lines
        )

    def test_in_profit_with_trend_arms_trailing_stop(self, dbm: Any) -> None:
        rig = Rig(dbm, price=110.0, trend="up")
        rig.open(strategy="mean_reversion")

        rig.regime_change()

        assert rig.venue.orders == [] and rig.rows() == []
        assert rig.manager.get_trailing_stop(SYMBOL, "long") == pytest.approx(106.0)
        assert rig.risk.get_migrated_positions(SYMBOL)[0]["trend_direction"] == "up"

    def test_trailing_stop_is_never_looser_than_the_stored_stop(self, dbm: Any) -> None:
        rig = Rig(dbm, price=110.0, trend="up")
        rig.open(strategy="mean_reversion", stop=108.0)  # tighter than 106

        rig.regime_change()

        assert rig.manager.get_trailing_stop(SYMBOL, "long") == pytest.approx(108.0)

    def test_stored_stop_looser_than_the_trail_is_tightened(self, dbm: Any) -> None:
        rig = Rig(dbm, price=110.0, trend="up")
        rig.open(strategy="mean_reversion", stop=95.0)

        rig.regime_change()

        assert rig.manager.get_trailing_stop(SYMBOL, "long") == pytest.approx(106.0)

    def test_owner_still_active_is_untouched(self, rig: Rig) -> None:
        rig.open(strategy="mean_reversion")

        rig.regime_change(old="trending_strong", new="ranging_calm")

        assert rig.untouched()

    @pytest.mark.parametrize(
        "strategy",
        ["liquidation_capture", "orderbook_imbalance", "session_range_breakout"],
    )
    def test_overlay_strategy_is_untouched(self, rig: Rig, strategy: str) -> None:
        rig.open(strategy=strategy)

        rig.regime_change()

        assert rig.untouched()

    @pytest.mark.parametrize(
        "strategy,flag",
        [
            ("calendar_flow", "enable_calendar_flow"),
            ("vwap_pullback", "enable_vwap_pullback"),
        ],
    )
    def test_all_regime_strategy_follows_strategy_manager(
        self, dbm: Any, strategy: str, flag: str
    ) -> None:
        """Activation comes from StrategyManager, not a second map.

        These two run in all regimes when enabled but are not in
        OVERLAY_STRATEGIES; enabled -> untouched, disabled -> reviewed.
        """
        enabled = Rig(dbm, extra_strategies={flag: True})
        enabled.open(strategy=strategy)
        enabled.regime_change()
        assert enabled.untouched()

        disabled = Rig(dbm, extra_strategies={flag: False})
        disabled.venue.quantities[(SYMBOL, "long")] = 2.0
        disabled.regime_change()
        assert len(disabled.venue.orders) == 1
        assert disabled.rows()[0]["strategy"] == strategy

    def test_vwap_scalping_active_set_comes_from_strategy_manager(
        self, dbm: Any
    ) -> None:
        """The detector's own map never lists VWAPScalping."""
        rig = Rig(dbm, extra_strategies={"enable_vwap_scalping": True})
        rig.open(strategy="vwap_scalping")
        active = next(iter(rig.strategy_manager.vwap_active_regimes))
        old = next(r for r in MarketRegime if r != active)

        rig.regime_change(old=old.value, new=active.value)

        assert rig.untouched()

    @pytest.mark.parametrize("tag", ["unknown", "migrated", None, "not_a_strategy"])
    def test_position_without_an_owner_is_untouched(
        self, rig: Rig, log_lines: List[str], tag: Optional[str]
    ) -> None:
        rig.open(strategy=tag)

        rig.regime_change()
        rig.regime_change(old="trending_strong", new="trending_moderate")

        assert rig.untouched()
        notes = [m for m in log_lines if "has no owning strategy" in m]
        assert len(notes) == 1  # logged once, not per event

    def test_grid_owned_symbol_is_untouched(self, dbm: Any) -> None:
        rig = Rig(dbm, grid_symbols=(SYMBOL,))
        rig.open(strategy="mean_reversion")

        rig.regime_change()

        assert rig.untouched()

    def test_grid_tagged_position_is_untouched(self, rig: Rig) -> None:
        rig.open(strategy="grid_trading")

        rig.regime_change()

        assert rig.untouched()

    def test_unreadable_grid_state_means_no_review(self, rig: Rig) -> None:
        rig.open(strategy="mean_reversion")
        rig.reviewer._grid_symbol_checker = Mock(side_effect=RuntimeError("boom"))

        rig.regime_change()

        assert rig.untouched()


class TestRegimeReviewMissingData:
    def test_price_unavailable_means_no_action(
        self, dbm: Any, log_lines: List[str]
    ) -> None:
        rig = Rig(dbm, price=None, trend="down")
        rig.open(strategy="mean_reversion")

        rig.regime_change()

        assert rig.untouched()
        assert any("reason=price_unavailable" in m for m in log_lines)

    def test_unreadable_venue_means_no_action(
        self, rig: Rig, log_lines: List[str]
    ) -> None:
        rig.open(strategy="mean_reversion")
        rig.venue.readable = False

        rig.regime_change()

        assert rig.untouched()
        assert any("reason=venue_unreadable" in m for m in log_lines)

    def test_row_whose_venue_position_is_gone_is_left_to_the_sync(
        self, rig: Rig
    ) -> None:
        rig.open(strategy="mean_reversion")
        rig.venue.quantities.clear()

        rig.regime_change()

        assert rig.untouched()

    def test_unresolvable_active_set_means_no_action(self, rig: Rig) -> None:
        rig.open(strategy="mean_reversion")
        rig.reviewer._active_strategy_resolver = Mock(side_effect=RuntimeError("x"))

        rig.regime_change()

        assert rig.untouched()

    def test_positions_table_is_the_source_not_trades(self, rig: Rig) -> None:
        """A hand-written open row in ``trades`` is not a position."""
        rig.dbm.save_trade(
            {
                "symbol": SYMBOL,
                "side": "BUY",
                "strategy": "MEAN_REVERSION",
                "quantity": 2.0,
                "entry_price": 100.0,
                "entry_time": "2026-10-01T00:00:00",
                "status": "open",
                "asset_class": "perpetual",
            }
        )
        rig.venue.quantities[(SYMBOL, "long")] = 2.0

        rig.regime_change()

        assert rig.venue.orders == []


class TestRegimeExitNeedsAChangeEvent:
    def test_nothing_happens_without_an_event(self, rig: Rig) -> None:
        """Startup state: a closable position, no REGIME_CHANGED published."""
        rig.open(strategy="mean_reversion")

        rig.manage()

        assert rig.untouched()

    @pytest.mark.parametrize("old", [None, "trending_strong"])
    def test_event_that_is_not_a_change_is_ignored(
        self, rig: Rig, old: Optional[str]
    ) -> None:
        rig.open(strategy="mean_reversion")

        rig.regime_change(old=old, new="trending_strong")

        assert rig.untouched()

    def test_first_detection_after_start_publishes_no_change(self, dbm: Any) -> None:
        """The real detector accepts its first regime without an event.

        The position was opened under ranging_calm by a strategy that is
        not active in trending_strong.  The first detection after a
        restart is trending_strong: nothing is closed.  Only a later,
        confirmed switch (two detections) is a change event.
        """
        rig = Rig(dbm)
        rig.open(strategy="mean_reversion")
        clock = [datetime(2026, 10, 10, 12, 0, 0)]
        script = [
            MarketRegime.TRENDING_STRONG,  # first detection after start
            MarketRegime.TRENDING_MODERATE,  # candidate (1 of 2)
            MarketRegime.TRENDING_MODERATE,  # confirmed -> REGIME_CHANGED
        ]
        rig.detector._clock = lambda: clock[0]
        rig.detector._event_bus = rig.bus
        rig.detector.detect_regime = (
            lambda market_data, previous_regime=None: script.pop(0)
        )

        assert rig.detector.detect_regime_cached(SYMBOL, CANDLES) == (
            MarketRegime.TRENDING_STRONG
        )
        assert rig.bus.get_events_by_type(EventType.REGIME_CHANGED) == []
        assert rig.untouched()

        clock[0] += timedelta(hours=2)
        rig.detector.detect_regime_cached(SYMBOL, CANDLES)
        assert rig.untouched()

        clock[0] += timedelta(hours=2)
        assert rig.detector.detect_regime_cached(SYMBOL, CANDLES) == (
            MarketRegime.TRENDING_MODERATE
        )
        assert len(rig.bus.get_events_by_type(EventType.REGIME_CHANGED)) == 1
        assert len(rig.venue.orders) == 1
        assert rig.rows()[0]["close_reason"] == "regime_exit"


# ----------------------------------------------------------------------
# Max-hold time exit
# ----------------------------------------------------------------------


class TestTimeExit:
    def test_older_than_max_hold_is_closed_once(
        self, rig: Rig, log_lines: List[str]
    ) -> None:
        rig.open(strategy="session_range_breakout", age_hours=5.0)  # max 4h

        result = rig.manage()

        assert result["positions_closed"] == 1
        assert len(rig.venue.orders) == 1
        assert rig.venue.orders[0]["reduce_only"] is True
        rows = rig.rows()
        assert len(rows) == 1
        assert rows[0]["close_reason"] == "time_exit"
        assert rows[0]["strategy"] == "session_range_breakout"
        assert rows[0]["quantity"] == 2.0
        decisions = [line for line in log_lines if "TIME EXIT decision" in line]
        assert len(decisions) == 1
        for fact in (
            f"symbol={SYMBOL}",
            "strategy=session_range_breakout",
            "reason=time_exit",
            "held=5.00h",
            "max_hold=4.0h",
        ):
            assert fact in decisions[0]

    def test_second_pass_after_the_close_does_nothing(self, rig: Rig) -> None:
        rig.open(strategy="session_range_breakout", age_hours=5.0)
        rig.manage()
        assert rig.dbm.get_position(SYMBOL, "LONG") is not None

        second = rig.manage()

        assert second["positions_closed"] == 0
        assert len(rig.venue.orders) == 1 and len(rig.rows()) == 1

    def test_younger_than_max_hold_is_untouched(self, rig: Rig) -> None:
        rig.open(strategy="session_range_breakout", age_hours=3.9)

        result = rig.manage()

        assert result["positions_closed"] == 0
        assert rig.untouched()

    def test_configured_hours_are_read_from_the_environment(
        self, dbm: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORB_TIME_EXIT_HOURS", "1.5")
        rig = Rig(dbm)
        rig.open(strategy="session_range_breakout", age_hours=2.0)

        rig.manage()

        assert rig.rows()[0]["close_reason"] == "time_exit"

    def test_only_session_range_breakout_has_a_max_hold(self, rig: Rig) -> None:
        assert rig.manager._max_hold_hours == {"session_range_breakout": 4.0}

    @pytest.mark.parametrize(
        "strategy",
        [
            "mean_reversion",
            "ma_crossover",
            "momentum_scalping",
            "liquidation_capture",
            "vwap_pullback",
            "calendar_flow",
        ],
    )
    def test_strategy_without_a_max_hold_is_untouched(
        self, rig: Rig, strategy: str
    ) -> None:
        rig.open(strategy=strategy, age_hours=500.0)

        result = rig.manage()

        assert result["positions_closed"] == 0
        assert rig.untouched()

    @pytest.mark.parametrize("tag", ["unknown", "migrated", None])
    def test_position_without_an_owner_is_untouched(
        self, rig: Rig, tag: Optional[str]
    ) -> None:
        rig.open(strategy=tag, age_hours=500.0)

        rig.manage()

        assert rig.untouched()

    def test_second_strategy_on_the_position_makes_it_unknown(self, rig: Rig) -> None:
        rig.open(strategy="session_range_breakout", age_hours=5.0)
        rig.dbm.tag_position_owner(SYMBOL, "LONG", "mean_reversion")

        rig.manage()

        assert rig.untouched()

    @pytest.mark.parametrize("opened_at", ["now", "", "garbage"])
    def test_unreadable_entry_time_never_expires(
        self, rig: Rig, log_lines: List[str], opened_at: str
    ) -> None:
        rig.open(strategy="session_range_breakout", opened_at=opened_at or " ")

        rig.manage()
        rig.manage()

        assert rig.untouched()
        notes = [m for m in log_lines if "TIME EXIT inactive" in m]
        assert len(notes) == 1

    def test_price_unavailable_defers_the_exit(self, dbm: Any) -> None:
        rig = Rig(dbm, price=None)
        rig.open(strategy="session_range_breakout", age_hours=5.0)

        result = rig.manage()

        assert result["positions_closed"] == 0
        assert rig.untouched()

    def test_unreadable_venue_defers_the_exit(self, rig: Rig) -> None:
        rig.open(strategy="session_range_breakout", age_hours=5.0)
        rig.venue.readable = False

        result = rig.manage()

        assert result["positions_closed"] == 0
        assert rig.venue.orders == [] and rig.rows() == []

        rig.venue.readable = True
        assert rig.manage()["positions_closed"] == 1
        assert len(rig.rows()) == 1

    def test_grid_owned_symbol_is_untouched(self, dbm: Any) -> None:
        rig = Rig(dbm, grid_symbols=(SYMBOL,))
        rig.open(strategy="session_range_breakout", age_hours=5.0)

        rig.manage()

        assert rig.untouched()

    def test_registered_position_finds_its_owner_on_the_positions_row(
        self, rig: Rig
    ) -> None:
        """The registry record has no strategy key; the row tag is used."""
        rig.open(strategy="session_range_breakout", age_hours=5.0)
        rig.risk.register_migrated_position(
            SYMBOL, {"side": "long", "qty": 2.0, "entry_price": 100.0, "has_stop": True}
        )
        assert "strategy" not in rig.risk.get_migrated_positions(SYMBOL)[0]

        result = rig.manage()

        assert result["positions_closed"] == 1
        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "time_exit"
        assert rig.risk.get_migrated_positions() == []
        rig.manage()
        assert len(rig.venue.orders) == 1 and len(rig.rows()) == 1

    def test_registered_position_age_is_the_entry_not_the_registration(
        self, rig: Rig
    ) -> None:
        rig.open(strategy="session_range_breakout", age_hours=1.0)
        rig.risk.register_migrated_position(
            SYMBOL, {"side": "long", "qty": 2.0, "entry_price": 100.0, "has_stop": True}
        )
        rig.risk.migrated_positions[SYMBOL][0]["migrated_at"] = time.time() - 36000

        assert rig.manager._check_time_exit(SYMBOL, {"side": "long"}) is False

    def test_rejected_close_is_retried_up_to_the_cap_and_recorded_once(
        self, rig: Rig
    ) -> None:
        rig.open(strategy="session_range_breakout", age_hours=5.0)
        rig.venue.reject = True

        rig.manage()
        rig.manage()

        assert len(rig.venue.orders) == 2 and rig.rows() == []
        assert rig.manager.get_pending_closes()[(SYMBOL, "long")]["attempts"] == 2

        rig.venue.reject = False
        rig.manage()

        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "time_exit"
        assert rig.manager.get_pending_closes() == {}
        rig.manage()
        assert len(rig.venue.orders) == 3 and len(rig.rows()) == 1

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("2026-10-10T08:00:00+00:00", 1791619200.0),
            ("2026-10-10 08:00:00", 1791619200.0),
            ("2026-10-10T08:00:00Z", 1791619200.0),
            (1791619200, 1791619200.0),
            ("1791619200000", 1791619200.0),
            ("now", None),
            (None, None),
            ("", None),
            (0, None),
        ],
    )
    def test_entry_time_parsing(self, value: Any, expected: Optional[float]) -> None:
        assert _epoch_seconds(value) == expected


# ----------------------------------------------------------------------
# Circuit breaker: entries are blocked, exits still run
# ----------------------------------------------------------------------


class TestExitsRunWhileBreakerTripped:
    def _tripped_bot(self, rig: Rig) -> SimpleNamespace:
        bot = SimpleNamespace(
            client=rig.client,
            migrated_position_manager=rig.manager,
            _circuit_breaker_triggered=True,
            _circuit_breaker_reason="portfolio loss",
            _get_ticker_ws=lambda symbol: {"last": rig.venue.price},
        )
        bot._manage_migrated_positions = TradingBot._manage_migrated_positions.__get__(
            bot
        )
        assert _breaker_block_reason(bot) is not None  # entries are blocked
        return bot

    def test_time_exit_runs(self, rig: Rig) -> None:
        rig.open(strategy="session_range_breakout", age_hours=5.0)
        bot = self._tripped_bot(rig)

        bot._manage_migrated_positions()

        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "time_exit"

    def test_regime_exit_runs(self, rig: Rig) -> None:
        rig.open(strategy="mean_reversion")
        self._tripped_bot(rig)

        rig.regime_change()

        rows = rig.rows()
        assert len(rows) == 1 and rows[0]["close_reason"] == "regime_exit"


# ----------------------------------------------------------------------
# Wiring: a real TradingBot connects both paths to the live sources
# ----------------------------------------------------------------------


class TestTradingBotWiring:
    @pytest.fixture
    def bot(self) -> TradingBot:
        client = create_autospec(PacificaClient, instance=True)
        client.get_positions.return_value = []
        client.get_market_data.return_value = {"symbol": SYMBOL, "price": 90.0}
        return TradingBot(db=Mock(), client=client)

    def test_reviewer_uses_strategy_manager_activation(self, bot: TradingBot) -> None:
        reviewer = bot.regime_position_reviewer
        assert reviewer._active_strategy_resolver == (
            bot.strategy_manager.get_active_strategy_names
        )
        assert reviewer.db is bot.db
        assert reviewer.migrated_position_manager is bot.migrated_position_manager

    def test_flag_defaults_to_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENABLE_REGIME_POSITION_REVIEW", raising=False)
        client = create_autospec(PacificaClient, instance=True)
        client.get_positions.return_value = []
        bot = TradingBot(db=Mock(), client=client)
        assert bot.regime_position_reviewer.enabled is True

    def test_construction_reviews_nothing(self, bot: TradingBot) -> None:
        """No REGIME_CHANGED at construction: the db is never asked."""
        bot.db.get_position.assert_not_called()
        bot.client.place_order.assert_not_called()

    @pytest.mark.parametrize(
        "state,expected",
        [
            (GridState.ACTIVE, True),
            (GridState.EMERGENCY_EXIT, True),
            (GridState.DISABLED_BY_REGIME, True),
            (GridState.CLOSED, False),
            (GridState.IDLE, False),
        ],
    )
    def test_grid_checker_reads_the_grid_lifecycle(
        self, bot: TradingBot, state: GridState, expected: bool
    ) -> None:
        assert bot._symbol_has_grid(SYMBOL) is False
        bot.grid_lifecycle._grids[SYMBOL] = {"state": state}

        assert bot._symbol_has_grid(SYMBOL) is expected
        assert bot.regime_position_reviewer._grid_owns_symbol(SYMBOL) is expected
        assert bot.migrated_position_manager._grid_tracked(SYMBOL, "long") is expected

    def test_strategy_manager_activation_matches_signal_path_rules(
        self, bot: TradingBot
    ) -> None:
        """Grid is removed in trending regimes; the detector map is the base."""
        sm = bot.strategy_manager
        names = sm.get_active_strategy_names(MarketRegime.RANGING_CALM, SYMBOL)
        assert "MeanReversion" in names
        trending = sm.get_active_strategy_names(MarketRegime.TRENDING_STRONG, SYMBOL)
        assert "GridTrading" not in trending and "MeanReversion" not in trending
        assert "MACrossover" in trending


# ----------------------------------------------------------------------
# One decision at a time: the review may run on an API thread
# ----------------------------------------------------------------------


class TestCloseDecisionsAreSerialized:
    """REGIME_CHANGED is delivered on the publisher's thread.

    The API status endpoint runs regime detection on the bot's detector,
    so the review can run beside the trading loop's manage_positions.
    Both hold the manager's operation_lock while they decide and close.
    """

    @staticmethod
    def _probe(rig: Rig) -> List[bool]:
        """Record, at order time, whether another thread could take the lock."""
        taken: List[bool] = []
        place = rig.venue.place_order

        def place_order(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            def other_thread() -> None:
                got = rig.manager.operation_lock.acquire(timeout=0.05)
                taken.append(got)
                if got:
                    rig.manager.operation_lock.release()

            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join()
            return place(*args, **kwargs)

        rig.client.place_order.side_effect = place_order
        return taken

    def test_regime_exit_holds_the_manager_lock(self, rig: Rig) -> None:
        rig.open(strategy="mean_reversion")
        taken = self._probe(rig)

        rig.regime_change()

        assert taken == [False]
        assert len(rig.rows()) == 1

    def test_time_exit_holds_the_manager_lock(self, rig: Rig) -> None:
        rig.open(strategy="session_range_breakout", age_hours=5.0)
        taken = self._probe(rig)

        rig.manage()

        assert taken == [False]
        assert len(rig.rows()) == 1

    def test_lock_is_released_afterwards(self, rig: Rig) -> None:
        rig.open(strategy="mean_reversion")
        rig.regime_change()

        assert rig.manager.operation_lock.acquire(timeout=0.05)
        rig.manager.operation_lock.release()

    def test_review_holds_the_lock_while_it_reads_the_venue(self, rig: Rig) -> None:
        """The decision itself, not only the order, is inside the lock."""
        rig.open(strategy="mean_reversion")
        taken: List[bool] = []
        read = rig.venue.get_positions

        def get_positions() -> List[Dict[str, Any]]:
            def other_thread() -> None:
                got = rig.manager.operation_lock.acquire(timeout=0.05)
                taken.append(got)
                if got:
                    rig.manager.operation_lock.release()

            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join()
            return read()

        rig.client.get_positions.side_effect = get_positions

        rig.regime_change()

        assert taken and not any(taken)


class TestTimeExitOwnerLookup:
    """Each owner check on its own (they back each other up in the loop)."""

    @pytest.mark.parametrize("tag", ["unknown", "migrated", None, "mean_reversion"])
    def test_registered_position_without_a_max_hold_owner_is_not_due(
        self, rig: Rig, tag: Optional[str]
    ) -> None:
        rig.open(strategy=tag, age_hours=500.0)
        rig.risk.register_migrated_position(
            SYMBOL, {"side": "long", "qty": 2.0, "entry_price": 100.0, "has_stop": True}
        )
        rig.manager._trailing_stops[SYMBOL] = {"long": 50.0}

        result = rig.manage()

        assert result["positions_closed"] == 0
        assert rig.venue.orders == [] and rig.rows() == []

    @pytest.mark.parametrize("tag", ["unknown", "migrated", None, "mean_reversion"])
    def test_only_max_hold_owners_are_time_exit_candidates(
        self, rig: Rig, tag: Optional[str]
    ) -> None:
        rig.open(strategy=tag, age_hours=500.0)

        assert rig.manager._owned_time_exit_positions(set()) == []

    def test_max_hold_owner_is_a_candidate(self, rig: Rig) -> None:
        rig.open(strategy="session_range_breakout", age_hours=1.0)

        candidates = rig.manager._owned_time_exit_positions(set())

        assert [(c["symbol"], c["side"], c["strategy"]) for c in candidates] == [
            (SYMBOL, "long", "session_range_breakout")
        ]
        assert rig.manager._owned_time_exit_positions({(SYMBOL, "long")}) == []


class TestFailedArmingLeavesTheOriginalStop:
    def test_position_is_not_left_registered_without_a_trailing_stop(
        self, dbm: Any
    ) -> None:
        """A registered position is skipped by local stop enforcement."""
        rig = Rig(dbm, price=110.0, trend="up")
        rig.open(strategy="mean_reversion", stop=95.0)
        rig.manager._update_trailing_stop = Mock(side_effect=RuntimeError("boom"))
        rig.manager._trailing_stops.clear()
        rig.manager.arm_trailing_stop = Mock(side_effect=RuntimeError("boom"))

        rig.regime_change()  # must not raise

        assert rig.risk.get_migrated_positions() == []
        assert rig.venue.orders == [] and rig.rows() == []
