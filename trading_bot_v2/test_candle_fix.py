"""Test script to verify candle fetching after timestamp fix."""

import os
from core_logic.pacifica_client import PacificaClient, PacificaEnvironment
from .multi_timeframe_fetcher import MultiTimeframeFetcher
from dotenv import load_dotenv

load_dotenv()

# Initialize client
private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

client = PacificaClient(private_key, public_key, PacificaEnvironment.TESTNET)

# Initialize fetcher
fetcher = MultiTimeframeFetcher(client)

# Test candle fetching for SUI across 3 timeframes
print("Testing candle fetching for SUI...")
try:
    data = fetcher.get_candles_multi_tf(
        "SUI", ["15m", "1h", "4h"], lookback_candles=200
    )

    print("\n[SUCCESS] Candle data fetched!")
    print(f"15m candles: {len(data.get('15m', {}).get('close', []))}")
    print(f"1h candles: {len(data.get('1h', {}).get('close', []))}")
    print(f"4h candles: {len(data.get('4h', {}).get('close', []))}")

    # Show sample of latest candles
    if data.get("15m") and len(data["15m"]["close"]) > 0:
        print(f"\nLatest 15m close prices (last 5):")
        print(data["15m"]["close"][-5:])

except Exception as e:
    print(f"\n[FAILED] Error fetching candles: {e}")
