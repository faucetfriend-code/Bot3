"""
Optuna Search Spaces for Trading Strategies
==========================================

Defines parameter search spaces for all 8 trading strategies.
Each search space is tailored to the strategy's specific parameters
and their valid ranges based on historical analysis and domain knowledge.

Usage:
    from trading_bot_v2.optimization.search_spaces import get_search_space

    space = get_search_space("mean_reversion")
    # Returns a dict of parameter_name -> (low, high) or categorical choices
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..diagnostics.gate_metrics import (
    SEVERITY_UNREACHABLE,
    load_calibrations,
    threshold_verdicts,
)

logger = logging.getLogger(__name__)

# Type alias for search space definitions
SearchSpace = Dict[str, Any]


# ---------------------------------------------------------------------------
# Feasibility constraints
# ---------------------------------------------------------------------------
#
# Some strategies derive both stop and target from the SAME ATR value, so
# their reward/risk ratio is the constant atr_target_mult / atr_stop_mult -
# it does not depend on market data at all. If that constant falls below the
# strategy's rrr_meets_minimum threshold, every signal it emits fails
# Signal.is_valid() and is dropped by StrategyManager. Such a parameter set
# is not "bad", it is unbacktestable: it produces zero trades no matter what
# the data does.
#
# Maps strategy name -> the minimum reward/risk its signals must clear.
MIN_RRR_CONSTRAINTS: Dict[str, float] = {
    "momentum_scalping": 1.5,  # momentum_scalping.py min_rrr default
    "orderbook_imbalance": 1.5,  # orderbook_imbalance.py hardcoded gate
}

# Headroom applied to the coupled lower bound in suggest_params. The realised
# RRR is recomputed from prices at signal time, where float noise turns a
# nominal 1.5 into 1.4999999999999998 and fails a `>=` gate, so suggestions
# must sit strictly inside the feasible region rather than on its edge.
RRR_FEASIBILITY_MARGIN = 1.02


# Pairs of parameters where the first MUST stay strictly below the second,
# because the interval between them is a gate the strategy has to pass
# through. An inverted or collapsed pair is not "bad tuning" - it makes the
# gate unreachable, so the strategy runs and emits nothing.
#
# Maps strategy name -> tuple of (lower_param, upper_param, consequence).
ORDERED_PAIR_CONSTRAINTS: Dict[str, Tuple[Tuple[str, str, str], ...]] = {
    "ma_crossover": (
        (
            "fast_ma_period",
            "slow_ma_period",
            "a fast MA at or above the slow MA can never cross it, so no "
            "crossover is ever detected",
        ),
        (
            "pullback_range_min",
            "pullback_range_max",
            "the accepted pullback/rally band would be empty, so no "
            "detected crossover could ever convert into an entry",
        ),
        (
            "min_entry_bars",
            "max_entry_bars",
            "the post-crossover entry window would be empty, so every "
            "crossover expires unused",
        ),
    ),
}


# Strategies whose longest indicator lookback is a searched parameter, and
# whose _validate_data() refuses to run when the caller's rolling history
# window is shorter than it. The backtest engine hands each strategy
# BACKTEST_HISTORY_LOOKBACK candles per timeframe (default 60), so a slow
# MA at or above that budget disables the strategy for the whole trial.
#
# Maps strategy name -> (param_name, extra_candles_needed).
HISTORY_BUDGET_CONSTRAINTS: Dict[str, Tuple[str, int]] = {
    # required_history() = max(slow_ma_period + 1, macd_slow + macd_signal)
    "ma_crossover": ("slow_ma_period", 1),
}

# Fallback when BACKTEST_HISTORY_LOOKBACK is unset - mirrors the same
# default used by backtesting/engine.py.
DEFAULT_HISTORY_LOOKBACK = 60


def get_history_lookback() -> int:
    """Return the rolling history budget the backtest engine will supply.

    Read at call time (not import time) so a caller that adjusts
    BACKTEST_HISTORY_LOOKBACK before optimizing gets the matching bound.

    Returns:
        Candles per timeframe handed to each strategy, minimum 1.
    """
    raw = os.getenv("BACKTEST_HISTORY_LOOKBACK")
    try:
        value = int(raw) if raw else DEFAULT_HISTORY_LOOKBACK
    except ValueError:
        value = DEFAULT_HISTORY_LOOKBACK
    return max(1, value)


class InfeasibleParamsError(ValueError):
    """Raised when a parameter set can never produce a tradeable signal."""


# ---------------------------------------------------------------------------
# Calibrated gate metrics
# ---------------------------------------------------------------------------
#
# The constraints above are ALGEBRAIC: they can be evaluated with zero
# data because the relationship is fixed by construction (a fast MA at or
# above a slow MA never crosses it). The VWAP class of bug is not like
# that. VWAP_SD_ENTRY_THRESHOLD=4.037 was arithmetically fine and broke
# nothing structurally - it was simply above the highest value the metric
# ever took on real data (3.95 over 52,041 bars), so no bar could pass and
# the strategy silently produced nothing for months.
#
# Catching that needs measurement, which is what the calibration
# artifacts under diagnostics/calibration/ carry. Consumption rules, per
# the observability plan:
#
#   threshold above the observed max  -> HARD violation, the trial is
#                                        pruned before the backtest burns
#   threshold above p99               -> WARNING, the trial still runs
#   no artifact for this strategy     -> skipped silently, logged once
#
# Deliberately wired through check_param_feasibility rather than as a
# parallel path, so everything downstream (suggest_params, validate_params,
# InfeasibleParamsError, optuna_runner's TrialPruned handler) keeps
# working unchanged.

#: Keys already logged, so a study of 200 trials emits one line per
#: dimension rather than 200.
_CALIBRATION_LOGGED: set[Tuple[str, ...]] = set()


def calibration_warnings(
    strategy_name: str,
    params: Dict[str, Any],
    symbol: Optional[str] = None,
) -> List[str]:
    """
    Non-fatal calibration findings for a parameter set.

    A threshold beyond the 99th percentile of its metric is satisfiable
    but by under 1% of bars - worth recording, not worth pruning. Exposed
    separately so a caller can attach these to a trial's user_attrs.

    Args:
        strategy_name: Strategy name.
        params: Candidate parameter values.
        symbol: Restrict to one calibrated symbol (None = all).

    Returns:
        Human-readable warnings. Empty when there is nothing to say.
    """
    _, warnings = threshold_verdicts(strategy_name, params, symbol=symbol)
    return [
        verdict.reason
        for verdict in warnings
        if verdict.severity != SEVERITY_UNREACHABLE
    ]


def _calibration_reasons(
    strategy_name: str,
    params: Dict[str, Any],
    symbol: Optional[str] = None,
) -> List[str]:
    """
    Hard infeasibility reasons drawn from the calibration artifacts.

    Args:
        strategy_name: Strategy name.
        params: Candidate parameter values.
        symbol: Restrict to one calibrated symbol (None = all).

    Returns:
        Reasons for thresholds no observed value could ever satisfy.
    """
    if not load_calibrations(strategy_name, symbol=symbol):
        key: Tuple[str, ...] = ("missing", strategy_name, symbol or "*")
        if key not in _CALIBRATION_LOGGED:
            _CALIBRATION_LOGGED.add(key)
            logger.info(
                "No gate-metric calibration artifact for %s%s - unreachable "
                "thresholds cannot be detected. Build one with: python -m "
                "trading_bot_v2.diagnostics.calibrate --strategies %s",
                strategy_name,
                f" ({symbol})" if symbol else "",
                strategy_name,
            )
        return []

    hard, warnings = threshold_verdicts(strategy_name, params, symbol=symbol)
    for verdict in warnings:
        key = ("warn", strategy_name, verdict.metric, verdict.param_key)
        if key not in _CALIBRATION_LOGGED:
            _CALIBRATION_LOGGED.add(key)
            logger.warning("Gate-metric calibration: %s", verdict.reason)
    return [verdict.reason for verdict in hard]


def check_param_feasibility(
    strategy_name: str,
    params: Dict[str, Any],
    symbol: Optional[str] = None,
) -> List[str]:
    """
    Check a parameter set for combinations that can never trade.

    Args:
        strategy_name: Strategy name (e.g. "momentum_scalping")
        params: Candidate parameter values.
        symbol: Symbol whose calibration artifact should be consulted.
            None consults every calibrated symbol and only calls a
            threshold unreachable when it is unreachable on all of them.

    Returns:
        List of human-readable reasons. Empty means feasible.
    """
    reasons: List[str] = []

    min_rrr = MIN_RRR_CONSTRAINTS.get(strategy_name)
    if min_rrr is not None:
        stop = params.get("atr_stop_mult")
        target = params.get("atr_target_mult")
        if stop is not None and target is not None:
            if stop <= 0:
                reasons.append(f"atr_stop_mult={stop} must be > 0")
            elif target / stop < min_rrr:
                reasons.append(
                    f"atr_target_mult={target:.3f} / atr_stop_mult={stop:.3f} "
                    f"= RRR {target / stop:.3f} < required {min_rrr:.2f}; "
                    f"every signal would fail rrr_meets_minimum and be "
                    f"discarded before execution"
                )

    for lower_name, upper_name, consequence in ORDERED_PAIR_CONSTRAINTS.get(
        strategy_name, ()
    ):
        lower = params.get(lower_name)
        upper = params.get(upper_name)
        if lower is None or upper is None:
            continue
        if lower >= upper:
            reasons.append(
                f"{lower_name}={lower} must be < {upper_name}={upper}; "
                f"otherwise {consequence}"
            )

    budget = HISTORY_BUDGET_CONSTRAINTS.get(strategy_name)
    if budget is not None:
        param_name, extra = budget
        value = params.get(param_name)
        if value is not None:
            lookback = get_history_lookback()
            if value + extra > lookback:
                reasons.append(
                    f"{param_name}={value} needs {value + extra} candles of "
                    f"history but the engine supplies only {lookback} "
                    f"(BACKTEST_HISTORY_LOOKBACK); _validate_data would "
                    f"reject every bar and the strategy would emit nothing"
                )

    reasons.extend(_calibration_reasons(strategy_name, params, symbol=symbol))

    return reasons


def validate_params(
    strategy_name: str,
    params: Dict[str, Any],
    symbol: Optional[str] = None,
) -> None:
    """
    Raise InfeasibleParamsError if params can never produce a valid signal.

    Args:
        strategy_name: Strategy name.
        params: Candidate parameter values.
        symbol: Symbol whose calibration artifact should be consulted.

    Raises:
        InfeasibleParamsError: With all failing reasons in the message.
    """
    reasons = check_param_feasibility(strategy_name, params, symbol=symbol)
    if reasons:
        raise InfeasibleParamsError(
            f"Infeasible parameters for {strategy_name}: " + "; ".join(reasons)
        )


def get_search_space(strategy_name: str) -> SearchSpace:
    """
    Get the Optuna search space for a given strategy.

    Args:
        strategy_name: Name of the strategy (e.g., "mean_reversion")

    Returns:
        Dictionary mapping parameter names to their search ranges.
        - For continuous parameters: {"name": (low, high)}
        - For integer parameters: {"name": (low, high)}
        - For categorical parameters: {"name": [option1, option2, ...]}

    Raises:
        ValueError: If strategy_name is not recognized.
    """
    if strategy_name not in SEARCH_SPACE_BUILDERS:
        available = ", ".join(SEARCH_SPACE_BUILDERS.keys())
        raise ValueError(f"Unknown strategy: '{strategy_name}'. Available: {available}")

    return SEARCH_SPACE_BUILDERS[strategy_name]()


def list_strategies() -> List[str]:
    """Return list of all available strategy names for optimization."""
    return list(SEARCH_SPACE_BUILDERS)


# ---------------------------------------------------------------------------
# Strategy Search Spaces
# ---------------------------------------------------------------------------


def _mean_reversion_space() -> SearchSpace:
    """
    Mean Reversion Strategy search space.

    Focuses on RSI thresholds, Bollinger Bands, and ATR-based stops.
    Optimized for RANGING_CALM regime.

    NOTE: ``min_confidence`` is deliberately ABSENT (removed 2026-08-02).
    It is inert for this strategy - MeanReversion assigns it in __init__
    and then references it only inside debug log f-strings; nothing ever
    compares against it. The one gate that could act on confidence,
    StrategyManager._apply_regime_confidence_gate, uses the global
    MIN_SIGNAL_CONFIDENCE_FLOOR (0.0) plus a per-regime adjustment that
    is zero for RANGING_CALM - the only regime MeanReversion is admitted
    to - so it returns early every time. Tuning it therefore sampled
    pure noise and consumed trial budget that the other four dimensions
    could have used. Confidence still matters LIVE, where ConfidenceSizer
    scales position size by it, but that is a continuous effect with no
    threshold to tune. Do not re-add without first making it gate
    something; see docs/MEANREVERSION-MTF-CONFIDENCE-2026-08-02.md.
    """
    return {
        # RSI parameters
        "rsi_oversold": (25.0, 45.0),  # Lower = more aggressive oversold
        "rsi_overbought": (55.0, 75.0),  # Higher = more aggressive overbought
        # Bollinger Bands
        "bb_std_dev": (1.5, 3.0),  # Wider = fewer but stronger signals
        # Stop loss
        "atr_stop_multiplier": (1.5, 3.0),  # Tighter = more stops, wider = more room
    }


def _ma_crossover_space() -> SearchSpace:
    """
    MA Crossover Strategy search space.

    Optimized for TRENDING regimes. Ordered by measured leverage, from a
    signal-funnel run over SUI-USDC 2024-06-01..2024-09-01 (285 raw
    signals -> 3 closed trades):

        validity_dropped    181 (63.5% of raw)  validity:volume_confirmation
        execution_blocked   101 (35.4% of raw)  exec:same_direction_skip

    So volume_threshold - not the entry window, as previously assumed -
    is the binding constraint, and its range is widened BELOW 1.0 so the
    optimizer can weaken or effectively disable the gate. It also feeds
    the confidence score (volume_ratio / volume_threshold), so lowering
    it relieves min_confidence at the same time.

    COUPLED DIMENSIONS (enforced in suggest_params / check_param_feasibility,
    see ORDERED_PAIR_CONSTRAINTS and HISTORY_BUDGET_CONSTRAINTS):
    - fast_ma_period < slow_ma_period, else no crossover is ever detected.
    - pullback_range_min < pullback_range_max, else the entry band is empty.
    - slow_ma_period + 1 <= BACKTEST_HISTORY_LOOKBACK, else _validate_data
      rejects every bar. The declared upper bound of 55 already respects
      the default 60-candle budget; the feasibility check catches a
      lowered lookback.

    min_entry_bars is deliberately NOT searched. Pinned at the strategy
    default of 1, it means "no entry on the crossover bar itself", which
    is the point of the pullback model; making it a free dimension mostly
    buys ways to skip the best bars. max_entry_bars IS searched: at 4h
    resolution the default 5 is only a 20-hour window.
    """
    return {
        # --- Binding constraint (funnel-confirmed) ---
        # .env runs 1.2. Below 1.0 the gate accepts below-average volume;
        # 0.5 is effectively off.
        "volume_threshold": (0.5, 1.8),
        # --- Entry window, in 4h bars after the crossover bar ---
        # 1 = same 20h band as today's default at the low end,
        # 24 = four days of patience.
        "max_entry_bars": (1, 24),
        # --- Pullback entry band (fraction of the fast MA) ---
        # .env runs 0.00-0.10. min < max is enforced.
        "pullback_range_min": (0.0, 0.04),
        "pullback_range_max": (0.01, 0.12),
        # --- Moving average periods (fast < slow enforced) ---
        # .env runs 10/30. slow caps at 55 to stay inside the engine's
        # 60-candle 4h history budget (a 40-80 range, as declared before,
        # spent most of its mass on configurations that emit nothing).
        "fast_ma_period": (5, 25),
        "slow_ma_period": (20, 55),
        # --- Risk ---
        "atr_stop_multiplier": (1.5, 3.5),  # ATR multiplier for stop
        # --- Confidence gate ---
        # Strategy default is 0.50; the score is
        # 0.3*volume + 0.4*macd + 0.3*pullback, which rarely clears 0.6.
        "min_confidence": (0.20, 0.60),
    }


def _vwap_pullback_space() -> SearchSpace:
    """
    VWAP Pullback (trend-side continuation) search space.

    Shipped defaults passed the gate untuned (PF 1.61, 2026-07-30
    campaign), so ranges bracket the defaults rather than exploring far
    from them - the tuner must beat a known-good baseline out-of-sample.
    Params are applied by setattr; every key matches an instance
    attribute of VWAPPullbackStrategy.
    """
    return {
        # Entry geometry
        "band_sd": (0.1, 0.5),  # Pullback band half-width (sigma)
        "extension_min_sd": (0.5, 2.0),  # Required prior extension (sigma)
        # Exit geometry
        "atr_stop_buffer": (0.25, 1.25),  # Stop beyond pullback extreme (ATR)
        "tp_rr": (1.0, 3.0),  # Target as multiple of risk
        "time_exit_hours": (8, 48),  # Max hold (int hours)
        # Filters
        "rvol_min": (0.0, 2.0),  # Resumption-bar rvol floor (0 = off)
        "cooldown_hours": (2.0, 8.0),  # Per-symbol entry spacing
    }


def _grid_trading_space() -> SearchSpace:
    """
    Grid Trading Strategy search space.

    Focuses on grid levels, spacing, and risk parameters.
    Optimized for RANGING_VOLATILE regime.
    """
    return {
        # Grid structure
        "grid_levels": (4, 12),  # Number of grid levels (int)
        "grid_spacing_atr_multiplier": (0.3, 0.8),  # ATR multiplier for spacing
        # Risk management
        "emergency_stop_loss_pct": (0.03, 0.08),  # Emergency stop threshold
        "adx_regime_threshold": (15.0, 25.0),  # ADX threshold for regime change
        # Confidence
        "min_confidence": (0.35, 0.60),
    }


def _liquidation_capture_space() -> SearchSpace:
    """
    Liquidation Capture Strategy search space.

    Focuses on cascade detection thresholds.
    Runs in ALL regimes.
    """
    return {
        # Cascade detection
        "price_move_threshold": (0.02, 0.04),  # Min price move % (2-4%)
        "volume_spike_multiplier": (2.0, 4.0),  # Volume spike threshold
        # RSI thresholds (extreme values)
        "rsi_oversold_threshold": (15.0, 25.0),  # Long liquidation trigger
        "rsi_overbought_threshold": (75.0, 85.0),  # Short squeeze trigger
        # Pattern recognition
        "min_consecutive_moves": (3, 6),  # Min candles in same direction
        "min_wick_ratio": (1.2, 2.5),  # Min wick-to-body ratio
        # Risk/reward
        "rrr_target": (2.0, 4.0),  # Minimum RRR target
    }


def _vwap_scalping_space() -> SearchSpace:
    """
    VWAP Scalping Strategy search space.

    Focuses on VWAP deviation thresholds and MACD confirmation.
    Runs in ALL regimes (overlay strategy).
    """
    return {
        # VWAP deviation
        "sd_entry_threshold": (1.0, 3.0),  # Min SD for entry
        # NOTE: "sd_exit_threshold" was removed on 2026-07-28. VWAPScalping
        # has no exit-at-SD mechanism at all - it exits on the ATR stop or
        # target - so the parameter never reached the strategy and every
        # sampled value scored identically. It was a pure noise dimension.
        # ATR stop loss
        "atr_stop_multiplier": (1.0, 2.5),  # ATR multiplier for stop
        # NOTE: "rsi_oversold" and "rsi_overbought" were removed on
        # 2026-07-29, for the same reason "sd_exit_threshold" was. The
        # constructor accepts and stores them (vwap_scalping.py:265-274)
        # and the docstring already calls them RESERVED, but
        # generate_signals() only ever interpolates the RSI value into a
        # note string - no branch reads either threshold. Every sampled
        # value scored identically, so they were two more pure noise
        # dimensions in a six-dimensional space, and every trial spent on
        # them was still charged to the strategy's deflated Sharpe.
        # Confidence
        "min_confidence": (0.55, 0.75),
        # Cooldown
        "cooldown_minutes": (5, 15),  # Minutes between trades
    }


def _funding_arb_space() -> SearchSpace:
    """
    Funding Arbitrage Strategy search space.

    Focuses on funding rate thresholds and position sizing.
    Runs in ALL regimes (passive strategy).
    """
    return {
        # Funding rate thresholds
        "min_funding_rate": (0.0001, 0.001),  # 0.01% - 0.1% minimum
        # Position sizing
        "max_allocation_pct": (0.10, 0.30),  # 10-30% of account
        # Rebalance
        "rebalance_threshold": (0.01, 0.05),  # 1-5% delta threshold
        # Analysis window
        "lookback_hours": (4, 16),  # Hours of history
        # Confidence
        "min_confidence": (0.60, 0.85),
    }


def _momentum_scalping_space() -> SearchSpace:
    """
    Momentum Scalping Strategy search space.

    Focuses on EMA crossover parameters and ATR-based targets.
    Optimized for TRENDING regimes (1h timeframe).

    NOTE: atr_target_mult is coupled to atr_stop_mult by
    MIN_RRR_CONSTRAINTS["momentum_scalping"] - suggest_params() raises the
    effective lower bound to atr_stop_mult * min_rrr * RRR_FEASIBILITY_MARGIN
    so trials cannot land in the region where every signal is discarded
    (see check_param_feasibility).
    The declared range below is the union across all stop values.
    """
    return {
        # EMA periods
        "ema_fast": (5, 15),  # Fast EMA length
        "ema_slow": (15, 30),  # Slow EMA length
        # RSI filters
        "rsi_lower": (25.0, 40.0),  # RSI floor
        "rsi_upper": (60.0, 75.0),  # RSI ceiling
        # ATR risk management (RRR = target/stop is constant, see note above)
        "atr_stop_mult": (1.0, 2.5),  # Stop loss multiplier
        "atr_target_mult": (2.0, 4.5),  # Take profit multiplier
        # Volume
        "volume_threshold": (1.0, 1.8),  # Min volume multiplier
        # Confidence
        "min_confidence": (0.45, 0.65),
    }


def _orderbook_imbalance_space() -> SearchSpace:
    """
    Order Book Imbalance Strategy search space.

    Focuses on imbalance thresholds and detection parameters.
    Runs in ALL regimes (overlay strategy).
    """
    return {
        # Imbalance thresholds
        "imbalance_long_threshold": (0.55, 0.70),  # Long trigger
        "imbalance_short_threshold": (0.30, 0.45),  # Short trigger
        "strong_imbalance_threshold": (0.68, 0.80),  # High conviction
        # Detection
        "levels": (5, 20),  # Price levels to analyze
        "min_order_density": (3, 10),  # Min orders on winning side
        # ATR risk management (RRR = target/stop is constant; coupled by
        # MIN_RRR_CONSTRAINTS["orderbook_imbalance"] in suggest_params)
        "atr_stop_mult": (0.5, 1.0),  # Tight stop for fast trades
        "atr_target_mult": (1.0, 2.5),  # Quick target
        # Confidence
        "min_confidence": (0.50, 0.70),
    }


# Single source of truth for which strategies are tunable.  Both
# get_search_space() and list_strategies() derive from this map, so a new
# strategy becomes visible to the tuner by adding exactly one entry.
# Declared after the builders so the references resolve at import time.
SEARCH_SPACE_BUILDERS: Dict[str, Callable[[], SearchSpace]] = {
    "mean_reversion": _mean_reversion_space,
    "ma_crossover": _ma_crossover_space,
    "grid_trading": _grid_trading_space,
    "liquidation_capture": _liquidation_capture_space,
    "vwap_scalping": _vwap_scalping_space,
    "vwap_pullback": _vwap_pullback_space,
    "funding_arb": _funding_arb_space,
    "momentum_scalping": _momentum_scalping_space,
    "orderbook_imbalance": _orderbook_imbalance_space,
}


# ---------------------------------------------------------------------------
# Parameter Type Metadata (for Optuna trial suggestions)
# ---------------------------------------------------------------------------

# Maps strategy -> param_name -> ("int" | "float")
PARAMETER_TYPES: Dict[str, Dict[str, str]] = {
    "mean_reversion": {
        # No min_confidence: inert for this strategy, see
        # _mean_reversion_space() for why it was removed.
        "rsi_oversold": "float",
        "rsi_overbought": "float",
        "bb_std_dev": "float",
        "atr_stop_multiplier": "float",
    },
    "ma_crossover": {
        "fast_ma_period": "int",
        "slow_ma_period": "int",
        "max_entry_bars": "int",
        "pullback_range_min": "float",
        "pullback_range_max": "float",
        "volume_threshold": "float",
        "atr_stop_multiplier": "float",
        "min_confidence": "float",
    },
    "grid_trading": {
        "grid_levels": "int",
        "grid_spacing_atr_multiplier": "float",
        "emergency_stop_loss_pct": "float",
        "adx_regime_threshold": "float",
        "min_confidence": "float",
    },
    "liquidation_capture": {
        "price_move_threshold": "float",
        "volume_spike_multiplier": "float",
        "rsi_oversold_threshold": "float",
        "rsi_overbought_threshold": "float",
        "min_consecutive_moves": "int",
        "min_wick_ratio": "float",
        "rrr_target": "float",
    },
    "vwap_scalping": {
        "sd_entry_threshold": "float",
        "atr_stop_multiplier": "float",
        "rsi_oversold": "float",
        "rsi_overbought": "float",
        "min_confidence": "float",
        "cooldown_minutes": "int",
    },
    "vwap_pullback": {
        "band_sd": "float",
        "extension_min_sd": "float",
        "atr_stop_buffer": "float",
        "tp_rr": "float",
        "time_exit_hours": "int",
        "rvol_min": "float",
        "cooldown_hours": "float",
    },
    "funding_arb": {
        "min_funding_rate": "float",
        "max_allocation_pct": "float",
        "rebalance_threshold": "float",
        "lookback_hours": "int",
        "min_confidence": "float",
    },
    "momentum_scalping": {
        "ema_fast": "int",
        "ema_slow": "int",
        "rsi_lower": "float",
        "rsi_upper": "float",
        "atr_stop_mult": "float",
        "atr_target_mult": "float",
        "volume_threshold": "float",
        "min_confidence": "float",
    },
    "orderbook_imbalance": {
        "imbalance_long_threshold": "float",
        "imbalance_short_threshold": "float",
        "strong_imbalance_threshold": "float",
        "levels": "int",
        "min_order_density": "int",
        "atr_stop_mult": "float",
        "atr_target_mult": "float",
        "min_confidence": "float",
    },
}


# Minimum separation imposed between the two halves of an ordered pair of
# CONTINUOUS parameters. Sampling max exactly equal to min would leave a
# razor-thin band that no real price ever lands inside; 0.2% keeps the
# pullback band meaningful. Integer pairs simply use a gap of 1.
ORDERED_PAIR_FLOAT_GAP = 0.002


def _ordered_pair_floor(
    strategy_name: str,
    param_name: str,
    param_type: str,
    sampled: Dict[str, Any],
) -> Optional[float]:
    """Lowest value an upper-half parameter may take, given its partner.

    Args:
        strategy_name: Strategy name.
        param_name: The parameter about to be suggested.
        param_type: "int" or "float".
        sampled: Parameters already suggested in this trial.

    Returns:
        The coupled lower bound, or None when the parameter is not the
        upper half of a constraint (or its partner is not in the space).
    """
    for lower_name, upper_name, _ in ORDERED_PAIR_CONSTRAINTS.get(strategy_name, ()):
        if upper_name != param_name:
            continue
        lower_value: Optional[float] = sampled.get(lower_name)
        if lower_value is None:
            continue
        gap = 1 if param_type == "int" else ORDERED_PAIR_FLOAT_GAP
        return lower_value + gap
    return None


def get_param_type(strategy_name: str, param_name: str) -> str:
    """
    Get the Optuna suggestion type for a parameter.

    Args:
        strategy_name: Strategy name
        param_name: Parameter name

    Returns:
        "int" or "float"
    """
    types = PARAMETER_TYPES.get(strategy_name, {})
    return types.get(param_name, "float")


def suggest_params(trial: Any, strategy_name: str) -> Dict[str, Any]:
    """
    Suggest parameters from an Optuna trial for a given strategy.

    This is a convenience function that reads the search space and
    suggests parameters with the correct types.

    For strategies listed in MIN_RRR_CONSTRAINTS the lower bound of
    atr_target_mult is raised to atr_stop_mult * min_rrr (plus
    RRR_FEASIBILITY_MARGIN), so no trial is spent on a combination whose
    signals would all be discarded before execution. The result is checked
    once more before being returned.

    Two more couplings are applied the same way:

    - ORDERED_PAIR_CONSTRAINTS raises the lower bound of the upper half of
      a pair (e.g. slow_ma_period > fast_ma_period), so an inverted pair -
      which would make a gate unreachable - is never sampled.
    - HISTORY_BUDGET_CONSTRAINTS lowers the upper bound of the longest
      indicator lookback to the engine's rolling history budget.

    Both rely on the search space's dict ordering: the lower half of every
    pair is declared before its upper half.

    Args:
        trial: Optuna trial object
        strategy_name: Strategy name

    Returns:
        Dictionary of suggested parameters

    Raises:
        InfeasibleParamsError: If the constrained region is empty (the
            declared search space cannot satisfy the strategy's minimum
            RRR, an ordered pair, or the history budget).
    """
    space = get_search_space(strategy_name)
    min_rrr: Optional[float] = MIN_RRR_CONSTRAINTS.get(strategy_name)
    params: Dict[str, Any] = {}

    for param_name, param_range in space.items():
        param_type = get_param_type(strategy_name, param_name)

        if isinstance(param_range, list):
            # Categorical parameter
            params[param_name] = trial.suggest_categorical(param_name, param_range)
            continue

        low, high = param_range

        # Couple the take-profit multiplier to the stop multiplier so the
        # implied (constant) reward/risk always clears the strategy's gate.
        if (
            min_rrr is not None
            and param_name == "atr_target_mult"
            and params.get("atr_stop_mult", 0) > 0
        ):
            feasible_low = params["atr_stop_mult"] * min_rrr * RRR_FEASIBILITY_MARGIN
            if feasible_low > high:
                raise InfeasibleParamsError(
                    f"Infeasible parameters for {strategy_name}: "
                    f"atr_stop_mult={params['atr_stop_mult']:.3f} needs "
                    f"atr_target_mult >= {feasible_low:.3f} for RRR "
                    f"{min_rrr:.2f}, but the search space caps it at {high}"
                )
            low = max(low, feasible_low)

        # Keep the upper half of an ordered pair strictly above its
        # partner (fast/slow MA, pullback band edges, entry window).
        pair_low = _ordered_pair_floor(strategy_name, param_name, param_type, params)
        if pair_low is not None:
            if pair_low > high:
                raise InfeasibleParamsError(
                    f"Infeasible parameters for {strategy_name}: "
                    f"{param_name} must exceed its already-sampled "
                    f"partner (>= {pair_low}), but the search space caps "
                    f"it at {high}"
                )
            low = max(low, pair_low)

        # Cap the longest indicator lookback at the engine's rolling
        # history budget - beyond it the strategy validates away every bar.
        budget = HISTORY_BUDGET_CONSTRAINTS.get(strategy_name)
        if budget is not None and budget[0] == param_name:
            lookback = get_history_lookback()
            budget_high = lookback - budget[1]
            if budget_high < low:
                raise InfeasibleParamsError(
                    f"Infeasible parameters for {strategy_name}: "
                    f"{param_name} needs to be <= {budget_high} to fit the "
                    f"{lookback}-candle history budget "
                    f"(BACKTEST_HISTORY_LOOKBACK), but its lower bound is "
                    f"{low}. Raise BACKTEST_HISTORY_LOOKBACK."
                )
            high = min(high, budget_high)

        if param_type == "int":
            params[param_name] = trial.suggest_int(param_name, int(low), int(high))
        else:
            params[param_name] = trial.suggest_float(param_name, low, high)

    validate_params(strategy_name, params)
    return params
