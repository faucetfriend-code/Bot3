<objective>
Investigate why the API server crashes on load. This is a critical issue preventing the trading bot from functioning properly. The crash occurs during server startup, likely due to import errors, configuration issues, database connection problems, or dependency conflicts.
</objective>

<context>
This is for a Solana-based perpetual trading bot with a FastAPI backend and HTML interface. The API server (api_server.py) serves trading data, handles authentication, and manages bot operations. The crash prevents users from accessing the trading interface and executing trades.

Key files to examine:
@api_server.py - Main server file where crash occurs
@config.py - Configuration loading that might fail
@database.py - Database connections that might fail
@requirements.txt - Dependencies that might be missing or incompatible
@pyproject.toml - Additional dependency management
</context>

<requirements>
Thoroughly analyze the crash by:
1. Attempting to start the API server and capturing exact error messages
2. Examining import statements for missing modules
3. Checking configuration loading and environment variables
4. Testing database connections and schema
5. Verifying all dependencies are installed and compatible
6. Analyzing error stack traces for root causes

Use systematic debugging approach - test each component individually before full startup.
</requirements>

<investigation_steps>
1. **Initial Crash Reproduction**: Run the server startup command and capture full error output
2. **Import Analysis**: Check each import statement in api_server.py for missing modules
3. **Configuration Check**: Verify config.py loads correctly and environment variables are set
4. **Database Connection Test**: Test database connectivity and schema integrity
5. **Dependency Verification**: Ensure all required packages are installed with correct versions
6. **Environment Analysis**: Check Python version, virtual environment, and system dependencies
7. **Error Pattern Analysis**: Identify if crash is consistent or intermittent
</investigation_steps>

<output>
Save comprehensive investigation report to: ./diagnoses/api-server-crash-analysis.md

Report should include:
- Exact error messages and stack traces
- Component-by-component test results
- Root cause analysis with confidence levels
- Recommended fix approaches
- Any immediate workarounds found
</output>

<verification>
Before completing investigation:
- Confirm you can reproduce the crash consistently
- Verify error messages are captured in full
- Test any potential workarounds you discover
- Ensure analysis covers all major components (imports, config, database, dependencies)
</verification>

<success_criteria>
Investigation is complete when:
- Root cause(s) of the crash are identified with high confidence
- All error messages are documented and explained
- Clear path forward for repairs is established
- Report provides actionable insights for fixing the issues
</success_criteria></content>
<parameter name="filePath">prompts/073-investigate-api-server-crash.md