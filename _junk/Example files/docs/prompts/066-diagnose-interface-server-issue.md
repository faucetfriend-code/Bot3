<objective>
Diagnose and repair the issue where the most recent interface fix has caused problems with the API server. This is a critical regression that needs immediate attention to restore system functionality.

The goal is to identify what interface changes broke the server, understand the root cause, and implement a proper fix that maintains both interface functionality and server stability.
</objective>

<context>
This is a critical regression in the trading bot project where a recent interface fix has broken the API server. The project consists of:

- FastAPI backend (`api_server.py`) with WebSocket support
- HTML/JavaScript frontend (`trading_bot_interface.html`) with real-time data streaming
- Complex integration between frontend and backend for trading operations

The issue occurred after recent interface modifications, suggesting the problem is likely in:
- API endpoint calls from the frontend
- WebSocket connection handling
- Data serialization/deserialization between frontend and backend
- Authentication or session management
- Resource cleanup or initialization issues

Recent changes may have introduced:
- Invalid API calls
- WebSocket connection issues
- Memory leaks or resource exhaustion
- Race conditions in async operations
- Incorrect data formats being sent to the server

@api_server.py - Main API server implementation
@trading_bot_interface.html - Frontend interface (recently modified)
@tests/ - Test suite to verify functionality
</context>

<requirements>
Thoroughly diagnose and repair the interface-induced server issue:

1. **Identify Recent Changes**: Determine what interface modifications caused the problem
2. **Server Impact Analysis**: Understand how the interface changes affected server operation
3. **Root Cause Analysis**: Find the specific mechanism causing server failure
4. **Minimal Fix Implementation**: Apply the smallest change needed to restore functionality
5. **Regression Prevention**: Ensure the fix doesn't break existing functionality
6. **Comprehensive Testing**: Verify all interface and server features work correctly

The diagnosis must be systematic and thorough, checking all possible interaction points between interface and server.
</requirements>

<diagnostic_approach>
<step_by_step_investigation>
Follow this systematic diagnostic process:

1. **Server Startup Analysis**:
   - Attempt to start the API server normally
   - Check for immediate startup failures or errors
   - Review server logs for initialization issues
   - Verify all dependencies are available

2. **Interface Change Review**:
   - Examine recent modifications to `trading_bot_interface.html`
   - Identify new API calls, WebSocket usage, or data handling
   - Check for changes in authentication, session management, or data formats
   - Look for potential resource leaks or improper cleanup

3. **API Endpoint Testing**:
   - Test each API endpoint individually for functionality
   - Check WebSocket endpoints for connection issues
   - Verify data serialization/deserialization works correctly
   - Test authentication and authorization flows

4. **Integration Testing**:
   - Test complete interface-to-server workflows
   - Verify real-time data streaming works
   - Check error handling and recovery mechanisms
   - Test concurrent operations and load scenarios

5. **Performance and Resource Analysis**:
   - Monitor server resource usage during interface operations
   - Check for memory leaks or connection exhaustion
   - Verify proper cleanup of resources
   - Test server stability under sustained load
</step_by_step_investigation>

<common_failure_patterns>
Check for these typical interface-induced server issues:

- **Invalid API Calls**: Frontend sending malformed requests
- **WebSocket Connection Issues**: Improper connection handling or cleanup
- **Data Format Problems**: Frontend/backend data format mismatches
- **Authentication Failures**: Session or token handling issues
- **Resource Exhaustion**: Connections, memory, or file handles not released
- **Race Conditions**: Async operations interfering with each other
- **Import/Dependency Issues**: Frontend changes requiring server-side updates
</common_failure_patterns>
</diagnostic_approach>

<implementation>
<diagnostic_tools>
Use these tools to systematically investigate the issue:

1. **Server Testing**:
   - Start server with verbose logging: `python tasks.py run-api`
   - Test individual endpoints: `curl -X GET http://localhost:8000/api/status`
   - Check WebSocket connections: Use test script to verify connectivity

2. **Interface Analysis**:
   - Review recent changes in git history
   - Check browser console for JavaScript errors
   - Test interface functionality in isolation
   - Verify API calls are properly formatted

3. **Integration Testing**:
   - Run full test suite: `pytest tests/`
   - Test end-to-end workflows manually
   - Monitor server logs during interface operations
   - Use browser developer tools to inspect network requests

