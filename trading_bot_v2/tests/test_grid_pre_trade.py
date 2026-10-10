"""
Grid pre-trade check on the live grid path.

The live route is _execute_grid_signal_coordinated -> _place_grid_orders.
These tests drive that route with the real TradingBot methods bound onto a
duck-typed stand-in and the real exchange adapter wrapped around a mock
REST client, so only the network edge is faked.

Covered:
- a valid grid places exactly the orders it placed before the check existed
- each sub-check: a violating grid sends zero orders and registers nothing
- the level-count range is a warning, not a rejection
- the repo's default grid parameters pass for the default symbols
- the check has no bare ``assert`` (it must survive ``python -O``)
"""

import ast
import inspect
import textwrap
from types import MethodType, SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch

import pytest

from trading_bot_v2.config import AssetClass, MarketState, StrategyType, TradeQuality
from trading_bot_v2.exchanges import get_exchange_client
from trading_bot_v2.exchanges.blofin import BlofinExchange
from trading_bot_v2.grid_lifecycle_manager import GridLifecycleManager
from trading_bot_v2.models import OrderSide, OrderType, Signal
from trading_bot_v2.trading_bot import (
    GridPreTradeError,
    TradingBot,
    _grid_emergency_stop_price,
    _grid_min_order_constraint,
)

# Pacifica /info values (USD order value), identical for every default symbol.
PACIFICA_MIN_ORDER_USD = 10.0


