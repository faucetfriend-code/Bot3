"""B01 / B03: position managers must get a price from a live client.

``RegimePositionReviewer._get_current_price`` and
``MigratedPositionManager._get_current_price`` called
``client.get_ticker(symbol)``. The live REST clients (PacificaClient,
BlofinClient) have no ``get_ticker``; the AttributeError was swallowed and
the price came back None. Effects:

* regime review: ``in_profit`` was always False, so an in-profit position
  that was against the 4h trend was closed at market instead of being kept
  under a trailing stop;
* migrated manager: a position missing from ``current_prices`` got no
  stop / trailing / take-profit check that cycle.

The client doubles here are spec'd on the real PacificaClient, so a call
to ``get_ticker`` fails exactly as it does live.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, Mock, create_autospec

import pytest

from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.blofin_client import BlofinClient
from trading_bot_v2.event_system import Event, EventType
from trading_bot_v2.migrated_position_manager import MigratedPositionManager
from trading_bot_v2.pacifica_client import PacificaClient
from trading_bot_v2.price_lookup import fetch_current_price
from trading_bot_v2.regime_position_review import RegimePositionReviewer
from trading_bot_v2.trading_bot import TradingBot

SYMBOL = "SUI-PERP"


def _live_client(market_data: Optional[Dict[str, Any]] = None) -> Any:
    """PacificaClient-spec'd double: it has no ``get_ticker``."""
    client = create_autospec(PacificaClient, instance=True)
    client.get_market_data.return_value = market_data if market_data else {}
    return client


def _ws_ticker(price: float) -> Dict[str, Any]:
    """Ticker in the shape TradingBot._get_ticker_ws returns."""
    return {"symbol": SYMBOL, "last": price, "volume": 0, "_source": "ws_last_only"}


class TestLiveClientsHaveNoGetTicker:
    """The premise of the bug, pinned so the doubles stay honest."""

    @pytest.mark.parametrize("client_cls", [PacificaClient, BlofinClient])
    def test_real_client_class_has_no_get_ticker(self, client_cls):
        assert not hasattr(client_cls, "get_ticker")
        assert callable(getattr(client_cls, "get_market_data", None))

    def test_specced_double_rejects_get_ticker(self):
        with pytest.raises(AttributeError):
            _live_client().get_ticker(SYMBOL)


class TestFetchCurrentPrice:
    def test_live_client_uses_get_market_data(self):
        client = _live_client({"symbol": "SUI", "price": 1.25})
        assert fetch_current_price(client, SYMBOL) == 1.25
        client.get_market_data.assert_called_once_with("SUI")

    def test_venue_mark_field_is_read(self):
        client = _live_client({"symbol": "SUI", "mark": "1.3"})
        assert fetch_current_price(client, SYMBOL) == 1.3

    def test_injected_lookup_wins_and_client_is_not_asked(self):
        client = _live_client({"symbol": "SUI", "price": 9.0})
        price = fetch_current_price(client, SYMBOL, lambda s: _ws_ticker(1.5))
        assert price == 1.5
        client.get_market_data.assert_not_called()

    @pytest.mark.parametrize(
        "ticker", [None, {}, {"last": 0}, {"last": "abc"}, {"last": -1.0}]
    )
    def test_unusable_ticker_is_none(self, ticker):
        assert fetch_current_price(_live_client(), SYMBOL, lambda s: ticker) is None

    def test_empty_market_data_is_none(self):
        assert fetch_current_price(_live_client(), SYMBOL) is None

    def test_backtest_exchange_get_ticker_still_works(self):
        """SimulatedExchange (string prices, real get_ticker) keeps working."""
        exchange = SimulatedExchange(initial_capital=1000.0)
        exchange._current_price = 2.5
        assert fetch_current_price(exchange, SYMBOL) == 2.5


# ----------------------------------------------------------------------
# B01: regime-flip position review
# ----------------------------------------------------------------------


