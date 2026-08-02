"""
Test Pacifica API connectivity and authentication.

This script tests:
1. API client initialization
2. Public endpoint access (get_markets)
3. Authenticated endpoint access (get_balance, get_positions)
4. Error handling and timeouts
"""

import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from .pacifica_client import PacificaClient
from .config import config


def test_api_connectivity():
    """Test Pacifica API connectivity."""
    print("=" * 60)
    print("Pacifica API Connectivity Test")
    print("=" * 60)

    # Test 1: Configuration
    print("\n[1/5] Testing configuration...")
    try:
        config.validate()
        print(f"[PASS] Configuration valid")
        print(f"       Testnet: {config.testnet}")
        print(f"       Public Key: {config.pacifica_public_key[:10]}...")
        print(f"       Max Positions: {config.max_positions}")
        print(f"       Default Leverage: {config.default_leverage}x")
    except Exception as e:
        print(f"[FAIL] Configuration error: {e}")
        return False

    # Test 2: Client initialization
    print("\n[2/5] Testing client initialization...")
    try:
        client = PacificaClient(
            config.pacifica_private_key, config.pacifica_public_key, config.testnet
        )
        print(f"[PASS] Client initialized")
        print(f"       Base URL: {client.base_url}")
        print(f"       Agent Wallet: {client.agent_wallet_public_key[:10]}...")
    except Exception as e:
        print(f"[FAIL] Client initialization failed: {e}")
        return False

    # Test 3: Public endpoint (no auth required)
    print("\n[3/5] Testing public endpoint (get_markets)...")
    try:
        markets = client.get_markets()
        print(f"[PASS] Retrieved {len(markets)} markets")
        if markets:
            print(f"       First market: {markets[0].get('symbol', 'N/A')}")
            if len(markets) >= 3:
                print(
                    f"       Sample markets: {', '.join([m.get('symbol', 'N/A') for m in markets[:3]])}"
                )
    except Exception as e:
        print(f"[FAIL] get_markets failed: {e}")
        print(f"       This might indicate network issues or incorrect API URL")
        return False

    # Test 4: Authenticated endpoint (get_balance)
    print("\n[4/5] Testing authenticated endpoint (get_balance)...")
    try:
        balance = client.get_balance()
        print(f"[PASS] Balance retrieved successfully")
        print(f"       Response keys: {list(balance.keys())}")
        if "balance" in balance:
            print(f"       Balance: ${balance['balance']}")
        elif "equity" in balance:
            print(f"       Equity: ${balance['equity']}")
    except Exception as e:
        print(f"[FAIL] get_balance failed: {e}")
        print(f"       This might indicate:")
        print(f"       - Invalid API keys")
        print(f"       - Incorrect signature")
        print(f"       - Account not found on testnet")
        return False

    # Test 5: Authenticated endpoint (get_positions)
    print("\n[5/5] Testing authenticated endpoint (get_positions)...")
    try:
        positions = client.get_positions()
        print(f"[PASS] Positions retrieved successfully")
        print(f"       Open positions: {len(positions)}")
        if positions:
            for i, pos in enumerate(positions[:3], 1):
                print(
                    f"       Position {i}: {pos.get('symbol', 'N/A')} - {pos.get('side', 'N/A')}"
                )
    except Exception as e:
        print(f"[FAIL] get_positions failed: {e}")
        return False

    # Summary
    print("\n" + "=" * 60)
    print("API Connectivity Test Summary")
    print("=" * 60)
    print("[+] Configuration: VALID")
    print("[+] Client initialization: SUCCESS")
    print("[+] Public endpoints: ACCESSIBLE")
    print("[+] Authentication: WORKING")
    print("[+] API connectivity: CONFIRMED")
    print("\n[SUCCESS] All connectivity tests passed!")
    print("=" * 60)
    print("\nYour bot is ready for testnet trading!")
    print("Next steps:")
    print("  1. Start the API server: python api_server.py")
    print("  2. Open http://localhost:8000 in your browser")
    print("  3. Click 'Start Bot' to begin trading")
    print("=" * 60)

    return True


if __name__ == "__main__":
    try:
        success = test_api_connectivity()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
