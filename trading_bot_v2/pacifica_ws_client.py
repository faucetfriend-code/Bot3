"""
Pacifica.fi Real-Time WebSocket Client
Replaces ALL polling REST calls (prices, funding, positions, orders, balance)

Features:
- Automatic reconnection with exponential backoff
- Same agent-wallet authentication as REST client
- Thread-safe in-memory cache (latest prices, positions, etc.)
- Async + sync access methods
- Local event broadcasting (so FastAPI /ws can forward to frontend)
"""

import asyncio
import json
import os
import time
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Callable, List, Union
import websockets
from websockets.exceptions import ConnectionClosed
from loguru import logger
from solders.keypair import Keypair
import base58

# Enhanced data retention configuration
MIN_CANDLES_REQUIRED = 200  # Increased from 50 for robust analysis
DATA_RETENTION_HOURS = 48   # Keep 2 days of data
MAX_CANDLES_PER_TIMEFRAME = 500  # Maximum candles to store per timeframe


class CandleDataBuffer:
    """Enhanced candle data buffer with extended retention and validation."""
    
    def __init__(self, symbol: str, timeframe: str, max_size: int = MAX_CANDLES_PER_TIMEFRAME):
        """
        Initialize candle data buffer.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe interval (1m, 5m, 15m, 1h, 4h)
            max_size: Maximum number of candles to retain
        """
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.max_size = max_size
        self.candles = deque(maxlen=max_size)
        self.last_updated = None
        self.created_at = datetime.utcnow()
        
    def add_candle(self, candle: Dict[str, Any]) -> bool:
        """
        Add a new candle to the buffer.
        
        Args:
            candle: OHLCV candle data with timestamp
            
        Returns:
            True if candle was added, False if duplicate or invalid
        """
        if not self._validate_candle(candle):
            return False
            
        # Check for duplicate timestamp (update existing)
        candle_ts = candle.get('timestamp', 0)
        for i, existing_candle in enumerate(self.candles):
            if existing_candle.get('timestamp') == candle_ts:
                # Update existing candle
                self.candles[i] = candle
                self.last_updated = datetime.utcnow()
                return True
        
        # Add new candle
        self.candles.append(candle)
        self.last_updated = datetime.utcnow()
        return True
        
    def get_candles(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get candles from the buffer.
        
        Args:
            limit: Maximum number of candles to return (most recent)
            
        Returns:
            List of candles sorted by timestamp (oldest to newest)
        """
        candles = list(self.candles)
        candles.sort(key=lambda x: x.get('timestamp', 0))
        
        if limit:
            return candles[-limit:] if len(candles) >= limit else candles
        return candles
        
    def is_sufficient(self, required_candles: int = MIN_CANDLES_REQUIRED) -> bool:
        """Check if buffer has sufficient candles for analysis."""
        return len(self.candles) >= required_candles
        
    def get_data_age_hours(self) -> float:
        """Get the age of the oldest data in hours."""
        if not self.candles:
            return float('inf')
            
        oldest_ts = min(c.get('timestamp', 0) for c in self.candles)
        if oldest_ts == 0:
            return float('inf')
            
        return (datetime.utcnow().timestamp() - oldest_ts / 1000) / 3600
        
    def _validate_candle(self, candle: Dict[str, Any]) -> bool:
        """Validate candle data structure and values."""
        required_fields = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        
        # Check required fields
        for field in required_fields:
            if field not in candle:
                return False
                
        # Validate numeric values
        try:
            for field in ['open', 'high', 'low', 'close', 'volume']:
                value = float(candle[field])
                # Check for NaN or Inf
                if value != value or value == float('inf') or value == float('-inf'):
                    return False
                # Prices must be positive
                if field != 'volume' and value <= 0:
                    return False
                    
            # Validate timestamp
            ts = int(candle['timestamp'])
            if ts <= 0:
                return False
                
        except (ValueError, TypeError):
            return False
            
        return True
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert buffer to dictionary for serialization."""
        return {
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'max_size': self.max_size,
            'candles': list(self.candles),
            'last_updated': self.last_updated.isoformat() if self.last_updated else None,
            'created_at': self.created_at.isoformat(),
            'is_sufficient': self.is_sufficient(),
            'data_age_hours': self.get_data_age_hours()
        }


class PacificaWebSocketClient:
    _instance = None
    _lock = threading.Lock()

    __slots__ = (
        "_initialized",
        "_loop",
        "_task",
        "_ws",
        "_connected",
        "_reconnect_delay",
        "_agent_keypair",
        "_account_pubkey",
        "_ws_url",
        "_price_cache",
        "_funding_cache",
        "_position_cache",
        "_balance_cache",
        "_kline_cache",
        "_candle_buffers",  # Enhanced candle data buffers
        "_orderbook_cache",
        "_ui_callbacks",
        "_channel_callbacks",
        "_last_heartbeat",
        "_running",
        "_message_count",
        "_data_recovery_task",  # Background data recovery task
        "_cache_save_task",  # Background cache persistence task
    )

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        # Config
        self._running = False
        self._connected = False
        self._reconnect_delay = 1
        self._last_heartbeat = 0
        self._ui_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._channel_callbacks: Dict[str, Callable[[Dict[str, Any]], None]] = {}

        # Determine WebSocket URL based on TESTNET env
        testnet = os.getenv("TESTNET", "true").lower() == "true"
        self._ws_url = (
            "wss://test-ws.pacifica.fi/ws" if testnet else "wss://ws.pacifica.fi/ws"
        )

        # Caches (thread-safe via GIL + atomic ops)
        self._price_cache: Dict[str, float] = {}
        self._funding_cache: Dict[str, Dict[str, Any]] = {}
        self._position_cache: Dict[str, Dict[str, Any]] = {}
        self._balance_cache: Dict[str, Any] = {
            "balance": 0.0,
            "available": 0.0,
            "equity": 0.0,
        }
        self._kline_cache: Dict[
            str, List[Dict[str, Any]]
        ] = {}  # Legacy Kline cache for backward compatibility
        
        # Enhanced candle data buffers with extended retention
        self._candle_buffers: Dict[str, CandleDataBuffer] = {}
        
        self._orderbook_cache: Dict[
            str, Dict[str, Any]
        ] = {}  # Orderbook data: {symbol: {"bids": [...], "asks": [...], "timestamp": int}}

        # Debug counters and background tasks
        self._message_count = 0
        self._data_recovery_task = None
        self._cache_save_task = None

        # Load keys from environment (same as REST client)
        agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")
        if not agent_private_key or not account_public_key:
            raise ValueError(
                "Pacifica credentials not found in environment. Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY."
            )
        self._agent_keypair = Keypair.from_base58_string(agent_private_key)
        self._account_pubkey = account_public_key

        # Event loop in background thread
        self._loop = asyncio.new_event_loop()
        self._task = None
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    # ====================== PUBLIC API ======================

    def start(self):
        """Start the WebSocket client (idempotent)"""
        if self._running:
            return
        self._running = True
        
        # Start main WebSocket connection
        asyncio.run_coroutine_threadsafe(self._run(), self._loop)
        
        # Start background tasks
        asyncio.run_coroutine_threadsafe(self._start_background_tasks(), self._loop)

    async def _start_background_tasks(self):
        """Start background data management tasks."""
        # Load cached data first
        await self._load_all_cached_data()
        
        # Start background recovery task
        self._data_recovery_task = asyncio.create_task(self.background_data_recovery())
        
        # Start background cache persistence task
        self._cache_save_task = asyncio.create_task(self.background_cache_persistence())
        
        logger.info("Background data management tasks started")

    async def _load_all_cached_data(self):
        """Load all cached candle data on startup."""
        logger.info("Loading cached candle data on startup")
        
        loaded_count = 0
        for symbol in ["BTC", "ETH", "SOL", "SUI", "ADA", "AVAX", "LTC", "LINK", "DOGE", "WLD"]:
            for timeframe in ["1m", "5m", "15m", "1h", "4h"]:
                candles = await self.load_candle_data_from_cache(symbol, timeframe)
                if candles:
                    buffer = self._get_or_create_buffer(symbol, timeframe)
                    for candle in candles:
                        buffer.add_candle(candle)
                    
                    # Update legacy cache
                    cache_key = f"{symbol.upper()}_{timeframe}"
                    self._kline_cache[cache_key] = buffer.get_candles()
                    loaded_count += 1
                    
        logger.info(f"Loaded {loaded_count} candle datasets from cache")

    def stop(self):
        """Stop the client"""
        self._running = False
        
        # Cancel background tasks
        if self._data_recovery_task:
            self._data_recovery_task.cancel()
        if self._cache_save_task:
            self._cache_save_task.cancel()
            
        if hasattr(self, "_task") and self._task:
            self._task.cancel()

    def get_price(self, symbol: str) -> Optional[float]:
        """Get latest price (sync)"""
        return self._price_cache.get(symbol.upper())

    def get_funding(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest funding info"""
        return self._funding_cache.get(symbol.upper())

    def get_positions(self) -> Dict[str, Dict[str, Any]]:
        """Get all current positions (read-only view to avoid memory copies)"""
        from types import MappingProxyType

        return dict(MappingProxyType(self._position_cache))

    def get_balance(self) -> Dict[str, Any]:
        """Get latest balance info (read-only view to avoid memory copies)"""
        from types import MappingProxyType

        return dict(MappingProxyType(self._balance_cache))

    def get_kline_data(
        self, symbol: str, interval: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Get cached real-time Kline data for a symbol/interval in REST API format"""
        buffer_key = f"{symbol.upper()}_{interval}"
        
        # Try enhanced buffer first
        if buffer_key in self._candle_buffers:
            candle_list = self._candle_buffers[buffer_key].get_candles()
        else:
            # Fallback to legacy cache
            candle_list = self._kline_cache.get(buffer_key)

        if not candle_list:
            return None

        # Convert our internal format to REST API format expected by MultiTimeframeFetcher
        # REST API format: [{"o": open, "c": close, "h": high, "l": low, "v": volume}, ...]
        rest_format = []
        for candle in candle_list:
            rest_candle = {
                "o": str(candle["open"]),  # Pacifica uses string prices
                "c": str(candle["close"]),
                "h": str(candle["high"]),
                "l": str(candle["low"]),
                "v": str(candle["volume"]),
            }
            rest_format.append(rest_candle)

        return rest_format

    def get_orderbook(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get cached orderbook for a symbol.

        Returns:
            Dict with 'bids', 'asks', 'timestamp', 'nonce' or None if not available
        """
        clean_symbol = symbol.upper().replace("-PERP", "")
        return self._orderbook_cache.get(clean_symbol)

    def get_orderbook_imbalance(
        self, symbol: str, levels: int = 10
    ) -> Optional[float]:
        """
        Calculate order book imbalance for a symbol.

        Args:
            symbol: Trading symbol
            levels: Number of price levels to consider

        Returns:
            Imbalance ratio (0.0 to 1.0) or None if no data
            > 0.5 = more bids (buy pressure)
            < 0.5 = more asks (sell pressure)
        """
        book = self.get_orderbook(symbol)
        if not book:
            return None

        bids = book.get("bids", [])[:levels]
        asks = book.get("asks", [])[:levels]

        if not bids and not asks:
            return None

        bid_volume = sum(float(level.get("a", 0)) for level in bids)
        ask_volume = sum(float(level.get("a", 0)) for level in asks)

        total_volume = bid_volume + ask_volume
        if total_volume == 0:
            return 0.5

        return bid_volume / total_volume

    def subscribe_orderbook(self, symbol: str, agg_level: int = 10) -> None:
        """
        Subscribe to orderbook depth for a symbol.

        Args:
            symbol: Trading symbol (e.g., "BTC", "SOL")
            agg_level: Aggregation level (1, 10, 100, 1000, 10000)
                       Lower = finer price resolution
        """
        clean_symbol = symbol.upper().replace("-PERP", "")

        payload = {
            "method": "subscribe",
            "params": {
                "source": "book",
                "symbol": clean_symbol,
                "agg_level": agg_level,
            },
        }

        asyncio.run_coroutine_threadsafe(self._send_subscription(payload), self._loop)
        logger.info(f"📚 Subscribed to orderbook: {clean_symbol} (agg_level={agg_level})")

    def bootstrap_kline_cache(
        self,
        rest_client,
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
        lookback: int = 250,
        use_disk_cache: bool = True,
        max_concurrent: int = 3,
    ):
        """
        Pre-populate kline cache with historical data - OPTIMIZED VERSION.

        Optimizations:
        - Parallel requests with rate limiting (max 3 concurrent)
        - Disk caching to avoid re-fetching on restart
        - Priority loading (strategy timeframes first)
        - Reduced lookback for execution timeframes

        Args:
            rest_client: PacificaClient instance for REST API calls
            symbols: List of symbols to bootstrap (default: core trading symbols)
            intervals: List of intervals to fetch (default: 15m, 1h, 4h)
            lookback: Number of candles for strategy timeframes (default: 250 for 200 MA)
            use_disk_cache: Whether to use disk caching (default: True)
            max_concurrent: Maximum concurrent API requests (default: 3)
        """
        from datetime import datetime, timedelta
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import json
        import os
        import time

        symbols = symbols or ["BTC", "ETH", "LTC", "SOL", "SUI", "AVAX", "XRP", "DOGE"]

        # Strategy timeframes need 250 candles for 200 MA
        # Execution timeframes only need 50 for recent context
        strategy_intervals = ["15m", "1h", "4h"]
        execution_intervals = ["1m", "5m"]

        # Use provided intervals or default to strategy intervals only for faster startup
        if intervals is None:
            intervals = strategy_intervals  # Default: only fetch what's needed for regime detection

        logger.info(
            f"⚡ Fast bootstrap: {len(symbols)} symbols × {len(intervals)} intervals "
            f"(max {max_concurrent} concurrent, disk_cache={use_disk_cache})"
        )

        # Disk cache location
        cache_dir = os.path.join(os.path.dirname(__file__), ".kline_cache")
        cache_file = os.path.join(cache_dir, "bootstrap_cache.json")
        cache_max_age_hours = 4  # Cache valid for 4 hours

        # Try loading from disk cache first
        if use_disk_cache and os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    cached_data = json.load(f)

                cache_time = datetime.fromisoformat(cached_data.get("timestamp", "2000-01-01"))
                cache_age_hours = (datetime.now() - cache_time).total_seconds() / 3600

                if cache_age_hours < cache_max_age_hours:
                    # Load from cache
                    klines = cached_data.get("klines", {})
                    loaded_count = 0
                    for cache_key, candles in klines.items():
                        if candles:
                            self._kline_cache[cache_key] = candles
                            loaded_count += 1

                    logger.info(
                        f"⚡ Loaded {loaded_count} kline pairs from disk cache "
                        f"({cache_age_hours:.1f}h old, valid for {cache_max_age_hours}h)"
                    )
                    return  # Skip API calls entirely
                else:
                    logger.info(f"📁 Disk cache expired ({cache_age_hours:.1f}h old), refreshing...")
            except Exception as e:
                logger.warning(f"Failed to load disk cache: {e}")

        # Map intervals to minutes and lookback
        interval_config = {
            "1m": {"minutes": 1, "lookback": 50},
            "5m": {"minutes": 5, "lookback": 50},
            "15m": {"minutes": 15, "lookback": lookback},
            "1h": {"minutes": 60, "lookback": lookback},
            "4h": {"minutes": 240, "lookback": lookback},
        }

        def fetch_candles(symbol: str, interval: str) -> tuple:
            """Fetch candles for a single symbol/interval pair."""
            try:
                config = interval_config.get(interval, {"minutes": 60, "lookback": 50})
                minutes = config["minutes"]
                interval_lookback = config["lookback"]

                start_time = datetime.now() - timedelta(minutes=minutes * interval_lookback)

                candles = rest_client.get_candles(
                    market=f"{symbol}-PERP",
                    interval=interval,
                    start_time=int(start_time.timestamp() * 1000),
                    limit=interval_lookback,
                )

                if candles:
                    cache_key = f"{symbol.upper()}_{interval}"
                    internal_candles = []
                    for c in candles:
                        internal_candles.append({
                            "timestamp": c.get("t") or c.get("timestamp", 0),
                            "open": float(c.get("o") or c.get("open", 0)),
                            "high": float(c.get("h") or c.get("high", 0)),
                            "low": float(c.get("l") or c.get("low", 0)),
                            "close": float(c.get("c") or c.get("close", 0)),
                            "volume": float(c.get("v") or c.get("volume", 0)),
                            "trades": int(c.get("n") or c.get("trades", 0)),
                        })

                    internal_candles.sort(key=lambda x: x["timestamp"])
                    return (cache_key, internal_candles[-250:], None)
                return (f"{symbol.upper()}_{interval}", [], None)

            except Exception as e:
                return (f"{symbol.upper()}_{interval}", [], str(e))

        # Build task list - priority order (strategy timeframes first)
        tasks = []
        for interval in intervals:
            if interval in strategy_intervals:
                for symbol in symbols:
                    tasks.append((symbol, interval))
        for interval in intervals:
            if interval in execution_intervals:
                for symbol in symbols:
                    tasks.append((symbol, interval))

        # Execute with rate-limited parallelism
        successful = 0
        failed = 0
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            # Submit tasks in batches to respect rate limits
            batch_size = max_concurrent
            delay_between_batches = 0.5  # 500ms between batches

            for i in range(0, len(tasks), batch_size):
                batch = tasks[i:i + batch_size]
                futures = {executor.submit(fetch_candles, sym, intv): (sym, intv) for sym, intv in batch}

                for future in as_completed(futures):
                    cache_key, candles, error = future.result()
                    if error:
                        logger.debug(f"⚠️ Failed {cache_key}: {error}")
                        failed += 1
                    elif candles:
                        self._kline_cache[cache_key] = candles
                        successful += 1
                    else:
                        failed += 1

                # Small delay between batches to avoid rate limiting
                if i + batch_size < len(tasks):
                    time.sleep(delay_between_batches)

        elapsed = time.time() - start_time
        logger.info(
            f"✅ Bootstrap complete: {successful}/{len(tasks)} pairs in {elapsed:.1f}s "
            f"({elapsed/max(successful,1)*1000:.0f}ms avg)"
        )

        # Save to disk cache
        if use_disk_cache and successful > 0:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                cache_data = {
                    "timestamp": datetime.now().isoformat(),
                    "klines": {k: v for k, v in self._kline_cache.items()},
                }
                with open(cache_file, "w") as f:
                    json.dump(cache_data, f)
                logger.info(f"💾 Saved {successful} kline pairs to disk cache")
            except Exception as e:
                logger.warning(f"Failed to save disk cache: {e}")

    async def subscribe_candle(self, symbol: str, interval: str):
        """Dynamically subscribe to a specific candle stream"""
        if not self._running or not self._connected:
            logger.warning(
                f"Cannot subscribe to {symbol} {interval}: WebSocket not connected"
            )
            return

        try:
            candle_subscription = {
                "method": "subscribe",
                "params": {
                    "source": "candle",
                    "symbol": symbol.upper(),
                    "interval": interval,
                },
            }

            # Send subscription through the event loop
            asyncio.run_coroutine_threadsafe(
                self._send_subscription(candle_subscription), self._loop
            )
            logger.info(f"📊 Dynamically subscribed to candle: {symbol} {interval}")

        except Exception as e:
            logger.error(f"Failed to subscribe to candle {symbol} {interval}: {e}")

    def update_trading_symbols(self, active_symbols: List[str]):
        """
        Update WebSocket subscriptions to only include actively traded symbols.

        This method should be called when trading symbols change to optimize
        WebSocket subscriptions and reduce unnecessary data streams.

        Args:
            active_symbols: List of symbols currently being traded (e.g., ["SUI", "DOGE"])
        """
        if not self._running:
            logger.warning("WebSocket client not running - cannot update subscriptions")
            return

        # Normalize symbols
        normalized_symbols = [s.upper() for s in active_symbols]
        intervals = ["1m", "5m", "15m", "1h", "4h"]  # Include execution timeframes

        # Create subscription tasks for all symbol/interval combinations
        subscription_tasks = []
        for symbol in normalized_symbols:
            for interval in intervals:
                task = self.subscribe_candle(symbol, interval)
                subscription_tasks.append(task)

        # Run all subscriptions concurrently
        if subscription_tasks:
            async def _run_subscriptions():
                await asyncio.gather(*subscription_tasks, return_exceptions=True)
            asyncio.run_coroutine_threadsafe(_run_subscriptions(), self._loop)
            logger.info(
                f"✅ Updated WebSocket subscriptions for {len(normalized_symbols)} symbols: {normalized_symbols}"
            )

    async def _send_subscription(self, subscription_msg: dict):
        """Send a subscription message through the WebSocket"""
        if self._ws and self._connected:
            await self._ws.send(json.dumps(subscription_msg))

    def register_ui_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Register callback for FastAPI /ws to forward events to browser"""
        self._ui_callbacks.append(callback)

    async def subscribe(
        self, channel_type: str, callback: Callable[[Dict[str, Any]], None]
    ):
        """Subscribe to a channel with a callback"""
        # Map channel type to event type
        channel_map = {
            "ticker": "price",
            "funding": "funding",
            "position": "position_update",
            "order": "order_update",
            "balance": "balance",
            "candle": "candle_update",
        }
        event_type = channel_map.get(str(channel_type), str(channel_type))
        self._channel_callbacks[event_type] = callback

    # ====================== ENHANCED DATA MANAGEMENT ======================

    def _get_or_create_buffer(self, symbol: str, timeframe: str) -> CandleDataBuffer:
        """Get or create candle data buffer for symbol/timeframe."""
        buffer_key = f"{symbol.upper()}_{timeframe}"
        
        if buffer_key not in self._candle_buffers:
            self._candle_buffers[buffer_key] = CandleDataBuffer(
                symbol=symbol.upper(),
                timeframe=timeframe,
                max_size=MAX_CANDLES_PER_TIMEFRAME
            )
            logger.debug(f"Created candle buffer: {buffer_key}")
            
        return self._candle_buffers[buffer_key]

    def validate_data_sufficiency(
        self, symbol: str, timeframe: str, required_candles: int = MIN_CANDLES_REQUIRED
    ) -> tuple[bool, str]:
        """
        Check if symbol has sufficient data for analysis.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe interval
            required_candles: Minimum candles required (default: 200)
            
        Returns:
            Tuple of (is_sufficient, message)
        """
        buffer_key = f"{symbol.upper()}_{timeframe}"
        
        if buffer_key not in self._candle_buffers:
            return False, f"No data buffer for {symbol} {timeframe}"
            
        buffer = self._candle_buffers[buffer_key]
        
        if not buffer.is_sufficient(required_candles):
            return (
                False, 
                f"Insufficient data: {len(buffer.candles)} < {required_candles} candles"
            )
            
        return True, f"Data sufficient: {len(buffer.candles)} candles"

    def get_data_sufficiency_report(self, symbols: Optional[List[str]] = None, 
                                  timeframes: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Generate comprehensive data sufficiency report for all symbols.
        
        Args:
            symbols: List of symbols to check (default: all symbols with buffers)
            timeframes: List of timeframes to check (default: all timeframes)
            
        Returns:
            Dictionary with detailed sufficiency information
        """
        if not symbols:
            symbols = []
            symbol_set = set()
            for buffer_key in self._candle_buffers.keys():
                symbol = buffer_key.split('_')[0]
                symbol_set.add(symbol)
            symbols = list(symbol_set)
            
        if not timeframes:
            timeframes = ["1m", "5m", "15m", "1h", "4h"]
            
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "min_candles_required": MIN_CANDLES_REQUIRED,
            "symbols_checked": len(symbols),
            "timeframes_checked": timeframes,
            "summary": {
                "total_buffers": len(self._candle_buffers),
                "sufficient_buffers": 0,
                "insufficient_buffers": 0,
                "missing_buffers": 0
            },
            "details": {}
        }
        
        for symbol in symbols:
            report["details"][symbol] = {}
            for timeframe in timeframes:
                sufficient, message = self.validate_data_sufficiency(symbol, timeframe)
                buffer_key = f"{symbol.upper()}_{timeframe}"
                
                if buffer_key in self._candle_buffers:
                    buffer = self._candle_buffers[buffer_key]
                    candle_count = len(buffer.candles)
                    data_age_hours = buffer.get_data_age_hours()
                    
                    if sufficient:
                        report["summary"]["sufficient_buffers"] += 1
                    else:
                        report["summary"]["insufficient_buffers"] += 1
                else:
                    candle_count = 0
                    data_age_hours = float('inf')
                    report["summary"]["missing_buffers"] += 1
                    
                report["details"][symbol][timeframe] = {
                    "sufficient": sufficient,
                    "candle_count": candle_count,
                    "required": MIN_CANDLES_REQUIRED,
                    "message": message,
                    "data_age_hours": data_age_hours,
                    "has_buffer": buffer_key in self._candle_buffers
                }
                
        return report

    async def save_candle_data_to_cache(self, symbol: str, timeframe: str, 
                                       candles: List[Dict[str, Any]]) -> bool:
        """
        Save candle data to disk cache for recovery.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe interval
            candles: List of candle data
            
        Returns:
            True if save successful, False otherwise
        """
        try:
            cache_dir = os.path.join(os.path.dirname(__file__), ".candle_cache")
            os.makedirs(cache_dir, exist_ok=True)
            
            cache_file = os.path.join(
                cache_dir, 
                f"candle_cache_{symbol.upper()}_{timeframe}.json"
            )
            
            data = {
                'symbol': symbol.upper(),
                'timeframe': timeframe,
                'candles': candles,
                'saved_at': datetime.utcnow().isoformat(),
                'candle_count': len(candles)
            }
            
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
                
            logger.debug(f"Saved {len(candles)} candles to cache: {cache_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save candle cache for {symbol} {timeframe}: {e}")
            return False

    async def load_candle_data_from_cache(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        """
        Load cached candle data from disk.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe interval
            
        Returns:
            List of candle data (empty list if no cache)
        """
        try:
            cache_dir = os.path.join(os.path.dirname(__file__), ".candle_cache")
            cache_file = os.path.join(
                cache_dir, 
                f"candle_cache_{symbol.upper()}_{timeframe}.json"
            )
            
            if not os.path.exists(cache_file):
                return []
                
            with open(cache_file, 'r') as f:
                data = json.load(f)
                
            candles = data.get('candles', [])
            saved_at = data.get('saved_at', '')
            
            # Check cache age (reject if older than 24 hours)
            if saved_at:
                saved_time = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
                age_hours = (datetime.utcnow() - saved_time).total_seconds() / 3600
                if age_hours > 24:
                    logger.info(f"Candle cache too old for {symbol} {timeframe}: {age_hours:.1f}h")
                    os.remove(cache_file)  # Remove stale cache
                    return []
                    
            logger.debug(f"Loaded {len(candles)} candles from cache: {symbol} {timeframe}")
            return candles
            
        except Exception as e:
            logger.error(f"Failed to load candle cache for {symbol} {timeframe}: {e}")
            return []

    async def fill_data_gap_rest(self, symbol: str, timeframe: str, 
                                lookback: int = MIN_CANDLES_REQUIRED) -> bool:
        """
        Fill data gap using REST API for insufficient data.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe interval
            lookback: Number of candles to fetch
            
        Returns:
            True if gap fill successful, False otherwise
        """
        try:
            # Import here to avoid circular dependencies
            from .pacifica_client import PacificaClient
            
            # Create REST client
            agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
            account_pubkey = os.getenv("ACCOUNT_PUBLIC_KEY")
            
            if not agent_private_key or not account_pubkey:
                logger.error("Missing Pacifica credentials for gap fill")
                return False
                
            client = PacificaClient(
                agent_wallet_private_key=agent_private_key,
                account_public_key=account_pubkey
            )
            
            # Calculate start time
            interval_minutes = self._interval_to_minutes(timeframe)
            start_time = datetime.utcnow() - timedelta(minutes=interval_minutes * lookback)
            
            # Fetch candles
            candles_data = client.get_candles(
                market=f"{symbol.upper()}-PERP",
                interval=timeframe,
                start_time=int(start_time.timestamp() * 1000),
                limit=lookback
            )
            
            if not candles_data:
                logger.warning(f"No REST data available for gap fill: {symbol} {timeframe}")
                return False
                
            # Convert to internal format and add to buffer
            buffer = self._get_or_create_buffer(symbol, timeframe)
            added_count = 0
            
            for candle in candles_data:
                ohlcv_candle = {
                    "timestamp": int(candle.get("t", 0)),
                    "open": float(candle.get("o", 0)),
                    "high": float(candle.get("h", 0)),
                    "low": float(candle.get("l", 0)),
                    "close": float(candle.get("c", 0)),
                    "volume": float(candle.get("v", 0)),
                    "trades": int(candle.get("n", 0)),
                }
                
                if buffer.add_candle(ohlcv_candle):
                    added_count += 1
                    
            # Update legacy cache for backward compatibility
            if added_count > 0:
                cache_key = f"{symbol.upper()}_{timeframe}"
                self._kline_cache[cache_key] = buffer.get_candles()
                
                # Save to persistent cache
                await self.save_candle_data_to_cache(symbol, timeframe, buffer.get_candles())
                
            logger.info(f"Gap fill successful: {symbol} {timeframe} - added {added_count}/{len(candles_data)} candles")
            return True
            
        except Exception as e:
            logger.error(f"Failed to fill data gap for {symbol} {timeframe}: {e}")
            return False

    def _interval_to_minutes(self, interval: str) -> int:
        """Convert interval string to minutes."""
        interval = interval.lower().strip()
        
        if interval.endswith('m'):
            return int(interval[:-1])
        elif interval.endswith('h'):
            return int(interval[:-1]) * 60
        elif interval.endswith('d'):
            return int(interval[:-1]) * 24 * 60
        else:
            raise ValueError(f"Unknown interval format: {interval}")

    async def background_data_recovery(self):
        """Background task to monitor and recover data gaps."""
        logger.info("Starting background data recovery task")
        
        while self._running:
            try:
                # Get sufficiency report
                report = self.get_data_sufficiency_report()
                
                recovery_count = 0
                
                # Check each symbol/timeframe combination
                for symbol, details in report["details"].items():
                    for timeframe, status in details.items():
                        if not status["sufficient"]:
                            logger.warning(
                                f"Data gap detected: {symbol} {timeframe} - "
                                f"{status['candle_count']}/{status['required']} candles"
                            )
                            
                            # Attempt to fill gap
                            if await self.fill_data_gap_rest(symbol, timeframe):
                                recovery_count += 1
                                
                if recovery_count > 0:
                    logger.info(f"Background recovery: filled {recovery_count} data gaps")
                    
                # Check every 5 minutes
                await asyncio.sleep(300)
                
            except Exception as e:
                logger.error(f"Error in background data recovery: {e}")
                await asyncio.sleep(60)  # Short retry on error

    async def background_cache_persistence(self):
        """Background task to persist candle data to disk periodically."""
        logger.info("Starting background cache persistence task")
        
        while self._running:
            try:
                # Save all buffers to cache every hour
                saved_count = 0
                
                for buffer_key, buffer in self._candle_buffers.items():
                    if buffer.candles:  # Only save non-empty buffers
                        symbol = buffer.symbol
                        timeframe = buffer.timeframe
                        
                        if await self.save_candle_data_to_cache(
                            symbol, timeframe, buffer.get_candles()
                        ):
                            saved_count += 1
                            
                if saved_count > 0:
                    logger.debug(f"Cache persistence: saved {saved_count} buffers to disk")
                    
                # Run every hour
                await asyncio.sleep(3600)
                
            except Exception as e:
                logger.error(f"Error in background cache persistence: {e}")
                await asyncio.sleep(300)  # Short retry on error

    # ====================== INTERNAL ======================

    async def _run(self):
        logger.info("Starting Pacifica WebSocket client...")
        while self._running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10,
                    max_size=2**20,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._reconnect_delay = 1
                    logger.success("Connected to Pacifica WebSocket")

                    # Authenticate for private channels
                    await self._authenticate(ws)

                    # Subscribe to everything
                    await self._subscribe_public(ws)
                    await self._subscribe_private(ws)

                    # Listen loop
                    async for message in ws:
                        await self._handle_message(message)
                        self._last_heartbeat = time.time()

            except (ConnectionClosed, asyncio.CancelledError):
                pass
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self._running:
                logger.warning(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)
                self._connected = False

    async def _authenticate(self, ws):
        timestamp = int(time.time() * 1000)
        message = json.dumps(
            {"op": "auth", "timestamp": timestamp, "account": self._account_pubkey}
        ).encode()

        signature = self._agent_keypair.sign_message(message)
        sig_b58 = base58.b58encode(bytes(signature)).decode()

        auth_msg = {
            "op": "auth",
            "timestamp": timestamp,
            "account": self._account_pubkey,
            "signature": sig_b58,
        }
        await ws.send(json.dumps(auth_msg))
        logger.info("Sent auth message")

    async def _subscribe_public(self, ws):
        # Subscribe to all-market price stream (SDK format)
        await ws.send(
            json.dumps({"method": "subscribe", "params": {"source": "prices"}})
        )
        logger.info("Subscribed to prices stream (all markets)")

        # Subscribe to Candle streams for real-time Kline data
        # Based on SDK documentation: source="candle" with symbol and interval
        # Optimized: Only subscribe to actively traded symbols to minimize subscriptions
        try:
            # Subscribe to core markets for real-time candle data
            symbols = ["BTC", "LTC", "ETH"]
            # Include execution timeframes (1m, 5m) for precise entry timing
            intervals = ["1m", "5m", "15m", "1h", "4h"]  # Full timeframe hierarchy

            subscription_count = 0
            for symbol in symbols:
                for interval in intervals:
                    candle_subscription = {
                        "method": "subscribe",
                        "params": {
                            "source": "candle",
                            "symbol": symbol,
                            "interval": interval,
                        },
                    }
                    await ws.send(json.dumps(candle_subscription))
                    subscription_count += 1
                    logger.debug(f"📊 Subscribed to candle: {symbol} {interval}")

                    # Small delay between subscriptions to avoid overwhelming
                    await asyncio.sleep(0.1)

            logger.info(
                f"✅ Completed {subscription_count} candle subscriptions for {len(symbols)} symbols"
            )

        except Exception as e:
            logger.warning(f"❌ Candle WebSocket subscriptions failed: {e}")

    async def _subscribe_private(self, ws):
        # Wait a moment for auth to settle
        await asyncio.sleep(1)
        subs = ["position", "order", "executionReport", "balance"]
        await ws.send(json.dumps({"op": "subscribe", "channels": subs}))
        logger.info(f"Subscribed to private: {subs}")

    async def _handle_message(self, raw: Union[str, bytes]):
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)

            # Heartbeat
            if data.get("type") == "pong":
                self._last_heartbeat = time.time()
                return

            # Log subscription responses and errors
            if (
                data.get("type") in ("subscription", "subscribed")
                or "error" in data
                or "success" in data
                or data.get("event") == "subscribed"
            ):
                logger.info(f"📡 WS Subscription response: {data}")
            elif (
                data.get("channel") == "candle" and "subscription" in str(data).lower()
            ):
                logger.info(f"📊 Candle subscription event: {data}")

            channel = data.get("channel", "")
            symbol = data.get("symbol", "").upper()

            # Debug: Log incoming messages (first 50 messages only to avoid spam)
            if self._message_count < 50:
                logger.debug(
                    f"WS message: channel={channel}, symbol={symbol}, data_keys={list(data.keys())}"
                )
                self._message_count += 1

            if channel == "prices":
                # Price data is a list of price objects under "data" key
                price_list = data.get("data", [])
                if isinstance(price_list, list):
                    for price_item in price_list:
                        if isinstance(price_item, dict):
                            price_symbol = price_item.get("symbol", "").upper()
                            # Use "mark" price as the current price (most relevant for trading)
                            mark_price = price_item.get("mark")
                            if price_symbol and mark_price:
                                try:
                                    self._price_cache[price_symbol] = float(mark_price)
                                    logger.debug(
                                        f"Price update: {price_symbol} = ${mark_price}"
                                    )
                                    event = {
                                        "type": "price",
                                        "symbol": price_symbol,
                                        "price": mark_price,
                                    }
                                    self._broadcast(event)
                                    self._call_channel_callback("price", event)
                                except (ValueError, TypeError) as e:
                                    logger.warning(
                                        f"Invalid price data for {price_symbol}: {mark_price} - {e}"
                                    )
                else:
                    logger.warning(f"Prices message data is not a list: {data}")

            elif channel.startswith("funding"):
                if symbol:
                    self._funding_cache[symbol] = {
                        "rate": float(data["fundingRate"]),
                        "next": data.get("nextFundingTime"),
                        "timestamp": data.get("timestamp"),
                    }
                    event = {
                        "type": "funding",
                        "symbol": symbol,
                        **self._funding_cache[symbol],
                    }
                    self._broadcast(event)
                    self._call_channel_callback("funding", event)

            elif channel == "position":
                pos = data.get("position", {})
                pos_symbol = pos.get("symbol", "").upper()
                if pos_symbol:
                    self._position_cache[pos_symbol] = pos
                    event = {"type": "position_update", "position": pos}
                    self._broadcast(event)
                    self._call_channel_callback("position_update", event)

            elif channel == "balance":
                self._balance_cache.update(
                    {
                        "balance": float(data.get("balance", 0)),
                        "available": float(data.get("availableBalance", 0)),
                        "equity": float(data.get("equity", 0)),
                        "timestamp": data.get("timestamp"),
                    }
                )
                event = {"type": "balance", **self._balance_cache}
                self._broadcast(event)
                self._call_channel_callback("balance", event)

            elif channel in ("order", "executionReport"):
                event = {"type": "order_update", "data": data}
                self._broadcast(event)
                self._call_channel_callback("order_update", event)

            elif channel == "candle":
                # Handle real-time candle data from WebSocket
                # Debug: Log first few candle messages to understand format
                if self._message_count < 10:
                    logger.debug(f"📊 Raw candle data: {data}")

                candle_data = data.get("data", {})
                if not candle_data:
                    logger.warning(f"Candle message missing data field: {data}")
                    return

                # Check if we have the required fields
                required_fields = ["s", "i", "o", "c", "h", "l", "v"]
                missing_fields = [
                    field for field in required_fields if field not in candle_data
                ]
                if missing_fields:
                    logger.warning(
                        f"Candle data missing fields {missing_fields}: {candle_data}"
                    )
                    return

                try:
                    symbol = candle_data["s"].upper()
                    interval = candle_data["i"]

                    # Convert WebSocket candle format to our internal OHLCV format
                    ohlcv_candle = {
                        "timestamp": int(candle_data.get("t", 0)),  # start time
                        "open": float(candle_data["o"]),
                        "high": float(candle_data["h"]),
                        "low": float(candle_data["l"]),
                        "close": float(candle_data["c"]),
                        "volume": float(candle_data["v"]),
                        "trades": int(candle_data.get("n", 0)),
                    }

                    # Store in enhanced candle buffer
                    buffer = self._get_or_create_buffer(symbol, interval)
                    buffer.add_candle(ohlcv_candle)
                    
                    # Update legacy cache for backward compatibility
                    cache_key = f"{symbol}_{interval}"
                    self._kline_cache[cache_key] = buffer.get_candles()

                    event = {
                        "type": "candle_update",
                        "symbol": symbol,
                        "interval": interval,
                        "candle": ohlcv_candle,
                    }
                    self._broadcast(event)
                    self._call_channel_callback("candle_update", event)
                    logger.debug(
                        f"📊 Candle update: {symbol} {interval} - O:{ohlcv_candle['open']:.2f} C:{ohlcv_candle['close']:.2f}"
                    )

                except (ValueError, KeyError) as e:
                    logger.error(
                        f"Error processing candle data: {e} | Data: {candle_data}"
                    )

            elif channel == "book":
                # Handle orderbook depth data
                book_symbol = data.get("symbol", "").upper()
                if book_symbol:
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])

                    self._orderbook_cache[book_symbol] = {
                        "bids": bids,
                        "asks": asks,
                        "timestamp": data.get("t", 0),
                        "nonce": data.get("li", 0),
                    }

                    # Broadcast orderbook update event
                    event = {
                        "type": "orderbook_update",
                        "symbol": book_symbol,
                        "bids": len(bids),
                        "asks": len(asks),
                        "timestamp": data.get("t", 0),
                    }
                    self._broadcast(event)
                    self._call_channel_callback("orderbook_update", event)

                    # Debug log first few orderbook messages
                    if self._message_count < 5:
                        logger.debug(
                            f"📚 Orderbook update: {book_symbol} - "
                            f"{len(bids)} bids, {len(asks)} asks"
                        )

        except Exception as e:
            logger.error(f"Error handling WS message: {e} | Raw: {raw[:200]}")

    def _call_channel_callback(self, event_type: str, event: Dict[str, Any]):
        """Call the specific channel callback"""
        if event_type in self._channel_callbacks:
            try:
                callback = self._channel_callbacks[event_type]
                if callback and callable(callback):
                    if asyncio.iscoroutinefunction(callback):
                        asyncio.run_coroutine_threadsafe(callback(event), self._loop)
                    else:
                        callback(event)  # Sync callback
            except Exception as e:
                logger.error(f"Channel callback error for {event_type}: {e}")

    def _broadcast(self, event: Dict[str, Any]):
        """Send event to all registered UI callbacks (FastAPI /ws)"""
        for cb in self._ui_callbacks[:]:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"UI callback error: {e}")
                self._ui_callbacks.remove(cb)


# ====================== GLOBAL ACCESS ======================

# Singleton instance
_ws_client_instance: Optional[PacificaWebSocketClient] = None


def get_ws_client() -> PacificaWebSocketClient:
    """Singleton accessor - returns the same instance across all callers"""
    global _ws_client_instance
    if _ws_client_instance is None:
        _ws_client_instance = PacificaWebSocketClient()
    # Don't start automatically - let the caller start it when ready
    return _ws_client_instance


# Auto-start on import (optional)
# get_ws_client()
