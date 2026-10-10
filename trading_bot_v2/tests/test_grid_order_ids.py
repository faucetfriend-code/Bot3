"""
Grid order-ID fill-attribution tests.

GridLifecycleManager._process_trades used to attribute EVERY account trade
on a symbol to the grid. Overlay strategies (LiquidationCapture,
OrderBookImbalance) trade the same symbols, so their fills triggered
spurious grid ladder replenishment and corrupted FIFO round-trip PnL.

These tests cover the order-ID tracking that fixes this:

1. Non-grid fills on the same symbol are ignored (no replenishment).
2. Grid fills matched by ID still ladder, and the ID set rotates
   (filled ID removed, replacement ID added).
3. Legacy grids without tracked IDs fall back to attribute-everything
   with a one-time WARNING and keep functioning.
4. IDs persist through save/load of grid state and the re-adoption path.
5. Cancel/unwind paths clear the tracked ID set.
"""

import os
import tempfile
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from loguru import logger as loguru_logger

from trading_bot_v2.grid_lifecycle_manager import (
    GridLifecycleManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(db=None):
    """GridLifecycleManager with mock client and risk manager."""
    client = Mock()
    risk_manager = MagicMock()
    risk_manager.grid_exposure = {}
    manager = GridLifecycleManager(client, risk_manager, db=db)
    return manager, client, risk_manager


def _trade(trade_id, order_id, side="bid", price=0.99, amount="5"):
    """Build a Pacifica-shaped trade dict."""
    return {
        "symbol": "SUI",
        "trade_id": trade_id,
        "order_id": order_id,
        "side": side,
        "price": price,
        "amount": amount,
        "fee": 0.01,
        "timestamp": 1700000000,
    }


def _register_tracked_grid(manager, order_ids=("g1", "g2", "g3")):
    manager.register_new_grid(
        "SUI",
        1000.0,
        0.90,
        spacing=0.01,
        center_price=1.0,
        order_ids=list(order_ids),
    )


@pytest.fixture
def temp_db():
    """Point trading_bot_v2.database at a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    original_env = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = tmp_path
    os.environ["DATABASE_BACKEND"] = "sqlite"

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = tmp_path
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool
    if original_env is not None:
        os.environ["DATABASE_PATH"] = original_env
    elif "DATABASE_PATH" in os.environ:
        del os.environ["DATABASE_PATH"]
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 1. Non-grid fills are ignored
# ---------------------------------------------------------------------------


class TestNonGridFillIgnored:
    def test_overlay_fill_does_not_replenish(self):
        """A fill from another strategy's order must not touch the grid."""
        manager, client, _ = _make_manager()
        _register_tracked_grid(manager)

        new_fills = manager._process_trades("SUI", [_trade("t1", "overlay-order-77")])

        assert new_fills == 0
        client.place_order.assert_not_called()
        assert manager._fills["SUI"] == []
        metrics = manager._metrics["SUI"]
        assert metrics.total_buy_fills == 0
        assert metrics.net_position == 0.0
        # ID set untouched
        assert manager._grids["SUI"]["order_ids"] == {"g1", "g2", "g3"}

    def test_overlay_fill_does_not_corrupt_round_trips(self):
        """Overlay buy+sell pair must not create a fake grid round-trip."""
        manager, client, _ = _make_manager()
        _register_tracked_grid(manager)

        manager._process_trades(
            "SUI",
            [
                _trade("t1", "ov-1", side="bid", price=0.99),
                _trade("t2", "ov-2", side="ask", price=1.01),
            ],
        )

        assert manager._calculate_round_trips("SUI") == 0
        assert manager._metrics["SUI"].realized_pnl == 0.0

    def test_ignored_fill_not_reprocessed(self):
        """Ignored trades are marked processed and skipped next cycle."""
        manager, client, _ = _make_manager()
        _register_tracked_grid(manager)

        manager._process_trades("SUI", [_trade("t1", "ov-1")])
        assert "t1" in manager._processed_trades["SUI"]


# ---------------------------------------------------------------------------
# 2. Grid fills matched by ID still ladder; ID set rotates
# ---------------------------------------------------------------------------


class TestGridFillRotation:
    def test_matched_fill_replenishes_and_rotates_ids(self):
        manager, client, _ = _make_manager()
        _register_tracked_grid(manager)
        client.place_order.return_value = {
            "success": True,
            "data": {"order_id": 555},
        }

        new_fills = manager._process_trades(
            "SUI", [_trade("t1", "g1", side="bid", price=0.99)]
        )

        assert new_fills == 1
        # Ladder: BUY fill at 0.99 -> SELL replenish at 0.99 + 0.01
        client.place_order.assert_called_with("SUI", "sell", 5.0, "limit", 1.0)
        # Rotation: filled ID out, replacement ID in
        ids = manager._grids["SUI"]["order_ids"]
        assert "g1" not in ids
        assert "555" in ids
        assert ids == {"g2", "g3", "555"}

    def test_replacement_order_fill_is_attributed(self):
        """The replenished order's own fill matches on the next cycle."""
        manager, client, _ = _make_manager()
        _register_tracked_grid(manager)
        client.place_order.return_value = {
            "success": True,
            "data": {"order_id": 555},
        }

        manager._process_trades("SUI", [_trade("t1", "g1", side="bid")])
        new_fills = manager._process_trades(
            "SUI", [_trade("t2", "555", side="ask", price=1.0)]
        )

        assert new_fills == 1
        assert manager._calculate_round_trips("SUI") == 1

    def test_unextractable_replenish_id_does_not_crash(self):
        """Bad place_order response: fill is recorded, ID just not added."""
        manager, client, _ = _make_manager()
        _register_tracked_grid(manager)
        client.place_order.return_value = "success"  # string, no ID

        new_fills = manager._process_trades("SUI", [_trade("t1", "g1")])

        assert new_fills == 1
        assert manager._grids["SUI"]["order_ids"] == {"g2", "g3"}


# ---------------------------------------------------------------------------
# 3. Legacy grids (no tracked IDs) fall back with a warning
# ---------------------------------------------------------------------------


class TestLegacyFallback:
    def test_legacy_grid_attributes_everything_and_warns_once(self):
        manager, client, _ = _make_manager()
        manager.register_new_grid("SUI", 1000.0, 0.90, spacing=0.01, center_price=1.0)
        assert "order_ids" not in manager._grids["SUI"]

        messages = []
        sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level="WARNING")
        try:
            new_fills = manager._process_trades("SUI", [_trade("t1", "any-order")])
            manager._process_trades("SUI", [_trade("t2", "other-order")])
        finally:
            loguru_logger.remove(sink_id)

        # Old behavior preserved: fill attributed, ladder replenished
        assert new_fills == 1
        client.place_order.assert_called_with("SUI", "sell", 5.0, "limit", 1.0)

        # Warning emitted exactly once per grid
        warnings = [m for m in messages if "no tracked order IDs" in m]
        assert len(warnings) == 1
        assert manager._grids["SUI"]["order_id_warning_emitted"] is True


