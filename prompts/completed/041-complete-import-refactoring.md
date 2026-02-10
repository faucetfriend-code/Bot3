<objective>
Complete the remaining import refactoring to achieve full compliance with the import fix rules, then perform comprehensive diagnostics and testing to verify the project's health and create a detailed status report.
</objective>

<context>
This follows up on the partial import fixes already implemented. The main application files are working, but numerous test files, utilities, and modules still use forbidden sys.path.insert() calls and bare imports instead of absolute imports from core_logic.

The project is a trading bot with core_logic package containing models, indicators, and pacifica_client modules. The goal is LSP-compatible imports that work statically without runtime path manipulation.

@research/import fix.txt - Contains the mandatory rules
@research/import-repair-summary.md - Shows what was already completed
@pyrightconfig.json - LSP configuration
</context>

<requirements>
1. **Complete Import Refactoring**:
   - Find all remaining files using sys.path.insert()
   - Replace bare imports (from models import) with absolute imports (from core_logic.models import)
   - Remove all sys.path.insert() calls from application code
   - Ensure core_logic package is properly importable

2. **Code Quality Verification**:
   - Run linting (ruff check)
   - Run type checking (mypy)
   - Verify LSP can resolve all imports without errors

3. **Comprehensive Testing**:
   - Run full test suite (pytest)
   - Test import resolution across all modules
   - Verify bot can start and initialize without import errors

4. **Diagnostic Analysis**:
   - Check for any remaining import violations
   - Verify package structure integrity
   - Test runtime vs static analysis consistency

5. **Status Report Creation**:
   - Document all completed fixes
   - List any remaining issues
   - Provide recommendations for next steps
</requirements>

<implementation>
**Import Refactoring Approach:**
- Use grep to find all sys.path.insert() occurrences
- For each file, replace path manipulation with proper absolute imports
- Test each change incrementally to avoid breaking functionality
- Follow the exact patterns from working files (trading_bot.py, strategy_manager.py)

**Testing Strategy:**
- Start with import-only tests (python -c "import module")
- Progress to unit tests
- End with integration tests including bot startup

**Diagnostic Depth:**
Thoroughly analyze the codebase for patterns and ensure no regressions. Consider edge cases like test files, utility scripts, and optional imports.
</implementation>

<steps>
1. **Audit Remaining Violations**:
   - Use grep to identify all files with sys.path.insert()
   - Categorize by type (test files, strategy files, utilities)

2. **Fix Import Violations** (process files sequentially):
   - For each file, replace sys.path.insert() with absolute imports
   - Update import statements to use core_logic prefix
   - Remove path manipulation code entirely

3. **Verify LSP Compatibility**:
   - Run mypy type checking
   - Check for unresolved import errors
   - Restart LSP/Pylance if needed

4. **Run Code Quality Checks**:
   - Execute ruff check for linting
   - Fix any style or import issues found

5. **Execute Test Suite**:
   - Run pytest on all test files
   - Verify no import-related test failures
   - Check test coverage if available

6. **Integration Testing**:
   - Test bot import: python -c "import trading_bot_v2.trading_bot"
   - Test core_logic imports individually
   - Attempt bot startup with launcher script

7. **Create Comprehensive Report**:
   - Document all changes made
   - List any remaining issues or failures
   - Provide next steps and recommendations
</steps>

<output>
Save the final status report to: ./analyses/import-refactoring-completion-report.md

The report should include:
- Summary of fixes completed
- Files modified and changes made
- Test results and diagnostics
- Any remaining issues with specific file locations
- Recommendations for future maintenance
</output>

<verification>
Before declaring complete:
- Confirm zero sys.path.insert() calls remain in application code
- Verify all imports resolve in LSP without errors
- Ensure bot can start successfully
- Confirm all tests pass
- Validate report contains actionable information

After each major step, run quick validation tests to catch issues early.
</verification>

<success_criteria>
- Zero sys.path.insert() calls in codebase (except possibly in experimental scripts)
- All imports use absolute package paths (from core_logic.module import ...)
- LSP/Pylance shows no unresolved import errors
- Bot starts and initializes without import failures
- Full test suite passes
- Comprehensive report documents project state and any remaining issues
</success_criteria></content>
<parameter name="filePath">./prompts/041-complete-import-refactoring.md

---
Completed at: 2026-01-14T18:56:24.710Z