4. **Debugging Tools**:
   - Enable server debug logging
   - Use browser network inspector
   - Check server resource usage
   - Test with different browsers/configurations
</diagnostic_tools>

<repair_strategy>
Once the root cause is identified, implement fixes following these principles:

1. **Minimal Changes**: Make the smallest possible fix to restore functionality
2. **Backward Compatibility**: Ensure existing functionality continues to work
3. **Proper Error Handling**: Add appropriate error handling and logging
4. **Documentation**: Document the fix and any changed behavior
5. **Testing**: Add tests to prevent future regressions

Common repair approaches:
- Fix malformed API calls in the interface
- Correct WebSocket connection handling
- Update data serialization/deserialization
- Fix authentication or session issues
- Add proper resource cleanup
- Resolve race conditions or timing issues
</repair_strategy>
</implementation>

<output>
Create diagnostic reports and implement fixes:

1. **Diagnostic Report**:
   - `./diagnoses/interface-server-issue-diagnosis.md` - Detailed analysis of the problem
   - Include symptoms, root cause, and impact assessment

2. **Fix Implementation**:
   - Modify affected files (likely `trading_bot_interface.html` and/or `api_server.py`)
   - Document all changes made with clear reasoning

3. **Test Results**:
   - `./diagnoses/interface-server-fix-verification.md` - Verification that the fix works
   - Include before/after test results

4. **Prevention Measures**:
   - Add regression tests to prevent future occurrences
   - Update documentation with lessons learned

All changes should be minimal, targeted, and thoroughly tested.
</output>

<constraints>
<critical_requirements>
- **Restore Server Functionality**: The API server must start and operate normally
- **Maintain Interface Features**: All existing interface functionality must work
- **No Breaking Changes**: Fix must not break other parts of the system
- **Minimal Impact**: Make the smallest possible change to fix the issue
- **Comprehensive Testing**: Verify the fix works across all scenarios
</critical_requirements>

<diagnostic_constraints>
- **Systematic Approach**: Follow the diagnostic steps in order, don't skip steps
- **Evidence-Based**: Base conclusions on observable evidence, not assumptions
- **Thorough Documentation**: Document every finding and step taken
- **Reproducible Results**: Ensure the diagnosis can be reproduced by others
</diagnostic_constraints>

<why_these_constraints_matter>
These constraints ensure the diagnosis is reliable and the fix is safe. Skipping diagnostic steps could miss the real root cause. Making assumptions rather than gathering evidence leads to incorrect fixes. Without thorough documentation, future developers can't understand what happened. Non-reproducible results make it impossible to verify the fix works.
</why_these_constraints_matter>
</constraints>

<verification>
Before declaring the issue resolved, verify the fix comprehensively:

1. **Server Stability Tests**:
   - Server starts without errors: `python tasks.py run-api`
   - All API endpoints respond correctly
   - WebSocket connections work properly
   - Server handles load and concurrent connections

2. **Interface Functionality Tests**:
   - Interface loads without JavaScript errors
   - All UI components work correctly
   - Real-time data streaming functions
   - Authentication and trading operations work

3. **Integration Tests**:
   - Complete workflows function end-to-end
   - Error handling works properly
   - Performance is acceptable
   - No resource leaks or memory issues

4. **Regression Tests**:
   - Run full test suite: `pytest tests/`
   - Verify no existing functionality is broken
   - Test edge cases and error scenarios

5. **Cross-Platform Verification**:
   - Test on different browsers
   - Verify functionality across different network conditions
   - Test with different server configurations

<success_criteria>
The diagnosis and repair is successful when:

- ✅ **Server Starts Normally**: API server starts without errors or warnings
- ✅ **Interface Works Fully**: All interface features function correctly
- ✅ **No Regressions**: Existing functionality continues to work
- ✅ **Root Cause Identified**: The specific cause of the issue is clearly documented
- ✅ **Fix is Minimal**: Only necessary changes were made
- ✅ **Prevention Measures**: Tests added to prevent future occurrences
- ✅ **Comprehensive Testing**: All verification steps pass

The system returns to full functionality with the interface working correctly and the server operating stably.
</success_criteria>
</verification>

<success_criteria>
The issue is fully resolved when the interface works correctly without causing server problems, and the system operates as it did before the problematic changes were introduced.
</success_criteria></content>
<parameter name="filePath">prompts/066-diagnose-interface-server-issue.md