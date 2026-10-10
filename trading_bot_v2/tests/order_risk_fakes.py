"""Shared fakes for the order / risk / position-sizing suite.

Everything here is in-memory and deterministic.  Nothing touches the
network, a real exchange or the live database.  The fakes are plain
classes rather than MagicMocks so a test that reads ``exchange.orders``
sees exactly what the production code sent, with no auto-created
attributes hiding a typo.

The tests directory has no ``__init__.py``, so pytest's default import
mode puts it on ``sys.path`` and test modules import this one as
``from order_risk_fakes import ...``.  The file does not match
``test_*.py`` and is never collected itself.
"""

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from trading_bot_v2.config import AssetClass, StrategyType, TradeQuality
from trading_bot_v2.exchanges.base import (
    ExchangeBalance,
    ExchangeCapabilities,
    ExchangePosition,
    PositionSide,
)
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.order_result import OrderResult
from trading_bot_v2.trading_bot import TradingBot

ALL_FLAGS = {
    "volume_confirmation": True,
    "multi_timeframe_alignment": True,
    "support_resistance_valid": True,
    "rrr_meets_minimum": True,
    "liquidation_buffer_safe": True,
    "account_risk_ok": True,
    "margin_drawdown_ok": True,
    "forbidden_conditions_clear": True,
}


def make_signal(
    strategy: StrategyType = StrategyType.MEAN_REVERSION,
    asset: str = "BTC",
    side: OrderSide = OrderSide.BUY,
    entry_price: float = 100.0,
    stop_loss: Optional[float] = 95.0,
    take_profit: Optional[float] = 110.0,
    confidence: float = 0.7,
    all_flags: bool = True,
    **overrides: Any,
) -> Signal:
    """Build a fully validated Signal with sensible defaults.

    Args:
        strategy: Strategy that emitted the signal.
        asset: Trading symbol.
        side: Order side.
        entry_price: Planned entry price.
        stop_loss: Protective stop (None for a stopless signal).
        take_profit: Target price.
        confidence: Signal confidence in [0, 1].
        all_flags: Set every validity flag True (False leaves them False).
        **overrides: Any other Signal field.

    Returns:
        A Signal instance.
    """
    fields: Dict[str, Any] = dict(ALL_FLAGS) if all_flags else {}
    fields.update(overrides)
    return Signal(
        strategy=strategy,
        asset=asset,
        asset_class=AssetClass.CRYPTO,
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=confidence,
        quality=TradeQuality.STANDARD,
        **fields,
    )


def raiser(exc: Exception) -> Callable[..., Any]:
    """Return a callable that raises ``exc`` whatever it is called with."""

    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return _raise


class RecordingSignalLogger:
    """SignalLogger stand-in that records every call by outcome."""

    def __init__(self) -> None:
        self.pending: List[Dict[str, Any]] = []
        self.executed: List[Dict[str, Any]] = []
        self.failed: List[Dict[str, Any]] = []
        self.rejected: List[Dict[str, Any]] = []

    def log_signal_pending(self, **kw: Any) -> Dict[str, Any]:
        self.pending.append(kw)
        return kw

    def log_signal_executed(self, **kw: Any) -> Dict[str, Any]:
        self.executed.append(kw)
        return kw

    def log_signal_failed(self, **kw: Any) -> Dict[str, Any]:
        self.failed.append(kw)
        return kw

    def log_signal_rejected(self, **kw: Any) -> Dict[str, Any]:
        self.rejected.append(kw)
        return kw


