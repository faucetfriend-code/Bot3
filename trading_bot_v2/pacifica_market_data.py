"""
Pacifica Market Data Handler
Centralized management of live market data fetching and caching.

This module provides:
- Live market data fetching from Pacifica.fi API
- In-memory caching with TTL to respect rate limits
- Technical indicator calculations (RSI, EMA, MACD)
- Error handling and graceful degradation
"""

import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from loguru import logger

from .pacifica_client import PacificaClient


class MarketDataCache:
    """In-memory cache for market data with TTL (Time To Live)."""

    def __init__(self):
        self.cache: Dict[str, Tuple[Any, datetime]] = {}  # {key: (data, timestamp)}

    def get(self, key: str, ttl_seconds: int = 10) -> Optional[Any]:
        """
        Get cached data if not stale.

        Args:
            key: Cache key
            ttl_seconds: Time to live in seconds

        Returns:
            Cached data if fresh, None if stale or missing
        """
        if key not in self.cache:
            return None

        data, timestamp = self.cache[key]
        age = (datetime.now() - timestamp).total_seconds()

        if age > ttl_seconds:
            del self.cache[key]
            return None

        return data

    def set(self, key: str, data: Any) -> None:
        """
        Cache data with current timestamp.

        Args:
            key: Cache key
            data: Data to cache
        """
        self.cache[key] = (data, datetime.now())

    def is_stale(self, key: str, ttl_seconds: int = 10) -> bool:
        """
        Check if cache entry is stale or missing.

        Args:
            key: Cache key
            ttl_seconds: Time to live in seconds

        Returns:
            True if stale or missing, False if fresh
        """
        return self.get(key, ttl_seconds) is None

    def clear(self) -> None:
        """Clear all cached data."""
        self.cache.clear()

    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        return {"total_entries": len(self.cache), "keys": list(self.cache.keys())}