def _reviewer(client: Any, trend: str, price_lookup: Any = None):
    """Reviewer whose active strategies exclude the position's owner."""
    db = MagicMock()
    db.get_open_trades.return_value = [
        {
            "id": 1,
            "symbol": SYMBOL,
            "side": "BUY",
            "quantity": 2.0,
            "entry_price": 1.0,
            "strategy": "MOMENTUM_SCALPING",
        }
    ]
    detector = MagicMock()
    detector.get_active_strategies.return_value = ["MeanReversion", "GridTrading"]
    detector.get_trend_direction.return_value = trend
    mpm = MagicMock()
    mpm.risk_manager.get_migrated_positions.return_value = []
    fetcher = MagicMock()
    fetcher.get_candles_multi_tf.return_value = {"4h": {"close": [1.0] * 200}}
    reviewer = RegimePositionReviewer(
        db=db,
        client=client,
        regime_detector=detector,
        migrated_position_manager=mpm,
        multi_tf_fetcher=fetcher,
        enabled=True,
        price_lookup=price_lookup,
    )
    return reviewer, mpm


def _regime_event() -> Event:
    return Event(
        EventType.REGIME_CHANGED,
        {
            "symbol": SYMBOL,
            "old_regime": "trending_strong",
            "new_regime": "ranging_calm",
        },
        source="test",
    )


class TestRegimeReviewPriceSource:
    def test_in_profit_against_trend_is_trailed_not_closed(self):
        """Long from 1.0, now 1.2, 4h trend down, strategy no longer active.

        Before the fix the price was None, ``in_profit`` False, and this
        position was closed at market with reason ``regime_exit``.
        """
        client = _live_client({"symbol": "SUI", "price": 1.2})
        reviewer, mpm = _reviewer(client, trend="down")

        reviewer.handle_regime_changed(_regime_event())

        mpm.arm_trailing_stop.assert_called_once()
        symbol, pos, price, _market_data = mpm.arm_trailing_stop.call_args.args
        assert (symbol, pos["side"], price) == (SYMBOL, "long", 1.2)
        mpm.close_position.assert_not_called()

    def test_injected_bot_lookup_is_used(self):
        client = _live_client()
        reviewer, mpm = _reviewer(
            client, trend="down", price_lookup=lambda s: _ws_ticker(1.2)
        )

        reviewer.handle_regime_changed(_regime_event())

        mpm.arm_trailing_stop.assert_called_once()
        assert mpm.arm_trailing_stop.call_args.args[2] == 1.2
        mpm.close_position.assert_not_called()
        client.get_market_data.assert_not_called()

    def test_in_profit_not_against_trend_arms_trailing(self):
        """Second half of B01: this case used to be "log only"."""
        client = _live_client({"symbol": "SUI", "price": 1.2})
        reviewer, mpm = _reviewer(client, trend="up")

        reviewer.handle_regime_changed(_regime_event())

        mpm.arm_trailing_stop.assert_called_once()
        mpm.close_position.assert_not_called()

    def test_losing_against_trend_is_still_closed(self):
        """A real price must not turn every review into a trailing stop."""
        client = _live_client({"symbol": "SUI", "price": 0.8})
        reviewer, mpm = _reviewer(client, trend="down")

        reviewer.handle_regime_changed(_regime_event())

        mpm.arm_trailing_stop.assert_not_called()
        mpm.close_position.assert_called_once()
        assert mpm.close_position.call_args.args[2] == "regime_exit"

    def test_price_source_failure_degrades_to_no_price(self):
        """Unchanged behaviour when no price exists: handler does not raise."""

        def failing(symbol: str) -> Dict[str, Any]:
            raise RuntimeError("Both WebSocket and REST API failed")

        reviewer, mpm = _reviewer(_live_client(), trend="down", price_lookup=failing)

        reviewer.handle_regime_changed(_regime_event())

        mpm.arm_trailing_stop.assert_not_called()
        mpm.close_position.assert_called_once()


# ----------------------------------------------------------------------
# B03: migrated position manager fallback price
# ----------------------------------------------------------------------


