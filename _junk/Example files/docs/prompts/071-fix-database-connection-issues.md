<objective>
Fix the database connection issues and locking problems without modifying the server code. The database is experiencing connection failures and locks that prevent storing collected trading data, causing data loss and system instability.

The goal is to resolve database connectivity issues and eliminate locking problems so that all collected trading data can be properly stored and retrieved.
</objective>

<context>
This is a critical database infrastructure issue in the trading bot project. The server is experiencing database connection failures and locking issues that prevent proper data storage. From the error logs in `atemp.txt`, the system is unable to store collected trading data due to database connectivity and locking problems.

The issues manifest as:
- Database connection failures during normal operation
- Database locks that prevent data insertion/updates
- Data loss when trading data cannot be stored
- System instability due to database access problems

These problems are preventing the trading bot from functioning properly, as it cannot persist trading data, positions, balances, and other critical information.

@database.py - Main database implementation with connection logic
@atemp.txt - Contains error logs showing database connection and locking issues
</context>

<requirements>
Fix database connectivity and locking issues without server modifications:

1. **Diagnose Connection Issues** - Identify why database connections are failing
2. **Resolve Locking Problems** - Fix database locks that prevent data storage
3. **Improve Connection Management** - Enhance connection pooling and lifecycle management
4. **Add Connection Recovery** - Implement automatic reconnection and retry logic
5. **Optimize Database Operations** - Improve query efficiency and transaction handling
6. **Add Monitoring and Diagnostics** - Implement database health monitoring and error reporting

The fixes must work within the existing database architecture without requiring server code changes.
</requirements>

<diagnostic_approach>
<step_by_step_investigation>
Follow this systematic diagnostic process:

1. **Connection Issue Analysis**:
   - Examine database connection initialization and lifecycle
   - Check connection string and authentication parameters
   - Verify database file permissions and accessibility
   - Test connection establishment under different conditions

2. **Locking Problem Investigation**:
   - Analyze database transaction patterns and locking behavior
   - Check for long-running transactions or connection leaks
   - Identify concurrent access patterns that cause deadlocks
   - Review database schema for potential locking issues

3. **Database Health Assessment**:
   - Check database file integrity and corruption
   - Verify database schema and table structures
   - Test basic database operations (CRUD) in isolation
   - Monitor database file size and growth patterns

4. **Performance and Resource Analysis**:
   - Check memory usage during database operations
   - Monitor connection pool utilization
   - Identify resource contention issues
   - Test database performance under load

5. **Error Pattern Analysis**:
   - Review error logs for recurring failure patterns
   - Identify specific operations that consistently fail
   - Check for race conditions in database access
   - Verify error handling and recovery mechanisms
</step_by_step_investigation>

<common_database_issues>
Check for these typical database connectivity and locking problems:

- **Connection Pool Exhaustion**: Too many open connections without proper cleanup
- **Transaction Deadlocks**: Concurrent operations blocking each other
- **File Locking Issues**: Database file locked by another process
- **Permission Problems**: Insufficient file system permissions
- **Resource Contention**: Memory or CPU constraints affecting database operations
- **Schema Inconsistencies**: Database structure issues causing operation failures
- **Connection Timeout Issues**: Network or configuration timeouts
</common_database_issues>
</diagnostic_approach>

<implementation>
<diagnostic_tools>
Use these tools to systematically investigate the database issues:

1. **Database Connection Testing**:
   - Test basic connectivity: `python -c "from database import DatabaseManager; db=DatabaseManager(); print('Connected')"`
   - Check connection pooling: Monitor active connections over time
   - Test connection recovery: Simulate connection failures and recovery

2. **Database Integrity Checking**:
   - Verify database file: Check file size, permissions, and integrity
   - Test schema validation: Ensure all required tables exist
   - Check data consistency: Validate existing data integrity

3. **Locking Analysis**:
   - Monitor database locks: Check for long-held locks
   - Test concurrent operations: Simulate multiple simultaneous database operations
   - Check transaction isolation: Verify transaction handling

4. **Performance Monitoring**:
   - Monitor query execution times
   - Check memory usage during database operations
   - Test database performance under different loads

5. **Error Simulation and Testing**:
   - Test database operations with simulated failures
   - Check error handling and recovery mechanisms
   - Verify data persistence during failure scenarios
</diagnostic_tools>

<repair_strategy>
Implement fixes that resolve database issues while maintaining compatibility:

1. **Connection Management Improvements**:
   - Implement proper connection pooling with size limits
   - Add connection health checks and automatic recovery
   - Improve connection lifecycle management and cleanup
   - Add connection timeout and retry logic

