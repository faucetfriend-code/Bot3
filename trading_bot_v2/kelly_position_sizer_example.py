"""
Kelly Position Sizer - Usage Examples

This demonstrates how to integrate the Kelly Criterion position sizer
with the trading bot for optimal position sizing based on historical performance.
"""

from .kelly_position_sizer import KellyPositionSizer
from .database import DatabaseManager
from .models import Signal, OrderSide
from .config import StrategyType, AssetClass, TradeQuality, MarketState


def example_basic_usage():
    """Basic usage example with a trading signal."""
    print("=" * 80)
    print("EXAMPLE 1: Basic Kelly Position Sizing")
    print("=" * 80)

    # Initialize database and Kelly sizer
    db = DatabaseManager()
    kelly_sizer = KellyPositionSizer(
        db=db,
        kelly_fraction=0.5,  # Half Kelly (conservative)
        min_trades=50,  # Need 50+ trades before using Kelly
    )

    # Create a sample trading signal
    signal = Signal(
        strategy=StrategyType.TREND_FOLLOWING,
        asset="BTC-PERP",
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        entry_price=50000.0,
        stop_loss=49000.0,  # 2% stop loss
        take_profit=53000.0,  # 6% take profit (3:1 RRR)
        confidence=0.8,
        quality=TradeQuality.HIGH_CONVICTION,
        market_state=MarketState.TREND,
    )

    # Calculate position size
    account_balance = 10000.0  # $10,000 account
    quantity = kelly_sizer.calculate_position_size(signal, account_balance)

    print(f"\nSignal Details:")
    print(f"  Strategy: {signal.strategy.value}")
    print(f"  Entry: ${signal.entry_price:,.2f}")
    print(f"  Stop Loss: ${signal.stop_loss:,.2f}")
    print(f"  Stop Distance: {signal.stop_distance_pct:.2%}")
    print(f"\nAccount Balance: ${account_balance:,.2f}")
    print(f"Calculated Position Size: {quantity:.4f} contracts")
    print(f"Position Value: ${quantity * signal.entry_price:,.2f}")
    print()


def example_different_strategies():
    """Compare position sizing across different strategies."""
    print("=" * 80)
    print("EXAMPLE 2: Position Sizing Across Different Strategies")
    print("=" * 80)

    db = DatabaseManager()
    kelly_sizer = KellyPositionSizer(db=db, kelly_fraction=0.5)

    account_balance = 10000.0

    strategies_to_test = [
        (StrategyType.TREND_FOLLOWING, 49000.0, "Trend Following"),
        (StrategyType.MEAN_REVERSION, 49500.0, "Mean Reversion"),
        (StrategyType.GRID_TRADING, 49800.0, "Grid Trading"),
        (StrategyType.LIQUIDATION_CAPTURE, 48500.0, "Liquidation Capture"),
    ]

    print(f"\nAccount Balance: ${account_balance:,.2f}\n")

    for strategy, stop_loss, name in strategies_to_test:
        signal = Signal(
            strategy=strategy,
            asset="BTC-PERP",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.BUY,
            entry_price=50000.0,
            stop_loss=stop_loss,
        )

        quantity = kelly_sizer.calculate_position_size(signal, account_balance)
        position_value = quantity * signal.entry_price
        risk_pct = (position_value / account_balance) * signal.stop_distance_pct

        print(f"{name}:")
        print(f"  Stop Distance: {signal.stop_distance_pct:.2%}")
        print(f"  Position Size: {quantity:.4f} contracts")
        print(f"  Position Value: ${position_value:,.2f}")
        print(f"  Account Risk: {risk_pct:.2%}")
        print()


def example_kelly_fraction_adjustment():
    """Demonstrate dynamic Kelly fraction adjustment based on performance."""
    print("=" * 80)
    print("EXAMPLE 3: Dynamic Kelly Fraction Adjustment")
    print("=" * 80)

    db = DatabaseManager()
    kelly_sizer = KellyPositionSizer(db=db, kelly_fraction=0.5)

    signal = Signal(
        strategy=StrategyType.TREND_FOLLOWING,
        asset="BTC-PERP",
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        entry_price=50000.0,
        stop_loss=49000.0,
    )

    account_balance = 10000.0

    # Test different Kelly fractions
    fractions = [
        (0.25, "Quarter Kelly (Very Conservative)"),
        (0.33, "Third Kelly (Conservative)"),
        (0.5, "Half Kelly (Balanced)"),
        (1.0, "Full Kelly (Aggressive - NOT RECOMMENDED)"),
    ]

    print(f"\nAccount Balance: ${account_balance:,.2f}")
    print(f"Entry: ${signal.entry_price:,.2f}, Stop: ${signal.stop_loss:,.2f}\n")

    for fraction, description in fractions:
        kelly_sizer.update_kelly_fraction(fraction)
        quantity = kelly_sizer.calculate_position_size(signal, account_balance)
        position_value = quantity * signal.entry_price

        print(f"{description} (fraction={fraction}):")
        print(f"  Position Size: {quantity:.4f} contracts")
        print(f"  Position Value: ${position_value:,.2f}")
        print(f"  % of Account: {(position_value / account_balance) * 100:.2f}%")
        print()


