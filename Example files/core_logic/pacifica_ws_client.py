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
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Callable, List, Union
import websockets
from websockets.exceptions import ConnectionClosed
from loguru import logger
from solders.keypair import Keypair
import base58


class PacificaWebSocketClient:
    _instance = None
    _lock = threading.Lock()

    __slots__ = (
        "_initialized", "_loop", "_task", "_ws", "_connected", "_reconnect_delay",
        "_agent_keypair", "_account_pubkey", "_ws_url",
        "_price_cache", "_funding_cache", "_position_cache", "_balance_cache",
        "_ui_callbacks", "_channel_callbacks", "_last_heartbeat", "_running",
        "_message_count"  # For debug logging
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
        self._message_count = 0  # Initialize message counter for debug logging
        self._ui_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._channel_callbacks: Dict[str, Callable[[Dict[str, Any]], None]] = {}

        # Determine WebSocket URL based on TESTNET env
        testnet = os.getenv("TESTNET", "true").lower() == "true"
        self._ws_url = "wss://test-ws.pacifica.fi/ws" if testnet else "wss://ws.pacifica.fi/ws"

        # Caches (thread-safe via GIL + atomic ops)
        self._price_cache: Dict[str, float] = {}
        self._funding_cache: Dict[str, Dict[str, Any]] = {}
        self._position_cache: Dict[str, Dict[str, Any]] = {}
        self._balance_cache: Dict[str, Any] = {"balance": 0.0, "available": 0.0, "equity": 0.0}

        # Load keys from environment (same as REST client)
        agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")
        if not agent_private_key or not account_public_key:
            raise ValueError("Pacifica credentials not found in environment. Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY.")
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
        asyncio.run_coroutine_threadsafe(self._run(), self._loop)

    def stop(self):
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

    def get_positions(self) -> Dict[str, Dict[str, Any]]:
        """Get all current positions (read-only view to avoid memory copies)"""
        from types import MappingProxyType
        return MappingProxyType(self._position_cache)

    def get_balance(self) -> Dict[str, Any]:
        """Get latest balance info (read-only view to avoid memory copies)"""
        from types import MappingProxyType
        return MappingProxyType(self._balance_cache)

    def register_ui_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Register callback for FastAPI /ws to forward events to browser"""
        self._ui_callbacks.append(callback)

    async def subscribe(self, channel_type, callback: Callable[[Dict[str, Any]], None]):
        """Subscribe to a channel with a callback"""
        # Map channel type to event type
        channel_map = {
            "ticker": "price",
            "funding": "funding",
            "position": "position_update",
            "order": "order_update",
            "balance": "balance"
        }
        event_type = channel_map.get(channel_type.value if hasattr(channel_type, 'value') else str(channel_type), str(channel_type))
        self._channel_callbacks[event_type] = callback

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
                    max_size=2**20
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
        message = json.dumps({
            "op": "auth",
            "timestamp": timestamp,
            "account": self._account_pubkey
        }).encode()

        signature = self._agent_keypair.sign_message(message)
        sig_b58 = base58.b58encode(bytes(signature)).decode()

        auth_msg = {
            "op": "auth",
            "timestamp": timestamp,
            "account": self._account_pubkey,
            "signature": sig_b58
        }
        await ws.send(json.dumps(auth_msg))
        logger.info("Sent auth message")

    async def _subscribe_public(self, ws):
        # Subscribe to all-market price stream (SDK format)
        await ws.send(json.dumps({"method": "subscribe", "params": {"source": "prices"}}))
        logger.info("Subscribed to prices stream (all markets)")

    async def _subscribe_private(self, ws):
        # Wait a moment for auth to settle
        await asyncio.sleep(1)
        subs = ["position", "order", "executionReport", "balance"]
        await ws.send(json.dumps({"op": "subscribe", "channels": subs}))
        logger.info(f"Subscribed to private: {subs}")

    async def _handle_message(self, raw: Union[str, bytes]):
        try:
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            data = json.loads(raw)

            # Heartbeat
            if data.get("type") == "pong":
                self._last_heartbeat = time.time()
                return

            channel = data.get("channel", "")

            # Debug: Log incoming messages (first 50 messages only to avoid spam)
            if self._message_count < 50:
                logger.debug(f"WS message: channel={channel}, data_keys={list(data.keys())}")
                self._message_count += 1

            # Handle prices stream (SDK format - array of market data)
            if channel == "prices":
                price_data = data.get("data", [])
                if isinstance(price_data, list):
                    for market in price_data:
                        symbol = market.get("symbol", "").upper()
                        # Use 'mark' price (mark price is most reliable for perpetuals)
                        price = market.get("mark") or market.get("oracle") or market.get("mid")
                        if symbol and price:
                            try:
                                self._price_cache[symbol] = float(price)
                                if self._message_count < 10:  # Only log first few prices
                                    logger.debug(f"Price update: {symbol} = ${price}")
                                event = {"type": "price", "symbol": symbol, "price": price}
                                self._broadcast(event)
                                self._call_channel_callback("price", event)
                            except (ValueError, TypeError) as e:
                                logger.warning(f"Invalid price data for {symbol}: {price} - {e}")

            # Legacy ticker format (if still used by some channels)
            elif channel.startswith("ticker"):
                symbol = data.get("symbol", "").upper()
                if symbol and "price" in data:
                    self._price_cache[symbol] = float(data["price"])
                    logger.debug(f"Price update: {symbol} = ${data['price']}")
                    event = {"type": "price", "symbol": symbol, "price": data["price"]}
                    self._broadcast(event)
                    self._call_channel_callback("price", event)
                else:
                    logger.warning(f"Ticker message missing symbol or price: {data}")

            elif channel.startswith("funding"):
                symbol = data.get("symbol", "").upper()
                if symbol:
                    self._funding_cache[symbol] = {
                        "rate": float(data["fundingRate"]),
                        "next": data.get("nextFundingTime"),
                        "timestamp": data.get("timestamp")
                    }
                    event = {"type": "funding", "symbol": symbol, **self._funding_cache[symbol]}
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
                self._balance_cache.update({
                    "balance": float(data.get("balance", 0)),
                    "available": float(data.get("availableBalance", 0)),
                    "equity": float(data.get("equity", 0)),
                    "timestamp": data.get("timestamp")
                })
                event = {"type": "balance", **self._balance_cache}
                self._broadcast(event)
                self._call_channel_callback("balance", event)

            elif channel in ("order", "executionReport"):
                event = {"type": "order_update", "data": data}
                self._broadcast(event)
                self._call_channel_callback("order_update", event)

        except Exception as e:
            logger.error(f"Error handling WS message: {e} | Raw: {raw[:200]}")

    def _call_channel_callback(self, event_type: str, event: Dict[str, Any]):
        """Call the specific channel callback"""
        if event_type in self._channel_callbacks:
            try:
                asyncio.create_task(self._channel_callbacks[event_type](event))
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

def get_ws_client() -> PacificaWebSocketClient:
    """Singleton accessor"""
    client = PacificaWebSocketClient()
    client.start()
    return client


# Auto-start on import (optional)
# get_ws_client()