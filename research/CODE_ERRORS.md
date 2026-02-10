# Code Errors & Type Issues Documentation

**Document Version:** 1.0  
**Date:** February 10, 2026  
**Status:** 🟡 **REQUIRES ATTENTION**

---

## 📋 Executive Summary

This document captures all LSP (Language Server Protocol) errors and type hint issues detected in the trading bot codebase. These errors primarily relate to type safety and should be addressed to improve code quality and maintainability.

**Total Errors:** 6 across 3 files  
**Severity:** Medium - Type safety issues that don't affect runtime but impact development experience

---

## 🔍 Error Details by File

### 1. funding_arb.py

**File:** `trading_bot_v2/strategies/funding_arb.py`  
**Total Errors:** 3

#### Error 1: None Assignment to List[str] Parameter
- **Line:** 66:57
- **Error:** `Expression of type "None" cannot be assigned to parameter of type "List[str]"`
- **Code Context:**
  ```python
  def update_funding_rates(self, symbols: List[str] = None) -> None:
  ```
- **Issue:** Default parameter `None` conflicts with type hint `List[str]`
- **Fix:** Change type hint to `Optional[List[str]]`
- **Suggested Fix:**
  ```python
  from typing import Optional
  def update_funding_rates(self, symbols: Optional[List[str]] = None) -> None:
  ```

#### Error 2: None Assignment to stop_loss Parameter
- **Line:** 233:23
- **Error:** `Argument of type "None" cannot be assigned to parameter "stop_loss" of type "float"`
- **Code Context:**
  ```python
  stop_loss=None,  # Delta-neutral doesn't use traditional stops
  ```
- **Issue:** Signal constructor expects `float` but receives `None`
- **Fix:** Change parameter type to `Optional[float]` in Signal class
- **Note:** This is intentional design for delta-neutral strategy

#### Error 3: None Assignment to stop_loss Parameter
- **Line:** 303:23
- **Error:** `Argument of type "None" cannot be assigned to parameter "stop_loss" of type "float"`
- **Code Context:**
  ```python
  stop_loss=None,
  ```
- **Issue:** Same as Error 2, in closing signal generation
- **Fix:** Same as Error 2

---

### 2. orderbook_imbalance.py

**File:** `trading_bot_v2/strategies/orderbook_imbalance.py`  
**Total Errors:** 2

#### Error 1: None Assignment to direction Parameter
- **Line:** 351:52
- **Error:** `Argument of type "Literal['long', 'short'] | None" cannot be assigned to parameter "direction" of type "str"`
- **Code Context:**
  ```python
  if not self._check_imbalance_trend(symbol, direction):
  ```
- **Issue:** `direction` variable can be `None` but function expects `str`
- **Fix:** Add null check or change parameter type to `Optional[str]`
- **Suggested Fix:**
  ```python
  if direction and not self._check_imbalance_trend(symbol, direction):
  ```

#### Error 2: Attribute Access on None
- **Line:** 465:58
- **Error:** `"upper" is not a known attribute of "None"`
- **Code Context:**
  ```python
  f"{symbol}: OB imbalance signal - {direction.upper()} @ ${current_price:.2f}, "
  ```
- **Issue:** `direction` can be `None` when calling `.upper()`
- **Fix:** Add null check before string method
- **Suggested Fix:**
  ```python
  f"{symbol}: OB imbalance signal - {direction.upper() if direction else 'UNKNOWN'} @ ${current_price:.2f}, "
  ```

---

### 3. models.py

**File:** `trading_bot_v2/models.py`  
**Total Errors:** 1

#### Error 1: Undefined Tuple Type
- **Line:** 352:40
- **Error:** `"Tuple" is not defined`
- **Code Context:**
  ```python
  def is_valid_for_pacifica(self) -> Tuple[bool, List[str]]:
  ```
- **Issue:** `Tuple` type not imported
- **Fix:** Add `Tuple` to typing imports
- **Suggested Fix:**
  ```python
  from typing import Dict, List, Optional, Any, Union, Tuple
  ```

---

## 🛠️ Recommended Fixes

### Priority 1: Quick Fixes (5 minutes)

#### 1. Fix models.py Import Issue
```python
# In models.py line 8, add Tuple to imports
from typing import Dict, List, Optional, Any, Union, Tuple
```

#### 2. Fix funding_arb.py Type Hint
```python
# In funding_arb.py, add Optional import and fix type hint
from typing import Optional

def update_funding_rates(self, symbols: Optional[List[str]] = None) -> None:
```

