<objective>
Fix the frontend API connection errors (ERR_CONNECTION_REFUSED) and conduct a comprehensive audit to find and fix any other hidden errors in the trading bot codebase.

The frontend is failing to connect to the API server at localhost:8000, causing all API calls to fail with connection refused errors. This is blocking the entire web interface functionality. After fixing the connection issues, systematically identify and resolve any other errors that may be masked by the connection failures.
</objective>

<context>
This is for the trading bot project with a FastAPI backend and HTML/JavaScript frontend. The server was recently fixed and is running, but the frontend cannot establish connections to API endpoints like /api/status, /api/pacifica/markets, etc.

The errors show repeated "ERR_CONNECTION_REFUSED" for all API calls, with retry logic attempting multiple times before failing. The interface loads but cannot fetch any data.

Tech stack: Python/FastAPI (backend), HTML/CSS/JavaScript (frontend), SQLite database.
</context>

<research>
Thoroughly investigate the connection issues by:
1. Verifying the API server is actually running and listening on the correct port/interface
2. Checking CORS configuration and headers
3. Examining frontend API call URLs and configuration
4. Testing direct API access with curl/browser
5. Analyzing network/firewall issues
6. Checking for port conflicts or binding problems

After fixing connections, perform comprehensive error audit:
1. Run all tests to identify failing tests
2. Check for import errors, syntax issues, and runtime exceptions
3. Analyze logs for hidden errors
4. Test all major functionality paths
5. Look for deprecated code, unused imports, and potential bugs
</research>

<requirements>
1. Diagnose and fix the frontend-backend connection issues
2. Verify API server accessibility and CORS setup
3. Test all API endpoints from frontend
4. Conduct systematic error audit across the entire codebase
5. Fix any identified errors, bugs, or issues
6. Ensure all functionality works end-to-end
</requirements>

<implementation>
For maximum efficiency, use parallel tool calls when checking multiple API endpoints or running tests.

When testing connections, use curl commands to verify server responses: `curl -v http://localhost:8000/api/status`

For error audit, run comprehensive tests: `python -m pytest tests/ -v` and analyze failures.

Go beyond basic fixes - check for subtle issues like race conditions, memory leaks, or edge case failures that only appear under load.

If connection issues persist, consider server binding (0.0.0.0 vs localhost), firewall rules, or proxy configurations.
</implementation>

<output>
Fix any necessary files for connection issues:
- Update API server configuration if needed
- Modify frontend API URLs or CORS settings
- Fix any discovered errors during audit

Create comprehensive audit reports:
- ./diagnoses/frontend-connection-diagnosis.md (connection fix details)
- ./diagnoses/comprehensive-error-audit.md (all found errors and fixes)
</output>

<verification>
Before declaring complete, verify:
- Frontend can successfully connect to all API endpoints
- All API calls return proper responses (not connection errors)
- Web interface loads and displays data correctly
- Comprehensive test suite passes (or all failures are documented/explained)
- No runtime errors in server logs
- All major functionality (trading, data display, settings) works

Test the full user workflow from interface to ensure no hidden errors remain.
</verification>

<success_criteria>
- Frontend successfully connects to API server without ERR_CONNECTION_REFUSED errors
- All API endpoints respond correctly from frontend
- Web interface fully functional with data loading
- Comprehensive error audit completed with all identified issues fixed
- No critical errors remain in the codebase
- System ready for production use
</success_criteria>