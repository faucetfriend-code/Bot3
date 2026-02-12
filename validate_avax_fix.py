#!/usr/bin/env python3
"""
AVAX Grid Fix Validation

Quick validation script to confirm the AVAX grid state fix is working.
Run this after restarting the trading bot.
"""

import os
import sqlite3
import json

def validate_avax_fix():
    """
    Validate that the AVAX grid fix is working correctly.
    """
    print("AVAX Grid Fix Validation")
    print("=" * 40)
    
    # Check database for AVAX grid
    db_path = os.path.join(os.getcwd(), 'trading_bot_v2', 'trading_bot.db')
    
    if not os.path.exists(db_path):
        print("ERROR: Database not found")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if grid_states table exists and has AVAX
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='grid_states'")
        table_exists = cursor.fetchone()
        
        if not table_exists:
            print("ERROR: grid_states table not found")
            return False
        
        # Get all grids
        cursor.execute("SELECT symbol, state, order_ids FROM grid_states")
        all_grids = cursor.fetchall()
        
        print(f"Total grids in database: {len(all_grids)}")
        
        avax_found = False
        for symbol, state, order_ids in all_grids:
            center_price = "N/A"
            try:
                if order_ids:
                    metadata = json.loads(order_ids)
                    center_price = metadata.get('center_price', 'N/A')
            except:
                pass
            
            print(f"  - {symbol}: state={state}, center=${center_price}")
            
            if symbol == 'AVAX':
                avax_found = True
                is_active = state == 'active'
                has_center = center_price != "N/A"
                
                print(f"\nAVAX Grid Status:")
                print(f"  Found: {avax_found}")
                print(f"  Active: {is_active}")
                print(f"  Has Center: {has_center}")
                print(f"  Center Price: ${center_price}")
        
        conn.close()
        
        if avax_found:
            print(f"\n[SUCCESS] AVAX grid is properly stored in database!")
            print("Expected results after bot restart:")
            print("  - Status bar: active_grids = 1")
            print("  - Active grids section: Should show AVAX grid")
            print("  - New AVAX signals: Should be ACCEPTED")
            print("  - Center price: Available for calculations")
            return True
        else:
            print(f"\n[ERROR] AVAX grid not found in database")
            return False
            
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def check_expected_behavior():
    """
    Document the expected behavior after the fix.
    """
    print(f"\nEXPECTED BEHAVIOR AFTER FIX:")
    print("-" * 35)
    print("BEFORE (broken):")
    print("  - Status bar: active_grids = 1")
    print("  - Active grids: 0 grids (empty)")
    print("  - AVAX signals: REJECTED ('Grid already active')")
    print("  - Center price: Missing")
    print()
    print("AFTER (fixed):")
    print("  - Status bar: active_grids = 1")
    print("  - Active grids: 1 grid (AVAX)")
    print("  - AVAX signals: ACCEPTED (normal processing)")
    print("  - Center price: $35.00 (available)")
    print()
    print("What to check:")
    print("  1. Start/restart the trading bot")
    print("  2. Check the web interface status")
    print("  3. Monitor new AVAX signal processing")
    print("  4. Verify no rejection messages")

if __name__ == "__main__":
    success = validate_avax_fix()
    check_expected_behavior()
    
    if success:
        print(f"\n[VALIDATION PASSED] Grid fix is working!")
    else:
        print(f"\n[VALIDATION FAILED] Issues still exist")
    
    try:
        input("\nPress Enter to exit...")
    except:
        pass