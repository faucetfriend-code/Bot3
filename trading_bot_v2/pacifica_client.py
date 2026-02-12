import asyncio
import json
import time
import uuid
import base58
import json
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union, Tuple, Callable
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


class RequestPriority(Enum):
    """Priority levels for API requests."""
    CRITICAL = 3  # Trading operations (place/cancel orders)
    HIGH = 2     # Market data needed for trading decisions
    NORMAL = 1   # Background refreshes and non-critical data
    LOW = 0      # Statistics and monitoring


class RateLimitManager:
    """Manages API rate limiting with circuit breaker patterns."""
    
    def __init__(self, max_requests_per_minute: int = 30):
        """
        Initialize rate limit manager.
        
        Args:
            max_requests_per_minute: Maximum requests per minute (conservative limit)
        """
        self.max_rpm = max_requests_per_minute
        self.requests = deque(maxlen=max_requests_per_minute)
        self.last_reset = time.time()
        self.circuit_breaker_active = False
        self.circuit_breaker_until = 0
        self.rate_limit_hits = 0
        self.total_requests = 0
        
        # Statistics tracking
        self.priority_stats = defaultdict(lambda: {'count': 0, 'success': 0})
        
    def can_make_request(self, priority: Union[str, RequestPriority] = RequestPriority.NORMAL) -> bool:
        """
        Check if request can be made based on rate limits and circuit breaker.
        
        Args:
            priority: Request priority level
            
        Returns:
            True if request can proceed, False otherwise
        """
        if isinstance(priority, str):
            priority = RequestPriority[priority.upper()]
            
        now = time.time()
        
        # Reset counter every minute
        if now - self.last_reset > 60:
            self.requests.clear()
            self.last_reset = now
            self.circuit_breaker_active = False
            self.rate_limit_hits = 0
            
        # Check circuit breaker
        if self.circuit_breaker_active:
            if now < self.circuit_breaker_until:
                return False
            else:
                self.circuit_breaker_active = False
                
        # Allow critical requests even during rate limit (within reason)
        if priority == RequestPriority.CRITICAL:
            if len(self.requests) < self.max_rpm * 0.9:  # 90% threshold for critical
                return True
            elif len(self.requests) < self.max_rpm:
                logger.warning(f"Critical request near rate limit threshold: {len(self.requests)}/{self.max_rpm}")
                return True
            else:
                logger.error(f"Critical request blocked by rate limit: {len(self.requests)}/{self.max_rpm}")
                return False
            
        # Check rate limit for other priorities
        return len(self.requests) < self.max_rpm
    
    def record_request(self, priority: Union[str, RequestPriority] = RequestPriority.NORMAL, success: bool = True):
        """
        Record a request was made.
        
        Args:
            priority: Request priority level
            success: Whether the request was successful
        """
        if isinstance(priority, str):
            priority = RequestPriority[priority.upper()]
            
        self.requests.append(time.time())
        self.total_requests += 1
        self.priority_stats[priority]['count'] += 1
        if success:
            self.priority_stats[priority]['success'] += 1
    
    def record_rate_limit_hit(self):
        """Record a rate limit hit and activate circuit breaker if needed."""
        self.rate_limit_hits += 1
        
        # Activate circuit breaker if multiple hits in short time
        recent_hits = sum(1 for req_time in self.requests if time.time() - req_time < 60)
        if recent_hits >= 3:  # 3+ rate limit hits in a minute
            self.activate_circuit_breaker(duration=30)
            
    def activate_circuit_breaker(self, duration: int = 30):
        """
        Temporarily pause requests due to rate limit.
        
        Args:
            duration: Duration in seconds to pause requests
        """
        self.circuit_breaker_active = True
        self.circuit_breaker_until = time.time() + duration
        logger.warning(f"Circuit breaker activated for {duration} seconds due to rate limiting")
        
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiting statistics."""
        return {
            'total_requests': self.total_requests,
            'rate_limit_hits': self.rate_limit_hits,
            'circuit_breaker_active': self.circuit_breaker_active,
            'current_usage': len(self.requests),
            'max_rpm': self.max_rpm,
            'reset_in': max(0, 60 - (time.time() - self.last_reset)),
            'priority_stats': dict(self.priority_stats)
        }


class SmartCache:
    """Intelligent caching system with TTL and invalidation."""
    
    def __init__(self, rate_manager: RateLimitManager):
        """
        Initialize smart cache.
        
        Args:
            rate_manager: RateLimitManager instance for rate limiting
        """
        self.cache: Dict[str, Tuple[Any, float, RequestPriority]] = {}  # key -> (data, timestamp, priority)
        self.cache_stats = defaultdict(lambda: {'hits': 0, 'misses': 0})
        self.rate_manager = rate_manager
        
        # TTL by data type (in seconds)
        self.ttl_config = {
            'market_data': 10,      # Market prices - very short TTL
            'candles_1m': 60,      # 1-minute candles
            'candles_5m': 120,     # 5-minute candles
            'candles_15m': 300,    # 15+ minute candles
            'account_data': 30,     # Account info - short TTL for trading
            'positions': 30,       # Positions - short TTL
            'orders': 60,          # Orders - medium TTL
            'markets': 3600,       # Market info - long TTL
            'funding': 300,        # Funding rates - medium TTL
        }
        
    def get(self, key: str, fetch_func: Callable[[], Any], ttl: Optional[int] = None, 
            priority: Union[str, RequestPriority] = RequestPriority.NORMAL) -> Any:
        """
        Get cached data or fetch if not available.
        
        Args:
            key: Cache key
            fetch_func: Function to call if cache miss
            ttl: Time-to-live in seconds (overrides default)
            priority: Request priority for rate limiting
            
        Returns:
            Cached or fetched data
        """
        if isinstance(priority, str):
            priority = RequestPriority[priority.upper()]
            
        # Check cache
        if key in self.cache:
            data, timestamp, cached_priority = self.cache[key]
            cache_ttl = ttl or self._get_ttl_for_key(key)
            
            if time.time() - timestamp < cache_ttl:
                self.cache_stats[key]['hits'] += 1
                logger.debug(f"Cache HIT: {key} (age: {time.time() - timestamp:.1f}s)")
                return data
            else:
                # Stale cache entry
                del self.cache[key]
                logger.debug(f"Cache STALE: {key} (age: {time.time() - timestamp:.1f}s > TTL: {cache_ttl}s)")
        
        # Cache miss - check rate limit before fetching
        if not self.rate_manager.can_make_request(priority):
            logger.warning(f"Rate limit reached for {key}, waiting...")
            self._wait_for_rate_limit(priority)
            
        self.cache_stats[key]['misses'] += 1
        try:
            data = fetch_func()
            self.cache[key] = (data, time.time(), priority)
            self.rate_manager.record_request(priority, success=True)
            logger.debug(f"Cache SET: {key}")
            return data
        except Exception as e:
            self.rate_manager.record_request(priority, success=False)
            if "rate limit" in str(e).lower() or "429" in str(e):
                self.rate_manager.record_rate_limit_hit()
            raise
            
    async def get_async(self, key: str, fetch_func: Callable[[], Any], ttl: Optional[int] = None,
                       priority: Union[str, RequestPriority] = RequestPriority.NORMAL) -> Any:
        """
        Async version of get method.
        
        Args:
            key: Cache key
            fetch_func: Async function to call if cache miss
            ttl: Time-to-live in seconds (overrides default)
            priority: Request priority for rate limiting
            
        Returns:
            Cached or fetched data
        """
        if isinstance(priority, str):
            priority = RequestPriority[priority.upper()]
            
        # Check cache (same as sync version)
        if key in self.cache:
            data, timestamp, cached_priority = self.cache[key]
            cache_ttl = ttl or self._get_ttl_for_key(key)
            
            if time.time() - timestamp < cache_ttl:
                self.cache_stats[key]['hits'] += 1
                logger.debug(f"Cache HIT: {key} (age: {time.time() - timestamp:.1f}s)")
                return data
            else:
                del self.cache[key]
                
        # Cache miss - check rate limit before fetching
        if not self.rate_manager.can_make_request(priority):
            logger.warning(f"Rate limit reached for {key}, waiting...")
            await self._wait_for_rate_limit_async(priority)
            
        self.cache_stats[key]['misses'] += 1
        try:
            data = await fetch_func()
            self.cache[key] = (data, time.time(), priority)
            self.rate_manager.record_request(priority, success=True)
            logger.debug(f"Cache SET: {key}")
            return data
        except Exception as e:
            self.rate_manager.record_request(priority, success=False)
            if "rate limit" in str(e).lower() or "429" in str(e):
                self.rate_manager.record_rate_limit_hit()
            raise
    
    def _get_ttl_for_key(self, key: str) -> int:
        """Get TTL for a cache key based on its type."""
        for pattern, ttl in self.ttl_config.items():
            if pattern in key:
                return ttl
        return 300  # Default 5 minutes
        
    def _wait_for_rate_limit(self, priority: RequestPriority):
        """Wait until rate limit allows requests."""
        wait_count = 0
        while not self.rate_manager.can_make_request(priority):
            wait_count += 1
            if wait_count > 60:  # Maximum wait of 1 minute
                raise RateLimitError("Extended rate limit exceeded")
            time.sleep(1)
            
    async def _wait_for_rate_limit_async(self, priority: RequestPriority):
        """Async version of wait for rate limit."""
        wait_count = 0
        while not self.rate_manager.can_make_request(priority):
            wait_count += 1
            if wait_count > 60:  # Maximum wait of 1 minute
                raise RateLimitError("Extended rate limit exceeded")
            await asyncio.sleep(1)
    
    def invalidate(self, pattern: Optional[str] = None):
        """
        Invalidate cache entries.
        
        Args:
            pattern: Optional pattern to match keys for selective invalidation
        """
        if pattern:
            keys_to_remove = [key for key in self.cache.keys() if pattern in key]
            for key in keys_to_remove:
                del self.cache[key]
            logger.debug(f"Invalidated {len(keys_to_remove)} cache entries matching '{pattern}'")
        else:
            cache_size = len(self.cache)
            self.cache.clear()
            logger.debug(f"Invalidated all {cache_size} cache entries")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'cache_size': len(self.cache),
            'cache_stats': dict(self.cache_stats),
            'hit_rate': self._calculate_hit_rate(),
            'memory_usage': sum(len(str(data[0])) for data in self.cache.values())
        }
    
    def _calculate_hit_rate(self) -> float:
        """Calculate overall cache hit rate."""
        total_hits = sum(stats['hits'] for stats in self.cache_stats.values())
        total_requests = total_hits + sum(stats['misses'] for stats in self.cache_stats.values())
        return (total_hits / total_requests * 100) if total_requests > 0 else 0.0


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
    """Enhanced client for interacting with the Pacifica exchange API with rate limiting and caching."""

    def __init__(
        self,
        agent_wallet_private_key: str,
        account_public_key: str,
        testnet: bool = True,
        max_requests_per_minute: int = 30,
    ):
        """
        Initialize the enhanced Pacifica client.

        Args:
            agent_wallet_private_key: Private key for the agent wallet.
            account_public_key: Public key for the account.
            testnet: Whether to use testnet (default True).
            max_requests_per_minute: Maximum requests per minute for rate limiting.
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
        
        # Initialize rate limiting and caching components
        self.rate_manager = RateLimitManager(max_requests_per_minute=max_requests_per_minute)
        self.cache = SmartCache(self.rate_manager)
        
        # Request queues for different priorities
        self.request_queue = asyncio.Queue()
        self.priority_queue = asyncio.PriorityQueue()
        
        # WebSocket availability flag (set by external WebSocket client)
        self.ws_client = None
        
        logger.info(f"PacificaClient initialized with rate limiting ({max_requests_per_minute} RPM) and smart caching")
    
    def set_websocket_client(self, ws_client):
        """Set WebSocket client for data source optimization."""
        self.ws_client = ws_client
        logger.info("WebSocket client set for data source optimization")
    
    def should_use_websocket(self, data_type: str, symbol: str, timeframe: Optional[str] = None) -> bool:
        """
        Determine if data should come from WebSocket vs REST.
        
        Args:
            data_type: Type of data (market_data, candles, account_data, etc.)
            symbol: Trading symbol
            timeframe: Optional timeframe for candle data
            
        Returns:
            True if WebSocket should be used, False otherwise
        """
        if not self.ws_client:
            return False
            
        # Define WebSocket preference rules
        websocket_preferences = {
            'market_data': True,  # Real-time prices always from WebSocket
            'candles': lambda tf: tf in ['1m', '5m'] if tf else False,  # Recent timeframes from WebSocket
            'account_data': False,  # Always use REST for account info (security)
            'positions': False,    # Always use REST for positions (security)
            'orders': False,       # Always use REST for orders (security)
            'funding': False,      # Use REST for funding rates
        }  # type: Dict[str, Union[bool, Callable[[str], bool]]]
        
        preference = websocket_preferences.get(data_type, False)
        if callable(preference) and timeframe:
            return bool(preference(timeframe))
        return bool(preference)
    
    def make_priority_request(self, endpoint: str, params: Dict[str, Any], 
                             priority: Union[str, RequestPriority] = RequestPriority.NORMAL,
                             method: str = 'POST') -> Dict[str, Any]:
        """
        Make request with priority consideration.
        
        Args:
            endpoint: API endpoint
            params: Request parameters
            priority: Request priority level
            method: HTTP method (GET/POST)
            
        Returns:
            API response as dict
        """
        if isinstance(priority, str):
            priority = RequestPriority[priority.upper()]
            
        if priority == RequestPriority.CRITICAL:
            # Trading operations - execute immediately
            return self._execute_request_immediately(endpoint, params, method)
        elif priority == RequestPriority.HIGH:
            # Market data needed for trading decisions
            return self._make_request_with_short_wait(endpoint, params, method)
        else:
            # Background refreshes and non-critical data
            return self._queue_request_for_later(endpoint, params, method)
    
    def _execute_request_immediately(self, endpoint: str, params: Dict[str, Any], method: str = 'POST') -> Dict[str, Any]:
        """Execute critical request immediately, bypassing queues."""
        if method == 'GET':
            return self._make_get_request(endpoint, params)
        else:
            # Determine request type based on endpoint
            if '/orders/create' in endpoint:
                request_type = 'create_order' if 'market' not in endpoint else 'create_market_order'
            elif '/orders/cancel' in endpoint:
                request_type = 'cancel_order'
            else:
                request_type = f"signed_{endpoint.replace('/', '_')}"
            return self._make_signed_request(endpoint, params, request_type)
    
    def _make_request_with_short_wait(self, endpoint: str, params: Dict[str, Any], method: str = 'POST') -> Dict[str, Any]:
        """Make request with minimal wait time for high priority."""
        if not self.rate_manager.can_make_request(RequestPriority.HIGH):
            time.sleep(0.1)  # Short wait for high priority
            
        if method == 'GET':
            return self._make_get_request(endpoint, params)
        else:
            return self._make_signed_request(endpoint, params, f"signed_{endpoint.replace('/', '_')}")
    
    def _queue_request_for_later(self, endpoint: str, params: Dict[str, Any], method: str = 'POST') -> Dict[str, Any]:
        """Queue non-critical request for later execution."""
        # For now, execute with longer wait time (could be enhanced with actual queueing)
        if not self.rate_manager.can_make_request(RequestPriority.NORMAL):
            time.sleep(1.0)  # Longer wait for normal priority
            
        if method == 'GET':
            return self._make_get_request(endpoint, params)
        else:
            return self._make_signed_request(endpoint, params, f"signed_{endpoint.replace('/', '_')}")
    
    async def batch_kline_requests(self, symbols: List[str], timeframes: List[str], limit: int = 100) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """
        Batch multiple kline requests into fewer API calls.
        
        Args:
            symbols: List of symbols
            timeframes: List of timeframes
            limit: Number of candles per request
            
        Returns:
            Dictionary with results organized by symbol and timeframe
        """
        results = {}
        
        # Group by timeframe to optimize requests
        for tf in timeframes:
            tf_results = {}
            
            # Check cache first for each symbol/timeframe combination
            uncached_symbols = []
            for symbol in symbols:
                cache_key = f"candles_{symbol}_{tf}"
                try:
                    # Try to get from cache
                    cached_data = await self.cache.get_async(cache_key, 
                        lambda: None, ttl=self.cache.ttl_config.get(f'candles_{tf}', 300))
                    
                    if cached_data:
                        tf_results[symbol] = cached_data
                        logger.debug(f"Cache HIT for batch request: {symbol}_{tf}")
                    else:
                        uncached_symbols.append(symbol)
                except Exception:
                    uncached_symbols.append(symbol)
            
            # Fetch uncached symbols
            if uncached_symbols:
                logger.info(f"Batch fetching {len(uncached_symbols)} symbols for timeframe {tf}")
                
                # Process symbols in parallel
                fetch_tasks = []
                for symbol in uncached_symbols:
                    task = asyncio.create_task(self._fetch_single_candle_async(symbol, tf, limit))
                    fetch_tasks.append((symbol, task))
                
                # Wait for all fetches
                for symbol, task in fetch_tasks:
                    try:
                        candles = await task
                        if candles:
                            tf_results[symbol] = candles
                            # Cache the results
                            cache_key = f"candles_{symbol}_{tf}"
                            self.cache.cache[cache_key] = (candles, time.time(), RequestPriority.NORMAL)
                    except Exception as e:
                        logger.error(f"Failed to fetch candles for {symbol}_{tf}: {e}")
            
            results[tf] = tf_results
            
        logger.info(f"Batch request completed: {len(results)} timeframes processed")
        return results
    
    async def _fetch_single_candle_async(self, symbol: str, timeframe: str, limit: int) -> List[Dict[str, Any]]:
        """Async wrapper for fetching single candle data."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.get_candles(market=symbol, interval=timeframe, limit=limit)
        )

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
            self.rate_manager.record_rate_limit_hit()
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(f"API error: {response.text}")

        # Handle response - could be JSON or string
        try:
            return response.json()
        except json.JSONDecodeError:
            # Handle non-JSON responses (e.g., "success" string)
            response_text = response.text.strip()
            logger.debug(f"API returned non-JSON response: {response_text}")
            
            # Return a standardized format for string responses
            if response_text.lower() == '"success"' or response_text.lower() == "success":
                return {"success": True, "data": {"status": "success"}, "raw_response": response_text}
            elif response_text.lower() == '"error"' or response_text.lower() == "error":
                return {"success": False, "data": {}, "error": "API returned error response", "raw_response": response_text}
            else:
                # Unknown string response - treat as success but include raw response
                logger.warning(f"API returned unexpected string response: {response_text}")
                return {"success": True, "data": {"raw_response": response_text}, "raw_response": response_text}

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
                self.rate_manager.record_rate_limit_hit()
                raise RateLimitError(
                    "Rate limit exceeded - retrying with exponential backoff"
                )
            else:
                raise ValueError(
                    f"API error: {response.reason or response.text or 'Unknown error'}"
                )

        # Handle response - could be JSON or string
        try:
            return response.json()
        except json.JSONDecodeError:
            # Handle non-JSON responses (e.g., "success" string)
            response_text = response.text.strip()
            logger.debug(f"API returned non-JSON GET response: {response_text}")
            
            # Return a standardized format for string responses
            if response_text.lower() == '"success"' or response_text.lower() == "success":
                return {"success": True, "data": {"status": "success"}, "raw_response": response_text}
            elif response_text.lower() == '"error"' or response_text.lower() == "error":
                return {"success": False, "data": {}, "error": "API returned error response", "raw_response": response_text}
            else:
                # Unknown string response - treat as success but include raw response
                logger.warning(f"API returned unexpected string GET response: {response_text}")
                return {"success": True, "data": {"raw_response": response_text}, "raw_response": response_text}

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

        payload: Dict[str, Any] = {}
        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        params: Dict[str, Any] = {
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

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get open positions with smart caching.

        Returns:
            List of position data.
        """
        # Positions should not use WebSocket for security reasons
        cache_key = f"positions_data"
        positions_data = self.cache.get(
            cache_key,
            lambda: self._fetch_positions_fallback(),
            ttl=self.cache.ttl_config['positions'],
            priority=RequestPriority.HIGH
        )
        return positions_data if isinstance(positions_data, list) else positions_data.get("positions", [])
    
    def _fetch_positions_fallback(self) -> List[Dict[str, Any]]:
        """Fallback method for fetching positions via REST API."""
        # Account positions endpoint uses GET with query parameters
        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": "get_positions",
        }

        payload: Dict[str, Any] = {}
        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        params: Dict[str, Any] = {
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
            self.rate_manager.record_rate_limit_hit()
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
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Place a new order with critical priority.

        Args:
            symbol: Trading symbol.
            side: 'buy' or 'sell'.
            quantity: Order quantity.
            order_type: Order type (e.g., 'limit', 'market').
            price: Order price (required for limit orders).
            stop_loss: Stop loss price (optional but recommended for safety).
            take_profit: Take profit price (optional).

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

        # Include stop loss and take profit if provided
        if stop_loss is not None and stop_loss > 0:
            payload["stop_loss"] = str(stop_loss)
        if take_profit is not None and take_profit > 0:
            payload["take_profit"] = str(take_profit)

        # Use critical priority for order placement
        return self._execute_request_immediately(endpoint, payload, 'POST')

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

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel all open orders, optionally for a specific symbol.

        Args:
            symbol: Trading symbol (e.g., "BTC"). If None, cancels all orders.

        Returns:
            Cancellation summary as dict.
        """
        payload: Dict[str, Any] = {
            "all_symbols": symbol is None,
            "exclude_reduce_only": False,
        }
        if symbol:
            payload["symbol"] = symbol.upper()
            payload["all_symbols"] = False
        return self._make_signed_request("/orders/cancel_all", payload, "cancel_all_orders")

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """
        Get market data (prices) for a symbol with caching and WebSocket optimization.

        Args:
            symbol: Trading symbol (without -PERP suffix).

        Returns:
            Market data as dict.
        """
        # Check WebSocket first for real-time data
        if self.should_use_websocket('market_data', symbol):
            if self.ws_client and hasattr(self.ws_client, 'get_ticker_data'):
                try:
                    ws_data = self.ws_client.get_ticker_data(symbol)
                    if ws_data:
                        logger.debug(f"WebSocket HIT for market data: {symbol}")
                        return ws_data
                except Exception as e:
                    logger.debug(f"WebSocket failed for market data {symbol}: {e}")
        
        # Use cached data if available
        cache_key = f"market_data_{symbol}"
        return self.cache.get(
            cache_key,
            lambda: self._fetch_market_data_fallback(symbol),
            ttl=self.cache.ttl_config['market_data'],
            priority=RequestPriority.HIGH
        )
    
    def _fetch_market_data_fallback(self, symbol: str) -> Dict[str, Any]:
        """Fallback method for fetching market data via REST API."""
        response = self._make_request_with_short_wait("/prices", {"symbol": symbol}, 'GET')
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
        Get historical candle/OHLCV data for a market with WebSocket optimization and smart caching.

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

        # Check WebSocket first for recent timeframes
        if self.should_use_websocket('candles', clean_symbol, interval):
            if self.ws_client and hasattr(self.ws_client, 'get_kline_data'):
                try:
                    ws_data = self.ws_client.get_kline_data(clean_symbol, interval)
                    if ws_data and len(ws_data) >= min(50, limit):  # Minimum data requirement
                        logger.debug(f"WebSocket HIT for candles: {clean_symbol} {interval}")
                        # Take the most recent candles
                        return ws_data[-limit:] if len(ws_data) > limit else ws_data
                except Exception as e:
                    logger.debug(f"WebSocket failed for candles {clean_symbol} {interval}: {e}")

        # Use cache for non-real-time or fallback data
        cache_key = f"candles_{clean_symbol}_{interval}"
        return self.cache.get(
            cache_key,
            lambda: self._fetch_candles_fallback(market, interval, start_time, end_time, limit),
            ttl=self.cache.ttl_config.get(f'candles_{interval}', 300),
            priority=RequestPriority.HIGH if interval in ['1m', '5m'] else RequestPriority.NORMAL
        )
    
    def _fetch_candles_fallback(self, market: str, interval: str, start_time: Optional[int], 
                                end_time: Optional[int], limit: int) -> List[Dict[str, Any]]:
        """Fallback method for fetching candles via REST API."""
        # Strip -PERP suffix if present - Pacifica API expects plain symbol
        import re
        clean_symbol = re.sub(r"-perp$", "", market, flags=re.IGNORECASE).upper()

        params: Dict[str, Any] = {
            "symbol": clean_symbol,
            "interval": interval,
        }

        # start_time is required by Pacifica API
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time

        try:
            response = self._make_request_with_short_wait("/kline", params, 'GET')
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
    def get_trades(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get recent trades/trade history.

        Args:
            limit: Maximum number of trades to return (default: 100).
            symbol: Optional symbol to filter trades by.
            start_time: Optional start time in milliseconds.
            end_time: Optional end time in milliseconds.

        Returns:
            List of trade data with standardized fields.
        """
        # Account trades history endpoint uses GET with query parameters
        timestamp = int(time.time() * 1000)

        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5000,
            "type": "get_trades",
        }

        payload: Dict[str, Any] = {}
        message, signature = sign_message(signature_header, payload, self.agent_keypair)

        params: Dict[str, Any] = {
            "account": self.account_public_key,
            "agent_wallet": self.agent_wallet_public_key,
            "signature": signature,
            "timestamp": signature_header["timestamp"],
            "expiry_window": signature_header["expiry_window"],
            "limit": limit,
        }

        # Add optional parameters
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        url = self.base_url + "/trades/history"

        try:
            response = self.session.get(url, params=params, timeout=API_TIMEOUT)
        except requests.exceptions.Timeout:
            logger.warning("GET /trades/history timeout, will retry...")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GET /trades/history connection error: {e}, will retry...")
            raise

        if response.status_code == 401:
            raise ValueError("Authentication failed")
        elif response.status_code == 429:
            logger.warning("Rate limit hit (429) for GET /trades/history, backing off...")
            raise RateLimitError(
                "Rate limit exceeded - retrying with exponential backoff"
            )
        elif response.status_code >= 400:
            raise ValueError(f"API error: {response.text}")

        response_data = response.json()
        trades_data = response_data.get("data", [])

        # Standardize trade format to match expected fields
        standardized_trades = []
        for trade in trades_data:
            try:
                standardized_trade = {
                    "history_id": trade.get("history_id"),
                    "order_id": trade.get("order_id"),
                    "client_order_id": trade.get("client_order_id"),
                    "symbol": trade.get("symbol"),
                    "side": trade.get("side"),
                    "amount": self._safe_float_convert(trade.get("amount")),
                    "price": self._safe_float_convert(trade.get("price")),
                    "entry_price": self._safe_float_convert(trade.get("entry_price")),
                    "fee": self._safe_float_convert(trade.get("fee")),
                    "pnl": self._safe_float_convert(trade.get("pnl")),
                    "event_type": trade.get("event_type"),
                    "timestamp": trade.get("created_at"),
                    "created_at": trade.get("created_at"),
                    "cause": trade.get("cause"),
                    # Add additional standard fields for compatibility
                    "quantity": self._safe_float_convert(trade.get("amount")),
                    "executed_price": self._safe_float_convert(trade.get("price")),
                    "executed_amount": self._safe_float_convert(trade.get("amount")),
                    "status": "filled",  # All trades in history are filled
                }
                standardized_trades.append(standardized_trade)
            except Exception as e:
                logger.warning(f"Error parsing trade data: {e}")
                continue

        logger.debug(f"Retrieved {len(standardized_trades)} trades")
        return standardized_trades

    def get_balance(self) -> Dict[str, Any]:
        """
        Get account balance and equity information with smart caching.

        Returns:
            Dict with balance information including fields like 'balance', 'account_equity', etc.
        """
        # Account data should not use WebSocket for security reasons
        cache_key = f"account_data_balance"
        return self.cache.get(
            cache_key,
            lambda: self._fetch_balance_fallback(),
            ttl=self.cache.ttl_config['account_data'],
            priority=RequestPriority.HIGH
        )
    
    def _fetch_balance_fallback(self) -> Dict[str, Any]:
        """Fallback method for fetching balance via REST API."""
        # Account info endpoint uses GET with query parameters (no signature required for basic account info)
        params = {
            "account": self.account_public_key,
        }

        try:
            response = self._make_request_with_short_wait("/account", params, 'GET')
            
            # Extract balance data from response
            data = response.get("data", {})
            
            # Standardize the balance response to match expected format
            balance_data = {
                "balance": data.get("balance", "0"),
                "account_equity": data.get("account_equity", "0"),
                "equity": data.get("account_equity", "0"),  # Alias for compatibility
                "available_balance": data.get("balance", "0"),
                "total_equity": data.get("account_equity", "0"),
                "usd_balance": data.get("balance", "0"),
                "portfolio_value": data.get("account_equity", "0"),
                "pending_balance": data.get("pending_balance", "0"),
                "total_margin_used": data.get("total_margin_used", "0"),
                "fee_level": data.get("fee_level", 0),
                "maker_fee": data.get("maker_fee", "0"),
                "taker_fee": data.get("taker_fee", "0"),
                # Include original data for debugging
                "raw_response": data,
            }
            
            logger.debug(f"Balance data retrieved: {balance_data}")
            return balance_data
            
        except ValueError as e:
            logger.error(f"Failed to fetch account balance: {e}")
            # Return default balance structure on error
            return {
                "balance": "0",
                "account_equity": "0", 
                "equity": "0",
                "available_balance": "0",
                "total_equity": "0",
                "usd_balance": "0",
                "portfolio_value": "0",
                "error": str(e),
            }
        except Exception as e:
            logger.error(f"Unexpected error fetching account balance: {e}")
            return {
                "balance": "0",
                "account_equity": "0",
                "equity": "0", 
                "available_balance": "0",
                "total_equity": "0",
                "usd_balance": "0",
                "portfolio_value": "0",
                "error": str(e),
            }
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive performance statistics for monitoring and optimization.
        
        Returns:
            Dictionary with rate limiting, caching, and overall performance metrics
        """
        return {
            'rate_limiting': self.rate_manager.get_stats(),
            'caching': self.cache.get_stats(),
            'websocket_enabled': self.ws_client is not None,
            'api_endpoint': self.base_url,
            'summary': {
                'total_requests_made': self.rate_manager.total_requests,
                'rate_limit_hits': self.rate_manager.rate_limit_hits,
                'cache_hit_rate': self.cache._calculate_hit_rate(),
                'circuit_breaker_active': self.rate_manager.circuit_breaker_active,
                'current_rpm_usage': f"{len(self.rate_manager.requests)}/{self.rate_manager.max_rpm}"
            }
        }
    
    def clear_caches(self, pattern: Optional[str] = None):
        """
        Clear caches for maintenance or troubleshooting.
        
        Args:
            pattern: Optional pattern to selectively clear cache entries
        """
        self.cache.invalidate(pattern)
        logger.info(f"Cache cleared with pattern: {pattern or 'all'}")
    
    def reset_rate_limiting(self):
        """Reset rate limiting state (use with caution)."""
        self.rate_manager.requests.clear()
        self.rate_manager.circuit_breaker_active = False
        self.rate_manager.last_reset = time.time()
        logger.warning("Rate limiting state reset - use with caution")
    
    def optimize_for_high_frequency(self):
        """Optimize settings for high-frequency trading."""
        self.rate_manager.max_rpm = 60  # Increase to 60 RPM for HFT
        # Reduce cache TTLs for more frequent updates
        self.cache.ttl_config['market_data'] = 5
        self.cache.ttl_config['candles_1m'] = 30
        self.cache.ttl_config['candles_5m'] = 60
        logger.info("Optimized for high-frequency trading (60 RPM, reduced TTLs)")
    
    def optimize_for_low_frequency(self):
        """Optimize settings for low-frequency trading."""
        self.rate_manager.max_rpm = 20  # Reduce to 20 RPM for LFT
        # Increase cache TTLs for less frequent updates
        self.cache.ttl_config['market_data'] = 30
        self.cache.ttl_config['candles_1m'] = 120
        self.cache.ttl_config['candles_5m'] = 300
        self.cache.ttl_config['candles_15m'] = 600
        logger.info("Optimized for low-frequency trading (20 RPM, increased TTLs)")
