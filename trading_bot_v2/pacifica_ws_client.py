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
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Dict,
    Any,
    ClassVar,
    Mapping,
    Optional,
    Callable,
    List,
    Tuple,
    Union,
)
import websockets
from websockets.exceptions import ConnectionClosed
from loguru import logger
from solders.keypair import Keypair
import base58

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from .pacifica_client import PacificaClient


class PacificaWebSocketClient:
    _instance: ClassVar[Optional["PacificaWebSocketClient"]] = None
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
        "_skip_private_channels",
        "_price_cache",
        "_funding_cache",
        "_position_cache",
        "_balance_cache",
        "_kline_cache",
        "_orderbook_cache",
        "_ui_callbacks",
        "_channel_callbacks",
        "_last_heartbeat",
        "_running",
        "_message_count",
    )

    def __new__(cls) -> "PacificaWebSocketClient":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        # Config
        self._running = False
        self._connected = False
        self._reconnect_delay = 1
        self._last_heartbeat: float = 0
        self._ui_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._channel_callbacks: Dict[str, Callable[[Dict[str, Any]], None]] = {}

        # Determine WebSocket URL.
        # PACIFICA_DATA_WS_URL overrides the TESTNET flag for the market-data feed,
        # allowing live candle/price data while order execution stays on testnet REST.
        testnet = os.getenv("TESTNET", "true").lower() == "true"
        data_ws_override = os.getenv("PACIFICA_DATA_WS_URL", "").strip()
        if data_ws_override:
            self._ws_url = data_ws_override
        else:
            self._ws_url = (
                "wss://test-ws.pacifica.fi/ws" if testnet else "wss://ws.pacifica.fi/ws"
            )

        # When pointing at the live WS while running in testnet mode, our testnet
        # keys have no mainnet account, so private channel subscriptions (position,
        # balance, order) would return nothing useful. Skip them and rely on testnet
        # REST polling for account state.
        self._skip_private_channels = (
            data_ws_override == "wss://ws.pacifica.fi/ws" and testnet
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
        ] = {}  # Real-time Kline data: {symbol_interval: [candles]}
        self._orderbook_cache: Dict[
            str, Dict[str, Any]
        ] = {}  # Orderbook data: {symbol: {"bids": [...], "asks": [...], "timestamp": int}}

        # Debug counters
        self._message_count = 0

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
        self._task: Optional["asyncio.Task[Any]"] = None
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    # ====================== PUBLIC API ======================

    def start(self) -> None:
        """Start the WebSocket client (idempotent)"""
        if self._running:
            return
        self._running = True
        asyncio.run_coroutine_threadsafe(self._run(), self._loop)

    def stop(self) -> None:
        """Stop the client"""
        self._running = False
        if hasattr(self, "_task") and self._task:
            self._task.cancel()

    def get_price(self, symbol: str) -> Optional[float]:
        """Get latest price (sync)"""
        return self._price_cache.get(symbol.upper())

    def get_funding(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest funding info"""
        return self._funding_cache.get(symbol.upper())

    def get_positions(self) -> Mapping[str, Dict[str, Any]]:
        """Get all current positions (read-only view to avoid memory copies)"""
        from types import MappingProxyType

        return MappingProxyType(self._position_cache)

    def get_balance(self) -> Mapping[str, Any]:
        """Get latest balance info (read-only view to avoid memory copies)"""
        from types import MappingProxyType

        return MappingProxyType(self._balance_cache)

    def get_kline_data(
        self, symbol: str, interval: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Get cached real-time Kline data for a symbol/interval in REST API format"""
        cache_key = f"{symbol.upper()}_{interval}"
        candle_list = self._kline_cache.get(cache_key)

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

    def get_orderbook_imbalance(self, symbol: str, levels: int = 10) -> Optional[float]:
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
        logger.info(
            f"📚 Subscribed to orderbook: {clean_symbol} (agg_level={agg_level})"
        )

    def bootstrap_kline_cache(
        self,
        rest_client: "PacificaClient",
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
        lookback: int = 250,
        use_disk_cache: bool = True,
        max_concurrent: int = 3,
    ) -> None:
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
        from datetime import timedelta
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

                cache_time = datetime.fromisoformat(
                    cached_data.get("timestamp", "2000-01-01")
                )
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
                    logger.info(
                        f"📁 Disk cache expired ({cache_age_hours:.1f}h old), refreshing..."
                    )
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

        def fetch_candles(
            symbol: str, interval: str
        ) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
            """Fetch candles for a single symbol/interval pair."""
            try:
                config = interval_config.get(interval, {"minutes": 60, "lookback": 50})
                minutes = config["minutes"]
                interval_lookback = config["lookback"]

                start_time = datetime.now() - timedelta(
                    minutes=minutes * interval_lookback
                )

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
                        internal_candles.append(
                            {
                                "timestamp": c.get("t") or c.get("timestamp", 0),
                                "open": float(c.get("o") or c.get("open", 0)),
                                "high": float(c.get("h") or c.get("high", 0)),
                                "low": float(c.get("l") or c.get("low", 0)),
                                "close": float(c.get("c") or c.get("close", 0)),
                                "volume": float(c.get("v") or c.get("volume", 0)),
                                "trades": int(c.get("n") or c.get("trades", 0)),
                            }
                        )

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
                batch = tasks[i : i + batch_size]
                futures = {
                    executor.submit(fetch_candles, sym, intv): (sym, intv)
                    for sym, intv in batch
                }

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
            f"({elapsed / max(successful, 1) * 1000:.0f}ms avg)"
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

    async def subscribe_candle(self, symbol: str, interval: str) -> None:
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

    def update_trading_symbols(self, active_symbols: List[str]) -> None:
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
            asyncio.run_coroutine_threadsafe(
                asyncio.gather(*subscription_tasks, return_exceptions=True),  # type: ignore[arg-type]  # gather() is a Future, not a coroutine; reported
                self._loop,
            )
            logger.info(
                f"✅ Updated WebSocket subscriptions for {len(normalized_symbols)} symbols: {normalized_symbols}"
            )

    async def _send_subscription(self, subscription_msg: Dict[str, Any]) -> None:
        """Send a subscription message through the WebSocket"""
        if self._ws and self._connected:
            await self._ws.send(json.dumps(subscription_msg))

    def register_ui_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register callback for FastAPI /ws to forward events to browser"""
        self._ui_callbacks.append(callback)

    async def subscribe(
        self, channel_type: str, callback: Callable[[Dict[str, Any]], None]
    ) -> None:
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

    # ====================== INTERNAL ======================

    async def _run(self) -> None:
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
                    logger.success(
                        f"Connected to Pacifica WebSocket: {self._ws_url}"
                        + (
                            " (public data only — testnet keys, live endpoint)"
                            if self._skip_private_channels
                            else ""
                        )
                    )

                    # Authenticate for private channels.
                    # Skipped when using the live WS with testnet credentials because
                    # testnet keys have no mainnet account.
                    if not self._skip_private_channels:
                        await self._authenticate(ws)

                    # Public subscriptions (prices, candles) work on any endpoint.
                    await self._subscribe_public(ws)

                    # Private subscriptions only make sense when our auth credentials
                    # match the WS endpoint (both testnet or both mainnet).
                    if not self._skip_private_channels:
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

    async def _authenticate(self, ws: "ClientConnection") -> None:
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

    async def _subscribe_public(self, ws: "ClientConnection") -> None:
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

    async def _subscribe_private(self, ws: "ClientConnection") -> None:
        # Wait a moment for auth to settle
        await asyncio.sleep(1)
        subs = ["position", "order", "executionReport", "balance"]
        await ws.send(json.dumps({"op": "subscribe", "channels": subs}))
        logger.info(f"Subscribed to private: {subs}")

    async def _handle_message(self, raw: Union[str, bytes]) -> None:
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

                    # Store in cache (maintain last N candles for each symbol/interval)
                    cache_key = f"{symbol}_{interval}"
                    if cache_key not in self._kline_cache:
                        self._kline_cache[cache_key] = []

                    # Keep only recent candles (limit to prevent memory growth)
                    candle_list = self._kline_cache[cache_key]
                    candle_list.append(ohlcv_candle)

                    # Sort by timestamp and keep most recent 250 candles (for 200 MA)
                    candle_list.sort(key=lambda x: x["timestamp"])
                    self._kline_cache[cache_key] = candle_list[-250:]

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
            logger.error(f"Error handling WS message: {e} | Raw: {raw[:200]}")  # type: ignore[str-bytes-safe]  # bytes frame logs as b'..'; reported

    def _call_channel_callback(self, event_type: str, event: Dict[str, Any]) -> None:
        """Call the specific channel callback"""
        if event_type in self._channel_callbacks:
            try:
                asyncio.create_task(self._channel_callbacks[event_type](event))  # type: ignore[arg-type]  # callback typed as sync; reported
            except Exception as e:
                logger.error(f"Channel callback error for {event_type}: {e}")

    def _broadcast(self, event: Dict[str, Any]) -> None:
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
