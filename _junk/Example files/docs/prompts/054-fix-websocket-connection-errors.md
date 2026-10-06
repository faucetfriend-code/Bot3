<objective>
Fix WebSocket connection errors in the trading bot's real-time data client. The client is failing to connect to the Pacifica WebSocket stream with DNS resolution errors and missing subscription functionality, preventing real-time price, position, and balance updates.

This matters because the WebSocket client is critical for live trading data, and these errors block the entire real-time data streaming capability.
</objective>

<context>
This is for the FastAPI-based trading bot server that successfully initializes the WebSocket client but fails during connection attempts. The errors occur during runtime when attempting to establish WebSocket connections and subscribe to data channels.

Key files to examine:
@pacifica_ws_client.py - WebSocket client implementation with connection and subscription issues
@api_server.py - server code that calls WebSocket subscription methods

Reference the python-sdk directory at "G:\ai-workspace\trade bot\python-sdk" for correct code examples and patterns for WebSocket implementations.

The WebSocket URL may have DNS resolution issues, and the client is missing a public subscribe method that's being called by the server.
</context>

<requirements>
1. Diagnose the DNS resolution failure for "wss://stream.pacifica.fi/stream"
2. Verify the WebSocket URL is correct and accessible
3. Add the missing 'subscribe' method to PacificaWebSocketClient class
4. Ensure the subscribe method properly handles channel subscriptions
5. Maintain backward compatibility with existing WebSocket functionality
6. Fix any network connectivity issues preventing WebSocket connections

The fixes should be targeted to resolve the specific errors while maintaining the existing WebSocket architecture.
</requirements>

<implementation>
Analyze the WebSocket connection flow:
- The client attempts to connect to "wss://stream.pacifica.fi/stream" but fails DNS resolution
- The server calls ws_client.subscribe() but the method doesn't exist
- Check if the URL is correct or if there are network/proxy issues
- Add subscribe method that delegates to existing private _subscribe methods

Fix approach:
- Verify WebSocket URL correctness
- Add public subscribe method to match server expectations
- Ensure proper error handling for connection failures
- Test DNS resolution and network connectivity
</implementation>

<constraints>
- Do not break existing WebSocket connection logic
- Maintain the singleton pattern and async architecture
- Keep WebSocket reconnection and authentication intact
- Ensure the fix works in both testnet and mainnet environments
- Do not modify core trading logic, only WebSocket connectivity
</constraints>

<output>
Modify file with relative path:
- ./pacifica_ws_client.py - add subscribe method and fix connection issues

Document the specific fixes applied and any URL/network changes made.
</output>

<verification>
Before declaring complete, verify your work by:

1. Successfully start the server without WebSocket connection errors
2. Confirm WebSocket client connects to the stream without DNS errors
3. Test that subscribe method exists and can be called
4. Verify real-time data updates work (check logs for successful subscriptions)
5. Ensure no new errors are introduced during server operation

Use server logs to confirm clean WebSocket connections and subscriptions.
</verification>

<success_criteria>
- Server starts without WebSocket DNS resolution errors
- WebSocket client successfully connects to stream.pacifica.fi
- subscribe method is available and functional
- No "getaddrinfo failed" errors during connection attempts
- No "'PacificaWebSocketClient' object has no attribute 'subscribe'" errors
- WebSocket real-time data streaming is operational
</success_criteria>