#!/usr/bin/env python3
"""
Pacifica.fi Subaccount Discovery Script

Discovers all subaccounts associated with your main account and maps
public keys to internal subaccount IDs.

Usage:
    python discover_subaccounts.py

Requirements:
    - ACCOUNT_PUBLIC_KEY in .env
    - AGENT_WALLET_PRIVATE_KEY in .env
"""

import asyncio
import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class SubaccountDiscovery:
    """Discover and map Pacifica subaccounts."""

    def __init__(self):
        """Initialize discovery with credentials from environment."""
        self.account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")
        self.agent_wallet_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")

        if not self.account_public_key:
            raise ValueError("ACCOUNT_PUBLIC_KEY not found in environment")
        if not self.agent_wallet_private_key:
            raise ValueError("AGENT_WALLET_PRIVATE_KEY not found in environment")

    async def discover(self) -> List[Dict[str, Any]]:
        """
        Discover all subaccounts via Pacifica API.

        Returns:
            List of subaccount dictionaries with id, name, balance, etc.
        """
        try:
            from pacifica_client import PacificaClient, PacificaEnvironment

            # Initialize Pacifica client
            logger.info("Initializing Pacifica client...")
            client = PacificaClient(
                agent_wallet_private_key=self.agent_wallet_private_key,
                account_public_key=self.account_public_key,
                environment=PacificaEnvironment.TESTNET
            )

            # Query subaccounts endpoint
            logger.info("Querying /subaccounts endpoint...")
            response = client.get_subaccounts()

            if not response.get("success"):
                logger.error(f"Failed to get subaccounts: {response.get('error')}")
                return []

            subaccounts = response.get("data", {}).get("subaccounts", [])
            logger.info(f"✅ Found {len(subaccounts)} subaccounts")

            return subaccounts

        except ImportError:
            logger.error("Could not import pacifica_client. Make sure it exists.")
            return []
        except Exception as e:
            logger.error(f"Error discovering subaccounts: {e}")
            return []

    def display_subaccounts(self, subaccounts: List[Dict[str, Any]]):
        """
        Display discovered subaccounts in readable format.

        Args:
            subaccounts: List of subaccount data
        """
        if not subaccounts:
            logger.warning("No subaccounts found")
            return

        print("\n" + "="*80)
        print("DISCOVERED SUBACCOUNTS")
        print("="*80)

        for idx, sub in enumerate(subaccounts, 1):
            print(f"\n[{idx}] Subaccount Details:")
            print(f"  Subaccount ID:  {sub.get('subaccount_id', 'N/A')}")
            print(f"  Name:           {sub.get('name', 'N/A')}")
            print(f"  Balance:        ${sub.get('balance', 0):.2f}")
            print(f"  Equity:         ${sub.get('equity', 0):.2f}")
            print(f"  Created:        {sub.get('created_at', 'N/A')}")

            # Show public key if available
            if 'public_key' in sub:
                print(f"  Public Key:     {sub['public_key']}")

        print("\n" + "="*80)

    def save_to_config(self, subaccounts: List[Dict[str, Any]], filepath: str = "subaccounts.json"):
        """
        Save discovered subaccounts to JSON file.

        Args:
            subaccounts: List of subaccount data
            filepath: Output file path
        """
        try:
            config = {
                "discovered_at": datetime.now().isoformat(),
                "main_account": self.account_public_key,
                "subaccounts": subaccounts
            }

            with open(filepath, 'w') as f:
                json.dump(config, f, indent=2)

            logger.info(f"✅ Saved configuration to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def save_to_database(self, subaccounts: List[Dict[str, Any]]) -> bool:
        """
        Save discovered subaccounts to database.

        Args:
            subaccounts: List of subaccount data

        Returns:
            True if successful
        """
        try:
            from database import DatabaseManager

            db = DatabaseManager("trading_bot.db")

            # Ensure subaccount_configs table exists
            db.execute("""
                CREATE TABLE IF NOT EXISTS subaccount_configs (
                    subaccount_id TEXT PRIMARY KEY,
                    subaccount_name TEXT,
                    subaccount_public_key TEXT,
                    trading_strategy TEXT DEFAULT 'balanced',
                    max_position_size REAL DEFAULT 10000.0,
                    risk_per_trade REAL DEFAULT 0.02,
                    max_leverage INTEGER DEFAULT 20,
                    enabled BOOLEAN DEFAULT 1,
                    created_at DATETIME,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Insert/update each subaccount
            for sub in subaccounts:
                subaccount_id = sub.get('subaccount_id')
                name = sub.get('name', 'Unknown')
                public_key = sub.get('public_key', '')
                created_at = sub.get('created_at', datetime.now().isoformat())

                # Check if exists
                existing = db.execute(
                    "SELECT subaccount_id FROM subaccount_configs WHERE subaccount_id = ?",
                    (subaccount_id,)
                ).fetchone()

                if existing:
                    # Update existing
                    db.execute("""
                        UPDATE subaccount_configs
                        SET subaccount_name = ?,
                            subaccount_public_key = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE subaccount_id = ?
                    """, (name, public_key, subaccount_id))
                    logger.info(f"Updated subaccount {subaccount_id} in database")
                else:
                    # Insert new
                    db.execute("""
                        INSERT INTO subaccount_configs
                        (subaccount_id, subaccount_name, subaccount_public_key, created_at)
                        VALUES (?, ?, ?, ?)
                    """, (subaccount_id, name, public_key, created_at))
                    logger.info(f"Added subaccount {subaccount_id} to database")

            db.commit()
            logger.info(f"✅ Saved {len(subaccounts)} subaccounts to database")
            return True

        except Exception as e:
            logger.error(f"Failed to save to database: {e}")
            return False

    def map_public_key_to_id(
        self,
        subaccounts: List[Dict[str, Any]],
        public_key: str
    ) -> Optional[str]:
        """
        Map a subaccount public key to its internal ID.

        Args:
            subaccounts: List of subaccount data
            public_key: Solana public key to look up

        Returns:
            Subaccount ID or None if not found
        """
        for sub in subaccounts:
            if sub.get('public_key') == public_key:
                return sub.get('subaccount_id')
        return None

    def recommend_default(self, subaccounts: List[Dict[str, Any]]) -> Optional[str]:
        """
        Recommend which subaccount should be default.

        Uses heuristics:
        1. Highest balance
        2. Most recently created

        Args:
            subaccounts: List of subaccount data

        Returns:
            Recommended subaccount_id
        """
        if not subaccounts:
            return None

        # Sort by balance (descending)
        sorted_subs = sorted(
            subaccounts,
            key=lambda x: x.get('balance', 0),
            reverse=True
        )

        recommended = sorted_subs[0]
        logger.info(
            f"💡 Recommend default: {recommended['subaccount_id']} "
            f"(${recommended.get('balance', 0):.2f} balance)"
        )

        return recommended.get('subaccount_id')


async def main():
    """Main discovery workflow."""
    print("\n" + "="*80)
    print("PACIFICA SUBACCOUNT DISCOVERY")
    print("="*80 + "\n")

    try:
        # Initialize discovery
        discovery = SubaccountDiscovery()

        logger.info(f"Main Account: {discovery.account_public_key}")

        # Discover subaccounts
        subaccounts = await discovery.discover()

        if not subaccounts:
            logger.warning("⚠️  No subaccounts found")
            logger.info("You may need to create subaccounts via Pacifica interface first")
            return

        # Display results
        discovery.display_subaccounts(subaccounts)

        # Save to JSON file
        discovery.save_to_config(subaccounts)

        # Save to database
        discovery.save_to_database(subaccounts)

        # Recommend default
        default_id = discovery.recommend_default(subaccounts)

        # Check if user's provided public key matches any subaccount
        user_subaccount_key = "6Jj5ahJwLVceRw5kZgyw9VMszjEcS2ZfaWFHuumgfLDt"
        matched_id = discovery.map_public_key_to_id(subaccounts, user_subaccount_key)

        if matched_id:
            logger.info(f"✅ Your subaccount public key maps to ID: {matched_id}")
        else:
            logger.warning(
                f"⚠️  Could not find subaccount with public key: {user_subaccount_key}"
            )

        # Summary
        print("\n" + "="*80)
        print("NEXT STEPS:")
        print("="*80)
        print(f"\n1. Add to your .env file:")
        print(f"   DEFAULT_SUBACCOUNT_ID={default_id}")
        print(f"\n2. Configure strategies in database:")
        print(f"   UPDATE subaccount_configs SET trading_strategy='conservative' WHERE subaccount_id='{default_id}';")
        print(f"\n3. Subaccount configuration saved to:")
        print(f"   - subaccounts.json")
        print(f"   - trading_bot.db (subaccount_configs table)")
        print("\n" + "="*80 + "\n")

    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Run discovery
    asyncio.run(main())
