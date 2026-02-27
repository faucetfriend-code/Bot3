#!/usr/bin/env python3
"""
Pacifica Subaccount Manager

Centralized management for multiple Pacifica subaccounts with strategy assignment.

Features:
- Load subaccounts from database
- Assign trading strategies per subaccount
- Get subaccount by strategy
- Balance and position tracking per subaccount
- Transfer funds between subaccounts
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from loguru import logger

from database import DatabaseManager
from config import SubaccountConfig, TradingStrategy, get_pacifica_config


class SubaccountManager:
    """
    Centralized manager for Pacifica subaccounts.

    Handles loading, configuring, and routing operations to the correct subaccount.
    """

    def __init__(self, db: DatabaseManager):
        """
        Initialize SubaccountManager.

        Args:
            db: Database manager instance
        """
        self.db = db
        self.subaccounts: Dict[str, SubaccountConfig] = {}
        self.default_subaccount_id: Optional[str] = None

        # Load subaccounts from database on initialization
        self._load_from_database()

    def _load_from_database(self):
        """Load subaccount configurations from database."""
        try:
            cursor = self.db.execute("""
                SELECT subaccount_id, subaccount_name, subaccount_public_key,
                       trading_strategy, max_position_size, risk_per_trade,
                       max_leverage, enabled, created_at, updated_at
                FROM subaccount_configs
                ORDER BY created_at
            """)

            rows = cursor.fetchall()

            for row in rows:
                subaccount = SubaccountConfig(
                    subaccount_id=row[0],
                    subaccount_name=row[1],
                    subaccount_public_key=row[2],
                    trading_strategy=TradingStrategy(row[3]),
                    max_position_size=row[4],
                    risk_per_trade=row[5],
                    max_leverage=row[6],
                    enabled=bool(row[7]),
                    created_at=row[8],
                    updated_at=row[9],
                )
                self.subaccounts[subaccount.subaccount_id] = subaccount

            logger.info(f"Loaded {len(self.subaccounts)} subaccounts from database")

            # Set default from config if available
            config = get_pacifica_config()
            if config.default_subaccount_id:
                self.default_subaccount_id = config.default_subaccount_id
            elif self.subaccounts:
                # Use first enabled subaccount as default
                for subaccount_id, sub in self.subaccounts.items():
                    if sub.enabled:
                        self.default_subaccount_id = subaccount_id
                        break

        except Exception as e:
            logger.error(f"Failed to load subaccounts from database: {e}")

    def discover_subaccounts(self) -> List[SubaccountConfig]:
        """
        Discover subaccounts from Pacifica API and sync to database.

        Returns:
            List of discovered SubaccountConfig objects
        """
        try:
            from pacifica_client import PacificaClient
            from config import PacificaEnvironment
            import os

            # Get credentials
            agent_wallet_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
            account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

            if not agent_wallet_key or not account_public_key:
                logger.error("Missing Pacifica credentials in environment")
                return []

            # Initialize client
            client = PacificaClient(
                agent_wallet_private_key=agent_wallet_key,
                account_public_key=account_public_key,
                environment=PacificaEnvironment.TESTNET
            )

            # Query subaccounts
            response = client.get_subaccounts()

            if not response.get("success"):
                logger.error(f"Failed to discover subaccounts: {response.get('error')}")
                return []

            subaccounts_data = response.get("data", {}).get("subaccounts", [])
            discovered = []

            for sub_data in subaccounts_data:
                subaccount_id = sub_data.get("subaccount_id")
                name = sub_data.get("name", "Unnamed")
                public_key = sub_data.get("public_key", "")
                created_at = sub_data.get("created_at", datetime.now().isoformat())

                # Check if already exists
                if subaccount_id not in self.subaccounts:
                    # Create new subaccount config with default values
                    subaccount = SubaccountConfig(
                        subaccount_id=subaccount_id,
                        subaccount_name=name,
                        subaccount_public_key=public_key,
                        trading_strategy=TradingStrategy.BALANCED,
                        max_position_size=10000.0,
                        risk_per_trade=0.02,
                        max_leverage=20,
                        enabled=True,
                        created_at=created_at,
                        updated_at=datetime.now().isoformat(),
                    )

                    # Save to database
                    self.db.execute("""
                        INSERT OR REPLACE INTO subaccount_configs
                        (subaccount_id, subaccount_name, subaccount_public_key,
                         trading_strategy, max_position_size, risk_per_trade,
                         max_leverage, enabled, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        subaccount.subaccount_id,
                        subaccount.subaccount_name,
                        subaccount.subaccount_public_key,
                        subaccount.trading_strategy.value,
                        subaccount.max_position_size,
                        subaccount.risk_per_trade,
                        subaccount.max_leverage,
                        int(subaccount.enabled),
                        subaccount.created_at,
                        subaccount.updated_at,
                    ))

                    self.subaccounts[subaccount_id] = subaccount
                    discovered.append(subaccount)

            self.db.commit()
            logger.info(f"✅ Discovered and synced {len(discovered)} new subaccounts")

            return discovered

        except Exception as e:
            logger.error(f"Error discovering subaccounts: {e}")
            return []

    def get_subaccount(self, subaccount_id: str) -> Optional[SubaccountConfig]:
        """
        Get subaccount configuration by ID.

        Args:
            subaccount_id: Subaccount ID

        Returns:
            SubaccountConfig or None if not found
        """
        return self.subaccounts.get(subaccount_id)

    def get_default_subaccount(self) -> Optional[SubaccountConfig]:
        """
        Get the default subaccount.

        Returns:
            SubaccountConfig or None if no default set
        """
        if self.default_subaccount_id:
            return self.subaccounts.get(self.default_subaccount_id)
        return None

    def set_default_subaccount(self, subaccount_id: str) -> bool:
        """
        Set the default subaccount.

        Args:
            subaccount_id: Subaccount ID to set as default

        Returns:
            True if successful
        """
        if subaccount_id not in self.subaccounts:
            logger.error(f"Subaccount {subaccount_id} not found")
            return False

        self.default_subaccount_id = subaccount_id
        logger.info(f"Set default subaccount to: {subaccount_id}")
        return True

    def get_active_subaccounts(self) -> List[SubaccountConfig]:
        """
        Get all enabled subaccounts.

        Returns:
            List of enabled SubaccountConfig objects
        """
        return [sub for sub in self.subaccounts.values() if sub.enabled]

    def get_subaccount_for_strategy(
        self, strategy: TradingStrategy
    ) -> Optional[SubaccountConfig]:
        """
        Get the first enabled subaccount assigned to a strategy.

        Args:
            strategy: Trading strategy to find

        Returns:
            SubaccountConfig or None if not found
        """
        for subaccount in self.subaccounts.values():
            if subaccount.trading_strategy == strategy and subaccount.enabled:
                return subaccount
        return None

    def get_subaccounts_by_strategy(
        self, strategy: TradingStrategy
    ) -> List[SubaccountConfig]:
        """
        Get all enabled subaccounts assigned to a strategy.

        Args:
            strategy: Trading strategy to filter by

        Returns:
            List of SubaccountConfig objects
        """
        return [
            sub for sub in self.subaccounts.values()
            if sub.trading_strategy == strategy and sub.enabled
        ]

    def assign_strategy_to_subaccount(
        self,
        subaccount_id: str,
        strategy: TradingStrategy
    ) -> bool:
        """
        Assign a trading strategy to a subaccount.

        Args:
            subaccount_id: Subaccount ID
            strategy: Trading strategy to assign

        Returns:
            True if successful
        """
        subaccount = self.subaccounts.get(subaccount_id)
        if not subaccount:
            logger.error(f"Subaccount {subaccount_id} not found")
            return False

        # Update in memory
        subaccount.trading_strategy = strategy
        subaccount.updated_at = datetime.now().isoformat()

        # Update in database
        self.db.execute("""
            UPDATE subaccount_configs
            SET trading_strategy = ?, updated_at = ?
            WHERE subaccount_id = ?
        """, (strategy.value, subaccount.updated_at, subaccount_id))
        self.db.commit()

        logger.info(f"Assigned {strategy.value} strategy to subaccount {subaccount_id}")
        return True

    def update_risk_parameters(
        self,
        subaccount_id: str,
        max_position_size: Optional[float] = None,
        risk_per_trade: Optional[float] = None,
        max_leverage: Optional[int] = None,
    ) -> bool:
        """
        Update risk parameters for a subaccount.

        Args:
            subaccount_id: Subaccount ID
            max_position_size: Maximum position size in USD
            risk_per_trade: Risk per trade (0.0-1.0)
            max_leverage: Maximum leverage (5-50)

        Returns:
            True if successful
        """
        subaccount = self.subaccounts.get(subaccount_id)
        if not subaccount:
            logger.error(f"Subaccount {subaccount_id} not found")
            return False

        # Update fields if provided
        if max_position_size is not None:
            subaccount.max_position_size = max_position_size
        if risk_per_trade is not None:
            subaccount.risk_per_trade = risk_per_trade
        if max_leverage is not None:
            subaccount.max_leverage = max_leverage

        subaccount.updated_at = datetime.now().isoformat()

        # Update database
        self.db.execute("""
            UPDATE subaccount_configs
            SET max_position_size = ?, risk_per_trade = ?, max_leverage = ?, updated_at = ?
            WHERE subaccount_id = ?
        """, (
            subaccount.max_position_size,
            subaccount.risk_per_trade,
            subaccount.max_leverage,
            subaccount.updated_at,
            subaccount_id
        ))
        self.db.commit()

        logger.info(f"Updated risk parameters for subaccount {subaccount_id}")
        return True

    def enable_subaccount(self, subaccount_id: str) -> bool:
        """
        Enable a subaccount for trading.

        Args:
            subaccount_id: Subaccount ID

        Returns:
            True if successful
        """
        return self._set_subaccount_status(subaccount_id, True)

    def disable_subaccount(self, subaccount_id: str) -> bool:
        """
        Disable a subaccount (stop trading).

        Args:
            subaccount_id: Subaccount ID

        Returns:
            True if successful
        """
        return self._set_subaccount_status(subaccount_id, False)

    def _set_subaccount_status(self, subaccount_id: str, enabled: bool) -> bool:
        """Set subaccount enabled status."""
        subaccount = self.subaccounts.get(subaccount_id)
        if not subaccount:
            logger.error(f"Subaccount {subaccount_id} not found")
            return False

        subaccount.enabled = enabled
        subaccount.updated_at = datetime.now().isoformat()

        self.db.execute("""
            UPDATE subaccount_configs
            SET enabled = ?, updated_at = ?
            WHERE subaccount_id = ?
        """, (int(enabled), subaccount.updated_at, subaccount_id))
        self.db.commit()

        status = "enabled" if enabled else "disabled"
        logger.info(f"Subaccount {subaccount_id} {status}")
        return True

    def get_subaccount_balance(self, subaccount_id: str) -> Optional[float]:
        """
        Get subaccount balance from Pacifica API.

        Args:
            subaccount_id: Subaccount ID

        Returns:
            Balance in USD or None if error
        """
        try:
            from pacifica_client import PacificaClient
            from config import PacificaEnvironment
            import os

            agent_wallet_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
            account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

            if not agent_wallet_key or not account_public_key:
                return None

            client = PacificaClient(
                agent_wallet_private_key=agent_wallet_key,
                account_public_key=account_public_key,
                environment=PacificaEnvironment.TESTNET
            )

            response = client.get_balance(currency="USDC", subaccount_id=subaccount_id)

            if response.get("success"):
                return response.get("data", {}).get("available", 0.0)

            return None

        except Exception as e:
            logger.error(f"Failed to get subaccount balance: {e}")
            return None

    def get_all_balances(self) -> Dict[str, float]:
        """
        Get balances for all subaccounts.

        Returns:
            Dict mapping subaccount_id to balance
        """
        balances = {}
        for subaccount_id in self.subaccounts:
            balance = self.get_subaccount_balance(subaccount_id)
            if balance is not None:
                balances[subaccount_id] = balance
        return balances

    def transfer_between_subaccounts(
        self,
        from_subaccount_id: str,
        to_subaccount_id: str,
        amount: float
    ) -> bool:
        """
        Transfer funds between subaccounts.

        Args:
            from_subaccount_id: Source subaccount
            to_subaccount_id: Destination subaccount
            amount: Amount to transfer

        Returns:
            True if successful
        """
        try:
            from pacifica_client import PacificaClient
            from config import PacificaEnvironment
            import os

            agent_wallet_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
            account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

            if not agent_wallet_key or not account_public_key:
                logger.error("Missing credentials")
                return False

            client = PacificaClient(
                agent_wallet_private_key=agent_wallet_key,
                account_public_key=account_public_key,
                environment=PacificaEnvironment.TESTNET
            )

            # Note: from_account is for logging/reference only
            # The actual source account is the signing keypair
            response = client.transfer_funds(
                to_account=to_subaccount_id,
                amount=amount,
                from_account=from_subaccount_id  # Reference only
            )

            if response.get("success"):
                logger.info(
                    f"✅ Transferred ${amount} from {from_subaccount_id} to {to_subaccount_id}"
                )
                return True

            logger.error(f"Transfer failed: {response.get('error')}")
            return False

        except Exception as e:
            logger.error(f"Transfer error: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Get subaccount manager statistics.

        Returns:
            Statistics dictionary
        """
        active_count = len(self.get_active_subaccounts())
        strategy_distribution = {}

        for subaccount in self.subaccounts.values():
            strategy = subaccount.trading_strategy.value
            strategy_distribution[strategy] = strategy_distribution.get(strategy, 0) + 1

        return {
            "total_subaccounts": len(self.subaccounts),
            "active_subaccounts": active_count,
            "default_subaccount_id": self.default_subaccount_id,
            "strategy_distribution": strategy_distribution,
        }

    def __repr__(self) -> str:
        return (
            f"<SubaccountManager total={len(self.subaccounts)} "
            f"active={len(self.get_active_subaccounts())}>"
        )


# Convenience function
def get_subaccount_manager(db: Optional[DatabaseManager] = None) -> SubaccountManager:
    """
    Get or create SubaccountManager instance.

    Args:
        db: Database manager (creates new if None)

    Returns:
        SubaccountManager instance
    """
    if db is None:
        db = DatabaseManager("trading_bot.db")

    return SubaccountManager(db)


if __name__ == "__main__":
    # Example usage
    manager = get_subaccount_manager()

    print("\n=== Subaccount Manager ===")
    print(f"Total subaccounts: {len(manager.subaccounts)}")
    print(f"Default: {manager.default_subaccount_id}")

    print("\nActive subaccounts:")
    for sub in manager.get_active_subaccounts():
        print(f"  - {sub.subaccount_id}: {sub.subaccount_name} ({sub.trading_strategy.value})")

    print("\nStats:", manager.get_stats())
