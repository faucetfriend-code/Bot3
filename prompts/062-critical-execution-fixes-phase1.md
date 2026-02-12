<objective>
Implement critical trade execution fixes to restore the trading bot's functionality. The bot currently has 28 signals generated but 0 executions due to multiple critical failures in the signal-to-execution pipeline.

This prompt implements Phase 1 critical fixes that unblock ALL trading activity:
- Fix #2: Account Balance Validation (unblocks all signals)
- Fix #3: Grid Trading Capital Issues (restores grid functionality)
</objective>

<context>
The trading bot has a complete execution pipeline breakdown with 0% execution rate despite generating 28 signals. The investigation identified 4 critical issues with the following impact:

Current Status:
- 28 signals generated ✅
- 0 executions (0% success) ❌ 
- 8 rejections due to "Invalid account balance (<=0)" ❌
- 6 failures due to grid trading capital issues ❌

Phase 1 fixes target the most impactful issues first:
1. Account Balance Validation - ALL signals blocked at validation stage
2. Grid Trading Capital Issues - Grid trading completely non-functional

These fixes follow the existing Python SDK patterns and best practices at "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3\Example files\utilities\python-sdk".

Files to modify:
- `trading_bot_v2/trading_bot.py` (main implementation)
- No imports needed - using existing codebase patterns
</context>

<requirements>
Implement the following fixes in the specified order:

**Fix #2: Account Balance Validation (HIGH PRIORITY)**
- Replace `_get_account_balance()` method (lines 1744-1768) with enhanced parsing
- Update balance validation logic (lines 1158-1164) with config support
- Add robust parsing for multiple balance field names
- Handle string/numeric values, commas, currency symbols
- Add environment variable support for testing

**Fix #3: Grid Trading Capital Issues (HIGH PRIORITY)**  
- Fix capital extraction in `_execute_grid_signal_coordinated()` (lines 1382-1387)
- Fix capital extraction in `_place_grid_orders()` (lines 1289-1296) 
- Make `_calculate_grid_levels()` accept both `capital` and `total_capital` parameters
- Add defensive check for `register_new_grid` method (lines 1326-1336)
- Add fallback keys for capital allocation parsing

**Code Quality Requirements:**
- Follow existing code style (snake_case, type hints, docstrings)
- Add comprehensive error handling with try/catch blocks
- Use existing logger instance (not logging module)
- Maintain backward compatibility
- Add defensive programming patterns
- Include detailed logging for debugging

**Performance Requirements:**
- No performance impact on signal generation
- Minimal overhead for balance parsing
- Maintain existing signal processing speed
</requirements>

<implementation>
**Phase Order Implementation:**

1. **First implement Account Balance Fix:**
   - This unblocks ALL trading immediately
   - Lowest risk (additive changes only)
   - Highest impact across all strategies

2. **Then implement Grid Trading Fix:**
   - Restores grid trading functionality
   - Medium risk (method signature changes)
   - Important for overall strategy coverage

**Key Implementation Notes:**
- Use `logger` instance, not `logging` module
- Follow existing error handling patterns with try/catch
- Maintain existing method signatures where possible
- Add backward compatibility for method parameters
- Use existing import statements and dependencies
- Include detailed logging for debugging (🔍, ✅, ❌, ⚠️ emojis)
- Test with various balance response formats
- Ensure no breaking changes to existing code

**Avoid These Issues:**
- Don't modify existing method signatures without backward compatibility
- Don't change existing imports
- Don't break existing test cases
- Don't remove existing functionality
- Don't hardcode values - use environment variables for configuration
</implementation>

<output>
Modify files with relative paths:
- `trading_bot_v2/trading_bot.py` - Enhanced balance parsing and grid capital fixes

**Specific Changes:**
1. Lines 1744-1768: Replace `_get_account_balance()` with robust parsing
2. Lines 1158-1164: Update balance validation with config support  
3. Lines 1382-1387: Fix capital extraction with fallback keys
4. Lines 1289-1296: Fix capital extraction with fallback keys
5. Lines 2056-2058: Make `_calculate_grid_levels()` accept both parameter names
6. Lines 1326-1336: Add defensive check for register_new_grid method
</output>

<verification>
Before declaring complete, verify your work:

1. **Code Quality Checks:**
   - Run `ruff check trading_bot_v2/trading_bot.py` for linting
   - Run `mypy trading_bot_v2/trading_bot.py` for type checking
   - Ensure no syntax errors with `python -m py_compile`

2. **Function Verification:**
   - Test `_get_account_balance()` with various response formats
   - Test `_calculate_grid_levels()` with both `capital` and `total_capital` parameters
   - Verify capital extraction works with different allocation result keys

3. **Integration Tests:**
   ```python
   # Test balance parsing
   bot = TradingBot()
   balance = bot._get_account_balance()
   assert isinstance(balance, float)
   
   # Test grid calculation compatibility
   signal = create_test_signal()
   levels1 = bot._calculate_grid_levels(signal, total_capital=1000)
   levels2 = bot._calculate_grid_levels(signal, capital=1000)
   assert levels1 == levels2
   ```

4. **Log Verification:**
   - Check for "🔍 Raw balance response" in debug output
   - Verify no more "Invalid account balance" errors when balance > 0
   - Confirm "💰 Capital allocated for grid" appears with positive amounts

5. **Success Metrics:**
   - Balance validation should pass with positive account equity
   - Grid capital extraction should work with multiple key formats
   - No more "No capital allocated" errors in logs
</verification>

<success_criteria>
The implementation is successful when:

1. **Account Balance Fix:**
   - ✅ Balance parsing works with multiple field names (balance, account_equity, equity, etc.)
   - ✅ Handles string values with commas and currency symbols
   - ✅ Supports environment variable BYPASS_BALANCE_VALIDATION for testing
   - ✅ Zero rejections due to "Invalid account balance (<=0)" when actual balance > 0

2. **Grid Trading Fix:**
   - ✅ Capital extraction works with allocation result keys (allocated_amount, capital_allocated, etc.)
   - ✅ `_calculate_grid_levels()` accepts both `capital` and `total_capital` parameters
   - ✅ Defensive check prevents register_new_grid errors
   - ✅ No more "No capital allocated" errors in grid execution

3. **Overall System:**
   - ✅ All modifications follow existing code patterns and style
   - ✅ No breaking changes to existing functionality
   - ✅ Enhanced error handling and logging throughout
   - ✅ Ready for Phase 2 implementation (ExecutionLayer integration)

**Expected Impact After This Phase:**
- Signal execution rate: 0% → >10% (unblocked by balance validation)
- Grid trading functionality: 0% → >80% success rate
- Error reduction: 50% fewer critical errors in logs
</success_criteria>