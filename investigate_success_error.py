#!/usr/bin/env python3
"""
Targeted investigation of the '"success"' error in trading bot execution.
"""

import json
from typing import Dict, Any

def analyze_error_message():
    """Analyze the specific error message format."""
    
    print("=== Analyzing Error Message ===\n")
    
    # The error shows: '"success"' as the exception message
    error_message = '"success"'
    
    print(f"Error message: {error_message}")
    print(f"Type: {type(error_message)}")
    print(f"Length: {len(error_message)}")
    print(f"First char: '{error_message[0]}'")
    print(f"Last char: '{error_message[-1]}'")
    
    # Try to parse as JSON
    try:
        parsed = json.loads(error_message)
        print(f"Parsed as JSON: {parsed} (type: {type(parsed)})")
    except Exception as e:
        print(f"Failed to parse as JSON: {e}")
    
    print()

def test_api_response_scenarios():
    """Test different API response scenarios that could cause this."""
    
    print("=== Testing API Response Scenarios ===\n")
    
    scenarios = [
        {
            "name": "Expected format",
            "response": '{"success": true, "data": {"order_id": "12345"}}',
            "expected": "dict"
        },
        {
            "name": "String 'success' response", 
            "response": '"success"',
            "expected": "error"
        },
        {
            "name": "Boolean success response",
            "response": "true",
            "expected": "error" 
        },
        {
            "name": "Plain success string",
            "response": "success",
            "expected": "error"
        },
        {
            "name": "Empty object",
            "response": "{}",
            "expected": "dict"
        },
        {
            "name": "Success field missing",
            "response": '{"data": {"order_id": "12345"}}',
            "expected": "dict"
        }
    ]
    
    for scenario in scenarios:
        print(f"Scenario: {scenario['name']}")
        print(f"   Raw response: {scenario['response']}")
        
        try:
            # Simulate response.json() call
            parsed = json.loads(scenario['response'])
            print(f"   Parsed type: {type(parsed)}")
            print(f"   Parsed value: {parsed}")
            
            if isinstance(parsed, dict):
                success_val = parsed.get('success')
                print(f"   success field: {success_val} (type: {type(success_val)})")
                
                # Check if success is a string instead of boolean
                if isinstance(success_val, str):
                    print(f"   [WARNING] success field is string, expected boolean")
            
            # Check if this matches expected outcome
            actual_type = type(parsed).__name__
            if actual_type == scenario['expected']:
                print(f"   [OK] Expected result")
            else:
                print(f"   [ISSUE] Expected {scenario['expected']}, got {actual_type}")
                
        except json.JSONDecodeError as e:
            print(f"   JSON decode error: {e}")
            if scenario['expected'] == "error":
                print(f"   [OK] Expected error")
            else:
                print(f"   [ISSUE] Unexpected error")
        except Exception as e:
            print(f"   Unexpected error: {e}")
        
        print()

def find_root_cause():
    """Try to identify the root cause of the issue."""
    
    print("=== Root Cause Analysis ===\n")
    
    print("Based on the error message '\"success\"', the issue is likely:")
    print("1. The Pacifica API is returning just the string '\"success\"' instead of JSON object")
    print("2. This string is being treated as an exception somewhere in the execution pipeline")
    print("3. The trading bot expects {'success': bool, 'data': {...}} format")
    print()
    
    print("Possible causes:")
    print("a) API endpoint change or bug in Pacifica")
    print("b) Authentication issue causing API to return different response format") 
    print("c) Rate limiting or API error returning unexpected format")
    print("d) Double JSON encoding/decoding issue")
    print("e) Network proxy or middleware modifying the response")
    print()
    
    print("Immediate actions needed:")
    print("1. Add better error handling for unexpected response formats")
    print("2. Log the raw API response before processing")
    print("3. Add response validation to ensure expected format")
    print("4. Check Pacifica API status and recent changes")
    print()

if __name__ == "__main__":
    analyze_error_message()
    test_api_response_scenarios()
    find_root_cause()