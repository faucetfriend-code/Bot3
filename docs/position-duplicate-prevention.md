# Position Duplicate Prevention

## Overview

The trading bot implements a robust two-phase position checking system to prevent duplicate positions. This system addresses race conditions where a position could be opened between initial signal validation and actual order execution.

## Problem Statement

**Race Condition Risk**: A signal undergoes multiple validation phases before execution. Between the initial position check and the actual order placement:
- Another signal for the same asset may have been processed
- External market conditions may have changed
- Manual trades may have been executed

Without protection, these scenarios could result in duplicate positions, violating risk management rules and causing unintended exposure.

## Solution Architecture

### Phase 1: Initial Position Check (~line 1384)

The first check occurs early in the signal validation pipeline:

```python
# Check if position already exists for this symbol
try:
    existing_positions = self.client.get_positions()
    if existing_positions:
        for pos in existing_positions:
            pos_symbol = pos.get("symbol", "")
            pos_side = pos.get("side", "").lower()
            pos_size = pos.get("size", 0)
            
            if pos_symbol == signal.asset:
                if pos_side == signal_side:
                    # Reject duplicate
                    self.signal_logger.log_signal_rejected(...)
                    return False
```

**Key Features:**
- Detailed logging of all found positions (symbol, side, size)
- Graceful exception handling - rejects signal if position check fails
- Distinguishes between same-side duplicates and opposite-side positions
- Safety-first approach: any check failure results in rejection

### Phase 2: Final Position Re-check (~line 1508)

A second verification occurs immediately before order execution:

```python
# PHASE 3: Final position re-check before execution
logger.info(f"🔍 Final position re-check for {signal.asset} before execution...")
try:
    recheck_positions = self.client.get_positions()
    
    for pos in recheck_positions:
        if pos_symbol == signal.asset and pos_side == signal_side and pos_size > 0:
            logger.warning(f"🚫 RACE CONDITION PREVENTED: ...")
            self.signal_logger.log_signal_rejected(
                signal=signal,
                reason="Duplicate position detected at execution time",
                notes="Duplicate detected at execution re-check - race condition prevented"
            )
            return
```

**Critical Purpose:**
- Catches positions opened between Phase 1 and Phase 2
- Prevents execution of signals that would create duplicates
- Logs specifically as "Race condition prevented" for monitoring
- Fails safe - any exception results in rejection

## Signal Status Categories

The system now supports five distinct signal statuses for comprehensive tracking:

| Status | Meaning | When Used |
|--------|---------|-----------|
| `generated` | Signal was created by strategy | After signal generation |
| `executed` | New position successfully opened | After order fills |
| `position_updated` | Existing position modified | After adding to position |
| `rejected` | Signal blocked (duplicate, etc.) | Failed validation checks |
| `failed` | Signal execution error | Order placement failed |

### Status Validation

The `log_signal_executed()` method validates status values:

```python
def log_signal_executed(self, signal, ..., status: str = "executed"):
    # Validate status - only allow "executed" or "position_updated"
    if status not in ("executed", "position_updated"):
        logger.warning(f"Invalid status '{status}' - defaulting to 'executed'")
        status = "executed"
```

## Race Condition Prevention

### The Problem

In high-frequency trading environments, the time window between validation and execution can be significant:

```
Timeline:
T0: Signal generated
T1: Initial position check (Phase 1) - No position found ✓
   ↓  (Other signals processed, market changes)
T2: Capital allocated
T3: Final position check (Phase 2) - Position now exists! ✗
T4: Signal rejected - race condition prevented
```

### The Solution

The dual-check architecture creates a "validation sandwich":

1. **Early Check**: Filters obvious duplicates before resource allocation
2. **Late Check**: Catches race conditions that occurred during processing
3. **Safe Defaults**: Any check failure results in rejection, never execution

### Logging and Monitoring

Both phases log detailed information for debugging:

```
# Phase 1 Log
🔍 Position check: Found position for BTC-USD - side=long, size=0.5, signal_side=long
🔁 Position already exists for BTC-USD BUY - skipping duplicate signal

# Phase 2 Log (Race Condition)
🚫 RACE CONDITION PREVENTED: Duplicate position detected at execution time 
   for BTC-USD (existing: side=long, size=0.5)
```

## Implementation Files

| File | Lines | Purpose |
|------|-------|---------|
| `trading_bot.py` | 1384-1427 | Phase 1: Initial position check |
| `trading_bot.py` | 1507-1560 | Phase 2: Final position re-check |
| `signal_logger.py` | 277-340 | Status validation and logging |

## Best Practices

1. **Always Fail Safe**: Position check exceptions result in rejection, not execution
2. **Log Everything**: Detailed logging enables debugging of edge cases
3. **Monitor Rejections**: Track rejection reasons to identify system issues
4. **Status Accuracy**: Use correct status codes for proper analytics

## Testing

When testing duplicate prevention:

```python
# Test case: Race condition simulation
def test_race_condition_prevention():
    # 1. Create signal for asset with no position
    signal = create_signal(asset="BTC-USD", side="BUY")
    
    # 2. Simulate position opening between checks
    mock_position_opened_during_processing("BTC-USD", side="long")
    
    # 3. Execute signal - should be rejected
    result = bot.execute_signal(signal)
    
    # 4. Verify rejection
    assert result.status == "rejected"
    assert "race condition" in result.notes.lower()
```

## Related Documentation

- [Signal Logging](./signal-logging.md) - Complete signal lifecycle tracking
- [Risk Management](./risk-management.md) - Position sizing and exposure limits
- [Error Handling](./error-handling.md) - Safety-first error management patterns
