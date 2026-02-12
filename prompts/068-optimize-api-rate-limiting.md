<objective>
Optimize API rate limiting to resolve excessive 429 errors that are degrading real-time trading capabilities. This is a Priority 2 high issue where the system experienced 6+ rate limit hits in the first 5 minutes, causing exponential backoff delays of 2-4 seconds and compromising real-time trading performance.

The diagnostic report identified multiple 429 errors on `/kline`, `/account`, `/positions` endpoints, with the system entering retry cycles that impact trading responsiveness.
</objective>

<context>
This is a trading bot v2 system that relies heavily on Pacifica REST API for various operations including historical data retrieval, account information, and position management. The system needs to make frequent API calls to maintain real-time market data and trading operations.

The system uses:
- Pacifica REST API client for historical data and account operations
- Rate limiting with exponential backoff retry mechanisms
- Request queuing and caching for frequently accessed data
- WebSocket for real-time data (to reduce API dependency)
- Error handling for rate limit responses

@trading_bot_v2/pacifica_client.py
@trading_bot_v2/multi_timeframe_fetcher.py

The excessive rate limiting indicates that the current API usage patterns are inefficient, potentially making redundant requests or not properly utilizing existing data and WebSocket connections. This creates a bottleneck that prevents smooth trading operations and reduces the system's responsiveness to market opportunities.
</context>

<requirements>
1. Implement intelligent request queuing and throttling mechanisms
2. Add comprehensive caching for frequently accessed data types
3. Prioritize WebSocket data over REST API when possible
4. Add request batching to reduce total API call count
5. Implement smarter retry logic with circuit breaker patterns
6. Add request priority levels for critical vs non-critical operations
7. Monitor and track rate limit usage patterns
8. Create fallback mechanisms for rate limit scenarios

The fix must handle:
- Multiple API endpoints (/kline, /account, /positions, /info)
- Different types of requests (historical data, real-time data, account management)
- Rate limit detection and adaptive throttling
- Cache invalidation and refresh strategies
- Integration with existing WebSocket data streams
- Error recovery and graceful degradation

Why this matters: Rate limiting directly impacts the bot's ability to:
- Retrieve historical market data for analysis
- Check account positions and balances
- Execute trades in timely manner
- Respond to market opportunities quickly
- Maintain smooth operation without unnecessary delays

</requirements>

<implementation>
Thoroughly analyze the Pacifica client API usage patterns and rate limiting mechanisms. Focus on:

1. **Request Pattern Analysis**: Review current API call frequency and types
2. **Cache Utilization**: Examine existing caching effectiveness
3. **Rate Limit Handling**: Assess current retry and backoff strategies
4. **WebSocket Integration**: Check how well WebSocket data reduces API usage
5. **Request Batching Opportunities**: Identify requests that can be combined
6. **Priority Assessment**: Differentiate critical vs non-critical API calls

Optimization strategies to implement:
1. **Smart Request Queuing**: Implement request throttling with rate limit awareness
2. **Enhanced Caching**: Cache more data types with longer TTL
3. **Request Batching**: Combine multiple requests into single API calls where possible
4. **Priority-based Access**: Prioritize critical trading operations over data refresh
5. **Circuit Breaker Patterns**: Temporarily pause non-essential requests during rate limits
6. **Predictive Caching**: Pre-fetch data likely to be needed soon
7. **Rate Limit Monitoring**: Track usage patterns and adapt accordingly
8. **WebSocket Maximization**: Use WebSocket data for everything possible

What to avoid:
- Changing the Pacifica API integration or authentication
- Removing existing error handling or retry logic
- Breaking compatibility with existing code that uses the client
- Making major architectural changes without testing
- Caching sensitive data (account info, positions) inappropriately

Why these constraints matter: The API client functionality is working correctly - it's successfully making requests and handling responses. The issue is in the usage patterns and efficiency, so we should optimize how and when requests are made rather than changing the core API integration.
</implementation>

<output>
Modify the file: `trading_bot_v2/pacifica_client.py`

Key optimizations required:

1. **Enhanced rate limiting and queuing**:
```python
class RateLimitManager:
    def __init__(self, max_requests_per_minute=60):
        self.max_rpm = max_requests_per_minute
        self.requests = deque(maxlen=max_requests_per_minute)
        self.last_reset = time.time()
        self.circuit_breaker_active = False
        
    def can_make_request(self, priority='normal'):
        """Check if request can be made based on rate limits"""
        now = time.time()
        
        # Reset counter every minute
        if now - self.last_reset > 60:
            self.requests.clear()
            self.last_reset = now
            self.circuit_breaker_active = False
            
        # Allow critical requests even during rate limit
        if priority == 'critical' and not self.circuit_breaker_active:
            return True
            
        # Check rate limit
        return len(self.requests) < self.max_rpm and not self.circuit_breaker_active
    
    def record_request(self):
        """Record a request was made"""
        self.requests.append(time.time())
        
    def activate_circuit_breaker(self, duration=30):
        """Temporarily pause requests due to rate limit"""
        self.circuit_breaker_active = True
        # Auto-reset after duration
        asyncio.create_task(self._reset_circuit_breaker(duration))

class EnhancedPacificaClient:
    def __init__(self):
        self.rate_manager = RateLimitManager(max_requests_per_minute=30)  # Conservative limit
        self.request_queue = asyncio.Queue()
        self.priority_queue = asyncio.PriorityQueue()
```

