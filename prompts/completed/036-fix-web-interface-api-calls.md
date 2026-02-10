<objective>
Investigate and fix the web interface issue where buttons don't work and API calls fail with "Failed to fetch" errors. The interface should be able to successfully communicate with the backend API to start/stop the bot and display status information.

This is critical for the user interface to function properly and allow manual control of the trading bot.
</objective>

<context>
This issue affects the web interface at localhost:8000 where users should be able to start/stop the trading bot and view status information. The frontend is failing to make API calls to the backend, resulting in non-functional buttons and error messages.

Key files to examine and fix:
@trading_bot_v2/interface.html - Frontend implementation and API calls
@trading_bot_v2/api_server.py - Backend API endpoints and CORS configuration
@research/error.txt - Error details ("Failed to start bot: Failed to fetch")

The interface serves as the primary control mechanism for the trading bot system.
</context>

<requirements>
Investigate and resolve the "Failed to fetch" error by:

1. **Verify API Server Configuration** - Check CORS settings, routing, and server startup
2. **Test API Endpoints** - Verify /api/bot/start and /api/bot/stop endpoints work
3. **Check Frontend-Backend Communication** - Ensure API_BASE and fetch calls are correct
4. **Validate Bot Object Initialization** - Confirm bot instance is properly created and accessible
5. **Fix Error Handling** - Improve error messages and fallback behavior

Each issue must be identified and resolved with proper error handling and logging.
</requirements>

<implementation>
Follow systematic debugging approach:

**Step 1: API Server Investigation**
- Check if API server starts without errors
- Verify CORS middleware is properly configured
- Test individual endpoints with curl to isolate issues

**Step 2: Frontend-Backend Communication**
- Verify API_BASE is correctly set to '/api'
- Check if fetch calls include proper headers and methods
- Ensure error handling doesn't break subsequent calls

**Step 3: Bot Object Validation**
- Confirm bot instance is created in api_server.py
- Verify bot.start() and bot.stop() methods exist and work
- Check for threading or async issues

**Step 4: Error Handling Improvements**
- Replace generic "Failed to fetch" with specific error messages
- Add retry logic for transient network issues
- Implement proper loading states for buttons

**Why This Matters:**
The web interface is the primary user control mechanism. If buttons don't work, users cannot control the trading bot, making the entire system unusable for manual operations.
</implementation>

<output>
Modify the following files with the identified fixes:

- `./trading_bot_v2/api_server.py` - Fix CORS, routing, and bot initialization issues
- `./trading_bot_v2/interface.html` - Improve error handling and API communication
- Create diagnostic script `./test_api_endpoints.py` for testing

Create a new file documenting the fixes:
- `./WEB_INTERFACE_FIXES_COMPLETE.md` - Summary of issues found and resolutions
</output>

<verification>
After fixes, verify the interface works by:

1. **Server Startup Test**: API server starts without errors and serves interface.html
2. **API Endpoint Test**: All endpoints (/status, /bot/start, /bot/stop) respond correctly
3. **Frontend Test**: Buttons work and display proper feedback
4. **Error Handling Test**: Network issues show helpful error messages instead of "Failed to fetch"
5. **Integration Test**: Full start/stop cycle works end-to-end

Test both successful operations and error conditions.
</verification>

<success_criteria>
- Web interface loads without errors at localhost:8000
- All buttons (Start Bot, Stop Bot) work correctly
- API calls succeed and return proper responses
- Error messages are informative and actionable
- Status updates display correctly
- No "Failed to fetch" errors in normal operation
</success_criteria></content>
<parameter name="filePath">prompts/036-fix-web-interface-api-calls.md

---
Completed at: 2026-01-13T21:19:15.253Z
