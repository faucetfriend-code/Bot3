"""
Grid pipeline regression tests.

Each test class reproduces a defect found in the July 2026 grid-trading
debugging session:

1. Grid signals did not carry their geometry (signal.spacing /
   signal.grid_levels were None), so grids were registered with spacing=0
   and fills could never be replenished (ladder was dead).
2. Nothing called GridLifecycleManager.on_regime_disallowed, so grid orders
   were orphaned on the exchange after regime flips.
3. _force_exit sent "buy" for LONG positions (side check used "bid" while
   Pacifica reports "long"/"short"), DOUBLING positions instead of closing.
4. Replenish rounding used a hardcoded 0.01 tick that collapsed sub-cent
   ladders on low-priced symbols.
5. Re-adoption paths stored RELATIVE spacing (fraction of price) where a
   DOLLAR offset is consumed.
6. Mixed naive/aware fill timestamps crashed FIFO round-trip matching.
"""

from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from trading_bot_v2.grid_lifecycle_manager import (
    GRID_ALLOWED_REGIMES,
    GridLifecycleManager,
    GridState,
)
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.strategies.grid_trading import GridTradingStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager():
    """GridLifecycleManager with mock client and risk manager."""
    client = Mock()
    risk_manager = MagicMock()
    risk_manager.grid_exposure = {}
    manager = GridLifecycleManager(client, risk_manager)
    return manager, client, risk_manager


def _ranging_ohlcv(n=80, base=1.0, amplitude=0.02):
    """Synthetic oscillating OHLCV series (ranging market)."""
    highs, lows, closes, opens, volumes = [], [], [], [], []
    for i in range(n):
        # Triangle wave oscillation around base
        phase = (i % 10) / 10.0
        offset = amplitude * (phase if phase < 0.5 else 1.0 - phase)
        close = base + offset
        closes.append(close)
        highs.append(close + 0.005)
        lows.append(close - 0.005)
        opens.append(close)
        volumes.append(1000.0)
    return {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }


# ---------------------------------------------------------------------------
# 1. Signals must carry grid geometry
# ---------------------------------------------------------------------------


class TestGridSignalGeometry:
    def test_signals_carry_spacing_and_levels(self):
        """Grid BUY/SELL signals must set signal.spacing and grid_levels."""
        strategy = GridTradingStrategy(
            grid_levels=10,
            grid_spacing_atr_multiplier=0.65,
            max_positions_per_symbol=10,
            emergency_stop_loss_pct=0.05,
            adx_regime_threshold=20.0,
            risk_manager=SimpleNamespace(last_known_balance=15000),
        )
        data = {"1h": _ranging_ohlcv(), "4h": _ranging_ohlcv()}

        signals = strategy.generate_signals(
            "SUI-USDC", data, current_price=1.0, regime_adx=12.0
        )

        assert len(signals) == 2, "expected BUY + SELL grid pair"
        buy = next(s for s in signals if s.side == OrderSide.BUY)
        sell = next(s for s in signals if s.side == OrderSide.SELL)
        assert buy.entry_price < sell.entry_price

        for sig in (buy, sell):
            assert sig.spacing is not None and sig.spacing > 0, (
                "signal.spacing missing - grid would register with spacing=0 "
                "and never replenish fills"
            )
            assert sig.grid_levels == 10
            # spacing is a dollar offset, sane vs price
            assert sig.spacing < 0.2 * 1.0


# ---------------------------------------------------------------------------
# 2. Regime change must unwind active grids
# ---------------------------------------------------------------------------


