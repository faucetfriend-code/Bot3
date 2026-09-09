"""
Tests for trailing exits: VWAPPullback's trailing signal shape and the
engine's stop-ratchet sweep against scripted bars.
"""

from trading_bot_v2.backtesting.cost_model import CostTable
from trading_bot_v2.backtesting.engine import BacktestEngine
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.tests.test_vwap_pullback import (
    BULL_4H,
    _long_session,
    _strategy,
)


def _bar(h, lo):
    return {
        "open": (h + lo) / 2,
        "high": h,
        "low": lo,
        "close": (h + lo) / 2,
        "volume": 1000,
    }


def _exchange():
    costs = CostTable(
        profile="legacy",
        base_maker_fee_pct=0.0,
        base_taker_fee_pct=0.0,
        base_slippage_pct=0.0,
        read_env_overrides=False,
    )
    return SimulatedExchange(
        initial_capital=10000.0, cost_table=costs, funding_hourly_pct=0.0
    )


def _engine():
    engine = BacktestEngine()
    engine._position_trailing = {}
    return engine


def _open_long(exchange, entry=100.0, qty="1"):
    exchange.advance(_bar(entry, entry), "2024-01-01T00:00:00")
    exchange.place_order(
        symbol="BTC-USDC", side="bid", quantity=qty, order_type="market"
    )
    return exchange._positions["BTC-USDC"]


def _stops(exchange):
    return [
        o
        for o in exchange._orders.values()
        if o.symbol == "BTC-USDC" and o.status == "open" and o.order_type == "stop"
    ]


class TestStrategyTrailingSignal:
    def test_trailing_signal_has_no_take_profit_and_carries_config(self):
        strategy = _strategy(exit_mode="trailing", trail_activation_r=1.0, trail_r=0.75)
        sig = strategy.generate_signals(
            "BTC-USDC", {"15m": _long_session(), "4h": BULL_4H}, 108.0
        )[0]
        assert sig.take_profit is None
        assert sig.stop_loss is not None
        trailing = sig.indicators["trailing"]
        assert trailing["activation_r"] == 1.0
        assert trailing["trail_r"] == 0.75
        assert trailing["risk"] > 0
        assert sig.rrr_meets_minimum  # passes by design without a target

    def test_fixed_mode_unchanged(self):
        strategy = _strategy()
        sig = strategy.generate_signals(
            "BTC-USDC", {"15m": _long_session(), "4h": BULL_4H}, 108.0
        )[0]
        assert sig.take_profit is not None
        assert sig.indicators["trailing"] is None

    def test_unknown_exit_mode_falls_back_to_fixed(self):
        assert _strategy(exit_mode="banana").exit_mode == "fixed"


class TestEngineRatchet:
    def _tracked(self, engine, risk=2.0, activation=1.0, trail=1.0, placed=98.0):
        engine._position_trailing["BTC-USDC"] = {
            "activation_r": activation,
            "trail_r": trail,
            "risk": risk,
            "side": "long",
            "strategy": "vwap_pullback",
            "peak": None,
            "active": False,
            "placed_stop": placed,
            "seen_position": True,
        }

    def test_no_ratchet_before_activation(self):
        ex = _exchange()
        _open_long(ex, 100.0)
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="stop", price=98.0
        )
        engine = _engine()
        self._tracked(engine, risk=2.0, activation=1.0)
        # High 101.5 < entry + 1R (102): not active, stop untouched
        engine._apply_trailing_stops(ex, _bar(101.5, 100.5))
        assert engine._position_trailing["BTC-USDC"]["active"] is False
        assert _stops(ex)[0].price == 98.0

    def test_ratchet_replaces_stop_behind_peak(self):
        ex = _exchange()
        _open_long(ex, 100.0)
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="stop", price=98.0
        )
        engine = _engine()
        self._tracked(engine, risk=2.0, activation=1.0, trail=1.0)
        # High 104 >= 102 activates; new stop = 104 - 2 = 102
        engine._apply_trailing_stops(ex, _bar(104.0, 101.0))
        stops = _stops(ex)
        assert len(stops) == 1
        assert stops[0].price == 102.0
        # Peak only ratchets up: a lower bar must NOT lower the stop
        engine._apply_trailing_stops(ex, _bar(102.5, 101.5))
        assert _stops(ex)[0].price == 102.0
        # New high 106 -> stop follows to 104
        engine._apply_trailing_stops(ex, _bar(106.0, 103.0))
        assert _stops(ex)[0].price == 104.0

    def test_ratcheted_stop_fills_on_retrace(self):
        ex = _exchange()
        _open_long(ex, 100.0)
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="stop", price=98.0
        )
        engine = _engine()
        self._tracked(engine, risk=2.0)
        engine._apply_trailing_stops(ex, _bar(106.0, 101.0))  # stop -> 104
        # Retrace through 104 fills the stop and closes the position
        ex.advance(_bar(105.0, 103.0), "2024-01-01T00:05:00")
        assert "BTC-USDC" not in ex._positions
        closed = [t for t in ex.trade_log if t.get("pnl", 0) != 0]
        assert len(closed) == 1
        assert closed[0]["pnl"] > 0  # locked in above entry

    def test_tracking_cleared_after_close(self):
        ex = _exchange()
        _open_long(ex, 100.0)
        engine = _engine()
        self._tracked(engine)
        # Close the position manually
        ex.place_order(symbol="BTC-USDC", side="ask", quantity="1", order_type="market")
        engine._apply_trailing_stops(ex, _bar(100.0, 100.0))
        assert "BTC-USDC" not in engine._position_trailing
