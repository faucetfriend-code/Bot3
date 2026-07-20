"""
Regime Parameter Overlays (P4)
==============================

Runtime application of per-regime optimized strategy parameters.

Overlays are produced by the regime-conditional optimization CLI
(``python -m trading_bot_v2.optimization.run_regime_optimization``) and
persisted to the ``regime_param_overlays`` table (one active row per
(strategy, regime), history preserved). This module loads the active
overlays and swaps whitelisted parameters onto the live strategy
instances when the confirmed market regime changes.

SCOPING DECISION (shared strategy instances):
    Strategy objects in StrategyManager are single instances shared
    across ALL traded symbols, so per-symbol parameter swapping would be
    wrong whenever two symbols are in different regimes at the same
    time. Overlays are therefore applied GLOBALLY, keyed off the
    confirmed regime of a single designated reference symbol
    (env ``OVERLAY_REFERENCE_SYMBOL``, default ``BACKTEST_SYMBOL`` /
    "SUI-USDC" - the primary optimized market). REGIME_CHANGED events
    for any other symbol are ignored by this manager.

Safety:
    - Ships behind ``ENABLE_REGIME_PARAM_OVERLAYS`` (default false).
      When disabled the manager loads nothing and every handler is a
      no-op, so optimized parameters can never silently go live.
    - Only parameters present in the strategy's Optuna search space
      (``optimization/search_spaces.py``) may be set; anything else in
      the stored JSON is ignored with a warning.
    - Baseline (pre-overlay) values are captured on first application
      and restored whenever the new regime has no stored overlay.
"""

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .market_regime import MarketRegime

# Canonical mapping between optimization strategy keys (snake_case, used
# by search spaces / CLI / overlay storage) and StrategyManager display
# names (keys of StrategyManager.strategies).
STRATEGY_KEY_TO_DISPLAY: Dict[str, str] = {
    "mean_reversion": "MeanReversion",
    "ma_crossover": "MACrossover",
    "grid_trading": "GridTrading",
    "liquidation_capture": "LiquidationCapture",
    "vwap_scalping": "VWAPScalping",
    "funding_arb": "FundingArb",
    "momentum_scalping": "MomentumScalping",
    "orderbook_imbalance": "OrderBookImbalance",
    "session_range_breakout": "SessionRangeBreakout",
}

DISPLAY_TO_STRATEGY_KEY: Dict[str, str] = {
    display: key for key, display in STRATEGY_KEY_TO_DISPLAY.items()
}


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment (true/1/yes/on, false/0/no/off)."""
    value = os.getenv(name, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


def normalize_regime_value(regime: Any) -> str:
    """Normalize a regime identifier to its lowercase enum value.

    Args:
        regime: MarketRegime, enum value string ("ranging_calm"), or
            enum name string ("RANGING_CALM").

    Returns:
        Lowercase regime value (e.g. "ranging_calm").

    Raises:
        ValueError: If the input does not map to a known MarketRegime.
    """
    raw = getattr(regime, "value", regime)
    value = str(raw).strip().lower()
    valid = {r.value for r in MarketRegime}
    if value not in valid:
        raise ValueError(
            f"Unknown regime: {regime!r}. Valid values: {sorted(valid)}"
        )
    return value


def resolve_strategy_display_name(name: str) -> Optional[str]:
    """Resolve a strategy identifier to its StrategyManager display name.

    Accepts either the display form ("MeanReversion") or the
    optimization snake_case form ("mean_reversion").

    Args:
        name: Strategy identifier in either form.

    Returns:
        Display name, or None when the identifier is unknown.
    """
    if name in DISPLAY_TO_STRATEGY_KEY:
        return name
    return STRATEGY_KEY_TO_DISPLAY.get(str(name).strip().lower())


def resolve_strategy_key(name: str) -> Optional[str]:
    """Resolve a strategy identifier to its snake_case optimization key.

    Args:
        name: Strategy identifier in display or snake_case form.

    Returns:
        Snake_case key, or None when the identifier is unknown.
    """
    lowered = str(name).strip().lower()
    if lowered in STRATEGY_KEY_TO_DISPLAY:
        return lowered
    return DISPLAY_TO_STRATEGY_KEY.get(name)


def get_param_whitelist(strategy_key: str) -> set:
    """Return the set of settable parameter names for a strategy.

    The whitelist is derived from the strategy's Optuna search space so
    stored overlay JSON can never set arbitrary attributes.

    Args:
        strategy_key: Snake_case strategy key (e.g. "mean_reversion").

    Returns:
        Set of parameter names; empty set for unknown strategies.
    """
    # Imported lazily to avoid a circular import: the optimization
    # package's __init__ pulls in the backtesting engine, which imports
    # this module for its mapping/apply helpers.
    from .optimization.search_spaces import get_search_space

    try:
        return set(get_search_space(strategy_key).keys())
    except ValueError:
        return set()


def apply_params_to_strategy(
    strategy: Any, strategy_key: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Set whitelisted parameters on a strategy instance.

    Only parameters present in the strategy's search space AND already
    existing as attributes on the instance are applied. Everything else
    is skipped with a warning.

    Args:
        strategy: Strategy instance to modify.
        strategy_key: Snake_case strategy key used for whitelist lookup.
        params: Parameter name -> value mapping to apply.

    Returns:
        Dict of the parameters that were actually applied.
    """
    whitelist = get_param_whitelist(strategy_key)
    applied: Dict[str, Any] = {}

    for name, value in params.items():
        if name not in whitelist:
            logger.warning(
                f"Param overlay: ignoring non-whitelisted key "
                f"'{name}' for {strategy_key}"
            )
            continue
        if not hasattr(strategy, name):
            logger.warning(
                f"Param overlay: {strategy_key} instance has no attribute "
                f"'{name}' - skipping"
            )
            continue
        setattr(strategy, name, value)
        applied[name] = value

    return applied


