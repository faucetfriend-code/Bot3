<objective>
Implement comprehensive server stability fixes to eliminate "AbortError: signal is aborted without reason" issues. Focus on database connection management, request timeouts, error handling, and concurrent request processing to ensure the API server runs reliably without crashes or aborted signals.
</objective>

<context>
The server is experiencing stability issues that cause frontend requests to abort unexpectedly. Diagnostics show import errors and type issues that prevent proper server operation. The AbortError suggests requests are being terminated prematurely, likely due to:

- Database connection issues or timeouts
- Improper async handling causing blocking operations
- Missing error recovery mechanisms
- Concurrent request conflicts

Reference the current api_server.py and database.py implementations to identify and fix stability bottlenecks.
</context>

<requirements>
1. Fix all import and dependency issues preventing server startup
2. Implement proper database connection pooling and cleanup
3. Add request timeouts and circuit breakers for external API calls
4. Fix async/await patterns to prevent blocking operations
5. Add comprehensive error handling with graceful degradation
6. Implement proper server shutdown and cleanup procedures
7. Add health monitoring endpoints for stability tracking

Prioritize fixes that directly address the AbortError - database connections, timeouts, and request handling.
</requirements>

<implementation>
Use proven stability patterns:
- Database connection pooling with automatic cleanup
- Request timeouts with configurable limits
- Async context managers for resource management
- Structured error handling with recovery mechanisms
- Circuit breaker pattern for external API calls
- Proper logging for debugging stability issues

Explain WHY each pattern matters: Trading systems require 99.9% uptime - stability issues can result in lost opportunities or failed trades.
</implementation>

<output>
Implement comprehensive stability improvements:

1. **Fix core import issues** in api_server.py, database.py, config.py
   - Resolve missing dependencies and path issues
   - Fix type annotations and Optional handling

2. **Implement database connection stability** in database.py
   - Add connection pooling with proper cleanup
   - Fix async database operations
   - Add connection health monitoring

3. **Add server stability features** to api_server.py
   - Request timeouts for all endpoints
   - Proper error handling without crashes
   - Concurrent request management
   - Graceful shutdown handling

4. **Create health monitoring endpoint**: /api/health
   - Database connectivity checks
   - External API availability
   - Memory and performance metrics

5. **Add server monitoring script**: ./scripts/monitor_server_stability.py
   - Automated stability testing
   - Load testing capabilities
   - Error pattern detection
</output>

<verification>
Before declaring complete:
1. Start server and verify no import errors: `python -c "from api_server import app; print('Server imports OK')"`
2. Test health endpoint: `curl http://localhost:8000/api/health`
3. Run concurrent load test: `ab -n 50 -c 5 http://localhost:8000/api/health`
4. Monitor for crashes or aborted signals over 10+ minutes
5. Check frontend - no more "AbortError" messages during normal operation
6. Verify database connections remain stable under load
</verification>

<success_criteria>
- Server starts without any import or initialization errors
- Health endpoint returns proper status information
- No crashes or aborted signals during concurrent requests
- Database connections properly managed and cleaned up
- Frontend API calls complete successfully without AbortError
- Server maintains stability under load for extended periods
- Proper error handling and recovery from network issues
</success_criteria></content>
</xai:function_call name="write">
<parameter name="filePath">prompts/045-add-server-health-monitoring.md