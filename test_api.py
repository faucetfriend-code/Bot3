import requests
import json
import subprocess
import time

BASE_URL = "http://127.0.0.1:8001"

def start_server():
    """Start the server in background"""
    print('Starting server...')
    return subprocess.Popen(['python', '-c', 'import uvicorn; uvicorn.run("trading_bot_v2.api_server:app", host="127.0.0.1", port=8001)'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def stop_server(proc):
    """Stop the server"""
    if proc:
        proc.terminate()
        proc.wait()

def test_endpoint(endpoint, description):
    try:
        print(f"\n=== Testing {description} ===")
        response = requests.get(f"{BASE_URL}{endpoint}", timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Success: {data.get('success', 'N/A')}")
            if 'data' in data:
                print(f"Data type: {type(data['data'])}")
                if isinstance(data['data'], list):
                    print(f"Items count: {len(data['data'])}")
                    if len(data['data']) > 0:
                        print(f"Sample item keys: {list(data['data'][0].keys()) if isinstance(data['data'][0], dict) else 'Not dict'}")
                elif isinstance(data['data'], dict):
                    print(f"Keys: {list(data['data'].keys())}")
            print(f"Source: {data.get('source', 'N/A')}")
            if 'error' in data:
                print(f"Error: {data['error']}")
        else:
            print(f"Response: {response.text[:500]}")
    except Exception as e:
        print(f"Error: {e}")

# Start server
proc = start_server()
time.sleep(5)  # Wait for startup

try:
    # Test endpoints
    test_endpoint("/api/health", "Health Check")
    test_endpoint("/api/status", "Status")
    test_endpoint("/api/positions", "Positions")
    test_endpoint("/api/trades", "Trades")

    # Test interface loading
    print("\n=== Testing Interface Loading ===")
    try:
        response = requests.get("http://127.0.0.1:8001/", timeout=10)
        print(f"Interface status: {response.status_code}")
        if response.status_code == 200:
            content = response.text
            if "Trading Bot" in content:
                print("✓ Interface loads successfully")
            else:
                print("✗ Interface content missing expected text")
            if "apiCall" in content:
                print("✓ JavaScript API calls found in interface")
            else:
                print("✗ JavaScript API calls not found")
        else:
            print(f"Interface response: {response.text[:200]}")
    except Exception as e:
        print(f"Interface error: {e}")

finally:
    stop_server(proc)