# ---------------------------------------------------------------------------
# 4. Persistence: save/load and re-adoption
# ---------------------------------------------------------------------------


class TestOrderIdPersistence:
    def test_ids_survive_save_and_load(self, temp_db):
        manager, _, _ = _make_manager(db=object())
        _register_tracked_grid(manager, order_ids=("11", "22", "33"))

        fresh, _, _ = _make_manager(db=object())
        loaded = fresh.load_grid_states()

        assert "SUI" in loaded
        assert fresh._grids["SUI"]["order_ids"] == {"11", "22", "33"}

    def test_rotated_ids_survive_metrics_update(self, temp_db):
        manager, client, _ = _make_manager(db=object())
        _register_tracked_grid(manager, order_ids=("g1", "g2"))
        client.place_order.return_value = {
            "success": True,
            "data": {"order_id": 999},
        }

        manager._process_trades("SUI", [_trade("t1", "g1")])
        manager.update_grid_metrics_in_db("SUI")

        fresh, _, _ = _make_manager(db=object())
        fresh.load_grid_states()
        assert fresh._grids["SUI"]["order_ids"] == {"g2", "999"}

    def test_legacy_row_loads_without_ids(self, temp_db):
        manager, _, _ = _make_manager(db=object())
        manager.register_new_grid("SUI", 1000.0, 0.90, spacing=0.01, center_price=1.0)

        fresh, _, _ = _make_manager(db=object())
        fresh.load_grid_states()
        assert "order_ids" not in fresh._grids["SUI"]

    def test_lifecycle_readoption_populates_and_persists_ids(self, temp_db):
        manager, client, _ = _make_manager(db=object())
        orders = []
        for i, price in enumerate((96000, 97000, 98000)):
            orders.append(
                {
                    "symbol": "BTC",
                    "side": "bid",
                    "price": str(price),
                    "quantity": "0.01",
                    "order_id": f"b{i}",
                }
            )
        for i, price in enumerate((102000, 103000, 104000)):
            orders.append(
                {
                    "symbol": "BTC",
                    "side": "ask",
                    "price": str(price),
                    "quantity": "0.01",
                    "order_id": f"s{i}",
                }
            )
        client.get_orders.return_value = orders

        buy_orders = [o for o in orders if o["side"] == "bid"]
        sell_orders = [o for o in orders if o["side"] == "ask"]
        ok = manager._readopt_orphaned_grid_from_exchange(
            "BTC", buy_orders, sell_orders
        )

        assert ok is True
        expected = {"b0", "b1", "b2", "s0", "s1", "s2"}
        assert manager._grids["BTC"]["order_ids"] == expected

        # Re-adoption persisted the IDs too
        fresh, _, _ = _make_manager(db=object())
        fresh.load_grid_states()
        assert fresh._grids["BTC"]["order_ids"] == expected

    def test_trading_bot_readoption_populates_ids(self):
        from trading_bot_v2.trading_bot import TradingBot

        manager, _, _ = _make_manager()
        bot_stub = SimpleNamespace(grid_lifecycle=manager)
        readopt = MethodType(TradingBot._readopt_orphaned_grid, bot_stub)

        buy_orders = [
            {
                "symbol": "BTC",
                "side": "bid",
                "price": str(p),
                "initial_amount": "0.01",
                "order_id": f"b{p}",
            }
            for p in (96000, 97000, 98000)
        ]
        sell_orders = [
            {
                "symbol": "BTC",
                "side": "ask",
                "price": str(p),
                "initial_amount": "0.01",
                "order_id": f"s{p}",
            }
            for p in (102000, 103000, 104000)
        ]

        readopt("BTC", buy_orders, sell_orders)

        assert manager._grids["BTC"]["order_ids"] == {
            "b96000",
            "b97000",
            "b98000",
            "s102000",
            "s103000",
            "s104000",
        }


