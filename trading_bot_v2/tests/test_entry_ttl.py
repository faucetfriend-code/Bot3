"""
Tests for the engine's entry-order TTL sweep (maker-mode entries).

The hazard being pinned: a resting limit entry that never fills leaves
its exit orders (stop + take-profit) alive on the book, and a
take-profit limit can then fill as an INVERTED position. The TTL sweep
cancels the whole order set together once the entry expires unfilled.
"""

from trading_bot_v2.backtesting.cost_model import CostTable
from trading_bot_v2.backtesting.engine import BacktestEngine
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange

FLAT_BAR = {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000}


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


def _engine_with(exchange):
    engine = BacktestEngine()
    engine._pending_entry_ttl = {}
    return engine


def _open_orders(exchange, symbol):
    return [
        o
        for o in exchange._orders.values()
        if o.symbol == symbol and o.status == "open"
    ]


class TestEntryTTL:
    def test_expired_unfilled_entry_cancels_entry_and_exits(self):
        ex = _exchange()
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")
        # Resting long entry below market + naked exit set
        ex.place_order(
            symbol="BTC-USDC", side="bid", quantity="1", order_type="limit", price=99.0
        )
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="stop", price=97.0
        )
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="limit", price=103.0
        )
        assert len(_open_orders(ex, "BTC-USDC")) == 3

        engine = _engine_with(ex)
        engine._pending_entry_ttl["BTC-USDC"] = 10

        engine._expire_stale_entries(ex, candle_idx=9)  # before deadline
        assert len(_open_orders(ex, "BTC-USDC")) == 3

        engine._expire_stale_entries(ex, candle_idx=10)  # at deadline
        assert len(_open_orders(ex, "BTC-USDC")) == 0
        assert "BTC-USDC" not in engine._pending_entry_ttl

    def test_filled_entry_keeps_exits_alive(self):
        ex = _exchange()
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")
        # Market entry fills immediately -> position exists
        ex.place_order(symbol="BTC-USDC", side="bid", quantity="1", order_type="market")
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="stop", price=97.0
        )
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="limit", price=103.0
        )
        assert "BTC-USDC" in ex._positions

        engine = _engine_with(ex)
        engine._pending_entry_ttl["BTC-USDC"] = 10

        engine._expire_stale_entries(ex, candle_idx=50)  # well past deadline
        # Position exists -> tracking dropped, exits untouched
        assert len(_open_orders(ex, "BTC-USDC")) == 2
        assert "BTC-USDC" not in engine._pending_entry_ttl

    def test_naked_takeprofit_cannot_invert_after_expiry(self):
        ex = _exchange()
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")
        ex.place_order(
            symbol="BTC-USDC", side="bid", quantity="1", order_type="limit", price=99.0
        )
        ex.place_order(
            symbol="BTC-USDC", side="ask", quantity="1", order_type="limit", price=103.0
        )  # the would-be TP

        engine = _engine_with(ex)
        engine._pending_entry_ttl["BTC-USDC"] = 5
        engine._expire_stale_entries(ex, candle_idx=5)

        # Price rallies through the old TP level - nothing may fill
        rally = {"open": 100, "high": 105, "low": 100, "close": 104, "volume": 1000}
        ex.advance(rally, "2024-01-01T00:05:00")
        assert "BTC-USDC" not in ex._positions
