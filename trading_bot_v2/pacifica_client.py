import json
import time
import uuid
import base58
from typing import Dict, List, Any, Optional
from enum import Enum

import requests
from solders.keypair import Keypair
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

# API request timeout in seconds
API_TIMEOUT = 30

# Configure logger for retry
logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Custom exception for HTTP 429 rate limit errors - allows retry with backoff."""

    pass


class PacificaEnvironment(Enum):
    """Pacifica API environments."""

    TESTNET = "https://test-api.pacifica.fi/api/v1"
    MAINNET = "https://api.pacifica.fi/api/v1"


class RateLimitTier(Enum):
    """Rate limit tiers based on trading volume."""

    BASIC = {"rest_per_second": 10, "websocket_subscriptions": 50}
    ADVANCED = {"rest_per_second": 20, "websocket_subscriptions": 100}
    VIP = {"rest_per_second": 50, "websocket_subscriptions": 200}


def sign_message(header, payload, keypair):
    message = prepare_message(header, payload)
    message_bytes = message.encode("utf-8")
    signature = keypair.sign_message(message_bytes)
    return (message, base58.b58encode(bytes(signature)).decode("ascii"))


def prepare_message(header, payload):
    if (
        "type" not in header
        or "timestamp" not in header
        or "expiry_window" not in header
    ):
        raise ValueError("Header must have type, timestamp, and expiry_window")

    data = {
        **header,
        "data": payload,
    }

    message = sort_json_keys(data)

    # Specifying the separators is important because the JSON message is expected to be compact.
    message = json.dumps(message, separators=(",", ":"))

    return message


def sort_json_keys(value):
    if isinstance(value, dict):
        sorted_dict = {}
        for key in sorted(value.keys()):
            sorted_dict[key] = sort_json_keys(value[key])
        return sorted_dict
    elif isinstance(value, list):
        return [sort_json_keys(item) for item in value]
    else:
        return value


class PacificaClient:
    """Client for interacting with the Pacifica exchange API."""

    def __init__(
        self,
        agent_wallet_private_key: str,
        account_public_key: str,
        testnet: bool = True,
    ):
        """
        Initialize the Pacifica client.

        Args:
            agent_wallet_private_key: Private key for the agent wallet.
            account_public_key: Public key for the account.
            testnet: Whether to use testnet (default True).
        """
        self.agent_keypair = Keypair.from_base58_string(agent_wallet_private_key)
        self.agent_wallet_public_key = str(self.agent_keypair.pubkey())
        self.account_public_key = account_public_key
        self.base_url = (
            "https://test-api.pacifica.fi/api/v1"
            if testnet
            else "https://api.pacifica.fi/api/v1"
        )

        # Connection pooling - reuse TCP connections for better performance
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        logger.info("PacificaClient initialized with connection pooling")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RateLimitError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _make_signed_request(
        self, endpoint: str, payload: Dict[str, Any], request_type: str
    ) -> Dict[str, Any]:
        """
        Make a signed POST request to the API with retry logic.

        Args:
            endpoint: API endpoint.
            payload: Request payload.
            request_type: Type for signature header.

        Returns:
            JSON response as dict.

        Raises:
            ValueError: For authentication or API errors.
            RateLimitError: For rate limit (429) - will be retried with backoff.
        """
        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": request_type,
        }

        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        request_data = {
            "account": self.account_public_key,
            "agent_wallet": self.agent_wallet_public_key,
            "signature": signature,
            "timestamp": signature_header["timestamp"],
            "expiry_window": signature_header["expiry_window"],
            **payload,
        }

        url = self.base_url + endpoint

        try:
            response = self.session.post(url, json=request_data, timeout=API_TIMEOUT)
        except requests.exceptions.Timeout:
            logger.warning(f"Request timeout for {endpoint}, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error for {endpoint}: {e}, will retry...")
            raise

        if response.status_code == 401:
            raise ValueError("Authentication failed")
        elif response.status_code == 429:
            logger.warning(f"Rate limit hit (429) for {endpoint}, backing off...")
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(f"API error: {response.text}")

        return response.json()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RateLimitError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _make_get_request(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make a GET request to the API with retry logic.

        Args:
            endpoint: API endpoint.
            params: Query parameters.

        Returns:
            JSON response as dict.

        Raises:
            ValueError: For authentication or API errors.
            RateLimitError: For rate limit (429) - will be retried with backoff.
        """
        url = self.base_url + endpoint

        try:
            response = self.session.get(url, params=params, timeout=API_TIMEOUT)
        except requests.exceptions.Timeout:
            logger.warning(f"GET timeout for {endpoint}, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GET connection error for {endpoint}: {e}, will retry...")
            raise

        if response.status_code >= 400:
            if response.status_code == 401:
                raise ValueError("Authentication failed")
            elif response.status_code == 429:
                logger.warning(
                    f"Rate limit hit (429) for GET {endpoint}, backing off..."
                )
                raise RateLimitError(
                    "Rate limit exceeded - retrying with exponential backoff"
                )
            else:
                raise ValueError(
                    f"API error: {response.reason or response.text or 'Unknown error'}"
                )

        return response.json()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RateLimitError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Dict[str, Any]:
        """
        Generic request method that can handle both GET and POST requests.

        Args:
            endpoint: API endpoint.
            method: HTTP method ('GET' or 'POST').
            params: Query parameters for GET requests.
            data: Request body data for POST requests.
            signed: Whether to sign the request.

        Returns:
            JSON response as dict.

        Raises:
            ValueError: For authentication or API errors.
            RateLimitError: For rate limit (429) - will be retried with backoff.
        """
        url = self.base_url + endpoint
        headers = {"Content-Type": "application/json"}

        if signed and method == "GET":
            # For signed GET requests, add signature to params
            timestamp = int(time.time() * 1000)
            signature_header = {
                "timestamp": timestamp,
                "expiry_window": 5000,
                "type": "get_request",  # Generic type for signed GET
            }
            payload = data or {}
            message, signature = sign_message(
                signature_header, payload, self.agent_keypair
            )

            params = params or {}
            params.update(
                {
                    "account": self.account_public_key,
                    "agent_wallet": self.agent_wallet_public_key,
                    "signature": signature,
                    "timestamp": signature_header["timestamp"],
                    "expiry_window": signature_header["expiry_window"],
                }
            )

        try:
            if method.upper() == "GET":
                response = self.session.get(url, params=params, timeout=API_TIMEOUT)
            elif method.upper() == "POST":
                if signed:
                    # For signed POST, use the existing _make_signed_request logic
                    if data:
                        request_type = data.get("type", "request")
                        return self._make_signed_request(endpoint, data, request_type)
                    else:
                        raise ValueError("Data required for signed POST requests")
                else:
                    response = self.session.post(url, json=data, timeout=API_TIMEOUT)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

        except requests.exceptions.Timeout:
            logger.warning(f"{method} timeout for {endpoint}, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(
                f"{method} connection error for {endpoint}: {e}, will retry..."
            )
            raise

        if response.status_code == 401:
            raise ValueError("Authentication failed")
        elif response.status_code == 429:
            logger.warning(
                f"Rate limit hit (429) for {method} {endpoint}, backing off..."
            )
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(
                f"API error: {response.reason or response.text or 'Unknown error'}"
            )

        try:
            return response.json()
        except ValueError:
            # Handle non-JSON responses
            return {"data": response.text, "success": response.status_code < 400}

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RateLimitError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def get_balance(self) -> Dict[str, Any]:
        """
        Get account balance.

        Returns:
            Balance data as dict.
        """
        # Account endpoint uses GET with query parameters, not POST
        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": "get_account",
        }

        payload = {}
        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        params = {
            "account": self.account_public_key,
            "agent_wallet": self.agent_wallet_public_key,
            "signature": signature,
            "timestamp": signature_header["timestamp"],
            "expiry_window": signature_header["expiry_window"],
        }

        url = self.base_url + "/account"

        try:
            response = self.session.get(url, params=params, timeout=API_TIMEOUT)
        except requests.exceptions.Timeout:
            logger.warning("GET /account timeout, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GET /account connection error: {e}, will retry...")
            raise

        if response.status_code == 401:
            raise ValueError("Authentication failed")
        elif response.status_code == 429:
            logger.warning("Rate limit hit (429) for GET /account, backing off...")
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(f"API error: {response.text}")

        return response.json().get("data", {})

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RateLimitError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get trade history (orders).

        Args:
            limit: Maximum number of trades to return.

        Returns:
            List of trade data as dicts.
        """
        # Account orders endpoint uses GET with query parameters
        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": "get_orders",
        }

        payload = {"limit": limit}
        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        params = {
            "account": self.account_public_key,
            "agent_wallet": self.agent_wallet_public_key,
            "signature": signature,
            "timestamp": signature_header["timestamp"],
            "expiry_window": signature_header["expiry_window"],
            "limit": limit,
        }

        url = self.base_url + "/orders"

        try:
            response = self.session.get(url, params=params, timeout=API_TIMEOUT)
        except requests.exceptions.Timeout:
            logger.warning("GET /orders (trades) timeout, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GET /orders (trades) connection error: {e}, will retry...")
            raise

        if response.status_code == 401:
            raise ValueError("Authentication failed")
        elif response.status_code == 429:
            logger.warning(
                "Rate limit hit (429) for GET /orders (trades), backing off..."
            )
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(f"API error: {response.text}")

        response_data = response.json().get("data", [])
        # API may return list directly or dict with orders key
        if isinstance(response_data, list):
            return response_data
        return response_data.get("orders", [])

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RateLimitError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def get_orders(self) -> List[Dict[str, Any]]:
        """
        Get open orders.

        Returns:
            List of order data.
        """
        # Account orders endpoint uses GET with query parameters
        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": "get_orders",
        }

        payload = {}
        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        params = {
            "account": self.account_public_key,
            "agent_wallet": self.agent_wallet_public_key,
            "signature": signature,
            "timestamp": signature_header["timestamp"],
            "expiry_window": signature_header["expiry_window"],
        }

        url = self.base_url + "/orders"

        try:
            response = self.session.get(url, params=params, timeout=API_TIMEOUT)
        except requests.exceptions.Timeout:
            logger.warning("GET /orders timeout, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GET /orders connection error: {e}, will retry...")
            raise

        if response.status_code == 401:
            raise ValueError("Authentication failed")
        elif response.status_code == 429:
            logger.warning("Rate limit hit (429) for GET /orders, backing off...")
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(f"API error: {response.text}")

        response_data = response.json().get("data", [])
        # API may return list directly or dict with orders key
        if isinstance(response_data, list):
            return response_data
        return response_data.get("orders", [])

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                RateLimitError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get open positions.

        Returns:
            List of position data.
        """
        # Account positions endpoint uses GET with query parameters
        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": "get_positions",
        }

        payload = {}
        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        params = {
            "account": self.account_public_key,
            "agent_wallet": self.agent_wallet_public_key,
            "signature": signature,
            "timestamp": signature_header["timestamp"],
            "expiry_window": signature_header["expiry_window"],
        }

        url = self.base_url + "/positions"

        try:
            response = self.session.get(url, params=params, timeout=API_TIMEOUT)
        except requests.exceptions.Timeout:
            logger.warning("GET /positions timeout, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GET /positions connection error: {e}, will retry...")
            raise

        if response.status_code == 401:
            raise ValueError("Authentication failed")
        elif response.status_code == 429:
            logger.warning("Rate limit hit (429) for GET /positions, backing off...")
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(f"API error: {response.text}")

        response_data = response.json().get("data", [])
        # API may return list directly or dict with positions key
        if isinstance(response_data, list):
            return response_data
        return response_data.get("positions", [])

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Place a new order.

        Args:
            symbol: Trading symbol.
            side: 'buy' or 'sell'.
            quantity: Order quantity.
            order_type: Order type (e.g., 'limit', 'market').
            price: Order price (required for limit orders).

        Returns:
            Order data as dict.
        """
        payload = {
            "symbol": symbol,
            "amount": str(quantity),
            "side": "bid" if side == "buy" else "ask",
            "client_order_id": str(uuid.uuid4()),
            "reduce_only": False,  # Required field - set to True to only reduce existing position
        }

        if order_type == "limit":
            if price is None:
                raise ValueError("Price is required for limit orders")
            payload["price"] = str(price)
            payload["tif"] = "GTC"
            request_type = "create_order"
            endpoint = "/orders/create"
        elif order_type == "market":
            payload["slippage_percent"] = "0.5"  # Default slippage
            request_type = "create_market_order"
            endpoint = "/orders/create_market"
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        return self._make_signed_request(endpoint, payload, request_type)

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """
        Cancel an order.

        Args:
            symbol: Trading symbol (e.g., "BTC").
            order_id: ID of the order to cancel.

        Returns:
            Cancellation confirmation as dict.
        """
        # Convert order_id to int if numeric (API requires int for numeric IDs)
        parsed_order_id = int(order_id) if str(order_id).isdigit() else order_id
        payload = {
            "symbol": symbol,
            "order_id": parsed_order_id,
        }
        return self._make_signed_request("/orders/cancel", payload, "cancel_order")

    def cancel_all_orders(self, symbol: str = None) -> Dict[str, Any]:
        """
        Cancel all open orders, optionally for a specific symbol.

        Args:
            symbol: Trading symbol (e.g., "BTC"). If None, cancels all orders.

        Returns:
            Cancellation summary as dict.
        """
        payload = {
            "all_symbols": symbol is None,
            "exclude_reduce_only": False,
        }
        if symbol:
            payload["symbol"] = symbol.upper()
            payload["all_symbols"] = False
        return self._make_signed_request("/orders/cancel_all", payload, "cancel_all_orders")

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """
        Get market data (prices) for a symbol.

        Args:
            symbol: Trading symbol (without -PERP suffix).

        Returns:
            Market data as dict.
        """
        response = self._make_get_request("/prices", {"symbol": symbol})
        prices = response.get("data", {}).get("prices", [])
        return prices[0] if prices else {}

    def get_markets(self) -> List[Dict[str, Any]]:
        """
        Get list of available markets.

        Returns:
            List of market data.
        """
        response = self._make_get_request("/info")
        return response.get("data", [])

    def _safe_float_convert(self, value, default: float = 0.0) -> float:
        """Safely convert value to float with validation."""
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

    def _validate_candle_data(self, candle: Dict) -> bool:
        """Validate that a candle dict has required numeric data."""
        if not isinstance(candle, dict):
            return False

        # Check for at least one set of OHLCV keys
        has_abbreviated = any(k in candle for k in ["o", "h", "l", "c"])
        has_full = any(k in candle for k in ["open", "high", "low", "close"])

        if not (has_abbreviated or has_full):
            return False

        # Check that at least close price is valid
        close_val = candle.get("c") or candle.get("close")
        if close_val is None:
            return False
        try:
            close_float = float(close_val)
            if close_float <= 0 or close_float != close_float:  # NaN check
                return False
        except (TypeError, ValueError):
            return False

        return True

    def get_candles(
        self,
        market: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Get historical candle/OHLCV data for a market with robust validation.

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP" - -PERP suffix will be stripped)
            interval: Candle interval (e.g., "1m", "5m", "15m", "1h", "4h", "1d")
            start_time: Start timestamp in milliseconds (required by Pacifica API)
            end_time: End timestamp in milliseconds (optional)
            limit: Maximum number of candles to return (default: 200)

        Returns:
            List of candle data dicts with keys: timestamp, open, high, low, close, volume

        Note:
            Pacifica uses /kline endpoint with symbol WITHOUT -PERP suffix.
            Invalid candles are filtered out. Returns empty list on complete failure.
        """
        # Strip -PERP suffix if present - Pacifica API expects plain symbol
        import re

        clean_symbol = re.sub(r"-perp$", "", market, flags=re.IGNORECASE).upper()

        params = {
            "symbol": clean_symbol,
            "interval": interval,
        }

        # start_time is required by Pacifica API
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time

        try:
            response = self._make_get_request("/kline", params)
        except ValueError as e:
            logger.error(f"Failed to fetch candles for {clean_symbol}: {e}")
            return []

        # Validate response structure
        if response is None:
            logger.error(f"Null response for {clean_symbol} candles")
            return []

        # Parse response - Pacifica API may return data in "data" field or directly
        if isinstance(response, dict):
            candles = response.get("data", [])
            # Handle nested data structure
            if isinstance(candles, dict):
                candles = candles.get("candles", candles.get("klines", []))
        elif isinstance(response, list):
            candles = response
        else:
            logger.error(
                f"Unexpected response type for {clean_symbol} candles: {type(response)}"
            )
            return []

        # Handle case where candles might be in different structures
        if not candles:
            logger.warning(f"No candle data in response for {clean_symbol}")
            return []

        if not isinstance(candles, list):
            logger.error(
                f"Candles data is not a list for {clean_symbol}: {type(candles)}"
            )
            return []

        # Debug: Log first candle to see format
        if candles:
            logger.debug(f"First candle raw: {candles[0]}")

        # Standardize candle format with validation
        # Full keys: open, high, low, close, volume
        # Abbreviated keys: o, h, l, c, v (used by WebSocket and possibly REST)
        standardized_candles = []
        invalid_count = 0

        for candle in candles:
            # Skip invalid candles
            if not self._validate_candle_data(candle):
                invalid_count += 1
                continue

            try:
                standardized_candles.append(
                    {
                        "timestamp": candle.get("t")
                        or candle.get("timestamp")
                        or candle.get("time"),
                        "open": self._safe_float_convert(
                            candle.get("o") or candle.get("open")
                        ),
                        "high": self._safe_float_convert(
                            candle.get("h") or candle.get("high")
                        ),
                        "low": self._safe_float_convert(
                            candle.get("l") or candle.get("low")
                        ),
                        "close": self._safe_float_convert(
                            candle.get("c") or candle.get("close")
                        ),
                        "volume": self._safe_float_convert(
                            candle.get("v") or candle.get("volume")
                        ),
                    }
                )
            except Exception as e:
                logger.warning(f"Error parsing candle: {e}")
                invalid_count += 1
                continue

        if invalid_count > 0:
            logger.warning(
                f"Skipped {invalid_count}/{len(candles)} invalid candles for {clean_symbol}"
            )

        if not standardized_candles:
            logger.error(f"No valid candles after parsing for {clean_symbol}")

        return standardized_candles

    def get_funding_history(
        self,
        symbol: str,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Get funding rate history for a symbol.

        Pacifica has HOURLY funding (24x/day) vs standard 8-hour funding.

        Args:
            symbol: Trading symbol (e.g., "BTC" or "BTC-PERP")
            limit: Number of historical funding periods to return (default: 8)

        Returns:
            List of funding rate records with keys: timestamp, funding_rate, next_funding
        """
        import re

        # Strip -PERP suffix if present
        clean_symbol = re.sub(r"-perp$", "", symbol, flags=re.IGNORECASE).upper()

        try:
            # Try dedicated funding endpoint first
            params = {"symbol": clean_symbol, "limit": limit}
            response = self._make_get_request("/funding_history", params)

            if response and "data" in response:
                funding_data = response.get("data", [])
                if isinstance(funding_data, list) and funding_data:
                    return funding_data

        except Exception as e:
            logger.debug(f"Funding history endpoint not available for {clean_symbol}: {e}")

        # Fallback: Extract from market data / prices endpoint
        try:
            market_data = self.get_market_data(clean_symbol)
            if market_data:
                current_rate = market_data.get("funding_rate", 0)
                next_funding = market_data.get("next_funding_time")

                # Return single entry with current rate
                return [{
                    "funding_rate": float(current_rate) if current_rate else 0,
                    "timestamp": int(time.time() * 1000),
                    "next_funding": next_funding,
                }]
        except Exception as e:
            logger.debug(f"Failed to get funding from market data for {clean_symbol}: {e}")

        return []

    def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get current funding rate for a symbol.

        Args:
            symbol: Trading symbol (e.g., "BTC" or "BTC-PERP")

        Returns:
            Dict with funding_rate, next_funding_time, or None if unavailable
        """
        import re

        clean_symbol = re.sub(r"-perp$", "", symbol, flags=re.IGNORECASE).upper()

        try:
            market_data = self.get_market_data(clean_symbol)
            if market_data:
                return {
                    "funding_rate": float(market_data.get("funding_rate", 0)) if market_data.get("funding_rate") else 0,
                    "next_funding_time": market_data.get("next_funding_time"),
                    "symbol": clean_symbol,
                }
        except Exception as e:
            logger.debug(f"Failed to get funding rate for {clean_symbol}: {e}")

        return None
