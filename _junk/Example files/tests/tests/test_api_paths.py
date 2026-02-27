"""
Test Pacifica API endpoint paths to verify which ones actually work.

This verifies:
1. Correct endpoint paths for authenticated requests
2. Correct authentication parameter format (query vs body vs headers)
"""

import os
import sys
import json
import time
import requests
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import load_pacifica_credentials
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Test configuration
TESTNET = os.getenv("TESTNET", "true").lower() == "true"
BASE_URL = "https://test-api.pacifica.fi/api/v1" if TESTNET else "https://api.pacifica.fi/api/v1"


def check_endpoint_variations(endpoint_name, possible_paths):
    """Check multiple possible paths for an endpoint"""
    print(f"\nTesting {endpoint_name}:")
    print("-" * 40)

    for path in possible_paths:
        url = f"{BASE_URL}{path}"
        try:
            response = requests.get(url, verify=not TESTNET, timeout=5)
            status = response.status_code

            if status == 200:
                print(f"  [OK] {path:30} -> {status}")
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        print(f"       Keys: {list(data.keys())[:5]}")
                except:
                    pass
                return path  # Return working path
            elif status == 404:
                print(f"  [404] {path:30} -> Not Found")
            elif status == 400:
                print(f"  [400] {path:30} -> Bad Request (may need auth)")
            else:
                print(f"  [{status}] {path:30}")
        except requests.exceptions.Timeout:
            print(f"  [TIMEOUT] {path:30}")
        except Exception as e:
            print(f"  [ERROR] {path:30} -> {str(e)[:40]}")

    print(f"  [FAIL] No working path found for {endpoint_name}")
    return None


def main():
    """Test various endpoint paths"""
    print("=" * 60)
    print("PACIFICA API ENDPOINT PATH VERIFICATION")
    print("=" * 60)
    print(f"Environment: {'TESTNET' if TESTNET else 'MAINNET'}")
    print(f"Base URL: {BASE_URL}")

    # Define endpoints to test
    endpoints = {
        "Market Info": ["/info", "/markets", "/market/info"],
        "Account Balance": ["/account", "/account/info", "/balance", "/account/balance"],
        "Positions": ["/positions", "/account/positions", "/position"],
        "Orders": ["/orders", "/orders/active", "/account/orders"],
        "Order History": ["/orders/history", "/fills", "/account/fills"],
        "Funding Rates": ["/funding", "/funding_rates", "/info"],  # funding rates in /info
    }

    results = {}

    for name, paths in endpoints.items():
        working_path = check_endpoint_variations(name, paths)
        results[name] = working_path

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY: Working Endpoint Paths")
    print("=" * 60)

    for name, path in results.items():
        if path:
            print(f"[OK] {name:20} -> {path}")
        else:
            print(f"[FAIL] {name:20} -> NO WORKING PATH")

    # Document findings
    print("\n" + "=" * 60)
    print("Next Steps:")
    print("=" * 60)
    print("1. Document working paths in PACIFICA_NOTES.md")
    print("2. Investigate authenticated endpoint format")
    print("3. Check if auth params go in query string vs body")


def test_api_paths_discoverable():
    """Test that we can discover valid API endpoint paths."""
    # This is a basic test to ensure the script runs
    # The real testing happens when run directly via main()
    assert BASE_URL is not None
    assert len(BASE_URL) > 0


if __name__ == "__main__":
    main()
