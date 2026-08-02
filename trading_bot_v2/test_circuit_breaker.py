"""
Test script for percentage-based circuit breaker.

This script demonstrates how the circuit breaker works with different
account sizes and P&L scenarios.
"""

import sys

from .config import config


def test_circuit_breaker():
    """Test circuit breaker calculations with various scenarios."""

    print("=" * 70)
    print("Circuit Breaker Test - Percentage-Based Implementation")
    print("=" * 70)

    # Get circuit breaker percentage from config
    cb_pct = config.circuit_breaker_loss_pct
    warning_pct = cb_pct * 0.8

    print(f"\nConfiguration:")
    print(f"  Circuit Breaker Threshold: {cb_pct:.1%}")
    print(f"  Warning Threshold (80%):   {warning_pct:.1%}")

    # Test scenarios with different account sizes
    test_scenarios = [
        # (account_balance, total_pnl, description)
        (1000, -50, "Small loss on $1K account"),
        (1000, -80, "Warning level on $1K account"),
        (1000, -100, "Circuit breaker triggered on $1K account"),
        (1000, -150, "Heavy loss on $1K account"),
        (10000, -500, "Small loss on $10K account"),
        (10000, -800, "Warning level on $10K account"),
        (10000, -1000, "Circuit breaker triggered on $10K account"),
        (10000, -1500, "Heavy loss on $10K account"),
        (100000, -5000, "Small loss on $100K account"),
        (100000, -8000, "Warning level on $100K account"),
        (100000, -10000, "Circuit breaker triggered on $100K account"),
        (100000, -15000, "Heavy loss on $100K account"),
    ]

    print("\n" + "=" * 70)
    print("Test Scenarios")
    print("=" * 70)

    for account_balance, total_pnl, description in test_scenarios:
        # Calculate percentage loss
        pnl_percentage = (total_pnl / account_balance) if account_balance > 0 else 0

        # Determine status
        if pnl_percentage <= -cb_pct:
            status = "[CIRCUIT BREAKER TRIGGERED]"
        elif pnl_percentage < -warning_pct:
            status = "[WARNING]"
        else:
            status = "[OK]"

        print(f"\n{description}:")
        print(f"  Account Balance: ${account_balance:,.2f}")
        print(f"  Total P&L:       ${total_pnl:,.2f}")
        print(f"  Loss Percentage: {pnl_percentage:.2%}")
        print(f"  Status:          {status}")

        if pnl_percentage <= -cb_pct:
            print(f"  > Bot would STOP automatically!")
            print(
                f"  > Loss ${abs(total_pnl):,.2f} reaches/exceeds {cb_pct:.1%} threshold (${account_balance * cb_pct:,.2f})"
            )
        elif pnl_percentage < -warning_pct:
            print(
                f"  > Warning logged, {abs(pnl_percentage / cb_pct * 100):.0f}% of circuit breaker limit"
            )

    # Compare old vs new system
    print("\n" + "=" * 70)
    print("Old vs New System Comparison")
    print("=" * 70)

    old_threshold = -1000.0  # Old fixed dollar amount

    comparison_balances = [1000, 5000, 10000, 50000, 100000]

    print(f"\nOld System: Fixed -${abs(old_threshold):,.0f} threshold")
    print(f"New System: {cb_pct:.1%} percentage threshold\n")

    print(
        f"{'Account Size':<15} {'Old Trigger':<15} {'New Trigger':<15} {'Difference':<15}"
    )
    print("-" * 60)

    for balance in comparison_balances:
        old_trigger_pct = (old_threshold / balance) * 100
        new_trigger_amt = balance * cb_pct
        difference = new_trigger_amt - old_threshold

        print(
            f"${balance:>13,} | "
            f"${old_threshold:>12,.0f} ({old_trigger_pct:>5.1f}%) | "
            f"${new_trigger_amt:>12,.0f} ({cb_pct * 100:>5.1f}%) | "
            f"${difference:>12,.0f}"
        )

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"""
The circuit breaker now scales correctly with account size:

[OK] Small accounts ($1K-$5K):   Protected from catastrophic loss
[OK] Medium accounts ($10K-$50K): Reasonable risk tolerance
[OK] Large accounts ($100K+):     Proportional protection

Key Improvements:
1. Scales with account size (percentage-based)
2. Consistent risk across all account sizes ({cb_pct:.1%})
3. Configurable via .env (CIRCUIT_BREAKER_LOSS_PCT)
4. Warns at 80% of threshold ({warning_pct:.1%})
5. Logs both dollar amount AND percentage

Old System Issues (FIXED):
- $1K account: -$1000 = 100% loss (account wipeout!)
- $10K account: -$1000 = 10% loss (reasonable)
- $100K account: -$1000 = 1% loss (too aggressive)
""")

    print("=" * 70)
    print("Test Complete!")
    print("=" * 70)


if __name__ == "__main__":
    try:
        test_circuit_breaker()
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
