#!/usr/bin/env python3
"""
Simple test for ResponseHandler to check if it handles 'success' responses correctly.
"""

import sys
from pathlib import Path

# Add the trading_bot_v2 directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "trading_bot_v2"))

# Change to the trading_bot_v2 directory to avoid relative import issues
import os
os.chdir(Path(__file__).parent / "trading_bot_v2")

from trading_bot import ResponseHandler

def test_response_handler():
    """Test ResponseHandler with various success responses."""
    
    print("Testing ResponseHandler...")
    
    test_cases = [
        '"success"',  # The problematic response from the logs
        'success',
        '{"success": true, "data": {"order_id": "12345"}}',  # Expected JSON
        True,  # Boolean response
        {'success': True, 'data': {'order_id': '12345'}},  # Dict response
    ]
    
    for i, test_response in enumerate(test_cases):
        print(f"\nTest case {i+1}: {test_response} (type: {type(test_response)})")
        try:
            result = ResponseHandler.validate_order_response(test_response)
            print(f"✅ Result: {result}")
            print(f"   Success: {result.get('success')}")
            print(f"   Data: {result.get('data')}")
            print(f"   Error: {result.get('error')}")
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            print(f"   Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    test_response_handler()