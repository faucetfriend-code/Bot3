#!/usr/bin/env python3
"""
Test the full authentication flow with the web interface.
"""

import requests
import time


def test_health_check():
    """Test that the server is running."""
    try:
        response = requests.get("http://localhost:8000/api/health", timeout=5)
        if response.status_code == 200:
            print("[OK] Server is running and healthy")
            return True
        else:
            print(f"[ERROR] Server health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"[ERROR] Cannot connect to server: {e}")
        return False


def test_authentication_flow():
    """Test the complete authentication flow."""

    # Your private key
    private_key = "5i8E7n6iXTqoxhi38YBVJD2SGagiqRBs4HVzpsUUG7gtebcpqnGG6SfJRyWQr9uMYDiffSohWPdRYPwNKEhpw8x2"

    auth_data = {
        "private_key": private_key,
        "device_fingerprint": "test_device_123",
        "account_name": "Test Account",
    }

    try:
        print("Testing authentication endpoint...")
        response = requests.post(
            "http://localhost:8000/api/auth/login",
            json=auth_data,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")

        if response.status_code == 200:
            result = response.json()
            print("[SUCCESS] Authentication successful!")
            print(f"Token: {result.get('access_token', 'N/A')[:20]}...")
            print(f"Agent Wallet: {result.get('agent_wallet', 'N/A')}")
            return result.get("access_token")
        else:
            print(f"[ERROR] Authentication failed: {response.text}")
            return None

    except Exception as e:
        print(f"[ERROR] Authentication request failed: {e}")
        return None


def test_web_interface():
    """Test that the web interface loads."""
    try:
        response = requests.get("http://localhost:8000/", timeout=5)
        if response.status_code == 200 and "Trading Bot" in response.text:
            print("[OK] Web interface loads successfully")
            return True
        else:
            print(f"[ERROR] Web interface failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"[ERROR] Web interface request failed: {e}")
        return False


if __name__ == "__main__":
    print("Testing Full Authentication Flow")
    print("=" * 50)

    # Test server health
    if not test_health_check():
        print("Server is not running. Please start test_server_minimal.py first.")
        exit(1)

    # Test web interface
    test_web_interface()

    # Test authentication
    token = test_authentication_flow()

    if token:
        print("\n[SUCCESS] Full authentication flow is working!")
        print("The private key authentication system is ready for use.")
    else:
        print("\n[FAILED] Authentication flow needs debugging.")
