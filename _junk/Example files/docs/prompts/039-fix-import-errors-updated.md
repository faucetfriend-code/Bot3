<objective>
Fix all import resolution errors identified in the diagnostic analysis by correcting module paths, adding missing __init__.py files, and ensuring proper package structure. This will restore module accessibility across the trading bot codebase, excluding pacifica_client.py.
</objective>

<context>
Diagnostics showed extensive import failures in api_server.py, database.py, models.py, and test files. Common issues include missing internal modules (config, database, auth, subaccount_manager) and path resolution problems.

Key files affected:
@api_server.py - 20+ import errors
@database.py - Import resolution issues
@models.py - Config import failure
@tests/ - Database and config imports

Do not modify pacifica_client.py as per user instructions.
</context>

<requirements>
1. Analyze import statements in all affected files (excluding pacifica_client.py)
2. Identify root causes (missing files, incorrect paths, circular imports)
3. Add missing __init__.py files to make directories packages
4. Correct relative/absolute import paths
5. Resolve any circular import dependencies
6. Test all imports after fixes

Thoroughly investigate each import error - don't just add files, ensure the imported modules actually exist and are properly structured.
</requirements>

<implementation>
Follow Python import best practices:
- Use absolute imports for clarity
- Add __init__.py files to all package directories
- Avoid circular imports by restructuring if needed
- Use try/except for optional imports where appropriate

Explain WHY fixes matter: Proper imports ensure code modularity and prevent runtime failures that could crash the trading bot during operation.
</implementation>

<output>
Fix import issues in affected files (excluding pacifica_client.py):

1. **Add missing __init__.py files** to package directories
2. **Update import statements** in:
   - ./api_server.py
   - ./database.py
   - ./models.py
   - ./tests/test_api_db.py
   - ./validate_groked.py
3. **Create import verification script**: ./scripts/test_imports.py
</output>

<verification>
Before declaring complete:
1. Run `python -c "import api_server, database, models; print('Core imports OK')"`
2. Run `python scripts/test_imports.py` and confirm all imports succeed
3. Run `python -m py_compile api_server.py database.py models.py` to check syntax
4. Run `python -m pytest tests/ -k "import" --tb=short` for any import-related tests
</verification>

<success_criteria>
- All import errors from diagnostics are resolved (excluding pacifica_client.py)
- Core modules can be imported without errors
- Python compilation succeeds for all affected files
- No circular import issues remain
- Import verification script passes all tests
</success_criteria></content>
</xai:function_call name="write">
<parameter name="filePath">prompts/040-fix-type-errors-updated.md