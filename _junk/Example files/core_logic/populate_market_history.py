#!/usr/bin/env python3
"""
One-time script to populate market history database with initial data.
Run this to enable market history, funding rates, and parameter change tracking.
"""

import asyncio
import sys
import os
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

from database import DatabaseManager
from pacifica_client import PacificaClient, PacificaEnvironment, RateLimitTier
from market_data_collector import MarketDataCollector

# Load environment variables from .env file
load_dotenv()


async def populate_market_history():
    """Populate market history database with initial snapshot."""
    try:
        logger.info("=== Market History Population Script ===")
        logger.info(f"Starting at: {datetime.now()}")

        # Initialize components
        db_manager = DatabaseManager()

        # Get credentials from environment
        agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")
        use_testnet = os.getenv("TESTNET", "true").lower() == "true"
        environment = (
            PacificaEnvironment.TESTNET
            if use_testnet
            else PacificaEnvironment.MAINNET
        )

        logger.info(f"Initializing Pacifica client ({environment.name})...")
        pacifica_client = PacificaClient(
            agent_wallet_private_key=agent_private_key,
            account_public_key=account_public_key,
            environment=environment,
            rate_limit_tier=RateLimitTier.BASIC,
        )

        logger.info("Initializing market data collector...")
        collector = MarketDataCollector(db_manager)

        # Fetch and store market data
        logger.info("Fetching market data from Pacifica API...")
        success = await collector.collect_market_info(pacifica_client)

        if success:
            # Get statistics
            stats = await collector.get_market_stats()
            logger.success(f"✓ Successfully populated market history!")
            logger.info(f"  Total markets: {stats['total_markets']}")
            logger.info(f"  Avg funding rate: {stats['avg_funding_rate']:.6f}")
            logger.info(f"  Last update: {stats['last_update']}")

            # Verify data was stored
            import aiosqlite

            async with db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM market_info_history"
                )
                row = await cursor.fetchone()
                history_count = row[0]

                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM market_parameter_changes"
                )
                row = await cursor.fetchone()
                changes_count = row[0]

            logger.info(f"\nDatabase verification:")
            logger.info(f"  market_info_history: {history_count} rows")
            logger.info(f"  market_parameter_changes: {changes_count} rows")

            logger.success("\n✓ Market history features are now enabled!")
            logger.info(
                "  - Funding rate history charts will now display data"
            )
            logger.info("  - Market parameter change tracking is active")
            logger.info("  - Technical indicators will populate over time")

            return 0
        else:
            logger.error("✗ Failed to collect market data")
            return 1

    except Exception as e:
        logger.error(f"✗ Error populating market history: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(populate_market_history())
    sys.exit(exit_code)
