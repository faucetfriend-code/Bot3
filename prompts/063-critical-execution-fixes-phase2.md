<objective>
Implement Phase 2 critical fixes to complete the trade execution pipeline restoration. This phase focuses on ExecutionLayer integration and signal deduplication to achieve full system functionality.

This prompt implements:
- Fix #1: ExecutionLayer Integration Missing (CRITICAL) 
- Fix #4: Signal Duplication/Double Logging (MEDIUM)
</objective>

<context>
Phase 1 successfully implemented account balance validation and grid trading capital fixes. Now Phase 2 addresses the remaining critical issues:

Current Status After Phase 1:
- Account balance validation: ✅ FIXED (signals should pass validation)
- Grid trading capital issues: ✅ FIXED (grid trading should work)
- ExecutionLayer integration: ❌ STILL BROKEN (VWAP_SCALPING, MOMENTUM_SCALPING failing)
- Signal duplication: ❌ STILL BROKEN (inflated statistics, confusing metrics)

Phase 2 Target:
- Restore standard signal execution (VWAP_SCALPING, MOMENTUM_SCALPING, ORDER_BOOK_IMBALANCE, FUNDING_ARBITRAGE)
- Eliminate signal double logging for accurate metrics
- Complete the signal-to-execution pipeline

Files to modify:
- `trading_bot_v2/trading_bot.py` (ExecutionLayer integration)
- `trading_bot_v2/signal_logger.py` (signal deduplication)
- Following Python SDK patterns from "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3\Example files\utilities\python-sdk"
</context>

<requirements>
Implement the following fixes in the specified order:

**Fix #1: ExecutionLayer Integration Missing (CRITICAL)**
- Add error handling to ExecutionLayer initialization (lines 200-203)
- Add defensive check for ExecutionLayer availability (lines 1508-1520)
- Ensure proper method calls and fallback handling
- Fix any AttributeError issues with ExecutionLayer methods
- Add comprehensive logging for ExecutionLayer operations

**Fix #4: Signal Duplication/Double Logging (MEDIUM)**
- Add signal deduplication tracking to SignalLogger class
- Implement `_generate_signal_id()` method for unique signal identification
- Add `_is_duplicate()` method with 60-second deduplication window
- Modify `log_signal_generated()` to check for duplicates
- Modify `log_signal_rejected()` to handle duplicate rejections
- Simplify event handler to prevent double logging

**Code Quality Requirements:**
- Follow existing code patterns and style conventions
- Use existing logger instance with consistent emoji patterns
- Add comprehensive error handling with try/catch blocks
- Maintain backward compatibility for all existing functionality
- Include detailed logging for debugging and monitoring
- Use type hints and proper docstrings

**Performance Requirements:**
- Minimal overhead for signal deduplication (60-second window cleanup)
- No impact on signal generation speed
- Efficient memory usage for deduplication tracking
- Maintain existing signal processing throughput

**Integration Requirements:**
- Ensure ExecutionLayer works with all standard strategies
- Verify signal deduplication doesn't block legitimate signals
- Test with various signal types and edge cases
- Maintain compatibility with existing logging and monitoring
</requirements>

<implementation>
**Phase Order Implementation:**

1. **First implement ExecutionLayer Integration:**
   - Critical for standard signal execution (VWAP_SCALPING, etc.)
   - Low risk (defensive checks and error handling only)
   - Immediate impact on trading functionality

2. **Then implement Signal Deduplication:**
   - Cleans up metrics and logging
   - Low risk (logging changes only)
   - Improves system monitoring and debugging

**Key Implementation Notes:**

**ExecutionLayer Integration:**
- The ExecutionLayer class exists and has `refine_entry()` method
- Add defensive checks for method availability before calling
- Include proper error handling with fallback to original signal
- Add initialization error handling with graceful degradation
- Use existing logger patterns with descriptive messages

**Signal Deduplication:**
- Generate unique signal IDs from key fields (symbol, strategy, side, entry_price)
- Use Set-based tracking for O(1) lookup performance
- Implement automatic cleanup of old entries (60-second window)
- Don't block legitimate rejections - mark them as "Duplicate rejection"
- Maintain existing CSV format and logging structure

**Avoid These Issues:**
- Don't break existing signal flow or processing
- Don't change existing method signatures in SignalLogger
- Don't remove any existing logging functionality
- Don't hardcode signal ID generation - use flexible field extraction
- Don't block legitimate signal retries or rejections
</implementation>

