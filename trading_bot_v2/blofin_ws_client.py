"""Blofin public WebSocket data client (market data only, no auth).

Mirrors the surface of ``pacifica_ws_client.PacificaWebSocketClient``
that the bot actually consumes (multi_timeframe_fetcher, api_server,
strategy_manager):

* ``start`` / ``stop`` / ``is_connected`` (+ ``_connected`` /
  ``_running`` / ``_kline_cache`` attributes read by api_server debug
  endpoints)
* ``get_price(symbol)`` - latest trade price by bare base symbol
* ``get_kline_data(symbol, interval)`` - candles in the Pacifica REST
  string format [{"o","c","h","l","v"}, ...] expected by
  MultiTimeframeFetcher._parse_candles
* ``get_orderbook(symbol)`` - {"bids": [{"p","a"}...], "asks": [...],
  "timestamp"} as read by OrderBookImbalance (sizes stay in contracts;
  the imbalance ratio is unit-invariant)
* ``get_orderbook_imbalance(symbol, levels)``
* ``subscribe_orderbook(symbol, agg_level)`` - maps to the Blofin
  "books5" snapshot channel (5 levels; agg_level is accepted for
  interface compatibility and ignored)
* ``bootstrap_kline_cache(rest_client, ...)`` - REST pre-population
  with the same disk-cache behavior as the Pacifica client

Wire facts (verified against wss://openapi.blofin.com/ws/public on
2026-07-20): subscribe {"op":"subscribe","args":[{"channel":"candle15m",
"instId":"BTC-USDT"}]}; candle pushes carry data rows [ts,o,h,l,c,vol,
volCurrency,volCurrencyQuote,confirm]; "books5" pushes carry a data
DICT {asks,bids,ts}; tickers pushes carry a list of ticker dicts; the
server answers a literal "ping" text frame with "pong".  Reconnect uses
the same exponential-backoff loop as the Pacifica client.

Private WS channels are intentionally not implemented: the bot polls
REST for positions/orders/balance on the Pacifica side too.
"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import websockets

from .blofin_client import BAR_MAP

logger = logging.getLogger(__name__)

BLOFIN_PUBLIC_WS_URL = "wss://openapi.blofin.com/ws/public"
BLOFIN_DEMO_PUBLIC_WS_URL = "wss://demo-trading-openapi.blofin.com/ws/public"

# Reverse map: Blofin bar ("1H") -> bot interval ("1h").
_BAR_TO_INTERVAL = {bar: interval for interval, bar in BAR_MAP.items()}

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "SUI", "AVAX", "XRP", "DOGE", "LTC"]
DEFAULT_INTERVALS = ["1m", "5m", "15m", "1h", "4h"]
_MAX_CACHED_CANDLES = 500
_PING_INTERVAL_SECONDS = 20


class BlofinWebSocketClient:
    """Public market-data WebSocket client for Blofin USDT perps.

    Construction performs no network I/O; call :meth:`start` to begin
    connecting (idempotent, runs in a daemon background thread).

    Args:
        symbols: Bare base symbols to stream (default: core bot set).
        intervals: Bot candle intervals to stream (default: 1m..4h).
        url: WS URL override (default: ``BLOFIN_DATA_WS_URL`` env var,
            else demo/prod public endpoint per ``BLOFIN_DEMO``).
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
        url: Optional[str] = None,
    ):
        self._symbols = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
        self._intervals = list(intervals or DEFAULT_INTERVALS)

        if url:
            self._ws_url = url
        else:
            override = os.getenv("BLOFIN_DATA_WS_URL", "").strip()
            if override:
                self._ws_url = override
            else:
                demo = os.getenv("BLOFIN_DEMO", "true").strip().lower() == "true"
                self._ws_url = (
                    BLOFIN_DEMO_PUBLIC_WS_URL if demo else BLOFIN_PUBLIC_WS_URL
                )

        self._running = False
        self._connected = False
        self._reconnect_delay = 1
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._message_count = 0

        # Caches (GIL-atomic reads/writes, mirroring the Pacifica client)
        self._price_cache: Dict[str, float] = {}
        self._kline_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._orderbook_cache: Dict[str, Dict[str, Any]] = {}
        self._orderbook_symbols: List[str] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the client (idempotent); spawns the event-loop thread."""
        if self._running:
            return
        self._running = True
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever, daemon=True
            )
            self._loop_thread.start()
        asyncio.run_coroutine_threadsafe(self._run(), self._loop)

    def stop(self) -> None:
        """Stop the client; the connection closes on the next cycle."""
        self._running = False

    def is_connected(self) -> bool:
        """True while a WebSocket connection is established."""
        return self._connected

    # ------------------------------------------------------------------
    # Sync data accessors (bot-facing surface)
    # ------------------------------------------------------------------

    def get_price(self, symbol: str) -> Optional[float]:
        """Return the latest traded price for a bare base symbol."""
        return self._price_cache.get(self._clean(symbol))

    def get_kline_data(
        self, symbol: str, interval: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Return cached candles in the Pacifica REST string format.

        Format: [{"o": str, "c": str, "h": str, "l": str, "v": str},
        ...] ascending - exactly what MultiTimeframeFetcher expects.
        """
        candles = self._kline_cache.get(f"{self._clean(symbol)}_{interval}")
        if not candles:
            return None
        return [
            {
                "o": str(c["open"]),
                "c": str(c["close"]),
                "h": str(c["high"]),
                "l": str(c["low"]),
                "v": str(c["volume"]),
            }
            for c in candles
        ]

    def get_orderbook(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return the cached book: {"bids": [{"p","a"}...], "asks": ...}."""
        return self._orderbook_cache.get(self._clean(symbol))

    def get_orderbook_imbalance(
        self, symbol: str, levels: int = 10
    ) -> Optional[float]:
        """Return bid volume / total volume over the top ``levels``."""
        book = self.get_orderbook(symbol)
        if not book:
            return None
        bids = book.get("bids", [])[:levels]
        asks = book.get("asks", [])[:levels]
        if not bids and not asks:
            return None
        bid_volume = sum(float(level.get("a", 0)) for level in bids)
        ask_volume = sum(float(level.get("a", 0)) for level in asks)
        total = bid_volume + ask_volume
        if total == 0:
            return 0.5
        return bid_volume / total

    def subscribe_orderbook(self, symbol: str, agg_level: int = 10) -> None:
        """Subscribe to the books5 snapshot channel for a symbol.

        Args:
            symbol: Bare base symbol (e.g. "BTC").
            agg_level: Accepted for Pacifica interface compatibility;
                Blofin books5 has no aggregation parameter.
        """
        clean = self._clean(symbol)
        if clean not in self._orderbook_symbols:
            self._orderbook_symbols.append(clean)
        if self._loop is not None and self._running:
            message = {
                "op": "subscribe",
                "args": [{"channel": "books5", "instId": f"{clean}-USDT"}],
            }
            asyncio.run_coroutine_threadsafe(self._send(message), self._loop)
        logger.info("Subscribed to Blofin orderbook: %s (books5)", clean)

    def bootstrap_kline_cache(
        self,
        rest_client,
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
        lookback: int = 250,
        use_disk_cache: bool = True,
        max_concurrent: int = 3,
    ) -> None:
        """Pre-populate the kline cache from REST history.

        Same contract as the Pacifica client's bootstrap: strategy
        timeframes get ``lookback`` candles, execution timeframes 50,
        with an optional 4h-valid disk cache.

        Args:
            rest_client: BlofinClient (anything with ``get_candles``).
            symbols: Symbols to bootstrap (default: streaming set).
            intervals: Intervals to fetch (default: 15m/1h/4h).
            lookback: Candles per strategy timeframe.
            use_disk_cache: Load/save the on-disk bootstrap cache.
            max_concurrent: Thread-pool size for parallel fetches.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        symbols = [self._clean(s) for s in (symbols or self._symbols)]
        intervals = intervals or ["15m", "1h", "4h"]
        execution_intervals = ("1m", "5m")

        cache_dir = os.path.join(os.path.dirname(__file__), ".kline_cache")
        cache_file = os.path.join(cache_dir, "blofin_bootstrap_cache.json")
        cache_max_age_hours = 4

        if use_disk_cache and os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as handle:
                    cached = json.load(handle)
                cache_time = datetime.fromisoformat(
                    cached.get("timestamp", "2000-01-01")
                )
                age_hours = (datetime.now() - cache_time).total_seconds() / 3600
                if age_hours < cache_max_age_hours:
                    loaded = 0
                    for key, candles in (cached.get("klines") or {}).items():
                        if candles:
                            self._kline_cache[key] = candles
                            loaded += 1
                    logger.info(
                        "Loaded %d Blofin kline pairs from disk cache (%.1fh old)",
                        loaded,
                        age_hours,
                    )
                    return
            except (ValueError, OSError) as exc:
                logger.warning("Failed to load Blofin kline disk cache: %s", exc)

        def fetch(symbol: str, interval: str):
            count = 50 if interval in execution_intervals else lookback
            try:
                candles = rest_client.get_candles(
                    market=symbol, interval=interval, limit=count
                )
                return (f"{symbol}_{interval}", candles, None)
            except Exception as exc:  # noqa: BLE001 - bootstrap is best-effort
                return (f"{symbol}_{interval}", [], str(exc))

        tasks = [(s, i) for i in intervals for s in symbols]
        successful = 0
        started = time.time()
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(fetch, s, i): (s, i) for s, i in tasks}
            for future in as_completed(futures):
                cache_key, candles, error = future.result()
                if error:
                    logger.debug("Bootstrap failed for %s: %s", cache_key, error)
                elif candles:
                    self._kline_cache[cache_key] = candles[-_MAX_CACHED_CANDLES:]
                    successful += 1
        logger.info(
            "Blofin bootstrap complete: %d/%d pairs in %.1fs",
            successful,
            len(tasks),
            time.time() - started,
        )

        if use_disk_cache and successful > 0:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_file, "w") as handle:
                    json.dump(
                        {
                            "timestamp": datetime.now().isoformat(),
                            "klines": self._kline_cache,
                        },
                        handle,
                    )
            except OSError as exc:
                logger.warning("Failed to save Blofin kline disk cache: %s", exc)

    # ------------------------------------------------------------------
    # Connection loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Connect/subscribe/listen loop with exponential backoff."""
        logger.info("Starting Blofin WebSocket client: %s", self._ws_url)
        while self._running:
            ping_task = None
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=None,  # Blofin uses text "ping" frames
                    close_timeout=10,
                    max_size=2**20,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._reconnect_delay = 1
                    logger.info("Connected to Blofin WebSocket: %s", self._ws_url)
                    await self._subscribe_all(ws)
                    ping_task = asyncio.ensure_future(self._ping_loop(ws))
                    async for message in ws:
                        if not self._running:
                            break
                        self._handle_raw(message)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - reconnect on any failure
                logger.error("Blofin WebSocket error: %s", exc)
            finally:
                if ping_task is not None:
                    ping_task.cancel()
                self._connected = False
            if self._running:
                logger.warning(
                    "Blofin WS reconnecting in %ds...", self._reconnect_delay
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def _ping_loop(self, ws) -> None:
        """Send the literal 'ping' text frame Blofin expects (<30s idle)."""
        try:
            while self._running:
                await asyncio.sleep(_PING_INTERVAL_SECONDS)
                await ws.send("ping")
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    async def _subscribe_all(self, ws) -> None:
        """Send tickers + candle + pending book subscriptions."""
        args: List[Dict[str, str]] = []
        for symbol in self._symbols:
            inst_id = f"{symbol}-USDT"
            args.append({"channel": "tickers", "instId": inst_id})
            for interval in self._intervals:
                bar = BAR_MAP.get(interval.lower())
                if bar:
                    args.append({"channel": f"candle{bar}", "instId": inst_id})
        for symbol in self._orderbook_symbols:
            args.append({"channel": "books5", "instId": f"{symbol}-USDT"})
        # Chunk to keep individual frames small.
        for start in range(0, len(args), 20):
            await ws.send(
                json.dumps({"op": "subscribe", "args": args[start : start + 20]})
            )
            await asyncio.sleep(0.05)
        logger.info("Blofin WS: sent %d channel subscriptions", len(args))

    async def _send(self, message: Dict[str, Any]) -> None:
        """Send a JSON message if connected (best effort)."""
        if self._ws is not None and self._connected:
            try:
                await self._ws.send(json.dumps(message))
            except Exception as exc:  # noqa: BLE001 - resubscribe on reconnect
                logger.debug("Blofin WS send failed: %s", exc)

    # ------------------------------------------------------------------
    # Message handling (sync core for testability)
    # ------------------------------------------------------------------

    def _handle_raw(self, raw: Any) -> None:
        """Decode and dispatch one raw frame (text 'pong' tolerated)."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if raw == "pong":
            return
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return
        self._process_message(message)

    def _process_message(self, message: Dict[str, Any]) -> None:
        """Update caches from one decoded push message."""
        if not isinstance(message, dict):
            return
        if "event" in message:
            event = message.get("event")
            if event == "error":
                logger.warning("Blofin WS error event: %s", message)
            return
        arg = message.get("arg") or {}
        channel = str(arg.get("channel", ""))
        inst_id = str(arg.get("instId", ""))
        base = inst_id.split("-", 1)[0].upper()
        data = message.get("data")
        if not channel or data is None:
            return
        self._message_count += 1
        if channel == "tickers":
            self._handle_tickers(base, data)
        elif channel.startswith("candle"):
            self._handle_candle(base, channel[len("candle") :], data)
        elif channel in ("books5", "books"):
            self._handle_books(base, data)

    def _handle_tickers(self, base: str, data: Any) -> None:
        """Cache the latest price from a tickers push."""
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            try:
                last = float(entry.get("last", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if last > 0:
                self._price_cache[base] = last

    def _handle_candle(self, base: str, bar: str, data: Any) -> None:
        """Merge candle rows [ts,o,h,l,c,vol,volCurrency,...] into cache."""
        interval = _BAR_TO_INTERVAL.get(bar)
        if interval is None:
            return
        cache_key = f"{base}_{interval}"
        candles = self._kline_cache.setdefault(cache_key, [])
        rows = data if isinstance(data, list) else []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                continue
            try:
                candle = {
                    "timestamp": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[6]),  # volCurrency = base units
                }
            except (TypeError, ValueError):
                continue
            if candles and candles[-1]["timestamp"] == candle["timestamp"]:
                candles[-1] = candle
            elif not candles or candle["timestamp"] > candles[-1]["timestamp"]:
                candles.append(candle)
        if len(candles) > _MAX_CACHED_CANDLES:
            del candles[: len(candles) - _MAX_CACHED_CANDLES]

    def _handle_books(self, base: str, data: Any) -> None:
        """Cache a books5 snapshot as {"bids": [{"p","a"}...], ...}.

        Observed wire shape: data is a DICT {asks, bids, ts}; a
        single-element list is tolerated.  Level rows are
        [price, size_in_contracts].
        """
        book = data
        if isinstance(data, list):
            book = data[0] if data else None
        if not isinstance(book, dict):
            return

        def levels(rows: Any) -> List[Dict[str, float]]:
            converted = []
            for row in rows or []:
                try:
                    converted.append({"p": float(row[0]), "a": float(row[1])})
                except (TypeError, ValueError, IndexError):
                    continue
            return converted

        try:
            timestamp = int(book.get("ts", 0) or 0)
        except (TypeError, ValueError):
            timestamp = 0
        self._orderbook_cache[base] = {
            "bids": levels(book.get("bids")),
            "asks": levels(book.get("asks")),
            "timestamp": timestamp,
        }

    @staticmethod
    def _clean(symbol: str) -> str:
        """Normalize a symbol to the bare uppercase base."""
        cleaned = symbol.strip().upper()
        for suffix in ("-PERP", "-USDT"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
        return cleaned


_ws_singleton: Optional[BlofinWebSocketClient] = None
_ws_singleton_lock = threading.Lock()


def get_blofin_ws_client() -> BlofinWebSocketClient:
    """Return the process-wide Blofin WS data client singleton."""
    global _ws_singleton
    if _ws_singleton is None:
        with _ws_singleton_lock:
            if _ws_singleton is None:
                _ws_singleton = BlofinWebSocketClient()
    return _ws_singleton
