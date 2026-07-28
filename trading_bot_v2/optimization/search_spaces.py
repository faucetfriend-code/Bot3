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

from typing import Dict, Any, List, Optional

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
    "momentum_scalping": 1.5,      # momentum_scalping.py min_rrr default
    "orderbook_imbalance": 1.5,    # orderbook_imbalance.py hardcoded gate
}

# Headroom applied to the coupled lower bound in suggest_params. The realised
# RRR is recomputed from prices at signal time, where float noise turns a
# nominal 1.5 into 1.4999999999999998 and fails a `>=` gate, so suggestions
# must sit strictly inside the feasible region rather than on its edge.
RRR_FEASIBILITY_MARGIN = 1.02


class InfeasibleParamsError(ValueError):
    """Raised when a parameter set can never produce a tradeable signal."""


def check_param_feasibility(
    strategy_name: str, params: Dict[str, Any]
) -> List[str]:
    """
    Check a parameter set for combinations that can never trade.

    Args:
        strategy_name: Strategy name (e.g. "momentum_scalping")
        params: Candidate parameter values.

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

    return reasons


def validate_params(strategy_name: str, params: Dict[str, Any]) -> None:
    """
    Raise InfeasibleParamsError if params can never produce a valid signal.

    Args:
        strategy_name: Strategy name.
        params: Candidate parameter values.

    Raises:
        InfeasibleParamsError: With all failing reasons in the message.
    """
    reasons = check_param_feasibility(strategy_name, params)
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
    strategy_map = {
        "mean_reversion": _mean_reversion_space,
        "ma_crossover": _ma_crossover_space,
        "grid_trading": _grid_trading_space,
        "liquidation_capture": _liquidation_capture_space,
        "vwap_scalping": _vwap_scalping_space,
        "funding_arb": _funding_arb_space,
        "momentum_scalping": _momentum_scalping_space,
        "orderbook_imbalance": _orderbook_imbalance_space,
    }

    if strategy_name not in strategy_map:
        available = ", ".join(strategy_map.keys())
        raise ValueError(
            f"Unknown strategy: '{strategy_name}'. Available: {available}"
        )

    return strategy_map[strategy_name]()


def list_strategies() -> List[str]:
    """Return list of all available strategy names for optimization."""
    return [
        "mean_reversion",
        "ma_crossover",
        "grid_trading",
        "liquidation_capture",
        "vwap_scalping",
        "funding_arb",
        "momentum_scalping",
        "orderbook_imbalance",
    ]


# ---------------------------------------------------------------------------
# Strategy Search Spaces
# ---------------------------------------------------------------------------


def _mean_reversion_space() -> SearchSpace:
    """
    Mean Reversion Strategy search space.

    Focuses on RSI thresholds, Bollinger Bands, and ATR-based stops.
    Optimized for RANGING_CALM regime.
    """
    return {
        # RSI parameters
        "rsi_oversold": (25.0, 45.0),          # Lower = more aggressive oversold
        "rsi_overbought": (55.0, 75.0),        # Higher = more aggressive overbought
        # Bollinger Bands
        "bb_std_dev": (1.5, 3.0),              # Wider = fewer but stronger signals
        # Stop loss
        "atr_stop_multiplier": (1.5, 3.0),     # Tighter = more stops, wider = more room
        # Confidence threshold
        "min_confidence": (0.35, 0.65),        # Lower = more signals, higher = fewer
    }


def _ma_crossover_space() -> SearchSpace:
    """
    MA Crossover Strategy search space.

    Focuses on MA periods and pullback parameters.
    Optimized for TRENDING regimes.
    """
    return {
        # Moving average periods
        "fast_ma_period": (10, 30),             # Fast MA length
        "slow_ma_period": (40, 80),             # Slow MA length
        # Pullback entry
        "pullback_range_min": (0.005, 0.02),    # Minimum pullback % (0.5-2%)
        "pullback_range_max": (0.02, 0.05),     # Maximum pullback % (2-5%)
        # Volume confirmation
        "volume_threshold": (1.0, 2.0),         # Volume multiplier for confirmation
        # Stop loss
        "atr_stop_multiplier": (1.5, 3.5),     # ATR multiplier for stop
    }


def _grid_trading_space() -> SearchSpace:
    """
    Grid Trading Strategy search space.

    Focuses on grid levels, spacing, and risk parameters.
    Optimized for RANGING_VOLATILE regime.
    """
    return {
        # Grid structure
        "grid_levels": (4, 12),                 # Number of grid levels (int)
        "grid_spacing_atr_multiplier": (0.3, 0.8),  # ATR multiplier for spacing
        # Risk management
        "emergency_stop_loss_pct": (0.03, 0.08),    # Emergency stop threshold
        "adx_regime_threshold": (15.0, 25.0),       # ADX threshold for regime change
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
        "price_move_threshold": (0.02, 0.04),    # Min price move % (2-4%)
        "volume_spike_multiplier": (2.0, 4.0),   # Volume spike threshold
        # RSI thresholds (extreme values)
        "rsi_oversold_threshold": (15.0, 25.0),  # Long liquidation trigger
        "rsi_overbought_threshold": (75.0, 85.0), # Short squeeze trigger
        # Pattern recognition
        "min_consecutive_moves": (3, 6),          # Min candles in same direction
        "min_wick_ratio": (1.2, 2.5),             # Min wick-to-body ratio
        # Risk/reward
        "rrr_target": (2.0, 4.0),                # Minimum RRR target
    }


def _vwap_scalping_space() -> SearchSpace:
    """
    VWAP Scalping Strategy search space.

    Focuses on VWAP deviation thresholds and MACD confirmation.
    Runs in ALL regimes (overlay strategy).
    """
    return {
        # VWAP deviation
        "sd_entry_threshold": (1.0, 3.0),        # Min SD for entry
        "sd_exit_threshold": (0.5, 1.5),         # Exit at this SD
        # ATR stop loss
        "atr_stop_multiplier": (1.0, 2.5),       # ATR multiplier for stop
        # RSI confirmation
        "rsi_oversold": (30.0, 40.0),            # RSI buy gate
        "rsi_overbought": (60.0, 70.0),          # RSI sell gate
        # Confidence
        "min_confidence": (0.55, 0.75),
        # Cooldown
        "cooldown_minutes": (5, 15),             # Minutes between trades
    }


def _funding_arb_space() -> SearchSpace:
    """
    Funding Arbitrage Strategy search space.

    Focuses on funding rate thresholds and position sizing.
    Runs in ALL regimes (passive strategy).
    """
    return {
        # Funding rate thresholds
        "min_funding_rate": (0.0001, 0.001),     # 0.01% - 0.1% minimum
        # Position sizing
        "max_allocation_pct": (0.10, 0.30),      # 10-30% of account
        # Rebalance
        "rebalance_threshold": (0.01, 0.05),     # 1-5% delta threshold
        # Analysis window
        "lookback_hours": (4, 16),               # Hours of history
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
        "ema_fast": (5, 15),                     # Fast EMA length
        "ema_slow": (15, 30),                    # Slow EMA length
        # RSI filters
        "rsi_lower": (25.0, 40.0),               # RSI floor
        "rsi_upper": (60.0, 75.0),               # RSI ceiling
        # ATR risk management (RRR = target/stop is constant, see note above)
        "atr_stop_mult": (1.0, 2.5),             # Stop loss multiplier
        "atr_target_mult": (2.0, 4.5),           # Take profit multiplier
        # Volume
        "volume_threshold": (1.0, 1.8),          # Min volume multiplier
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
        "imbalance_long_threshold": (0.55, 0.70),   # Long trigger
        "imbalance_short_threshold": (0.30, 0.45),   # Short trigger
        "strong_imbalance_threshold": (0.68, 0.80),  # High conviction
        # Detection
        "levels": (5, 20),                           # Price levels to analyze
        "min_order_density": (3, 10),                # Min orders on winning side
        # ATR risk management (RRR = target/stop is constant; coupled by
        # MIN_RRR_CONSTRAINTS["orderbook_imbalance"] in suggest_params)
        "atr_stop_mult": (0.5, 1.0),                 # Tight stop for fast trades
        "atr_target_mult": (1.0, 2.5),               # Quick target
        # Confidence
        "min_confidence": (0.50, 0.70),
    }


# ---------------------------------------------------------------------------
# Parameter Type Metadata (for Optuna trial suggestions)
# ---------------------------------------------------------------------------

# Maps strategy -> param_name -> ("int" | "float")
PARAMETER_TYPES: Dict[str, Dict[str, str]] = {
    "mean_reversion": {
        "rsi_oversold": "float",
        "rsi_overbought": "float",
        "bb_std_dev": "float",
        "atr_stop_multiplier": "float",
        "min_confidence": "float",
    },
    "ma_crossover": {
        "fast_ma_period": "int",
        "slow_ma_period": "int",
        "pullback_range_min": "float",
        "pullback_range_max": "float",
        "volume_threshold": "float",
        "atr_stop_multiplier": "float",
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
        "sd_exit_threshold": "float",
        "atr_stop_multiplier": "float",
        "rsi_oversold": "float",
        "rsi_overbought": "float",
        "min_confidence": "float",
        "cooldown_minutes": "int",
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

    Args:
        trial: Optuna trial object
        strategy_name: Strategy name

    Returns:
        Dictionary of suggested parameters

    Raises:
        InfeasibleParamsError: If the constrained region is empty (the
            declared search space cannot satisfy the strategy's minimum RRR).
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
            feasible_low = (
                params["atr_stop_mult"] * min_rrr * RRR_FEASIBILITY_MARGIN
            )
            if feasible_low > high:
                raise InfeasibleParamsError(
                    f"Infeasible parameters for {strategy_name}: "
                    f"atr_stop_mult={params['atr_stop_mult']:.3f} needs "
                    f"atr_target_mult >= {feasible_low:.3f} for RRR "
                    f"{min_rrr:.2f}, but the search space caps it at {high}"
                )
            low = max(low, feasible_low)

        if param_type == "int":
            params[param_name] = trial.suggest_int(param_name, int(low), int(high))
        else:
            params[param_name] = trial.suggest_float(param_name, low, high)

    validate_params(strategy_name, params)
    return params
