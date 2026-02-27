#!/usr/bin/env python3
"""
Trading Strategy Profiles

Defines strategy configurations for different trading styles.
Each strategy has risk parameters, position sizing rules, and behavioral characteristics.

Strategies:
- CONSERVATIVE: Low risk, low leverage, longer holds
- BALANCED: Moderate risk/reward, standard parameters
- AGGRESSIVE: Higher risk, higher leverage, faster trades
- SCALPING: Very short-term, high frequency, tight stops
- SWING: Multi-day holds, trend following
- DAY_TRADE: Intraday only, no overnight positions
- GRID: Grid trading with multiple orders
- DCA: Dollar-cost averaging, buy dips
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

from config import TradingStrategy


@dataclass
class StrategyProfile:
    """
    Trading strategy profile with risk and behavioral parameters.
    """

    name: str
    strategy_type: TradingStrategy

    # Risk Parameters
    max_position_size: float = 10000.0  # Maximum position size in USD
    risk_per_trade: float = 0.02  # Risk per trade (0.0-1.0)
    max_leverage: int = 20  # Maximum leverage (5-50)
    max_daily_loss: float = 0.05  # Max daily loss before stopping (0.0-1.0)
    max_open_positions: int = 5  # Maximum concurrent positions

    # Position Sizing
    position_sizing_method: str = "fixed"  # "fixed", "risk_based", "kelly"
    base_position_size: float = 1000.0  # Base position size in USD
    scale_in_enabled: bool = False  # Allow scaling into positions
    scale_out_enabled: bool = True  # Allow scaling out of positions

    # Stop Loss / Take Profit
    use_stop_loss: bool = True
    stop_loss_pct: float = 0.02  # Stop loss as % of entry (0.02 = 2%)
    use_trailing_stop: bool = False
    trailing_stop_pct: float = 0.03
    use_take_profit: bool = True
    take_profit_pct: float = 0.04  # Take profit as % of entry
    risk_reward_ratio: float = 2.0  # Minimum risk/reward ratio

    # Time-Based Rules
    max_hold_time_hours: Optional[int] = None  # Max hold time (None = unlimited)
    trade_timeframe: str = "any"  # "any", "intraday", "swing", "scalp"
    allow_overnight: bool = True

    # Order Preferences
    preferred_order_type: str = "limit"  # "limit", "market", "post_only"
    use_post_only: bool = False
    slippage_tolerance: float = 0.001  # 0.1% slippage tolerance

    # Entry Rules
    min_signal_confidence: float = 0.6  # Minimum signal confidence (0.0-1.0)
    require_confirmation: bool = False  # Require multiple confirmations
    avoid_high_funding: bool = False  # Avoid high funding rates
    max_funding_rate: float = 0.01  # Max acceptable funding rate (hourly)

    # Market Conditions
    trade_in_consolidation: bool = True
    trade_in_trend: bool = True
    require_volume: bool = False
    min_volume_24h: float = 1000000.0  # Minimum 24h volume

    # Advanced
    hedge_enabled: bool = False  # Allow hedging positions
    martingale_enabled: bool = False  # Enable martingale (dangerous!)
    pyramid_enabled: bool = False  # Add to winning positions
    rebalance_enabled: bool = False  # Periodic portfolio rebalancing

    # Metadata
    description: str = ""
    tags: list = field(default_factory=list)


# ===========================
# STRATEGY DEFINITIONS
# ===========================

CONSERVATIVE_STRATEGY = StrategyProfile(
    name="Conservative",
    strategy_type=TradingStrategy.CONSERVATIVE,
    description="Low-risk strategy with tight stops and minimal leverage",

    # Risk Parameters
    max_position_size=5000.0,
    risk_per_trade=0.01,  # 1% risk per trade
    max_leverage=10,
    max_daily_loss=0.03,  # Stop after 3% daily loss
    max_open_positions=3,

    # Position Sizing
    position_sizing_method="risk_based",
    base_position_size=1000.0,
    scale_in_enabled=False,
    scale_out_enabled=True,

    # Stop Loss / Take Profit
    use_stop_loss=True,
    stop_loss_pct=0.015,  # 1.5% stop loss
    use_trailing_stop=True,
    trailing_stop_pct=0.02,
    use_take_profit=True,
    take_profit_pct=0.03,  # 3% take profit (2:1 R/R)
    risk_reward_ratio=2.0,

    # Time-Based Rules
    max_hold_time_hours=48,
    trade_timeframe="swing",
    allow_overnight=True,

    # Order Preferences
    preferred_order_type="limit",
    use_post_only=True,
    slippage_tolerance=0.0005,

    # Entry Rules
    min_signal_confidence=0.7,
    require_confirmation=True,
    avoid_high_funding=True,
    max_funding_rate=0.005,

    # Market Conditions
    trade_in_consolidation=False,
    trade_in_trend=True,
    require_volume=True,
    min_volume_24h=5000000.0,

    tags=["low-risk", "beginner-friendly", "capital-preservation"]
)


BALANCED_STRATEGY = StrategyProfile(
    name="Balanced",
    strategy_type=TradingStrategy.BALANCED,
    description="Standard strategy with moderate risk/reward balance",

    # Risk Parameters
    max_position_size=10000.0,
    risk_per_trade=0.02,  # 2% risk per trade
    max_leverage=20,
    max_daily_loss=0.05,
    max_open_positions=5,

    # Position Sizing
    position_sizing_method="risk_based",
    base_position_size=2000.0,
    scale_in_enabled=True,
    scale_out_enabled=True,

    # Stop Loss / Take Profit
    use_stop_loss=True,
    stop_loss_pct=0.02,
    use_trailing_stop=False,
    use_take_profit=True,
    take_profit_pct=0.04,
    risk_reward_ratio=2.0,

    # Time-Based Rules
    max_hold_time_hours=None,
    trade_timeframe="any",
    allow_overnight=True,

    # Order Preferences
    preferred_order_type="limit",
    use_post_only=False,
    slippage_tolerance=0.001,

    # Entry Rules
    min_signal_confidence=0.6,
    require_confirmation=False,
    avoid_high_funding=False,
    max_funding_rate=0.01,

    # Market Conditions
    trade_in_consolidation=True,
    trade_in_trend=True,
    require_volume=False,

    tags=["balanced", "all-rounder", "medium-risk"]
)


AGGRESSIVE_STRATEGY = StrategyProfile(
    name="Aggressive",
    strategy_type=TradingStrategy.AGGRESSIVE,
    description="High-risk, high-reward strategy with maximum leverage",

    # Risk Parameters
    max_position_size=25000.0,
    risk_per_trade=0.05,  # 5% risk per trade
    max_leverage=40,
    max_daily_loss=0.10,  # 10% daily loss tolerance
    max_open_positions=8,

    # Position Sizing
    position_sizing_method="fixed",
    base_position_size=5000.0,
    scale_in_enabled=True,
    scale_out_enabled=False,

    # Stop Loss / Take Profit
    use_stop_loss=True,
    stop_loss_pct=0.03,
    use_trailing_stop=False,
    use_take_profit=True,
    take_profit_pct=0.06,
    risk_reward_ratio=1.5,

    # Time-Based Rules
    max_hold_time_hours=12,
    trade_timeframe="any",
    allow_overnight=False,

    # Order Preferences
    preferred_order_type="market",
    use_post_only=False,
    slippage_tolerance=0.002,

    # Entry Rules
    min_signal_confidence=0.5,
    require_confirmation=False,
    avoid_high_funding=False,
    max_funding_rate=0.02,

    # Market Conditions
    trade_in_consolidation=True,
    trade_in_trend=True,
    require_volume=False,

    # Advanced
    pyramid_enabled=True,

    tags=["high-risk", "aggressive", "experienced-only"]
)


SCALPING_STRATEGY = StrategyProfile(
    name="Scalping",
    strategy_type=TradingStrategy.SCALPING,
    description="Ultra-short-term strategy targeting small price movements",

    # Risk Parameters
    max_position_size=15000.0,
    risk_per_trade=0.015,  # 1.5% risk per trade
    max_leverage=30,
    max_daily_loss=0.07,
    max_open_positions=10,

    # Position Sizing
    position_sizing_method="fixed",
    base_position_size=3000.0,
    scale_in_enabled=False,
    scale_out_enabled=True,

    # Stop Loss / Take Profit
    use_stop_loss=True,
    stop_loss_pct=0.005,  # 0.5% tight stop
    use_trailing_stop=False,
    use_take_profit=True,
    take_profit_pct=0.01,  # 1% quick profit
    risk_reward_ratio=1.5,

    # Time-Based Rules
    max_hold_time_hours=1,  # Maximum 1 hour hold
    trade_timeframe="scalp",
    allow_overnight=False,

    # Order Preferences
    preferred_order_type="limit",
    use_post_only=True,
    slippage_tolerance=0.0003,

    # Entry Rules
    min_signal_confidence=0.55,
    require_confirmation=False,
    avoid_high_funding=False,

    # Market Conditions
    trade_in_consolidation=True,
    trade_in_trend=False,
    require_volume=True,
    min_volume_24h=10000000.0,

    tags=["scalping", "high-frequency", "tight-stops"]
)


SWING_STRATEGY = StrategyProfile(
    name="Swing Trading",
    strategy_type=TradingStrategy.SWING,
    description="Multi-day trend-following strategy",

    # Risk Parameters
    max_position_size=20000.0,
    risk_per_trade=0.03,  # 3% risk per trade
    max_leverage=15,
    max_daily_loss=0.06,
    max_open_positions=4,

    # Position Sizing
    position_sizing_method="risk_based",
    base_position_size=5000.0,
    scale_in_enabled=True,
    scale_out_enabled=True,

    # Stop Loss / Take Profit
    use_stop_loss=True,
    stop_loss_pct=0.04,  # 4% wider stop
    use_trailing_stop=True,
    trailing_stop_pct=0.05,
    use_take_profit=True,
    take_profit_pct=0.10,  # 10% target
    risk_reward_ratio=2.5,

    # Time-Based Rules
    max_hold_time_hours=168,  # 7 days
    trade_timeframe="swing",
    allow_overnight=True,

    # Order Preferences
    preferred_order_type="limit",
    use_post_only=False,
    slippage_tolerance=0.001,

    # Entry Rules
    min_signal_confidence=0.65,
    require_confirmation=True,
    avoid_high_funding=True,
    max_funding_rate=0.008,

    # Market Conditions
    trade_in_consolidation=False,
    trade_in_trend=True,
    require_volume=True,
    min_volume_24h=3000000.0,

    tags=["swing", "trend-following", "multi-day"]
)


DAY_TRADE_STRATEGY = StrategyProfile(
    name="Day Trading",
    strategy_type=TradingStrategy.DAY_TRADE,
    description="Intraday strategy with no overnight exposure",

    # Risk Parameters
    max_position_size=12000.0,
    risk_per_trade=0.02,
    max_leverage=25,
    max_daily_loss=0.05,
    max_open_positions=6,

    # Position Sizing
    position_sizing_method="risk_based",
    base_position_size=3000.0,
    scale_in_enabled=True,
    scale_out_enabled=True,

    # Stop Loss / Take Profit
    use_stop_loss=True,
    stop_loss_pct=0.02,
    use_trailing_stop=True,
    trailing_stop_pct=0.025,
    use_take_profit=True,
    take_profit_pct=0.04,
    risk_reward_ratio=2.0,

    # Time-Based Rules
    max_hold_time_hours=8,
    trade_timeframe="intraday",
    allow_overnight=False,

    # Order Preferences
    preferred_order_type="limit",
    use_post_only=False,
    slippage_tolerance=0.001,

    # Entry Rules
    min_signal_confidence=0.6,
    require_confirmation=False,
    avoid_high_funding=False,

    # Market Conditions
    trade_in_consolidation=True,
    trade_in_trend=True,
    require_volume=True,
    min_volume_24h=5000000.0,

    tags=["day-trading", "intraday", "no-overnight"]
)


GRID_STRATEGY = StrategyProfile(
    name="Grid Trading",
    strategy_type=TradingStrategy.GRID,
    description="Grid trading with multiple buy/sell levels",

    # Risk Parameters
    max_position_size=30000.0,
    risk_per_trade=0.01,
    max_leverage=15,
    max_daily_loss=0.08,
    max_open_positions=15,  # Multiple grid levels

    # Position Sizing
    position_sizing_method="fixed",
    base_position_size=1500.0,  # Smaller per-level size
    scale_in_enabled=True,
    scale_out_enabled=True,

    # Stop Loss / Take Profit
    use_stop_loss=False,  # Grid doesn't use traditional stops
    use_take_profit=True,
    take_profit_pct=0.02,  # Tight profit per level

    # Time-Based Rules
    max_hold_time_hours=None,
    trade_timeframe="any",
    allow_overnight=True,

    # Order Preferences
    preferred_order_type="limit",
    use_post_only=True,
    slippage_tolerance=0.0005,

    # Entry Rules
    min_signal_confidence=0.5,
    require_confirmation=False,
    avoid_high_funding=True,
    max_funding_rate=0.006,

    # Market Conditions
    trade_in_consolidation=True,  # Grid works best in ranges
    trade_in_trend=False,
    require_volume=False,

    # Advanced
    rebalance_enabled=True,

    tags=["grid", "range-bound", "multiple-orders"]
)


DCA_STRATEGY = StrategyProfile(
    name="Dollar-Cost Averaging",
    strategy_type=TradingStrategy.DCA,
    description="Systematic buying on dips with averaging down",

    # Risk Parameters
    max_position_size=50000.0,
    risk_per_trade=0.02,
    max_leverage=10,  # Conservative leverage
    max_daily_loss=0.10,
    max_open_positions=3,

    # Position Sizing
    position_sizing_method="fixed",
    base_position_size=2500.0,
    scale_in_enabled=True,
    scale_out_enabled=False,

    # Stop Loss / Take Profit
    use_stop_loss=False,  # DCA doesn't use stops
    use_take_profit=True,
    take_profit_pct=0.15,  # Higher target
    risk_reward_ratio=3.0,

    # Time-Based Rules
    max_hold_time_hours=None,  # Long-term holds
    trade_timeframe="swing",
    allow_overnight=True,

    # Order Preferences
    preferred_order_type="limit",
    use_post_only=True,
    slippage_tolerance=0.001,

    # Entry Rules
    min_signal_confidence=0.5,
    require_confirmation=False,
    avoid_high_funding=True,
    max_funding_rate=0.005,

    # Market Conditions
    trade_in_consolidation=True,
    trade_in_trend=False,  # Buy dips, not breakouts
    require_volume=False,

    # Advanced
    # ⚠️ WARNING: Martingale DISABLED by default - it can cause catastrophic losses!
    # Only enable if you fully understand the risks of averaging down
    martingale_enabled=False,  # Disabled for safety - averaging down is dangerous

    tags=["dca", "buy-the-dip", "long-term"]
)


# ===========================
# STRATEGY REGISTRY
# ===========================

STRATEGY_PROFILES: Dict[TradingStrategy, StrategyProfile] = {
    TradingStrategy.CONSERVATIVE: CONSERVATIVE_STRATEGY,
    TradingStrategy.BALANCED: BALANCED_STRATEGY,
    TradingStrategy.AGGRESSIVE: AGGRESSIVE_STRATEGY,
    TradingStrategy.SCALPING: SCALPING_STRATEGY,
    TradingStrategy.SWING: SWING_STRATEGY,
    TradingStrategy.DAY_TRADE: DAY_TRADE_STRATEGY,
    TradingStrategy.GRID: GRID_STRATEGY,
    TradingStrategy.DCA: DCA_STRATEGY,
}


def get_strategy_profile(strategy: TradingStrategy) -> StrategyProfile:
    """
    Get strategy profile by type.

    Args:
        strategy: Trading strategy enum

    Returns:
        StrategyProfile instance

    Raises:
        ValueError: If strategy not found
    """
    if strategy not in STRATEGY_PROFILES:
        raise ValueError(f"Unknown strategy: {strategy}")
    return STRATEGY_PROFILES[strategy]


def list_strategies() -> list[TradingStrategy]:
    """
    List all available strategies.

    Returns:
        List of strategy enums
    """
    return list(STRATEGY_PROFILES.keys())


def compare_strategies(*strategies: TradingStrategy) -> Dict[str, Any]:
    """
    Compare multiple strategies side by side.

    Args:
        *strategies: Strategy enums to compare

    Returns:
        Comparison dictionary
    """
    comparison = {}
    for strategy in strategies:
        profile = get_strategy_profile(strategy)
        comparison[strategy.value] = {
            "risk_per_trade": profile.risk_per_trade,
            "max_leverage": profile.max_leverage,
            "max_position_size": profile.max_position_size,
            "stop_loss_pct": profile.stop_loss_pct,
            "take_profit_pct": profile.take_profit_pct,
            "max_hold_time_hours": profile.max_hold_time_hours,
            "allow_overnight": profile.allow_overnight,
        }
    return comparison


if __name__ == "__main__":
    # Example usage
    print("Available Trading Strategies:")
    print("=" * 60)

    for strategy_type in list_strategies():
        profile = get_strategy_profile(strategy_type)
        print(f"\n{profile.name} ({strategy_type.value})")
        print(f"  Description: {profile.description}")
        print(f"  Risk/Trade: {profile.risk_per_trade*100:.1f}%")
        print(f"  Max Leverage: {profile.max_leverage}x")
        print(f"  Stop Loss: {profile.stop_loss_pct*100:.1f}%")
        print(f"  Take Profit: {profile.take_profit_pct*100:.1f}%")
        print(f"  Tags: {', '.join(profile.tags)}")

    print("\n" + "=" * 60)
    print("\nStrategy Comparison (Conservative vs Aggressive):")
    print("=" * 60)

    comparison = compare_strategies(
        TradingStrategy.CONSERVATIVE,
        TradingStrategy.AGGRESSIVE
    )

    import json
    print(json.dumps(comparison, indent=2))
