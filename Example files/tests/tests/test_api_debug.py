#!/usr/bin/env python3
"""
Debug the Pacifica.fi API to understand the correct request format.
"""

import requests
import json


def test_api_endpoint():
    """Test the basic API endpoint to see what it returns."""

    url = "https://test-api.pacifica.fi/api/v1/agent/bind"

    # Simple test request
    try:
        response = requests.get(url.replace("/agent/bind", "/health"), timeout=10)
        print(f"Health check status: {response.status_code}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"Health check failed: {e}")

    # Test with a simple POST to see what error we get
    try:
        test_payload = {"test": "data"}
        response = requests.post(url, json=test_payload, timeout=10)
        print(f"\nBind endpoint status: {response.status_code}")
        print(f"Response: {response.text}")
        print(f"Request URL: {url}")
        print(f"Request payload: {json.dumps(test_payload, indent=2)}")
    except Exception as e:
        print(f"API test failed: {e}")


def test_signature_format():
    """Test our signature format against what might be expected."""

    from solders.keypair import Keypair
    import time
    import hashlib
    import base64

    # Your private key
    private_key = "5i8E7n6iXTqoxhi38YBVJD2SGagiqRBs4HVzpsUUG7gtebcpqnGG6SfJRyWQr9uMYDiffSohWPdRYPwNKEhpw8x2"

    keypair = Keypair.from_base58_string(private_key)
    account_public_key = str(keypair.pubkey())

    agent_wallet = Keypair()
    agent_wallet_public_key = str(agent_wallet.pubkey())

    # Create signature header
    timestamp = int(time.time() * 1_000)
    signature_header = {
        "timestamp": timestamp,
        "expiry_window": 5_000,
        "type": "bind_agent_wallet",
    }

    # Create signature payload
    signature_payload = {
        "agent_wallet": agent_wallet_public_key,
    }

    # Create message string (our current format)
    message_parts = [
        signature_header["type"],
        str(signature_header["timestamp"]),
        str(signature_header["expiry_window"]),
        str(signature_payload),
    ]
    message = "\n".join(message_parts)

    # Sign the message
    message_bytes = message.encode("utf-8")
    signature = keypair.sign_message(message_bytes)

    print("\nOur signature format:")
    print(f"Message: {message}")
    print(f"Signature: {signature}")
    print(f"Account: {account_public_key}")
    print(f"Agent Wallet: {agent_wallet_public_key}")

    # Try alternative message format (maybe they expect JSON string instead of repr)
    message_json = json.dumps(signature_payload, separators=(",", ":"))
    alt_message = f"{signature_header['type']}\n{timestamp}\n{signature_header['expiry_window']}\n{message_json}"
    alt_signature = keypair.sign_message(alt_message.encode("utf-8"))

    print("\nAlternative format (JSON payload):")
    print(f"Message: {alt_message}")
    print(f"Signature: {alt_signature}")


if __name__ == "__main__":
    print("Debugging Pacifica.fi API Integration")
    print("=" * 50)

    test_api_endpoint()
    test_signature_format()
