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

from typing import Dict, Any, List

# Type alias for search space definitions
SearchSpace = Dict[str, Any]


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
    """
    return {
        # EMA periods
        "ema_fast": (5, 15),                     # Fast EMA length
        "ema_slow": (15, 30),                    # Slow EMA length
        # RSI filters
        "rsi_lower": (25.0, 40.0),               # RSI floor
        "rsi_upper": (60.0, 75.0),               # RSI ceiling
        # ATR risk management
        "atr_stop_mult": (1.0, 2.5),             # Stop loss multiplier
        "atr_target_mult": (2.0, 4.0),           # Take profit multiplier
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
        # ATR risk management
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

    Args:
        trial: Optuna trial object
        strategy_name: Strategy name

    Returns:
        Dictionary of suggested parameters
    """
    space = get_search_space(strategy_name)
    params = {}

    for param_name, param_range in space.items():
        param_type = get_param_type(strategy_name, param_name)

        if isinstance(param_range, list):
            # Categorical parameter
            params[param_name] = trial.suggest_categorical(param_name, param_range)
        elif param_type == "int":
            low, high = param_range
            params[param_name] = trial.suggest_int(param_name, int(low), int(high))
        else:
            low, high = param_range
            params[param_name] = trial.suggest_float(param_name, low, high)

    return params
