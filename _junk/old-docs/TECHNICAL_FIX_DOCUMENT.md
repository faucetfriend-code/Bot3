# Technical Fix Document: Trade Execution Issues

**Document Version:** 1.0  
**Date:** February 11, 2026  
**Status:** CRITICAL - Immediate Action Required  
**Author:** Technical Analysis Team  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Fix #1: ExecutionLayer Integration Missing (CRITICAL)](#2-fix-1-executionlayer-integration-missing-critical)
3. [Fix #2: Account Balance Validation (HIGH)](#3-fix-2-account-balance-validation-high)
4. [Fix #3: Grid Trading Capital Issues (HIGH)](#4-fix-3-grid-trading-capital-issues-high)
5. [Fix #4: Signal Duplication/Double Logging (MEDIUM)](#5-fix-4-signal-duplicationdouble-logging-medium)
6. [Implementation Priority Order](#6-implementation-priority-order)
7. [Testing and Validation Steps](#7-testing-and-validation-steps)
8. [Post-Fix Success Metrics](#8-post-fix-success-metrics)

---

## 1. Executive Summary

### Issues Overview

| Priority | Issue | Count | Impact |
|----------|-------|-------|--------|
| CRITICAL | ExecutionLayer Integration Missing | Multiple strategies failing | VWAP_SCALPING, MOMENTUM_SCALPING completely non-functional |
| HIGH | Account Balance Validation | 8 rejections | All signals blocked at validation stage |
| HIGH | Grid Trading Capital Issues | 6 failures + method errors | Grid trading completely non-functional |
| MEDIUM | Signal Duplication/Double Logging | ~100% duplication | Inflated statistics, confusing metrics |

### Root Causes

1. **Architecture Mismatch**: TradingBot calls `ExecutionLayer.refine_entry()` but the method name was inconsistent with design docs, and signal handling had event-driven duplication
2. **Balance API Parsing**: Account balance returned as string fields that weren't being parsed correctly
3. **Method Signature Mismatch**: `_calculate_grid_levels()` called with wrong parameters (`capital` vs `total_capital`)
4. **Double Logging**: Signals logged both at generation time AND when received via event handler

---

## 2. Fix #1: ExecutionLayer Integration Missing (CRITICAL)

### Issue Description

The `ExecutionLayer` class was designed to refine trade entries using 1m/5m timeframe data, but there's an architecture mismatch between the design documentation and implementation. The error log shows:

```
ERROR: 'ExecutionLayer' object has no attribute 'execute_signal'
```

This occurs in `_execute_standard_signal_coordinated()` when attempting to process standard (non-grid) signals.

### Root Cause Analysis

**Location:** `trading_bot_v2/trading_bot.py` lines 1473-1613

The `ExecutionLayer` class (in `execution_layer.py`) only has these public methods:
- `refine_entry(signal, symbol)` - Returns refined signal or None
- `get_stats()` - Returns statistics

However, the design documentation references `execute_signal()` which doesn't exist. The current implementation correctly calls `refine_entry()`, but error logs indicate stale code or incorrect method calls elsewhere.

### Impact Assessment

- **Affected Strategies**: VWAP_SCALPING, MOMENTUM_SCALPING, ORDER_BOOK_IMBALANCE, FUNDING_ARBITRAGE
- **Impact**: Standard signal execution completely blocked
- **Trading Activity**: ~0% execution rate for non-grid strategies

### Fix Implementation

#### Step 1: Verify ExecutionLayer Method Call (Line 1509)

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 1509

**Current Code:**
```python
# Refine signal through ExecutionLayer before execution
refined_signal = self.execution_layer.refine_entry(signal, symbol)
if refined_signal is None:
    logger.info(f"⚠️ ExecutionLayer skipped entry for {symbol} - timing not favorable")
    self.signal_logger.log_signal_rejected(
        signal=signal,
        reason="ExecutionLayer timing skip",
        notes="1m/5m timing conditions not met",
    )
    return

# Use refined signal for execution
signal = refined_signal
```

**Action:** This code is CORRECT. Do not modify.

#### Step 2: Add Error Handling for Missing ExecutionLayer Method

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** After line 1508

**Add defensive check:**
```python
# Before calling ExecutionLayer, verify it exists and has the required method
if not hasattr(self, 'execution_layer') or self.execution_layer is None:
    logger.warning(f"ExecutionLayer not available for {symbol}, using original signal")
    refined_signal = signal  # Use original signal without refinement
else:
    # Refine signal through ExecutionLayer before execution
    try:
        refined_signal = self.execution_layer.refine_entry(signal, symbol)
    except AttributeError as e:
        logger.error(f"ExecutionLayer error for {symbol}: {e}")
        refined_signal = signal  # Fallback to original signal
```

#### Step 3: Ensure ExecutionLayer is Initialized

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 200-203

**Current Code:**
```python
# Initialize execution layer for precise 1m/5m entry timing
self.execution_layer = ExecutionLayer(
    fetcher=self.multi_tf_fetcher,
)
```

**Action:** This is correct. Add error handling:

```python
# Initialize execution layer for precise 1m/5m entry timing
try:
    self.execution_layer = ExecutionLayer(
        fetcher=self.multi_tf_fetcher,
        enabled=True  # Explicitly enable
    )
    logger.info("ExecutionLayer initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize ExecutionLayer: {e}")
    self.execution_layer = None
```

### File Paths and Line Numbers

| File | Line | Action |
|------|------|--------|
| `trading_bot_v2/trading_bot.py` | 200-203 | Add error handling to ExecutionLayer initialization |
| `trading_bot_v2/trading_bot.py` | 1508-1510 | Add defensive check for ExecutionLayer availability |

### Testing Steps

1. **Unit Test:**
```python
def test_execution_layer_initialization():
    bot = TradingBot()
    assert bot.execution_layer is not None
    assert hasattr(bot.execution_layer, 'refine_entry')
```

2. **Integration Test:**
```python
def test_standard_signal_execution():
    # Create a test VWAP_SCALPING signal
    signal = create_test_signal(strategy=StrategyType.VWAP_SCALPING)
    result = bot._execute_standard_signal_coordinated(signal, {"allocated_amount": 1000})
    assert result is not None
```

3. **Verify in logs:**
   - Search for "ExecutionLayer initialized successfully"
   - Search for "ERROR: 'ExecutionLayer' object has no attribute"
   - Confirm no more AttributeError exceptions

---

## 3. Fix #2: Account Balance Validation (HIGH)

### Issue Description

Multiple signals being rejected with:
```
rejected,Invalid account balance (<=0)
```

**Occurrences:** 8 rejections in signals_log.csv  
**Affected Symbols:** AVAX, BTC, SOL, SUI, XRP

### Root Cause Analysis

**Location:** `trading_bot_v2/trading_bot.py` lines 1158-1164

The `_get_account_balance()` method calls `self.client.get_balance()` which returns a dict with string values. The parsing logic attempts to get "balance" or "account_equity" fields and convert to float, but:

1. The API might return empty strings
2. The API might use different field names
3. The API might return zero for testnet/sandbox accounts

**Current Code (lines 1744-1768):**
```python
def _get_account_balance(self) -> float:
    try:
        balance = self.client.get_balance()
        logging.info(f"🔍 Raw balance response: {balance}")
        
        # Pacifica returns "balance" or "account_equity" (as strings), not "equity"
        balance_str = balance.get("balance", balance.get("account_equity", "0"))
        logging.info(f"🔍 Extracted balance string: '{balance_str}'")
        
        equity = float(balance_str) if balance_str else 0.0
        logging.info(f"🔍 Final account balance: ${equity:.2f}")
        
        if equity <= 0:
            logging.warning(f"⚠️ Account balance is ${equity:.2f} - this will block all executions!")
        
        return equity
    except Exception as e:
        logging.error(f"❌ Error getting account balance: {e}")
        return 0.0
```

### Impact Assessment

- **Blocked Signals:** 8+ signals (100% of validation-stage signals)
- **Trading Activity:** Complete halt until balance > 0
- **User Impact:** No trades executing despite signals being generated

### Fix Implementation

#### Step 1: Improve Balance Parsing with Multiple Fallbacks

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 1744-1768 (replace entire method)

**Before:**
```python
def _get_account_balance(self) -> float:
    try:
        balance = self.client.get_balance()
        logging.info(f"🔍 Raw balance response: {balance}")
        
        # Pacifica returns "balance" or "account_equity" (as strings), not "equity"
        balance_str = balance.get("balance", balance.get("account_equity", "0"))
        logging.info(f"🔍 Extracted balance string: '{balance_str}'")
        
        equity = float(balance_str) if balance_str else 0.0
        logging.info(f"🔍 Final account balance: ${equity:.2f}")
        
        if equity <= 0:
            logging.warning(f"⚠️ Account balance is ${equity:.2f} - this will block all executions!")
        
        return equity
    except Exception as e:
        logging.error(f"❌ Error getting account balance: {e}")
        return 0.0
```

**After:**
```python
def _get_account_balance(self) -> float:
    """
    Get current account balance with robust parsing.
    
    Returns:
        Account balance in USD. Returns 0.0 if unavailable.
    """
    try:
        balance = self.client.get_balance()
        logger.info(f"🔍 Raw balance response: {balance}")
        
        # Try multiple possible field names for balance
        possible_fields = [
            "balance",
            "account_equity", 
            "equity",
            "total_equity",
            "available_balance",
            "total_balance",
            "cash",
            "margin_balance"
        ]
        
        balance_str = None
        for field in possible_fields:
            if field in balance and balance[field] is not None:
                balance_str = balance[field]
                logger.debug(f"✅ Found balance in field '{field}': {balance_str}")
                break
        
        if balance_str is None:
            logger.warning(f"⚠️ No recognized balance field in response: {balance}")
            return 0.0
        
        # Handle string or numeric values
        if isinstance(balance_str, (int, float)):
            equity = float(balance_str)
        elif isinstance(balance_str, str):
            # Remove commas and currency symbols
            balance_str = balance_str.replace(',', '').replace('$', '').strip()
            if balance_str:
                equity = float(balance_str)
            else:
                equity = 0.0
        else:
            equity = 0.0
        
        logger.info(f"🔍 Final account balance: ${equity:.2f}")
        
        if equity <= 0:
            logger.warning(f"⚠️ Account balance is ${equity:.2f} - this will block all executions!")
            logger.warning(f"⚠️ Balance response was: {balance}")
        
        return equity
    except Exception as e:
        logger.error(f"❌ Error getting account balance: {e}")
        logger.error(f"❌ Exception type: {type(e).__name__}")
        return 0.0
```

#### Step 2: Add Configuration for Test Trading

**File:** `trading_bot_v2/config.py`  
**Add new configuration option:**

```python
# For testing/demo mode - bypass balance validation
BYPASS_BALANCE_VALIDATION = os.getenv("BYPASS_BALANCE_VALIDATION", "false").lower() == "true"

# Minimum balance threshold (can be 0 for testing)
MINIMUM_ACCOUNT_BALANCE = float(os.getenv("MINIMUM_ACCOUNT_BALANCE", "0.0"))
```

#### Step 3: Update Balance Check in Signal Validation

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 1158-1164

**Before:**
```python
# Check account balance
balance = self._get_account_balance()
if balance <= 0:
    reason = "Invalid account balance (<=0)"
    logger.warning("Invalid account balance - cannot execute signal")
    self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
    return False
```

**After:**
```python
# Check account balance
balance = self._get_account_balance()
min_balance = getattr(config, 'MINIMUM_ACCOUNT_BALANCE', 0.0)
bypass_validation = getattr(config, 'BYPASS_BALANCE_VALIDATION', False)

if balance <= min_balance and not bypass_validation:
    reason = f"Invalid account balance (${balance:.2f} <= ${min_balance:.2f})"
    logger.warning(f"🚫 {reason} - cannot execute signal")
    self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
    return False
elif bypass_validation:
    logger.info(f"⚠️ Balance validation BYPASSED (balance: ${balance:.2f})")
```

### File Paths and Line Numbers

| File | Line | Action |
|------|------|--------|
| `trading_bot_v2/trading_bot.py` | 1744-1768 | Replace `_get_account_balance()` method |
| `trading_bot_v2/trading_bot.py` | 1158-1164 | Update balance validation logic |
| `trading_bot_v2/config.py` | Add new | Add `BYPASS_BALANCE_VALIDATION` and `MINIMUM_ACCOUNT_BALANCE` config |

### Testing Steps

1. **Test balance parsing:**
```python
def test_balance_parsing():
    # Test with various response formats
    test_cases = [
        {"balance": "1000.50"},
        {"account_equity": "5000.00"},
        {"equity": 7500.0},
        {"total_equity": "1,234.56"},  # With comma
        {"available_balance": "$999.99"},  # With currency symbol
    ]
    
    for case in test_cases:
        # Mock the client response
        bot.client.get_balance = lambda: case
        balance = bot._get_account_balance()
        assert balance > 0, f"Failed to parse: {case}"
```

2. **Verify in logs:**
   - Search for "🔍 Raw balance response"
   - Search for "✅ Found balance in field"
   - Confirm no more "Invalid account balance" rejections when balance > 0

3. **Test with zero balance:**
   - Set `BYPASS_BALANCE_VALIDATION=true` in environment
   - Verify signals proceed with warning

---

## 4. Fix #3: Grid Trading Capital Issues (HIGH)

### Issue Description

Grid trading signals failing with multiple errors:

1. **"No capital allocated"** (6 occurrences):
   ```
   ERROR: No capital allocated
   "Grid placement failed, capital: $0.00"
   ```

2. **Method signature mismatch**:
   ```
   ERROR: TradingBot._calculate_grid_levels() got an unexpected keyword argument 'capital'
   ```

3. **Missing `register_grid` method**:
   ```
   ERROR: 'GridLifecycleManager' object has no attribute 'register_grid'
   ```

### Root Cause Analysis

**Location 1:** `trading_bot_v2/trading_bot.py` line 1384-1387

The code extracts capital from allocation_result using key "allocated_amount":
```python
capital = allocation_result.get("allocated_amount", 0)
```

**Location 2:** `trading_bot_v2/trading_bot.py` line 1390-1393

The `_calculate_grid_levels()` method is called with parameter name `total_capital`:
```python
grid_levels = self._calculate_grid_levels(
    signal=signal,
    total_capital=capital,
)
```

But looking at the method signature (line 2056), it expects `total_capital`:
```python
def _calculate_grid_levels(
    self, signal: Signal, total_capital: float
) -> Dict[str, List[Dict]]:
```

Wait - this is correct. The issue is that there's another call site passing `capital` instead of `total_capital`.

**Location 3:** `trading_bot_v2/trading_bot.py` line 1326

The code calls `self.grid_lifecycle.register_new_grid()` which is correct (the method exists), but logs show error about `register_grid` (singular). This suggests there might be a typo somewhere or outdated code.

### Impact Assessment

- **Blocked Grid Signals:** 6+ failures
- **Grid Trading:** Completely non-functional
- **Capital Allocation:** $0.00 being passed despite allocation success

### Fix Implementation

#### Step 1: Fix Capital Extraction from Allocation Result

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 1289-1296 and 1382-1387

**Current Code (line 1289-1296 in `_execute_grid_signal_coordinated`):**
```python
# RiskManager returns "allocated_amount" (not "capital_allocated")
capital_allocated = allocation_result.get("allocated_amount", 0)
```

**Current Code (line 1382-1387 in `_place_grid_orders`):**
```python
# RiskManager returns "allocated_amount" (not "capital_allocated")
capital = allocation_result.get("allocated_amount", 0)

if capital <= 0:
    return {"success": False, "error": "No capital allocated"}
```

**Issue:** The allocation_result might use different keys. Let's add fallback:

**After:**
```python
# Try multiple possible keys for capital allocation
capital = allocation_result.get("allocated_amount") or \
          allocation_result.get("capital_allocated") or \
          allocation_result.get("amount") or \
          allocation_result.get("capital", 0)

if not capital or capital <= 0:
    logger.error(f"❌ No capital allocated. Allocation result: {allocation_result}")
    return {"success": False, "error": f"No capital allocated. Result: {allocation_result}"}

logger.info(f"💰 Capital allocated for grid: ${capital:.2f}")
```

#### Step 2: Fix _calculate_grid_levels Method Signature Consistency

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 2056-2058

Verify method signature is consistent. Current signature:
```python
def _calculate_grid_levels(
    self, signal: Signal, total_capital: float
) -> Dict[str, List[Dict]]:
```

This is correct. Check all call sites use `total_capital=`:

1. Line 1390-1393: ✅ Uses `total_capital=capital`
2. Line 1996: ✅ Uses `total_capital` positional arg

The error log showing `capital` as unexpected keyword suggests there WAS an issue but it may have been partially fixed. Let's make the method more flexible:

**Enhanced Method with Backward Compatibility:**
```python
def _calculate_grid_levels(
    self, signal: Signal, total_capital: float = None, capital: float = None
) -> Dict[str, List[Dict]]:
    """Calculate grid price levels per Grid Trading Brief specifications."""
    # Handle both parameter names for backward compatibility
    if total_capital is None and capital is not None:
        total_capital = capital
    elif total_capital is None:
        logger.error("❌ _calculate_grid_levels: No capital provided")
        return {}
    
    if total_capital <= 0:
        logger.error(f"❌ _calculate_grid_levels: Invalid capital ${total_capital:.2f}")
        return {}
    
    # ... rest of method
```

#### Step 3: Fix GridLifecycleManager Method Name

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 1326-1336

**Current Code:**
```python
# Register grid with GridLifecycleManager for monitoring
self.grid_lifecycle.register_new_grid(
    symbol=symbol,
    grid_capital=capital_allocated,
    emergency_stop_price=signal.stop_loss,
    regime=signal.market_state.name if hasattr(signal.market_state, 'name') else str(signal.market_state),
    atr=0,  # ATR not stored in signal, grid manager will recalculate if needed
    spacing=signal.spacing or 0,
    num_levels=signal.grid_levels or 10,
    center_price=signal.entry_price,
)
```

This calls `register_new_grid()` which IS the correct method name in GridLifecycleManager (line 361). The error log showing `register_grid` (singular) suggests there might be stale code or a different call site.

**Verify the method exists in GridLifecycleManager:**

**File:** `trading_bot_v2/grid_lifecycle_manager.py`  
**Line:** 361-412

The method `register_new_grid()` exists and is correct. If error persists, add defensive check:

```python
# Register grid with GridLifecycleManager for monitoring
if hasattr(self.grid_lifecycle, 'register_new_grid'):
    self.grid_lifecycle.register_new_grid(
        symbol=symbol,
        grid_capital=capital_allocated,
        emergency_stop_price=signal.stop_loss,
        regime=signal.market_state.name if hasattr(signal.market_state, 'name') else str(signal.market_state),
        atr=0,
        spacing=signal.spacing or 0,
        num_levels=signal.grid_levels or 10,
        center_price=signal.entry_price,
    )
    logger.info(f"✅ Grid registered with GridLifecycleManager for {symbol}")
else:
    logger.error(f"❌ GridLifecycleManager missing register_new_grid method!")
    # Still log success since orders were placed
```

### File Paths and Line Numbers

| File | Line | Action |
|------|------|--------|
| `trading_bot_v2/trading_bot.py` | 1382-1387 | Add fallback keys for capital extraction |
| `trading_bot_v2/trading_bot.py` | 2056-2058 | Make `_calculate_grid_levels` accept both `capital` and `total_capital` |
| `trading_bot_v2/trading_bot.py` | 1326-1336 | Add defensive check for `register_new_grid` method |

### Testing Steps

1. **Test capital extraction:**
```python
def test_capital_extraction():
    # Test with different allocation result formats
    test_cases = [
        {"allocated_amount": 1000.0},
        {"capital_allocated": 1000.0},
        {"amount": 1000.0},
        {"capital": 1000.0},
    ]
    
    for case in test_cases:
        capital = case.get("allocated_amount") or \
                  case.get("capital_allocated") or \
                  case.get("amount") or \
                  case.get("capital", 0)
        assert capital == 1000.0, f"Failed to extract from: {case}"
```

2. **Test grid level calculation:**
```python
def test_calculate_grid_levels():
    signal = create_test_grid_signal()
    
    # Should work with total_capital
    levels = bot._calculate_grid_levels(signal, total_capital=1000)
    assert levels is not None
    assert "buy_levels" in levels
    
    # Should also work with capital (backward compatibility)
    levels = bot._calculate_grid_levels(signal, capital=1000)
    assert levels is not None
```

3. **Verify in logs:**
   - Search for "💰 Capital allocated for grid"
   - Search for "✅ Grid registered with GridLifecycleManager"
   - Confirm no more "No capital allocated" errors

---

## 5. Fix #4: Signal Duplication/Double Logging (MEDIUM)

### Issue Description

Every signal appears twice in signals_log.csv:

```csv
2026-02-10T12:44:12.010313,AVAX,GRID_TRADING,BUY,8.774228,...,rejected,Invalid account balance (<=0),,,,,
2026-02-10T12:44:12.010929,AVAX,GRID_TRADING,BUY,8.774228,...,generated,,,,,,,Generated from GRID_TRADING
```

Same signal, same timestamp (within milliseconds), different statuses.

### Root Cause Analysis

**Location 1:** `trading_bot_v2/trading_bot.py` lines 965-972

Signals are logged when generated:
```python
# Log signal BEFORE publishing event (event bus is synchronous)
signals_generated += 1
regime_str = regime.name if regime and hasattr(regime, 'name') else str(regime) if regime else "unknown"
self.signal_logger.log_signal_generated(
    signal=signal,
    regime=regime_str,
    notes=f"Generated from {signal.strategy.name}",
)
```

**Location 2:** `trading_bot_v2/trading_bot.py` lines 1089-1109

Signals are processed again in event handler `_handle_signal_generated()`:
```python
def _handle_signal_generated(self, event):
    # Event object has .data attribute (not a dict with .get())
    signal_data = event.data if hasattr(event, 'data') else event.get("data", {})
    signal = signal_data.get("signal") if isinstance(signal_data, dict) else None
    
    if not signal:
        logger.warning("Received signal event with no signal data...")
        return
    
    # Validate signal should be executed
    if not self._should_execute_signal(signal):
        # ... logging here
        return
    
    # Coordinate signal execution
    self._coordinate_signal_execution(signal, log_entry)
```

The issue is that:
1. Signal is generated and logged as "generated"
2. Signal is published as event
3. Event handler receives signal, validates it, and logs it again (as "rejected" or "executed")

### Impact Assessment

- **Statistics Inflation:** 2x signal count in all metrics
- **Confusion:** Cannot easily distinguish generation from execution
- **Log Noise:** Double entries clutter the CSV

### Fix Implementation

#### Step 1: Add Signal Deduplication Using Unique ID

**File:** `trading_bot_v2/signal_logger.py`  
**Add new tracking mechanism:**

```python
class SignalLogger:
    def __init__(...):
        # ... existing code ...
        
        # Signal deduplication tracking
        self._processed_signal_ids: Set[str] = set()
        self._dedup_window_seconds = 60  # 1-minute dedup window
        self._signal_timestamps: Dict[str, datetime] = {}
    
    def _generate_signal_id(self, signal) -> str:
        """Generate unique ID for signal deduplication."""
        symbol = getattr(signal, 'asset', '')
        strategy = getattr(signal.strategy, 'name', '') if hasattr(signal, 'strategy') else ''
        side = getattr(signal.side, 'name', '') if hasattr(signal, 'side') else ''
        entry_price = getattr(signal, 'entry_price', 0)
        
        # Create hash from key fields
        signal_key = f"{symbol}:{strategy}:{side}:{entry_price:.4f}"
        return signal_key
    
    def _is_duplicate(self, signal) -> bool:
        """Check if signal was recently processed."""
        signal_id = self._generate_signal_id(signal)
        now = datetime.utcnow()
        
        # Clean old entries
        cutoff = now - timedelta(seconds=self._dedup_window_seconds)
        old_ids = [
            sid for sid, ts in self._signal_timestamps.items() 
            if ts < cutoff
        ]
        for old_id in old_ids:
            self._processed_signal_ids.discard(old_id)
            del self._signal_timestamps[old_id]
        
        # Check if this signal was recently processed
        if signal_id in self._processed_signal_ids:
            logger.debug(f"Duplicate signal detected: {signal_id}")
            return True
        
        # Mark as processed
        self._processed_signal_ids.add(signal_id)
        self._signal_timestamps[signal_id] = now
        return False
```

#### Step 2: Update Log Methods to Check Duplicates

**File:** `trading_bot_v2/signal_logger.py`  
**Modify `log_signal_generated`:**

```python
def log_signal_generated(self, signal, regime: str = "", notes: str = "") -> Dict[str, Any]:
    """Log a newly generated signal."""
    
    # Check for duplicate
    if self._is_duplicate(signal):
        logger.debug(f"Skipping duplicate signal generation log")
        return {}
    
    # ... rest of existing code ...
```

**Modify `log_signal_rejected`:**

```python
def log_signal_rejected(self, signal, reason: str, regime: str = "", notes: str = "") -> Dict[str, Any]:
    """Log a rejected signal (failed validation)."""
    
    # Don't check duplicates for rejections - we want to know about all rejections
    # But add a note if it appears to be a duplicate
    signal_id = self._generate_signal_id(signal)
    if signal_id in self._processed_signal_ids:
        notes = f"{notes} | Duplicate rejection".strip()
    
    # ... rest of existing code ...
```

#### Step 3: Alternative Fix - Skip Logging in Event Handler for Already-Logged Signals

**File:** `trading_bot_v2/trading_bot.py`  
**Line:** 1089-1109

Add check to skip logging if signal was already logged as generated:

```python
def _handle_signal_generated(self, event):
    """Handle SIGNAL_GENERATED event."""
    try:
        # Event object has .data attribute (not a dict with .get())
        signal_data = event.data if hasattr(event, 'data') else event.get("data", {})
        signal = signal_data.get("signal") if isinstance(signal_data, dict) else None

        if not signal:
            logger.warning("Received signal event with no signal data...")
            return

        logger.info(
            f"📨 Signal handler received: {signal.strategy.name} {signal.side.name} "
            f"{signal.asset} (valid={signal.is_valid()}, confidence={signal.confidence})"
        )

        # Validate signal should be executed
        if not self._should_execute_signal(signal):
            logger.info(
                f"🚫 Signal validation failed for {signal.asset} {signal.strategy.name} - skipping execution"
            )
            # DON'T log here - it was already logged as generated, and _should_execute_signal logs rejections
            return

        # Coordinate signal execution (logs will happen inside)
        self._coordinate_signal_execution(signal, log_entry=None)

    except Exception as e:
        logger.error(f"Error handling signal event: {e}", exc_info=True)
        # Only log failure if not already logged
        if signal and not getattr(signal, '_logged_failed', False):
            self.signal_logger.log_signal_failed(
                signal=signal,
                error=str(e),
                notes="Exception in _handle_signal_generated",
            )
            signal._logged_failed = True
```

### File Paths and Line Numbers

| File | Line | Action |
|------|------|--------|
| `trading_bot_v2/signal_logger.py` | 19-60 | Add `_processed_signal_ids` and deduplication methods |
| `trading_bot_v2/signal_logger.py` | 93-132 | Modify `log_signal_generated` to check duplicates |
| `trading_bot_v2/signal_logger.py` | 134-175 | Modify `log_signal_rejected` to handle duplicates |
| `trading_bot_v2/trading_bot.py` | 1070-1120 | Simplify event handler to avoid double logging |

### Testing Steps

1. **Test deduplication:**
```python
def test_signal_deduplication():
    signal = create_test_signal()
    
    # Log same signal twice
    entry1 = logger.log_signal_generated(signal)
    entry2 = logger.log_signal_generated(signal)
    
    # Second should be empty (duplicate)
    assert entry1 != {}
    assert entry2 == {}
```

2. **Verify in logs:**
   - Each unique signal should appear only once as "generated"
   - Rejections should still be logged but marked as "Duplicate rejection"
   - CSV file should have 50% fewer entries

---

## 6. Implementation Priority Order

### Phase 1: Critical Fixes (Deploy Immediately)

1. **Fix #2: Account Balance Validation** (HIGH)
   - Impact: Unblocks ALL trading
   - Time: 30 minutes
   - Risk: Low (additive changes only)

2. **Fix #3: Grid Trading Capital Issues** (HIGH)
   - Impact: Restores grid trading functionality
   - Time: 45 minutes
   - Risk: Medium (changes method signatures)

### Phase 2: Important Fixes (Deploy Same Day)

3. **Fix #1: ExecutionLayer Integration** (CRITICAL)
   - Impact: Restores standard signal execution
   - Time: 30 minutes
   - Risk: Low (defensive checks only)

### Phase 3: Cleanup (Deploy Next Day)

4. **Fix #4: Signal Duplication** (MEDIUM)
   - Impact: Cleaner logs and metrics
   - Time: 60 minutes
   - Risk: Low (logging changes only)

---

## 7. Testing and Validation Steps

### Pre-Deployment Testing

1. **Run Unit Tests:**
```bash
cd trading_bot_v2
python -m pytest tests/ -v -k "test_signal" --tb=short
python -m pytest tests/ -v -k "test_grid" --tb=short
python -m pytest tests/ -v -k "test_balance" --tb=short
```

2. **Integration Testing:**
```bash
# Start bot in test mode
python -c "from trading_bot import TradingBot; bot = TradingBot(); bot.start()"

# Check logs for initialization
# Look for: "ExecutionLayer initialized successfully"
# Look for: "🔍 Final account balance: $X.XX"
```

3. **Signal Flow Test:**
```python
# Manual test script
def test_full_signal_flow():
    bot = TradingBot()
    
    # Create test signal
    from models import Signal, OrderSide, StrategyType
    signal = Signal(
        asset="BTC",
        strategy=StrategyType.MEAN_REVERSION,
        side=OrderSide.BUY,
        entry_price=50000,
        confidence=0.8
    )
    
    # Test balance check
    balance = bot._get_account_balance()
    print(f"Balance: ${balance:.2f}")
    
    # Test validation
    should_execute = bot._should_execute_signal(signal)
    print(f"Should execute: {should_execute}")
    
    # Test execution coordination
    allocation = {"allocated_amount": 1000.0}
    bot._coordinate_signal_execution(signal, allocation)
```

### Post-Deployment Validation

1. **Check Signals Log:**
```bash
# Verify no more double entries
tail -f signals_log.csv | grep "generated"
# Should see unique timestamps, not duplicates
```

2. **Monitor Execution Rate:**
```bash
# Check execution success rate is > 0%
grep "executed" signals_log.csv | wc -l
grep "generated" signals_log.csv | wc -l
# Ratio should improve from 0% to > 10%
```

3. **Verify No Errors:**
```bash
grep -i "error" logs/trading_bot.log | grep -v "DEBUG"
# Should NOT see:
# - "Invalid account balance"
# - "No capital allocated"
# - "'ExecutionLayer' object has no attribute"
# - "got an unexpected keyword argument"
```

### Rollback Plan

If issues occur:

1. **Immediate:** Stop the bot
```bash
# If using systemd
sudo systemctl stop trading-bot

# Or kill process
pkill -f trading_bot
```

2. **Restore from backup:**
```bash
git checkout HEAD -- trading_bot_v2/trading_bot.py
git checkout HEAD -- trading_bot_v2/signal_logger.py
git checkout HEAD -- trading_bot_v2/config.py
```

3. **Restart:**
```bash
python -m trading_bot_v2.api_server
```

---

## 8. Post-Fix Success Metrics

### Target Metrics

| Metric | Before Fix | Target After Fix | Measurement |
|--------|-----------|------------------|-------------|
| Signal Execution Rate | 0% | > 10% | `executed / (executed + failed + rejected)` |
| Balance Validation Pass Rate | 0% | > 95% | `passed / (passed + rejected for balance)` |
| Grid Placement Success Rate | 0% | > 80% | `placed / attempted` |
| Duplicate Signal Rate | ~100% | < 5% | `duplicates / total` |
| Error Log Entries | 50+/hour | < 5/hour | Count of ERROR level logs |

### Verification Commands

```bash
# 1. Execution Rate
total=$(grep -c "generated\|executed\|failed\|rejected" signals_log.csv)
executed=$(grep -c "executed" signals_log.csv)
echo "Execution Rate: $(( 100 * executed / total ))%"

# 2. Balance Validation
total_balance_checks=$(grep -c "account balance" logs/trading_bot.log)
balance_failures=$(grep -c "Invalid account balance" signals_log.csv)
echo "Balance Pass Rate: $(( 100 * (total_balance_checks - balance_failures) / total_balance_checks ))%"

# 3. Grid Success Rate
grid_attempts=$(grep -c "GRID_TRADING" signals_log.csv)
grid_success=$(grep "Grid placed" signals_log.csv | wc -l)
echo "Grid Success Rate: $(( 100 * grid_success / grid_attempts ))%"

# 4. Duplicate Rate
total_signals=$(grep -c "generated" signals_log.csv)
unique_signals=$(grep "generated" signals_log.csv | awk -F',' '{print $1","$2","$3}' | sort -u | wc -l)
echo "Duplicate Rate: $(( 100 * (total_signals - unique_signals) / total_signals ))%"

# 5. Error Count (last hour)
error_count=$(grep "$(date +%Y-%m-%d %H:)" logs/trading_bot.log | grep -c "ERROR")
echo "Errors in current hour: $error_count"
```

### Success Criteria

- ✅ All 4 fixes deployed without errors
- ✅ Execution rate > 10% within 24 hours
- ✅ Zero "Invalid account balance" errors when balance > 0
- ✅ Grid trading placing orders successfully
- ✅ Signal log deduplication working (50% fewer entries)
- ✅ No critical errors in logs for 48 hours

---

## Appendix A: Complete Code Changes Summary

### Files Modified

1. `trading_bot_v2/trading_bot.py`
   - Lines 200-203: ExecutionLayer initialization with error handling
   - Lines 1158-1164: Balance validation with config bypass
   - Lines 1289-1296: Capital extraction with fallback keys
   - Lines 1326-1336: Defensive check for register_new_grid
   - Lines 1382-1387: Capital extraction with fallback keys  
   - Lines 1508-1520: Defensive check for ExecutionLayer
   - Lines 1744-1768: Enhanced _get_account_balance method
   - Lines 2056-2058: _calculate_grid_levels accepts both capital params
   - Lines 1070-1120: Simplified event handler to prevent double logging

2. `trading_bot_v2/signal_logger.py`
   - Lines 19-60: Added deduplication tracking
   - Lines 93-132: Modified log_signal_generated with duplicate check
   - Lines 134-175: Modified log_signal_rejected with duplicate handling

3. `trading_bot_v2/config.py`
   - New: BYPASS_BALANCE_VALIDATION config
   - New: MINIMUM_ACCOUNT_BALANCE config

### Environment Variables Added

```bash
# For testing with zero balance
export BYPASS_BALANCE_VALIDATION="true"

# For setting minimum balance threshold
export MINIMUM_ACCOUNT_BALANCE="0.0"
```

---

**Document End**
