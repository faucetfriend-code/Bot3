<objective>
Fix the WebSocket client initialization error in the trading bot server. The server starts successfully but fails to initialize the WebSocket client with the error "'PacificaWebSocketClient' object has no attribute '_initialized' and no __dict__ for setting new attributes". This prevents real-time data streaming from working.

This matters because the WebSocket client is critical for real-time trading data, and the error blocks the entire WebSocket functionality.
</objective>

<context>
This is for the FastAPI-based trading bot server that successfully initializes the REST API client but fails on WebSocket client startup. The error occurs during server lifespan initialization, specifically when trying to create the WebSocket client instance.

Key files to examine:
@pacifica_ws_client.py - WebSocket client implementation with the error
@api_server.py - server initialization code that calls the WebSocket client

The WebSocket client uses `__slots__` for memory optimization but has attribute access issues during initialization.
</context>

<requirements>
1. Identify the root cause of the WebSocket initialization failure
2. Fix the `__slots__` usage to allow proper attribute initialization
3. Ensure the singleton pattern works correctly with `__slots__`
4. Maintain memory optimization benefits of `__slots__` while fixing the error
5. Verify WebSocket client can be instantiated without errors

The fix should be minimal and targeted to resolve the attribute access issue.
</requirements>

<implementation>
Analyze the WebSocket client class structure:
- The class uses `__slots__` for memory efficiency
- The `__init__` method checks for `_initialized` attribute
- With `__slots__`, attributes must be declared or the object can't have dynamic attributes

Fix by either:
- Adding missing attributes to `__slots__`
- Using a different initialization pattern
- Removing `__slots__` if the memory benefit isn't critical

Choose the approach that maintains functionality while fixing the error.
</implementation>

<constraints>
- Do not break the singleton pattern
- Maintain WebSocket connection capabilities
- Keep the fix minimal and focused
- Ensure the server can still start with WebSocket disabled if needed
</constraints>

<output>
Modify file with relative path:
- ./pacifica_ws_client.py - fix the `__slots__` and initialization issues

Document the specific fix applied.
</output>

<verification>
Before declaring complete, verify your work by:

1. Successfully start the server without WebSocket errors
2. Confirm WebSocket client initializes properly in logs
3. Test that the singleton pattern still works
4. Verify no attribute access errors during startup

Use server logs to confirm clean WebSocket initialization.
</verification>

<success_criteria>
- Server starts without WebSocket initialization errors
- WebSocket client singleton initializes correctly
- No "'PacificaWebSocketClient' object has no attribute '_initialized'" errors
- WebSocket functionality is available for real-time data
</success_criteria>