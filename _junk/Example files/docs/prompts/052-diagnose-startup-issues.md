<objective>
Diagnose and fix the most common causes of server startup failures in the trading bot API server. The server is crashing immediately on launch, preventing the application from running. This systematic diagnosis will identify the root cause and provide targeted fixes.

This matters because a non-functional server blocks all trading operations, data access, and user interactions with the bot.
</objective>

<context>
This is for the FastAPI-based trading bot server (api_server.py) that integrates with Pacifica exchange, WebSocket real-time data, and SQLite database. The server has complex initialization including environment variable checks, module imports, database setup, WebSocket connections, and background tasks.

Key files to examine:
@api_server.py - main server file with lifespan initialization
@pacifica_ws_client.py - WebSocket client implementation
@database.py - database connection and setup
@config.py - configuration and environment handling
@requirements.txt - dependency list
@.env - environment variables (if exists)

The server should start successfully and provide REST API endpoints plus WebSocket real-time updates.
</context>

<requirements>
Follow the systematic checklist to identify and fix startup killers:

1. **Check environment variables**: Verify AGENT_WALLET_PRIVATE_KEY, ACCOUNT_PUBLIC_KEY, and TESTNET are set
2. **Test imports**: Run individual import tests for all modules to identify failures
3. **Check uvicorn usage**: Ensure proper server startup mechanism is in place
4. **Analyze lifespan errors**: Examine startup initialization for WebSocket, database, and profile creation issues
5. **Verify database access**: Check DATABASE_PATH file exists and is accessible
6. **Test WebSocket initialization**: Isolate WebSocket startup issues
7. **Check event loop conflicts**: Identify async/sync mixing problems

Use the provided diagnostic commands and error patterns to pinpoint the exact failure.
</requirements>

<implementation>
Execute diagnostics in the recommended order:

- Start with basic import testing and environment checks
- Progress to server startup attempts with detailed logging
- Isolate components (comment out WebSocket, database) to identify the failing piece
- Apply targeted fixes based on specific error messages

For each potential issue, provide both diagnostic commands and fix steps.
</implementation>

<constraints>
- Do not make wholesale changes - identify and fix the specific root cause
- Maintain existing functionality while resolving startup issues
- Ensure fixes are robust and don't introduce new failures
- Keep diagnostic changes temporary for testing purposes
</constraints>

<output>
Modify files as needed to fix the identified startup issues:
- ./api_server.py - fix initialization, imports, or startup mechanism
- ./.env - add missing environment variables
- ./requirements.txt - add any missing dependencies
- Other files as identified by diagnostics

Document the specific issue found and fix applied.
</output>

<verification>
Before declaring complete, verify your work by:

1. Successfully start the server with `uvicorn api_server:app --reload`
2. Confirm all imports load without errors
3. Check that environment variables are properly set
4. Verify database connections work
5. Test that WebSocket initialization completes
6. Run basic API health checks

Use debug logging to confirm clean startup.
</verification>

<success_criteria>
- api_server.py starts successfully without crashes
- All diagnostic checks pass (imports, env vars, database, WebSocket)
- Server runs stably and responds to requests
- No startup errors in logs
- All initialization components (WebSocket, database, profiles) load correctly
</success_criteria>