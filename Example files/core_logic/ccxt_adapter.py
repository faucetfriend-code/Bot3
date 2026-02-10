"""
CCXT-based exchange adapter for unified cryptocurrency exchange integration.
Provides a standardized interface across multiple exchanges while maintaining
compatibility with existing Pacifica.fi integration.
"""

import asyncio
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
import ccxt
import ccxt.async_support as ccxt_async
from config import get_config
from models import Order, Position, Trade, OrderSide, OrderType, OrderStatus
from risk import RiskViolationError, InsufficientFundsError
from loguru import logger


class CCXTExchangeAdapter:
    """
    Unified exchange adapter using CCXT library.
    Supports multiple exchanges with standardized API.
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = True,
        sandbox: bool = True,
    ):
        """
        Initialize CCXT exchange adapter.

        Args:
            exchange_id: CCXT exchange identifier (e.g., 'binance', 'coinbase', 'kraken')
            api_key: Exchange API key
            api_secret: Exchange API secret
            testnet: Use testnet/sandbox mode
            sandbox: Enable sandbox mode for testing
        """
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.sandbox = sandbox

        # Initialize sync and async exchange instances
        self.exchange_class = getattr(ccxt, exchange_id)
        self.async_exchange_class = getattr(ccxt_async, exchange_id)

        # Configure exchange parameters
        exchange_params = {
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {},
        }

        if sandbox or testnet:
            exchange_params['sandbox'] = True

        # Create exchange instances
        self.exchange = self.exchange_class(exchange_params)
        self.async_exchange = self.async_exchange_class(exchange_params)

        # Connection status
        self.connected = False
        self.last_ping = None

        logger.info(f"Initialized CCXT adapter for {exchange_id} (testnet: {testnet}, sandbox: {sandbox})")

    async def connect(self) -> bool:
        """
        Establish connection to exchange.

        Returns:
            bool: True if connection successful
        """
        try:
            # Test connection by loading markets
            await self.async_exchange.loadMarkets()
            self.connected = True
            self.last_ping = datetime.now()
            logger.info(f"Connected to {self.exchange_id} exchange")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to {self.exchange_id}: {e}")
            self.connected = False
            return False

    async def disconnect(self):
        """Close exchange connection."""
        try:
            await self.async_exchange.close()
            self.connected = False
            logger.info(f"Disconnected from {self.exchange_id}")
        except Exception as e:
            logger.error(f"Error disconnecting from {self.exchange_id}: {e}")

    async def get_balance(self, currency: Optional[str] = None) -> Dict[str, Any]:
        """
        Get account balance.

        Args:
            currency: Specific currency to get balance for (optional)

        Returns:
            Dict containing balance information
        """
        try:
            if not self.connected:
                await self.connect()

            balance = await self.async_exchange.fetchBalance()

            if currency:
                return {
                    'currency': currency,
                    'free': balance.get(currency, {}).get('free', 0),
                    'used': balance.get(currency, {}).get('used', 0),
                    'total': balance.get(currency, {}).get('total', 0),
                }

            return balance
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            raise ExchangeError(f"Failed to fetch balance: {e}")

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get ticker information for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT')

        Returns:
            Dict containing ticker data
        """
        try:
            if not self.connected:
                await self.connect()

            ticker = await self.async_exchange.fetchTicker(symbol)
            return ticker
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            raise ExchangeError(f"Failed to fetch ticker: {e}")

    async def get_orderbook(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """
        Get order book for a symbol.

        Args:
            symbol: Trading pair symbol
            limit: Number of orders to retrieve

        Returns:
            Dict containing bids and asks
        """
        try:
            if not self.connected:
                await self.connect()

            orderbook = await self.async_exchange.fetchOrderBook(symbol, limit)
            return orderbook
        except Exception as e:
            logger.error(f"Error fetching orderbook for {symbol}: {e}")
            raise ExchangeError(f"Failed to fetch orderbook: {e}")

    async def place_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Place an order on the exchange.

        Args:
            symbol: Trading pair symbol
            order_type: Order type ('limit', 'market', 'stop', etc.)
            side: Order side ('buy' or 'sell')
            amount: Order amount
            price: Order price (required for limit orders)
            params: Additional order parameters

        Returns:
            Dict containing order information
        """
        try:
            if not self.connected:
                await self.connect()

            if params is None:
                params = {}

            # Place order using CCXT
            order = await self.async_exchange.createOrder(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=params,
            )

            logger.info(f"Placed {order_type} {side} order for {amount} {symbol}")
            return order

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            raise OrderPlacementError(f"Failed to place order: {e}")

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel
            symbol: Trading pair symbol

        Returns:
            bool: True if cancellation successful
        """
        try:
            if not self.connected:
                await self.connect()

            result = await self.async_exchange.cancelOrder(order_id, symbol)
            logger.info(f"Cancelled order {order_id}")
            return True

        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False

    async def get_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Get order information.

        Args:
            order_id: Order ID
            symbol: Trading pair symbol

        Returns:
            Dict containing order information
        """
        try:
            if not self.connected:
                await self.connect()

            order = await self.async_exchange.fetchOrder(order_id, symbol)
            return order

        except Exception as e:
            logger.error(f"Error fetching order {order_id}: {e}")
            raise ExchangeError(f"Failed to fetch order: {e}")

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get open orders.

        Args:
            symbol: Trading pair symbol (optional)

        Returns:
            List of open orders
        """
        try:
            if not self.connected:
                await self.connect()

            orders = await self.async_exchange.fetchOpenOrders(symbol)
            return orders

        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            raise ExchangeError(f"Failed to fetch open orders: {e}")

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get current positions.

        Args:
            symbol: Trading pair symbol (optional)

        Returns:
            List of positions
        """
        try:
            if not self.connected:
                await self.connect()

            # For spot trading, positions are represented as balances
            # For futures, this would be different
            if hasattr(self.async_exchange, 'fetchPositions'):
                positions = await self.async_exchange.fetchPositions(symbol)
            else:
                # For spot exchanges, return empty list or mock positions
                positions = []

            return positions

        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            raise ExchangeError(f"Failed to fetch positions: {e}")

    async def get_trades(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get trade history.

        Args:
            symbol: Trading pair symbol
            since: Timestamp to fetch trades from
            limit: Maximum number of trades to retrieve

        Returns:
            List of trades
        """
        try:
            if not self.connected:
                await self.connect()

            trades = await self.async_exchange.fetchMyTrades(symbol, since, limit)
            return trades

        except Exception as e:
            logger.error(f"Error fetching trades for {symbol}: {e}")
            raise ExchangeError(f"Failed to fetch trades: {e}")

    def get_supported_exchanges(self) -> List[str]:
        """
        Get list of supported exchanges.

        Returns:
            List of exchange IDs
        """
        return ccxt.exchanges

    def get_exchange_info(self) -> Dict[str, Any]:
        """
        Get exchange information and capabilities.

        Returns:
            Dict containing exchange information
        """
        return {
            'id': self.exchange.id,
            'name': self.exchange.name,
            'countries': self.exchange.countries,
            'urls': self.exchange.urls,
            'has': self.exchange.has,
            'timeframes': getattr(self.exchange, 'timeframes', {}),
            'markets': len(self.exchange.markets) if hasattr(self.exchange, 'markets') else 0,
        }


class ExchangeError(Exception):
    """Base exception for exchange-related errors."""
    pass


class OrderPlacementError(ExchangeError):
    """Raised when order placement fails."""
    pass


class ConnectionError(ExchangeError):
    """Raised when exchange connection fails."""
    pass


# Factory function to create exchange adapters
def create_exchange_adapter(
    exchange_id: str,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    testnet: bool = True,
) -> CCXTExchangeAdapter:
    """
    Factory function to create exchange adapter instances.

    Args:
        exchange_id: CCXT exchange identifier
        api_key: Exchange API key
        api_secret: Exchange API secret
        testnet: Use testnet mode

    Returns:
        CCXTExchangeAdapter instance
    """
    return CCXTExchangeAdapter(
        exchange_id=exchange_id,
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet,
    )


# Utility functions
def get_available_exchanges() -> List[str]:
    """Get list of all available CCXT exchanges."""
    return ccxt.exchanges


def is_exchange_supported(exchange_id: str) -> bool:
    """Check if an exchange is supported by CCXT."""
    return exchange_id in ccxt.exchanges


def get_exchange_capabilities(exchange_id: str) -> Dict[str, bool]:
    """Get capabilities of a specific exchange."""
    try:
        exchange_class = getattr(ccxt, exchange_id)
        exchange = exchange_class()
        return exchange.has
    except Exception:
        return {}