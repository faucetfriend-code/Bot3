#!/usr/bin/env python3
"""
Synchronous version to populate market history database.
"""

import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv

from pacifica_client import PacificaClient, PacificaEnvironment, RateLimitTier
from database import DATABASE_PATH

# Load environment variables
load_dotenv()


def populate_market_history_sync():
    """Populate market history using synchronous database operations."""
    try:
        print("=== Market History Population (Sync) ===")
        print(f"Starting at: {datetime.now()}\n")

        # Get credentials
        agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")
        use_testnet = os.getenv("TESTNET", "true").lower() == "true"
        environment = (
            PacificaEnvironment.TESTNET
            if use_testnet
            else PacificaEnvironment.MAINNET
        )

        print(f"Initializing Pacifica client ({environment.name})...")
        pacifica_client = PacificaClient(
            agent_wallet_private_key=agent_private_key,
            account_public_key=account_public_key,
            environment=environment,
            rate_limit_tier=RateLimitTier.BASIC,
        )

        print("Fetching market data from Pacifica API...")
        markets = pacifica_client.get_markets()

        if not markets:
            print("[ERROR] No market data received")
            return 1

        print(f"Received data for {len(markets)} markets")

        # Connect to database
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        # Insert market info
        print("\nStoring market data...")
        stored_count = 0
        fetched_at = datetime.now()

        for market in markets:
            try:
                cursor.execute(
                    """
                    INSERT INTO market_info_history
                    (symbol, tick_size, min_tick, max_tick, lot_size, max_leverage,
                     isolated_only, min_order_size, max_order_size, funding_rate,
                     next_funding_rate, created_at, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        market.get("symbol", ""),
                        str(market.get("tick_size", "0")),
                        str(market.get("min_tick", "0")),
                        str(market.get("max_tick", "0")),
                        str(market.get("lot_size", "0")),
                        int(market.get("max_leverage", 1)),
                        bool(market.get("isolated_only", False)),
                        str(market.get("min_order_size", "0")),
                        str(market.get("max_order_size", "0")),
                        str(market.get("funding_rate", "0")),
                        str(market.get("next_funding_rate", "0")),
                        int(market.get("created_at", 0)),
                        fetched_at,
                    ),
                )
                stored_count += 1
                print(
                    f"  [OK] {market['symbol']}: funding_rate={market.get('funding_rate', '0')}"
                )
            except Exception as e:
                print(f"  [ERROR] {market.get('symbol', 'unknown')}: {e}")

        # Commit transaction
        conn.commit()
        print(f"\n[SUCCESS] Stored {stored_count} markets")

        # Verify
        cursor.execute("SELECT COUNT(*) FROM market_info_history")
        count = cursor.fetchone()[0]
        print(f"[VERIFY] Database now has {count} rows in market_info_history")

        conn.close()

        print("\n=== Market History Features Enabled ===")
        print("  - Funding rate charts will now display data")
        print("  - Market parameter tracking is active")
        print("  - Run this script periodically to build history")

        return 0

    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(populate_market_history_sync())
