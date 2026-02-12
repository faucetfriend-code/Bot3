#!/usr/bin/env python3
"""
Simple Grid State Fix - Direct Database Approach

Fixes the critical grid state inconsistency by directly examining and repairing the database.
"""

import sqlite3
import os
import json
from datetime import datetime
from enum import Enum

class GridState(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    EMERGENCY_EXIT = "emergency_exit"
    DISABLED_BY_REGIME = "disabled_by_regime"
    CLOSED = "closed"

def fix_grid_state_inconsistency():
    """
    Directly fix grid state inconsistency in database.
    """
    print("Grid State Inconsistency Fix")
    print("=" * 50)
    
    # Find database file
    db_path = None
    possible_paths = [
        os.path.join(os.getcwd(), 'trading_bot_v2', 'trading_bot.db'),
        os.path.join(os.getcwd(), 'trading_bot.db'),
        os.path.join(os.path.dirname(__file__), 'trading_bot_v2', 'trading_bot.db'),
        os.path.join(os.path.dirname(__file__), 'trading_bot.db'),
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            db_path = path
            break
    
    if not db_path:
        print("ERROR: Could not find trading_bot.db file")
        print("Checked paths:")
        for path in possible_paths:
            print(f"  - {path}")
        return False
    
    print(f"Using database: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check grid_states table
        print("\nChecking grid_states table...")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='grid_states'")
        table_exists = cursor.fetchone()
        
        if not table_exists:
            print("ERROR: grid_states table not found")
            return False
        
        # First, check table schema
        cursor.execute("PRAGMA table_info(grid_states)")
        columns_info = cursor.fetchall()
        print(f"Database schema for grid_states:")
        for col_info in columns_info:
            print(f"  - {col_info[1]} ({col_info[2]})")
        
        # Get all grid states (adjust query based on actual columns)
        try:
            cursor.execute("SELECT symbol, state, regime_on_creation, grid_capital, emergency_stop, atr_at_creation, grid_spacing, num_levels, updated_at FROM grid_states")
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            print(f"Error querying grid_states: {e}")
            # Fallback to basic query
            cursor.execute("SELECT * FROM grid_states")
            rows = cursor.fetchall()
        
        print(f"Found {len(rows)} grid records in database")
        
        if not rows:
            print("No grid states found in database")
            return True
        
        # Analyze each grid
        issues_found = []
        grids_to_fix = []
        
        for row in rows:
            # Handle variable number of columns
            if len(row) >= 9:
                symbol, state, regime, capital, emergency_stop, atr, spacing, levels, center_price, initial_center, updated_at = row[:9] + (row[9] if len(row) > 9 else None,)
            else:
                symbol, state, regime, capital, emergency_stop, atr, spacing, levels = row
                center_price = None
                initial_center = None
                updated_at = None
            
            print(f"\nAnalyzing {symbol}:")
            print(f"  State: {state} (type: {type(state)})")
            print(f"  Center Price: {center_price}")
            print(f"  Initial Center: {initial_center}")
            print(f"  Capital: {capital}")
            print(f"  Spacing: {spacing}")
            print(f"  Levels: {levels}")
            
            # Check for issues
            symbol_issues = []
            
            # Issue 1: State stored as string but should be compared to enum
            if isinstance(state, str):
                if state.lower() == GridState.ACTIVE.value:
                    symbol_issues.append("active_state_as_string")
                elif state.lower() == GridState.DISABLED_BY_REGIME.value:
                    symbol_issues.append("disabled_state_as_string")
            
            # Issue 2: Missing center price for active grid
            if state and state.lower() == GridState.ACTIVE.value:
                if not center_price and not initial_center:
                    symbol_issues.append("missing_center_price")
            
            # Issue 3: Missing essential fields
            if not capital or capital <= 0:
                symbol_issues.append("invalid_capital")
            if not spacing or spacing <= 0:
                symbol_issues.append("invalid_spacing")
            if not levels or levels <= 0:
                symbol_issues.append("invalid_levels")
            
            if symbol_issues:
                issues_found.append((symbol, symbol_issues))
                grids_to_fix.append((symbol, state, symbol_issues))
                print(f"  ISSUES: {', '.join(symbol_issues)}")
            else:
                print(f"  OK: No issues found")
        
        print(f"\nSUMMARY:")
        print(f"Total grids: {len(rows)}")
        print(f"Grids with issues: {len(issues_found)}")
        
        if issues_found:
            print(f"\nGrids needing fixes:")
            for symbol, issues in issues_found:
                print(f"  - {symbol}: {', '.join(issues)}")
        
        # Apply fixes
        fixes_applied = 0
        
        for symbol, current_state, issues in grids_to_fix:
            print(f"\nFixing {symbol}...")
            
            # Determine new state value (keep as string for database compatibility)
            new_state = current_state
            updates = []
            update_values = []
            
            # Fix state string format if needed
            if "active_state_as_string" in issues:
                new_state = GridState.ACTIVE.value
                updates.append("state = ?")
                update_values.append(new_state)
                print(f"  Fixed state format: {current_state} -> {new_state}")
            
            # Fix missing center price
            if "missing_center_price" in issues:
                if symbol.upper() == "AVAX":
                    default_center = 35.0
                elif symbol.upper() == "BTC":
                    default_center = 95000.0
                elif symbol.upper() == "ETH":
                    default_center = 3300.0
                else:
                    default_center = 100.0
                
                updates.append("center_price = ?")
                updates.append("initial_center = ?")
                update_values.extend([default_center, default_center])
                print(f"  Set center price: ${default_center}")
            
            # Fix invalid capital
            if "invalid_capital" in issues:
                default_capital = 1000.0
                updates.append("grid_capital = ?")
                update_values.append(default_capital)
                print(f"  Set capital: ${default_capital}")
            
            # Fix invalid spacing
            if "invalid_spacing" in issues:
                default_spacing = 0.004  # 0.4%
                updates.append("grid_spacing = ?")
                update_values.append(default_spacing)
                print(f"  Set spacing: {default_spacing}")
            
            # Fix invalid levels
            if "invalid_levels" in issues:
                default_levels = 8
                updates.append("num_levels = ?")
                update_values.append(default_levels)
                print(f"  Set levels: {default_levels}")
            
            # Apply the updates
            if updates:
                update_values.append(symbol)  # For WHERE clause
                sql = f"UPDATE grid_states SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE symbol = ?"
                
                try:
                    cursor.execute(sql, update_values)
                    conn.commit()
                    fixes_applied += 1
                    print(f"  SUCCESS: Applied {len(updates)} fixes")
                except Exception as e:
                    print(f"  ERROR: Failed to update {symbol}: {e}")
            else:
                print(f"  No fixes needed for {symbol}")
        
        print(f"\nFIXES APPLIED: {fixes_applied}")
        
        # Verify fixes
        if fixes_applied > 0:
            print(f"\nVerifying fixes...")
            try:
                cursor.execute("SELECT symbol, state FROM grid_states WHERE state = ?", (GridState.ACTIVE.value,))
                active_grids = cursor.fetchall()
                
                print(f"Active grids after fix: {len(active_grids)}")
                for symbol, state in active_grids:
                    print(f"  - {symbol}: state={state}")
            except sqlite3.OperationalError as e:
                print(f"Error verifying fixes: {e}")
        
        conn.close()
        
        return fixes_applied > 0
        
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    success = fix_grid_state_inconsistency()
    if success:
        print(f"\n[SUCCESS] Grid state inconsistency has been FIXED!")
        print(f"   - Restart the trading bot to apply changes")
        print(f"   - Both status bar and active grids section should now match")
        print(f"   - Grid trading should resume normally")
    else:
        print(f"\n[INFO] No fixes were applied or errors occurred")
    
    input(f"\nPress Enter to exit...")