class RegimeParamOverlayManager:
    """Applies per-regime parameter overlays to shared strategy instances.

    Subscribes to EventType.REGIME_CHANGED (wired in trading_bot.py) and
    reacts only to events for the designated reference symbol - see the
    module docstring for the shared-instance scoping rationale. When the
    reference symbol's confirmed regime changes:

    - If an active overlay exists for (strategy, new_regime): capture the
      strategy's baseline values (first time only) and setattr the
      whitelisted overlay params, logging each swap at INFO.
    - If no overlay exists and the strategy was previously overlaid:
      restore the captured baseline values.

    Behind ENABLE_REGIME_PARAM_OVERLAYS (default false). When disabled
    the manager loads nothing and handle_regime_changed is a no-op.

    Args:
        strategy_manager: StrategyManager holding the live strategy
            instances (its ``strategies`` dict is keyed by display name).
        db: DatabaseManager exposing get_regime_param_overlays. Optional;
            without it no overlays can be loaded.
        enabled: Override for ENABLE_REGIME_PARAM_OVERLAYS (default
            env/false).
        reference_symbol: Override for OVERLAY_REFERENCE_SYMBOL.
    """

    def __init__(
        self,
        strategy_manager: Any,
        db: Optional[Any] = None,
        enabled: Optional[bool] = None,
        reference_symbol: Optional[str] = None,
    ):
        self.strategy_manager = strategy_manager
        self.db = db
        self.enabled = (
            enabled
            if enabled is not None
            else _env_bool("ENABLE_REGIME_PARAM_OVERLAYS", False)
        )
        self.reference_symbol = (
            reference_symbol
            or os.getenv("OVERLAY_REFERENCE_SYMBOL", "")
            or os.getenv("BACKTEST_SYMBOL", "SUI-USDC")
        )

        self._lock = threading.Lock()
        # (strategy_key, regime_value) -> params dict
        self._overlays: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # strategy_key -> {param: baseline_value} captured before first swap
        self._baselines: Dict[str, Dict[str, Any]] = {}
        # strategy_key -> {"regime":, "source":, "params":} of last change
        self._last_applied: Dict[str, Dict[str, Any]] = {}
        self._current_regime: Optional[str] = None

        if self.enabled:
            self.reload()

        logger.info(
            f"RegimeParamOverlayManager initialized (enabled={self.enabled}, "
            f"reference_symbol={self.reference_symbol}, "
            f"overlays_loaded={len(self._overlays)})"
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def reload(self) -> int:
        """Load active overlays from the database.

        No-op (returns 0) when the manager is disabled or no db is wired.

        Returns:
            Number of active overlays loaded.
        """
        if not self.enabled or self.db is None:
            return 0

        try:
            rows = self.db.get_regime_param_overlays(active_only=True)
        except Exception as e:
            logger.error(f"Regime param overlays: load failed: {e}")
            return 0

        loaded: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            strategy_key = resolve_strategy_key(str(row.get("strategy", "")))
            if strategy_key is None:
                logger.warning(
                    f"Regime param overlays: unknown strategy "
                    f"{row.get('strategy')!r} in store - skipping"
                )
                continue
            try:
                regime_value = normalize_regime_value(row.get("regime"))
            except ValueError as e:
                logger.warning(f"Regime param overlays: {e} - skipping row")
                continue
            params = row.get("params") or {}
            if not isinstance(params, dict) or not params:
                logger.warning(
                    f"Regime param overlays: empty/invalid params for "
                    f"({strategy_key}, {regime_value}) - skipping"
                )
                continue
            loaded[(strategy_key, regime_value)] = params

        with self._lock:
            self._overlays = loaded

        logger.info(f"Regime param overlays: loaded {len(loaded)} active overlay(s)")
        return len(loaded)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def handle_regime_changed(self, event: Any) -> None:
        """Handle a REGIME_CHANGED event (exception-safe, runs inline).

        Only events whose symbol matches the reference symbol are acted
        upon (overlays apply globally to shared strategy instances).

        Args:
            event: Event with data containing symbol and new_regime.
        """
        if not self.enabled:
            return

        try:
            data = event.data if hasattr(event, "data") else {}
            symbol = data.get("symbol")
            new_regime_value = data.get("new_regime")
            if not symbol or not new_regime_value:
                logger.warning(
                    "Regime param overlays: event missing symbol/new_regime "
                    "- skipping"
                )
                return
            if symbol != self.reference_symbol:
                logger.debug(
                    f"Regime param overlays: ignoring regime change for "
                    f"{symbol} (reference symbol is {self.reference_symbol})"
                )
                return
            regime_value = normalize_regime_value(new_regime_value)
        except Exception as e:
            logger.error(f"Regime param overlays: could not parse event: {e}")
            return

        try:
            self.apply_for_regime(regime_value)
        except Exception as e:
            logger.error(f"Regime param overlays: application failed: {e}")

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def apply_for_regime(self, regime: Any) -> Dict[str, Dict[str, Any]]:
        """Apply overlays for the given regime to all strategy instances.

        Strategies with an active overlay for the regime get the overlay
        params; strategies previously overlaid but without an overlay for
        this regime are restored to their captured baseline.

        Args:
            regime: Regime identifier (enum, value, or name).

        Returns:
            Dict of strategy_key -> params applied (overlay or baseline).
        """
        if not self.enabled:
            return {}

        regime_value = normalize_regime_value(regime)
        changes: Dict[str, Dict[str, Any]] = {}

        with self._lock:
            self._current_regime = regime_value
            strategies = getattr(self.strategy_manager, "strategies", {}) or {}

            for display_name, strategy in strategies.items():
                strategy_key = DISPLAY_TO_STRATEGY_KEY.get(display_name)
                if strategy_key is None:
                    continue

                overlay = self._overlays.get((strategy_key, regime_value))
                if overlay:
                    self._capture_baseline(strategy_key, strategy)
                    applied = apply_params_to_strategy(
                        strategy, strategy_key, overlay
                    )
                    if applied:
                        logger.info(
                            f"Regime param overlay applied: {strategy_key} "
                            f"regime={regime_value} params={applied}"
                        )
                        self._last_applied[strategy_key] = {
                            "regime": regime_value,
                            "source": "overlay",
                            "params": applied,
                        }
                        changes[strategy_key] = applied
                elif strategy_key in self._baselines:
                    baseline = self._baselines[strategy_key]
                    restored = apply_params_to_strategy(
                        strategy, strategy_key, baseline
                    )
                    if restored:
                        logger.info(
                            f"Regime param overlay: no overlay for "
                            f"{strategy_key} in regime={regime_value} - "
                            f"baseline restored ({restored})"
                        )
                    self._last_applied[strategy_key] = {
                        "regime": regime_value,
                        "source": "baseline",
                        "params": restored,
                    }
                    changes[strategy_key] = restored

        return changes

    def _capture_baseline(self, strategy_key: str, strategy: Any) -> None:
        """Capture pre-overlay values for all whitelisted params (once)."""
        if strategy_key in self._baselines:
            return
        baseline: Dict[str, Any] = {}
        for name in get_param_whitelist(strategy_key):
            if hasattr(strategy, name):
                baseline[name] = getattr(strategy, name)
        self._baselines[strategy_key] = baseline
        logger.debug(
            f"Regime param overlays: captured baseline for {strategy_key}: "
            f"{baseline}"
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return a JSON-serialisable status snapshot for the API layer.

        Returns:
            Dict with enabled flag, reference symbol, current regime,
            loaded overlays, and the last applied state per strategy.
        """
        with self._lock:
            overlays: List[Dict[str, Any]] = [
                {"strategy": key[0], "regime": key[1], "params": params}
                for key, params in sorted(self._overlays.items())
            ]
            return {
                "enabled": self.enabled,
                "reference_symbol": self.reference_symbol,
                "current_regime": self._current_regime,
                "loaded_overlays": overlays,
                "last_applied": dict(self._last_applied),
                "baselines_captured": sorted(self._baselines.keys()),
            }