2. **Intelligent caching system**:
```python
class SmartCache:
    def __init__(self):
        self.cache = {}
        self.cache_stats = defaultdict(lambda: {'hits': 0, 'misses': 0})
        
    async def get(self, key, fetch_func, ttl=300, priority='normal'):
        """Get cached data or fetch if not available"""
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < ttl:
                self.cache_stats[key]['hits'] += 1
                return data
        
        # Check rate limit before fetching
        if not self.rate_manager.can_make_request(priority):
            await self._wait_for_rate_limit()
            
        self.cache_stats[key]['misses'] += 1
        data = await fetch_func()
        self.cache[key] = (data, time.time())
        self.rate_manager.record_request()
        return data
        
    async def _wait_for_rate_limit(self):
        """Wait until rate limit allows requests"""
        while not self.rate_manager.can_make_request():
            await asyncio.sleep(1)
```

3. **Request batching implementation**:
```python
async def batch_kline_requests(self, symbols, timeframes, limit=100):
    """Batch multiple kline requests into single API call"""
    # Group by timeframe to reduce requests
    batch_requests = {}
    for symbol in symbols:
        for timeframe in timeframes:
            key = f"{symbol}_{timeframe}"
            if key not in await self._get_cached_klines(key):
                batch_requests.setdefault(timeframe, []).append(symbol)
    
    # Make batched requests
    results = {}
    for timeframe, tf_symbols in batch_requests.items():
        # Pacifica may support multiple symbols in single request
        batch_key = f"klines_batch_{timeframe}"
        results[timeframe] = await self._get_cached(
            batch_key, 
            lambda: self._fetch_batch_klines(tf_symbols, timeframe, limit),
            ttl=300
        )
    
    return results
```

4. **Priority-based request handling**:
```python
async def make_priority_request(self, endpoint, params, priority='normal'):
    """Make request with priority consideration"""
    if priority == 'critical':
        # Trading operations
        return await self._execute_request_immediately(endpoint, params)
    elif priority == 'high':
        # Market data needed for trading decisions
        return await self._make_request_with_short_wait(endpoint, params)
    else:
        # Background refreshes and non-critical data
        return await self._queue_request_for_later(endpoint, params)

async def _execute_request_immediately(self, endpoint, params):
    """Execute critical request immediately, bypassing queues"""
    return await super().make_request(endpoint, params)
```

5. **WebSocket maximization**:
```python
def should_use_websocket(self, data_type, symbol, timeframe):
    """Determine if data should come from WebSocket vs REST"""
    websocket_data_types = {
        'real_time_price': True,
        'recent_candles': lambda tf: tf in ['1m', '5m'],  # Recent timeframes
        'account_data': False,  # Always use REST for account info
        'historical_data': lambda tf: tf not in ['1m', '5m']  # Old data
    }
    
    return websocket_data_types.get(data_type, False)(symbol, timeframe)
```

Focus on:
- Rate limiting and queuing mechanisms
- Smart caching with TTL and priority
- Request batching for multiple symbols/timeframes
- Priority-based request handling
- WebSocket vs REST API optimization
- Circuit breaker patterns for rate limit recovery
- Monitoring and statistics tracking
</output>

<verification>
Before declaring complete, verify your work:

1. **Rate Limiting Test**: Confirm requests are properly throttled
2. **Cache Effectiveness Test**: Verify cache hit rates improve significantly
3. **Request Batching Test**: Test batch requests for multiple symbols
4. **Priority Handling Test**: Confirm critical requests get priority treatment
5. **Circuit Breaker Test**: Verify circuit breaker activates during rate limits
6. **WebSocket Optimization Test**: Confirm WebSocket usage is maximized
7. **Performance Test**: Verify reduced API call count and faster response times
8. **Statistics Test**: Check rate limit monitoring and reporting

Test rate limit behavior:
```python
# The optimized client should:
# - Reduce API calls by at least 50% through caching
# - Handle rate limits gracefully without breaking
# - Prioritize critical trading operations
# - Use WebSocket data for everything possible
# - Batch requests for multiple symbols/timeframes
# - Show improved response times and fewer 429 errors
```

</verification>

<success_criteria>
1. Rate limit errors reduced by > 80% (fewer than 1 per hour)
2. Cache hit rate > 70% for frequently accessed data
3. Request batching reduces total API calls by > 40%
4. Critical trading requests experience no delays
5. WebSocket usage maximized for real-time data
6. Circuit breaker prevents excessive rate limit hits
7. Overall API response time improved by > 50%
8. No regression in existing API functionality
</success_criteria>