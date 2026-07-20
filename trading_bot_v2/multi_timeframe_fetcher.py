"""
Multi-Timeframe Data Fetcher

Fetches and caches market data across multiple timeframes for indicator
calculations, regime detection, and precise entry execution.

Timeframe Hierarchy:
- REGIME_TIMEFRAMES (15m, 1h, 4h): Used for regime detection and strategy enablement
- EXECUTION_TIMEFRAMES (1m, 5m): Used for precise entry timing (never affects regime)

Features:
- Multi-timeframe candle fetching with tiered caching
- WebSocket-first data sourcing with REST fallback
- Automatic stale cache cleanup
- Graceful error handling
"""

import asyncio
import concurrent.futures

# Timeframe constants for clear separation of concerns
# Regime timeframes: Used for market state detection and strategy enablement
REGIME_TIMEFRAMES = ["15m", "1h", "4h"]

# Execution timeframes: Used for precise entry timing (NEVER affects regime)
EXECUTION_TIMEFRAMES = ["1m", "5m"]

# All supported timeframes
ALL_TIMEFRAMES = (
    EXECUTION_TIMEFRAMES + REGIME_TIMEFRAMES
)  # ["1m", "5m", "15m", "1h", "4h"]
import concurrent.futures
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from loguru import logger
from .pacifica_client import PacificaClient
from .data_validation import DataValidator