class TestRegimeChangeUnwind:
    def test_trending_regime_unwinds_active_grid(self):
        manager, client, _ = _make_manager()
        client.get_positions.return_value = []
        manager.register_new_grid("SUI", 1000.0, 0.90)

        unwound = manager.handle_regime_change("SUI", "trending_strong")

        assert unwound is True
        client.cancel_all_orders.assert_called_with("SUI")
        assert not manager.has_active_grid("SUI")

    def test_allowed_regimes_do_not_unwind(self):
        manager, client, _ = _make_manager()
        manager.register_new_grid("SUI", 1000.0, 0.90)

        for regime in sorted(GRID_ALLOWED_REGIMES):
            assert manager.handle_regime_change("SUI", regime) is False

        assert manager.has_active_grid("SUI")
        client.cancel_all_orders.assert_not_called()

    def test_no_grid_is_a_noop(self):
        manager, client, _ = _make_manager()
        assert manager.handle_regime_change("SUI", "trending_strong") is False
        client.cancel_all_orders.assert_not_called()

    def test_enum_like_regime_accepted(self):
        manager, client, _ = _make_manager()
        client.get_positions.return_value = []
        manager.register_new_grid("SUI", 1000.0, 0.90)

        regime = SimpleNamespace(value="trending_moderate")
        assert manager.handle_regime_change("SUI", regime) is True

    def test_trading_bot_handler_routes_event_to_unwind(self):
        """TradingBot._handle_regime_changed_for_grids wires the event."""
        from trading_bot_v2.trading_bot import TradingBot

        manager, client, _ = _make_manager()
        client.get_positions.return_value = []
        manager.register_new_grid("SUI", 1000.0, 0.90)

        fetcher = Mock()
        fetcher.get_candles_multi_tf.side_effect = RuntimeError("no data")
        bot_stub = SimpleNamespace(
            grid_lifecycle=manager, multi_tf_fetcher=fetcher
        )
        handler = MethodType(
            TradingBot._handle_regime_changed_for_grids, bot_stub
        )

        event = SimpleNamespace(
            data={"symbol": "SUI", "new_regime": "trending_strong"}
        )
        handler(event)

        assert not manager.has_active_grid("SUI")
        client.cancel_all_orders.assert_called_with("SUI")


# ---------------------------------------------------------------------------
# 3. Force exit must CLOSE positions, not double them
# ---------------------------------------------------------------------------


class TestForceExitSides:
    def test_long_position_closed_with_sell(self):
        manager, client, _ = _make_manager()
        client.get_positions.return_value = [
            {"symbol": "SUI", "side": "long", "amount": "5"}
        ]
        manager.register_new_grid("SUI", 1000.0, 0.90)

        manager.on_emergency_stop_triggered("SUI")

        client.place_order.assert_called_with("SUI", "sell", 5.0, "market")

    def test_short_position_closed_with_buy(self):
        manager, client, _ = _make_manager()
        client.get_positions.return_value = [
            {"symbol": "SUI", "side": "short", "amount": "3"}
        ]
        manager.register_new_grid("SUI", 1000.0, 0.90)

        manager.on_emergency_stop_triggered("SUI")

        client.place_order.assert_called_with("SUI", "buy", 3.0, "market")

    def test_legacy_bid_side_still_closed_with_sell(self):
        manager, client, _ = _make_manager()
        client.get_positions.return_value = [
            {"symbol": "SUI", "side": "bid", "amount": "2"}
        ]
        manager.register_new_grid("SUI", 1000.0, 0.90)

        manager.on_emergency_stop_triggered("SUI")

        client.place_order.assert_called_with("SUI", "sell", 2.0, "market")


# ---------------------------------------------------------------------------
# 4. Fill replenishment (the ladder)
# ---------------------------------------------------------------------------


