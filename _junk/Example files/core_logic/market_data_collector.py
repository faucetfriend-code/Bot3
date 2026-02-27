#!/usr/bin/env python3
"""
Market Data Collector Service
Stores historical market information from Pacifica /info endpoint for analysis.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

import aiosqlite
from loguru import logger

from database import DatabaseManager

@dataclass
class MarketInfoRecord:
    """Represents a single market info record from /info endpoint."""
    symbol: str
    tick_size: str
    min_tick: str
    max_tick: str
    lot_size: str
    max_leverage: int
    isolated_only: bool
    min_order_size: str
    max_order_size: str
    funding_rate: str
    next_funding_rate: str
    created_at: int
    fetched_at: datetime

class MarketDataCollector:
    """
    Collects and stores historical market information from Pacifica API.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.last_collection = None
        self.collection_interval = timedelta(minutes=15)  # Collect every 15 minutes

    async def collect_market_info(self, pacifica_client) -> bool:
        """
        Fetch market info from Pacifica and store in database.

        Args:
            pacifica_client: Initialized PacificaClient instance

        Returns:
            bool: True if collection successful
        """
        try:
            # Check if we should collect (rate limiting)
            now = datetime.now()
            if self.last_collection and (now - self.last_collection) < self.collection_interval:
                logger.debug("Skipping market info collection - too soon since last collection")
                return True

            logger.info("Starting market info collection from Pacifica API")

            # Fetch market data
            markets_data = pacifica_client.get_markets()
            if not markets_data:
                logger.warning("No market data received from Pacifica API")
                return False

            # Store the data
            return await self._store_market_data(markets_data, now)

        except Exception as e:
            logger.error(f"Market info collection failed: {e}")
            return False

    async def store_market_data(self, markets_data: List[Dict]) -> bool:
        """
        Store pre-fetched market data directly.

        Args:
            markets_data: List of market dictionaries from /info endpoint

        Returns:
            bool: True if storage successful
        """
        try:
            now = datetime.now()
            return await self._store_market_data(markets_data, now)

        except Exception as e:
            logger.error(f"Market data storage failed: {e}")
            return False

    async def _store_market_data(self, markets_data: List[Dict], timestamp: datetime) -> bool:
        """Store market data and detect changes."""
        try:
            logger.info(f"Storing data for {len(markets_data)} markets")

            # Store each market's info
            stored_count = 0
            for market in markets_data:
                try:
                    record = self._parse_market_data(market)
                    await self._store_market_info(record)
                    stored_count += 1
                except Exception as e:
                    logger.error(f"Failed to store market {market.get('symbol', 'unknown')}: {e}")

            # Check for parameter changes
            await self._detect_parameter_changes(markets_data)

            self.last_collection = timestamp
            logger.info(f"Successfully stored market info for {stored_count} markets")

            return True

        except Exception as e:
            logger.error(f"Market data storage failed: {e}")
            return False

    def _parse_market_data(self, market_data: Dict[str, Any]) -> MarketInfoRecord:
        """Parse raw market data into structured record."""
        return MarketInfoRecord(
            symbol=market_data.get('symbol', ''),
            tick_size=str(market_data.get('tick_size', '0')),
            min_tick=str(market_data.get('min_tick', '0')),
            max_tick=str(market_data.get('max_tick', '0')),
            lot_size=str(market_data.get('lot_size', '0')),
            max_leverage=int(market_data.get('max_leverage', 1)),
            isolated_only=bool(market_data.get('isolated_only', False)),
            min_order_size=str(market_data.get('min_order_size', '0')),
            max_order_size=str(market_data.get('max_order_size', '0')),
            funding_rate=str(market_data.get('funding_rate', '0')),
            next_funding_rate=str(market_data.get('next_funding_rate', '0')),
            created_at=int(market_data.get('created_at', 0)),
            fetched_at=datetime.now()
        )

    async def _store_market_info(self, record: MarketInfoRecord) -> None:
        """Store market info record in database."""
        query = """
        INSERT INTO market_info_history
        (symbol, tick_size, min_tick, max_tick, lot_size, max_leverage,
         isolated_only, min_order_size, max_order_size, funding_rate,
         next_funding_rate, created_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        values = (
            record.symbol,
            record.tick_size,
            record.min_tick,
            record.max_tick,
            record.lot_size,
            record.max_leverage,
            record.isolated_only,
            record.min_order_size,
            record.max_order_size,
            record.funding_rate,
            record.next_funding_rate,
            record.created_at,
            record.fetched_at
        )

        async with self.db.get_connection() as conn:
            await conn.execute(query, values)
            await conn.commit()

    async def _detect_parameter_changes(self, current_markets: List[Dict]) -> None:
        """Detect and record changes in market parameters."""
        try:
            # Get previous market data (most recent for each symbol)
            previous_data = await self._get_previous_market_data()

            current_symbols = {m['symbol'] for m in current_markets}
            previous_symbols = set(previous_data.keys())

            # Check for new markets
            new_markets = current_symbols - previous_symbols
            for symbol in new_markets:
                await self._record_market_change(symbol, 'availability', None, 'available', 'added')

            # Check for removed markets
            removed_markets = previous_symbols - current_symbols
            for symbol in removed_markets:
                await self._record_market_change(symbol, 'availability', 'available', None, 'removed')

            # Check for parameter changes in existing markets
            for market in current_markets:
                symbol = market['symbol']
                if symbol in previous_data:
                    await self._compare_market_parameters(symbol, previous_data[symbol], market)

        except Exception as e:
            logger.error(f"Failed to detect parameter changes: {e}")

    async def _get_previous_market_data(self) -> Dict[str, Dict]:
        """Get the most recent market data for each symbol."""
        query = """
        SELECT symbol, tick_size, lot_size, max_leverage, funding_rate,
               next_funding_rate, min_order_size, max_order_size
        FROM market_info_history
        WHERE (symbol, fetched_at) IN (
            SELECT symbol, MAX(fetched_at)
            FROM market_info_history
            GROUP BY symbol
        )
        """

        previous_data = {}
        async with self.db.get_connection() as conn:
            async with conn.execute(query) as cursor:
                async for row in cursor:
                    previous_data[row[0]] = {
                        'tick_size': row[1],
                        'lot_size': row[2],
                        'max_leverage': row[3],
                        'funding_rate': row[4],
                        'next_funding_rate': row[5],
                        'min_order_size': row[6],
                        'max_order_size': row[7]
                    }

        return previous_data

    async def _compare_market_parameters(self, symbol: str, previous: Dict, current: Dict) -> None:
        """Compare parameters between previous and current market data."""
        parameters_to_check = [
            'tick_size', 'lot_size', 'max_leverage', 'funding_rate',
            'next_funding_rate', 'min_order_size', 'max_order_size'
        ]

        for param in parameters_to_check:
            prev_value = str(previous.get(param, ''))
            curr_value = str(current.get(param, ''))

            if prev_value != curr_value:
                await self._record_market_change(symbol, param, prev_value, curr_value, 'modified')

    async def _record_market_change(self, symbol: str, parameter: str,
                                   old_value: Any, new_value: Any, change_type: str) -> None:
        """Record a market parameter change."""
        query = """
        INSERT INTO market_parameter_changes
        (symbol, parameter_name, old_value, new_value, change_type)
        VALUES (?, ?, ?, ?, ?)
        """

        async with self.db.get_connection() as conn:
            await conn.execute(query, (symbol, parameter, str(old_value), str(new_value), change_type))
            await conn.commit()

        logger.info(f"Market change recorded: {symbol}.{parameter} {old_value} -> {new_value} ({change_type})")

    # Analytics methods
    async def get_funding_rate_history(self, symbol: str, days: int = 30) -> List[Dict]:
        """Get funding rate history for a symbol."""
        query = """
        SELECT funding_rate, next_funding_rate, fetched_at
        FROM market_info_history
        WHERE symbol = ?
        AND fetched_at >= datetime('now', '-{} days')
        ORDER BY fetched_at DESC
        """.format(days)

        results = []
        async with self.db.get_connection() as conn:
            async with conn.execute(query, (symbol,)) as cursor:
                async for row in cursor:
                    results.append({
                        'funding_rate': float(row[0]) if row[0] else 0,
                        'next_funding_rate': float(row[1]) if row[1] else 0,
                        'timestamp': row[2]
                    })

        return results

    async def get_market_parameter_changes(self, symbol: str, days: int = 30) -> List[Dict]:
        """Get parameter change history for a symbol."""
        query = """
        SELECT parameter_name, old_value, new_value, change_type, changed_at
        FROM market_parameter_changes
        WHERE symbol = ?
        AND changed_at >= datetime('now', '-{} days')
        ORDER BY changed_at DESC
        """.format(days)

        results = []
        async with self.db.get_connection() as conn:
            async with conn.execute(query, (symbol,)) as cursor:
                async for row in cursor:
                    results.append({
                        'parameter': row[0],
                        'old_value': row[1],
                        'new_value': row[2],
                        'change_type': row[3],
                        'timestamp': row[4]
                    })

        return results

    async def get_market_stats(self) -> Dict:
        """Get overall market statistics."""
        query = """
        SELECT
            COUNT(DISTINCT symbol) as total_markets,
            AVG(CAST(funding_rate AS REAL)) as avg_funding_rate,
            MAX(fetched_at) as last_update
        FROM market_info_history
        WHERE fetched_at >= datetime('now', '-1 day')
        """

        async with self.db.get_connection() as conn:
            async with conn.execute(query) as cursor:
                row = await cursor.fetchone()

        return {
            'total_markets': row[0] if row[0] else 0,
            'avg_funding_rate': row[1] if row[1] else 0,
            'last_update': row[2] if row[2] else None
        }