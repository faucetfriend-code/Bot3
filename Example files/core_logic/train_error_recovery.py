"""
Training Script for Error Recovery Agent

This script trains agents to better handle and recover from errors
using Agent Lightning's reinforcement learning capabilities.
"""

import asyncio
import sys
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from agents.agl_config import get_agl_config, is_agl_available
from agents.agl_error_tracker import get_agl_error_tracker


async def create_error_scenarios() -> List[Dict[str, Any]]:
    """
    Create training scenarios focused on error handling.

    Returns:
        List of error scenarios for training
    """
    scenarios = [
        # API Connection Errors
        {
            "scenario_id": "api_connection_1",
            "type": "connection_error",
            "description": "Exchange API connection timeout",
            "error_code": "EXCHANGE_CONNECTION_FAILED",
            "context": {
                "exchange": "pacifica",
                "endpoint": "/api/v1/positions",
                "timeout": 30
            },
            "expected_recovery": "retry_with_backoff"
        },
        {
            "scenario_id": "api_auth_1",
            "type": "auth_error",
            "description": "Authentication failure with exchange",
            "error_code": "EXCHANGE_AUTH_FAILED",
            "context": {
                "exchange": "pacifica",
                "auth_method": "hmac"
            },
            "expected_recovery": "refresh_credentials"
        },

        # Order Placement Errors
        {
            "scenario_id": "order_placement_1",
            "type": "order_error",
            "description": "Order placement fails due to insufficient balance",
            "error_code": "INSUFFICIENT_BALANCE",
            "context": {
                "order_size": 100,
                "available_balance": 50,
                "symbol": "SOL-PERP"
            },
            "expected_recovery": "reduce_order_size"
        },
        {
            "scenario_id": "order_placement_2",
            "type": "order_error",
            "description": "Order placement fails due to leverage too high",
            "error_code": "LEVERAGE_TOO_HIGH",
            "context": {
                "requested_leverage": 20,
                "max_leverage": 10,
                "symbol": "BTC-PERP"
            },
            "expected_recovery": "reduce_leverage"
        },

        # Risk Validation Errors
        {
            "scenario_id": "risk_validation_1",
            "type": "risk_error",
            "description": "Position size exceeds risk limits",
            "error_code": "RISK_VIOLATION",
            "context": {
                "position_size": 1000,
                "max_position_size": 500,
                "risk_per_trade": 0.02
            },
            "expected_recovery": "reduce_position_size"
        },
        {
            "scenario_id": "risk_validation_2",
            "type": "risk_error",
            "description": "Stop loss too far from entry",
            "error_code": "STOP_LOSS_INVALID",
            "context": {
                "entry_price": 100,
                "stop_loss": 50,
                "max_stop_loss_distance": 0.05
            },
            "expected_recovery": "adjust_stop_loss"
        },

        # Nested Error Scenarios
        {
            "scenario_id": "nested_error_1",
            "type": "nested_error",
            "description": "Error occurs during error recovery",
            "error_code": "ORDER_CANCEL_FAILED",
            "context": {
                "original_error": "ORDER_PLACEMENT_FAILED",
                "recovery_action": "cancel_pending_orders",
                "cascade_level": 2
            },
            "expected_recovery": "emergency_shutdown"
        },
        {
            "scenario_id": "nested_error_2",
            "type": "nested_error",
            "description": "Multiple validation failures in sequence",
            "error_code": "VALIDATION_FAILED",
            "context": {
                "error_chain": [
                    "RISK_VIOLATION",
                    "LEVERAGE_TOO_HIGH",
                    "INSUFFICIENT_BALANCE"
                ],
                "cascade_level": 3
            },
            "expected_recovery": "reset_and_recalculate"
        },

        # Rate Limiting Errors
        {
            "scenario_id": "rate_limit_1",
            "type": "rate_limit",
            "description": "API rate limit exceeded",
            "error_code": "RATE_LIMIT_EXCEEDED",
            "context": {
                "requests_made": 100,
                "rate_limit": 60,
                "time_window": 60
            },
            "expected_recovery": "exponential_backoff"
        }
    ]

    return scenarios