class _RiskManager:
    """Minimal migrated-position registry."""

    def __init__(self, positions: List[Dict[str, Any]]):
        self.positions = [dict(p) for p in positions]

    def get_migrated_positions(self, symbol: Optional[str] = None):
        return [dict(p) for p in self.positions]

    def has_migrated_positions(self, symbol: Optional[str] = None) -> bool:
        return bool(self.positions)


LONG_POS = {"symbol": SYMBOL, "side": "long", "qty": 2.0, "entry_price": 100.0}


def _manager(client: Any, price_lookup: Any = None) -> MigratedPositionManager:
    manager = MigratedPositionManager(
        client,
        _RiskManager([LONG_POS]),
        regime_detector=None,
        price_lookup=price_lookup,
    )
    manager._trailing_stops[SYMBOL] = {"long": 95.0}
    manager._close_position = MagicMock()
    manager._check_stop_hit = MagicMock(wraps=manager._check_stop_hit)
    return manager


class TestMigratedManagerPriceSource:
    def test_empty_current_prices_still_runs_stop_check(self):
        """Price 90 is below the 95 stop: the stop check must see it.

        Before the fix the fallback returned None and the loop ``continue``d
        before any stop / trailing / take-profit check.
        """
        client = _live_client({"symbol": "SUI", "price": 90.0})
        manager = _manager(client)

        results = manager.manage_positions(current_prices={})

        manager._check_stop_hit.assert_called_once()
        assert manager._check_stop_hit.call_args.args[2] == 90.0
        manager._close_position.assert_called_once()
        assert manager._close_position.call_args.args[2] == "trailing_stop"
        assert results["positions_closed"] == 1
        assert results["errors"] == []

    def test_injected_bot_lookup_is_used(self):
        client = _live_client()
        manager = _manager(client, price_lookup=lambda s: _ws_ticker(90.0))

        results = manager.manage_positions(current_prices=None)

        assert manager._check_stop_hit.call_args.args[2] == 90.0
        assert results["positions_closed"] == 1
        client.get_market_data.assert_not_called()

    def test_price_above_stop_does_not_close(self):
        client = _live_client({"symbol": "SUI", "price": 99.0})
        manager = _manager(client)

        results = manager.manage_positions(current_prices={})

        manager._check_stop_hit.assert_called_once()
        manager._close_position.assert_not_called()
        assert results["positions_closed"] == 0

    def test_supplied_price_does_not_touch_the_fallback(self):
        client = _live_client()
        lookup = MagicMock()
        manager = _manager(client, price_lookup=lookup)

        manager.manage_positions(current_prices={SYMBOL: 99.0})

        lookup.assert_not_called()
        client.get_market_data.assert_not_called()

    def test_no_price_anywhere_reports_error_and_skips(self):
        manager = _manager(_live_client())

        results = manager.manage_positions(current_prices={})

        manager._check_stop_hit.assert_not_called()
        assert results["errors"] == [f"Could not get price for {SYMBOL}"]


# ----------------------------------------------------------------------
# Wiring: a real TradingBot hands both objects its own price lookup
# ----------------------------------------------------------------------


class TestTradingBotWiring:
    @pytest.fixture
    def bot(self):
        client = create_autospec(PacificaClient, instance=True)
        client.get_positions.return_value = []
        client.get_market_data.return_value = {"symbol": "SUI", "price": 1.2}
        return TradingBot(db=Mock(), client=client)

    def test_rest_fallback_reaches_both_objects(self, bot):
        """No WS client: the real _get_ticker_ws falls back to REST."""
        bot.ws_client = None

        assert bot.regime_position_reviewer._get_current_price(SYMBOL) == 1.2
        assert bot.migrated_position_manager._get_current_price(SYMBOL) == 1.2
        bot.client.get_market_data.assert_called_with("SUI")

    def test_ws_price_reaches_both_objects(self, bot):
        bot.ws_client = SimpleNamespace(_running=True, get_price=lambda s: 1.4)

        assert bot.regime_position_reviewer._get_current_price(SYMBOL) == 1.4
        assert bot.migrated_position_manager._get_current_price(SYMBOL) == 1.4
        bot.client.get_market_data.assert_not_called()