@pytest.fixture(autouse=True)
def _default_grid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test against the repo defaults unless it overrides them."""
    monkeypatch.delenv("GRID_EMERGENCY_STOP_PCT", raising=False)
    monkeypatch.delenv("EXCHANGE", raising=False)


def _make_manager() -> GridLifecycleManager:
    risk_manager = MagicMock()
    risk_manager.grid_exposure = {}
    return GridLifecycleManager(Mock(), risk_manager)


def _make_client(min_order_size: Any = PACIFICA_MIN_ORDER_USD) -> Mock:
    """Mock REST client that acks every order and reports a minimum size."""
    client = Mock()
    counter = {"n": 0}

    def _place_order(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        counter["n"] += 1
        return {"success": True, "data": {"order_id": counter["n"]}}

    client.place_order.side_effect = _place_order
    client.get_instrument_info.return_value = {
        "tick_size": 0.0001,
        "lot_size": 0.1,
        "min_order_size": min_order_size,
    }
    return client


def _make_bot(
    manager: GridLifecycleManager,
    client: Mock,
    price: float = 1.0,
    exchange: Any = None,
) -> SimpleNamespace:
    """Duck-typed bot running the real live grid path."""
    stub = SimpleNamespace(
        client=client,
        grid_lifecycle=manager,
        signal_logger=Mock(),
        market_regime=None,
        exchange=exchange or get_exchange_client(name="pacifica", rest_client=client),
    )
    stub._get_ticker_ws = lambda symbol: {"last": price}
    for name in (
        "_place_grid_orders",
        "_calculate_grid_levels",
        "_execute_grid_signal_coordinated",
    ):
        setattr(stub, name, MethodType(getattr(TradingBot, name), stub))
    return stub


def _grid_signal(
    asset: str = "SUI",
    entry_price: float = 0.994,
    grid_levels: Optional[int] = 10,
    spacing: Optional[float] = 0.006,
) -> Signal:
    return Signal(
        strategy=StrategyType.GRID_TRADING,
        asset=asset,
        asset_class=AssetClass.PERPETUAL,
        side=OrderSide.BUY,
        entry_price=entry_price,
        stop_loss=entry_price * 0.95,
        take_profit=entry_price * 1.01,
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
        grid_levels=grid_levels,
        spacing=spacing,
    )


def _run(stub: SimpleNamespace, signal: Signal, capital: float = 1000.0) -> None:
    stub._execute_grid_signal_coordinated(
        signal, {"approved": True, "allocated_amount": capital}
    )


def _order_calls(exchange: Any) -> List[Tuple[Any, ...]]:
    """(symbol, side, quantity, order_type, price) of every order sent."""
    return [
        (
            call.kwargs["symbol"],
            call.kwargs["side"],
            call.kwargs["quantity"],
            call.kwargs["order_type"],
            call.kwargs["price"],
        )
        for call in exchange.place_order.call_args_list
    ]


def _spy_exchange(client: Mock) -> Any:
    """Real Pacifica adapter whose place_order calls are recorded."""
    real = get_exchange_client(name="pacifica", rest_client=client)
    return SimpleNamespace(
        place_order=Mock(wraps=real.place_order),
        get_instrument_info=real.get_instrument_info,
        capabilities=real.capabilities,
    )


def _assert_nothing_sent(
    stub: SimpleNamespace, manager: GridLifecycleManager, client: Mock, symbol: str
) -> str:
    """Zero orders, no grid state, one failure record. Returns the reason."""
    client.place_order.assert_not_called()
    assert manager._grids == {}
    assert manager.has_active_grid(symbol) is False
    assert symbol not in manager.risk_manager.grid_exposure
    stub.signal_logger.log_signal_executed.assert_not_called()
    stub.signal_logger.log_signal_failed.assert_called_once()
    return str(stub.signal_logger.log_signal_failed.call_args.kwargs["error"])


# ---------------------------------------------------------------------------
# Valid grid: unchanged orders
# ---------------------------------------------------------------------------


class TestValidGridUnchanged:
    def _place(self, bypass_check: bool) -> Tuple[List[Tuple[Any, ...]], Any]:
        manager = _make_manager()
        client = _make_client()
        exchange = _spy_exchange(client)
        stub = _make_bot(manager, client, exchange=exchange)
        if bypass_check:
            with patch.object(TradingBot, "_assert_grid_pre_trade"):
                _run(stub, _grid_signal())
        else:
            _run(stub, _grid_signal())
        return _order_calls(exchange), manager

    def test_same_orders_as_without_the_check(self) -> None:
        """Same scenario as TestGridRegistrationGeometry in test_grid_pipeline."""
        with_check, manager = self._place(bypass_check=False)
        without_check, _ = self._place(bypass_check=True)

        assert with_check == without_check
        expected = [
            ("SUI", OrderSide.BUY, 100.0, OrderType.LIMIT, round(1.0 - i * 0.006, 3))
            for i in range(1, 6)
        ] + [
            ("SUI", OrderSide.SELL, 100.0, OrderType.LIMIT, round(1.0 + i * 0.006, 3))
            for i in range(1, 6)
        ]
        assert with_check == expected

        grid = manager._grids["SUI"]
        assert grid["grid_spacing"] == pytest.approx(0.006)
        assert grid["center_price"] == pytest.approx(1.0)
        assert grid["num_levels"] == 10
        assert grid["emergency_stop"] == pytest.approx((1.0 - 5 * 0.006) * 0.95)
        assert grid["order_ids"] == {str(i) for i in range(1, 11)}

    def test_validated_stop_is_the_registered_stop(self) -> None:
        """The check and the registration use one stop value."""
        manager = _make_manager()
        client = _make_client()
        stub = _make_bot(manager, client)
        with patch.object(
            TradingBot,
            "_assert_grid_pre_trade",
            wraps=TradingBot._assert_grid_pre_trade,
        ) as check:
            _run(stub, _grid_signal())

        check.assert_called_once()
        validated = check.call_args.kwargs["emergency_stop_price"]
        assert manager._grids["SUI"]["emergency_stop"] == pytest.approx(validated)
        assert check.call_args.kwargs["min_order_is_notional"] is True
        assert check.call_args.kwargs["min_order_size"] == PACIFICA_MIN_ORDER_USD

    def test_unknown_minimum_does_not_block(self) -> None:
        """Clients report 0.0 when the minimum is unavailable: warn, trade."""
        manager = _make_manager()
        client = _make_client(min_order_size=0.0)
        stub = _make_bot(manager, client)
        _run(stub, _grid_signal())
        assert client.place_order.call_count == 10
        assert manager.has_active_grid("SUI")


# ---------------------------------------------------------------------------
# One test per sub-check: violation -> zero orders, no grid state
# ---------------------------------------------------------------------------


class TestViolationsSendNothing:
    def test_non_positive_spacing_rejected(self) -> None:
        manager = _make_manager()
        client = _make_client()
        stub = _make_bot(manager, client)
        stub._calculate_grid_levels = lambda signal, total_capital: {
            "buy_levels": [{"price": 0.99, "quantity": 100.0}] * 5,
            "sell_levels": [{"price": 1.01, "quantity": 100.0}] * 5,
            "current_price": 1.0,
            "grid_spacing": 0.0,
            "quantity_per_level": 100.0,
        }
        _run(stub, _grid_signal())
        reason = _assert_nothing_sent(stub, manager, client, "SUI")
        assert "Grid pre-trade check failed" in reason
        assert "spacing must be positive" in reason

    def test_order_value_below_notional_minimum_rejected(self) -> None:
        """Pacifica: $40 over 10 levels at $1 is $3.88 per order, under $10."""
        manager = _make_manager()
        client = _make_client(min_order_size=PACIFICA_MIN_ORDER_USD)
        stub = _make_bot(manager, client)
        _run(stub, _grid_signal(), capital=40.0)
        reason = _assert_nothing_sent(stub, manager, client, "SUI")
        assert "below exchange minimum $10.0" in reason

    def test_quantity_below_base_unit_minimum_rejected(self) -> None:
        """Base-unit venue (Blofin semantics): quantity compared directly."""
        manager = _make_manager()
        client = _make_client(min_order_size=500.0)
        exchange = SimpleNamespace(
            place_order=client.place_order,
            get_instrument_info=client.get_instrument_info,
            capabilities=BlofinExchange.capabilities,
        )
        stub = _make_bot(manager, client, exchange=exchange)
        _run(stub, _grid_signal())
        reason = _assert_nothing_sent(stub, manager, client, "SUI")
        assert "quantity per level 100.0 below exchange minimum 500.0" in reason

    def test_base_unit_minimum_is_not_read_as_dollars(self) -> None:
        """BTC on a base-unit venue: 0.0009 BTC clears a 0.0001 BTC minimum."""
        manager = _make_manager()
        client = _make_client(min_order_size=0.0001)
        exchange = SimpleNamespace(
            place_order=client.place_order,
            get_instrument_info=client.get_instrument_info,
            capabilities=BlofinExchange.capabilities,
        )
        stub = _make_bot(manager, client, price=110000.0, exchange=exchange)
        _run(stub, _grid_signal("BTC", 109500.0, 8, 250.0), capital=800.0)
        assert client.place_order.call_count == 8
        assert manager.has_active_grid("BTC")

    @pytest.mark.parametrize(
        ("stop_pct", "fragment"),
        [("1.0", "must be positive"), ("-0.5", "must be below current price")],
    )
    def test_invalid_emergency_stop_rejected(
        self, monkeypatch: pytest.MonkeyPatch, stop_pct: str, fragment: str
    ) -> None:
        monkeypatch.setenv("GRID_EMERGENCY_STOP_PCT", stop_pct)
        manager = _make_manager()
        client = _make_client()
        stub = _make_bot(manager, client)
        _run(stub, _grid_signal())
        reason = _assert_nothing_sent(stub, manager, client, "SUI")
        assert fragment in reason

    def test_failure_logs_one_line_and_does_not_raise(self) -> None:
        manager = _make_manager()
        client = _make_client()
        stub = _make_bot(manager, client)
        with patch("trading_bot_v2.trading_bot.logger") as log:
            _run(stub, _grid_signal(), capital=40.0)

        _assert_nothing_sent(stub, manager, client, "SUI")
        assert log.error.call_count == 1
        line = log.error.call_args.args[0]
        assert "SUI" in line and "Grid pre-trade check failed" in line
        assert "below exchange minimum" in line

    def test_rejected_grid_can_be_retried(self) -> None:
        """No residue: the same symbol trades once the grid is valid."""
        manager = _make_manager()
        client = _make_client()
        stub = _make_bot(manager, client)
        _run(stub, _grid_signal(), capital=40.0)
        client.place_order.assert_not_called()
        _run(stub, _grid_signal(), capital=1000.0)
        assert client.place_order.call_count == 10
        assert manager.has_active_grid("SUI")


# ---------------------------------------------------------------------------
# Level count: warning only
# ---------------------------------------------------------------------------


class TestLevelCountIsWarningOnly:
    @pytest.mark.parametrize(("configured", "placed"), [(3, 2), (4, 4), (5, 4)])
    def test_small_ladder_still_trades_with_warning(
        self, configured: int, placed: int
    ) -> None:
        manager = _make_manager()
        client = _make_client()
        stub = _make_bot(manager, client)
        with patch("trading_bot_v2.trading_bot.logger") as log:
            _run(stub, _grid_signal(grid_levels=configured))

        assert client.place_order.call_count == placed
        assert manager.has_active_grid("SUI")
        warnings = [call.args[0] for call in log.warning.call_args_list]
        assert any(f"{placed} levels" in w and "5-20" in w for w in warnings)

    @pytest.mark.parametrize("levels", [5, 8, 20])
    def test_in_range_ladder_does_not_warn(self, levels: int) -> None:
        with patch("trading_bot_v2.trading_bot.logger") as log:
            TradingBot._assert_grid_pre_trade(
                symbol="SUI",
                grid_spacing=0.006,
                num_levels=levels,
                quantity_per_level=100.0,
                current_price=1.0,
                emergency_stop_price=0.92,
                min_order_size=10.0,
                min_order_is_notional=True,
                lowest_level_price=0.97,
            )
        log.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Default configuration passes
# ---------------------------------------------------------------------------

# (symbol, realistic price, realistic 1h ATR).  Minimum order value is the
# $10 Pacifica reports for each of these symbols.
DEFAULT_SYMBOL_MARKETS = [
    ("BTC", 110000.0, 600.0),
    ("ETH", 4000.0, 35.0),
    ("SOL", 200.0, 2.5),
    ("SUI", 3.0, 0.04),
    ("AVAX", 25.0, 0.3),
    ("XRP", 2.5, 0.03),
    ("DOGE", 0.2, 0.003),
]


class TestDefaultConfigurationPasses:
    @pytest.mark.parametrize(("symbol", "price", "atr"), DEFAULT_SYMBOL_MARKETS)
    @pytest.mark.parametrize(
        ("levels", "atr_multiplier"),
        [(8, 0.4), (10, 0.65)],
        ids=["strategy_manager_defaults", "grid_strategy_defaults"],
    )
    def test_default_grid_trades(
        self,
        symbol: str,
        price: float,
        atr: float,
        levels: int,
        atr_multiplier: float,
    ) -> None:
        """Default levels/spacing, 4% grid cap of a $10,000 account."""
        manager = _make_manager()
        client = _make_client(min_order_size=PACIFICA_MIN_ORDER_USD)
        stub = _make_bot(manager, client, price=price)
        signal = _grid_signal(symbol, price - atr * atr_multiplier, levels)
        signal.spacing = atr * atr_multiplier

        _run(stub, signal, capital=10000.0 * 0.04)

        assert client.place_order.call_count == levels
        assert manager.has_active_grid(symbol)
        stub.signal_logger.log_signal_failed.assert_not_called()


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------


def _check(**overrides: Any) -> None:
    params: Dict[str, Any] = {
        "symbol": "SUI",
        "grid_spacing": 0.006,
        "num_levels": 8,
        "quantity_per_level": 100.0,
        "current_price": 1.0,
        "emergency_stop_price": 0.92,
        "min_order_size": 10.0,
        "min_order_is_notional": True,
        "lowest_level_price": 0.97,
    }
    params.update(overrides)
    TradingBot._assert_grid_pre_trade(**params)


class TestCheckDirectly:
    def test_valid_grid_passes(self) -> None:
        _check()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"grid_spacing": 0.0},
            {"grid_spacing": -0.01},
            {"grid_spacing": float("nan")},
            {"quantity_per_level": 5.0},
            {"min_order_is_notional": False, "min_order_size": 100.5},
            {"emergency_stop_price": 0.0},
            {"emergency_stop_price": 1.0},
            {"emergency_stop_price": 1.2},
            {"emergency_stop_price": float("nan")},
        ],
    )
    def test_violation_raises(self, overrides: Dict[str, Any]) -> None:
        with pytest.raises(GridPreTradeError):
            _check(**overrides)

    def test_no_bare_assert_so_it_survives_python_O(self) -> None:
        source = textwrap.dedent(inspect.getsource(TradingBot._assert_grid_pre_trade))
        nodes = ast.walk(ast.parse(source))
        assert not any(isinstance(node, ast.Assert) for node in nodes)

    def test_failure_is_not_an_assertion_error(self) -> None:
        assert not issubclass(GridPreTradeError, AssertionError)


class TestHelpers:
    def test_stop_sits_below_lowest_buy(self) -> None:
        assert _grid_emergency_stop_price(0.97, 1.0) == pytest.approx(0.97 * 0.95)

    def test_stop_falls_back_to_center(self) -> None:
        assert _grid_emergency_stop_price(0, 1.0) == pytest.approx(0.95)

    def test_pacifica_minimum_is_notional(self) -> None:
        exchange = get_exchange_client(name="pacifica", rest_client=_make_client())
        assert _grid_min_order_constraint(exchange, "SUI") == (10.0, True)

    def test_blofin_minimum_is_base_units(self) -> None:
        exchange = SimpleNamespace(
            get_instrument_info=lambda symbol: {"min_order_size": 0.0001},
            capabilities=BlofinExchange.capabilities,
        )
        assert _grid_min_order_constraint(exchange, "BTC") == (0.0001, False)

    @pytest.mark.parametrize(
        "info",
        [
            Mock(),
            MagicMock(),
            None,
            {"min_order_size": "n/a"},
            {"min_order_size": True},
            {},
        ],
    )
    def test_unusable_instrument_info_means_unknown(self, info: Any) -> None:
        exchange = SimpleNamespace(get_instrument_info=lambda symbol: info)
        assert _grid_min_order_constraint(exchange, "SUI") == (0.0, False)
