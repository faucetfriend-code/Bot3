<objective>
Implement Performance Optimization by adding position caching to reduce database queries, implementing background sync processes, and optimizing frontend API call patterns.
</objective>

<context>
This is for the trading bot project (trading_bot_v2) to improve performance after fixing core functionality. Current system may have inefficient database queries and API call patterns.

Reference the following documentation for Pacifica API details:
- @Example files/docs/context files/pacifica/rate_limits.md
- @Example files/docs/context files/pacifica/api_reference_rest.md
- @Example files/docs/context files/pacifica/api_reference_websocket.md

Reference code examples from:
- @Example files/utilities/python-sdk

Who will use this: Trading bot users experiencing slow interface or high resource usage.
End goal: Optimized system with reduced latency and resource usage.
</context>

<requirements>
1. Implement position caching to reduce database queries
2. Add background sync processes to avoid blocking operations
3. Optimize frontend API call patterns (reduce unnecessary calls, batch operations)
4. Maintain data freshness while improving performance
5. Add performance monitoring/logging
</requirements>

<implementation>
- Cache position data in memory or database to avoid repeated queries
- Implement background threads/processes for sync operations
- Optimize frontend to batch API calls and use WebSocket for real-time updates
- Add caching layers following patterns from python-sdk if applicable
- Include performance metrics in logging
</implementation>

<output>
Modify files with relative paths:
- ./trading_bot_v2/database.py - Add caching layer for position queries
- ./trading_bot_v2/api_server.py - Implement background sync processes
- ./trading_bot_v2/interface.html - Optimize API call patterns in frontend
</output>

<verification>
Before declaring complete, verify:
- Database query count reduced for position displays
- Sync operations don't block interface responsiveness
- Frontend API calls are optimized and batched
- Performance metrics show improvement
- Data remains fresh and accurate
</verification>

<success_criteria>
- Position caching reduces database load
- Background sync processes implemented
- Frontend API patterns optimized
- No performance degradation in functionality
- Measurable improvement in response times
</success_criteria></content>
<parameter name="filePath">prompts/036-performance-optimization.md

---
Completed at: 2026-01-14T01:00:34.340Z
