# AVAX Grid Rejection Diagnostic Tools

## Overview

This diagnostic suite helps identify the disconnect between AVAX grid rejection logic and grid display logic. When the interface shows 0 active grids but AVAX signals are being rejected due to an "existing grid", these tools can pinpoint the exact issue.

## Quick Start

### 1. Simple Diagnostic (Recommended First)

```bash
# Make sure trading bot is running on port 8000
python simple_avax_diagnostic.py
```

The simple diagnostic will:
- Check API server availability
- Verify grid count consistency between `/api/status` and `/api/grids`
- Identify AVAX-specific grid state issues
- Provide targeted recommendations

### 2. Comprehensive Diagnostic (Advanced)

```bash
python diagnose_grid_rejection.py --symbol AVAX
```

*Note: This version imports trading modules and may have dependency issues. Use only if simple diagnostic doesn't reveal the issue.*

## What the Tools Check

### API Consistency Analysis
- **Status API** (`/api/status`): Reports `active_grids` count
- **Grids API** (`/api/grids`): Returns detailed grid information
- **Consistency Check**: Compares counts between endpoints

### Key Data Points Analyzed
1. **Active Grids Count** from status endpoint
2. **Total Grids Count** from grids endpoint  
3. **AVAX-Specific Grids**: Grids with symbol "AVAX"
4. **Grid States**: ACTIVE, IDLE, CLOSED, etc.

### Common Issues Identified

#### Issue 1: API Count Mismatch
```
Status API reports: 3 active grids
Grids API reports: 0 total grids  
AVAX grids found: 0
```
**Likely Cause**: GridLifecycleManager state not synchronized with API server

#### Issue 2: AVAX State Discrepancy
```
Status API reports: 1 active grids
Grids API reports: 1 total grids
AVAX grids found: 0
```
**Likely Cause**: Grid exists but AVAX grid not in ACTIVE state

#### Issue 3: Database Inconsistency
```
AVAX positions in database: 2
API active grids: 0
```
**Likely Cause**: Grid state tracking failure

## Understanding the Results

### Normal Output
```
API Server: Online
Bot Running: True
Grid Counts - Status: 0, API: 0, AVAX: 0
Issues Found: 0
```

### Problematic Output Examples

#### Example 1 - Grid State Sync Issue
```
API Server: Online
Bot Running: True
Grid Counts - Status: 3, API: 0, AVAX: 0
Issues Found:
  - Grid count mismatch: Status=3, Grids=0
```

#### Example 2 - AVAX-Specific Issue
```
API Server: Online
Bot Running: True  
Grid Counts - Status: 1, API: 1, AVAX: 0
Issues Found:
  - AVAX shows 0 grids but total > 0
```

## Common Root Causes

### 1. GridLifecycleManager State Issues
- Grid exists in `_grids` dictionary but state != `GridState.ACTIVE`
- State transition failed during grid creation
- Orphaned grid entries not properly cleaned up

### 2. API Server Integration Problems
- Grid manager reference not properly initialized in API server
- Race conditions between grid updates and API calls
- Method calls failing silently with exceptions

### 3. Database vs Memory Inconsistency
- Grid records exist in database but not loaded into memory
- State changes not persisted to database
- Cache invalidation failures

## Recommended Fixes

### For Grid State Sync Issues
```python
# In GridLifecycleManager
def get_all_active_grids(self):
    active_grids = []
    for symbol in self._grids:
        if self._grids[symbol]["state"] == GridState.ACTIVE:
            # Add debug logging
            logger.debug(f"Active grid found: {symbol}, state: {self._grids[symbol]['state']}")
            active_grids.append(self.get_grid_status(symbol))
    return active_grids
```

### For API Server Issues
```python
# In api_server.py get_status()
if self.grid_manager:
    try:
        active_grids = self.grid_manager.get_all_active_grids()
        status["active_grids"] = len(active_grids) if active_grids else 0
        
        # Add debugging
        logger.info(f"Grid manager returned {len(active_grids)} active grids")
        for grid in active_grids[:3]:  # Log first 3
            logger.debug(f"Active grid: {grid.get('symbol', 'unknown')}")
    except Exception as e:
        logger.error(f"Grid manager error: {e}")
        status["active_grids"] = 0
```

### For Debugging State Transitions
```python
# Add to grid creation process
def create_grid(self, symbol: str, ...):
    # ... existing code ...
    
    # Verify state was set correctly
    if symbol in self._grids:
        actual_state = self._grids[symbol].get("state")
        logger.info(f"Grid {symbol} created with state: {actual_state}")
        if actual_state != GridState.ACTIVE:
            logger.error(f"Grid {symbol} not in ACTIVE state after creation!")
```

## Troubleshooting Steps

### Step 1: Run Simple Diagnostic
```bash
python simple_avax_diagnostic.py
```

### Step 2: Check Trading Bot Logs
Look for:
- Grid creation messages
- State transition logs  
- Error messages around AVAX processing

### Step 3: Manual API Check
```bash
curl http://localhost:8000/api/status
curl http://localhost:8000/api/grids
```

### Step 4: Examine Grid Manager State
If you have Python access:
```python
# In the trading bot context
if bot.grid_lifecycle:
    print("Grid symbols:", list(bot.grid_lifecycle._grids.keys()))
    for symbol, grid in bot.grid_lifecycle._grids.items():
        print(f"{symbol}: {grid.get('state')}")
```

## Prevention Strategies

### 1. Add Comprehensive Logging
- Log every grid state change
- Log API endpoint responses
- Log grid manager method calls

### 2. Implement Health Checks
```python
def check_grid_consistency(self):
    """Periodic consistency check between grid manager and API"""
    # Compare internal state with API response
    # Log discrepancies
    # Trigger automatic fixes if possible
```

### 3. Add State Validation
```python
def validate_grid_state(self):
    """Validate grid state consistency"""
    for symbol, grid in self._grids.items():
        # Check required fields
        # Validate state transitions
        # Log any inconsistencies
```

## Emergency Fixes

### Quick Reset
If grids are stuck in wrong state:
```python
# Emergency grid cleanup
bot.grid_lifecycle._grids.clear()
logger.warning("Emergency grid cleanup performed")
```

### API Server Restart
```bash
# Restart trading bot to clear any stuck state
python trading_bot_v2/api_server.py
```

## Output Files

The diagnostic tools save detailed results to JSON files:
- `avax_diagnostic_YYYYMMDD_HHMMSS.json` - Simple diagnostic results
- `grid_diagnostic_AVAX_YYYYMMDD_HHMMSS.json` - Comprehensive results

These files contain complete API responses, error details, and troubleshooting data.

## Getting Help

If the diagnostic doesn't reveal the issue:

1. **Check the logs** for error messages around grid creation
2. **Verify the bot is fully initialized** before running diagnostics
3. **Restart the trading bot** to clear any stuck state
4. **Share the diagnostic JSON** for deeper analysis

Remember: The goal is to identify where the disconnect occurs between the grid rejection logic (saying AVAX has an active grid) and the display logic (showing 0 active grids).