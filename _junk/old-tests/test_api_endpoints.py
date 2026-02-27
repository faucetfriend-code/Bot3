#!/usr/bin/env python3
"""
API Endpoint Diagnostic Script
Tests all web interface endpoints to ensure they work correctly.
"""

import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000"

def test_endpoint(name, method, endpoint, data=None, expected_status=200):
    """Test a single API endpoint."""
    try:
        url = f"{BASE_URL}{endpoint}"
        print(f"Testing {name}: {method} {url}")

        if method == "GET":
            response = requests.get(url, timeout=10)
        elif method == "POST":
            headers = {"Content-Type": "application/json"} if data else {}
            response = requests.post(url, json=data, headers=headers, timeout=10)
        else:
            print(f"❌ Unsupported method: {method}")
            return False

        if response.status_code == expected_status:
            try:
                result = response.json()
                print(f"✅ {name}: Status {response.status_code}")
                if "success" in result:
                    print(f"   Success: {result['success']}")
                if "message" in result:
                    print(f"   Message: {result['message']}")
                return True
            except json.JSONDecodeError:
                print(f"✅ {name}: Status {response.status_code} (non-JSON response)")
                return True
        else:
            print(f"❌ {name}: Status {response.status_code}")
            print(f"   Response: {response.text[:200]}...")
            return False

    except requests.exceptions.RequestException as e:
        print(f"❌ {name}: Connection failed - {e}")
        return False
    except Exception as e:
        print(f"❌ {name}: Unexpected error - {e}")
        return False

def main():
    """Run all API endpoint tests."""
    print("🔍 API Endpoint Diagnostic Tool")
    print("=" * 50)

    # Test 1: Check if server is running
    print("\n1. Testing server availability...")
    try:
        response = requests.get(f"{BASE_URL}/", timeout=5)
        if response.status_code == 200 and "Trading Bot" in response.text:
            print("✅ Server is running and serving interface")
        else:
            print("❌ Server response unexpected")
            return False
    except Exception as e:
        print(f"❌ Cannot connect to server: {e}")
        print("💡 Make sure to run: python api_server.py")
        return False

    # Test 2: Status endpoint
    print("\n2. Testing API endpoints...")
    tests = [
        ("Status Endpoint", "GET", "/api/status"),
        ("Start Bot", "POST", "/api/bot/start"),
        ("Stop Bot", "POST", "/api/bot/stop"),
        ("Positions", "GET", "/api/positions"),
        ("Trades", "GET", "/api/trades"),
        ("Markets", "GET", "/api/markets"),
    ]

    passed = 0
    total = len(tests)

    for name, method, endpoint in tests:
        if test_endpoint(name, method, endpoint):
            passed += 1
        print()

    # Test 3: Stop bot if it was started
    print("3. Ensuring bot is stopped...")
    test_endpoint("Stop Bot (cleanup)", "POST", "/api/bot/stop")

    print("=" * 50)
    print(f"📊 Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All API endpoints are working correctly!")
        print("\n💡 The web interface should now work properly.")
        print("   Open http://localhost:8000 in your browser.")
        return True
    else:
        print("❌ Some API endpoints are not working.")
        print("\n🔧 Troubleshooting:")
        print("   1. Check that api_server.py is running")
        print("   2. Check the server logs for errors")
        print("   3. Verify CORS settings")
        print("   4. Check bot initialization")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)