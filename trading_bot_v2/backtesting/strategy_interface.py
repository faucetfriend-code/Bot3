"""
Strategy interface for the backtest harness
===========================================

The live strategies in ``trading_bot_v2/strategies/`` share one shape:
``generate_signals(symbol, multi_tf_data, current_price, ...)`` returning
a list of :class:`~trading_bot_v2.models.Signal`. They differ in detail -
MACrossover does not accept ``execution_tf_data``, MomentumScalping
swallows it through ``**kwargs``, MeanReversion and VWAPScalping consume
it - and they keep a private ``_sim_time`` the StrategyManager pokes
directly during replays.

This module pins that contract down as :class:`BacktestStrategy` and
wraps each live class in a :class:`StrategyAdapter` that

* calls ``generate_signals`` with exactly the arguments the wrapped
  class declares,
* propagates simulated time so cooldowns run on candle time,
* exposes ``required_history()`` where the strategy declares one, and
* counts calls, signals and exceptions for the run report.

The registry at the bottom is the list of strategies the harness can
drive directly (that is, without regime gating). Add a strategy by
appending a :class:`StrategySpec`; no other file needs to change.

The live strategy classes themselves are not modified.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from loguru import logger

from ..config import StrategyType
from ..models import Signal

TimeframeBundle = Dict[str, Dict[str, List[Any]]]


class BacktestStrategy(Protocol):
    """What the harness needs from a strategy.

    ``multi_tf_data`` carries the regime/structure timeframes
    (``"15m"``, ``"1h"``, ``"4h"``) and ``execution_tf_data`` the entry
    timeframes (``"5m"``, optionally ``"1m"``). Each value is an OHLCV
    dict of equal-length lists ordered oldest to newest, plus a
    ``"timestamp"`` list of canonical ISO strings. Implementations must
    not look past the last element of any list: the harness hands over
    only history that is complete at the decision time.
    """

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: TimeframeBundle,
        current_price: float,
        execution_tf_data: Optional[TimeframeBundle] = None,
    ) -> List[Signal]:
        """Return zero or more signals for the current bar."""
        ...


@dataclass(frozen=True)
class StrategySpec:
    """Registry entry describing how to build and drive one strategy.

    Attributes:
        key: snake_case identifier (matches ``StrategyType.value`` and the
            optimization/overlay keys used elsewhere in the package).
        display_name: Name the StrategyManager registers it under.
        strategy_type: Enum member the strategy stamps on its signals.
        factory: Zero-argument-capable callable returning a fresh instance;
            keyword overrides are passed straight through.
        primary_timeframe: Bundle the strategy computes its setup on.
        description: One line for ``--list-strategies``.
    """

    key: str
    display_name: str
    strategy_type: StrategyType
    factory: Callable[..., Any]
    primary_timeframe: str
    description: str


@dataclass
class AdapterStats:
    """Counters an adapter accumulates over a run."""

    calls: int = 0
    signals: int = 0
    errors: int = 0
    last_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe view."""
        return {
            "calls": self.calls,
            "signals": self.signals,
            "errors": self.errors,
            "last_error": self.last_error,
        }


class StrategyAdapter:
    """Drive a live strategy instance through :class:`BacktestStrategy`."""

    def __init__(self, strategy: Any, spec: StrategySpec):
        """Wrap ``strategy``.

        Args:
            strategy: Instance of the live strategy class.
            spec: Its registry entry.

        Raises:
            TypeError: When the instance has no callable ``generate_signals``.
        """
        if not callable(getattr(strategy, "generate_signals", None)):
            raise TypeError(
                f"{type(strategy).__name__} has no generate_signals() method"
            )
        self.strategy = strategy
        self.spec = spec
        self.stats = AdapterStats()
        self._passes_execution_data = self._accepts_execution_data(strategy)

    @staticmethod
    def _accepts_execution_data(strategy: Any) -> bool:
        """Whether ``generate_signals`` takes ``execution_tf_data``."""
        try:
            params = inspect.signature(strategy.generate_signals).parameters
        except (TypeError, ValueError):
            return False
        if "execution_tf_data" in params:
            return True
        return any(p.kind is p.VAR_KEYWORD for p in params.values())

    @property
    def key(self) -> str:
        """snake_case strategy identifier."""
        return self.spec.key

    @property
    def name(self) -> str:
        """Display name, as the StrategyManager would register it."""
        return self.spec.display_name

    def set_sim_time(self, dt: Optional[datetime]) -> None:
        """Propagate simulated time the way StrategyManager.set_sim_time does.

        Args:
            dt: Decision-time datetime for the current bar, or None to
                fall back to wall-clock (live behaviour).
        """
        self.strategy._sim_time = dt

    def required_history(self) -> Optional[int]:
        """Minimum primary-timeframe candles the strategy declares, if any."""
        probe = getattr(self.strategy, "required_history", None)
        if not callable(probe):
            return None
        try:
            return int(probe())
        except (TypeError, ValueError):
            return None

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: TimeframeBundle,
        current_price: float,
        execution_tf_data: Optional[TimeframeBundle] = None,
    ) -> List[Signal]:
        """Call the wrapped strategy with the arguments it declares.

        A raising strategy yields an empty list and increments
        ``stats.errors``; the replay must not die on one bad bar, but the
        count has to be visible in the report.
        """
        self.stats.calls += 1
        try:
            if self._passes_execution_data:
                signals = self.strategy.generate_signals(
                    symbol,
                    multi_tf_data,
                    current_price,
                    execution_tf_data=execution_tf_data,
                )
            else:
                signals = self.strategy.generate_signals(
                    symbol, multi_tf_data, current_price
                )
        except Exception as exc:  # noqa: BLE001 - counted, never fatal
            self.stats.errors += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"
            logger.debug(f"{self.name}: generate_signals raised {exc!r}")
            return []
        signals = list(signals or [])
        self.stats.signals += len(signals)
        return signals


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _mean_reversion(**params: Any) -> Any:
    from ..strategies.mean_reversion import MeanReversionStrategy

    return MeanReversionStrategy(**params)


