#!/usr/bin/env python3
"""
Debug script to investigate the '"success"' error in trading bot execution.
"""

import json
from typing import Dict, Any

def test_response_formats():
    """Test different response formats that might cause the issue."""
    
    print("=== Testing Response Format Scenarios ===\n")
    
    # Test 1: Normal expected response
    print("1. Normal expected response:")
    normal_response = {"success": True, "data": {"order_id": "12345", "price": 50000.0}}
    print(f"   Response: {normal_response}")
    print(f"   Type: {type(normal_response)}")
    print(f"   success value: {normal_response.get('success')} (type: {type(normal_response.get('success'))})")
    print()
    
    # Test 2: Response with string "success" instead of boolean
    print("2. Response with string 'success':")
    string_success_response = {"success": "success", "data": {"order_id": "12345", "price": 50000.0}}
    print(f"   Response: {string_success_response}")
    print(f"   Type: {type(string_success_response)}")
    print(f"   success value: {string_success_response.get('success')} (type: {type(string_success_response.get('success'))})")
    print()
    
    # Test 3: Double-encoded JSON response
    print("3. Double-encoded JSON response:")
    double_encoded = json.dumps({"success": True, "data": {"order_id": "12345", "price": 50000.0}})
    print(f"   Response: {double_encoded}")
    print(f"   Type: {type(double_encoded)}")
    try:
        parsed = json.loads(double_encoded)
        print(f"   After parsing: {parsed}")
        print(f"   success value: {parsed.get('success')} (type: {type(parsed.get('success'))})")
    except Exception as e:
        print(f"   Error parsing: {e}")
    print()
    
    # Test 4: Response that's just the string "success"
    print("4. Response that's just the string 'success':")
    string_only_response = '"success"'
    print(f"   Response: {string_only_response}")
    print(f"   Type: {type(string_only_response)}")
    try:
        if isinstance(string_only_response, str):
            parsed = json.loads(string_only_response)
            print(f"   After parsing: {parsed}")
            print(f"   Type: {type(parsed)}")
    except Exception as e:
        print(f"   Error parsing: {e}")
    print()
    
    # Test 5: Response with nested success field
    print("5. Response with nested success field:")
    nested_response = {"data": {"success": True, "order_id": "12345"}}
    print(f"   Response: {nested_response}")
    print(f"   Type: {type(nested_response)}")
    print(f"   success value: {nested_response.get('success')} (missing)")
    print(f"   data.success value: {nested_response.get('data', {}).get('success')}")
    print()

def test_execution_logic():
    """Test the execution logic from trading_bot.py"""
    
    print("=== Testing Execution Logic ===\n")
    
    # Simulate the execution result processing
    def process_order_response(order_response: Any) -> Dict[str, Any]:
        """Process order response similar to trading_bot.py"""
        
        print(f"   Processing response: {order_response} (type: {type(order_response)})")
        
        # Extract order data from response wrapper {"success": bool, "data": {...}}
        if not isinstance(order_response, dict):
            print(f"   [ERROR] Unexpected order response type: {type(order_response).__name__}")
            execution_result = {
                "success": False,
                "order_id": None,
                "executed_price": 50000.0,
                "error": f"Unexpected response type: {type(order_response).__name__}",
            }
        else:
            order_data = order_response.get("data", {})
            if not isinstance(order_data, dict):
                order_data = {}
            order_id = order_data.get("order_id") or order_data.get("id")

            # Convert to execution result format
            execution_result = {
                "success": order_response.get("success", False) and order_id is not None,
                "order_id": order_id,
                "executed_price": order_data.get("price", 50000.0),
                "error": order_response.get("error") if not order_response.get("success") else None,
            }
        
        print(f"   Execution result: {execution_result}")
        return execution_result
    
    # Test with different response formats
    test_cases = [
        {"name": "Normal response", "response": {"success": True, "data": {"order_id": "12345", "price": 50000.0}}},
        {"name": "String success", "response": {"success": "success", "data": {"order_id": "12345", "price": 50000.0}}},
        {"name": "Missing success", "response": {"data": {"order_id": "12345", "price": 50000.0}}},
        {"name": "String response", "response": '"success"'},
        {"name": "None response", "response": None},
    ]
    
    for test_case in test_cases:
        print(f"{test_case['name']}:")
        try:
            result = process_order_response(test_case['response'])
            print(f"   [OK] Success: {result.get('success')}")
        except Exception as e:
            print(f"   [ERROR] Error: {e} (type: {type(e).__name__})")
        print()

if __name__ == "__main__":
    test_response_formats()
    test_execution_logic()