# ---------------------------------------------------------------------------
# 5. Cancel / unwind clears IDs
# ---------------------------------------------------------------------------


class TestUnwindClearsIds:
    def test_force_exit_removes_grid_and_ids(self):
        manager, client, _ = _make_manager()
        client.get_positions.return_value = []
        _register_tracked_grid(manager)

        manager._force_exit("SUI", reason="TEST")

        assert "SUI" not in manager._grids
        assert manager.get_tracked_order_ids("SUI") is None

    def test_partial_exit_with_kept_positions_clears_ids(self):
        manager, client, _ = _make_manager()
        client.get_positions.return_value = [
            {
                "symbol": "SUI",
                "side": "long",
                "amount": "5",
                "entry_price": "0.98",
            }
        ]
        _register_tracked_grid(manager)

        result = manager._partial_exit(
            "SUI", reason="REGIME_CHANGE", trend_direction="up"
        )

        assert result["success"] is True
        assert len(result["kept_positions"]) == 1
        # Grid record survives (migrated positions) but IDs are cleared:
        # all grid orders were cancelled on the exchange.
        assert manager._grids["SUI"]["order_ids"] == set()

    def test_clear_grid_removes_ids(self):
        manager, client, _ = _make_manager()
        _register_tracked_grid(manager)

        manager.clear_grid("SUI")

        assert manager.get_tracked_order_ids("SUI") is None


# ---------------------------------------------------------------------------
# 6. Execution pipeline passes placement IDs into registration
# ---------------------------------------------------------------------------


class TestPlacementPipelineIds:
    def test_registered_grid_tracks_placed_order_ids(self):
        from trading_bot_v2.config import (
            AssetClass,
            MarketState,
            StrategyType,
            TradeQuality,
        )
        from trading_bot_v2.models import OrderSide, Signal
        from trading_bot_v2.trading_bot import TradingBot

        manager, _, _ = _make_manager()

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
        stub._place_grid_orders = MethodType(TradingBot._place_grid_orders, stub)
        stub._calculate_grid_levels = MethodType(
            TradingBot._calculate_grid_levels, stub
        )
        stub._execute_grid_signal_coordinated = MethodType(
            TradingBot._execute_grid_signal_coordinated, stub
        )

        signal = Signal(
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

        stub._execute_grid_signal_coordinated(
            signal, {"approved": True, "allocated_amount": 1000.0}
        )

        grid = manager._grids["SUI"]
        assert grid["order_ids"] == {str(i) for i in range(1, 11)}
