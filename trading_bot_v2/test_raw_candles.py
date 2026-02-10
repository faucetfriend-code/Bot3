"""Test to see the raw format of Pacifica candle data."""

import json
import os
from datetime import datetime, timedelta
from core_logic.pacifica_client import PacificaClient, PacificaEnvironment
from dotenv import load_dotenv

load_dotenv()

# Initialize client
private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

client = PacificaClient(private_key, public_key, PacificaEnvironment.TESTNET)

# Test getting raw candles
print("Fetching raw candles for SUI 15m...")
try:
    # Calculate start_time (50 hours ago for 200 candles @ 15m)
    start_time = datetime.now() - timedelta(minutes=15 * 200)
    start_time_ms = int(start_time.timestamp() * 1000)

    candles = client.get_candles(
        market="SUI", interval="15m", start_time=start_time_ms, limit=200
    )

    print(f"\nReceived {len(candles)} candles")
    if candles:
        print("\nFirst candle structure:")
        print(json.dumps(candles[0], indent=2))
        print(
            "\nKeys in first candle:",
            list(candles[0].keys()) if isinstance(candles[0], dict) else "Not a dict",
        )
except Exception as e:
    print(f"Error: {e}")
    import traceback

    traceback.print_exc()
