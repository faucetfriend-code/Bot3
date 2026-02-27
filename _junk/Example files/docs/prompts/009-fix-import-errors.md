<objective>
Fix all import resolution errors in the trading bot codebase to enable proper server operation and allow the data mapping audit to proceed.

The codebase has 50+ import errors preventing modules like config, database, models, etc. from being imported, blocking server startup and system functionality. These must be resolved systematically to restore full system operation.
</objective>

<context>
This is for the trading bot project with a complex Python codebase using FastAPI, SQLite, and various trading modules. The import errors are preventing the server from running properly and blocking further development tasks like data mapping audits.

The errors include: module not found, relative import issues, missing dependencies, and type annotation problems. The system worked previously but has import path/configuration issues.
</context>

<research>
Thoroughly investigate import issues by:
1. Analyzing Python path configuration and module structure
2. Checking relative vs absolute import usage
3. Verifying __init__.py files in all package directories
4. Examining sys.path and PYTHONPATH settings
5. Testing individual module imports to isolate issues
6. Checking for circular import dependencies

Consider multiple resolution approaches:
- Fix relative import paths
- Add missing __init__.py files
- Update PYTHONPATH or sys.path
- Resolve dependency installation issues
- Fix type annotation imports
</research>

<requirements>
1. Resolve all import resolution errors in api_server.py and related modules
2. Ensure all local modules (config, database, models, etc.) can be imported
3. Install any missing dependencies (talib, etc.)
4. Fix type annotation and syntax errors
5. Verify server can start without import errors
6. Test that all critical functionality imports work
</requirements>

<implementation>
For maximum efficiency, use parallel tool calls when testing multiple import fixes.

When fixing imports, test each change immediately: `python -c "import config; print('config imported successfully')"`

Go beyond basic fixes - implement proper Python packaging practices:
- Ensure all directories with modules have __init__.py files
- Use absolute imports where appropriate to avoid path issues
- Set up proper PYTHONPATH for the project structure

Explain WHY proper imports matter: "Correct import resolution prevents runtime failures and ensures the codebase is maintainable and deployable"
</implementation>

<output>
Modify any necessary files for import fixes:
- Update import statements in Python files
- Add missing __init__.py files
- Fix PYTHONPATH or module path configurations
- Install missing dependencies

Create import resolution report: ./diagnoses/import-errors-fixed.md
- Document all errors found and fixes applied
- Include before/after import test results
</output>

<verification>
Before declaring complete, verify:
- Server starts without import errors: `python api_server.py`
- All critical imports work: `python -c "import config, database, models"`
- No import-related exceptions in logs
- API endpoints are accessible without import failures
- Type checking passes: `python -m mypy api_server.py` (if mypy available)

Test the full application startup sequence to ensure no import issues remain.
</verification>

<success_criteria>
- All import resolution errors eliminated
- Server starts cleanly without any import-related errors
- All modules can be imported successfully
- Type annotations and syntax are correct
- System is ready for further development tasks
- Import report documents all fixes applied
</success_criteria>