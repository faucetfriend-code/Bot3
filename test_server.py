import subprocess
import time
import requests

def start_server():
    # Start the server in a subprocess
    return subprocess.Popen([
        "python", "-c",
        "from minimal_api_server import app; import uvicorn; uvicorn.run(app, host='127.0.0.1', port=8000)"
    ])

def test_api_endpoints():
    time.sleep(2)  # Wait for server to start

    # Test health endpoint
    try:
        response = requests.get("http://localhost:8000/api/health", timeout=5)
        print(f"Health endpoint: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Health endpoint failed: {e}")

    # Test status endpoint
    try:
        response = requests.get("http://localhost:8000/api/status", timeout=5)
        print(f"Status endpoint: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Status endpoint failed: {e}")

    # Test root endpoint
    try:
        response = requests.get("http://localhost:8000/", timeout=5)
        print(f"Root endpoint: {response.status_code} - Content length: {len(response.text)}")
    except Exception as e:
        print(f"Root endpoint failed: {e}")

if __name__ == "__main__":
    print("Starting server...")
    server_process = start_server()
    time.sleep(3)  # Wait for server to start

    print("Testing API endpoints...")
    test_api_endpoints()

    print("Stopping server...")
    server_process.terminate()
    server_process.wait()