<objective>
Diagnose and fix the API server crash on startup. The server crashes immediately when running api_server.py, preventing it from starting and blocking all trading bot functionality.

This is critical because the API server handles all trading operations, web interface access, and bot management - without it, the entire system is unusable.
</objective>

<context>
This is for the trading bot project built with Python/FastAPI, using SQLite database and various trading integrations.

The crash occurs immediately on startup with no time to read terminal output before the window closes, suggesting a critical error in initialization code.

Key files to examine: @api_server.py (main server file), @config.py (configuration loading), @requirements.txt (dependencies), @.env files (environment variables), @database.py (database setup).

Tech stack: Python 3.x, FastAPI, Uvicorn, SQLite, various trading APIs.
</context>

<research>
Thoroughly investigate the crash by:
1. Running the server with error capture to get the exact error message
2. Analyzing the stack trace and error details
3. Examining the startup code in api_server.py for potential failure points
4. Checking configuration loading and environment variables
5. Verifying database connections and file paths
6. Testing imports and dependencies

Consider multiple potential causes: missing dependencies, invalid config, database issues, import errors, path problems on Windows, or corrupted files.
</research>

<requirements>
1. Reproduce the crash and capture the full error output (use bash commands with stderr redirection)
2. Analyze the error thoroughly - identify the exact failure point and root cause
3. Implement targeted fixes based on the diagnosis
4. Test the fix by running the server again
5. Ensure no regression in other functionality
</requirements>

<implementation>
For maximum efficiency, use parallel tool calls when gathering diagnostic information.

When running commands, use proper error capture: `python api_server.py 2>&1` to see both stdout and stderr.

Go beyond basic fixes - check for Windows-specific path issues, environment variable loading problems, and subtle import conflicts.

If the error involves missing modules, ensure proper installation and virtual environment usage.
</implementation>

<output>
Modify any necessary files to resolve the crash:
- Update configuration files if needed
- Fix import issues or code errors
- Add error handling for startup failures

Create a diagnosis summary: ./diagnoses/server-crash-diagnosis.md
- Include the error message, root cause analysis, and fixes applied
- Document any configuration changes or environment setup required
</output>

<verification>
Before declaring complete, verify:
- Server starts successfully: `python api_server.py` runs without immediate crash
- API endpoints are accessible (test with curl or browser)
- No errors in server logs during startup
- Database connections work if applicable

Run a quick integration test to ensure trading functionality isn't broken.
</verification>

<success_criteria>
- API server starts without crashing on startup
- No error messages during initialization
- Server remains running and accepts connections
- All critical functionality (trading, web interface) is accessible
- Diagnosis document clearly explains the issue and resolution
</success_criteria>