<output>
Modify files with relative paths:
- `trading_bot_v2/trading_bot.py` - ExecutionLayer integration fixes
- `trading_bot_v2/signal_logger.py` - Signal deduplication implementation

**Specific Changes:**

**trading_bot.py:**
1. Lines 200-203: Add error handling to ExecutionLayer initialization
2. Lines 1508-1520: Add defensive check for ExecutionLayer availability

**signal_logger.py:**
1. Lines 19-60: Add deduplication tracking attributes and methods
2. Lines 93-132: Modify `log_signal_generated()` to check duplicates
3. Lines 134-175: Modify `log_signal_rejected()` to handle duplicates
4. Add `_generate_signal_id()` and `_is_duplicate()` helper methods
</output>

<verification>
Before declaring complete, verify your work:

1. **Code Quality Checks:**
   - Run `ruff check trading_bot_v2/trading_bot.py trading_bot_v2/signal_logger.py`
   - Run `mypy trading_bot_v2/trading_bot.py trading_bot_v2/signal_logger.py`
   - Ensure no syntax errors with `python -m py_compile`

2. **ExecutionLayer Verification:**
   ```python
   # Test ExecutionLayer initialization
   bot = TradingBot()
   assert hasattr(bot, 'execution_layer')
   assert bot.execution_layer is not None or bot.execution_layer is None  # Graceful degradation
   
   # Test defensive method calls
   signal = create_test_signal(strategy=StrategyType.VWAP_SCALPING)
   # Should not raise AttributeError
   ```

3. **Signal Deduplication Verification:**
   ```python
   # Test deduplication
   logger = SignalLogger()
   signal = create_test_signal()
   
   entry1 = logger.log_signal_generated(signal)
   entry2 = logger.log_signal_generated(signal)  # Should be empty
   
   assert entry1 != {}
   assert entry2 == {}
   ```

4. **Integration Tests:**
   - Test full signal flow with VWAP_SCALPING strategy
   - Verify ExecutionLayer refinement works correctly
   - Confirm signal deduplication prevents double logging
   - Check CSV output has 50% fewer duplicate entries

5. **Log Verification:**
   - Search for "ExecutionLayer initialized successfully"
   - Confirm no more AttributeError exceptions
   - Verify "Duplicate signal detected" debug messages
   - Check for "Duplicate rejection" notes in rejection logs

6. **Performance Tests:**
   - Verify signal processing speed is unchanged
   - Test deduplication memory usage stays reasonable
   - Confirm cleanup of old entries works properly
</verification>

<success_criteria>
The implementation is successful when:

1. **ExecutionLayer Integration:**
   - ✅ ExecutionLayer initializes successfully with error handling
   - ✅ Defensive checks prevent AttributeError exceptions
   - ✅ Standard signals (VWAP_SCALPING, MOMENTUM_SCALPING) execute successfully
   - ✅ Fallback to original signal works when ExecutionLayer unavailable
   - ✅ Comprehensive logging for debugging ExecutionLayer issues

2. **Signal Deduplication:**
   - ✅ Same signal logged only once as "generated"
   - ✅ Duplicate signals detected within 60-second window
   - ✅ Automatic cleanup of old entries prevents memory leaks
   - ✅ Rejections still logged but marked as "Duplicate rejection"
   - ✅ CSV file has 50% fewer duplicate entries

3. **Overall System:**
   - ✅ All 4 critical fixes implemented successfully
   - ✅ Signal execution rate improves from 0% to >10%
   - ✅ Standard strategies (VWAP_SCALPING, etc.) working correctly
   - ✅ Grid trading continues working from Phase 1
   - ✅ Clean, accurate signal metrics and logging
   - ✅ No breaking changes to existing functionality

**Expected Final System State:**
- Signal Generation: 100% ✅
- Signal Execution: >10% (up from 0%) 📈
- Balance Validation: >95% pass rate ✅
- Grid Trading: >80% success rate ✅
- Signal Deduplication: <5% duplicate rate ✅
- Error Log Entries: <5/hour (down from 50+/hour) ✅

**Ready for Production Deployment:**
All critical trade execution issues resolved with comprehensive error handling, logging, and monitoring. System should now execute trades successfully across all 8 strategies with accurate metrics tracking.
</success_criteria>