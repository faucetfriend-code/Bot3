<objective>
Fix the API server crash by resolving all import errors and dependency issues identified in the investigation. The crash is caused by extensive import resolution failures across the codebase, preventing the server from loading properly.
</objective>

<context>
The investigation revealed that the API server crashes due to numerous import resolution errors. Key modules like database, config, auth, pacifica_client, and others cannot be imported. This is a critical blocking issue for the trading bot functionality.

The root cause appears to be Python module resolution issues affecting the entire codebase. This needs systematic fixing of import paths, missing dependencies, and module structure.
</context>

<requirements>
Fix all import resolution errors by:
1. Ensuring all local modules are properly importable
2. Installing missing dependencies (base58, slowapi, etc.)
3. Fixing Python path and module structure issues
4. Resolving undefined variables and type errors
5. Ensuring all critical modules (database, config, auth, etc.) can be imported

Focus on getting the API server to start successfully without import errors.
</requirements>

<fix_priorities>
**HIGH PRIORITY - Critical for server startup:**
- Fix database.py import resolution
- Fix config.py import resolution
- Fix auth.py import resolution
- Install missing dependencies (base58, slowapi, etc.)
- Fix undefined variables (TradingBot, PacificaWebSocketManager)

**MEDIUM PRIORITY - Functionality:**
- Fix type errors in database operations
- Resolve import errors in test files
- Fix configuration loading issues

**LOW PRIORITY - Code quality:**
- Fix unreachable exception handlers
- Resolve type annotation issues
- Clean up import organization
</fix_priorities>

<implementation>
1. **Dependency Installation**: Install all missing packages identified in diagnostics
2. **Module Structure**: Ensure proper __init__.py files exist for all packages
3. **Import Path Fixes**: Fix any incorrect relative imports
4. **Undefined Variables**: Implement or import missing classes/functions
5. **Type Error Fixes**: Resolve type mismatches and None assignments
6. **Test each fix**: Verify server can import each module after fixes

Use systematic approach - fix one module at a time and test imports before moving to the next.
</implementation>

<output>
Create/modify files to fix import issues:
- Install missing dependencies in requirements.txt or pyproject.toml
- Fix import statements in affected files
- Add missing __init__.py files if needed
- Implement missing classes/functions
- Update configuration files as needed

Save summary of fixes applied to: ./diagnoses/api-server-crash-fixes.md
</output>

<verification>
After each major fix, test that:
- The specific module can be imported without errors
- API server startup gets further without crashing
- No new import errors are introduced

Final verification: API server should start successfully with `python api_server.py` or `uvicorn api_server:app`
</verification>

<success_criteria>
API server crash is fixed when:
- All critical imports (database, config, auth, pacifica_client) resolve successfully
- Server starts without import-related crashes
- Basic API endpoints are accessible
- No undefined variable errors during startup
- Dependencies are properly installed and importable
</success_criteria></content>
<parameter name="filePath">prompts/074-fix-api-server-crash.md