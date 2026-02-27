<objective>
Fix all type checking errors identified in the diagnostic analysis to ensure type safety and prevent runtime issues in the trading bot. This involves correcting type annotations, handling None values properly, and fixing unreachable exception handlers.
</objective>

<context>
Diagnostics revealed numerous type errors across database.py, api_server.py, and models.py. Common issues include:
- Type mismatches (int assigned to str parameters)
- None values assigned to non-optional types
- Unreachable exception clauses

Do not modify any files outside of database.py, api_server.py, and models.py.
</context>

<requirements>
1. Analyze each type error from diagnostics in the specified files
2. Add proper type annotations where missing
3. Handle Optional types and None values correctly
4. Fix unreachable exception handlers
5. Ensure type consistency across database operations
6. Run type checking to verify fixes

Go beyond basic fixes - deeply consider type safety implications for database operations and API responses to prevent data corruption or runtime errors.
</requirements>

<implementation>
Follow project type conventions:
- Type hints required for all function parameters and return values
- Use Union/Optional for nullable types
- Pydantic BaseModel for data structures
- Proper exception handling without unreachable clauses

Explain WHY type safety matters: Strong typing prevents bugs in financial calculations and database operations that could lead to trading losses or data corruption.
</implementation>

<output>
Fix type errors in affected files:

1. **Update type annotations** in:
   - ./database.py (20+ type errors)
   - ./api_server.py (exception handler issues)
   - ./models.py (undefined Tuple)

2. **Add type imports** where needed (typing module)
3. **Create type checking verification**: Update linting configuration
</output>

<verification>
Before declaring complete:
1. Run `python -m mypy database.py --ignore-missing-imports` and resolve all errors
2. Run `python -m mypy api_server.py --ignore-missing-imports` 
3. Run `ruff check .` and ensure no type-related errors
4. Run `python -c "from database import DatabaseManager; print('Type checks passed')"`
</verification>

<success_criteria>
- All type errors from diagnostics are resolved in the specified files
- Mypy runs without errors on affected files
- Ruff linting passes type checks
- Database operations maintain type safety
- No unreachable code remains
</success_criteria></content>
</xai:function_call name="write">
<parameter name="filePath">prompts/041-fix-database-schema-updated.md