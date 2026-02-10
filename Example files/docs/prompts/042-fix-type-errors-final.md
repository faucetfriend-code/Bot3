<objective>
Fix all type checking errors in database.py, api_server.py, and models.py to ensure type safety and prevent runtime issues in the trading bot.
</objective>

<context>
Diagnostics revealed type errors in key files. Common issues include:
- Type mismatches and None values
- Unreachable exception clauses
- Missing type definitions

Do not modify any other files.
</context>

<requirements>
1. Fix type errors in database.py, api_server.py, models.py
2. Handle Optional types and None values correctly
3. Fix unreachable exception handlers
4. Ensure type consistency

Go beyond basic fixes - ensure type safety for database operations and API responses.
</requirements>

<implementation>
Follow project conventions:
- Type hints required
- Use Union/Optional for nullable types
- Pydantic BaseModel for data structures
- Proper exception handling

Type safety prevents bugs in financial operations.
</implementation>

<output>
Fix type errors in:
- ./database.py
- ./api_server.py
- ./models.py
</output>

<verification>
1. Run `python -m mypy database.py --ignore-missing-imports` - no errors
2. Run `python -m mypy api_server.py --ignore-missing-imports` - no errors
3. Run `ruff check .` - no type errors
</verification>

<success_criteria>
- All type errors in specified files resolved
- Mypy passes
- Ruff passes
- Type safety maintained
</success_criteria></content>
</xai:function_call name="task">
<parameter name="description">Fix type errors in core files