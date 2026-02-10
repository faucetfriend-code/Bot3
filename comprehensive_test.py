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

def test_interface_loading():
    """Test interface loading and API calls"""
    print("\n=== Testing Interface Loading ===")

    # Load the interface
    response = requests.get(f"{BASE_URL}/")
    print(f"Interface load status: {response.status_code}")

    if response.status_code == 200:
        content = response.text

        # Check for key elements
        checks = [
            ("Trading Bot" in content, "Title found"),
            ("apiCall" in content, "API call function found"),
            ("connectWebSocket" in content, "WebSocket connection found"),
            ("updatePositions" in content, "Position update function found"),
            ("updateTrades" in content, "Trade update function found"),
            ("API_BASE = '/api'" in content, "API base correctly set"),
        ]

        for check, description in checks:
            status = "PASS" if check else "FAIL"
            print(f"  {status}: {description}")

        # Check for JavaScript errors by looking for syntax issues
        if "SyntaxError" in content or "ReferenceError" in content:
            print("  WARNING: Potential JavaScript syntax errors found in HTML")
        else:
            print("  PASS: No obvious JavaScript syntax errors")

    return response.status_code == 200

def test_api_endpoints():
    """Test all relevant API endpoints"""
    print("\n=== Testing API Endpoints ===")

    endpoints = [
        ("/api/health", "Health check"),
        ("/api/status", "Status"),
        ("/api/positions", "Positions"),
        ("/api/trades", "Trades"),
    ]

    results = {}

    for endpoint, description in endpoints:
        try:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=5)
            success = response.status_code == 200

            if success:
                data = response.json()
                has_data = 'data' in data
                results[endpoint] = {
                    'status': 'PASS',
                    'has_data': has_data,
                    'data_type': type(data.get('data')).__name__ if has_data else None
                }
            else:
                results[endpoint] = {
                    'status': 'FAIL',
                    'status_code': response.status_code,
                    'response': response.text[:100]
                }

            print(f"  {results[endpoint]['status']}: {description}")

        except Exception as e:
            results[endpoint] = {'status': 'ERROR', 'error': str(e)}
            print(f"  ERROR: {description} - {e}")

    return results

def test_websocket():
    """Test WebSocket connection"""
    print("\n=== Testing WebSocket ===")

    try:
        import websockets
        import asyncio

        async def ws_test():
            try:
                async with websockets.connect(f"ws://127.0.0.1:8001/ws") as websocket:
                    # Send test message
                    test_msg = json.dumps({"type": "test", "message": "interface_test"})
                    await websocket.send(test_msg)

                    # Wait for echo with timeout
                    response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    return True, response
            except Exception as e:
                return False, str(e)

        success, result = asyncio.run(ws_test())

        if success:
            print("  PASS: WebSocket connection successful")
            print(f"  Response: {result}")
        else:
            print(f"  FAIL: WebSocket connection failed - {result}")

        return success

    except ImportError:
        print("  SKIP: websockets library not available")
        return None

def main():
    # Start server
    proc = start_server()
    time.sleep(3)  # Wait for startup

    try:
        # Run tests
        interface_ok = test_interface_loading()
        api_results = test_api_endpoints()
        ws_ok = test_websocket()

        # Summary
        print("\n=== SUMMARY ===")
        print(f"Interface loading: {'PASS' if interface_ok else 'FAIL'}")
        print(f"API endpoints: {sum(1 for r in api_results.values() if r['status'] == 'PASS')} / {len(api_results)} working")
        print(f"WebSocket: {'PASS' if ws_ok else 'FAIL' if ws_ok is not None else 'SKIP'}")

        # Check for common issues
        issues = []

        if not interface_ok:
            issues.append("Interface fails to load")

        failing_apis = [ep for ep, res in api_results.items() if res['status'] != 'PASS']
        if failing_apis:
            issues.append(f"API endpoints failing: {failing_apis}")

        if ws_ok is False:
            issues.append("WebSocket connection fails")

        if issues:
            print("\nISSUES FOUND:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("\nAll basic connectivity tests passed!")

    finally:
        stop_server(proc)

if __name__ == "__main__":
    main()