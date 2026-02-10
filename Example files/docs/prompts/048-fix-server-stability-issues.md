<objective>
Diagnose and fix critical server stability issues causing "Network error: Expecting value: line 1 column 1 (char 0)" errors and database connection pool exhaustion. The server initially runs well but degrades over time, leading to API failures and connection leaks that prevent proper operation.

This is essential for maintaining reliable trading bot operation - connection leaks and API failures can cause data loss and trading disruptions.
</objective>

<context>
The server logs show a pattern of initial stability followed by cascading failures:
- Kline API calls fail with JSON parsing errors ("Expecting value: line 1 column 1")
- Database connection pool becomes exhausted ("Pool exhausted: 10 connections in use")
- System falls back to database/mock data for positions and market data
- Eventually leads to complete API unavailability

Reference the current api_server.py, database.py, and pacifica_client.py implementations to identify the root causes of these stability issues.
</context>

<requirements>
1. Analyze database connection management - identify connection leaks and improper cleanup
2. Examine kline API error handling - fix JSON parsing failures and empty responses
3. Review connection pool configuration and timeout settings
4. Check for async/await issues causing blocking operations
5. Implement proper error recovery and connection cleanup
6. Add monitoring for connection pool health and API success rates

Thoroughly investigate the progression from working to failing state to identify all contributing factors.
</requirements>

<implementation>
Use proven stability patterns:
- Implement proper database connection context managers
- Add connection pool monitoring and automatic cleanup
- Use circuit breaker pattern for failing API endpoints
- Implement exponential backoff for retry logic
- Add connection health checks and automatic recovery
- Use structured logging for debugging connection issues

Explain WHY each fix matters: Trading systems require 99.9% uptime - connection leaks can cause database unavailability and API failures can lead to missing market data.
</implementation>

<output>
Implement comprehensive stability fixes:

1. **Fix database connection management** in database.py
   - Add proper connection cleanup in all database operations
   - Implement connection pool monitoring
   - Add automatic connection recovery

2. **Fix kline API error handling** in pacifica_client.py and pacifica_market_data.py
   - Handle empty responses and JSON parsing errors gracefully
   - Add retry logic with exponential backoff
   - Implement fallback mechanisms

3. **Add connection monitoring** to api_server.py
   - Add health check endpoint for connection pool status
   - Implement connection pool size monitoring
   - Add automatic cleanup of stale connections

4. **Create stability monitoring script**: ./scripts/monitor_server_stability.py
   - Track connection pool usage over time
   - Monitor API success/failure rates
   - Alert on connection leaks or API failures
</output>

<verification>
Before declaring complete:
1. Start server and monitor connection pool usage over 30 minutes
2. Test kline API calls under load to ensure no JSON parsing errors
3. Verify database connections are properly cleaned up after operations
4. Check that server maintains stability without pool exhaustion
5. Confirm API endpoints remain responsive during extended operation
</verification>

<success_criteria>
- Server runs for extended periods (30+ minutes) without connection pool exhaustion
- Kline API calls succeed without "Expecting value: line 1 column 1" errors
- Database connections are properly managed and cleaned up
- System no longer falls back to mock data unexpectedly
- Connection pool monitoring shows healthy usage patterns
- API endpoints remain stable under normal load
</success_criteria></content>
</xai:function_call name="write">
<parameter name="filePath">prompts/049-implement-connection-pool-monitoring.md