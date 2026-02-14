"""
Comprehensive test for Grid Upgrade (Prompts 049-052)
Tests trend direction detection, partial unwind, migrated position tracking
"""

import sys
import json

def test_trend_direction():
    """Test 049: Trend direction detection"""
    from trading_bot_v2.market_regime import MarketRegimeDetector

    detector = MarketRegimeDetector()

    # Create trending UP data
    closes_up = [100 + (i * 0.5) for i in range(250)]
    market_data_up = {'close': closes_up}

    # Create trending DOWN data
    closes_down = [200 - (i * 0.5) for i in range(250)]
    market_data_down = {'close': closes_down}

    # Create insufficient data
    market_data_short = {'close': [100] * 50}

    results = []

    # Test 1: Uptrend detection
    result_up = detector.get_trend_direction(market_data_up)
    results.append(("Uptrend detection", result_up == 'up', f"got '{result_up}'"))

    # Test 2: Downtrend detection
    result_down = detector.get_trend_direction(market_data_down)
    results.append(("Downtrend detection", result_down == 'down', f"got '{result_down}'"))

    # Test 3: Insufficient data
    result_short = detector.get_trend_direction(market_data_short)
    results.append(("Insufficient data handling", result_short == 'none', f"got '{result_short}'"))

    # Test 4: Cached version
    result_cached = detector.get_trend_direction_cached('TEST', market_data_up)
    results.append(("Cached trend direction", result_cached == 'up', f"got '{result_cached}'"))

    return "Trend Direction (049)", results


def test_partial_exit():
    """Test 050: Partial grid unwind"""
    from trading_bot_v2.grid_lifecycle_manager import GridLifecycleManager, GridState
    from trading_bot_v2.risk_manager import RiskManager
    from trading_bot_v2.market_regime import MarketRegimeDetector

    class MockClient:
        def __init__(self):
            self.cancelled = []
            self.closed = []
            self.positions = [
                {'symbol': 'TEST', 'side': 'bid', 'amount': 1.0, 'entry_price': 100},
                {'symbol': 'TEST', 'side': 'ask', 'amount': 0.5, 'entry_price': 101},
            ]
        def cancel_all_orders(self, symbol): self.cancelled.append(symbol)
        def get_positions(self): return self.positions
        def place_order(self, symbol, side, qty, order_type):
            self.closed.append({'symbol': symbol, 'side': side, 'qty': qty})

    rm = RiskManager()
    detector = MarketRegimeDetector()
    client = MockClient()

    glm = GridLifecycleManager(client=client, risk_manager=rm, db=None, regime_detector=detector)
    glm._grids['TEST'] = {'state': GridState.ACTIVE, 'grid_capital': 1000}

    results = []

    # Test partial exit with UP trend (should close SHORT, keep LONG)
    result = glm._partial_exit('TEST', 'TEST_REASON', 'up')

    results.append(("Partial exit success", result['success'], ""))
    results.append(("Closed SHORT (against trend)",
                   len(result['closed_positions']) == 1 and result['closed_positions'][0]['side'] == 'short',
                   f"closed {result['closed_positions']}"))
    results.append(("Kept LONG (with trend)",
                   len(result['kept_positions']) == 1 and result['kept_positions'][0]['side'] == 'long',
                   f"kept {result['kept_positions']}"))
    results.append(("Orders cancelled", len(client.cancelled) == 1, f"cancelled {client.cancelled}"))
    results.append(("Position registered with RiskManager",
                   rm.has_migrated_positions('TEST'),
                   ""))

    return "Partial Exit (050)", results


