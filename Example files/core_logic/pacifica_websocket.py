"""
Pacifica.fi WebSocket Manager

Real-time WebSocket connection manager for Pacifica.fi exchange.
Handles market data, account updates, and hourly funding rate streams.

⚠️ CRITICAL: Funding rates update every 5 seconds, payments occur every hour

SDK Message Formats:
- Subscriptions: {"method": "subscribe", "params": {"source": "prices"}}
- Orders: {"id": "uuid", "params": {"create_market_order": {...}}}
"""

import asyncio
import json
import time
import uuid
import base58
from typing import Dict, List, Optional, Any, Callable, Set, Tuple
from datetime import datetime
from enum import Enum
import websockets
from websockets.client import WebSocketClientProtocol
from loguru import logger
from solders.keypair import Keypair


class ChannelType(Enum):
    """WebSocket channel types."""
    # Public channels
    TICKER = "ticker"
    ORDERBOOK = "orderbook"
    TRADES = "trades"
    FUNDING_RATE = "funding_rate"
    CANDLES = "candles"

    # Private channels (require authentication)
    ACCOUNT_BALANCE = "account_balance"
    ACCOUNT_POSITIONS = "account_positions"
    ACCOUNT_ORDERS = "account_orders"
    ACCOUNT_TRADES = "account_trades"


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class PacificaWebSocketManager:
    """
    WebSocket connection manager for Pacifica.fi.

    Features:
    - Automatic reconnection with exponential backoff
    - Subscription management
    - Message routing to callbacks
    - Heartbeat/ping-pong handling
    - Funding rate streaming (updates every 5 seconds)

    ⚠️ CRITICAL: Funding rates stream 24/7, updating every 5 seconds
    """

    def __init__(
        self,
        websocket_url: str,
        agent_wallet_keypair: Optional[Any] = None,
        account_public_key: Optional[str] = None,
        heartbeat_interval: int = 30,
        reconnect_delay: int = 5,
        max_reconnect_attempts: int = 10,
    ):
        """
        Initialize WebSocket manager.

        Args:
            websocket_url: WebSocket endpoint URL
            agent_wallet_keypair: Keypair for authentication
            account_public_key: Account public key
            heartbeat_interval: Heartbeat interval in seconds
            reconnect_delay: Base reconnect delay in seconds
            max_reconnect_attempts: Maximum reconnect attempts
        """
        self.websocket_url = websocket_url
        self.agent_wallet_keypair = agent_wallet_keypair
        self.account_public_key = account_public_key

        # Connection state
        self.ws: Optional[WebSocketClientProtocol] = None
        self.state = ConnectionState.DISCONNECTED
        self.connect_time: Optional[datetime] = None
        self.last_message_time: Optional[datetime] = None

        # Reconnection
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_count = 0

        # Heartbeat
        self.heartbeat_interval = heartbeat_interval
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None

        # Subscriptions
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        self.callbacks: Dict[str, List[Callable]] = {}

        # Message queue
        self.message_queue: asyncio.Queue = asyncio.Queue()

        # Statistics
        self.messages_received = 0
        self.messages_sent = 0
        self.reconnections = 0

        logger.info(f"Initialized Pacifica WebSocket manager: {websocket_url}")

    async def connect(self) -> bool:
        """
        Connect to WebSocket server.

        Returns:
            True if connected successfully
        """
        if self.state == ConnectionState.CONNECTED:
            logger.warning("Already connected")
            return True

        try:
            self.state = ConnectionState.CONNECTING
            logger.info(f"Connecting to {self.websocket_url}...")

            self.ws = await websockets.connect(
                self.websocket_url,
                ping_interval=self.heartbeat_interval,
                ping_timeout=self.heartbeat_interval * 2,
            )

            self.state = ConnectionState.CONNECTED
            self.connect_time = datetime.now()
            self.reconnect_count = 0

            logger.info("✅ WebSocket connected")

            # Start background tasks
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._receive_task = asyncio.create_task(self._receive_loop())

            # Re-subscribe to channels after reconnect
            await self._resubscribe_all()

            return True

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self.state = ConnectionState.FAILED
            return False

    async def disconnect(self):
        """Disconnect from WebSocket server."""
        logger.info("Disconnecting WebSocket...")

        # Cancel background tasks
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self.ws and not self.ws.closed:
            await self.ws.close()

        self.state = ConnectionState.DISCONNECTED
        self.ws = None
        logger.info("WebSocket disconnected")

    async def _heartbeat_loop(self):
        """Send periodic heartbeat/ping messages."""
        while self.state == ConnectionState.CONNECTED:
            try:
                await asyncio.sleep(self.heartbeat_interval)

                if self.ws and not self.ws.closed:
                    # Send ping
                    await self.send_message({
                        "type": "ping",
                        "timestamp": int(time.time() * 1000)
                    })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

    async def _receive_loop(self):
        """Receive and process messages from WebSocket."""
        while self.state == ConnectionState.CONNECTED:
            try:
                if not self.ws or self.ws.closed:
                    logger.warning("WebSocket closed, attempting reconnect...")
                    await self._reconnect()
                    continue

                # Receive message
                message_str = await self.ws.recv()
                self.messages_received += 1
                self.last_message_time = datetime.now()

                # Parse message
                try:
                    message = json.loads(message_str)
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON received: {e}")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed")
                await self._reconnect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Receive loop error: {e}")
                await asyncio.sleep(1)

    async def _handle_message(self, message: Dict[str, Any]):
        """
        Handle incoming WebSocket message.

        Args:
            message: Parsed message dictionary
        """
        message_type = message.get("type")

        # Handle system messages
        if message_type == "pong":
            logger.debug("Received pong")
            return

        if message_type == "error":
            error_code = message.get("error_code")
            error_msg = message.get("error", "Unknown error")
            logger.error(f"WebSocket error {error_code}: {error_msg}")
            return

        if message_type == "subscribed":
            channel = message.get("channel")
            logger.info(f"✅ Subscribed to {channel}")
            return

        if message_type == "unsubscribed":
            channel = message.get("channel")
            logger.info(f"❌ Unsubscribed from {channel}")
            return

        # Route to appropriate handler
        channel = message.get("channel")
        if channel:
            await self._route_message(channel, message)

    async def _route_message(self, channel: str, message: Dict[str, Any]):
        """
        Route message to registered callbacks.

        Args:
            channel: Channel identifier
            message: Message data
        """
        callbacks = self.callbacks.get(channel, [])

        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(message)
                else:
                    callback(message)
            except Exception as e:
                logger.error(f"Callback error for {channel}: {e}")

    async def _reconnect(self):
        """Reconnect to WebSocket with exponential backoff."""
        if self.reconnect_count >= self.max_reconnect_attempts:
            logger.error(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached")
            self.state = ConnectionState.FAILED
            return

        self.state = ConnectionState.RECONNECTING
        self.reconnect_count += 1
        self.reconnections += 1

        # Exponential backoff
        delay = self.reconnect_delay * (2 ** (self.reconnect_count - 1))
        logger.info(f"Reconnecting in {delay}s (attempt {self.reconnect_count}/{self.max_reconnect_attempts})")

        await asyncio.sleep(delay)
        await self.connect()

    async def _resubscribe_all(self):
        """Re-subscribe to all channels after reconnect."""
        if not self.subscriptions:
            return

        logger.info(f"Re-subscribing to {len(self.subscriptions)} channels...")

        for channel_id, sub_info in self.subscriptions.items():
            try:
                await self.send_message({
                    "type": "subscribe",
                    **sub_info
                })
            except Exception as e:
                logger.error(f"Failed to resubscribe to {channel_id}: {e}")

    async def send_message(self, message: Dict[str, Any]):
        """
        Send message to WebSocket server.

        Args:
            message: Message dictionary to send
        """
        if not self.ws or self.ws.closed:
            raise ConnectionError("WebSocket not connected")

        try:
            message_str = json.dumps(message)
            await self.ws.send(message_str)
            self.messages_sent += 1
            logger.debug(f"Sent: {message.get('type', 'unknown')}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise

    async def subscribe(
        self,
        channel: ChannelType,
        callback: Callable,
        market: Optional[str] = None,
        subaccount_id: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Subscribe to a WebSocket channel.

        Args:
            channel: Channel type
            callback: Callback function for messages
            market: Market symbol (for market data channels)
            subaccount_id: Specific subaccount ID for private channels (optional)
            **kwargs: Additional channel parameters

        Returns:
            Subscription ID

        Example:
            await ws.subscribe(
                ChannelType.FUNDING_RATE,
                on_funding_update,
                market="BTC-PERP"
            )

            await ws.subscribe(
                ChannelType.ACCOUNT_POSITIONS,
                on_positions_update,
                subaccount_id="sub_12345"
            )
        """
        # Build subscription message
        sub_message = {
            "type": "subscribe",
            "channel": channel.value,
        }

        if market:
            sub_message["market"] = market

        # Add subaccount_id for private channels
        if subaccount_id:
            sub_message["subaccount_id"] = subaccount_id

        sub_message.update(kwargs)

        # Generate subscription ID (include subaccount_id if present)
        parts = [channel.value]
        if market:
            parts.append(market)
        if subaccount_id:
            parts.append(subaccount_id)
        channel_id = ":".join(parts)

        # Store subscription
        self.subscriptions[channel_id] = sub_message

        # Register callback
        if channel_id not in self.callbacks:
            self.callbacks[channel_id] = []
        self.callbacks[channel_id].append(callback)

        # Send subscription if connected
        if self.state == ConnectionState.CONNECTED:
            await self.send_message(sub_message)

        logger.info(f"📡 Subscribed to {channel_id}")
        return channel_id

    async def unsubscribe(self, channel_id: str):
        """
        Unsubscribe from a channel.

        Args:
            channel_id: Subscription ID to unsubscribe
        """
        if channel_id not in self.subscriptions:
            logger.warning(f"Not subscribed to {channel_id}")
            return

        # Send unsubscribe message
        if self.state == ConnectionState.CONNECTED:
            await self.send_message({
                "type": "unsubscribe",
                "channel": channel_id
            })

        # Remove subscription
        del self.subscriptions[channel_id]
        if channel_id in self.callbacks:
            del self.callbacks[channel_id]

        logger.info(f"🔇 Unsubscribed from {channel_id}")

    async def subscribe_funding_rate(
        self,
        market: str,
        callback: Callable
    ) -> str:
        """
        Subscribe to hourly funding rate updates.

        ⚠️ CRITICAL: Funding rates update every 5 seconds!

        Args:
            market: Market symbol (e.g., "BTC-PERP")
            callback: Callback function receiving funding rate updates

        Returns:
            Subscription ID
        """
        return await self.subscribe(
            ChannelType.FUNDING_RATE,
            callback,
            market=market
        )

    async def subscribe_account_positions(self, callback: Callable, subaccount_id: Optional[str] = None) -> str:
        """
        Subscribe to account position updates.

        Args:
            callback: Callback function for position updates
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            Subscription ID
        """
        return await self.subscribe(
            ChannelType.ACCOUNT_POSITIONS,
            callback,
            subaccount_id=subaccount_id
        )

    async def subscribe_ticker(self, market: str, callback: Callable) -> str:
        """
        Subscribe to ticker updates for a market.

        Args:
            market: Market symbol
            callback: Callback function for ticker updates

        Returns:
            Subscription ID
        """
        return await self.subscribe(
            ChannelType.TICKER,
            callback,
            market=market
        )

    async def subscribe_orderbook(
        self,
        market: str,
        callback: Callable,
        depth: int = 20
    ) -> str:
        """
        Subscribe to orderbook updates.

        Args:
            market: Market symbol
            callback: Callback function for orderbook updates
            depth: Orderbook depth (default: 20)

        Returns:
            Subscription ID
        """
        return await self.subscribe(
            ChannelType.ORDERBOOK,
            callback,
            market=market,
            depth=depth
        )

    async def subscribe_trades(self, market: str, callback: Callable) -> str:
        """
        Subscribe to trade stream.

        Args:
            market: Market symbol
            callback: Callback function for trade updates

        Returns:
            Subscription ID
        """
        return await self.subscribe(
            ChannelType.TRADES,
            callback,
            market=market
        )

    async def subscribe_subaccount_all(
        self,
        subaccount_id: str,
        callbacks: Dict[str, Callable]
    ) -> List[str]:
        """
        Subscribe to all private channels for a specific subaccount.

        Convenience method to subscribe to positions, orders, trades, and balance
        updates for a given subaccount.

        Args:
            subaccount_id: Subaccount ID to subscribe to
            callbacks: Dict mapping channel types to callback functions
                       Keys: "positions", "orders", "trades", "balance"

        Returns:
            List of subscription IDs

        Example:
            subscription_ids = await ws.subscribe_subaccount_all(
                "sub_12345",
                {
                    "positions": on_positions_update,
                    "balance": on_balance_update
                }
            )
        """
        subscription_ids = []

        # Subscribe to positions
        if "positions" in callbacks:
            sub_id = await self.subscribe(
                ChannelType.ACCOUNT_POSITIONS,
                callbacks["positions"],
                subaccount_id=subaccount_id
            )
            subscription_ids.append(sub_id)

        # Subscribe to orders
        if "orders" in callbacks:
            sub_id = await self.subscribe(
                ChannelType.ACCOUNT_ORDERS,
                callbacks["orders"],
                subaccount_id=subaccount_id
            )
            subscription_ids.append(sub_id)

        # Subscribe to trades
        if "trades" in callbacks:
            sub_id = await self.subscribe(
                ChannelType.ACCOUNT_TRADES,
                callbacks["trades"],
                subaccount_id=subaccount_id
            )
            subscription_ids.append(sub_id)

        # Subscribe to balance
        if "balance" in callbacks:
            sub_id = await self.subscribe(
                ChannelType.ACCOUNT_BALANCE,
                callbacks["balance"],
                subaccount_id=subaccount_id
            )
            subscription_ids.append(sub_id)

        logger.info(f"✅ Subscribed to {len(subscription_ids)} channels for subaccount {subaccount_id}")
        return subscription_ids

    def get_stats(self) -> Dict[str, Any]:
        """
        Get WebSocket statistics.

        Returns:
            Statistics dictionary
        """
        uptime = None
        if self.connect_time:
            uptime = (datetime.now() - self.connect_time).total_seconds()

        return {
            "state": self.state.value,
            "connected": self.state == ConnectionState.CONNECTED,
            "uptime_seconds": uptime,
            "messages_received": self.messages_received,
            "messages_sent": self.messages_sent,
            "subscriptions": len(self.subscriptions),
            "reconnections": self.reconnections,
            "reconnect_attempts": self.reconnect_count,
            "last_message": self.last_message_time.isoformat() if self.last_message_time else None,
        }

    def __repr__(self) -> str:
        return f"<PacificaWebSocketManager state={self.state.value} subs={len(self.subscriptions)}>"

    # ============================================
    # SDK-STYLE MESSAGE SIGNING (for order operations)
    # ============================================

    def _sort_json_keys(self, obj: Any) -> Any:
        """Recursively sort all keys in nested dictionaries (SDK format)."""
        if isinstance(obj, dict):
            return {k: self._sort_json_keys(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, list):
            return [self._sort_json_keys(item) for item in obj]
        return obj

    def _prepare_message(self, header: Dict[str, Any], payload: Dict[str, Any]) -> str:
        """Prepare message for signing using SDK format."""
        data = {**header, "data": payload}
        sorted_data = self._sort_json_keys(data)
        return json.dumps(sorted_data, separators=(",", ":"))

    def _sign_message(self, message: str) -> str:
        """Sign a message using agent wallet keypair (SDK format with base58)."""
        if not self.agent_wallet_keypair:
            raise ValueError("Agent wallet keypair not configured for signing")

        message_bytes = message.encode("utf-8")
        signature = self.agent_wallet_keypair.sign_message(message_bytes)
        return base58.b58encode(bytes(signature)).decode("ascii")

    def _create_signed_request(
        self,
        request_type: str,
        payload: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], str]:
        """
        Create a signed request for WebSocket operations.

        Args:
            request_type: Type of request (e.g., "create_market_order")
            payload: Request payload data

        Returns:
            Tuple of (signed_message_dict, request_id)
        """
        if not self.agent_wallet_keypair or not self.account_public_key:
            raise ValueError("Agent wallet keypair and account public key required for signed operations")

        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": request_type,
        }

        # Prepare and sign message
        message = self._prepare_message(signature_header, payload)
        signature = self._sign_message(message)

        # Build request
        request_header = {
            "account": self.account_public_key,
            "signature": signature,
            "timestamp": timestamp,
            "expiry_window": 5000,
        }

        signed_message = {**request_header, **payload}
        request_id = str(uuid.uuid4())

        return signed_message, request_id

    # ============================================
    # WEBSOCKET ORDER OPERATIONS (SDK-style)
    # ============================================

    async def create_market_order_ws(
        self,
        symbol: str,
        side: str,
        amount: str,
        slippage_percent: str = "0.5",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a market order via WebSocket.

        Args:
            symbol: Trading pair (e.g., "BTC")
            side: "bid" (buy) or "ask" (sell)
            amount: Order amount as string
            slippage_percent: Max slippage percentage
            reduce_only: Only reduce existing position
            client_order_id: Optional client order ID

        Returns:
            Order response from server
        """
        payload = {
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "slippage_percent": slippage_percent,
            "reduce_only": reduce_only,
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }

        signed_message, request_id = self._create_signed_request("create_market_order", payload)

        ws_message = {
            "id": request_id,
            "params": {"create_market_order": signed_message}
        }

        await self.send_message(ws_message)
        logger.info(f"📤 Sent market order via WS: {symbol} {side} {amount}")

        # Return request_id so caller can match response
        return {"request_id": request_id, "sent": True}

    async def create_limit_order_ws(
        self,
        symbol: str,
        side: str,
        amount: str,
        price: str,
        tif: str = "GTC",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a limit order via WebSocket.

        Args:
            symbol: Trading pair (e.g., "BTC")
            side: "bid" (buy) or "ask" (sell)
            amount: Order amount as string
            price: Limit price as string
            tif: Time in force ("GTC", "IOC", "FOK")
            reduce_only: Only reduce existing position
            client_order_id: Optional client order ID

        Returns:
            Order response from server
        """
        payload = {
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "tif": tif,
            "reduce_only": reduce_only,
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }

        signed_message, request_id = self._create_signed_request("create_order", payload)

        ws_message = {
            "id": request_id,
            "params": {"create_order": signed_message}
        }

        await self.send_message(ws_message)
        logger.info(f"📤 Sent limit order via WS: {symbol} {side} {amount} @ {price}")

        return {"request_id": request_id, "sent": True}

    async def cancel_order_ws(
        self,
        symbol: str,
        order_id: int
    ) -> Dict[str, Any]:
        """
        Cancel an order via WebSocket.

        Args:
            symbol: Trading pair
            order_id: Order ID to cancel

        Returns:
            Cancel response from server
        """
        payload = {
            "symbol": symbol,
            "order_id": order_id,
        }

        signed_message, request_id = self._create_signed_request("cancel_order", payload)

        ws_message = {
            "id": request_id,
            "params": {"cancel_order": signed_message}
        }

        await self.send_message(ws_message)
        logger.info(f"📤 Sent cancel order via WS: {symbol} #{order_id}")

        return {"request_id": request_id, "sent": True}

    async def cancel_all_orders_ws(
        self,
        all_symbols: bool = True,
        symbol: Optional[str] = None,
        exclude_reduce_only: bool = False
    ) -> Dict[str, Any]:
        """
        Cancel all orders via WebSocket.

        Args:
            all_symbols: Cancel across all symbols
            symbol: Specific symbol (if not all_symbols)
            exclude_reduce_only: Keep reduce-only orders

        Returns:
            Cancel response from server
        """
        payload = {
            "all_symbols": all_symbols,
            "exclude_reduce_only": exclude_reduce_only,
        }

        if not all_symbols and symbol:
            payload["symbol"] = symbol

        signed_message, request_id = self._create_signed_request("cancel_all_orders", payload)

        ws_message = {
            "id": request_id,
            "params": {"cancel_all_orders": signed_message}
        }

        await self.send_message(ws_message)
        logger.info(f"📤 Sent cancel all orders via WS")

        return {"request_id": request_id, "sent": True}

    # ============================================
    # SDK-STYLE SUBSCRIPTIONS
    # ============================================

    async def subscribe_prices(self, callback: Callable) -> str:
        """
        Subscribe to real-time price updates for all markets.
        Uses SDK format: {"method": "subscribe", "params": {"source": "prices"}}

        Args:
            callback: Callback function for price updates

        Returns:
            Subscription ID
        """
        channel_id = "prices"

        # Store callback
        if channel_id not in self.callbacks:
            self.callbacks[channel_id] = []
        self.callbacks[channel_id].append(callback)

        # Store subscription for reconnect
        self.subscriptions[channel_id] = {"method": "subscribe", "params": {"source": "prices"}}

        # Send subscription if connected
        if self.state == ConnectionState.CONNECTED:
            await self.send_message({"method": "subscribe", "params": {"source": "prices"}})

        logger.info("📡 Subscribed to prices stream")
        return channel_id

    async def subscribe_account_twap(self, callback: Callable) -> List[str]:
        """
        Subscribe to TWAP order updates for the account.

        Args:
            callback: Callback function for TWAP updates

        Returns:
            List of subscription IDs
        """
        if not self.account_public_key:
            raise ValueError("Account public key required for TWAP subscription")

        subscription_ids = []

        # Subscribe to open TWAP orders
        twap_orders_id = f"account_twap_orders:{self.account_public_key}"
        if twap_orders_id not in self.callbacks:
            self.callbacks[twap_orders_id] = []
        self.callbacks[twap_orders_id].append(callback)

        self.subscriptions[twap_orders_id] = {
            "method": "subscribe",
            "params": {"source": "account_twap_orders", "account": self.account_public_key}
        }

        if self.state == ConnectionState.CONNECTED:
            await self.send_message({
                "method": "subscribe",
                "params": {"source": "account_twap_orders", "account": self.account_public_key}
            })
        subscription_ids.append(twap_orders_id)

        # Subscribe to TWAP order updates
        twap_updates_id = f"account_twap_order_updates:{self.account_public_key}"
        if twap_updates_id not in self.callbacks:
            self.callbacks[twap_updates_id] = []
        self.callbacks[twap_updates_id].append(callback)

        self.subscriptions[twap_updates_id] = {
            "method": "subscribe",
            "params": {"source": "account_twap_order_updates", "account": self.account_public_key}
        }

        if self.state == ConnectionState.CONNECTED:
            await self.send_message({
                "method": "subscribe",
                "params": {"source": "account_twap_order_updates", "account": self.account_public_key}
            })
        subscription_ids.append(twap_updates_id)

        logger.info("📡 Subscribed to TWAP order streams")
        return subscription_ids


# Example usage and callbacks
async def example_funding_rate_callback(message: Dict[str, Any]):
    """
    Example callback for funding rate updates.

    ⚠️ CRITICAL: Called every 5 seconds with updated funding rate
    """
    market = message.get("market")
    funding_rate = message.get("funding_rate")
    next_payment = message.get("next_payment_time")

    logger.info(
        f"💰 Funding Update: {market} = {funding_rate*100:.4f}% per hour "
        f"(Next payment: {next_payment})"
    )


async def example_position_callback(message: Dict[str, Any]):
    """Example callback for position updates."""
    positions = message.get("positions", [])
    logger.info(f"📊 Position Update: {len(positions)} positions")

    for pos in positions:
        symbol = pos.get("symbol")
        size = pos.get("size")
        unrealized_pnl = pos.get("unrealized_pnl")
        funding_paid = pos.get("cumulative_funding_paid", 0)

        logger.info(
            f"  {symbol}: Size={size}, PnL=${unrealized_pnl:.2f}, "
            f"Funding=${funding_paid:.2f}"
        )


# Factory functions
def create_pacifica_websocket(
    environment: str = "testnet",
    agent_wallet_keypair: Optional[Any] = None,
    account_public_key: Optional[str] = None,
) -> PacificaWebSocketManager:
    """
    Create Pacifica WebSocket manager instance.

    Args:
        environment: "testnet" or "mainnet"
        agent_wallet_keypair: Agent wallet keypair for auth
        account_public_key: Account public key

    Returns:
        PacificaWebSocketManager instance
    """
    ws_urls = {
        "testnet": "wss://test-ws.pacifica.fi/ws",
        "mainnet": "wss://ws.pacifica.fi/ws",
    }

    ws_url = ws_urls.get(environment, ws_urls["testnet"])

    return PacificaWebSocketManager(
        websocket_url=ws_url,
        agent_wallet_keypair=agent_wallet_keypair,
        account_public_key=account_public_key,
    )