def example_strategy_performance_review():
    """Review strategy performance and get recommendations."""
    print("=" * 80)
    print("EXAMPLE 4: Strategy Performance Review")
    print("=" * 80)

    db = DatabaseManager()
    kelly_sizer = KellyPositionSizer(db=db)

    strategies = [
        StrategyType.TREND_FOLLOWING,
        StrategyType.MEAN_REVERSION,
        StrategyType.LIQUIDATION_CAPTURE,
    ]

    print("\nStrategy Performance Summary:\n")

    for strategy in strategies:
        # Get performance stats
        stats = kelly_sizer.get_strategy_stats(strategy)

        # Get recommended Kelly fraction
        recommended_fraction = kelly_sizer.get_recommended_kelly_fraction(strategy)

        print(f"{strategy.value}:")
        print(f"  Total Trades: {stats['total_trades']}")

        if stats["total_trades"] >= 50:
            print(f"  Win Rate: {stats['win_rate']:.2%}")
            print(f"  Avg Win: ${stats['avg_win']:.2f}")
            print(f"  Avg Loss: ${stats['avg_loss']:.2f}")

            # Calculate profit factor
            if stats["avg_loss"] > 0:
                profit_factor = (stats["win_rate"] * stats["avg_win"]) / (
                    (1 - stats["win_rate"]) * stats["avg_loss"]
                )
                print(f"  Profit Factor: {profit_factor:.2f}")

            print(f"  Recommended Kelly Fraction: {recommended_fraction}")

            # Interpretation
            if recommended_fraction >= 0.5:
                print(f"  [+] Strong performance - use standard sizing")
            elif recommended_fraction >= 0.33:
                print(f"  [!] Moderate performance - use conservative sizing")
            else:
                print(f"  [-] Weak performance - use very conservative sizing")
        else:
            print(f"  [.] Insufficient data - using fallback sizing")

        print()


def example_integration_with_trading_bot():
    """Example of integrating Kelly sizer into trading bot workflow."""
    print("=" * 80)
    print("EXAMPLE 5: Integration with Trading Bot")
    print("=" * 80)

    print("""
Integration Steps:

1. Initialize Kelly Sizer in TradingBot.__init__():

    def __init__(self, ...):
        self.db = DatabaseManager()
        self.kelly_sizer = KellyPositionSizer(
            db=self.db,
            kelly_fraction=0.5,  # Start conservative
            min_trades=50
        )

2. Use in signal processing:

    def process_signal(self, signal: Signal):
        # Get current account balance
        account_balance = self.get_account_balance()

        # Calculate position size using Kelly
        quantity = self.kelly_sizer.calculate_position_size(
            signal=signal,
            account_balance=account_balance,
            account_id=self.account_id
        )

        # Update signal with calculated quantity
        signal.quantity = quantity

        # Execute trade with Kelly-optimized sizing
        if quantity >= 1.0:
            self.execute_trade(signal)

3. Periodic performance review:

    def review_strategy_performance(self):
        for strategy in [StrategyType.TREND_FOLLOWING, ...]:
            stats = self.kelly_sizer.get_strategy_stats(strategy)
            recommended_fraction = self.kelly_sizer.get_recommended_kelly_fraction(strategy)

            # Log performance
            logger.info(f"{strategy.value}: {stats['total_trades']} trades, "
                       f"{stats['win_rate']:.2%} win rate, "
                       f"recommended fraction: {recommended_fraction}")

4. Adaptive Kelly fraction:

    def adjust_kelly_fraction_based_on_performance(self):
        # Get overall account performance
        if self.consecutive_losses >= 3:
            # More conservative after losses
            self.kelly_sizer.update_kelly_fraction(0.25)
        elif self.win_rate >= 0.60 and self.profit_factor >= 2.0:
            # More aggressive when performing well
            self.kelly_sizer.update_kelly_fraction(0.5)

Benefits:
- Position sizing adapts to actual strategy performance
- New strategies start with conservative fallback sizing
- Winning strategies get larger allocations automatically
- Losing strategies get smaller allocations (or 1% minimum)
- Hard 10% cap prevents over-concentration
- Fractional Kelly (0.5x) provides safety margin
    """)


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("KELLY POSITION SIZER - USAGE EXAMPLES")
    print("=" * 80 + "\n")

    try:
        example_basic_usage()
        example_different_strategies()
        example_kelly_fraction_adjustment()
        example_strategy_performance_review()
        example_integration_with_trading_bot()

        print("\n" + "=" * 80)
        print("All examples completed successfully!")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"\n[ERROR] Error running examples: {e}")
        import traceback

        traceback.print_exc()