### Priority 2: Design Considerations (15 minutes)

#### 3. Address None Values in Signal Class
The funding arbitrage strategy intentionally uses `None` for stop_loss since it's delta-neutral. Consider:

**Option A:** Update Signal class to accept Optional[float]
```python
# In Signal class __init__ method
def __init__(self, ..., stop_loss: Optional[float] = None, ...):
```

**Option B:** Use sentinel value instead of None
```python
# In funding_arb.py
NO_STOP_LOSS = -999.0  # Sentinel value
stop_loss=NO_STOP_LOSS,
```

#### 4. Fix orderbook_imbalance.py None Handling
```python
# Add null checks for direction variable
if direction is None:
    return signals

if not self._check_imbalance_trend(symbol, direction):
    # ... rest of code

# In logging statement
direction_str = direction.upper() if direction else "UNKNOWN"
f"{symbol}: OB imbalance signal - {direction_str} @ ${current_price:.2f}, "
```

---

## 📊 Impact Assessment

### Runtime Impact
- **Low:** All errors are type-related and don't affect runtime functionality
- **Current Status:** Code runs correctly despite type errors

### Development Impact
- **Medium:** Type errors affect IDE features, auto-completion, and static analysis
- **Code Quality:** Reduces maintainability and makes refactoring riskier

### Testing Impact
- **Low:** Tests pass despite type errors
- **Coverage:** Type errors not caught by current test suite

---

## 🔧 Implementation Plan

### Phase 1: Import Fixes (Immediate)
1. Add `Tuple` to models.py imports
2. Add `Optional` to funding_arb.py imports
3. Fix funding_arb.py function signature

### Phase 2: Signal Class Update (Design Decision)
1. Review Signal class constructor parameters
2. Decide on None handling approach
3. Update type hints accordingly
4. Update all strategy files that create Signal objects

### Phase 3: orderbook_imbalance.py Robustness (Next Sprint)
1. Add proper null checks for direction variable
2. Improve error handling in imbalance detection
3. Add unit tests for edge cases

---

## 🧪 Testing Recommendations

### Type Safety Tests
```python
# Add tests to verify type annotations
def test_funding_arb_type_annotations():
    """Test that funding arb strategy handles None values correctly."""
    strategy = FundingArbStrategy()
    # Test with None symbols parameter
    strategy.update_funding_rates(symbols=None)
    # Verify no runtime errors

def test_orderbook_imbalance_none_direction():
    """Test orderbook imbalance strategy with None direction."""
    strategy = OrderBookImbalanceStrategy()
    # Test scenario where direction is None
    signals = strategy.generate_signals("BTC-PERP", {}, 50000.0)
    # Verify graceful handling
```

### Static Analysis
```bash
# Run type checking to verify fixes
mypy trading_bot_v2/strategies/funding_arb.py
mypy trading_bot_v2/strategies/orderbook_imbalance.py
mypy trading_bot_v2/models.py

# Run comprehensive type check
mypy trading_bot_v2/
```

---

## 📝 Notes & Considerations

### Design Philosophy
- The funding arbitrage strategy's use of `None` for stop_loss is intentional for delta-neutral positioning
- Consider whether this design choice should be maintained or refactored
- Document any design decisions that deviate from standard patterns

### Future Prevention
- Consider adding `mypy` to CI/CD pipeline to catch type errors early
- Use `--strict` flag for mypy to enforce better type safety
- Add type annotations to all new code

### Code Review Checklist
- [ ] All function parameters have proper type hints
- [ ] Optional parameters use `Optional[T]` syntax
- [ ] None values are properly handled before method calls
- [ ] All required types are imported from typing module

---

## 🎯 Success Criteria

### Completion Metrics
- [ ] All 6 LSP errors resolved
- [ ] `mypy` runs clean with no errors
- [ ] All existing tests still pass
- [ ] No runtime regressions introduced
- [ ] Code documentation updated for any design changes

### Quality Improvements
- ✅ Better IDE support and auto-completion
- ✅ Improved static analysis capabilities
- ✅ Enhanced code maintainability
- ✅ Reduced risk of type-related bugs

---

**Next Review Date:** February 17, 2026  
**Assigned To:** Development Team  
**Priority:** Medium - Address during next sprint planning

---

*This document should be updated as errors are resolved or new type issues are discovered. Regular reviews recommended to maintain code quality standards.*