2. **Locking Problem Resolution**:
   - Optimize transaction scopes to minimize lock duration
   - Implement proper transaction isolation levels
   - Add deadlock detection and automatic retry mechanisms
   - Review and optimize database schema for better concurrency

3. **Database Operation Optimization**:
   - Implement efficient query batching and bulk operations
   - Add database operation queuing for high-concurrency scenarios
   - Optimize database indexes and query patterns
   - Implement connection multiplexing for better resource utilization

4. **Error Handling and Recovery**:
   - Add comprehensive error handling for all database operations
   - Implement automatic retry logic for transient failures
   - Add database health monitoring and alerting
   - Create fallback mechanisms for critical operations

5. **Monitoring and Diagnostics**:
   - Add detailed database operation logging
   - Implement database performance metrics collection
   - Create database health check endpoints
   - Add diagnostic tools for troubleshooting database issues
</repair_strategy>
</implementation>

<output>
Create database fixes and improvements:

1. **Database Issue Analysis**:
   - `./diagnoses/database-connection-analysis.md` - Detailed analysis of connection and locking issues
   - Include root cause analysis and impact assessment

2. **Database Fix Implementation**:
   - Modify `database.py` to fix connection and locking issues
   - Implement improved connection management and error handling
   - Add database monitoring and diagnostics

3. **Test Results**:
   - `./diagnoses/database-fix-verification.md` - Verification that database issues are resolved
   - Include before/after performance metrics and error logs

4. **Database Improvements**:
   - Add database health monitoring utilities
   - Document database maintenance procedures

All changes should be contained within the database module and maintain backward compatibility.
</output>

<constraints>
<critical_requirements>
- **No Server Code Changes**: Absolutely do not modify any server files
- **Maintain Data Integrity**: Ensure all existing data remains intact
- **Preserve API Compatibility**: Database operations must work with existing server code
- **Improve Reliability**: Database connections must be stable and reliable
- **Resolve Locking Issues**: Eliminate database locks that prevent data storage
</critical_requirements>

<technical_constraints>
- **Database-Only Changes**: All modifications within `database.py`
- **SQLite Compatibility**: Maintain compatibility with existing SQLite database
- **Performance Impact**: Improvements should not degrade performance
- **Resource Efficiency**: Optimize resource usage and connection management
- **Error Resilience**: Database operations should handle failures gracefully
</technical_constraints>

<why_these_constraints_matter>
These constraints ensure database fixes are safe and effective. Server code changes are explicitly forbidden by the user. Data integrity is critical for trading operations. API compatibility ensures existing functionality continues to work. Reliability improvements prevent data loss. Locking resolution enables proper data storage.
</why_these_constraints_matter>
</constraints>

<verification>
Before declaring the database issues resolved, verify comprehensively:

1. **Connection Stability Verification**:
   - Database connections establish reliably under all conditions
   - Connection pooling works correctly with proper cleanup
   - Connection recovery functions during failures

2. **Locking Issue Resolution**:
   - Database operations complete without deadlocks or locks
   - Concurrent operations work correctly
   - Transaction handling is robust and efficient

3. **Data Storage Verification**:
   - All trading data can be stored successfully
   - Data retrieval works correctly and efficiently
   - No data loss occurs during normal operations

4. **Performance Verification**:
   - Database operations are reasonably fast
   - Memory usage is optimized during database operations
   - Resource utilization is efficient

5. **Error Handling Verification**:
   - Database failures are handled gracefully
   - Automatic recovery works for transient issues
   - Comprehensive error logging and monitoring

6. **Integration Verification**:
   - Database works correctly with existing server code
   - All database-dependent features function properly
   - Backward compatibility is maintained

<success_criteria>
The database connection and locking issues are successfully resolved when:

- ✅ **Stable Connections**: Database connections establish reliably without failures
- ✅ **No Locking Issues**: Database operations complete without deadlocks or blocking
- ✅ **Data Storage Works**: All collected trading data can be stored successfully
- ✅ **Performance Maintained**: Database operations remain fast and efficient
- ✅ **Error Recovery**: Automatic recovery works for connection and locking issues
- ✅ **Server Compatibility**: Database works seamlessly with existing server code
- ✅ **Data Integrity**: No data loss or corruption occurs during operations

The database now provides reliable connectivity and storage capabilities without interfering with server operations.
</success_criteria>
</verification>

<success_criteria>
The database issues are fully resolved when the server can reliably connect to the database, store all collected trading data without locking issues, and maintain stable operation without requiring any server code modifications.
</success_criteria></content>
<parameter name="filePath">prompts/071-fix-database-connection-issues.md