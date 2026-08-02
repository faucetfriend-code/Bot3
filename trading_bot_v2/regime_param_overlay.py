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
    - A stored overlay whose recorded objective value is at or below its
      objective's break-even (or which carries no score at all) is
      REFUSED at load and again at apply time, logged at ERROR, and the
      strategy keeps its normal parameters. This is deliberately a
      second, independent guard: ``database.save_regime_param_overlay``
      only sees rows written through the save API, so it cannot see a
      database restored from ``backups/*.db`` (a plain file copy), a
      hand-edited database, or a row written before that guard existed.
      All eleven snapshots under ``backups/`` still contain the
      -1.486 Sharpe mean_reversion/ranging_calm overlay with
      ``active=1``; restoring any of them reinstates it, and only this
      guard stands between that row and a live strategy.
"""

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .market_regime import MarketRegime
from .overlay_quality import (
    OVERLAY_OPT_IN_ENV,
    overlay_env_opt_in,
    overlay_rejection_reason,
)

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
    "calendar_flow": "CalendarFlow",
    "vwap_pullback": "VWAPPullback",
}

DISPLAY_TO_STRATEGY_KEY: Dict[str, str] = {
    display: key for key, display in STRATEGY_KEY_TO_DISPLAY.items()
}


# Search-space parameter name -> strategy INSTANCE attribute name, for the
# cases where the two diverge.
#
# WHY THIS EXISTS: overlays and optimizer parameter overrides are applied by
# setattr (see apply_params_to_strategy), so a search-space key that does not
# match the instance attribute is a SILENT NO-OP - the trial samples a value,
# the value is never read, and every sampled value scores identically. An
# audit on 2026-07-28 found 11 such parameters across four strategies, which
# meant liquidation_capture was searching 2 of its 7 declared dimensions and
# grid_trading 2 of its 5.
#
# The alias is applied instead of renaming the search-space keys so existing
# Optuna studies stay readable and comparable. tests/test_regime_optimization.py
# ::TestSearchSpaceReachability asserts every declared parameter resolves, so
# this class of bug cannot recur silently.
PARAM_ATTR_ALIASES: Dict[str, Dict[str, str]] = {
    "grid_trading": {
        "grid_spacing_atr_multiplier": "grid_spacing_multiplier",
        "emergency_stop_loss_pct": "emergency_stop_pct",
        "adx_regime_threshold": "adx_threshold",
    },
    "liquidation_capture": {
        "price_move_threshold": "price_threshold",
        "volume_spike_multiplier": "volume_multiplier",
        "rsi_oversold_threshold": "rsi_oversold",
        "rsi_overbought_threshold": "rsi_overbought",
        "min_consecutive_moves": "min_consecutive",
    },
    "orderbook_imbalance": {
        "imbalance_long_threshold": "imbalance_long",
        "imbalance_short_threshold": "imbalance_short",
        "strong_imbalance_threshold": "strong_imbalance",
    },
}


def resolve_param_attr(strategy_key: str, param: str) -> str:
    """Map a search-space parameter name to its instance attribute name.

    Args:
        strategy_key: Snake_case strategy key.
        param: Search-space parameter name.

    Returns:
        The attribute name to setattr, which is ``param`` itself unless
        PARAM_ATTR_ALIASES declares a rename for this strategy.
    """
    return PARAM_ATTR_ALIASES.get(strategy_key, {}).get(param, param)


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment (true/1/yes/on, false/0/no/off)."""
    value = os.getenv(name, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


#: Direction buckets a composite regime key may carry after the colon.
#: "bull"/"bear"/"neutral" match the trade log's direction tag exactly;
#: "trend" matches bull OR bear (mean-reversion-style strategies fade
#: symmetrically, so splitting their sample by trend side would halve
#: it for no modelled reason).
COMPOSITE_DIRECTIONS = ("bull", "bear", "neutral", "trend")


def normalize_regime_value(regime: Any) -> str:
    """Normalize a regime identifier to its lowercase value.

    Accepts a plain regime (MarketRegime, "ranging_calm", or
    "RANGING_CALM") or a composite "REGIME:DIRECTION" key
    (e.g. "VOL_LOW:TREND") whose direction part is one of
    COMPOSITE_DIRECTIONS. Composite keys are what the vol-tercile x
    direction variant tuner passes; every pre-existing caller sends
    plain regimes and is unaffected.

    Args:
        regime: Regime identifier, optionally "REGIME:DIRECTION".

    Returns:
        Lowercase value ("ranging_calm", "vol_low:trend").

    Raises:
        ValueError: If either part does not map to a known value.
    """
    raw = getattr(regime, "value", regime)
    value = str(raw).strip().lower()

    direction = None
    if ":" in value:
        value, direction = value.split(":", 1)
        if direction not in COMPOSITE_DIRECTIONS:
            raise ValueError(
                f"Unknown direction in composite regime: {direction!r}. "
                f"Valid: {sorted(COMPOSITE_DIRECTIONS)}"
            )

    valid = {r.value for r in MarketRegime}
    if value not in valid:
        raise ValueError(
            f"Unknown regime: {regime!r}. Valid values: {sorted(valid)}"
        )
    return value if direction is None else f"{value}:{direction}"


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


def effective_params(strategy: Any, strategy_key: str) -> Dict[str, Any]:
    """Read the strategy's current values for every search-space parameter.

    Used to feasibility-check an overlay against the parameters it does
    NOT set: a coupled constraint (e.g. momentum's constant RRR) is only
    decidable on the full combination.

    Args:
        strategy: Strategy instance to read.
        strategy_key: Snake_case strategy key.

    Returns:
        Parameter name -> current value, omitting params the instance
        does not carry.
    """
    values: Dict[str, Any] = {}
    for name in get_param_whitelist(strategy_key):
        attr = resolve_param_attr(strategy_key, name)
        if hasattr(strategy, attr):
            values[name] = getattr(strategy, attr)
    return values


def apply_params_to_strategy(
    strategy: Any,
    strategy_key: str,
    params: Dict[str, Any],
    check_feasibility: bool = True,
) -> Dict[str, Any]:
    """Set whitelisted parameters on a strategy instance.

    Only parameters present in the strategy's search space AND resolving
    to an existing attribute on the instance are applied. Everything else
    is skipped with a warning. Search-space names that differ from the
    instance attribute name are translated via PARAM_ATTR_ALIASES.

    Strategy ``__init__`` methods repair or clamp some parameters
    (momentum's constant RRR, VWAP's deviation threshold), and setattr
    bypasses all of that. So the resulting COMBINATION is re-checked
    against search_spaces.check_param_feasibility, and an infeasible one
    is rolled back rather than silently disabling the strategy - an
    out-of-range value here produces zero signals, not worse ones.

    This helper does NOT judge whether the parameters are any good: it
    is shared with the optimizer and the backtest engine, which apply
    unscored trial parameters by design. The was-it-measured-to-lose
    check therefore lives one level up, in
    :func:`refuse_overlay_reason` / RegimeParamOverlayManager, which is
    the only path that puts STORED overlays onto live strategies.

    Args:
        strategy: Strategy instance to modify.
        strategy_key: Snake_case strategy key used for whitelist lookup.
        params: Parameter name -> value mapping to apply.
        check_feasibility: Roll back the whole application when the
            resulting combination could never trade. Leave True outside
            tests.

    Returns:
        Dict of the parameters that were actually applied (empty when the
        combination was rejected as infeasible).
    """
    whitelist = get_param_whitelist(strategy_key)
    applied: Dict[str, Any] = {}
    previous: Dict[str, Any] = {}

    for name, value in params.items():
        if name not in whitelist:
            logger.warning(
                f"Param overlay: ignoring non-whitelisted key "
                f"'{name}' for {strategy_key}"
            )
            continue
        attr = resolve_param_attr(strategy_key, name)
        if not hasattr(strategy, attr):
            logger.warning(
                f"Param overlay: {strategy_key} instance has no attribute "
                f"'{attr}' (search-space name '{name}') - skipping. This "
                f"parameter is a NO-OP: add it to PARAM_ATTR_ALIASES or "
                f"drop it from the search space."
            )
            continue
        previous[attr] = getattr(strategy, attr)
        setattr(strategy, attr, value)
        applied[name] = value

    if applied and check_feasibility:
        reasons = _infeasible_reasons(strategy, strategy_key)
        if reasons:
            for attr, old in previous.items():
                setattr(strategy, attr, old)
            logger.error(
                f"Param overlay REJECTED for {strategy_key}: the resulting "
                f"combination could never trade - "
                + "; ".join(reasons)
                + ". Previous values restored."
            )
            return {}

    return applied


def _infeasible_reasons(strategy: Any, strategy_key: str) -> list:
    """Return feasibility violations of the strategy's current params.

    Args:
        strategy: Strategy instance, already mutated.
        strategy_key: Snake_case strategy key.

    Returns:
        List of human-readable reasons; empty means feasible. A missing
        or failing feasibility checker yields an empty list, so this can
        never block an application for infrastructural reasons.
    """
    try:
        from .optimization.search_spaces import check_param_feasibility

        return list(
            check_param_feasibility(
                strategy_key, effective_params(strategy, strategy_key)
            )
        )
    except Exception as e:  # noqa: BLE001 - never block on the checker
        logger.debug(f"Param overlay: feasibility check unavailable: {e}")
        return []


def refuse_overlay_reason(
    strategy_key: str,
    regime_value: str,
    objective: Optional[str],
    objective_value: Optional[float],
) -> Optional[str]:
    """Decide whether a stored overlay may touch a running strategy.

    This is the APPLY-side half of the losing-overlay guard. The
    WRITE-side half (``DatabaseManager.save_regime_param_overlay``) only
    sees rows written through the save API and is blind to a restored
    backup, a hand-edited database, or any row written before it
    existed - so the decision is taken again here, against the value
    recorded on the row itself.

    Policy, matching the write guard where they overlap:

    - Score at or below the objective's break-even -> refuse (0.0 for
      the Sharpe-like objectives, 1.0 for profit_factor).
    - Unknown or unnamed objective -> break-even 0.0, exactly as the
      write guard treats it.
    - No recorded score, or a non-numeric one -> refuse. The write guard
      is permissive here because it cannot judge a row it was handed
      without a score, and storing such a row is harmless (it is audit
      material). Applying one is not: an unscored overlay is precisely
      the shape of a row that never went through a measured write path,
      which is the hazard this guard exists for. Unknown is not the same
      as good, and the cost of refusing is only that the study has to be
      re-run.
    - ``ALLOW_LOSING_REGIME_OVERLAYS`` overrides the refusal, the same
      operator opt-in the write guard honours, logged at WARNING.

    Args:
        strategy_key: Snake_case strategy key, for the log line.
        regime_value: Regime the overlay is stored under, for the log.
        objective: Objective name recorded with the overlay.
        objective_value: Objective value recorded with the overlay.

    Returns:
        None when the overlay may be applied, otherwise the reason it
        was refused (already logged at ERROR).
    """
    reason = overlay_rejection_reason(objective, objective_value)
    if reason is None:
        return None

    if overlay_env_opt_in():
        logger.warning(
            f"Regime param overlay for {strategy_key} "
            f"regime={regime_value} would be refused ({reason}), but "
            f"{OVERLAY_OPT_IN_ENV} is set - applying it anyway."
        )
        return None

    logger.error(
        f"Regime param overlay REFUSED for strategy={strategy_key} "
        f"regime={regime_value} objective={objective or 'none'} "
        f"objective_value={objective_value!r}: {reason}. The overlay is "
        f"NOT applied and {strategy_key} keeps its normal parameters. "
        f"Deactivate the row (DatabaseManager."
        f"deactivate_regime_param_overlay) or re-run the study; set "
        f"{OVERLAY_OPT_IN_ENV}=true only if you intend to trade a "
        f"configuration that was measured to lose."
    )
    return reason


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
        # (strategy_key, regime_value) -> (objective, objective_value) as
        # recorded on the row, re-checked at apply time. A key present in
        # _overlays but missing here is treated as unscored, so an overlay
        # injected past reload() is refused too.
        self._overlay_scores: Dict[Tuple[str, str], Tuple[Any, Any]] = {}
        # (strategy_key, regime_value) -> refusal reason, for get_status
        self._refused: Dict[Tuple[str, str], str] = {}
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

        Rows that fail :func:`refuse_overlay_reason` (measured at or
        below break-even, or carrying no score at all) are dropped here
        with an ERROR and never reach a strategy - whatever put them in
        the table, including a restored backup.

        No-op (returns 0) when the manager is disabled or no db is wired.

        Returns:
            Number of active overlays loaded (refused rows excluded).
        """
        if not self.enabled or self.db is None:
            return 0

        try:
            rows = self.db.get_regime_param_overlays(active_only=True)
        except Exception as e:
            logger.error(f"Regime param overlays: load failed: {e}")
            return 0

        loaded: Dict[Tuple[str, str], Dict[str, Any]] = {}
        scores: Dict[Tuple[str, str], Tuple[Any, Any]] = {}
        refused: Dict[Tuple[str, str], str] = {}
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

            key = (strategy_key, regime_value)
            objective = row.get("objective")
            objective_value = row.get("objective_value")
            reason = refuse_overlay_reason(
                strategy_key, regime_value, objective, objective_value
            )
            if reason is not None:
                refused[key] = reason
                continue

            loaded[key] = params
            scores[key] = (objective, objective_value)

        with self._lock:
            self._overlays = loaded
            self._overlay_scores = scores
            self._refused = refused

        if refused:
            logger.error(
                f"Regime param overlays: {len(refused)} stored overlay(s) "
                f"refused as losing/unscored and will not be applied: "
                f"{sorted(refused.keys())}"
            )
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

        An overlay whose recorded objective value is losing or missing is
        refused here as well as at load (:meth:`_refuse_at_apply`) and is
        treated exactly like "no overlay for this regime": the strategy
        keeps (or is restored to) its normal parameters. Nothing is
        raised - a bad stored row must not take the trading loop down.

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

                key = (strategy_key, regime_value)
                overlay = self._overlays.get(key)
                if overlay and self._refuse_at_apply(key):
                    overlay = None
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

    def _refuse_at_apply(self, key: Tuple[str, str]) -> bool:
        """Re-check a loaded overlay's score at the moment of use.

        reload() already drops losing rows, so this normally passes. It
        exists because ``_overlays`` can be populated by something other
        than reload() (a test, a future loader, a caller poking the
        dict), and the guarantee has to be "nothing losing ever reaches
        a strategy", not "nothing losing survives reload". An overlay
        with no recorded score in ``_overlay_scores`` is treated as
        unscored and refused, which is the fail-closed default.

        Args:
            key: (strategy_key, regime_value) of the overlay.

        Returns:
            True when the overlay must not be applied.
        """
        objective, objective_value = self._overlay_scores.get(key, (None, None))
        reason = refuse_overlay_reason(key[0], key[1], objective, objective_value)
        if reason is None:
            return False
        self._refused[key] = reason
        return True

    def _capture_baseline(self, strategy_key: str, strategy: Any) -> None:
        """Capture pre-overlay values for all whitelisted params (once)."""
        if strategy_key in self._baselines:
            return
        baseline = effective_params(strategy, strategy_key)
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
            loaded overlays, refused overlays (losing or unscored rows
            that were dropped, with the reason), and the last applied
            state per strategy.
        """
        with self._lock:
            overlays: List[Dict[str, Any]] = [
                {"strategy": key[0], "regime": key[1], "params": params}
                for key, params in sorted(self._overlays.items())
            ]
            refused: List[Dict[str, Any]] = [
                {"strategy": key[0], "regime": key[1], "reason": reason}
                for key, reason in sorted(self._refused.items())
            ]
            return {
                "enabled": self.enabled,
                "reference_symbol": self.reference_symbol,
                "current_regime": self._current_regime,
                "loaded_overlays": overlays,
                "refused_overlays": refused,
                "last_applied": dict(self._last_applied),
                "baselines_captured": sorted(self._baselines.keys()),
            }
