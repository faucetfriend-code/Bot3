#!/usr/bin/env python3
"""
Test script for API server with database backend.
"""

import json
import time
from database import DatabaseManager, init_database


def test_database_operations():
    """Test database operations directly."""
    print("Testing database operations...")

    init_database()
    db = DatabaseManager()

    # Add some test data
    print("Adding test trade...")
    trade_id = db.save_trade(
        {
            "symbol": "BTC",
            "side": "long",
            "quantity": 0.1,
            "entry_price": 45000,
            "exit_price": 46000,
            "entry_time": "2024-01-01T10:00:00",
            "exit_time": "2024-01-01T11:00:00",
            "pnl": 100,
            "commission": 5,
            "strategy": "test_strategy",
            "status": "closed",
        }
    )
    print(f"Added trade with ID: {trade_id}")

    print("Adding test signal...")
    signal_id = db.save_signal(
        {
            "symbol": "ETH",
            "signal_type": "buy",
            "strength": 0.8,
            "indicators": json.dumps({"rsi": 30, "macd": "bullish"}),
            "timestamp": "2024-01-01T12:00:00",
            "strategy": "test_strategy",
        }
    )
    print(f"Added signal with ID: {signal_id}")

    print("Adding test market data...")
    market_id = db.save_market_data("BTC", 45000, 1000, "test")
    print(f"Added market data with ID: {market_id}")

    # Test retrieval
    print("\nTesting data retrieval...")
    trades = db.get_trades(limit=5)
    print(f"Retrieved {len(trades)} trades")

    signals = db.get_signals(limit=5)
    print(f"Retrieved {len(signals)} signals")

    market_data = db.get_market_data("BTC", limit=5)
    print(f"Retrieved {len(market_data)} BTC market data points")

    stats = db.get_stats()
    print(f"Database stats: {stats}")

    return True


def test_api_endpoints():
    """Test API endpoints (requires server to be running)."""
    print("\nTesting API endpoints...")

    # Note: This would require authentication, so we'll skip for now
    # In a real test, we'd need to login first and get a token

    print("API endpoint testing skipped (requires authentication)")
    return True


if __name__ == "__main__":
    try:
        success = test_database_operations()
        if success:
            print("\n[SUCCESS] Database operations test passed")
        else:
            print("\n[FAILED] Database operations test failed")

        test_api_endpoints()

    except Exception as e:
        print(f"\n[ERROR] Test failed with error: {e}")
        import traceback

        traceback.print_exc()
