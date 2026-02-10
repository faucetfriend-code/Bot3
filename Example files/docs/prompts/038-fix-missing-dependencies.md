<objective>
Install all missing Python dependencies identified in the diagnostic analysis to resolve import errors and enable proper module resolution. This is critical for the trading bot to function, as unresolved imports prevent core functionality like database operations, authentication, and API endpoints from working.
</objective>

<context>
This is for a Python trading bot project with extensive dependencies. The diagnostics revealed 50+ import resolution errors across key files including api_server.py, database.py, models.py, and test files.

Missing dependencies include:
- slowapi (rate limiting)
- talib (technical analysis)
- aiosqlite (async database operations)
- base58 (cryptocurrency encoding)
- config, database, auth, subaccount_manager (internal modules)

Reference @requirements.txt and @pyproject.toml for current dependencies. Check @python-sdk/requirements.txt for Pacifica-specific dependencies that may need integration.
</context>

<requirements>
1. Analyze current dependency files (requirements.txt, pyproject.toml)
2. Identify all missing packages from diagnostic errors
3. Update dependency files with correct versions
4. Install dependencies using Poetry (preferred) or pip
5. Verify installations work by running import tests
6. Ensure no version conflicts with existing packages

Go beyond basic installation - thoroughly research correct package versions and ensure compatibility with the existing codebase (Python 3.8+, async operations, SQLite integration).
</requirements>

<implementation>
Follow project patterns:
- Use Poetry for dependency management when available
- Pin versions to prevent future conflicts
- Add comments explaining why each dependency is needed
- Group dependencies logically (core, dev, optional)

Avoid common issues:
- Don't install conflicting versions
- Ensure async-compatible packages for database operations
- Verify licenses are compatible with MIT license
</implementation>

<output>
Update dependency files and install packages:

1. **Update requirements.txt**: Add missing packages with appropriate versions
2. **Update pyproject.toml**: Add Poetry dependencies if using Poetry
3. **Install dependencies**: Run installation commands
4. **Verification script**: Create ./scripts/verify_dependencies.py to test imports
</output>

<verification>
Before declaring complete:
1. Run `python -c "import slowapi, talib, aiosqlite, base58; print('Core dependencies OK')"`
2. Run `python scripts/verify_dependencies.py` and confirm all imports succeed
3. Run `python -m pytest tests/test_api_db.py -v` and ensure no import errors
4. Check that api_server.py can be imported without errors
</verification>

<success_criteria>
- All diagnostic import errors are resolved
- Core dependencies (slowapi, talib, aiosqlite) install successfully
- Internal modules (config, database, auth) can be imported
- No version conflicts or installation errors
- Project can run basic import tests without failures
</success_criteria></content>
</xai:function_call name="write">
<parameter name="filePath">prompts/039-fix-import-errors.md