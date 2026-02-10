Optimized Prompt for Hub System Fixes
Based on the comprehensive testing results, here's the structured prompt for implementing all Priority 1 and 2 fixes plus Priority 3 performance optimization:
---
<objective>
Implement all Priority 1 and Priority 2 fixes identified in the hub system testing, plus the performance optimization from Priority 3. Ensure the API communication hub system achieves reliable, secure, and high-performance operation.
</objective>
<priority_1_fixes>
<import_architecture_fix>
Fix the import architecture issues that prevent testing:
- Replace relative imports from .config import with dependency injection in api_server.py
- Accept config and database objects as constructor parameters to DataHub and ConnectionManager
- Create proper package structure with __init__.py files for test isolation
- Ensure all imports work in both production and test environments
</import_architecture_fix>
<websocket_client_testing>
Implement comprehensive WebSocket client testing:
- Add real WebSocket server testing using websockets library
- Test actual client connections, not just mocks
- Include connection lifecycle testing (connect, disconnect, reconnect)
- Test cross-browser WebSocket compatibility
- Add message handling and error scenario testing
- Implement heartbeat/ping-pong testing for connection health
</websocket_client_testing>
</priority_1_fixes>
<priority_2_fixes>
<synchronization_primitives>
Add proper synchronization for data operations:
- Implement locks for cache operations using asyncio.Lock()
- Add readiness flags for component initialization
- Use semaphores for limiting concurrent operations
- Implement atomic operations for cache updates
- Add synchronization barriers for startup sequences
</synchronization_primitives>
<enhanced_error_handling>
Improve error handling throughout the system:
- Add circuit breaker pattern for failing connections
- Implement exponential backoff for reconnections
- Add proper exception handling with specific error types
- Implement graceful degradation strategies
- Add error aggregation and reporting mechanisms
</enhanced_error_handling>
<security_hardening>
Implement security measures for the hub:
- Add WebSocket authentication using tokens or API keys
- Implement message validation and sanitization
- Add rate limiting for connections and messages
- Implement input validation for all broadcast messages
- Add audit logging for security events
</security_hardening>
</priority_2_fixes>
<priority_3_performance>
<performance_optimization>
Optimize performance for high-throughput scenarios:
- Implement connection pooling with configurable limits
- Add asynchronous message queuing for broadcasts
- Optimize broadcast algorithms to avoid blocking operations
- Implement connection resource limits and cleanup
- Add performance monitoring and metrics collection
- Use efficient data structures for subscriber management
</performance_optimization>
</priority_3_performance>
<implementation_requirements>
- Maintain backward compatibility with existing interfaces
- Ensure all changes are thoroughly tested with the existing test suite
- Add comprehensive logging for debugging and monitoring
- Document all new configuration options and parameters
- Provide migration path for existing deployments
</implementation_requirements>
<testing_validation>
- Run full test suite with 100% pass rate
- Validate WebSocket connections under load
- Test error scenarios and recovery mechanisms
- Performance benchmark against baseline metrics
- Security audit for new authentication mechanisms
</testing_validation>
<success_criteria>
- All Priority 1 and 2 fixes implemented and tested
- Performance optimization achieving target metrics
- Zero critical security vulnerabilities
- Full test coverage maintained
- System operates reliably in production environment
</success_criteria>