def test_risk_manager_migrated():
    """Test 051: RiskManager migrated position tracking"""
    from trading_bot_v2.risk_manager import RiskManager

    rm = RiskManager()
    results = []

    # Test register
    success = rm.register_migrated_position('TEST', {
        'side': 'long', 'qty': 1.0, 'entry_price': 100.0, 'has_stop': False
    })
    results.append(("Register migrated position", success, ""))

    # Test has_migrated_positions
    has_pos = rm.has_migrated_positions('TEST')
    results.append(("Has migrated positions", has_pos, ""))

    # Test get_migrated_exposure
    exposure = rm.get_migrated_exposure('TEST')
    results.append(("Get migrated exposure", exposure == 100.0, f"exposure={exposure}"))

    # Test get_total_exposure
    total = rm.get_total_exposure('TEST')
    results.append(("Get total exposure", total['migrated_exposure'] == 100.0, f"total={total}"))

    # Test update stop
    rm.update_migrated_stop('TEST', 'long', 95.0)
    positions = rm.get_migrated_positions('TEST')
    results.append(("Update migrated stop",
                   positions[0]['stop_price'] == 95.0 and positions[0]['has_stop'],
                   f"stop={positions[0].get('stop_price')}"))

    # Test unregister
    success = rm.unregister_migrated_position('TEST', 'long')
    results.append(("Unregister migrated position", success, ""))

    has_pos_after = rm.has_migrated_positions('TEST')
    results.append(("Position removed", not has_pos_after, ""))

    return "RiskManager Migrated (051)", results


def test_migrated_position_manager():
    """Test 052: MigratedPositionManager"""
    from trading_bot_v2.migrated_position_manager import MigratedPositionManager
    from trading_bot_v2.risk_manager import RiskManager
    from trading_bot_v2.market_regime import MarketRegimeDetector

    class MockClient:
        def get_ticker(self, symbol): return {'last': 100.0}
        def place_order(self, *args): pass

    rm = RiskManager()
    detector = MarketRegimeDetector()
    client = MockClient()

    mpm = MigratedPositionManager(
        client=client, risk_manager=rm, regime_detector=detector, multi_tf_fetcher=None
    )

    results = []

    # Test has_positions (empty)
    has_pos = mpm.has_positions()
    results.append(("Has positions (empty)", not has_pos, ""))

    # Register a position via RiskManager
    rm.register_migrated_position('TEST', {
        'side': 'long', 'qty': 1.0, 'entry_price': 100.0, 'trend_direction': 'up', 'has_stop': False
    })

    # Test has_positions (with position)
    has_pos = mpm.has_positions()
    results.append(("Has positions (with position)", has_pos, ""))

    # Test manage_positions (should set trailing stop)
    result = mpm.manage_positions({'TEST': 105.0})
    results.append(("Manage positions runs", 'positions_managed' in result, f"result={result}"))

    # Test get_status
    status = mpm.get_status()
    results.append(("Get status", 'total_positions' in status, f"status keys={list(status.keys())}"))

    return "MigratedPositionManager (052)", results


def test_trading_bot_integration():
    """Test TradingBot has all new components"""
    from trading_bot_v2.trading_bot import TradingBot

    bot = TradingBot()
    results = []

    # Check grid_lifecycle has regime_detector
    results.append(("GridLifecycle has regime_detector",
                   bot.grid_lifecycle.regime_detector is not None, ""))

    # Check migrated_manager exists
    results.append(("MigratedPositionManager exists",
                   hasattr(bot, 'migrated_manager'), ""))

    # Check _manage_migrated_positions method exists
    results.append(("_manage_migrated_positions method exists",
                   hasattr(bot, '_manage_migrated_positions'), ""))

    # Check regime_detector has trend direction methods
    results.append(("Regime detector has get_trend_direction",
                   hasattr(bot.strategy_manager.regime_detector, 'get_trend_direction'), ""))

    return "TradingBot Integration", results


def run_all_tests():
    """Run all tests and print results"""
    print("=" * 60)
    print("GRID UPGRADE TEST SUITE (Prompts 049-052)")
    print("=" * 60)

    all_passed = True
    test_functions = [
        test_trend_direction,
        test_partial_exit,
        test_risk_manager_migrated,
        test_migrated_position_manager,
        test_trading_bot_integration,
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
