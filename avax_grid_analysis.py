#!/usr/bin/env python3
"""
AVAX Grid Issue Analysis and Fix

Based on signal log analysis, AVAX has an active grid but no center price.
This causes signals to be rejected while interface shows 0 active grids.

The issue is in the grid lifecycle manager state.
"""

import os
import sys
from datetime import datetime

def analyze_avax_grid_issue():
    """Analyze the AVAX grid issue from signal logs."""
    
    print("="*60)
    print("AVAX GRID ISSUE ANALYSIS")
    print("="*60)
    
    print("\n1. PROBLEM IDENTIFICATION:")
    print("   - AVAX signals are being generated")
    print("   - AVAX signals are being rejected with 'Active grid has no center price'")
    print("   - Interface shows 0 active grids")
    print("   - This indicates a broken grid state")
    
    print("\n2. EVIDENCE FROM SIGNAL LOG:")
    print("   Recent AVAX rejections:")
    print("   - 2026-02-11T23:35:24: AVAX GRID_TRADING rejected - 'Active grid has no center price'")
    print("   - 2026-02-11T23:35:56: AVAX GRID_TRADING rejected - 'Active grid has no center price'")
    print("   - 2026-02-11T23:36:29: AVAX GRID_TRADING rejected - 'Active grid has no center price'")
    print("   - Multiple similar rejections every ~30 seconds")
    
    print("\n3. ROOT CAUSE:")
    print("   - GridLifecycleManager has an active grid for AVAX")
    print("   - But the grid is missing 'center_price' or 'initial_center' field")
    print("   - get_grid_center('AVAX') returns None")
    print("   - This triggers rejection in trading_bot.py line 1697")
    print("   - But get_all_active_grids() returns empty (not in proper ACTIVE state)")
    
    print("\n4. WHY INTERFACE SHOWS 0 GRIDS:")
    print("   - Interface calls get_all_active_grids() which filters by GridState.ACTIVE")
    print("   - The broken AVAX grid exists but is not in proper ACTIVE state")
    print("   - So interface shows 0 while rejection logic sees the grid")
    
    print("\n5. SOLUTION OPTIONS:")
    print("   Option A: Fix the broken grid by adding center price")
    print("   Option B: Remove the broken grid entirely")
    print("   Option C: Restart the bot (clears all grid state)")
    
    return True

def provide_fix_instructions():
    """Provide instructions for fixing the issue."""
    
    print("\n" + "="*60)
    print("FIX INSTRUCTIONS")
    print("="*60)
    
    print("\nIMMEDIATE FIXES:")
    print("\n1. RESTART THE BOT (Recommended):")
    print("   - Stop the trading bot")
    print("   - Clear any grid state in memory")
    print("   - Start the bot fresh")
    print("   - This will remove the broken AVAX grid")
    
    print("\n2. MANUAL GRID CLEAR:")
    print("   - Access the bot's API endpoint")
    print("   - Call POST /api/grids/clear-all")
    print("   - This will clear all grid state")
    
    print("\n3. INTERFACE FIX (Already Done):")
    print("   - Signal filtering now excludes 'generated' status")
    print("   - Grid count display improved")
    print("   - Active grids display enhanced")
    
    print("\nPREVENTION:")
    print("\n1. BETTER GRID VALIDATION:")
    print("   - Ensure grid creation always sets center_price")
    print("   - Add validation in GridLifecycleManager")
    print("   - Check for missing fields before activation")
    
    print("\n2. IMPROVED ERROR HANDLING:")
    print("   - Better logging for grid state issues")
    print("   - Graceful fallback for broken grids")
    print("   - Automatic cleanup of invalid grid state")
    
    print("\n3. STATE CONSISTENCY:")
    print("   - Ensure get_all_active_grids() and has_active_grid() are consistent")
    print("   - Add validation for grid state integrity")
    print("   - Regular health checks for grid manager")

def create_restart_script():
    """Create a script to safely restart the bot and clear grid state."""
    
    script_content = '''#!/usr/bin/env python3
"""
Bot Restart and Grid Clear Script

Safely restart the trading bot and clear broken grid state.
"""

import requests
import time
import sys

def check_api_server():
    """Check if API server is running."""
    try:
        response = requests.get("http://localhost:8000/api/status", timeout=5)
        return response.status_code == 200
    except:
        return False

def stop_bot():
    """Stop the trading bot via API."""
    try:
        response = requests.post("http://localhost:8000/api/bot/stop", timeout=10)
        if response.status_code == 200:
            print("Bot stop command sent successfully")
            return True
        else:
            print(f"Failed to stop bot: {response.status_code}")
            return False
    except Exception as e:
        print(f"Error stopping bot: {e}")
        return False

def clear_grids():
    """Clear all grid state."""
    try:
        response = requests.post("http://localhost:8000/api/grids/clear-all", timeout=10)
        if response.status_code == 200:
            print("Grid state cleared successfully")
            return True
        else:
            print(f"Failed to clear grids: {response.status_code}")
            return False
    except Exception as e:
        print(f"Error clearing grids: {e}")
        return False

def start_bot():
    """Start the trading bot via API."""
    try:
        response = requests.post("http://localhost:8000/api/bot/start", timeout=10)
        if response.status_code == 200:
            print("Bot start command sent successfully")
            return True
        else:
            print(f"Failed to start bot: {response.status_code}")
            return False
    except Exception as e:
        print(f"Error starting bot: {e}")
        return False

def main():
    print("Bot Restart and Grid Clear Script")
    print("="*40)
    
    # Check API server
    if not check_api_server():
        print("ERROR: API server not running on localhost:8000")
        print("Please start the API server first")
        sys.exit(1)
    
    print("API server is running")
    
    # Stop bot
    print("\\n1. Stopping bot...")
    if not stop_bot():
        print("WARNING: Could not stop bot gracefully")
    
    # Wait for stop
    print("2. Waiting for bot to stop...")
    time.sleep(5)
    
    # Clear grids
    print("3. Clearing grid state...")
    clear_grids()
    
    # Wait
    print("4. Waiting before restart...")
    time.sleep(3)
    
    # Start bot
    print("5. Starting bot...")
    if start_bot():
        print("6. Bot restart completed successfully")
        print("   AVAX grid issue should be resolved")
    else:
        print("ERROR: Failed to start bot")

if __name__ == "__main__":
    main()
'''
    
    with open("restart_bot_clear_grids.py", "w") as f:
        f.write(script_content)
    
    print("\nCreated restart_bot_clear_grids.py")
    print("Run this script to safely restart the bot and clear the AVAX grid issue")

def main():
    """Main analysis function."""
    
    # Analyze the issue
    analyze_avax_grid_issue()
    
    # Provide fix instructions
    provide_fix_instructions()
    
    # Create restart script
    create_restart_script()
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print("\nThe AVAX grid issue is caused by a broken grid state:")
    print("- Grid exists but missing center_price field")
    print("- Causes signal rejections")
    print("- Interface shows 0 grids due to state inconsistency")
    print("\nRECOMMENDED SOLUTION:")
    print("1. Run: python restart_bot_clear_grids.py")
    print("2. This will restart the bot and clear the broken grid")
    print("3. AVAX signals should work normally after restart")

if __name__ == "__main__":
    main()