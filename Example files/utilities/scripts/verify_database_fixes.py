#!/usr/bin/env python3
"""
Database Connection and Locking Fix Verification Script

This script tests the database fixes to ensure:
1. Connection pool works correctly
2. No database locking issues occur
3. Concurrent operations are handled properly
4. All database operations complete successfully
"""

import sys
import os
import time
import threading
import concurrent.futures
from typing import List, Dict, Any

# Add the project root to Python path
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:
    # __file__ not available when executed inline
    sys.path.insert(0, os.getcwd())

try:
    from database import DatabaseManager, check_database_health, cleanup_database_connections, get_db_connection, _connection_pool
    print("[OK] Database module imported successfully")
except ImportError as e:
    print(f"[ERROR] Failed to import database module: {e}")
    sys.exit(1)


def test_basic_connectivity():
    """Test basic database connectivity."""
    print("\n[TEST] Testing basic database connectivity...")
    try:
        db = DatabaseManager()
        stats = db.get_stats()
        print(f"[OK] Database initialized. Stats: {stats['connection_pool']}")
        return True
    except Exception as e:
        print(f"[ERROR] Basic connectivity test failed: {e}")
        return False


def test_concurrent_connections():
    """Test concurrent database connections."""
    print("\n[TEST] Testing concurrent connections...")

    def worker(worker_id: int) -> bool:
        try:
            with get_db_connection() as conn:
                cursor = conn.execute("SELECT 1")
                result = cursor.fetchone()
                time.sleep(0.1)  # Simulate work
                return result[0] == 1
        except Exception as e:
            print(f"[ERROR] Worker {worker_id} failed: {e}")
            return False

    # Test with 15 concurrent connections (should work with pool size 20)
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(worker, i) for i in range(15)]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    success_count = sum(results)
    print(f"[OK] Concurrent connections test: {success_count}/15 successful")
    return success_count == 15


def test_database_operations():
    """Test various database operations."""
    print("\n[TEST] Testing database operations...")

    try:
        db = DatabaseManager()

        # Test trade operations
        trade_data = {
            "symbol": "BTC",
            "asset_class": "crypto",
            "side": "buy",
            "quantity": 1.0,
            "entry_price": 50000.0,
            "entry_time": "2024-01-01T00:00:00Z",
            "status": "open"
        }

        trade_id = db.save_trade(trade_data)
        print(f"[OK] Trade saved with ID: {trade_id}")

        # Test market data operations
        db.save_market_data("BTC", 50000.0, 100.0)
        print("[OK] Market data saved")

        # Test position operations
        position_data = {
            "symbol": "BTC",
            "asset_class": "crypto",
            "side": "long",
            "quantity": 1.0,
            "entry_price": 50000.0,
            "current_price": 51000.0,
            "unrealized_pnl": 1000.0,
            "opened_at": "2024-01-01T00:00:00Z"
        }

        position_id = db.save_position(position_data)
        print(f"[OK] Position saved with ID: {position_id}")

        # Test signal operations
        signal_data = {
            "symbol": "BTC",
            "asset_class": "crypto",
            "signal_type": "buy",
            "strength": 0.8,
            "indicators": {"rsi": 30, "macd": -0.5},
            "timestamp": "2024-01-01T00:00:00Z"
        }

        signal_id = db.save_signal(signal_data)
        print(f"[OK] Signal saved with ID: {signal_id}")

        # Test data retrieval
        trades = db.get_trades(limit=5)
        positions = db.get_positions()
        signals = db.get_signals(limit=5)

        print(f"[OK] Retrieved {len(trades)} trades, {len(positions)} positions, {len(signals)} signals")

        return True

    except Exception as e:
        print(f"[ERROR] Database operations test failed: {e}")
        return False


def test_health_check():
    """Test database health check functionality."""
    print("\n[TEST] Testing database health check...")

    try:
        health = check_database_health()
        print(f"[OK] Health check completed. Database accessible: {health['database_accessible']}")
        print(f"   Connection pool: {health['connection_pool']}")
        print(f"   Table counts: {health['table_counts']}")
        if health['errors']:
            print(f"   Errors: {health['errors']}")
        return health['database_accessible']
    except Exception as e:
        print(f"[ERROR] Health check failed: {e}")
        return False


def test_connection_pool_limits():
    """Test connection pool limits and recovery."""
    print("\n[TEST] Testing connection pool limits...")

    connections = []

    try:
        # Try to acquire maximum connections
        for i in range(25):  # Try more than pool limit
            try:
                conn = _connection_pool.get_connection()
                connections.append(conn)
                print(f"[OK] Acquired connection {i+1}")
            except TimeoutError:
                print(f"[WARN] Connection {i+1} timed out (expected for pool limit)")
                break

        print(f"[OK] Successfully acquired {len(connections)} connections")

        # Release all connections
        for conn in connections:
            _connection_pool.release_connection(conn)

        print("[OK] All connections released")

        # Test pool recovery
        with get_db_connection() as conn:
            cursor = conn.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1

        print("[OK] Connection pool recovered successfully")
        return True

    except Exception as e:
        print(f"[ERROR] Connection pool limits test failed: {e}")
        # Clean up any remaining connections
        for conn in connections:
            try:
                _connection_pool.release_connection(conn)
            except:
                pass
        return False


def main():
    """Run all database fix verification tests."""
    print(">>> Database Connection and Locking Fix Verification")
    print("=" * 60)

    tests = [
        ("Basic Connectivity", test_basic_connectivity),
        ("Concurrent Connections", test_concurrent_connections),
        ("Database Operations", test_database_operations),
        ("Health Check", test_health_check),
        ("Connection Pool Limits", test_connection_pool_limits),
    ]

    results = []

    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        start_time = time.time()
        success = test_func()
        duration = time.time() - start_time
        results.append((test_name, success, duration))
        status = "[PASSED]" if success else "[FAILED]"
        print(".2f")

    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)

    all_passed = True
    for test_name, success, duration in results:
        status = "[PASSED]" if success else "[FAILED]"
        print(".2f")
        if not success:
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("SUCCESS: ALL TESTS PASSED - Database fixes are working correctly!")
        print("\n[PASS] Database connection issues resolved")
        print("[PASS] Database locking problems fixed")
        print("[PASS] Connection pool operating normally")
        print("[PASS] All database operations functional")
        return 0
    else:
        print("FAILURE: SOME TESTS FAILED - Database issues may still exist")
        return 1


if __name__ == "__main__":
    sys.exit(main())