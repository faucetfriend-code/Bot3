#!/usr/bin/env python3
"""
Test script for the process manager functionality.
"""

import time
import requests
from process_manager import process_manager

def test_process_manager():
    """Test the process manager start/stop functionality."""

    print("Testing Process Manager")
    print("=" * 30)

    # Test 1: Check initial status
    print("\n1. Checking initial server status...")
    status = process_manager.get_server_status()
    print(f"   Running: {status['running']}")
    print(f"   PID: {status['pid']}")
    print(f"   Uptime: {status['uptime']}")
    print(f"   Memory: {status['memory_usage']}")
    print(f"   CPU: {status['cpu_usage']}")

    # Test 2: Try to start server (should fail if already running)
    print("\n2. Attempting to start server...")
    result = process_manager.start_server()
    print(f"   Success: {result['success']}")
    if result['success']:
        print(f"   Message: {result['message']}")
        print(f"   PID: {result['pid']}")
    else:
        print(f"   Error: {result['error']}")

    # Test 3: Check status again
    print("\n3. Checking server status after start attempt...")
    status = process_manager.get_server_status()
    print(f"   Running: {status['running']}")
    print(f"   PID: {status['pid']}")

    # Test 4: Try to stop server
    print("\n4. Attempting to stop server...")
    result = process_manager.stop_server()
    print(f"   Success: {result['success']}")
    if result['success']:
        print(f"   Message: {result['message']}")
    else:
        print(f"   Error: {result['error']}")

    # Test 5: Check final status
    print("\n5. Checking final server status...")
    status = process_manager.get_server_status()
    print(f"   Running: {status['running']}")
    print(f"   PID: {status['pid']}")

    print("\nProcess Manager Test Complete")

def test_api_endpoints():
    """Test the API endpoints for server control."""

    print("\n\nTesting API Endpoints")
    print("=" * 30)

    base_url = "http://localhost:8000"

    # Test 1: Get server status
    print("\n1. Testing /api/server/status endpoint...")
    try:
        response = requests.get(f"{base_url}/api/server/status", timeout=5)
        print(f"   Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   Status: {data.get('status')}")
            print(f"   PID: {data.get('process_id')}")
            print(f"   Uptime: {data.get('uptime')}")
        else:
            print(f"   Response: {response.text}")
    except Exception as e:
        print(f"   Error: {e}")

    # Test 2: Try to start server via API (should require auth)
    print("\n2. Testing /api/server/start endpoint...")
    try:
        response = requests.post(f"{base_url}/api/server/start", timeout=5)
        print(f"   Status Code: {response.status_code}")
        print(f"   Response: {response.text}")
    except Exception as e:
        print(f"   Error: {e}")

    # Test 3: Try to stop server via API (should require auth)
    print("\n3. Testing /api/server/stop endpoint...")
    try:
        response = requests.post(f"{base_url}/api/server/stop", timeout=5)
        print(f"   Status Code: {response.status_code}")
        print(f"   Response: {response.text}")
    except Exception as e:
        print(f"   Error: {e}")

    print("\nAPI Endpoints Test Complete")

if __name__ == "__main__":
    test_process_manager()
    test_api_endpoints()