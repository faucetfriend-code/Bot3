#!/usr/bin/env python3
"""
Test script for the private key authentication endpoint.
"""

import requests
import json


def test_auth_endpoint():
    """Test the authentication endpoint with private key."""

    # Your private key
    private_key = "5i8E7n6iXTqoxhi38YBVJD2SGagiqRBs4HVzpsUUG7gtebcpqnGG6SfJRyWQr9uMYDiffSohWPdRYPwNKEhpw8x2"

    # Test data
    auth_data = {
        "private_key": private_key,
        "device_fingerprint": "test_device_123",
        "account_name": "Test Account",
    }

    try:
        print("Testing authentication endpoint...")
        print(f"Sending private key: {private_key[:20]}...")

        response = requests.post(
            "http://localhost:8000/api/auth/login",
            json=auth_data,
            headers={"Content-Type": "application/json"},
        )

        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")

        if response.status_code == 200:
            result = response.json()
            print("\n[SUCCESS] Authentication successful!")
            print(f"Account Name: {result.get('account_name')}")
            print(f"Agent Wallet: {result.get('agent_wallet', 'N/A')}")
            return True
        else:
            print(f"\n[ERROR] Authentication failed: {response.text}")
            return False

    except requests.exceptions.ConnectionError:
        print("[ERROR] Cannot connect to server. Is the API server running?")
        return False
    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        return False


if __name__ == "__main__":
    print("Testing Private Key Authentication Endpoint")
    print("=" * 50)

    success = test_auth_endpoint()

    if success:
        print("\n[SUCCESS] Private key authentication is working!")
    else:
        print("\n[FAILED] Private key authentication needs debugging.")
