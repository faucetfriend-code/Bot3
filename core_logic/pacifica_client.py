"""
Pacifica.fi REST API Client

Complete REST API client for Pacifica.fi perpetual futures exchange.
Handles authentication, request signing, rate limiting, and all API endpoints.

⚠️ CRITICAL: Pacifica uses HOURLY funding rates (24x per day)

Authentication method updated to match official Pacifica SDK:
- Uses sorted JSON message format
- Base58 signature encoding
- Proper header/payload structure
"""

import time
import json
import base58
import requests
import os
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from enum import Enum
from solders.keypair import Keypair
from loguru import logger


class PacificaEnvironment(Enum):
    """Pacifica API environments."""
    TESTNET = "https://test-api.pacifica.fi/api/v1"
    MAINNET = "https://api.pacifica.fi/api/v1"


class RateLimitTier(Enum):
    """Rate limit tiers based on trading volume."""
    BASIC = {"rest_per_second": 10, "websocket_subscriptions": 50}
    ADVANCED = {"rest_per_second": 20, "websocket_subscriptions": 100}
    VIP = {"rest_per_second": 50, "websocket_subscriptions": 200}


class PacificaAPIError(Exception):
    """Base exception for Pacifica API errors."""
    def __init__(self, message: str, error_code: Optional[int] = None):
        self.error_code = error_code
        super().__init__(message)


class AuthenticationError(PacificaAPIError):
    """Authentication failed (1000-1099)."""
    pass


class OrderError(PacificaAPIError):
    """Order-related error (2000-2099)."""
    pass


class PositionError(PacificaAPIError):
    """Position-related error (3000-3099)."""
    pass


class RiskError(PacificaAPIError):
    """Risk management error (4000-4099)."""
    pass


class SystemError(PacificaAPIError):
    """System error (9000-9099)."""
    pass


class RateLimiter:
    """
    Rate limiter for API requests.

    Implements token bucket algorithm to respect Pacifica rate limits.
    """

    def __init__(self, tier: RateLimitTier = RateLimitTier.BASIC):
        """
        Initialize rate limiter.

        Args:
            tier: Rate limit tier (Basic/Advanced/VIP)
        """
        self.tier = tier
        self.rate = tier.value["rest_per_second"]
        self.tokens = self.rate
        self.last_update = time.time()
        self.lock = False

    def acquire(self, wait: bool = True) -> bool:
        """
        Acquire a token for making a request.

        Args:
            wait: Whether to wait for token availability

        Returns:
            True if token acquired, False otherwise
        """
        now = time.time()
        elapsed = now - self.last_update

        # Refill tokens based on elapsed time
        self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
        self.last_update = now

        if self.tokens >= 1:
            self.tokens -= 1
            return True
        elif wait:
            # Wait for next token
            wait_time = (1 - self.tokens) / self.rate
            time.sleep(wait_time)
            self.tokens = 0
            self.last_update = time.time()
            return True
        else:
            return False


