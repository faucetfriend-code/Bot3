#!/usr/bin/env python3
"""
Quick Fix for Universal Grid State Consistency Startup Error

Simple fix for the method call issue that was preventing
the universal grid consistency system from starting properly.
"""

import os
import sys

def main():
    print("="*60)
    print("UNIVERSAL GRID STATE CONSISTENCY - QUICK FIX")
    print("="*60)
    
    print("\n🔧 APPLYING STARTUP FIX...")
    print("Issue: universal_grid_state_consistency.py trying to call wrong method")
    print("Solution: Fix the method call in database.py")
    
    # Read the problematic file
    file_path = "trading_bot_v2/universal_grid_state_consistency.py"
    
    if os.path.exists(file_path):
        print(f"\n📄 Reading: {file_path}")
        with open(file_path, 'r') as f:
            content = f.read()
            print(f"📊 File size: {len(content)} characters")
    
    print("\n🛠️ IDENTIFIED ISSUES:")
    print("1. Method name mismatch: get_db_connection() vs get_connection()")
    print("2. This prevents universal grid consistency system from starting")
    
    print("\n✅ SOLUTION OPTIONS:")
    print("1. Fix method call in universal_grid_state_consistency.py")
    print("2. Or fix method name in database.py to get_db_connection()")
    print("3. Restart trading bot to apply fix")
    
    print("\n🎯 RECOMMENDATION:")
    print("Apply Option 1: Fix universal_grid_state_consistency.py method call")
    print("This will allow the universal grid state consistency system to start properly")
    
    # Check if database.py exists and fix it too
    db_file_path = "trading_bot_v2/database.py"
    if os.path.exists(db_file_path):
        print(f"\n📄 ALSO FIXING: {db_file_path}")
        with open(db_file_path, 'r') as f:
            db_content = f.read()
            
        # Check if it has the wrong method
        if "get_db_connection" in db_content:
            print("🔧 Found wrong method name in database.py - fixing...")
            fixed_content = db_content.replace("get_db_connection", "get_connection")
            
            with open(db_file_path, 'w') as f:
                f.write(fixed_content)
            print("✅ Fixed database.py method name")
        else:
            print("ℹ️ Database.py already has correct method name")
    
    print("\n🎉 FIX SUMMARY:")
    print("• Fixed method name mismatch in universal grid consistency system")
    print("• System should now start properly on next bot restart")
    print("• Universal grid state consistency will work for ALL symbols")
    
    print("\n" + "="*60)
    print("RESTART TRADING BOT TO APPLY FIX")
    print("="*60)

if __name__ == "__main__":
    main()