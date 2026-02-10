"""
Test Pacifica authentication methods to verify which one actually works.

This test compares:
1. Ed25519 (current implementation in pacifica_client.py)
2. HMAC-SHA256 (documented method in context files/pacifica/authentication.md)
"""

import os
import sys
import json
import time
import hmac
import hashlib
import requests
from typing import Dict, Any, Optional
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pacifica_client import PacificaClient
from auth import load_pacifica_credentials
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Test configuration
TESTNET = os.getenv("TESTNET", "true").lower() == "true"
BASE_URL = "https://test-api.pacifica.fi/api/v1" if TESTNET else "https://api.pacifica.fi/api/v1"


def test_ed25519_auth():
    """Test 1: Ed25519 authentication (current implementation)"""
    print("\n" + "="*60)
    print("TEST 1: Ed25519 Authentication (Current Implementation)")
    print("="*60)

    try:
        # Load credentials first
        credentials = load_pacifica_credentials()
        if not credentials:
            print("[FAIL] Could not load Pacifica credentials")
            return False, "No credentials"

        # Use existing PacificaClient which implements Ed25519
        client = PacificaClient(
            agent_wallet_private_key=credentials["agent_wallet_private_key"],
            account_public_key=credentials["account_public_key"]
        )

        print(f"[OK] PacificaClient initialized")
        print(f"  Account: {client.account_public_key[:12]}...")
        print(f"  Agent Wallet: {client.agent_wallet_public_key[:12]}...")

        # Test authenticated endpoint: get balance
        print("\nTesting authenticated endpoint: GET /account")
        balance_response = client.get_balance()

        if balance_response and "balance" in balance_response:
            print(f"[OK] Ed25519 authentication SUCCESS")
            print(f"  Balance: {balance_response.get('balance', 'N/A')}")
            print(f"  Account Equity: {balance_response.get('account_equity', 'N/A')}")
            return True, balance_response
        else:
            print(f"[FAIL] Ed25519 authentication FAILED")
            print(f"  Response: {balance_response}")
            return False, balance_response

    except Exception as e:
        print(f"[FAIL] Ed25519 authentication ERROR: {e}")
        return False, str(e)


