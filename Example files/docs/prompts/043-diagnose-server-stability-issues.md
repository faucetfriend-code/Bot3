<objective>
Diagnose and fix server stability issues causing "AbortError: signal is aborted without reason" errors in the frontend. The API server is experiencing crashes, timeouts, or request handling failures that cause the frontend to retry requests with network errors.

The goal is to identify the root causes of server instability and implement fixes to ensure reliable API operation without aborted signals or unexpected crashes.
</objective>

<context>
The trading bot interface is now loading properly, but the server has stability issues. Frontend logs show:
- "Network error (attempt 1/3), retrying in 3054ms..."
- "AbortError: signal is aborted without reason"

This indicates the API server is either:
- Crashing during request handling
- Timing out on long-running operations
- Not properly handling concurrent requests
- Having issues with database connections or Pacifica API calls

Reference the current api_server.py implementation and identify stability bottlenecks.
</context>

<requirements>
1. Analyze server logs for crash patterns and error sources
2. Check for timeout issues in API endpoints
3. Identify database connection leaks or blocking operations
4. Review concurrent request handling and thread safety
5. Examine Pacifica API integration for hanging requests
6. Implement proper error handling and graceful degradation

Focus on the most critical stability issues first - database connections, request timeouts, and proper cleanup.
</requirements>

<implementation>
Follow established patterns:
- Use proper async/await for I/O operations
- Implement connection pooling for database access
- Add request timeouts and circuit breakers
- Use structured logging for debugging
- Handle exceptions gracefully without crashing
- Implement proper cleanup in startup/shutdown

Explain WHY each fix matters: Server stability is critical for trading operations - crashes can lead to lost data or failed trades.
</implementation>

<output>
Create stability fixes and monitoring:

1. **Update api_server.py** with stability improvements
   - Add request timeouts
   - Implement proper database connection management
   - Add error recovery mechanisms
   - Fix concurrent request handling

2. **Create server monitoring script**: ./scripts/monitor_server.py
   - Track request success/failure rates
   - Monitor database connection health
   - Log stability metrics

3. **Update error handling** in frontend API calls
   - Better error detection and recovery
   - Proper timeout handling
   - Graceful degradation

4. **Add health check endpoint**: /api/health
   - Server status monitoring
   - Database connectivity checks
   - Pacifica API availability
</output>

<verification>
Before declaring complete:
1. Start the server and run concurrent requests: `ab -n 100 -c 10 http://localhost:8000/api/health`
2. Monitor for crashes or aborted signals
3. Check server logs for error patterns
4. Verify frontend no longer shows "AbortError" messages
5. Run extended stability test: keep server running for 30+ minutes with periodic requests
</verification>

<success_criteria>
- Server runs for extended periods without crashes
- No "AbortError: signal is aborted" in frontend logs
- API requests complete successfully without unexpected aborts
- Proper error handling for network issues and timeouts
- Database connections remain stable under load
- Concurrent requests handled properly
</success_criteria></content>
</xai:function_call name="write">
<parameter name="filePath">prompts/044-implement-server-stability-fixes.md