class RecordingEventBus:
    """EventBus stand-in that stores published events."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def publish_event(self, event_type: Any, data: Any, source: str = "") -> None:
        self.events.append({"type": event_type, "data": data, "source": source})


def fake_capabilities(supports_venue_stops: bool) -> ExchangeCapabilities:
    """Capability record for the fake exchange."""
    return ExchangeCapabilities(
        name="fake",
        funding_interval_hours=1,
        has_testnet=True,
        native_order_sides=("bid", "ask"),
        native_position_sides=("long", "short"),
        amounts_as_strings=True,
        min_order_size_source="info_endpoint",
        supports_venue_stops=supports_venue_stops,
    )


class FakeExchange:
    """In-memory ExchangeClient stand-in.

    Records every ``place_order`` kwargs dict in ``orders`` and every
    ``install_stop`` call in ``installs``.  Behaviour is driven by the
    constructor arguments so a test states its scenario up front.

    Args:
        ack: Ack dict returned by ``place_order`` (default: accepted,
            order id ``o<n>``).  A callable receives the kwargs and
            returns the ack.  An Exception instance is raised.
        fill: OrderResult returned by ``get_order_fill`` (default: a
            full fill of the requested quantity at 100.0).  An Exception
            instance is raised.
        stops: Rows returned by ``list_stops`` (an Exception is raised).
        install: OrderResult returned by ``install_stop``.
        supports_venue_stops: Advertised capability.
        balance: Equity reported by ``get_balance``.
        positions: Normalized positions reported by ``get_positions``.
    """

    def __init__(
        self,
        ack: Any = None,
        fill: Any = None,
        stops: Any = None,
        install: Optional[OrderResult] = None,
        supports_venue_stops: bool = False,
        balance: float = 10_000.0,
        positions: Optional[List[ExchangePosition]] = None,
    ) -> None:
        self._ack = ack
        self._fill = fill
        self._stops = stops if stops is not None else []
        self._install = install or OrderResult(
            success=True, accepted=True, order_id="stop-1", status="accepted"
        )
        self._supports = supports_venue_stops
        self.balance = balance
        self.positions = positions or []
        self.orders: List[Dict[str, Any]] = []
        self.installs: List[tuple] = []
        self.fill_lookups: List[Dict[str, Any]] = []

    def capabilities(self) -> ExchangeCapabilities:
        return fake_capabilities(self._supports)

    def place_order(self, **kwargs: Any) -> Any:
        self.orders.append(kwargs)
        if isinstance(self._ack, Exception):
            raise self._ack
        if callable(self._ack):
            return self._ack(kwargs)
        if self._ack is not None:
            return self._ack
        return {"success": True, "data": {"order_id": f"o{len(self.orders)}"}}

    def get_order_fill(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        requested_quantity: Optional[float] = None,
    ) -> Any:
        self.fill_lookups.append(
            {
                "symbol": symbol,
                "order_id": order_id,
                "client_order_id": client_order_id,
                "requested_quantity": requested_quantity,
            }
        )
        if isinstance(self._fill, Exception):
            raise self._fill
        if self._fill is not None:
            return self._fill
        return OrderResult.from_fill_lookup(
            order_id,
            "filled",
            float(requested_quantity or 0.0),
            100.0,
            requested_quantity,
            client_order_id,
        )

    def list_stops(self, symbol: str) -> List[Dict[str, Any]]:
        if isinstance(self._stops, Exception):
            raise self._stops
        return list(self._stops)

    def install_stop(
        self, symbol: str, side: Any, quantity: float, stop_price: float
    ) -> OrderResult:
        self.installs.append((symbol, side, quantity, stop_price))
        return self._install

    def get_balance(self) -> ExchangeBalance:
        return ExchangeBalance(equity=self.balance, available=self.balance)

    def get_positions(self) -> List[ExchangePosition]:
        return list(self.positions)


class FakeRawClient:
    """Native-client stand-in for the few raw ``self.client`` call sites.

    ``get_positions`` returns Pacifica-style dicts (lowercase sides,
    string amounts) because ``exit_sizing`` and ``_monitor_risk`` read
    the raw client, not the adapter.
    """

    def __init__(self, positions: Optional[List[Dict[str, Any]]] = None) -> None:
        self.positions = positions if positions is not None else []
        self.calls = 0

    def get_positions(self) -> Any:
        self.calls += 1
        if isinstance(self.positions, Exception):
            raise self.positions
        return self.positions


def position(
    symbol: str = "BTC",
    side: PositionSide = PositionSide.LONG,
    quantity: float = 1.0,
    entry_price: float = 100.0,
    unrealized_pnl: float = 0.0,
) -> ExchangePosition:
    """Normalized ExchangePosition with defaults."""
    return ExchangePosition(
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        unrealized_pnl=unrealized_pnl,
    )


BOT_METHODS = (
    "_should_execute_signal",
    "_coordinate_signal_execution",
    "_execute_standard_signal_coordinated",
    "_lookup_entry_fill",
    "_record_entry_outcome",
    "_record_entry_fill",
    "_record_entry_rejected",
    "_complete_pending_entries",
    "_ensure_entry_protection",
    "_list_venue_stops",
    "_verify_venue_stop",
    "_apply_stop_failure_policy",
    "_persist_protection",
    "_protection_rows",
    "_is_migrated_symbol",
    "_current_price_for",
    "_enforce_local_stops",
    "_exchange_quantity_for",
    "_mark_position_closed",
    "_emergency_close_position",
    "_get_account_balance",
    "_get_current_exposure",
    "_monitor_risk",
    "_position_unrealized_pnl",
    "_trip_circuit_breaker",
    "_sweep_breaker_entry_orders",
    "_calculate_position_size",
    "_validate_position_size",
    "_calculate_emergency_stop",
)


def make_bot(
    exchange: FakeExchange,
    risk_manager: Any = None,
    raw_client: Any = None,
    policy: str = "close",
    circuit_breaker_loss_pct: float = 0.10,
    ws_prices: Optional[Dict[str, float]] = None,
) -> SimpleNamespace:
    """Duck-typed TradingBot carrying only the methods under test.

    Binding the real TradingBot methods onto a SimpleNamespace avoids
    the constructor (which builds a database, strategy manager, regime
    detector and WebSocket client) while still executing production
    code paths.

    Args:
        exchange: FakeExchange the bound methods will route orders to.
        risk_manager: RiskManager (or stand-in); None leaves it unset.
        raw_client: Native client stand-in for ``self.client``.
        policy: VENUE_STOP_FAILURE_POLICY value.
        circuit_breaker_loss_pct: Breaker threshold as the config stores it.
        ws_prices: Symbol -> last price served by the fake WS client.

    Returns:
        SimpleNamespace with the TradingBot methods bound to it.
    """
    bot = SimpleNamespace(
        exchange=exchange,
        client=raw_client if raw_client is not None else FakeRawClient(),
        db=None,
        risk_manager=risk_manager,
        signal_logger=RecordingSignalLogger(),
        event_bus=RecordingEventBus(),
        execution_layer=SimpleNamespace(refine_entry=lambda signal, symbol: signal),
        ws_client=_fake_ws_client(ws_prices or {}),
        hub_publish_func=None,
        _pending_entries={},
        _entry_fill_max_lookups=3,
        _venue_stop_policy=policy,
        _protection_records={},
        _circuit_breaker_triggered=False,
        _circuit_breaker_loss_pct=circuit_breaker_loss_pct,
        stopped=False,
    )
    _bind_bot_methods(bot)
    return bot


def _fake_ws_client(prices: Dict[str, float]) -> SimpleNamespace:
    """WS client stand-in: "running" only when it has prices to serve."""
    return SimpleNamespace(
        _running=bool(prices), get_price=lambda symbol: prices.get(symbol)
    )


def _bind_bot_methods(bot: SimpleNamespace) -> None:
    """Bind the production TradingBot methods under test onto ``bot``."""
    bot.stop = lambda: setattr(bot, "stopped", True)
    bot._get_ticker_ws = TradingBot._get_ticker_ws.__get__(bot)
    # No REST price in tests: callers fall back to entry price (exposure)
    # or treat the price as unknown (local stop enforcement).
    bot._get_ticker_rest = raiser(RuntimeError("no REST price in tests"))
    for name in BOT_METHODS:
        setattr(bot, name, getattr(TradingBot, name).__get__(bot))