def _ma_crossover(**params: Any) -> Any:
    from ..strategies.ma_crossover import MACrossoverStrategy

    return MACrossoverStrategy(**params)


def _momentum_scalping(**params: Any) -> Any:
    from ..strategies.momentum_scalping import MomentumScalpingStrategy

    return MomentumScalpingStrategy(**params)


def _vwap_scalping(**params: Any) -> Any:
    from ..strategies.vwap_scalping import VWAPScalpingStrategy

    return VWAPScalpingStrategy(**params)


STRATEGY_REGISTRY: Dict[str, StrategySpec] = {
    spec.key: spec
    for spec in (
        StrategySpec(
            key="mean_reversion",
            display_name="MeanReversion",
            strategy_type=StrategyType.MEAN_REVERSION,
            factory=_mean_reversion,
            primary_timeframe="15m",
            description="RSI + Bollinger reversion; 15m structure, 5m trigger",
        ),
        StrategySpec(
            key="ma_crossover",
            display_name="MACrossover",
            strategy_type=StrategyType.MA_CROSSOVER,
            factory=_ma_crossover,
            primary_timeframe="4h",
            description="Fast/slow SMA cross with pullback entry on 4h",
        ),
        StrategySpec(
            key="momentum_scalping",
            display_name="MomentumScalping",
            strategy_type=StrategyType.MOMENTUM_SCALPING,
            factory=_momentum_scalping,
            primary_timeframe="1h",
            description="EMA 9/21 cross with MACD/RSI filters on 1h",
        ),
        StrategySpec(
            key="vwap_scalping",
            display_name="VWAPScalping",
            strategy_type=StrategyType.VWAP_SCALPING,
            factory=_vwap_scalping,
            primary_timeframe="15m",
            description="VWAP standard-deviation band reversion",
        ),
    )
}


def resolve_strategy_key(name: str) -> str:
    """Map a display name or snake_case key to a registry key.

    Args:
        name: "MeanReversion", "mean_reversion" or "MEAN_REVERSION".

    Returns:
        The registry key.

    Raises:
        KeyError: When nothing in the registry matches.
    """
    wanted = str(name).strip()
    lowered = wanted.lower()
    for key, spec in STRATEGY_REGISTRY.items():
        if lowered in (key, spec.display_name.lower()):
            return key
    raise KeyError(
        f"unknown strategy {name!r}; known: {', '.join(sorted(STRATEGY_REGISTRY))}"
    )


def build_adapter(
    name: str, params: Optional[Mapping[str, Any]] = None
) -> StrategyAdapter:
    """Instantiate a registered strategy and wrap it.

    Args:
        name: Registry key or display name.
        params: Constructor keyword overrides (e.g. ``fast_ma_period``).

    Returns:
        A ready adapter with zeroed stats.
    """
    spec = STRATEGY_REGISTRY[resolve_strategy_key(name)]
    return StrategyAdapter(spec.factory(**dict(params or {})), spec)


def build_adapters(
    names: Sequence[str],
    params: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[StrategyAdapter]:
    """Build one adapter per name, in order, rejecting duplicates.

    Args:
        names: Registry keys or display names.
        params: Per-strategy constructor overrides keyed by registry key.

    Returns:
        Adapters in the order given.

    Raises:
        ValueError: When a strategy is named twice.
    """
    adapters: List[StrategyAdapter] = []
    seen = set()
    for name in names:
        key = resolve_strategy_key(name)
        if key in seen:
            raise ValueError(f"strategy {key!r} listed more than once")
        seen.add(key)
        adapters.append(build_adapter(key, (params or {}).get(key)))
    return adapters
