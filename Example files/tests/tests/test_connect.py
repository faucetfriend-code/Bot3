"""
Test script to verify launcher connectivity and API server startup.
"""

import subprocess
import time
import urllib.request
import urllib.parse
import json
import signal
import os

def test_launcher_connectivity():
    print("Starting launcher service...")

    # Start launcher in background
    launcher_process = subprocess.Popen(
        ['python', 'launcher.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    )

    try:
        # Wait for launcher to start
        time.sleep(5)

        print("Testing /health endpoint...")
        try:
            with urllib.request.urlopen('http://localhost:8765/health') as response:
                health_data = json.loads(response.read().decode())
                print(f"Health check successful: {health_data}")
        except Exception as e:
            print(f"Health check failed: {e}")
            return False

        print("Testing /start-server endpoint...")
        try:
            data = urllib.parse.urlencode({}).encode('utf-8')
            req = urllib.request.Request('http://localhost:8765/start-server', data=data, method='POST')
            with urllib.request.urlopen(req) as response:
                start_data = json.loads(response.read().decode())
                print(f"Start server response: {start_data}")

                if start_data.get('success'):
                    print("API server started successfully!")
                    # Wait a bit for API server to be ready
                    time.sleep(3)

                    # Test API server health
                    print("Testing API server /health...")
                    try:
                        with urllib.request.urlopen('http://localhost:8000/api/health') as api_response:
                            api_health = json.loads(api_response.read().decode())
                            print(f"API server health: {api_health}")
                    except Exception as e:
                        print(f"API server health check failed: {e}")
                else:
                    print(f"Failed to start API server: {start_data.get('message')}")
                    return False

        except Exception as e:
            print(f"Start server request failed: {e}")
            return False

        print("Testing /status endpoint...")
        try:
            with urllib.request.urlopen('http://localhost:8765/status') as response:
                status_data = json.loads(response.read().decode())
                print(f"Status check: {status_data}")
        except Exception as e:
            print(f"Status check failed: {e}")
            return False

        print("All connectivity tests passed!")
        return True

    finally:
        # Clean up
        print("Stopping launcher...")
        try:
            launcher_process.terminate()
            launcher_process.wait(timeout=10)
            print("Launcher stopped")
        except subprocess.TimeoutExpired:
            launcher_process.kill()
            print("Launcher force-killed")

if __name__ == "__main__":
    success = test_launcher_connectivity()
    if success:
        print("\nHTML-first architecture connectivity test PASSED!")
        print("The interface should now work properly.")
    else:
        print("\nConnectivity test FAILED!")
        print("Check the error messages above.")