#!/usr/bin/env python3
"""
Test script for CCXT integration.
Tests basic functionality without requiring real API keys.
"""

import asyncio
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from ccxt_adapter import CCXTExchangeAdapter, get_available_exchanges, is_exchange_supported
    CCXT_AVAILABLE = True
except ImportError as e:
    print(f"CCXT not available: {e}")
    CCXT_AVAILABLE = False

async def test_ccxt_basic():
    """Test basic CCXT functionality."""
    if not CCXT_AVAILABLE:
        print("FAIL: CCXT not available")
        return False

    print("Testing CCXT basic functionality...")

    try:
        # Test available exchanges
        exchanges = get_available_exchanges()
        print(f"Found {len(exchanges)} available exchanges")

        # Test some popular exchanges
        test_exchanges = ['binance', 'coinbase', 'kraken', 'okx']
        for exchange_id in test_exchanges:
            supported = is_exchange_supported(exchange_id)
            status = "OK" if supported else "FAIL"
            print(f"{status} {exchange_id}: {'supported' if supported else 'not supported'}")

        # Test exchange capabilities
        from ccxt_adapter import get_exchange_capabilities
        binance_caps = get_exchange_capabilities('binance')
        print(f"Binance capabilities loaded: {len(binance_caps)} features")

        return True

    except Exception as e:
        print(f"FAIL: CCXT basic test failed: {e}")
        return False

async def test_ccxt_adapter():
    """Test CCXT adapter with mock/sandbox mode."""
    if not CCXT_AVAILABLE:
        print("FAIL: CCXT not available for adapter test")
        return False

    print("Testing CCXT adapter...")

    try:
        # Create adapter for Binance with sandbox mode
        adapter = CCXTExchangeAdapter(
            exchange_id='binance',
            testnet=True,
            sandbox=True
        )

        print("CCXT adapter created successfully")

        # Test connection (should work in sandbox mode)
        connected = await adapter.connect()
        if connected:
            print("Connected to Binance sandbox")
        else:
            print("Could not connect to sandbox (expected for some exchanges)")

        # Test getting exchange info
        info = adapter.get_exchange_info()
        print(f"Exchange info: {info['name']} with {info['markets']} markets")

        # Test loading markets
        try:
            markets = adapter.exchange.markets
            if markets:
                print(f"Markets loaded: {len(markets)} markets")
                # Show a few example markets
                sample_markets = list(markets.keys())[:5]
                print(f"Sample markets: {sample_markets}")
            else:
                print("No markets loaded")
        except Exception as e:
            print(f"Could not load markets: {e}")

        # Clean up
        await adapter.disconnect()
        print("Adapter disconnected")

        return True

    except Exception as e:
        print(f"FAIL: CCXT adapter test failed: {e}")
        return False

async def test_unified_exchange():
    """Test unified exchange interface."""
    print("Testing unified exchange interface...")

    try:
        from execution import create_ccxt_exchange

        # Create unified exchange for Binance
        exchange = create_ccxt_exchange(
            exchange_id='binance',
            testnet=True
        )

        print("Unified exchange created")

        # Test basic methods
        connected = await exchange.connect()
        print(f"Exchange connection: {'successful' if connected else 'failed (expected in sandbox)'}")

        # Test getting supported exchanges
        from execution import get_supported_exchanges
        supported = get_supported_exchanges()
        print(f"Supported exchanges: {list(supported.keys())}")

        ccxt_exchanges = supported.get('ccxt', [])
        print(f"CCXT exchanges available: {len(ccxt_exchanges)}")

        return True

    except Exception as e:
        print(f"FAIL: Unified exchange test failed: {e}")
        return False

async def main():
    """Run all CCXT integration tests."""
    print("Starting CCXT Integration Tests")
    print("=" * 50)

    results = []

    # Test 1: Basic CCXT functionality
    results.append(await test_ccxt_basic())

    # Test 2: CCXT adapter
    results.append(await test_ccxt_adapter())

    # Test 3: Unified exchange interface
    results.append(await test_unified_exchange())

    # Summary
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("All CCXT integration tests passed!")
        return 0
    else:
        print("Some tests failed. Check the output above.")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)