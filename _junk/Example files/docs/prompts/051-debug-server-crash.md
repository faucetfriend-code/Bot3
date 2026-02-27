<objective>
Diagnose and fix the immediate crash of api_server.py after the WebSocket integration updates. The server fails to start properly, preventing the trading bot from running. This is critical because the server is the core API endpoint for the entire application.

This matters because without a functioning server, the trading bot cannot operate, users cannot access the interface, and real-time trading features are unavailable.
</objective>

<context>
This is for the FastAPI-based trading bot server that was recently updated to integrate WebSocket connections for real-time data feeds. The crash occurs immediately on startup, suggesting import errors, dependency issues, or initialization failures in the WebSocket code.

Key files to examine:
@api_server.py - main server file with WebSocket integration
@pacifica_ws_client.py - new WebSocket client implementation
@requirements.txt - dependency list
@package.json - Node.js dependencies (though server is Python)

The server should start successfully and provide both REST API endpoints and WebSocket connections for real-time updates.
</context>

<requirements>
1. Attempt to start the server and capture the exact error message and traceback
2. Analyze import errors, missing dependencies, and syntax issues
3. Check WebSocket client initialization and configuration
4. Verify all required modules and packages are installed
5. Identify any conflicts between old and new code
6. Fix the root cause of the crash with minimal changes

Thoroughly analyze the error logs and code to identify the exact failure point.
</requirements>

<implementation>
Use systematic debugging approach:
- Run the server with error capture to see the exact failure
- Check imports and dependencies first (most common cause)
- Examine WebSocket integration code for initialization issues
- Test individual components if needed
- Apply targeted fixes rather than wholesale changes

For dependency issues, check both Python (pip) and system-level packages. For import errors, verify file paths and module availability.
</implementation>

<constraints>
- Do not remove WebSocket functionality - the goal is to make it work, not revert
- Keep changes minimal and targeted to the crash cause
- Maintain existing REST API functionality
- Ensure fixes are robust and don't introduce new issues
</constraints>

<output>
Modify files as needed to fix the crash:
- ./api_server.py - fix any initialization or import issues
- ./pacifica_ws_client.py - fix any code errors preventing import
- ./requirements.txt - add any missing dependencies

Document the specific error and fix applied.
</output>

<verification>
Before declaring complete, verify your work by:

1. Successfully start the server without crashes
2. Confirm WebSocket client initializes properly
3. Test basic API endpoints respond correctly
4. Check that WebSocket connections can be established
5. Run a quick functional test of the server

Use logging to confirm successful startup and WebSocket integration.
</verification>

<success_criteria>
- api_server.py starts successfully without immediate crashes
- All imports resolve correctly
- WebSocket client initializes without errors
- Server responds to basic health checks
- No import errors or missing dependency issues
- Server runs stably for at least 30 seconds without crashing
</success_criteria>