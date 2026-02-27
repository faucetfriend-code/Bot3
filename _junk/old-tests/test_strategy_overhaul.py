"""
Comprehensive test for Strategy Overhaul (Prompts 053-058)
Tests 3-phase architecture, confidence sizing, cooldowns, and loosened thresholds
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "trading_bot_v2"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Example files", "core_logic"))


def test_signal_phases_import():
    """Test 053: Signal phases module imports correctly."""
    from trading_bot_v2.signal_phases import (
        Phase1RegimePermission,
        Phase3ExecutionFilter,
        SignalPipeline,
        PhaseResult,
        PhaseDecision
    )

    results = []
    results.append(("Phase1RegimePermission exists", Phase1RegimePermission is not None, ""))
    results.append(("Phase3ExecutionFilter exists", Phase3ExecutionFilter is not None, ""))
    results.append(("SignalPipeline exists", SignalPipeline is not None, ""))
    results.append(("PhaseResult enum exists", PhaseResult.PASS is not None, ""))

    return "Signal Phases (053)", results


def test_confidence_sizer():
    """Test 055: Confidence sizer module."""
    from trading_bot_v2.confidence_sizer import ConfidenceSizer, SizingResult
    from config import StrategyType, AssetClass
    from models import Signal, OrderSide

    sizer = ConfidenceSizer()
    results = []

    # Test floor behavior
    signal_low = Signal(
        strategy=StrategyType.MEAN_REVERSION,
        asset='TEST',
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        confidence=0.2  # Very low
    )
    result_low = sizer.calculate_multiplier(signal_low)
    results.append(("Confidence floor applied", result_low.multiplier >= 0.3, f"multiplier={result_low.multiplier}"))

    # Test normal confidence
    signal_normal = Signal(
        strategy=StrategyType.MEAN_REVERSION,
        asset='TEST',
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        confidence=0.7
    )
    result_normal = sizer.calculate_multiplier(signal_normal)
    results.append(("Normal confidence passes through", result_normal.multiplier == 0.7, f"multiplier={result_normal.multiplier}"))

    # Test apply_to_size
    sizing = sizer.apply_to_size(10.0, signal_normal)
    results.append(("Apply to size works", sizing.effective_size == 7.0, f"effective_size={sizing.effective_size}"))

    # Test strategy-specific floor
    signal_grid = Signal(
        strategy=StrategyType.GRID_TRADING,
        asset='TEST',
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        confidence=0.2
    )
    result_grid = sizer.calculate_multiplier(signal_grid)
    results.append(("Grid trading has higher floor", result_grid.multiplier >= 0.4, f"multiplier={result_grid.multiplier}"))

    return "Confidence Sizer (055)", results


def test_cooldown_manager():
    """Test 056: Cooldown manager module."""
    from trading_bot_v2.cooldown_manager import CooldownManager, CooldownType
    from config import StrategyType

    manager = CooldownManager()
    results = []

    # Test can_trade when no cooldown
    can_trade, reason = manager.can_trade('BTC', StrategyType.GRID_TRADING, '1h')
    results.append(("Can trade when no cooldown", can_trade, reason))

    # Set a cooldown
    manager.set_cooldown('BTC', StrategyType.GRID_TRADING, '1h', CooldownType.TRADE)

    # Test can_trade with cooldown
    can_trade, reason = manager.can_trade('BTC', StrategyType.GRID_TRADING, '1h')
    results.append(("Blocked when in cooldown", not can_trade, reason))

    # Test different strategy not blocked
    can_trade, reason = manager.can_trade('BTC', StrategyType.MEAN_REVERSION, '1h')
    results.append(("Different strategy not blocked", can_trade, reason))

    # Test different symbol not blocked
    can_trade, reason = manager.can_trade('ETH', StrategyType.GRID_TRADING, '1h')
    results.append(("Different symbol not blocked", can_trade, reason))

    # Test emergency cooldown
    manager.set_emergency_cooldown(60, "test")
    can_trade, reason = manager.can_trade('ETH', StrategyType.MEAN_REVERSION, '1h')
    results.append(("Emergency cooldown blocks all", not can_trade, reason))

    manager.clear_emergency_cooldown()
    can_trade, reason = manager.can_trade('ETH', StrategyType.MEAN_REVERSION, '1h')
    results.append(("Clear emergency cooldown works", can_trade, reason))

    return "Cooldown Manager (056)", results


def test_grid_thresholds():
    """Test 057: Grid trading loosened thresholds."""
    from trading_bot_v2.strategies.grid_trading import GridTradingStrategy

    # Create with defaults
    strategy = GridTradingStrategy(risk_manager=MockRiskManager())
    results = []

    # Check ADX threshold
    results.append(("ADX threshold is 25", strategy.adx_threshold == 25.0, f"got {strategy.adx_threshold}"))

    # Check min confidence
    results.append(("Min confidence is 0.45", strategy.min_confidence == 0.45, f"got {strategy.min_confidence}"))

    return "Grid Thresholds (057)", results


def test_mean_reversion_thresholds():
    """Test 058: Mean reversion loosened thresholds."""
    from trading_bot_v2.strategies.mean_reversion import MeanReversionStrategy

    strategy = MeanReversionStrategy()
    results = []

    # Check RSI thresholds
    results.append(("RSI oversold is 35", strategy.rsi_oversold == 35.0, f"got {strategy.rsi_oversold}"))
    results.append(("RSI overbought is 65", strategy.rsi_overbought == 65.0, f"got {strategy.rsi_overbought}"))

    # Check min confidence
    results.append(("Min confidence is 0.45", strategy.min_confidence == 0.45, f"got {strategy.min_confidence}"))

    return "Mean Reversion Thresholds (058)", results


def test_liquidation_capture_thresholds():
    """Test 058: Liquidation capture loosened thresholds."""
    from trading_bot_v2.strategies.liquidation_capture import LiquidationCaptureStrategy

    strategy = LiquidationCaptureStrategy()
    results = []

    # Check price threshold
    results.append(("Price threshold is 0.025", strategy.price_threshold == 0.025, f"got {strategy.price_threshold}"))

    # Check volume multiplier
    results.append(("Volume multiplier is 2.5", strategy.volume_multiplier == 2.5, f"got {strategy.volume_multiplier}"))

    # Check RSI thresholds
    results.append(("RSI oversold is 20", strategy.rsi_oversold == 20.0, f"got {strategy.rsi_oversold}"))
    results.append(("RSI overbought is 80", strategy.rsi_overbought == 80.0, f"got {strategy.rsi_overbought}"))

    # Check session limits
    results.append(("Max per session is 2", strategy.max_per_session == 2, f"got {strategy.max_per_session}"))
    results.append(("Min hours between is 2", strategy.min_hours_between == 2, f"got {strategy.min_hours_between}"))

    return "Liquidation Capture Thresholds (058)", results


def test_strategy_manager_integration():
    """Test 054: StrategyManager has new methods."""
    from trading_bot_v2.strategy_manager import StrategyManager

    sm = StrategyManager()
    results = []

    # Check get_strategies_by_type method
    results.append(("get_strategies_by_type exists", hasattr(sm, 'get_strategies_by_type'), ""))

    # Check set_cooldown_manager method
    results.append(("set_cooldown_manager exists", hasattr(sm, 'set_cooldown_manager'), ""))

    return "StrategyManager Integration (054)", results


class MockRiskManager:
    """Mock risk manager for testing."""
    last_known_balance = 15000

    def is_circuit_breaker_triggered(self):
        return False

    def get_total_exposure(self, symbol):
        return {'total_exposure': 0}


def run_all_tests():
    """Run all tests and print results."""
    print("=" * 60)
    print("STRATEGY OVERHAUL TEST SUITE (Prompts 053-058)")
    print("=" * 60)

    all_passed = True
    test_functions = [
        test_signal_phases_import,
        test_confidence_sizer,
        test_cooldown_manager,
        test_grid_thresholds,
        test_mean_reversion_thresholds,
        test_liquidation_capture_thresholds,
        test_strategy_manager_integration,
    ]

    for test_func in test_functions:
        try:
            section_name, results = test_func()
            print(f"\n{section_name}:")
            section_passed = True
            for test_name, passed, detail in results:
                status = "PASS" if passed else "FAIL"
                print(f"  [{status}] {test_name} {detail}")
                if not passed:
                    section_passed = False
                    all_passed = False
            print(f"  Section: {'ALL PASSED' if section_passed else 'SOME FAILED'}")
        except Exception as e:
            print(f"\n{test_func.__name__}: EXCEPTION - {e}")
            import traceback
            traceback.print_exc()
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("OVERALL RESULT: ALL TESTS PASSED")
    else:
        print("OVERALL RESULT: SOME TESTS FAILED")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