class PacificaClient:
    """
    Complete REST API client for Pacifica.fi.

    Handles all REST API endpoints with proper authentication,
    rate limiting, and error handling.

    ⚠️ CRITICAL: All funding rates are HOURLY (applied 24 times per day)
    """

    def __init__(
        self,
        agent_wallet_private_key: str,
        account_public_key: str,
        environment: PacificaEnvironment = PacificaEnvironment.TESTNET,
        rate_limit_tier: RateLimitTier = RateLimitTier.BASIC,
    ):
        """
        Initialize Pacifica API client.

        Args:
            agent_wallet_private_key: Solana private key for agent wallet (base58)
            account_public_key: Main account public key
            environment: Testnet or mainnet
            rate_limit_tier: Rate limit tier (Basic/Advanced/VIP)
        """
        # Authentication
        self.agent_wallet_private_key = agent_wallet_private_key
        self.account_public_key = account_public_key
        self.agent_wallet_keypair = Keypair.from_base58_string(agent_wallet_private_key)
        self.agent_wallet_public_key = str(self.agent_wallet_keypair.pubkey())

        # API configuration
        self.base_url = environment.value
        self.environment = environment

        # Rate limiting
        self.rate_limiter = RateLimiter(rate_limit_tier)

        # Session for connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "PacificaClient/1.0"
        })

        # Configure SSL verification to handle certificate issues
        # In production, this should use proper CA certificates
        import ssl
        try:
            # Try to create a custom SSL context that handles certificate issues
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            self.session.verify = False  # Disable SSL verification for now
        except Exception as e:
            logger.warning(f"SSL context setup failed: {e} - using default")
            self.session.verify = False  # Fallback to disable verification

        logger.info(f"Initialized Pacifica client ({environment.name})")
        logger.info(f"Account: {account_public_key}")
        logger.info(f"Agent Wallet: {self.agent_wallet_public_key}")
        logger.info(f"Rate limit: {rate_limit_tier.value['rest_per_second']} req/s")

    def _sort_json_keys(self, value: Any) -> Any:
        """
        Recursively sort JSON keys alphabetically.

        This is REQUIRED for Pacifica signature validation.

        Args:
            value: Value to sort (dict, list, or primitive)

        Returns:
            Sorted value
        """
        if isinstance(value, dict):
            sorted_dict = {}
            for key in sorted(value.keys()):
                sorted_dict[key] = self._sort_json_keys(value[key])
            return sorted_dict
        elif isinstance(value, list):
            return [self._sort_json_keys(item) for item in value]
        else:
            return value

    def _prepare_message(self, header: Dict[str, Any], payload: Dict[str, Any]) -> str:
        """
        Prepare message for signing according to official Pacifica SDK format.

        Args:
            header: Signature header with type, timestamp, expiry_window
            payload: Request payload data

        Returns:
            Compact JSON string with sorted keys
        """
        if "type" not in header or "timestamp" not in header or "expiry_window" not in header:
            raise ValueError("Header must have type, timestamp, and expiry_window")

        data = {
            **header,
            "data": payload,
        }

        # Sort keys recursively (CRITICAL for signature validation)
        message = self._sort_json_keys(data)

        # Compact JSON format (no spaces after separators)
        return json.dumps(message, separators=(",", ":"))

    def _sign_message(self, message: str) -> str:
        """
        Sign a message using agent wallet keypair.

        Args:
            message: Message to sign (prepared JSON string)

        Returns:
            Base58-encoded signature (official Pacifica format)
        """
        message_bytes = message.encode("utf-8")
        signature = self.agent_wallet_keypair.sign_message(message_bytes)
        # Base58 encode (NOT base64 - this is the official format)
        return base58.b58encode(bytes(signature)).decode("ascii")

    def _create_signature(
        self,
        request_type: str,
        payload: Dict[str, Any],
        timestamp: int,
        expiry_window: int = 5000
    ) -> Tuple[str, str]:
        """
        Create signature for API request using official Pacifica SDK format.

        Args:
            request_type: Type of request (e.g., "create_market_order")
            payload: Request payload
            timestamp: Current timestamp in milliseconds
            expiry_window: Signature expiry window in milliseconds

        Returns:
            (message, signature) tuple
        """
        # Create header according to official SDK
        header = {
            "type": request_type,
            "timestamp": timestamp,
            "expiry_window": expiry_window,
        }

        # Prepare message (sorted JSON)
        message = self._prepare_message(header, payload)

        # Sign message
        signature = self._sign_message(message)

        return message, signature

    def _make_request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
        request_type: str = "GET",
        authenticated: bool = True,
    ) -> Dict[str, Any]:
        """
        Make API request with authentication and rate limiting.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint (e.g., "/orders/place")
            payload: Request payload
            request_type: Signature request type
            authenticated: Whether request requires authentication

        Returns:
            Response data

        Raises:
            PacificaAPIError: If request fails
        """
        # Rate limiting
        self.rate_limiter.acquire(wait=True)

        # Construct URL
        url = f"{self.base_url}{endpoint}"

        # Prepare request
        if payload is None:
            payload = {}

        # Add authentication if required
        if authenticated:
            timestamp = int(time.time() * 1000)
            expiry_window = 5000  # 5 seconds

            message, signature = self._create_signature(
                request_type, payload, timestamp, expiry_window
            )

            # Add auth headers to payload
            # NOTE: Only include agent_wallet if it's different from account
            # (i.e., when a separate agent wallet is signing on behalf of account)
            auth_payload = {
                "account": self.account_public_key,
                "signature": signature,
                "timestamp": timestamp,
                "expiry_window": expiry_window,
                **payload
            }

            # Only add agent_wallet if it's different from account
            # (when account signs for itself, no agent_wallet needed)
            if self.agent_wallet_public_key != self.account_public_key:
                auth_payload["agent_wallet"] = self.agent_wallet_public_key
        else:
            auth_payload = payload

        # Make request with retry logic for rate limiting
        max_retries = 5
        base_delay = 1.0
        response = None

        for attempt in range(max_retries):
            try:
                if method == "GET":
                    response = self.session.get(url, params=auth_payload)
                elif method == "POST":
                    response = self.session.post(url, json=auth_payload)
                elif method == "DELETE":
                    response = self.session.delete(url, json=auth_payload)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Debug logging
                logger.debug(f"API Request: {method} {url}")
                logger.debug(f"Response Status: {response.status_code}")

                # Handle rate limiting (429)
                if response.status_code == 429:
                    if attempt == max_retries - 1:
                        logger.error(f"Rate limit exceeded after {max_retries} attempts")
                        raise PacificaAPIError("Rate limit exceeded", 429)

                    # Exponential backoff: base_delay * (2 ^ attempt)
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limit hit (attempt {attempt + 1}/{max_retries}), retrying in {delay:.1f}s")
                    time.sleep(delay)
                    continue

                # Safe JSON parsing with better error handling
                try:
                    if response.content and response.headers.get('content-type', '').startswith('application/json'):
                        response_data = response.json()
                    else:
                        # Handle non-JSON responses (HTML error pages, empty content)
                        response_data = {
                            "success": False,
                            "error": f"Non-JSON response: {response.status_code}",
                            "status_code": response.status_code,
                            "content_type": response.headers.get('content-type')
                        }
                except (ValueError, json.JSONDecodeError) as e:
                    response_data = {
                        "success": False,
                        "error": f"JSON parse error: {str(e)}",
                        "status_code": response.status_code
                    }

                # Handle API errors
                if not response.ok or not response_data.get("success", False):
                    error_code = response_data.get("error_code") or response.status_code
                    error_message = response_data.get("error") or response_data.get("message") or f"HTTP {response.status_code}"

                    # Log full response for debugging
                    logger.error(f"API error {error_code}: {error_message}")
                    logger.debug(f"Full response: status={response.status_code}, body={response_data}")

                    # For rate limiting, use a generic error
                    if response.status_code == 429:
                        raise PacificaAPIError("Rate limit exceeded", 429)

                    # Raise specific exception based on error code
                    if error_code and isinstance(error_code, int):
                        if 1000 <= error_code < 2000:
                            raise AuthenticationError(error_message, error_code)
                        elif 2000 <= error_code < 3000:
                            raise OrderError(error_message, error_code)
                        elif 3000 <= error_code < 4000:
                            raise PositionError(error_message, error_code)
                        elif 4000 <= error_code < 5000:
                            raise RiskError(error_message, error_code)
                        elif error_code >= 9000:
                            raise SystemError(error_message, error_code)

                    raise PacificaAPIError(error_message, error_code)

                # Success - return data
                return response_data

            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    logger.error(f"Request failed after {max_retries} attempts: {e}")
                    raise PacificaAPIError(f"Request failed: {str(e)}")
                else:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}), retrying in {delay:.1f}s: {e}")
                    time.sleep(delay)
                    continue

    # ===========================
    # ACCOUNT ENDPOINTS
    # ===========================

    def get_account_info(self) -> Dict[str, Any]:
        """
        Get account information.

        Returns:
            Account info including balance, margin, positions
        """
        response = self._make_request(
            "GET",
            "/account",
            request_type="get_account"
        )
        return response.get("data", {})

    def get_balance(self, currency: str = "USDC", subaccount_id: Optional[str] = None) -> Dict[str, float]:
        """
        Get account balance from account info endpoint.

        Args:
            currency: Currency to query (default: USDC)
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            Balance information (total, available, locked)
        """
        # Balance is part of account info in Pacifica API
        account_info = self.get_account_info()

        # Map Pacifica field names to our expected format (values are strings)
        return {
            "total_balance": float(account_info.get("balance", 0)),
            "available_balance": float(account_info.get("available_to_spend", 0)),
            "used_margin": float(account_info.get("total_margin_used", 0)),
            "unrealized_pnl": float(account_info.get("account_equity", 0)) - float(account_info.get("balance", 0)),
            "total_equity": float(account_info.get("account_equity", 0))
        }

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get ticker data for a symbol.

        Args:
            symbol: Trading symbol (e.g., "BTC-PERP")

        Returns:
            Ticker data dictionary with price information
        """
        response = self._make_request(
            "GET",
            f"/ticker/{symbol}",
            request_type="get_ticker"
        )
        return response.get("data", {})

    def get_positions(self, market: Optional[str] = None, subaccount_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get open positions.

        Args:
            market: Specific market to query (None for all)
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            List of positions with funding and market data
        """
        # Build query parameters - account is handled via authentication, not query params
        query_params = ""
        if market:
            query_params = f"market={market}"

        endpoint = f"/positions{ '?' + query_params if query_params else ''}"

        response = self._make_request(
            "GET",
            endpoint,
            request_type="get_positions"
        )

        # Extract positions from response
        positions_data = response.get("data", [])

        # If positions_data is a dict with "positions" key, extract it
        if isinstance(positions_data, dict) and "positions" in positions_data:
            return positions_data["positions"]
        # If it's already a list, return it
        elif isinstance(positions_data, list):
            return positions_data

        return []

    def get_subaccounts(self) -> Dict[str, Any]:
        """
        Get all subaccounts for the main account.

        Returns:
            Dictionary containing subaccounts list with details:
            - subaccount_id: Internal Pacifica ID (e.g., "sub_12345")
            - name: Human-readable name
            - public_key: Solana public key (for reference)
            - balance: USDC balance
            - equity: Total equity
            - created_at: Creation timestamp
        """
        response = self._make_request(
            "GET",
            "/account/subaccounts",
            request_type="get_subaccounts"
        )
        return response.get("data", {})

    def transfer_funds(
        self,
        to_account: str,
        amount: float,
        from_account: Optional[str] = None,
        currency: str = "USDC"
    ) -> Dict[str, Any]:
        """
        Transfer funds between subaccounts.

        Per official Pacifica SDK, the source account is determined by the signing keypair.
        The `from_account` parameter is only used for context/logging.

        Args:
            to_account: Destination subaccount public key
            amount: Amount to transfer (will be converted to string per SDK format)
            from_account: Source account (for reference, actual source is signing account)
            currency: Currency to transfer (default: USDC)

        Returns:
            Transfer confirmation details
        """
        # SDK format: only to_account and amount in payload
        # Source account is the signing keypair (self.account_public_key)
        payload = {
            "to_account": to_account,
            "amount": str(amount),  # SDK requires string format
        }

        response = self._make_request(
            "POST",
            "/account/subaccount/transfer",  # Correct SDK endpoint per python-sdk
            payload=payload,
            request_type="transfer_funds"
        )
        return response

    def get_margin_mode(self) -> str:
        """
        Get current margin mode.

        Returns:
            "cross" or "isolated"
        """
        response = self._make_request(
            "GET",
            "/account/margin-mode",
            request_type="get_margin_mode"
        )
        return response.get("data", {}).get("margin_mode", "cross")

    def set_margin_mode(self, margin_mode: str, subaccount_id: Optional[str] = None) -> bool:
        """
        Set margin mode.

        ⚠️ CRITICAL: Cannot switch with open positions!

        Args:
            margin_mode: "cross" or "isolated"
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            True if successful

        Raises:
            PositionError: If positions are open
        """
        if margin_mode not in ["cross", "isolated"]:
            raise ValueError(f"Invalid margin mode: {margin_mode}")

        payload = {"margin_mode": margin_mode}
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "POST",
            "/account/margin-mode",
            payload=payload,
            request_type="set_margin_mode"
        )
        return response.get("success", False)

    def get_leverage(self, market: str, subaccount_id: Optional[str] = None) -> int:
        """
        Get current leverage for a market.

        Args:
            market: Market symbol
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            Leverage (5-50)
        """
        payload = {"market": market}
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "GET",
            "/account/leverage",
            payload=payload,
            request_type="get_leverage"
        )
        return response.get("data", {}).get("leverage", 10)

    def set_leverage(self, market: str, leverage: int, subaccount_id: Optional[str] = None) -> bool:
        """
        Set leverage for a market.

        Args:
            market: Market symbol
            leverage: Leverage (5-50 for Pacifica)
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            True if successful
        """
        if not 5 <= leverage <= 50:
            raise ValueError(f"Leverage must be between 5 and 50, got {leverage}")

        payload = {"market": market, "leverage": leverage}
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "POST",
            "/account/leverage",
            payload=payload,
            request_type="set_leverage"
        )
        return response.get("success", False)

    # ===========================
    # ORDER ENDPOINTS
    # ===========================

    def place_order(
        self,
        market: str,
        side: str,
        order_type: str,
        size: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        leverage: Optional[int] = None,
        margin_mode: Optional[str] = None,
        reduce_only: bool = False,
        post_only: bool = False,
        time_in_force: str = "GTC",
        slippage_percent: float = 0.5,
        subaccount_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place an order.

        Args:
            market: Market symbol (e.g., "BTC-PERP" or "BTC")
            side: "buy" or "sell" (converted to "bid"/"ask" for API)
            order_type: "market", "limit", "stop_market", "stop_limit"
            size: Order size
            price: Limit price (for limit orders)
            stop_price: Stop trigger price (for stop orders)
            leverage: Leverage (5-50)
            margin_mode: "cross" or "isolated"
            reduce_only: Only reduce existing position
            post_only: Only provide liquidity (maker)
            time_in_force: "GTC", "IOC", or "FOK"
            slippage_percent: Slippage for market orders (default: 0.5%)
            subaccount_id: Specific subaccount ID (None for main account)
            client_order_id: Custom order ID (generated if not provided)

        Returns:
            Order information
        """
        import uuid

        # Convert market symbol (strip -PERP suffix if present)
        symbol = market.replace("-PERP", "").upper()

        # Convert side to Pacifica format
        pacifica_side = "bid" if side.lower() == "buy" else "ask"

        # Generate client_order_id if not provided
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())

        # Determine endpoint and request type based on order type
        if order_type.lower() == "market":
            endpoint = "/orders/create_market"
            request_type = "create_market_order"
            payload = {
                "symbol": symbol,
                "side": pacifica_side,
                "amount": str(size),
                "reduce_only": reduce_only,
                "slippage_percent": str(slippage_percent),
                "client_order_id": client_order_id,
            }
        else:
            # Limit order
            endpoint = "/orders/create"
            request_type = "create_order"
            payload = {
                "symbol": symbol,
                "side": pacifica_side,
                "amount": str(size),
                "price": str(price) if price is not None else "0",
                "reduce_only": reduce_only,
                "tif": time_in_force,
                "client_order_id": client_order_id,
            }

        if subaccount_id is not None:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "POST",
            endpoint,
            payload=payload,
            request_type=request_type
        )
        return response.get("data", {})

    def cancel_order(self, order_id: str, market: Optional[str] = None, subaccount_id: Optional[str] = None) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel (numeric ID or client_order_id)
            market: Market symbol (required for Pacifica API)
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            True if cancelled successfully
        """
        # Convert market symbol (strip -PERP suffix if present)
        symbol = market.replace("-PERP", "").upper() if market else "BTC"

        payload = {
            "symbol": symbol,
            "order_id": int(order_id) if order_id.isdigit() else order_id,
        }
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        # SDK uses POST for cancel, not DELETE
        response = self._make_request(
            "POST",
            "/orders/cancel",
            payload=payload,
            request_type="cancel_order"
        )
        return response.get("success", False)

    def cancel_all_orders(self, market: Optional[str] = None, subaccount_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel all open orders.

        Args:
            market: Specific market (None for all markets)
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            Cancellation summary
        """
        payload = {
            "all_symbols": market is None,
            "exclude_reduce_only": False,
        }
        if market:
            symbol = market.replace("-PERP", "").upper()
            payload["symbol"] = symbol
            payload["all_symbols"] = False
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        # SDK uses POST for cancel_all, not DELETE
        response = self._make_request(
            "POST",
            "/orders/cancel_all",
            payload=payload,
            request_type="cancel_all_orders"
        )
        return response.get("data", {})

    # ============================================
    # TWAP ORDER METHODS (Time-Weighted Average Price)
    # ============================================

    def create_twap_order(
        self,
        symbol: str,
        side: str,
        amount: str,
        duration_in_seconds: int,
        slippage_percent: str = "0.5",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
        subaccount_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a TWAP (Time-Weighted Average Price) order.

        TWAP orders execute large positions over time to minimize market impact.

        Args:
            symbol: Trading pair (e.g., "BTC")
            side: "bid" (buy) or "ask" (sell)
            amount: Total amount to execute
            duration_in_seconds: Time to spread execution over
            slippage_percent: Max slippage per sub-order
            reduce_only: Only reduce existing position
            client_order_id: Optional client order ID
            subaccount_id: Optional subaccount ID

        Returns:
            TWAP order details including order_id
        """
        import uuid as uuid_module

        payload = {
            "symbol": symbol.replace("-PERP", "").upper(),
            "side": side,
            "amount": str(amount),
            "duration_in_seconds": duration_in_seconds,
            "slippage_percent": str(slippage_percent),
            "reduce_only": reduce_only,
            "client_order_id": client_order_id or str(uuid_module.uuid4()),
        }

        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "POST",
            "/orders/twap/create",
            payload=payload,
            request_type="create_twap_order"
        )
        return response.get("data", {})

    def cancel_twap_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
        subaccount_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel a TWAP order.

        Args:
            symbol: Trading pair (e.g., "BTC")
            order_id: TWAP order ID (either this or client_order_id required)
            client_order_id: Client order ID (either this or order_id required)
            subaccount_id: Optional subaccount ID

        Returns:
            Cancellation result
        """
        payload = {
            "symbol": symbol.replace("-PERP", "").upper(),
        }

        if order_id is not None:
            payload["order_id"] = order_id
        elif client_order_id:
            payload["client_order_id"] = client_order_id
        else:
            raise ValueError("Either order_id or client_order_id must be provided")

        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "POST",
            "/orders/twap/cancel",
            payload=payload,
            request_type="cancel_twap_order"
        )
        return response.get("data", {})

    def get_open_twap_orders(self, subaccount_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open TWAP orders for the account.

        Args:
            subaccount_id: Optional subaccount ID (None for main account)

        Returns:
            List of open TWAP orders
        """
        account = subaccount_id or self.account_public_key

        # This is a public endpoint - no auth needed
        response = self._make_request(
            "GET",
            f"/orders/twap?account={account}",
            authenticated=False,
            request_type="get_open_twap_orders"
        )
        return response.get("data", [])

    def get_twap_order_history(self, subaccount_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get TWAP order history for the account.

        Args:
            subaccount_id: Optional subaccount ID (None for main account)

        Returns:
            List of historical TWAP orders
        """
        account = subaccount_id or self.account_public_key

        response = self._make_request(
            "GET",
            f"/orders/twap/history?account={account}",
            authenticated=False,
            request_type="get_twap_order_history"
        )
        return response.get("data", [])

    def get_twap_order_by_id(self, order_id: int) -> Dict[str, Any]:
        """
        Get detailed TWAP order history by order ID.

        Args:
            order_id: TWAP order ID

        Returns:
            Detailed TWAP order history including sub-orders
        """
        response = self._make_request(
            "GET",
            f"/orders/twap/history_by_id?order_id={order_id}",
            authenticated=False,
            request_type="get_twap_order_by_id"
        )
        return response.get("data", {})

    # ============================================
    # POSITION TP/SL (Take-Profit / Stop-Loss)
    # ============================================

    def set_position_tpsl(
        self,
        symbol: str,
        side: str,
        take_profit: Optional[Dict[str, Any]] = None,
        stop_loss: Optional[Dict[str, Any]] = None,
        subaccount_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Set Take-Profit and/or Stop-Loss for an existing position.

        Args:
            symbol: Trading pair (e.g., "BTC")
            side: "ask" for long position exit, "bid" for short position exit
            take_profit: TP config dict with:
                - stop_price: Trigger price (required)
                - limit_price: Order price (optional, market if omitted)
                - amount: Amount to close (optional, full position if omitted)
                - client_order_id: Optional client ID
            stop_loss: SL config dict with same structure as take_profit
            subaccount_id: Optional subaccount ID

        Returns:
            TP/SL order confirmation

        Example:
            client.set_position_tpsl(
                symbol="BTC",
                side="ask",  # Close long position
                take_profit={"stop_price": "120000", "limit_price": "120300"},
                stop_loss={"stop_price": "95000"}  # Market order at trigger
            )
        """
        payload = {
            "symbol": symbol.replace("-PERP", "").upper(),
            "side": side,
        }

        if take_profit:
            payload["take_profit"] = take_profit
        if stop_loss:
            payload["stop_loss"] = stop_loss
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        if not take_profit and not stop_loss:
            raise ValueError("At least one of take_profit or stop_loss must be provided")

        response = self._make_request(
            "POST",
            "/positions/tpsl",
            payload=payload,
            request_type="set_position_tpsl"
        )
        return response.get("data", {})

    # ============================================
    # BATCH ORDER OPERATIONS
    # ============================================

    def batch_orders(
        self,
        actions: List[Dict[str, Any]],
        subaccount_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute multiple order operations atomically.

        Args:
            actions: List of action dicts, each with:
                - type: "Create" or "Cancel"
                - data: Order payload (signed)
            subaccount_id: Optional subaccount ID

        Returns:
            Batch operation results

        Example:
            client.batch_orders([
                {"type": "Create", "data": {...}},
                {"type": "Cancel", "data": {...}}
            ])
        """
        payload = {"actions": actions}
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "POST",
            "/orders/batch",
            payload=payload,
            request_type="batch_orders"
        )
        return response.get("data", {})

    # ============================================
    # AGENT WALLET MANAGEMENT
    # ============================================

    def bind_agent_wallet(self, agent_wallet_address: str) -> Dict[str, Any]:
        """
        Bind an agent wallet to the main account for delegated trading.

        Args:
            agent_wallet_address: Public key of the agent wallet to bind

        Returns:
            Binding confirmation
        """
        payload = {"agent_wallet": agent_wallet_address}

        response = self._make_request(
            "POST",
            "/agent/bind",
            payload=payload,
            request_type="bind_agent_wallet"
        )
        return response.get("data", {})

    def list_agent_wallets(self) -> List[Dict[str, Any]]:
        """
        List all agent wallets bound to the main account.

        Returns:
            List of agent wallet details
        """
        response = self._make_request(
            "POST",
            "/agent/list",
            payload={},
            request_type="list_agent_wallets"
        )
        return response.get("data", [])

    def revoke_agent_wallet(self, agent_wallet_address: str) -> Dict[str, Any]:
        """
        Revoke a specific agent wallet.

        Args:
            agent_wallet_address: Public key of the agent wallet to revoke

        Returns:
            Revocation confirmation
        """
        payload = {"agent_wallet": agent_wallet_address}

        response = self._make_request(
            "POST",
            "/agent/revoke",
            payload=payload,
            request_type="revoke_agent_wallet"
        )
        return response.get("data", {})

    def revoke_all_agent_wallets(self) -> Dict[str, Any]:
        """
        Revoke ALL agent wallets (emergency use).

        Returns:
            Revocation confirmation
        """
        response = self._make_request(
            "POST",
            "/agent/revoke_all",
            payload={},
            request_type="revoke_all_agent_wallets"
        )
        return response.get("data", {})

    # ============================================
    # IP WHITELIST SECURITY
    # ============================================

    def list_ip_whitelist(self, agent_wallet_address: str) -> List[str]:
        """
        List IP whitelist for an agent wallet.

        Args:
            agent_wallet_address: Agent wallet public key

        Returns:
            List of whitelisted IP addresses
        """
        payload = {"api_agent_key": agent_wallet_address}

        response = self._make_request(
            "POST",
            "/agent/ip_whitelist/list",
            payload=payload,
            request_type="list_agent_ip_whitelist"
        )
        return response.get("data", [])

    def add_ip_to_whitelist(self, agent_wallet_address: str, ip_address: str) -> Dict[str, Any]:
        """
        Add an IP address to agent wallet whitelist.

        Args:
            agent_wallet_address: Agent wallet public key
            ip_address: IP address to whitelist (e.g., "192.168.1.1" or "203.0.113.0/24")

        Returns:
            Operation confirmation
        """
        payload = {
            "agent_wallet": agent_wallet_address,
            "ip_address": ip_address
        }

        response = self._make_request(
            "POST",
            "/agent/ip_whitelist/add",
            payload=payload,
            request_type="add_agent_whitelisted_ip"
        )
        return response.get("data", {})

    def remove_ip_from_whitelist(self, agent_wallet_address: str, ip_address: str) -> Dict[str, Any]:
        """
        Remove an IP address from agent wallet whitelist.

        Args:
            agent_wallet_address: Agent wallet public key
            ip_address: IP address to remove

        Returns:
            Operation confirmation
        """
        payload = {
            "agent_wallet": agent_wallet_address,
            "ip_address": ip_address
        }

        response = self._make_request(
            "POST",
            "/agent/ip_whitelist/remove",
            payload=payload,
            request_type="remove_agent_whitelisted_ip"
        )
        return response.get("data", {})

    def toggle_ip_whitelist(self, agent_wallet_address: str, enabled: bool) -> Dict[str, Any]:
        """
        Enable or disable IP whitelist enforcement for an agent wallet.

        Args:
            agent_wallet_address: Agent wallet public key
            enabled: True to enable, False to disable

        Returns:
            Operation confirmation
        """
        payload = {
            "agent_wallet": agent_wallet_address,
            "enabled": enabled
        }

        response = self._make_request(
            "POST",
            "/agent/ip_whitelist/toggle",
            payload=payload,
            request_type="set_agent_ip_whitelist_enabled"
        )
        return response.get("data", {})

    # ============================================
    # API CONFIG KEYS
    # ============================================

    def create_api_key(self) -> Dict[str, Any]:
        """
        Create a new API config key for external integrations.

        Returns:
            New API key with rate limit tier information
        """
        response = self._make_request(
            "POST",
            "/account/api_keys/create",
            payload={},
            request_type="create_api_key"
        )
        return response.get("data", {})

    def revoke_api_key(self, api_key: str) -> Dict[str, Any]:
        """
        Revoke an API config key.

        Args:
            api_key: The API key to revoke

        Returns:
            Revocation confirmation
        """
        payload = {"api_key": api_key}

        response = self._make_request(
            "POST",
            "/account/api_keys/revoke",
            payload=payload,
            request_type="revoke_api_key"
        )
        return response.get("data", {})

    def list_api_keys(self) -> List[Dict[str, Any]]:
        """
        List all API config keys for the account.

        Returns:
            List of API keys with metadata
        """
        response = self._make_request(
            "POST",
            "/account/api_keys",
            payload={},
            request_type="list_api_keys"
        )
        return response.get("data", [])

    # ============================================
    # SUBACCOUNT CREATION
    # ============================================

    def create_subaccount(
        self,
        subaccount_keypair: Keypair
    ) -> Dict[str, Any]:
        """
        Create a new subaccount using cross-signature authentication.

        The main account and subaccount must both consent to the relationship.
        This method handles the dual-signature flow automatically.

        Args:
            subaccount_keypair: Keypair for the new subaccount

        Returns:
            Subaccount creation confirmation
        """
        timestamp = int(time.time() * 1000)
        expiry_window = 5000

        main_public_key = self.account_public_key
        sub_public_key = str(subaccount_keypair.pubkey())

        # Step 1: Subaccount signs main account's public key
        sub_signature_header = {
            "timestamp": timestamp,
            "expiry_window": expiry_window,
            "type": "subaccount_initiate",
        }
        sub_payload = {"account": main_public_key}
        sub_message = self._prepare_message(sub_signature_header, sub_payload)
        sub_message_bytes = sub_message.encode("utf-8")
        sub_signature_obj = subaccount_keypair.sign_message(sub_message_bytes)
        sub_signature = base58.b58encode(bytes(sub_signature_obj)).decode("ascii")

        # Step 2: Main account signs the subaccount's signature
        main_signature_header = {
            "timestamp": timestamp,
            "expiry_window": expiry_window,
            "type": "subaccount_confirm",
        }
        main_payload = {"signature": sub_signature}
        main_message = self._prepare_message(main_signature_header, main_payload)
        main_signature = self._sign_message(main_message)

        # Step 3: Send request with both signatures
        request_payload = {
            "main_account": main_public_key,
            "subaccount": sub_public_key,
            "main_signature": main_signature,
            "sub_signature": sub_signature,
            "timestamp": timestamp,
            "expiry_window": expiry_window,
        }

        # Use direct request since this has custom auth format
        import requests as http_requests
        url = f"{self.base_url}/account/subaccount/create"
        headers = {"Content-Type": "application/json"}

        response = http_requests.post(url, json=request_payload, headers=headers)
        result = response.json()

        if not result.get("success", False):
            raise Exception(f"Subaccount creation failed: {result.get('error', 'Unknown error')}")

        return result.get("data", {})

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """
        Get order status.

        Args:
            order_id: Order ID

        Returns:
            Order information
        """
        response = self._make_request(
            "GET",
            "/orders/status",
            payload={"order_id": order_id},
            request_type="get_order"
        )
        return response.get("data", {})

    def get_open_orders(self, market: Optional[str] = None, subaccount_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get open orders.

        Args:
            market: Specific market (None for all)
            subaccount_id: Specific subaccount ID (None for main account)

        Returns:
            List of open orders
        """
        # Per Pacifica API docs: GET /api/v1/orders?account=...
        payload = {}
        if market:
            payload["symbol"] = market.replace("-PERP", "").upper()
        if subaccount_id:
            payload["subaccount_id"] = subaccount_id

        response = self._make_request(
            "GET",
            "/orders",
            payload=payload,
            request_type="get_open_orders"
        )
        # Response format: {"success": true, "data": [...orders...]}
        data = response.get("data", [])
        if isinstance(data, dict):
            return data.get("orders", [])
        return data if isinstance(data, list) else []

    def get_order_history(
        self,
        market: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get order history.

        Args:
            market: Specific market
            start_time: Start timestamp (ms)
            end_time: End timestamp (ms)
            limit: Max results (default: 100)

        Returns:
            List of historical orders
        """
        payload = {"limit": limit}
        if market:
            payload["market"] = market
        if start_time:
            payload["start_time"] = start_time
        if end_time:
            payload["end_time"] = end_time

        response = self._make_request(
            "GET",
            "/orders/history",
            payload=payload,
            request_type="get_order_history"
        )
        # Pacifica returns data as direct list, not {orders: [...]}
        data = response.get("data", [])
        return data if isinstance(data, list) else data.get("orders", [])

    def get_trade_history(
        self,
        market: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get trade (fill) history from Pacifica.

        Args:
            market: Specific market to filter (symbol like "BTC")
            start_time: Start timestamp (ms)
            end_time: End timestamp (ms)
            limit: Max results (default: 50)

        Returns:
            List of executed trades with details
        """
        # Per Pacifica docs: GET /api/v1/trades/history
        payload = {"limit": limit}
        if market:
            payload["symbol"] = market.replace("-PERP", "").upper()
        if start_time:
            payload["start_time"] = start_time
        if end_time:
            payload["end_time"] = end_time

        response = self._make_request(
            "GET",
            "/trades/history",
            payload=payload,
            request_type="get_trade_history"
        )
        # Response: {"success": true, "data": [...trades...]}
        data = response.get("data", [])
        return data if isinstance(data, list) else []

    # ===========================
    # MARKET DATA ENDPOINTS
    # ===========================

    def get_markets(self) -> List[Dict[str, Any]]:
        """
        Get all available markets.

        Returns:
            List of markets with specifications including:
            - symbol: Market symbol (e.g., "BTC", "ETH")
            - tick_size: Price tick size
            - lot_size: Minimum order size increment
            - max_leverage: Maximum allowed leverage
            - min_order_size: Minimum order size
            - max_order_size: Maximum order size
            - funding_rate: Current hourly funding rate
            - next_funding_rate: Next funding rate
        """
        response = self._make_request(
            "GET",
            "/info",  # Pacifica uses /info not /markets
            authenticated=False,
            request_type="get_markets"
        )
        # Response is {"success": true, "data": [...markets...]}
        data = response.get("data", [])

        # Store market data for historical analysis (async, non-blocking)
        # Note: This is disabled for now because async storage from sync context
        # causes "no running event loop" errors. Market data collection should be
        # handled by a dedicated background service instead.
        # TODO: Implement proper background market data collection service

        # data is already a list of markets, not nested
        return data if isinstance(data, list) else []

    def get_market_specs(self, market: str) -> Dict[str, Any]:
        """
        Get market specifications.

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP")

        Returns:
            Specifications (tick_size, lot_size, leverage limits, etc.)
        """
        # Normalize symbol
        symbol = market.replace("-PERP", "").upper()

        # Market specs come from /info endpoint (same as get_markets)
        markets = self.get_markets()

        # Find matching market
        for m in markets:
            m_symbol = m.get("symbol", "").replace("-PERP", "").upper()
            if m_symbol == symbol or m.get("symbol") == market:
                return m

        # Return first market if no match found
        return markets[0] if markets else {}

    def get_ticker(self, market: str) -> Dict[str, Any]:
        """
        Get ticker data using orderbook midpoint pricing.
        Uses /book endpoint to get real-time bid/ask prices.

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP")

        Returns:
            Ticker data dict with current price and market specs
        """
        # Pacifica uses symbol without -PERP suffix
        symbol = market.replace("-PERP", "").upper()

        try:
            # Get orderbook for real-time pricing
            orderbook = self.get_orderbook(symbol)

            if not orderbook:
                logger.warning(f"No orderbook data for {symbol}")
                return {}

            # Extract bids and asks from Pacifica format
            # Format: {"s": symbol, "l": [[bids], [asks]], "t": timestamp}
            levels = orderbook.get("l", [])

            if not levels or len(levels) < 2:
                logger.warning(f"Empty orderbook for {symbol}")
                return {}

            bids = levels[0]  # First array is bids
            asks = levels[1]  # Second array is asks

            if not bids or not asks:
                logger.warning(f"Empty orderbook for {symbol}")
                return {}

            # Get best bid/ask prices
            # Orderbook format: [{"p": "price", "a": "amount", "n": 1}, ...]
            best_bid = float(bids[0]["p"]) if bids else 0
            best_ask = float(asks[0]["p"]) if asks else 0

            # Calculate midpoint price
            last_price = (best_bid + best_ask) / 2 if (best_bid and best_ask) else 0

            if last_price == 0:
                logger.warning(f"Invalid price for {symbol}: bid={best_bid}, ask={best_ask}")
                return {}

            # Get market info for additional data
            markets = self.get_markets()
            market_info = next((m for m in markets if m.get("symbol", "").upper() == symbol), {})

            # Return ticker data in expected format
            ticker = {
                "symbol": symbol,
                "last": last_price,
                "bid": best_bid,
                "ask": best_ask,
                "high": last_price * 1.02,  # Approximate (no 24h data available)
                "low": last_price * 0.98,   # Approximate
                "volume": market_info.get("volume", 0),
                "timestamp": int(time.time() * 1000)
            }

            logger.debug(f"{symbol} ticker: last=${last_price:.4f}, bid=${best_bid:.4f}, ask=${best_ask:.4f}")
            return ticker

        except Exception as e:
            logger.error(f"Failed to get ticker data for {market}: {e}")
            return {}

    def get_orderbook(self, market: str, depth: int = 20) -> Dict[str, Any]:
        """
        Get orderbook.

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP")
            depth: Orderbook depth (default: 20)

        Returns:
            Orderbook with bids and asks
        """
        # Pacifica uses symbol without -PERP suffix
        symbol = market.replace("-PERP", "").upper()

        response = self._make_request(
            "GET",
            "/book",
            payload={"symbol": symbol},
            authenticated=False,
            request_type="get_orderbook"
        )
        return response.get("data", {})

    def get_trades(
        self,
        market: str,
        limit: int = 100,
        start_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get recent trades.

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP")
            limit: Max results (default: 100)
            start_time: Start timestamp (ms)

        Returns:
            List of trades
        """
        # Pacifica uses symbol without -PERP suffix
        symbol = market.replace("-PERP", "").upper()

        payload = {"symbol": symbol, "limit": limit}
        if start_time:
            payload["start_time"] = start_time

        response = self._make_request(
            "GET",
            "/trades",
            payload=payload,
            authenticated=False,
            request_type="get_trades"
        )
        # Handle different response formats
        data = response.get("data", {})
        if isinstance(data, list):
            return data
        return data.get("trades", [])

    def get_funding_rate(self, market: str) -> Dict[str, Any]:
        """
        Get current funding rate.

        ⚠️ CRITICAL: This is the HOURLY rate (applied 24 times per day)

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP")

        Returns:
            Funding rate information (rate, next_payment_time, etc.)
        """
        # Pacifica uses symbol without -PERP suffix
        symbol = market.replace("-PERP", "").upper()

        response = self._make_request(
            "GET",
            "/funding",
            payload={"symbol": symbol, "limit": 1},
            authenticated=False,
            request_type="get_funding_rate"
        )
        data = response.get("data", {})

        # Handle funding_history array response
        if isinstance(data, dict) and "funding_history" in data:
            history = data.get("funding_history", [])
            if history:
                data = history[0]  # Most recent funding rate
        # Handle case where data is a list directly
        elif isinstance(data, list) and data:
            data = data[0]

        # Log warning about hourly rate
        rate = data.get("funding_rate", 0.0) if isinstance(data, dict) else 0.0
        logger.debug(
            f"Funding rate for {market}: {rate*100:.4f}% per hour "
            f"(${rate*100:.4f} per $10,000 position per hour, 24x per day)"
        )

        return data if isinstance(data, dict) else {}

    def get_funding_rate_history(
        self,
        market: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get funding rate history.

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP")
            start_time: Start timestamp (ms)
            end_time: End timestamp (ms)
            limit: Max results (default: 100)

        Returns:
            List of historical funding rates (hourly)
        """
        # Pacifica uses symbol without -PERP suffix
        symbol = market.replace("-PERP", "").upper()

        payload = {"symbol": symbol, "limit": limit}
        if start_time:
            payload["start_time"] = start_time
        if end_time:
            payload["end_time"] = end_time

        response = self._make_request(
            "GET",
            "/funding",
            payload=payload,
            authenticated=False,
            request_type="get_funding_history"
        )
        # Handle different response formats
        data = response.get("data", {})
        if isinstance(data, list):
            return data
        return data.get("funding_history", [])

    def get_candles(
        self,
        market: str,
        interval: str = "1h",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get candlestick (kline) data.

        Args:
            market: Market symbol (e.g., "BTC" or "BTC-PERP")
            interval: Candle interval (1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d)
            start_time: Start timestamp (ms) - REQUIRED by Pacifica API
            end_time: End timestamp (ms) - defaults to current time
            limit: Max results (default: 100)

        Returns:
            List of candles (OHLCV)
        """
        # Pacifica uses symbol without -PERP suffix
        symbol = market.replace("-PERP", "").upper()

        # start_time is REQUIRED by Pacifica API - default to 24h ago if not provided
        if start_time is None:
            start_time = int((time.time() - 86400) * 1000)  # 24 hours ago in ms

        payload = {
            "symbol": symbol,
            "interval": interval,
            "start_time": start_time
        }
        if end_time:
            payload["end_time"] = end_time

        response = self._make_request(
            "GET",
            "/kline",
            payload=payload,
            authenticated=False,
            request_type="get_candles"
        )
        # Response contains klines array or data list
        data = response.get("data", {})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "klines" in data:
            return data.get("klines", [])
        return data.get("candles", []) if isinstance(data, dict) else []

    # ===========================
    # UTILITY METHODS
    # ===========================

    def close(self):
        """Close the API client session."""
        self.session.close()
        logger.info("Closed Pacifica API client")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


# Convenience functions
def create_testnet_client(
    agent_wallet_private_key: str,
    account_public_key: str,
    rate_limit_tier: RateLimitTier = RateLimitTier.BASIC
) -> PacificaClient:
    """
    Create testnet Pacifica client.

    Args:
        agent_wallet_private_key: Agent wallet private key
        account_public_key: Account public key
        rate_limit_tier: Rate limit tier

    Returns:
        PacificaClient instance
    """
    return PacificaClient(
        agent_wallet_private_key,
        account_public_key,
        PacificaEnvironment.TESTNET,
        rate_limit_tier
    )


def create_mainnet_client(
    agent_wallet_private_key: str,
    account_public_key: str,
    rate_limit_tier: RateLimitTier = RateLimitTier.BASIC
) -> PacificaClient:
    """
    Create mainnet Pacifica client.

    Args:
        agent_wallet_private_key: Agent wallet private key
        account_public_key: Account public key
        rate_limit_tier: Rate limit tier

    Returns:
        PacificaClient instance
    """
    return PacificaClient(
        agent_wallet_private_key,
        account_public_key,
        PacificaEnvironment.MAINNET,
        rate_limit_tier
    )
