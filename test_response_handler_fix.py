#!/usr/bin/env python3
"""
Test script to validate the fix for the '"success"' error in trading bot execution.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'trading_bot_v2'))

from trading_bot import ResponseHandler

def test_response_handler():
    """Test the ResponseHandler with various response formats."""
    
    print("=== Testing ResponseHandler Fix ===\n")
    
    test_cases = [
        {
            "name": "Normal expected response",
            "response": {"success": True, "data": {"order_id": "12345", "price": 50000.0}},
            "expected_success": True,
            "expected_order_id": "12345"
        },
        {
            "name": "String 'success' response (the bug)",
            "response": '"success"',
            "expected_success": True,
            "expected_order_id": None
        },
        {
            "name": "Plain success string",
            "response": "success",
            "expected_success": True,
            "expected_order_id": None
        },
        {
            "name": "String 'error' response",
            "response": '"error"',
            "expected_success": False,
            "expected_order_id": None
        },
        {
            "name": "Boolean true response",
            "response": True,
            "expected_success": True,
            "expected_order_id": None
        },
        {
            "name": "None response",
            "response": None,
            "expected_success": False,
            "expected_order_id": None
        },
        {
            "name": "Missing success field",
            "response": {"data": {"order_id": "12345"}},
            "expected_success": False,
            "expected_order_id": "12345"
        },
        {
            "name": "Success as string instead of boolean",
            "response": {"success": "success", "data": {"order_id": "12345"}},
            "expected_success": True,
            "expected_order_id": "12345"
        },
        {
            "name": "Error response",
            "response": {"success": False, "error": "API error", "data": {}},
            "expected_success": False,
            "expected_order_id": None
        }
    ]
    
    passed = 0
    failed = 0
    
    for test_case in test_cases:
        print(f"Test: {test_case['name']}")
        print(f"   Input: {test_case['response']} (type: {type(test_case['response']).__name__})")
        
        try:
            result = ResponseHandler.validate_order_response(test_case['response'])
            
            # Check results
            actual_success = result.get('success')
            actual_order_id = result.get('data', {}).get('order_id')
            
            success_match = actual_success == test_case['expected_success']
            order_id_match = actual_order_id == test_case['expected_order_id']
            
            if success_match and order_id_match:
                print(f"   [PASS] success={actual_success}, order_id={actual_order_id}")
                passed += 1
            else:
                print(f"   [FAIL] Expected success={test_case['expected_success']}, got {actual_success}")
                print(f"          Expected order_id={test_case['expected_order_id']}, got {actual_order_id}")
                failed += 1
                
        except Exception as e:
            print(f"   [ERROR] Exception: {e}")
            failed += 1
        
        print()
    
    print(f"=== Results ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total: {passed + failed}")
    
    if failed == 0:
        print("\n[SUCCESS] All tests passed! The fix should resolve the '\"success\"' error.")
    else:
        print(f"\n[WARNING] {failed} test(s) failed. The fix may need adjustment.")
    
    return failed == 0

def test_edge_cases():
    """Test additional edge cases."""
    
    print("\n=== Testing Edge Cases ===\n")
    
    edge_cases = [
        {
            "name": "Empty string",
            "response": "",
            "should_handle": True
        },
        {
            "name": "Invalid JSON string",
            "response": '{"invalid": json}',
            "should_handle": True
        },
        {
            "name": "Number response",
            "response": 200,
            "should_handle": True
        },
        {
            "name": "List response",
            "response": [{"success": True}],
            "should_handle": True
        },
        {
            "name": "Double-encoded JSON",
            "response": '{"success": true, "data": {"order_id": "12345"}}',
            "should_handle": True
        }
    ]
    
    for edge_case in edge_cases:
        print(f"Edge case: {edge_case['name']}")
        print(f"   Input: {edge_case['response']}")
        
        try:
            result = ResponseHandler.validate_order_response(edge_case['response'])
            print(f"   [HANDLED] Result: {result}")
        except Exception as e:
            if edge_case['should_handle']:
                print(f"   [UNHANDLED ERROR] {e}")
            else:
                print(f"   [EXPECTED ERROR] {e}")
        
        print()

if __name__ == "__main__":
    success = test_response_handler()
    test_edge_cases()
    
    if success:
        print("\n" + "="*60)
        print("FIX VALIDATION: SUCCESS")
        print("The ResponseHandler fix should resolve the critical '\"success\"' error.")
        print("Ready to deploy to production.")
        print("="*60)
    else:
        print("\n" + "="*60)
        print("FIX VALIDATION: NEEDS WORK")
        print("Some tests failed. Review and adjust the fix.")
        print("="*60)