class PacificaMarketDataHandler:
    """Handle all Pacifica market data operations with caching."""

    def __init__(
        self,
        pacifica_client: Optional[PacificaClient] = None,
        markets: List[str] = None,
        use_mock: bool = False,
    ):
        """
        Initialize market data handler.

        Args:
            pacifica_client: Initialized PacificaClient instance (None for mock mode)
            markets: List of markets to track (default: BTC, ETH, SOL, XRP)
            use_mock: Force mock mode even with valid client
        """
        self.client = pacifica_client
        self.cache = MarketDataCache()
        # Pacifica uses symbols without -PERP suffix (e.g., "BTC", "ETH")
        self.markets = markets or ["BTC", "ETH", "SOL", "XRP"]
        self.use_mock = use_mock or (pacifica_client is None)

        mode = "MOCK MODE" if self.use_mock else "LIVE API"
        logger.info(
            f"Initialized PacificaMarketDataHandler [{mode}] for markets: {', '.join(self.markets)}"
        )

    def _generate_mock_ticker(self, market: str) -> Dict:
        """
        Generate ticker data from order book API with database fallback.
        """
        try:
            # Try to fetch from order book API
            order_book = self._fetch_order_book_api(market)
            ticker_data = self._extract_ticker_from_order_book(order_book, market)

            # Enhance with database specs if available
            ticker_data = self._enhance_ticker_with_db_specs(ticker_data, market)

            return ticker_data

        except Exception as e:
            logger.warning(f"Order book API failed for {market}: {e}")
            # Fall back to database data
            return self._get_ticker_from_database(market)

    def _fetch_order_book_api(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch order book data from Pacifica API.
        """
        import requests

        try:
            base_url = "https://api.pacifica.fi/api/v1"
            url = f"{base_url}/book?symbol={symbol}"

            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("success"):
                return data.get("data", {})
            else:
                raise Exception(
                    f"API returned error: {data.get('error', 'Unknown error')}"
                )

        except Exception as e:
            logger.warning(f"Failed to fetch order book for {symbol}: {e}")
            raise

    def _extract_ticker_from_order_book(
        self, order_book: Dict[str, Any], symbol: str
    ) -> Dict[str, Any]:
        """
        Extract ticker data from order book.
        """
        try:
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

            if not bids or not asks:
                raise Exception("Order book missing bids or asks")

            # Get best bid and ask
            best_bid = float(bids[0][0]) if bids else 0.0
            best_ask = float(asks[0][0]) if asks else 0.0

            # Calculate mid price
            mid_price = (
                (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
            )

            return {
                "symbol": symbol,
                "last_price": mid_price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mark_price": mid_price,
                "index_price": mid_price,
                "volume_24h": 0.0,  # Not available in order book
                "high_24h": 0.0,  # Not available
                "low_24h": 0.0,  # Not available
                "change_24h": 0.0,  # Not available
                "funding_rate": 0.0,  # Will be enhanced from DB
                "timestamp": int(time.time() * 1000),
                "source": "order_book_api",
            }

        except Exception as e:
            logger.error(f"Failed to extract ticker from order book for {symbol}: {e}")
            raise

    def _enhance_ticker_with_db_specs(self, ticker_data: Dict, symbol: str) -> Dict:
        """
        Enhance ticker data with market specs from database.
        """
        try:
            # Get market specs from database
            from market_data_collector import MarketDataCollector
            from database import DatabaseManager
            import asyncio

            db_manager = DatabaseManager()
            collector = MarketDataCollector(db_manager)

            recent_data = asyncio.run(collector._get_previous_market_data())

            if symbol in recent_data:
                db_specs = recent_data[symbol]
                ticker_data.update(
                    {
                        "tick_size": float(db_specs.get("tick_size", 0)),
                        "lot_size": float(db_specs.get("lot_size", 0)),
                        "funding_rate": float(db_specs.get("funding_rate", 0)),
                        "next_funding_rate": float(
                            db_specs.get("next_funding_rate", 0)
                        ),
                    }
                )

        except Exception as e:
            logger.debug(f"Failed to enhance ticker with DB specs for {symbol}: {e}")

        return ticker_data

    def _get_ticker_from_database(self, symbol: str) -> Dict:
        """
        Get ticker data from database as fallback.
        """
        try:
            from market_data_collector import MarketDataCollector
            from database import DatabaseManager
            import asyncio

            db_manager = DatabaseManager()
            collector = MarketDataCollector(db_manager)

            recent_data = asyncio.run(collector._get_previous_market_data())

            if symbol in recent_data:
                db_data = recent_data[symbol]
                return {
                    "symbol": symbol,
                    "last_price": 0.0,
                    "best_bid": 0.0,
                    "best_ask": 0.0,
                    "mark_price": 0.0,
                    "index_price": 0.0,
                    "volume_24h": 0.0,
                    "high_24h": 0.0,
                    "low_24h": 0.0,
                    "change_24h": 0.0,
                    "tick_size": float(db_data.get("tick_size", 0)),
                    "lot_size": float(db_data.get("lot_size", 0)),
                    "funding_rate": float(db_data.get("funding_rate", 0)),
                    "next_funding_rate": float(db_data.get("next_funding_rate", 0)),
                    "timestamp": int(time.time() * 1000),
                    "source": "database_fallback",
                }
            else:
                raise Exception(f"No database data available for {symbol}")

        except Exception as e:
            logger.error(f"Database ticker fallback failed for {symbol}: {e}")
            # Return minimal ticker data
            return {
                "symbol": symbol,
                "last_price": 0.0,
                "best_bid": 0.0,
                "best_ask": 0.0,
                "mark_price": 0.0,
                "index_price": 0.0,
                "volume_24h": 0.0,
                "high_24h": 0.0,
                "low_24h": 0.0,
                "change_24h": 0.0,
                "tick_size": 0.0,
                "lot_size": 0.0,
                "funding_rate": 0.0,
                "timestamp": int(time.time() * 1000),
                "source": "error_fallback",
                "error": str(e),
            }

    def get_ticker(
        self, market: str, use_cache: bool = True, cache_ttl: int = 10
    ) -> Optional[Dict]:
        """
        Get ticker data for a specific market with optional caching.

        Args:
            market: Market symbol (e.g., "BTC-PERP")
            use_cache: Whether to use cached data
            cache_ttl: Cache time-to-live in seconds

        Returns:
            Ticker data dict or None if failed
        """
        cache_key = f"ticker_{market}"

        if use_cache:
            cached = self.cache.get(cache_key, cache_ttl)
            if cached:
                logger.debug(f"Using cached ticker for {market}")
                return cached

        # Use mock data if in mock mode
        if self.use_mock:
            ticker = self._generate_mock_ticker(market)
            self.cache.set(cache_key, ticker)
            logger.debug(f"Generated mock ticker for {market}")
            return ticker

        # Try live API
        try:
            ticker = self.client.get_ticker(market)
            self.cache.set(cache_key, ticker)
            logger.debug(f"Fetched fresh ticker for {market}")
            return ticker
        except Exception as e:
            logger.warning(
                f"Live API failed for {market}: {e}, falling back to mock data"
            )
            ticker = self._generate_mock_ticker(market)
            self.cache.set(cache_key, ticker)
            return ticker

    def get_all_tickers(self, use_cache: bool = True) -> Dict[str, Dict]:
        """
        Get tickers for all configured markets.

        Args:
            use_cache: Whether to use cached data

        Returns:
            Dict mapping market symbols to ticker data
        """
        tickers = {}

        for market in self.markets:
            ticker = self.get_ticker(market, use_cache=use_cache)
            if ticker:
                tickers[market] = {
                    "symbol": market,
                    "price": ticker.get("last_price", 0),
                    "bid": ticker.get("best_bid", 0),
                    "ask": ticker.get("best_ask", 0),
                    "volume": ticker.get("volume_24h", 0),
                    "funding_rate": ticker.get("funding_rate", 0),
                    "mark_price": ticker.get("mark_price", 0),
                    "change_24h": ticker.get(
                        "change_24h", 0
                    ),  # Required by frontend validation
                    "high_24h": ticker.get("high_24h", 0),
                    "low_24h": ticker.get("low_24h", 0),
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                tickers[market] = {
                    "symbol": market,
                    "error": "Failed to fetch ticker data",
                }

        return tickers

    def _generate_mock_candles(
        self, market: str, interval: str, limit: int
    ) -> List[Dict]:
        """
        Generate candle data from Kline API with database fallback.
        """
        try:
            # Try to fetch from Kline API
            candles = self._fetch_candles_api(market, interval, limit)
            return candles

        except Exception as e:
            logger.warning(f"Kline API failed for {market}: {e}")
            # Return empty list as fallback
            return []

    def _fetch_candles_api(self, symbol: str, interval: str, limit: int) -> List[Dict]:
        """
        Fetch candle data from Pacifica Kline API.
        """
        import requests

        try:
            base_url = "https://api.pacifica.fi/api/v1"
            url = f"{base_url}/kline"

            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": min(limit, 1000),  # API might have limits
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("success"):
                # Transform to expected format
                raw_candles = data.get("data", [])
                candles = []
                for candle in raw_candles:
                    if isinstance(candle, list) and len(candle) >= 6:
                        candles.append(
                            {
                                "timestamp": int(candle[0]),
                                "open": float(candle[1]),
                                "high": float(candle[2]),
                                "low": float(candle[3]),
                                "close": float(candle[4]),
                                "volume": float(candle[5]),
                            }
                        )
                return candles
            else:
                raise Exception(
                    f"API returned error: {data.get('error', 'Unknown error')}"
                )

        except Exception as e:
            logger.warning(f"Failed to fetch candles for {symbol}: {e}")
            raise

    def _generate_mock_funding_rate(self, market: str) -> Dict:
        """
        Generate funding rate data from Funding API with database fallback.
        """
        try:
            # Try to fetch from Funding API
            funding_data = self._fetch_funding_rate_api(market)
            return funding_data

        except Exception as e:
            logger.warning(f"Funding API failed for {market}: {e}")
            # Fall back to database data
            return self._get_funding_rate_from_database(market)

    def _fetch_funding_rate_api(self, symbol: str) -> Dict:
        """
        Fetch funding rate data from Pacifica Funding API.
        """
        import requests

        try:
            base_url = "https://api.pacifica.fi/api/v1"
            url = f"{base_url}/funding"

            params = {
                "symbol": symbol,
                "limit": 1,  # Get latest
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("success"):
                funding_history = data.get("data", {}).get("funding_history", [])
                if funding_history:
                    latest = funding_history[0]
                    return {
                        "symbol": symbol,
                        "funding_rate": float(latest.get("funding_rate", 0)),
                        "next_funding_rate": float(latest.get("next_funding_rate", 0)),
                        "funding_time": int(latest.get("funding_time", 0)),
                        "countdown": int(latest.get("countdown", 0)),
                        "source": "funding_api",
                    }
                else:
                    raise Exception("No funding history available")
            else:
                raise Exception(
                    f"API returned error: {data.get('error', 'Unknown error')}"
                )

        except Exception as e:
            logger.warning(f"Failed to fetch funding rate for {symbol}: {e}")
            raise

    def _get_funding_rate_from_database(self, symbol: str) -> Dict:
        """
        Get funding rate data from database as fallback.
        """
        try:
            from market_data_collector import MarketDataCollector
            from database import DatabaseManager
            import asyncio

            db_manager = DatabaseManager()
            collector = MarketDataCollector(db_manager)

            recent_data = asyncio.run(collector._get_previous_market_data())

            if symbol in recent_data:
                db_data = recent_data[symbol]
                return {
                    "symbol": symbol,
                    "funding_rate": float(db_data.get("funding_rate", 0)),
                    "next_funding_rate": float(db_data.get("next_funding_rate", 0)),
                    "funding_time": 0,
                    "countdown": 0,
                    "source": "database_fallback",
                }
            else:
                raise Exception(f"No database data available for {symbol}")

        except Exception as e:
            logger.error(f"Database funding rate fallback failed for {symbol}: {e}")
            return {
                "symbol": symbol,
                "funding_rate": 0.0,
                "next_funding_rate": 0.0,
                "funding_time": 0,
                "countdown": 0,
                "source": "error_fallback",
                "error": str(e),
            }

    def _generate_mock_market_specs(self, market: str) -> Dict:
        """
        Generate market specifications from Info API with database fallback.
        """
        try:
            # Try to fetch from Info API
            specs = self._fetch_market_specs_api(market)
            return specs

        except Exception as e:
            logger.warning(f"Market specs API failed for {market}: {e}")
            # Fall back to database data
            return self._get_market_specs_from_database(market)

    def _fetch_market_specs_api(self, symbol: str) -> Dict:
        """
        Fetch market specifications from Pacifica Info API.
        """
        import requests

        try:
            base_url = "https://api.pacifica.fi/api/v1"
            url = f"{base_url}/info"

            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("success"):
                markets = data.get("data", [])
                for market in markets:
                    if market.get("symbol", "").upper() == symbol.upper():
                        return {
                            "symbol": market.get("symbol"),
                            "tick_size": float(market.get("tick_size", 0)),
                            "lot_size": float(market.get("lot_size", 0)),
                            "min_order_size": float(market.get("min_order_size", 0)),
                            "max_order_size": float(market.get("max_order_size", 0)),
                            "max_leverage": int(market.get("max_leverage", 1)),
                            "funding_rate": float(market.get("funding_rate", 0)),
                            "next_funding_rate": float(
                                market.get("next_funding_rate", 0)
                            ),
                            "source": "info_api",
                        }
                raise Exception(f"Market {symbol} not found in API response")
            else:
                raise Exception(
                    f"API returned error: {data.get('error', 'Unknown error')}"
                )

        except Exception as e:
            logger.warning(f"Failed to fetch market specs for {symbol}: {e}")
            raise

    def _get_market_specs_from_database(self, symbol: str) -> Dict:
        """
        Get market specifications from database as fallback.
        """
        try:
            from market_data_collector import MarketDataCollector
            from database import DatabaseManager
            import asyncio

            db_manager = DatabaseManager()
            collector = MarketDataCollector(db_manager)

            recent_data = asyncio.run(collector._get_previous_market_data())

            if symbol in recent_data:
                db_data = recent_data[symbol]
                return {
                    "symbol": symbol,
                    "tick_size": float(db_data.get("tick_size", 0)),
                    "lot_size": float(db_data.get("lot_size", 0)),
                    "min_order_size": float(db_data.get("min_order_size", 0)),
                    "max_order_size": float(db_data.get("max_order_size", 0)),
                    "max_leverage": int(db_data.get("max_leverage", 1)),
                    "funding_rate": float(db_data.get("funding_rate", 0)),
                    "next_funding_rate": float(db_data.get("next_funding_rate", 0)),
                    "source": "database_fallback",
                }
            else:
                raise Exception(f"No database data available for {symbol}")

        except Exception as e:
            logger.error(f"Database market specs fallback failed for {symbol}: {e}")
            return {
                "symbol": symbol,
                "tick_size": 0.0,
                "lot_size": 0.0,
                "min_order_size": 0.0,
                "max_order_size": 0.0,
                "max_leverage": 1,
                "funding_rate": 0.0,
                "next_funding_rate": 0.0,
                "source": "error_fallback",
                "error": str(e),
            }

    def get_candles(
        self,
        market: str,
        interval: str = "1h",
        limit: int = 100,
        use_cache: bool = True,
    ) -> Optional[List[Dict]]:
        """
        Get candlestick (OHLCV) data for a market.

        Args:
            market: Market symbol
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of candles to fetch (max 1000)
            use_cache: Whether to use cached data

        Returns:
            List of candle dicts or None if failed
        """
        cache_key = f"candles_{market}_{interval}"

        if use_cache:
            cached = self.cache.get(cache_key, ttl_seconds=60)
            if cached:
                logger.debug(f"Using cached candles for {market} {interval}")
                return cached

        # Use mock data if in mock mode
        if self.use_mock:
            candles = self._generate_mock_candles(market, interval, limit)
            self.cache.set(cache_key, candles)
            logger.debug(
                f"Generated {len(candles)} mock candles for {market} {interval}"
            )
            return candles

        # Try live API
        try:
            candles = self.client.get_candles(market, interval, limit=limit)
            self.cache.set(cache_key, candles)
            logger.debug(
                f"Fetched {len(candles) if candles else 0} candles for {market} {interval}"
            )
            return candles
        except Exception as e:
            logger.warning(
                f"Live API failed for candles {market}: {e}, falling back to mock data"
            )
            candles = self._generate_mock_candles(market, interval, limit)
            self.cache.set(cache_key, candles)
            return candles

    def get_funding_rate(self, market: str, use_cache: bool = True) -> Optional[Dict]:
        """
        Get current funding rate for a market.

        ⚠️ CRITICAL: Pacifica charges funding HOURLY (24x per day)

        Args:
            market: Market symbol
            use_cache: Whether to use cached data

        Returns:
            Funding rate data dict or None if failed
        """
        cache_key = f"funding_{market}"

        if use_cache:
            cached = self.cache.get(cache_key, ttl_seconds=300)  # 5 minute cache
            if cached:
                logger.debug(f"Using cached funding rate for {market}")
                return cached

        # Use mock data if in mock mode
        if self.use_mock:
            funding = self._generate_mock_funding_rate(market)
            self.cache.set(cache_key, funding)
            logger.debug(f"Generated mock funding rate for {market}")
            return funding

        # Try live API
        try:
            funding = self.client.get_funding_rate(market)
            self.cache.set(cache_key, funding)
            logger.debug(f"Fetched funding rate for {market}")
            return funding
        except Exception as e:
            logger.warning(
                f"Live API failed for funding rate {market}: {e}, falling back to mock data"
            )
            funding = self._generate_mock_funding_rate(market)
            self.cache.set(cache_key, funding)
            return funding

    def get_funding_rates_all(self, use_cache: bool = True) -> Dict[str, Dict]:
        """
        Get funding rates for all configured markets.

        Returns rates in hourly, daily, and annual formats.

        Args:
            use_cache: Whether to use cached data

        Returns:
            Dict mapping market symbols to funding data
        """
        rates = {}

        for market in self.markets:
            funding = self.get_funding_rate(market, use_cache=use_cache)
            if funding:
                hourly = funding.get("funding_rate", 0)
                rates[market] = {
                    "market": market,
                    "hourly_rate": hourly,
                    "hourly_rate_pct": round(hourly * 100, 4),
                    "daily_rate": hourly * 24,  # CRITICAL: Pacifica is hourly!
                    "daily_rate_pct": round(hourly * 24 * 100, 4),
                    "annual_rate": hourly * 24 * 365,
                    "annual_rate_pct": round(hourly * 24 * 365 * 100, 2),
                    "next_payment": funding.get("next_funding_time"),
                    "mark_price": funding.get("mark_price"),
                    "index_price": funding.get("index_price"),
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                rates[market] = {
                    "market": market,
                    "error": "Failed to fetch funding rate",
                }

        return rates

    def get_market_specs(self, market: str, use_cache: bool = True) -> Optional[Dict]:
        """
        Get market specifications (tick size, lot size, leverage limits).

        Args:
            market: Market symbol
            use_cache: Whether to use cached data

        Returns:
            Market specs dict or None if failed
        """
        cache_key = f"specs_{market}"

        if use_cache:
            cached = self.cache.get(
                cache_key, ttl_seconds=3600
            )  # 1 hour cache (rarely changes)
            if cached:
                return cached

        # Use mock data if in mock mode
        if self.use_mock:
            specs = self._generate_mock_market_specs(market)
            self.cache.set(cache_key, specs)
            logger.debug(f"Generated mock market specs for {market}")
            return specs

        # Try live API
        try:
            specs = self.client.get_market_specs(market)
            self.cache.set(cache_key, specs)
            logger.debug(f"Fetched market specs for {market}")
            return specs
        except Exception as e:
            logger.warning(
                f"Live API failed for market specs {market}: {e}, falling back to mock data"
            )
            specs = self._generate_mock_market_specs(market)
            self.cache.set(cache_key, specs)
            return specs

    def get_all_market_specs(self, use_cache: bool = True) -> Dict[str, Dict]:
        """Get market specifications for all configured markets."""
        specs = {}

        for market in self.markets:
            spec = self.get_market_specs(market, use_cache=use_cache)
            if spec:
                specs[market] = spec
            else:
                specs[market] = {"market": market, "error": "Failed to fetch specs"}

        return specs


# Technical Indicator Calculation Functions


def calculate_rsi(closes: List[float], period: int = 14) -> float:
    """
    Calculate Relative Strength Index (RSI).

    Args:
        closes: List of closing prices
        period: RSI period (default 14)

    Returns:
        RSI value (0-100)
    """
    if len(closes) < period + 1:
        return 50.0  # Neutral RSI if insufficient data

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    seed = deltas[:period]

    up = sum([x for x in seed if x > 0]) / period
    down = -sum([x for x in seed if x < 0]) / period

    if down == 0:
        return 100.0

    rs = up / down
    rsi = 100.0 - 100.0 / (1.0 + rs)

    # Continue with exponential moving average for remaining periods
    for d in deltas[period:]:
        if d > 0:
            up = (up * (period - 1) + d) / period
            down = (down * (period - 1)) / period
        else:
            up = (up * (period - 1)) / period
            down = (down * (period - 1) - d) / period

        if down == 0:
            rsi = 100.0
        else:
            rs = up / down
            rsi = 100.0 - 100.0 / (1.0 + rs)

    return rsi


def calculate_ema(prices: List[float], period: int) -> float:
    """
    Calculate Exponential Moving Average (EMA).

    Args:
        prices: List of prices
        period: EMA period

    Returns:
        EMA value
    """
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0

    multiplier = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = price * multiplier + ema * (1.0 - multiplier)

    return ema


def calculate_sma(prices: List[float], period: int) -> float:
    """
    Calculate Simple Moving Average (SMA).

    Args:
        prices: List of prices
        period: SMA period

    Returns:
        SMA value
    """
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0

    return sum(prices[-period:]) / period


def calculate_macd(
    prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Dict[str, float]:
    """
    Calculate MACD (Moving Average Convergence Divergence).

    Args:
        prices: List of prices
        fast: Fast EMA period (default 12)
        slow: Slow EMA period (default 26)
        signal: Signal line period (default 9)

    Returns:
        Dict with macd_line, signal_line, histogram
    """
    if len(prices) < slow:
        return {"macd_line": 0, "signal_line": 0, "histogram": 0}

    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow

    # Calculate signal line (EMA of MACD line)
    # For simplicity, we'll use the current MACD value
    # In a full implementation, you'd calculate EMA of historical MACD values
    signal_line = macd_line  # Simplified

    histogram = macd_line - signal_line

    return {"macd_line": macd_line, "signal_line": signal_line, "histogram": histogram}
