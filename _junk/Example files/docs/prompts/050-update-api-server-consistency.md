<objective>
Update api_server.py to ensure it is fully consistent with recent changes to API interactions, particularly the WebSocket client integration and real-time data handling. This ensures the server properly supports the new WebSocket-based real-time updates for positions, orders, balance, and market data.

This matters because inconsistent API handling can lead to stale data, connection issues, or broken real-time features in the trading bot interface.
</objective>

<context>
This is for the FastAPI-based trading bot server (api_server.py) that serves the HTML interface and handles API requests. Recent changes include migrating from REST polling to WebSocket connections for real-time data (prices, positions, orders, balance) using a new pacifica_ws_client.py singleton.

Key files to examine:
@api_server.py - current server implementation
@pacifica_ws_client.py - new WebSocket client (if exists)
@execution.py - recent changes to position monitoring
@main.py - potential initialization changes

The server needs to integrate the WebSocket client for real-time updates to the frontend.
</context>

<requirements>
1. Review api_server.py for any missing imports or initialization of the WebSocket client
2. Ensure the /ws WebSocket endpoint is properly implemented to forward real-time events to connected frontend clients
3. Update any API endpoints that previously used REST polling to now use WebSocket cache access where appropriate
4. Verify that the server properly handles WebSocket client lifecycle (start/stop) alongside the bot lifecycle
5. Ensure error handling accounts for WebSocket connection states and fallbacks

Be explicit about maintaining backward compatibility while adding real-time capabilities.
</requirements>

<implementation>
Examine the current api_server.py implementation and compare with recent changes in execution.py and other files. Update imports, initialization, and endpoint handlers to support the WebSocket client.

For the /ws endpoint, ensure it registers callbacks with the WebSocket client and forwards events like price updates, position changes, and order fills to the browser.

Maintain the existing REST API endpoints as fallbacks for when WebSocket is unavailable.
</implementation>

<constraints>
- Do not break existing REST API functionality
- Ensure WebSocket integration is optional (fallback to REST if WebSocket fails)
- Keep the server startup process clean and reliable
- Maintain proper error handling and logging
</constraints>

<output>
Modify file with relative path:
- ./api_server.py - Update to integrate WebSocket client and ensure consistency with recent API interaction changes
</output>

<verification>
Before declaring complete, verify your work by:

1. Check that api_server.py imports and initializes the WebSocket client correctly
2. Confirm the /ws endpoint exists and properly forwards events
3. Test that the server starts without errors and WebSocket connections can be established
4. Verify existing REST endpoints still function as fallbacks
5. Run a basic server test to ensure no import or initialization issues

Use logging to confirm WebSocket integration is working.
</verification>

<success_criteria>
- api_server.py successfully imports and uses the WebSocket client
- /ws endpoint is implemented and functional for real-time updates
- Server maintains compatibility with existing REST API calls
- No startup errors related to WebSocket integration
- Real-time data flows correctly from WebSocket to frontend via the server
</success_criteria>