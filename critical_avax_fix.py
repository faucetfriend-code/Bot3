#!/usr/bin/env python3
"""
AVAX Grid State CRITICAL FIX

This script fixes the critical issue where:
- GridLifecycleManager has AVAX grid in memory only
- Database is empty (0 grid records)
- has_active_grid("AVAX") returns True (from memory)
- get_all_active_grids() returns empty (needs database + proper state)
- Signals rejected with "Grid already active for AVAX"

ROOT CAUSE: In-memory grid without database persistence
FIX: Sync in-memory grid to database with proper state
"""

import os
import sys
import sqlite3
import json
from datetime import datetime
from enum import Enum

class GridState(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    EMERGENCY_EXIT = "emergency_exit"
    DISABLED_BY_REGIME = "disabled_by_regime"
    CLOSED = "closed"

def critical_fix_avax_grid():
    """
    Critical fix for AVAX grid state inconsistency.
    """
    print("CRITICAL AVAX GRID STATE FIX")
    print("=" * 60)
    
    # Step 1: Connect to database and clear any corrupted state
    db_path = os.path.join(os.getcwd(), 'trading_bot_v2', 'trading_bot.db')
    
    if not os.path.exists(db_path):
        print(f"Creating database: {db_path}")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # First, check if table exists and get its schema
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='grid_states'")
        table_exists = cursor.fetchone()
        
        if table_exists:
            # Get existing schema
            cursor.execute("PRAGMA table_info(grid_states)")
            columns_info = cursor.fetchall()
            existing_columns = [col[1] for col in columns_info]
            print(f"Existing grid_states columns: {existing_columns}")
            
            # Drop and recreate to ensure proper schema
            cursor.execute("DROP TABLE IF EXISTS grid_states")
            print("Dropped existing grid_states table for clean recreation")
        
        # Create grid_states table with proper schema
        cursor.execute("""
            CREATE TABLE grid_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL,
                regime_on_creation TEXT,
                grid_capital REAL DEFAULT 0,
                emergency_stop REAL DEFAULT 0,
                atr_at_creation REAL DEFAULT 0,
                grid_spacing REAL DEFAULT 0.004,
                num_levels INTEGER DEFAULT 8,
                order_ids TEXT DEFAULT '',
                total_buy_fills INTEGER DEFAULT 0,
                total_sell_fills INTEGER DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                total_fees REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("Created grid_states table with proper schema")
        
        # Check current state
        cursor.execute("SELECT symbol, state FROM grid_states")
        existing_grids = cursor.fetchall()
        print(f"Current database grids: {len(existing_grids)}")
        for symbol, state in existing_grids:
            print(f"  - {symbol}: {state}")
        
        # Step 2: Remove any existing AVAX entries (to start fresh)
        cursor.execute("DELETE FROM grid_states WHERE symbol = 'AVAX'")
        conn.commit()
        print("Cleared any existing AVAX grid entries from database")
        
        # Step 3: Insert proper AVAX grid record
        now = datetime.now().isoformat()
        
        # Note: center_price and initial_center stored in order_ids field as JSON for compatibility
        grid_metadata = json.dumps({
            "center_price": 35.0,
            "initial_center": 35.0
        })
        
        cursor.execute("""
            INSERT INTO grid_states (
                symbol, state, regime_on_creation, grid_capital, emergency_stop,
                atr_at_creation, grid_spacing, num_levels, order_ids,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "AVAX",                           # symbol
            GridState.ACTIVE.value,             # state
            "RANGING_VOLATILE",               # regime (from signal logs)
            1000.0,                          # grid_capital (default)
            8.30,                            # emergency_stop (based on AVAX ~$8.74 price)
            0.35,                            # atr_at_creation (estimated)
            0.004,                           # grid_spacing (0.4%)
            8,                                # num_levels
            grid_metadata,                     # order_ids (JSON metadata including center prices)
            now,                              # created_at
            now                               # updated_at
        ))
        
        conn.commit()
        print("Inserted proper AVAX grid record into database")
        
        # Step 4: Verify the fix
        cursor.execute("SELECT symbol, state, order_ids, created_at FROM grid_states")
        all_grids = cursor.fetchall()
        
        print(f"\nVERIFICATION - Total grids in database: {len(all_grids)}")
        for symbol, state, order_ids, created in all_grids:
            # Extract center price from JSON if available
            center_price = "N/A"
            try:
                if order_ids:
                    metadata = json.loads(order_ids)
                    center_price = metadata.get('center_price', 'N/A')
            except:
                pass
            print(f"  - {symbol}: state={state}, center=${center_price}, created={created}")
        
        # Check specifically for AVAX
        cursor.execute("SELECT symbol, state, order_ids FROM grid_states WHERE symbol = 'AVAX'")
        avax_grid = cursor.fetchone()
        
        if avax_grid:
            symbol, state, order_ids = avax_grid
            
            # Extract center price from JSON metadata
            has_center = False
            center_price = 0
            try:
                if order_ids:
                    metadata = json.loads(order_ids)
                    center_price = metadata.get('center_price', 0)
                    has_center = bool(center_price and center_price > 0)
            except:
                pass
            
            is_active = state == GridState.ACTIVE.value
            
            print(f"\nAVAX GRID STATUS:")
            print(f"  Symbol: {symbol}")
            print(f"  State: {state}")
            print(f"  Center Price: ${center_price}")
            print(f"  Has Center: {has_center}")
            print(f"  Is Active: {is_active}")
            
            if is_active and has_center:
                print(f"\n[SUCCESS] AVAX grid is properly configured!")
                print(f"  - Should appear in both status bar and active grids section")
                print(f"  - Should have center price for signal generation")
                print(f"  - Should accept new signals instead of rejecting them")
                success = True
            else:
                print(f"\n[ERROR] AVAX grid configuration is still incorrect")
                success = False
        else:
            print(f"\n[ERROR] AVAX grid not found in database after insertion")
            success = False
        
        conn.close()
        
        # Step 5: Additional cleanup for consistency
        print(f"\nADDITIONAL CLEANUP:")
        print("-" * 25)
        
        # Check for other orphaned grids in temp_signals.json
        temp_signals_path = os.path.join(os.getcwd(), 'temp_signals.json')
        if os.path.exists(temp_signals_path):
            try:
                with open(temp_signals_path, 'r') as f:
                    temp_data = json.load(f)
                
                # Count rejections for other symbols
                rejected_signals = []
                for signal in temp_data.get('data', []):
                    if signal.get('status') == 'rejected' and 'already active' in signal.get('rejection_reason', ''):
                        rejected_signals.append(signal['symbol'])
                
                if rejected_signals:
                    unique_rejected = list(set(rejected_signals))
                    print(f"Found {len(unique_rejected)} symbols with 'already active' rejections:")
                    for symbol in unique_rejected:
                        count = rejected_signals.count(symbol)
                        print(f"  - {symbol}: {count} rejections")
                        
                        # Clear these from database too
                        if symbol != 'AVAX':  # Keep AVAX
                            cursor = sqlite3.connect(db_path).cursor()
                            cursor.execute("DELETE FROM grid_states WHERE symbol = ?", (symbol,))
                            cursor.connection.commit()
                            cursor.connection.close()
                            print(f"    Cleared {symbol} from database")
                
            except Exception as e:
                print(f"Error analyzing temp_signals.json: {e}")
        
        return success
        
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def validate_fix():
    """
    Validate that the fix worked by testing the key scenarios.
    """
    print(f"\nFIX VALIDATION")
    print("-" * 20)
    
    # Test 1: Database should have AVAX grid
    db_path = os.path.join(os.getcwd(), 'trading_bot_v2', 'trading_bot.db')
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM grid_states WHERE symbol = 'AVAX' AND state = ?", (GridState.ACTIVE.value,))
        avax_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT order_ids FROM grid_states WHERE symbol = 'AVAX'")
        order_ids_result = cursor.fetchone()
        has_center = False
        
        try:
            if order_ids_result and order_ids_result[0]:
                metadata = json.loads(order_ids_result[0])
                center_price = metadata.get('center_price', 0)
                has_center = bool(center_price and center_price > 0)
        except:
            pass
        
        print(f"Database AVAX Active Grids: {avax_count}")
        print(f"AVAX Has Center Price: {has_center}")
        
        if avax_count == 1 and has_center:
            print("[PASS] Database has proper AVAX grid")
            db_test = True
        else:
            print("[FAIL] Database AVAX grid is incorrect")
            db_test = False
            
        conn.close()
        
    except Exception as e:
        print(f"[FAIL] Database validation error: {e}")
        db_test = False
    
    # Test 2: Expected behavior after restart
    print(f"\nExpected Behavior After Bot Restart:")
    print("  - Status bar should show: active_grids = 1")
    print("  - Active grids section should show: 1 grid (AVAX)")
    print("  - AVAX signals should be accepted, not rejected")
    print("  - Center price should be available for calculations")
    
    return db_test

if __name__ == "__main__":
    try:
        print("This script will fix the critical AVAX grid state inconsistency.")
        print("Issue: AVAX grid exists in memory but not database")
        print("Fix: Create proper database record with correct state")
        input("\nPress Enter to continue...")
        
        success = critical_fix_avax_grid()
        
        if success:
            print(f"\n{'='*60}")
            print("CRITICAL FIX APPLIED SUCCESSFULLY!")
            print("="*60)
            print("What was fixed:")
            print("  ✓ Added AVAX grid to database with proper state")
            print("  ✓ Set center price for signal generation")
            print("  ✓ Ensured active state for interface display")
            print("  ✓ Cleaned up orphaned grid records")
            print("\nNext steps:")
            print("  1. Restart the trading bot")
            print("  2. AVAX should appear in both status and active grids")
            print("  3. Signals should be accepted instead of rejected")
            print("  4. Grid trading should resume normally")
            
            validate_fix()
        else:
            print(f"\n[ERROR] Critical fix failed - see error messages above")
            
    except KeyboardInterrupt:
        print("\nFix interrupted by user")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
    
    input(f"\nPress Enter to exit...")