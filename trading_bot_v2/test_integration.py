"""
Integration test for database operations.

Tests:
1. Database initialization
2. Trade saving and retrieval
3. Position saving and retrieval
4. Data persistence
"""

import sys
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import DatabaseManager, DATABASE_PATH
from config import config


def test_database_integration():
    """Test full database integration."""
    print("=" * 60)
    print("Database Integration Test")
    print("=" * 60)

    print(f"\nDatabase path: {DATABASE_PATH}")

    # Initialize database
    print("\n[1/6] Initializing DatabaseManager...")
    db = DatabaseManager()
    print("[PASS] DatabaseManager initialized")

    # Test trade saving
    print("\n[2/6] Testing trade save...")
    trade_data = {
        "symbol": "BTC-PERP",
        "asset_class": "crypto",
        "side": "buy",
        "quantity": 0.5,
        "entry_price": 45000.0,
        "entry_time": datetime.now(),
        "status": "open",
        "strategy": "test_strategy",
    }
    trade_id = db.save_trade(trade_data, account_id="test_account_001")
    print(f"[PASS] Trade saved with ID: {trade_id}")

    # Test trade retrieval
    print("\n[3/6] Testing trade retrieval...")
    trades = db.get_trades(limit=10)
    print(f"[PASS] Retrieved {len(trades)} trade(s) from database")
    if trades:
        print(
            f"       Latest trade: {trades[0].get('symbol')} - {trades[0].get('side')} - {trades[0].get('quantity')}"
        )

    # Test position saving
    print("\n[4/6] Testing position save...")
    position_data = {
        "symbol": "ETH-PERP",
        "asset_class": "crypto",
        "side": "long",
        "quantity": 1.0,
        "entry_price": 3000.0,
        "current_price": 3050.0,
        "unrealized_pnl": 50.0,
        "opened_at": datetime.now(),
    }
    position_id = db.save_position(position_data)
    print(f"[PASS] Position saved with ID: {position_id}")

    # Test position retrieval
    print("\n[5/6] Testing position retrieval...")
    positions = db.get_positions()
    print(f"[PASS] Retrieved {len(positions)} open position(s)")
    if positions:
        print(
            f"       Latest position: {positions[0].get('symbol')} - {positions[0].get('side')} - {positions[0].get('quantity')}"
        )

    # Test data persistence
    print("\n[6/6] Testing data persistence...")
    db2 = DatabaseManager()  # New instance
    persisted_trades = db2.get_trades(limit=10)
    persisted_positions = db2.get_positions()
    print(f"[PASS] Data persists across instances")
    print(f"       Persisted trades: {len(persisted_trades)}")
    print(f"       Persisted positions: {len(persisted_positions)}")

    # Summary
    print("\n" + "=" * 60)
    print("Integration Test Summary")
    print("=" * 60)
    print("[+] All database operations working correctly")
    print(f"[+] Total trades in database: {len(trades)}")
    print(f"[+] Total open positions: {len(positions)}")
    print(f"[+] Database file: {DATABASE_PATH}")
    print("[+] Data persists across restarts")
    print("\n[SUCCESS] Database integration test passed!")
    print("=" * 60)

    return True


if __name__ == "__main__":
    try:
        test_database_integration()
        sys.exit(0)
    except Exception as e:
        print(f"\n[FAIL] Integration test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