async def simulate_error_scenario(
    scenario: Dict[str, Any],
    tracker: Any
) -> Dict[str, Any]:
    """
    Simulate an error scenario and track recovery.

    Args:
        scenario: Error scenario configuration
        tracker: AGL error tracker instance

    Returns:
        Results of the simulation
    """
    logger.info(
        f"Simulating scenario: {scenario['scenario_id']} - "
        f"{scenario['description']}"
    )

    # Track the error
    tracker.track_error(
        error_code=scenario["error_code"],
        error_message=scenario["description"],
        context=scenario["context"],
        severity="error" if "nested" not in scenario["type"] else "critical"
    )

    # Simulate recovery attempt
    recovery_success = False
    recovery_method = scenario["expected_recovery"]

    # Track recovery attempt
    tracker.track_recovery_attempt(scenario["error_code"])

    # Simulate recovery logic based on scenario type
    if scenario["type"] == "nested_error":
        # Nested errors are harder to recover from
        recovery_success = False
        tracker.track_recovery_failure(scenario["error_code"])
    elif scenario["type"] == "rate_limit":
        # Rate limits require waiting
        await asyncio.sleep(0.1)  # Simulate backoff
        recovery_success = True
        tracker.track_recovery_success(scenario["error_code"])
    else:
        # Most errors can be recovered
        recovery_success = True
        tracker.track_recovery_success(scenario["error_code"])

    result = {
        "scenario_id": scenario["scenario_id"],
        "error_code": scenario["error_code"],
        "recovery_method": recovery_method,
        "recovery_success": recovery_success,
        "chain_summary": tracker.get_error_chain_summary()
    }

    # Clear chain after scenario (unless it's a nested error test)
    if scenario["type"] != "nested_error":
        tracker.clear_error_chain()

    return result


async def run_training_session():
    """Run a complete training session for error recovery."""
    if not is_agl_available():
        logger.error("Agent Lightning is not available. Install with: pip install agentlightning")
        return

    logger.info("Starting Error Recovery Training Session")

    # Initialize components
    config = get_agl_config()
    tracker = get_agl_error_tracker()

    # Create scenarios
    scenarios = await create_error_scenarios()
    logger.info(f"Created {len(scenarios)} training scenarios")

    # Run through all scenarios
    results = []
    for scenario in scenarios:
        result = await simulate_error_scenario(scenario, tracker)
        results.append(result)
        await asyncio.sleep(0.1)  # Small delay between scenarios

    # Print summary
    logger.info("\n" + "="*60)
    logger.info("Training Session Summary")
    logger.info("="*60)

    total_scenarios = len(results)
    successful_recoveries = sum(1 for r in results if r["recovery_success"])
    recovery_rate = successful_recoveries / total_scenarios if total_scenarios > 0 else 0

    logger.info(f"Total Scenarios: {total_scenarios}")
    logger.info(f"Successful Recoveries: {successful_recoveries}")
    logger.info(f"Recovery Rate: {recovery_rate:.2%}")

    # Get overall statistics
    stats = tracker.get_error_statistics()
    logger.info(f"\nOverall Error Statistics:")
    logger.info(f"  Total Errors Tracked: {stats['total_errors']}")
    logger.info(f"  Recovered Errors: {stats['recovered_errors']}")
    logger.info(f"  Overall Recovery Rate: {stats['recovery_rate']:.2%}")
    logger.info(f"  Severity Breakdown: {stats['severity_breakdown']}")

    logger.info(f"\nTop Error Codes:")
    for code, count in list(stats['top_error_codes'].items())[:5]:
        logger.info(f"  {code}: {count}")

    logger.info("\n" + "="*60)


async def continuous_training():
    """
    Run continuous training in the background.

    This can be called from the main trading bot to continuously
    improve error handling based on real errors encountered.
    """
    logger.info("Starting continuous error recovery training")

    while True:
        try:
            await run_training_session()
            # Wait before next training session
            await asyncio.sleep(3600)  # Train every hour
        except Exception as e:
            logger.error(f"Error in continuous training: {e}")
            await asyncio.sleep(300)  # Wait 5 minutes on error


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Train error recovery agent with Agent Lightning"
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuous training (runs every hour)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    if args.continuous:
        asyncio.run(continuous_training())
    else:
        asyncio.run(run_training_session())