class TestReplenishLadder:
    def _grid(self, spacing, center=1.0):
        manager, client, _ = _make_manager()
        manager.register_new_grid(
            "SUI",
            1000.0,
            center * 0.9,
            spacing=spacing,
            center_price=center,
        )
        return manager, client

    def test_buy_fill_places_sell_one_spacing_up(self):
        manager, client = self._grid(spacing=0.01)

        trades = [
            {
                "symbol": "SUI",
                "trade_id": "t1",
                "order_id": "o1",
                "side": "bid",
                "price": 0.99,
                "amount": "5",
                "fee": 0.01,
                "timestamp": 1700000000,
            }
        ]
        new_fills = manager._process_trades("SUI", trades)

        assert new_fills == 1
        client.place_order.assert_called_with("SUI", "sell", 5.0, "limit", 1.0)

    def test_sell_fill_places_buy_one_spacing_down(self):
        manager, client = self._grid(spacing=0.01)

        trades = [
            {
                "symbol": "SUI",
                "trade_id": "t2",
                "order_id": "o2",
                "side": "ask",
                "price": 1.01,
                "amount": "5",
                "fee": 0.01,
                "timestamp": 1700000000,
            }
        ]
        manager._process_trades("SUI", trades)

        client.place_order.assert_called_with("SUI", "buy", 5.0, "limit", 1.0)

    def test_subcent_spacing_survives_rounding(self):
        """Old code rounded to 0.01 ticks, collapsing sub-cent ladders."""
        manager, client = self._grid(spacing=0.003, center=0.9)

        trades = [
            {
                "symbol": "SUI",
                "trade_id": "t3",
                "order_id": "o3",
                "side": "bid",
                "price": 0.9,
                "amount": "5",
                "fee": 0.0,
                "timestamp": 1700000000,
            }
        ]
        manager._process_trades("SUI", trades)

        args = client.place_order.call_args[0]
        assert args[1] == "sell"
        assert args[4] == pytest.approx(0.903), (
            "counter price must keep sub-cent precision "
            "(old 0.01-tick rounding produced 0.90)"
        )

    def test_zero_spacing_blocks_replenish(self):
        manager, client = self._grid(spacing=0)

        trades = [
            {
                "symbol": "SUI",
                "trade_id": "t4",
                "side": "bid",
                "price": 0.99,
                "amount": "5",
                "fee": 0.0,
                "timestamp": 1700000000,
            }
        ]
        manager._process_trades("SUI", trades)

        client.place_order.assert_not_called()

    def test_insane_spacing_blocks_replenish(self):
        """Spacing over 20% of price = corrupted state, skip the order."""
        manager, client = self._grid(spacing=0.5, center=1.0)

        trades = [
            {
                "symbol": "SUI",
                "trade_id": "t5",
                "side": "bid",
                "price": 1.0,
                "amount": "5",
                "fee": 0.0,
                "timestamp": 1700000000,
            }
        ]
        manager._process_trades("SUI", trades)

        client.place_order.assert_not_called()

    def test_mixed_timestamp_formats_do_not_break_round_trips(self):
        """Naive/aware datetime mix must not crash FIFO matching."""
        manager, client = self._grid(spacing=0.01)

        trades = [
            {
                "symbol": "SUI",
                "trade_id": "b1",
                "side": "bid",
                "price": 0.99,
                "amount": "5",
                "fee": 0.0,
                "timestamp": 1700000000,  # epoch -> aware UTC
            },
            {
                "symbol": "SUI",
                "trade_id": "s1",
                "side": "ask",
                "price": 1.01,
                "amount": "5",
                "fee": 0.0,
                "created_at": "2024-01-02T00:00:00",  # naive ISO string
            },
        ]
        manager._process_trades("SUI", trades)

        round_trips = manager._calculate_round_trips("SUI")

        assert round_trips == 1
        metrics = manager._metrics["SUI"]
        assert metrics.realized_pnl == pytest.approx(5 * 0.02)


# ---------------------------------------------------------------------------
# 5. Re-adoption spacing units (dollars, not fractions)
# ---------------------------------------------------------------------------