class MultiTimeframeFetcher:
    """
    Fetches and caches market data across multiple timeframes.

    Caches data with TTL to avoid redundant API calls and respect rate limits.
    """

    # Tiered cache TTL by timeframe (lower TFs need fresher data)
    TIERED_TTL = {
        "1m": 60,  # 1 minute cache for 1m candles
        "5m": 120,  # 2 minute cache for 5m candles
        "15m": 300,  # 5 minute cache for 15m+ candles
        "1h": 300,
        "4h": 300,
    }

    def __init__(
        self, client: PacificaClient, ws_client=None, cache_ttl_seconds: int = 300
    ):
        """
        Initialize MultiTimeframeFetcher.

        Args:
            client: PacificaClient instance for API calls
            ws_client: Optional PacificaWebSocketClient for real-time data
            cache_ttl_seconds: Default cache TTL (overridden by TIERED_TTL for specific TFs)
        """
        self.client = client
        self.ws_client = ws_client
        self.cache_ttl = cache_ttl_seconds  # Default TTL (used if TF not in TIERED_TTL)
        self.cache: Dict[Tuple[str, str], Tuple[Dict, datetime]] = {}

        # Instantiated once (not per-call) since DataValidator is stateless
        # per-invocation but construction does I/O-free setup only.
        self.data_validator = DataValidator()

        logger.info(
            f"MultiTimeframeFetcher initialized with tiered TTL (1m:60s, 5m:120s, 15m+:300s), WS client: {ws_client is not None}"
        )

    def get_candles_multi_tf(
        self, symbol: str, timeframes: List[str] = None, lookback_candles: int = 200
    ) -> Dict[str, Dict[str, List[float]]]:
        """
        Fetch candles for multiple timeframes with intelligent data sources.

        Data source priority:
        1. WebSocket real-time data (if available)
        2. Local cache (tiered TTL: 1m=60s, 5m=120s, 15m+=300s)
        3. REST API with caching (parallel fetching for performance)

        Args:
            symbol: Trading pair (e.g., "SUI-PERP" or "BTC"). -PERP suffix is handled automatically.
            timeframes: List of intervals. Defaults to ALL_TIMEFRAMES ["1m", "5m", "15m", "1h", "4h"]
                        Use REGIME_TIMEFRAMES for regime detection only.
                        Use EXECUTION_TIMEFRAMES for entry timing only.
            lookback_candles: Number of candles to fetch per timeframe (default: 200)

        Returns:
            Dictionary mapping timeframe to OHLCV data:
            {
                "1m": {...},
                "5m": {...},
                "15m": {
                    "high": [100.5, 101.2, ...],
                    "low": [99.8, 100.1, ...],
                    "close": [100.2, 100.9, ...],
                    "open": [100.0, 100.3, ...],
                    "volume": [1000.0, 1200.0, ...]
                },
                "1h": {...},
                "4h": {...}
            }

        Raises:
            ValueError: If API call fails or data is invalid
        """
        # Default to all timeframes if not specified
        if timeframes is None:
            timeframes = ALL_TIMEFRAMES  # ["1m", "5m", "15m", "1h", "4h"]

        # FAST PATH: Try synchronous WebSocket data first (no async needed)
        # WebSocket data is stored in memory - just a dict lookup
        result = {}
        if self.ws_client:
            import re
            ws_symbol = re.sub(r"-perp$", "", symbol, flags=re.IGNORECASE).upper()
            logger.info(f"FAST PATH: Checking WS cache for {ws_symbol}, timeframes={timeframes}")
            for tf in timeframes:
                ws_data = self.ws_client.get_kline_data(ws_symbol, tf)
                # 5m is execution timing only; accept fewer WS candles to avoid slow REST fallback.
                min_candles_needed = min(30 if tf == "5m" else 50, lookback_candles)
                logger.debug(f"FAST PATH: {ws_symbol}_{tf} - ws_data={len(ws_data) if ws_data else 'None'}, need={min_candles_needed}")
                if ws_data and len(ws_data) >= min_candles_needed:
                    candles_to_use = ws_data[-lookback_candles:] if len(ws_data) >= lookback_candles else ws_data
                    parsed_data = self._parse_candles(candles_to_use)
                    close_count = len(parsed_data.get("close", [])) if parsed_data else 0
                    logger.debug(f"FAST PATH: {ws_symbol}_{tf} - parsed close_count={close_count}")
                    if parsed_data and parsed_data.get("close"):
                        result[tf] = parsed_data
                        logger.info(f"✅ WS SYNC HIT: {symbol} {tf} - {len(candles_to_use)} candles")
                    else:
                        logger.warning(f"❌ WS SYNC MISS: {symbol} {tf} - parse failed, close_count={close_count}")
                else:
                    logger.warning(f"❌ WS SYNC MISS: {symbol} {tf} - insufficient data: {len(ws_data) if ws_data else 0} < {min_candles_needed}")
        else:
            logger.warning(f"FAST PATH: No ws_client available for {symbol}")

        # If we got all timeframes from WebSocket, return immediately
        if len(result) == len(timeframes):
            logger.info(f"✅ All {len(timeframes)} timeframes served from WebSocket for {symbol}")
            return result

        # Log fast path partial success
        if result:
            logger.info(f"FAST PATH: Got {len(result)}/{len(timeframes)} timeframes from WebSocket for {symbol}: {list(result.keys())}")

        # SLOW PATH: Fall back to async for missing timeframes
        missing_tfs = [tf for tf in timeframes if tf not in result]
        if missing_tfs:
            logger.info(f"SLOW PATH: Fetching missing TFs for {symbol}: {missing_tfs} via REST API")
            try:
                # Try to get the running event loop (FastAPI case)
                loop = asyncio.get_running_loop()
                # Create a new event loop in a thread for the async work
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._fetch_all_timeframes_parallel(symbol, missing_tfs, lookback_candles)
                    )
                    rest_result = future.result(timeout=15.0)
                    result.update(rest_result or {})
            except RuntimeError:
                # No event loop running (standalone usage), safe to use asyncio.run()
                rest_result = asyncio.run(
                    self._fetch_all_timeframes_parallel(symbol, missing_tfs, lookback_candles)
                )
                result.update(rest_result or {})
            except concurrent.futures.TimeoutError:
                logger.error(f"Timeout fetching REST data for {symbol} {missing_tfs}")
            except Exception as e:
                logger.error(f"Error fetching REST data for {symbol}: {type(e).__name__}: {e}")

        if not result:
            raise ValueError(
                f"Failed to fetch data for any timeframe for {symbol}. "
                f"Requested: {timeframes}, WS client available: {self.ws_client is not None}"
            )

        return result

    async def _fetch_all_timeframes_parallel(
        self, symbol: str, timeframes: List[str], lookback_candles: int
    ) -> Dict[str, Dict[str, List[float]]]:
        """Fetch data for all timeframes in parallel for better performance."""
        result = {}

        # Create tasks for each timeframe
        tasks = []
        for tf in timeframes:
            task = asyncio.create_task(
                self._fetch_single_timeframe(symbol, tf, lookback_candles)
            )
            tasks.append((tf, task))

        # Wait for all tasks to complete
        completed_results = await asyncio.gather(
            *[task for _, task in tasks], return_exceptions=True
        )

        # Process results
        for (tf, _), completed_result in zip(tasks, completed_results):
            if isinstance(completed_result, Exception):
                logger.error(f"Error fetching {symbol} {tf}: {completed_result}")
                continue
            elif completed_result is not None:
                result[tf] = completed_result

        return result

    async def _fetch_single_timeframe(
        self, symbol: str, tf: str, lookback_candles: int
    ) -> Optional[Dict[str, List[float]]]:
        """Fetch data for a single timeframe with priority checking."""
        try:
            # Priority 1: Check WebSocket real-time data first
            logger.debug(f"WS client available: {self.ws_client is not None}")
            if self.ws_client:
                # Normalize symbol for WebSocket lookup (remove -PERP suffix, case-insensitive)
                import re

                ws_symbol = re.sub(r"-perp$", "", symbol, flags=re.IGNORECASE).upper()
                ws_data = self.ws_client.get_kline_data(ws_symbol, tf)
                # Log what we're looking for and what we found
                logger.debug(
                    f"WS lookup: symbol={symbol}, ws_symbol={ws_symbol}, tf={tf}, found={len(ws_data) if ws_data else 0} candles"
                )
                # Use WebSocket data only if we have enough for trading strategies:
                # - Regime detection needs 29 candles (ADX calculation)
                # - MA crossover needs 200+ candles (200 MA)
                # - Use 50 as minimum to ensure basic regime detection works
                # 5m is execution timing only; accept fewer WS candles to avoid slow REST fallback.
                # Otherwise, fall back to REST API for historical data
                min_candles_needed = min(30 if tf == "5m" else 50, lookback_candles)
                if ws_data and len(ws_data) >= min_candles_needed:
                    # ws_data is already in REST API format, so parse it directly
                    candles_to_use = (
                        ws_data[-lookback_candles:]
                        if len(ws_data) >= lookback_candles
                        else ws_data
                    )
                    parsed_data = self._parse_candles(candles_to_use)
                    logger.info(
                        f"WS HIT: {symbol} {tf} - using {len(candles_to_use)} candles (requested {lookback_candles})"
                    )
                    return parsed_data

            # Priority 2: Check cache
            # Normalize symbol for consistent caching (remove -PERP suffix, case-insensitive)
            import re

            normalized_symbol = re.sub(
                r"-perp$", "", symbol, flags=re.IGNORECASE
            ).upper()
            cache_key = (normalized_symbol, tf)
            if self._is_cache_valid(cache_key):
                cached_data, cached_time = self.cache[cache_key]
                logger.debug(
                    f"Cache HIT: {symbol} {tf} "
                    f"(age: {(datetime.now() - cached_time).total_seconds():.1f}s)"
                )
                return cached_data

            # Priority 3: Cache miss - fetch from REST API
            logger.debug(f"Cache MISS: {symbol} {tf} - fetching from REST API")

            # Calculate start_time based on interval and lookback
            interval_minutes = self._interval_to_minutes(tf)
            start_time = datetime.now() - timedelta(
                minutes=interval_minutes * lookback_candles
            )

            # Fetch from API using ThreadPoolExecutor for blocking call
            # Note: PacificaClient.get_candles handles symbol normalization internally
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                candles = await loop.run_in_executor(
                    executor,
                    lambda: self.client.get_candles(
                        market=symbol,
                        interval=tf,
                        start_time=int(start_time.timestamp() * 1000),
                        limit=lookback_candles,
                    ),
                )

            if not candles:
                logger.warning(f"No candles returned for {symbol} {tf}")
                return None

            # Log raw candle format for debugging
            if candles:
                sample_keys = list(candles[0].keys())
                logger.info(
                    f"{symbol} {tf}: Raw candle keys: {sample_keys}, count: {len(candles)}"
                )

            # Parse and cache (use normalized symbol for cache key)
            parsed_data = self._parse_candles(candles)
            self.cache[cache_key] = (parsed_data, datetime.now())

            # Get timestamp from first candle (handle both key formats)
            first_ts = candles[0].get("t") or candles[0].get("timestamp", "unknown")
            last_ts = candles[-1].get("t") or candles[-1].get("timestamp", "unknown")
            logger.info(
                f"Fetched {len(candles)} candles for {symbol} {tf} "
                f"(range: {first_ts} - {last_ts})"
            )

            return parsed_data

        except Exception as e:
            logger.error(f"Error fetching {symbol} {tf}: {e}")
            return None

    def _interval_to_minutes(self, interval: str) -> int:
        """
        Convert interval string to minutes.

        Args:
            interval: Interval string (e.g., "1m", "5m", "15m", "1h", "4h", "1d")

        Returns:
            Number of minutes

        Raises:
            ValueError: If interval format is invalid
        """
        interval = interval.lower().strip()

        # Check for empty string
        if not interval or len(interval) < 2:
            raise ValueError(f"Invalid interval format: '{interval}'")

        # Extract number and unit
        if interval[-1] == "m":
            # Minutes
            try:
                return int(interval[:-1])
            except ValueError:
                raise ValueError(f"Invalid interval format: {interval}")

        elif interval[-1] == "h":
            # Hours
            try:
                hours = int(interval[:-1])
                return hours * 60
            except ValueError:
                raise ValueError(f"Invalid interval format: {interval}")

        elif interval[-1] == "d":
            # Days
            try:
                days = int(interval[:-1])
                return days * 24 * 60
            except ValueError:
                raise ValueError(f"Invalid interval format: {interval}")

        else:
            raise ValueError(f"Unknown interval unit: {interval}")

    def _is_cache_valid(self, cache_key: Tuple[str, str]) -> bool:
        """
        Check if cached data is still valid based on tiered TTL.

        Uses TIERED_TTL for timeframe-specific cache durations:
        - 1m: 60s (execution data needs to be fresh)
        - 5m: 120s
        - 15m+: 300s (regime data can be cached longer)

        Args:
            cache_key: Tuple of (symbol, timeframe)

        Returns:
            True if cache exists and is not stale, False otherwise
        """
        if cache_key not in self.cache:
            return False

        _, cached_time = self.cache[cache_key]
        age_seconds = (datetime.now() - cached_time).total_seconds()

        # Use tiered TTL based on timeframe
        timeframe = cache_key[1]
        ttl = self.TIERED_TTL.get(timeframe, self.cache_ttl)

        if age_seconds > ttl:
            # Cache is stale, remove it
            logger.debug(
                f"Cache expired for {cache_key} (age: {age_seconds:.1f}s, ttl: {ttl}s)"
            )
            del self.cache[cache_key]
            return False

        return True

    def _validate_candle(self, candle: Dict, use_abbreviated: bool) -> bool:
        """
        Validate a single candle has all required fields with valid numeric data.

        Args:
            candle: Raw candle dictionary
            use_abbreviated: Whether candle uses abbreviated keys (h,l,c,o,v) vs full keys

        Returns:
            True if candle is valid, False otherwise
        """
        if not isinstance(candle, dict):
            return False

        required_keys = (
            ["h", "l", "c", "o", "v"]
            if use_abbreviated
            else ["high", "low", "close", "open", "volume"]
        )

        for key in required_keys:
            value = candle.get(key)
            if value is None:
                return False
            try:
                float_val = float(value)
                # Check for NaN, Inf, or negative prices (volume can be 0)
                if float_val != float_val:  # NaN check
                    return False
                if float_val == float("inf") or float_val == float("-inf"):
                    return False
                # Price values must be positive (volume can be 0)
                if key not in ["v", "volume"] and float_val <= 0:
                    return False
            except (TypeError, ValueError):
                return False

        return True

    def _to_validator_candle(
        self, candle: Dict, use_abbreviated: bool
    ) -> Dict[str, Any]:
        """
        Normalize a raw candle dict to the field names DataValidator expects.

        Args:
            candle: Raw candle dictionary (abbreviated or full keys)
            use_abbreviated: Whether candle uses abbreviated keys (h,l,c,o,v)

        Returns:
            Dictionary with keys: timestamp, open, high, low, close, volume
        """
        if use_abbreviated:
            return {
                "timestamp": candle.get("t") or candle.get("timestamp"),
                "open": candle.get("o"),
                "high": candle.get("h"),
                "low": candle.get("l"),
                "close": candle.get("c"),
                "volume": candle.get("v"),
            }
        return {
            "timestamp": candle.get("timestamp") or candle.get("t"),
            "open": candle.get("open"),
            "high": candle.get("high"),
            "low": candle.get("low"),
            "close": candle.get("close"),
            "volume": candle.get("volume"),
        }

    def _validate_candles_with_data_validator(
        self, candles: List[Dict], use_abbreviated: bool
    ) -> List[Dict]:
        """
        Run candles through the richer DataValidator checks and drop bad ones.

        Applies price-spike, duplicate-timestamp, and chronological-ordering
        checks on top of the basic per-field validation in
        ``_validate_candle``. This is defense-in-depth: it never raises out
        of the fetch path, and simply logs and filters offending candles so
        callers always get a (possibly smaller) list of clean candles.

        Args:
            candles: Candles that already passed ``_validate_candle``
            use_abbreviated: Whether candles use abbreviated keys (h,l,c,o,v)

        Returns:
            Filtered list of candles considered clean by DataValidator
        """
        if not candles:
            return candles

        try:
            normalized = [
                self._to_validator_candle(c, use_abbreviated) for c in candles
            ]
            result = self.data_validator.validate_candle_batch(normalized)

            if result.is_valid:
                return candles

            # Identify indices flagged by ERROR/CRITICAL issues that
            # reference a specific candle so we drop only the bad ones
            # rather than the whole batch.
            bad_indices = set()
            for issue in result.error_issues:
                for token in issue.message.split():
                    if token.isdigit():
                        idx = int(token)
                        if 0 <= idx < len(candles):
                            bad_indices.add(idx)

            if bad_indices:
                logger.warning(
                    f"DataValidator flagged {len(bad_indices)}/{len(candles)} "
                    f"candles as invalid ({result.error_count} errors, "
                    f"{result.warning_count} warnings); dropping them"
                )
                return [c for i, c in enumerate(candles) if i not in bad_indices]

            # Errors present but not attributable to a specific index -
            # log and keep candles as-is rather than discarding good data.
            logger.warning(
                f"DataValidator found {result.error_count} error(s) in candle "
                f"batch that could not be mapped to a specific candle: "
                f"{[i.message for i in result.error_issues[:3]]}"
            )
            return candles

        except Exception as e:
            # Never let validation errors break the fetch path.
            logger.debug(f"DataValidator batch check skipped due to error: {e}")
            return candles

    def _safe_float(self, value, default: float = 0.0) -> float:
        """
        Safely convert value to float with fallback.

        Args:
            value: Value to convert
            default: Default value if conversion fails

        Returns:
            Float value or default
        """
        if value is None:
            return default
        try:
            result = float(value)
            # Check for NaN and Inf
            if result != result or result == float("inf") or result == float("-inf"):
                return default
            return result
        except (TypeError, ValueError):
            return default

    def _parse_candles(self, raw_candles: List[Dict]) -> Dict[str, List[float]]:
        """
        Parse raw API candles into separate OHLCV lists with robust validation.

        Args:
            raw_candles: List of candle dicts from API

        Returns:
            Dictionary with separate lists for each OHLCV component:
            {
                "high": [...],
                "low": [...],
                "close": [...],
                "open": [...],
                "volume": [...],
                "timestamp": [...]
            }

        Note:
            Invalid candles are skipped. If no valid candles are found,
            returns empty lists. Logs warnings for data quality issues.
            The "timestamp" list carries the raw per-candle timestamp value
            unchanged (Pacifica REST/WS candles: epoch milliseconds int under
            "t"/"timestamp"; candles lacking a timestamp yield None). Consumers
            must handle both epoch-ms ints and ISO-8601 strings since backtest
            data uses ISO strings.
        """
        empty_result = {
            "high": [],
            "low": [],
            "close": [],
            "open": [],
            "volume": [],
            "timestamp": [],
        }

        if not raw_candles:
            logger.warning("No candles provided to parse")
            return empty_result

        if not isinstance(raw_candles, list):
            logger.error(f"Expected list of candles, got {type(raw_candles)}")
            return empty_result

        # Detect key format from first valid candle
        sample = raw_candles[0] if raw_candles else {}
        if not isinstance(sample, dict):
            logger.error(f"Invalid candle format: expected dict, got {type(sample)}")
            return empty_result

        use_abbreviated = "h" in sample or "c" in sample or "o" in sample

        # Basic per-field validation - skip structurally invalid candles
        basic_valid_candles = [
            c for c in raw_candles if self._validate_candle(c, use_abbreviated)
        ]
        invalid_count = len(raw_candles) - len(basic_valid_candles)

        # Richer validation (price spikes, duplicate/out-of-order timestamps,
        # large price changes) via the shared DataValidator pipeline. This
        # never raises - on any internal error it falls back to returning
        # the candles unchanged.
        valid_candles = self._validate_candles_with_data_validator(
            basic_valid_candles, use_abbreviated
        )
        invalid_count += len(basic_valid_candles) - len(valid_candles)

        # Parse validated candles into OHLCV lists.
        # "timestamp" keeps the raw source value (epoch-ms int for Pacifica
        # REST/WS candles) so downstream consumers see consistent units.
        parsed = {
            "high": [],
            "low": [],
            "close": [],
            "open": [],
            "volume": [],
            "timestamp": [],
        }

        for candle in valid_candles:
            if use_abbreviated:
                parsed["high"].append(self._safe_float(candle.get("h")))
                parsed["low"].append(self._safe_float(candle.get("l")))
                parsed["close"].append(self._safe_float(candle.get("c")))
                parsed["open"].append(self._safe_float(candle.get("o")))
                parsed["volume"].append(self._safe_float(candle.get("v")))
                parsed["timestamp"].append(candle.get("t", candle.get("timestamp")))
            else:
                parsed["high"].append(self._safe_float(candle.get("high")))
                parsed["low"].append(self._safe_float(candle.get("low")))
                parsed["close"].append(self._safe_float(candle.get("close")))
                parsed["open"].append(self._safe_float(candle.get("open")))
                parsed["volume"].append(self._safe_float(candle.get("volume")))
                parsed["timestamp"].append(
                    candle.get("timestamp", candle.get("t"))
                )

        # Log data quality issues
        if invalid_count > 0:
            logger.warning(
                f"Skipped {invalid_count}/{len(raw_candles)} invalid candles during parsing"
            )

        if not parsed["close"]:
            logger.error("No valid candles after parsing - all data invalid")
            return empty_result

        key_format = "abbreviated" if use_abbreviated else "full"
        logger.debug(
            f"Parsed {len(parsed['close'])}/{len(raw_candles)} candles ({key_format} keys): "
            f"close range {min(parsed['close']):.2f} - {max(parsed['close']):.2f}"
        )

        return parsed

    def clear_cache(self) -> None:
        """Clear all cached data."""
        cache_size = len(self.cache)
        self.cache.clear()
        logger.info(f"Cleared cache ({cache_size} entries)")

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats: size, oldest_entry_age, etc.
        """
        if not self.cache:
            return {"size": 0, "oldest_entry_age_seconds": 0, "entries": []}

        now = datetime.now()
        ages = [
            (key, (now - timestamp).total_seconds())
            for key, (_, timestamp) in self.cache.items()
        ]
        oldest_age = max(ages, key=lambda x: x[1])[1] if ages else 0

        return {
            "size": len(self.cache),
            "oldest_entry_age_seconds": oldest_age,
            "entries": [
                {"symbol": key[0], "timeframe": key[1], "age_seconds": age}
                for key, age in ages
            ],
        }
