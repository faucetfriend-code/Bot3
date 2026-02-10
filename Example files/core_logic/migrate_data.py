#!/usr/bin/env python3
"""
Data migration script for trading bot.
Migrates from in-memory storage to database.
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, List
import logging

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import DatabaseManager, init_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DataMigration:
    """Handles data migration from in-memory to database."""

    def __init__(self):
        self.db = DatabaseManager()
        self.backup_file = "migration_backup.json"

    def export_current_data(self) -> Dict[str, Any]:
        """Export current in-memory data and CSV journal."""
        logger.info("Exporting current data...")

        try:
            # Import current stores (this will be from api_server.py globals)
            from api_server import trades_store, market_data_store, signals_store

            data = {
                "trades": list(trades_store),
                "market_data": list(market_data_store.values()),
                "signals": list(signals_store),
                "exported_at": datetime.now().isoformat(),
                "version": "1.0",
            }

            # Try to export journal data if available
            try:
                from journal import create_journal

                journal = create_journal()
                data["journal_trades"] = journal.trades
                data["journal_performance"] = journal.performance_metrics
            except Exception as e:
                logger.warning(f"Could not export journal data: {e}")
                data["journal_trades"] = []
                data["journal_performance"] = {}

            # Save backup atomically
            import tempfile
            import shutil
            from backup import safe_json_serializer

            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir=self.backup_file.parent) as tmp:
                json.dump(data, tmp, indent=2, default=safe_json_serializer)
                tmp.flush()
                shutil.move(tmp.name, self.backup_file)

            logger.info(
                f"Exported {len(data['trades'])} trades, {len(data['market_data'])} market data points, {len(data['signals'])} signals"
            )
            return data

        except ImportError as e:
            logger.error(f"Could not import current data stores: {e}")
            logger.info("This is expected if running migration on fresh system")
            return self._create_empty_data()

    def _create_empty_data(self) -> Dict[str, Any]:
        """Create empty data structure for fresh migration."""
        return {
            "trades": [],
            "market_data": [],
            "signals": [],
            "journal_trades": [],
            "journal_performance": {},
            "exported_at": datetime.now().isoformat(),
            "version": "1.0",
        }

    def migrate_to_database(self, data: Dict[str, Any]):
        """Migrate exported data to database."""
        logger.info("Starting database migration...")

        try:
            # Migrate trades
            trades_migrated = 0
            for trade in data.get("trades", []):
                try:
                    self.db.save_trade(trade)
                    trades_migrated += 1
                except Exception as e:
                    logger.error(f"Failed to migrate trade {trade}: {e}")

            # Migrate journal trades
            for trade in data.get("journal_trades", []):
                try:
                    # Convert journal trade format to database format
                    db_trade = {
                        "symbol": trade.get("symbol", "UNKNOWN"),
                        "side": trade.get("side", "long"),
                        "quantity": trade.get("quantity", 0),
                        "entry_price": trade.get("entry_price", 0),
                        "exit_price": trade.get("exit_price"),
                        "entry_time": trade.get(
                            "entry_time", datetime.now().isoformat()
                        ),
                        "exit_time": trade.get("exit_time"),
                        "pnl": trade.get("pnl", 0),
                        "commission": trade.get("commission", 0),
                        "strategy": trade.get("strategy"),
                        "status": trade.get("status", "closed"),
                    }
                    self.db.save_trade(db_trade)
                    trades_migrated += 1
                except Exception as e:
                    logger.error(f"Failed to migrate journal trade {trade}: {e}")

            # Migrate market data
            market_data_migrated = 0
            for item in data.get("market_data", []):
                try:
                    if isinstance(item, dict) and "symbol" in item:
                        self.db.save_market_data(
                            symbol=item["symbol"],
                            price=item.get("price", 0),
                            volume=item.get("volume"),
                            source="migration",
                        )
                        market_data_migrated += 1
                except Exception as e:
                    logger.error(f"Failed to migrate market data {item}: {e}")

            # Migrate signals
            signals_migrated = 0
            for signal in data.get("signals", []):
                try:
                    self.db.save_signal(signal)
                    signals_migrated += 1
                except Exception as e:
                    logger.error(f"Failed to migrate signal {signal}: {e}")

            # Migrate performance metrics
            perf_data = data.get("journal_performance", {})
            if perf_data:
                try:
                    self.db.update_performance_metrics(perf_data)
                    logger.info("Migrated performance metrics")
                except Exception as e:
                    logger.error(f"Failed to migrate performance metrics: {e}")

            logger.info(
                f"Migration completed: {trades_migrated} trades, {market_data_migrated} market data points, {signals_migrated} signals"
            )

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise

    def verify_migration(self, original_data: Dict[str, Any]) -> bool:
        """Verify that migration was successful."""
        logger.info("Verifying migration...")

        try:
            # Check trade counts
            db_trades = self.db.get_trades(limit=10000)
            original_trade_count = len(original_data.get("trades", [])) + len(
                original_data.get("journal_trades", [])
            )
            if len(db_trades) != original_trade_count:
                logger.error(
                    f"Trade count mismatch: expected {original_trade_count}, got {len(db_trades)}"
                )
                return False

            # Check that key data is preserved
            if db_trades:
                first_trade = db_trades[0]
                required_fields = ["symbol", "side", "quantity", "entry_price"]
                for field in required_fields:
                    if field not in first_trade:
                        logger.error(
                            f"Missing required field '{field}' in migrated trade"
                        )
                        return False

            # Check database stats
            stats = self.db.get_stats()
            logger.info(f"Database stats: {stats}")

            logger.info("Migration verification passed")
            return True

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return False

    def create_rollback_backup(self):
        """Create a backup for rollback purposes."""
        try:
            # Export current database state
            db_data = self.db.export_data()
            backup_file = (
                f"rollback_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )

            with open(backup_file, "w") as f:
                json.dump(db_data, f, indent=2, default=safe_json_serializer)

            logger.info(f"Rollback backup created: {backup_file}")
            return backup_file

        except Exception as e:
            logger.error(f"Failed to create rollback backup: {e}")
            return None


def main():
    """Main migration function."""
    import argparse

    parser = argparse.ArgumentParser(description="Migrate trading bot data to database")
    parser.add_argument(
        "--export", action="store_true", help="Export current data only"
    )
    parser.add_argument("--migrate", action="store_true", help="Perform migration")
    parser.add_argument(
        "--verify", action="store_true", help="Verify migration success"
    )
    parser.add_argument(
        "--rollback", action="store_true", help="Create rollback backup"
    )

    args = parser.parse_args()

    migration = DataMigration()

    if args.export:
        logger.info("=== EXPORT MODE ===")
        data = migration.export_current_data()
        logger.info(f"Data exported to {migration.backup_file}")

    elif args.migrate:
        logger.info("=== MIGRATION MODE ===")
        # Create rollback backup first
        rollback_file = migration.create_rollback_backup()

        # Export current data
        data = migration.export_current_data()

        # Perform migration
        migration.migrate_to_database(data)

        # Verify migration
        if migration.verify_migration(data):
            logger.info("✅ Migration completed successfully")
            if rollback_file:
                logger.info(f"Rollback backup available: {rollback_file}")
        else:
            logger.error("❌ Migration verification failed")
            sys.exit(1)

    elif args.verify:
        logger.info("=== VERIFICATION MODE ===")
        # Load backup data
        if os.path.exists(migration.backup_file):
            with open(migration.backup_file, "r") as f:
                data = json.load(f)
            if migration.verify_migration(data):
                logger.info("✅ Migration verification passed")
            else:
                logger.error("❌ Migration verification failed")
                sys.exit(1)
        else:
            logger.error(f"Backup file {migration.backup_file} not found")
            sys.exit(1)

    elif args.rollback:
        logger.info("=== ROLLBACK MODE ===")
        backup_file = migration.create_rollback_backup()
        if backup_file:
            logger.info(f"Rollback backup created: {backup_file}")
        else:
            logger.error("Failed to create rollback backup")
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