class TestReadoptionSpacingUnits:
    def test_lifecycle_readopt_stores_dollar_spacing(self):
        manager, client, _ = _make_manager()
        orders = []
        for price in (96000, 97000, 98000):
            orders.append(
                {"symbol": "BTC", "side": "bid", "price": str(price), "quantity": "0.01"}
            )
        for price in (102000, 103000, 104000):
            orders.append(
                {"symbol": "BTC", "side": "ask", "price": str(price), "quantity": "0.01"}
            )
        client.get_orders.return_value = orders

        buy_orders = [o for o in orders if o["side"] == "bid"]
        sell_orders = [o for o in orders if o["side"] == "ask"]
        ok = manager._readopt_orphaned_grid_from_exchange(
            "BTC", buy_orders, sell_orders
        )

        assert ok is True
        spacing = manager._grids["BTC"]["grid_spacing"]
        assert spacing > 100, (
            f"spacing {spacing} looks RELATIVE (fraction) - must be dollars"
        )

    def test_trading_bot_readopt_stores_dollar_spacing(self):
        from trading_bot_v2.trading_bot import TradingBot

        manager, _, _ = _make_manager()
        bot_stub = SimpleNamespace(grid_lifecycle=manager)
        readopt = MethodType(TradingBot._readopt_orphaned_grid, bot_stub)

        buy_orders = [
            {"symbol": "BTC", "side": "bid", "price": str(p), "initial_amount": "0.01"}
            for p in (96000, 97000, 98000)
        ]
        sell_orders = [
            {"symbol": "BTC", "side": "ask", "price": str(p), "initial_amount": "0.01"}
            for p in (102000, 103000, 104000)
        ]

        readopt("BTC", buy_orders, sell_orders)

        grid = manager._grids["BTC"]
        # gap between best bid (98000) and best ask (102000) is 4000,
        # i.e. two grid steps -> spacing 2000 dollars
        assert grid["grid_spacing"] == pytest.approx(2000)
        assert grid["emergency_stop"] == pytest.approx(96000 * 0.95)


# ---------------------------------------------------------------------------
# 6. Grid registration geometry (execution path)
# ---------------------------------------------------------------------------


class TestGridRegistrationGeometry:
    def _make_bot_stub(self, manager):
        from trading_bot_v2.trading_bot import TradingBot

        client = Mock()
        order_counter = {"n": 0}

        def _place_order(symbol, side, quantity, order_type, price=None):
            order_counter["n"] += 1
            return {"success": True, "data": {"order_id": order_counter["n"]}}

        client.place_order.side_effect = _place_order

        stub = SimpleNamespace(
            client=client,
            grid_lifecycle=manager,
            signal_logger=Mock(),
            market_regime=None,
        )
        stub._get_ticker_ws = lambda symbol: {"last": 1.0}
        stub._place_grid_orders = MethodType(
            TradingBot._place_grid_orders, stub
        )
        stub._calculate_grid_levels = MethodType(
            TradingBot._calculate_grid_levels, stub
        )
        stub._execute_grid_signal_coordinated = MethodType(
            TradingBot._execute_grid_signal_coordinated, stub
        )
        return stub, client

    def _grid_signal(self):
        from trading_bot_v2.config import (
            AssetClass,
            MarketState,
            StrategyType,
            TradeQuality,
        )

        return Signal(
            strategy=StrategyType.GRID_TRADING,
            asset="SUI",
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=0.994,
            stop_loss=0.95,
            take_profit=1.0,
            confidence=0.6,
            quality=TradeQuality.STANDARD,
            market_state=MarketState.RANGE,
            volume_confirmation=True,
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=True,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
            indicators={"atr": 0.01},
            grid_levels=10,
            spacing=0.006,
        )

    def test_registered_grid_has_real_spacing_and_safe_stop(self):
        manager, _, _ = _make_manager()
        stub, client = self._make_bot_stub(manager)
        signal = self._grid_signal()

        stub._execute_grid_signal_coordinated(
            signal, {"approved": True, "allocated_amount": 1000.0}
        )

        assert "SUI" in manager._grids, "grid was not registered"
        grid = manager._grids["SUI"]

        # Spacing must be the dollar spacing actually used, never 0
        assert grid["grid_spacing"] == pytest.approx(0.006)

        # Emergency stop must sit BELOW the lowest buy level; the old code
        # used signal.stop_loss which could land INSIDE the grid.
        lowest_buy = 1.0 - 5 * 0.006
        assert grid["emergency_stop"] < lowest_buy
        assert grid["emergency_stop"] == pytest.approx(lowest_buy * 0.95)

        # Center must be the actual market price used for the levels
        assert grid["center_price"] == pytest.approx(1.0)

        # 5 buy + 5 sell limit orders were placed
        assert client.place_order.call_count == 10
