#!/usr/bin/env python3
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
    print("\n1. Stopping bot...")
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
