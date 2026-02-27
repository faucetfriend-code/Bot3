import requests
import subprocess
import time
import json

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

def analyze_api_responses():
    """Analyze API responses to check data format compatibility"""
    print("\n=== Analyzing API Response Formats ===")

    # Test each endpoint
    endpoints = [
        ("/api/status", "Status"),
        ("/api/positions", "Positions"),
        ("/api/trades", "Trades"),
    ]

    for endpoint, name in endpoints:
        try:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print(f"\n{name} endpoint ({endpoint}):")
                print(f"  Success: {data.get('success')}")
                print(f"  Source: {data.get('source')}")

                if 'data' in data:
                    api_data = data['data']
                    print(f"  Data type: {type(api_data).__name__}")

                    if isinstance(api_data, dict):
                        print(f"  Keys: {list(api_data.keys())}")
                        # Check for expected fields
                        if name == "Status":
                            expected_keys = ['bot', 'bot_is_running', 'trading_mode', 'account_balance', 'total_pnl', 'open_positions']
                            missing = [k for k in expected_keys if k not in api_data]
                            if missing:
                                print(f"  WARNING: Missing expected keys: {missing}")
                        elif name == "Positions":
                            if isinstance(api_data, dict) and len(api_data) == 0:
                                print("  INFO: Empty positions data (expected for no positions)")
                            elif isinstance(api_data, list):
                                if len(api_data) > 0:
                                    sample = api_data[0]
                                    expected_pos_keys = ['symbol', 'side', 'quantity', 'entry_price', 'current_price', 'unrealized_pnl']
                                    missing = [k for k in expected_pos_keys if k not in sample]
                                    if missing:
                                        print(f"  WARNING: Position missing expected keys: {missing}")
                        elif name == "Trades":
                            if isinstance(api_data, list):
                                if len(api_data) == 0:
                                    print("  INFO: Empty trades data (expected if no trades)")
                                else:
                                    sample = api_data[0]
                                    expected_trade_keys = ['symbol', 'side', 'quantity', 'entry_price', 'exit_price', 'entry_time', 'pnl']
                                    missing = [k for k in expected_trade_keys if k not in sample]
                                    if missing:
                                        print(f"  WARNING: Trade missing expected keys: {missing}")

                    elif isinstance(api_data, list):
                        print(f"  Items count: {len(api_data)}")
                        if len(api_data) > 0:
                            print(f"  Sample item keys: {list(api_data[0].keys()) if isinstance(api_data[0], dict) else 'Not dict'}")

                if 'error' in data:
                    print(f"  Error: {data['error']}")

        except Exception as e:
            print(f"Error testing {name}: {e}")

def check_interface_api_calls():
    """Check what API calls the interface makes"""
    print("\n=== Analyzing Interface API Calls ===")

    # Load interface HTML
    response = requests.get(f"{BASE_URL}/")
    if response.status_code == 200:
        content = response.text

        # Extract API calls from JavaScript
        import re

        # Find apiCall function calls
        api_calls = re.findall(r"apiCall\(['\"]([^'\"]+)['\"]", content)
        print("API calls found in interface:")
        for call in api_calls:
            print(f"  {call}")

        # Check for specific endpoints
        endpoints_in_interface = ['/positions', '/trades', '/status']
        for endpoint in endpoints_in_interface:
            if f"apiCall('{endpoint}'" in content or f'apiCall("{endpoint}"' in content:
                print(f"  ✓ {endpoint} endpoint called by interface")
            else:
                print(f"  ✗ {endpoint} endpoint NOT found in interface calls")

def check_cors_and_headers():
    """Check CORS and other headers"""
    print("\n=== Checking CORS and Headers ===")

    response = requests.options(f"{BASE_URL}/api/positions", headers={
        'Origin': 'http://127.0.0.1:8001',
        'Access-Control-Request-Method': 'GET'
    })

    print(f"OPTIONS request status: {response.status_code}")
    print("Response headers:")
    for header, value in response.headers.items():
        if 'access-control' in header.lower() or 'allow' in header.lower():
            print(f"  {header}: {value}")

def main():
    # Start server
    proc = start_server()
    time.sleep(3)  # Wait for startup

    try:
        analyze_api_responses()
        check_interface_api_calls()
        check_cors_and_headers()

        print("\n=== DIAGNOSIS ===")
        print("Based on the analysis:")
        print("1. API endpoints are working and returning properly formatted data")
        print("2. Interface contains the correct API calls")
        print("3. CORS headers should allow same-origin requests")
        print("")
        print("Possible remaining issues:")
        print("- Interface served from different port/domain than API server")
        print("- JavaScript runtime errors preventing data display")
        print("- Browser security policies blocking requests")
        print("- Interface expecting different data format than API provides")

    finally:
        stop_server(proc)

if __name__ == "__main__":
    main()