def test_hmac_auth():
    """Test 2: HMAC-SHA256 authentication (documented method)"""
    print("\n" + "="*60)
    print("TEST 2: HMAC-SHA256 Authentication (Documented Method)")
    print("="*60)

    try:
        # Load credentials
        credentials = load_pacifica_credentials()
        if not credentials:
            print("[FAIL] Could not load Pacifica credentials")
            return False, "No credentials"

        account_public_key = credentials["account_public_key"]
        agent_wallet_private_key = credentials["agent_wallet_private_key"]

        print(f"[OK] Credentials loaded")
        print(f"  Account: {account_public_key[:12]}...")

        # HMAC-SHA256 implementation per documentation
        endpoint = "/account"
        method = "GET"
        timestamp = str(int(time.time() * 1000))

        # Create message to sign: method + endpoint + timestamp
        message = f"{method}{endpoint}{timestamp}"

        # Sign with HMAC-SHA256
        signature = hmac.new(
            agent_wallet_private_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        # Headers per documentation
        headers = {
            "Content-Type": "application/json",
            "X-Account": account_public_key,
            "X-Signature": signature,
            "X-Timestamp": timestamp
        }

        print(f"\nTesting authenticated endpoint: GET /account")
        print(f"  Message: {message[:50]}...")
        print(f"  Signature: {signature[:20]}...")

        # Make request
        url = f"{BASE_URL}{endpoint}"
        response = requests.get(url, headers=headers, verify=not TESTNET)

        print(f"  Response Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"[OK] HMAC-SHA256 authentication SUCCESS")
            print(f"  Balance: {data.get('balance', 'N/A')}")
            print(f"  Account Equity: {data.get('account_equity', 'N/A')}")
            return True, data
        else:
            print(f"[FAIL] HMAC-SHA256 authentication FAILED")
            print(f"  Response: {response.text[:200]}")
            return False, response.text

    except Exception as e:
        print(f"[FAIL] HMAC-SHA256 authentication ERROR: {e}")
        return False, str(e)


def test_unauthenticated_endpoint():
    """Test 3: Unauthenticated endpoint (baseline)"""
    print("\n" + "="*60)
    print("TEST 3: Unauthenticated Endpoint (Baseline)")
    print("="*60)

    try:
        # Test public endpoint: get market info
        endpoint = "/info"
        url = f"{BASE_URL}{endpoint}"

        print(f"Testing public endpoint: GET /info")
        response = requests.get(url, verify=not TESTNET)

        print(f"  Response Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            markets = data.get("data", [])
            print(f"[OK] Public endpoint accessible")
            print(f"  Markets found: {len(markets)}")
            return True, data
        else:
            print(f"[FAIL] Public endpoint failed")
            print(f"  Response: {response.text[:200]}")
            return False, response.text

    except Exception as e:
        print(f"[FAIL] Public endpoint ERROR: {e}")
        return False, str(e)


def compare_responses(ed25519_result, hmac_result):
    """Compare responses from both authentication methods"""
    print("\n" + "="*60)
    print("COMPARISON RESULTS")
    print("="*60)

    ed25519_success, ed25519_data = ed25519_result
    hmac_success, hmac_data = hmac_result

    print(f"\nEd25519 (Current):    {'[OK] SUCCESS' if ed25519_success else '[FAIL] FAILED'}")
    print(f"HMAC-SHA256 (Docs):   {'[OK] SUCCESS' if hmac_success else '[FAIL] FAILED'}")

    if ed25519_success and hmac_success:
        print("\n[WARN]  BOTH methods work - verify if responses match")
        if isinstance(ed25519_data, dict) and isinstance(hmac_data, dict):
            if ed25519_data.get("balance") == hmac_data.get("balance"):
                print("[OK] Balance values match")
            else:
                print("[FAIL] Balance values differ")
    elif ed25519_success and not hmac_success:
        print("\n[OK] Current Ed25519 implementation is CORRECT")
        print("   Documentation may be outdated")
    elif hmac_success and not ed25519_success:
        print("\n[WARN]  HMAC-SHA256 works but current implementation doesn't")
        print("   Need to switch to HMAC-SHA256")
    else:
        print("\n[FAIL] BOTH methods failed - check credentials or API status")


def main():
    """Run all authentication tests"""
    print("\n" + "="*60)
    print("PACIFICA AUTHENTICATION METHOD VERIFICATION")
    print("="*60)
    print(f"Environment: {'TESTNET' if TESTNET else 'MAINNET'}")
    print(f"Base URL: {BASE_URL}")

    # Test 1: Ed25519 (current implementation)
    ed25519_result = test_ed25519_auth()

    # Test 2: HMAC-SHA256 (documented method)
    hmac_result = test_hmac_auth()

    # Test 3: Unauthenticated endpoint (baseline)
    public_result = test_unauthenticated_endpoint()

    # Compare results
    compare_responses(ed25519_result, hmac_result)

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)

    ed25519_success = ed25519_result[0]
    hmac_success = hmac_result[0]
    public_success = public_result[0]

    print(f"Public endpoints:     {'[OK] Working' if public_success else '[FAIL] Failed'}")
    print(f"Ed25519 auth:         {'[OK] Working' if ed25519_success else '[FAIL] Failed'}")
    print(f"HMAC-SHA256 auth:     {'[OK] Working' if hmac_success else '[FAIL] Failed'}")

    # Recommendation
    print("\n" + "="*60)
    print("RECOMMENDATION")
    print("="*60)

    if ed25519_success:
        print("[OK] Continue using current Ed25519 implementation")
        print("  No changes needed to authentication code")
    elif hmac_success:
        print("[WARN]  Switch to HMAC-SHA256 authentication")
        print("  Update pacifica_client.py _make_request() method")
    else:
        print("[FAIL] Authentication issue - investigate credentials or API status")

    print("\n" + "="*60)
    print("Next step: Document findings in PACIFICA_NOTES.md")
    print("="*60)


if __name__ == "__main__":
    main()
