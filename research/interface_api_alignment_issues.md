# Interface-API Alignment Issues Report

**Date:** 2026-02-14  
**Status:** Interface Fixes Complete - API Changes Required  
**Scope:** Trading Bot v2 Interface Alignment

---

## Executive Summary

This document outlines issues identified during the alignment of `interface.html` with the current API endpoints in `trading_bot_v2/api_server.py`. While the interface has been updated to work with the existing API structure, several issues remain that **require changes to the backend API** and cannot be resolved through interface modifications alone.

---

## Interface Changes Completed

The following changes were made to `interface.html` to align with the current API:

### 1. Activity Table
- **Issue:** Interface expected `response.errors`, `response.total_markets`, `response.successful` fields that don't exist
- **Fix:** Removed references to non-existent fields, simplified to use only `response.data`

### 2. Positions Sync
- **Issue:** Interface expected `response.synced_from_pacifica` but API returns `response.data.synced_count`
- **Fix:** Updated to access `response.data?.synced_count`

### 3. Grids Table
- **Issue:** Field names in interface didn't match API response structure
- **Fix:** Updated mappings:
  - `grid.state` instead of `grid.status`
  - `grid.lower_price` / `grid.upper_price` instead of `grid.price_low` / `grid.price_high`
  - `grid.filled_count` for total fills
  - `grid.realized_pnl` (API only provides realized P&L)
  - Added calculation logic for buy/sell split from `grid_levels` when available

### 4. Signals Table
- **Issue:** Interface didn't handle differences between `/signals` and `/signals/recent` endpoints
- **Fix:** Added detection for which endpoint data came from, handles both formats:
  - Signal logger (`/signals/recent`): has `status` field
  - Basic endpoint (`/signals`): has `is_valid` boolean

### 5. Signal Statistics
- **Issue:** Interface expected `response.data.excludes_generated` flag that doesn't exist
- **Fix:** Removed dependency, added fallback calculation logic

### 6. Side Field Normalization
- **Issue:** API returns sides in different formats across endpoints
- **Fix:** Normalized comparisons to handle:
  - `'long'`/`'short'` (lowercase)
  - `'BUY'`/`'SELL'`/`'LONG'`/`'SHORT'` (uppercase)
  - `'bid'`/`'ask'` variations

---

## API-Side Issues Requiring Changes

The following issues **CANNOT be fixed by modifying interface.html alone**. They require changes to the API server or underlying data structures.

---

### Issue 1: Grid Buy/Sell Fill Split

**What you're trying to connect:**  
Grid table displays showing separate buy fill counts and sell fill counts (e.g., "5B/3S" for 5 buy fills and 3 sell fills).

**Where you're trying to connect it:**  
`updateGrids()` function in interface.html - specifically the "Fills (B/S)" column in the grids table.

**Why the connection is needed:**  
Traders need to see the directional breakdown of grid fills to understand:
- Grid performance and profitability
- Current directional bias
- Rebalancing needs
- Grid health and activity

**Current API limitation:**  
The `GridLifecycleManager.get_all_active_grids()` method returns only `filled_count` (total fills), not split by side.

**Current API response:**
```json
{
  "symbol": "BTC",
  "filled_count": 8,
  "realized_pnl": 12.50,
  // ... other fields
}
```

**Required API change:**  
Update `GridLifecycleManager.get_all_active_grids()` to track and return separate counts:
```json
{
  "symbol": "BTC",
  "filled_count": 8,
  "buy_fills": 5,
  "sell_fills": 3,
  "realized_pnl": 12.50,
  // ... other fields
}
```

**Implementation notes:**  
The grid lifecycle manager should track fills as they occur and maintain running counts by side in the grid state object.

---

### Issue 2: Grid Net Position

**What you're trying to connect:**  
Accurate net position display showing the current directional exposure of each grid (e.g., "+0.05 BTC" or "-0.03 BTC").

**Where you're trying to connect it:**  
`updateGrids()` function in interface.html - "Net Pos" column in the grids table.

**Why the connection is needed:**  
Net position shows:
- Current directional exposure
- Grid imbalance that may need rebalancing
- Risk exposure per symbol
- Whether grid is net long or net short

**Current API limitation:**  
API doesn't provide a `net_position` field. The interface currently attempts to estimate from `grid_levels` when available, but this is:
- Unreliable if grid levels data is incomplete
- May not match actual position if fills haven't been properly tracked
- Adds complexity to interface code

**Current API response:**  
No position information in grid data.

**Required API change:**  
`GridLifecycleManager.get_all_active_grids()` should calculate and return `net_position`:
```json
{
  "symbol": "BTC",
  "net_position": 0.05,
  "realized_pnl": 12.50,
  // ... other fields
}
```

**Implementation notes:**  
The grid manager should calculate net position as:
```python
net_position = sum(buy_fill_quantities) - sum(sell_fill_quantities)
```

This should be calculated and stored whenever a fill occurs.

---

### Issue 3: Grid Total P&L vs Realized P&L

**What you're trying to connect:**  
Complete P&L picture including both realized gains (from completed round trips) and unrealized gains (from current open positions within the grid).

**Where you're trying to connect it:**  
`updateGrids()` function in interface.html - "Total P&L" column in the grids table.

**Why the connection is needed:**  
Traders need to see:
- Realized P&L: profit from completed buy/sell cycles
- Unrealized P&L: mark-to-market value of current grid position
- Total P&L: complete picture of grid performance

**Current API limitation:**  
API only returns `realized_pnl`. The interface currently shows realized P&L in both "Realized P&L" and "Total P&L" columns, which is misleading.

**Current API response:**
```json
{
  "symbol": "BTC",
  "realized_pnl": 12.50,
  // No unrealized_pnl or total_pnl fields
}
```

**Required API change:**  
Grid manager should track and return all three P&L fields:
```json
{
  "symbol": "BTC",
  "realized_pnl": 12.50,
  "unrealized_pnl": 8.25,
  "total_pnl": 20.75,
  // ... other fields
}
```

**Implementation notes:**  
1. Track realized P&L as each round-trip completes
2. Calculate unrealized P&L as: `net_position * (current_market_price - avg_entry_price_of_open_position)`
3. Total P&L = realized_pnl + unrealized_pnl
4. Current market price can be obtained from WebSocket cache

---

### Issue 4: Signal Quality Information

**What you're trying to connect:**  
Signal quality classification (e.g., "HIGH_CONVICTION", "MEDIUM", "LOW") to help users understand signal strength.

**Where you're trying to connect it:**  
`updateSignals()` function in interface.html - "Quality" column in the signals table.

**Why the connection is needed:**  
Quality helps traders:
- Prioritize which signals to act on
- Understand the confidence level
- Filter out low-quality signals
- Make informed trading decisions

**Current API limitation:**  
Only the signal logger endpoint (`/signals/recent`) includes quality information. The basic `/signals` endpoint (which returns event bus signals) does not include quality.

**Current behavior:**  
- If signal logger available: shows quality (e.g., "HIGH_CONVICTION")
- If using basic endpoint: shows "N/A" (current workaround in interface)

**Required API change:**  
Option A: Include quality in basic `/signals` endpoint response
```json
{
  "id": "signal-uuid",
  "symbol": "BTC",
  "strategy": "MeanReversion",
  "quality": "HIGH_CONVICTION",
  // ... other fields
}
```

Option B: Ensure signal logger is always the primary source (may already be the case when bot is running)

**Implementation notes:**  
The quality is already calculated during signal generation in the strategy manager. It should be included when signals are published to the event bus.

---

### Issue 5: Signal Status for Event Bus Signals

**What you're trying to connect:**  
Execution status (executed/rejected/failed) for signals to show whether they were acted upon by the trading bot.

**Where you're trying to connect it:**  
`updateSignals()` function in interface.html - "Status" column in the signals table.

**Why the connection is needed:**  
Users need to know:
- Whether a signal resulted in a trade
- If a signal was rejected (and why)
- If execution failed
- Historical signal performance

**Current API limitation:**  
The basic `/signals` endpoint returns signals from the event bus with only an `is_valid` boolean field, not execution status. Only the signal logger (`/signals/recent`) has status information.

**Current behavior:**  
- Interface derives status from `is_valid`: 'valid' if true, 'invalid' if false
- This doesn't show execution outcomes

**Required API change:**  
Option A: Track signal execution outcomes in the event bus and include in `/signals` response:
```json
{
  "id": "signal-uuid",
  "symbol": "BTC",
  "is_valid": true,
  "status": "executed",  // or "rejected", "failed", "pending"
  "execution_details": {
    "order_id": "order-uuid",
    "executed_at": "2026-02-14T10:30:00Z",
    "execution_price": 65000.00
  }
}
```

Option B: Make signal logger the canonical source and have `/signals` proxy to it

**Implementation notes:**  
The signal logger already tracks this information. The event bus should either:
1. Update signals with execution outcomes after they're published
2. Be replaced/augmented by the signal logger for historical queries

---

### Issue 6: Activity Error Tracking

**What you're trying to connect:**  
Per-market error information to show which markets failed to load during the activity fetch.

**Where you're trying to connect it:**  
`updateActivity()` function in interface.html - error display in the activity table section.

**Why the connection is needed:**  
Users should know:
- Which markets failed to load
- Why they failed (API error, timeout, etc.)
- Overall data quality (e.g., "8/10 markets loaded")

**Current API limitation:**  
The `get_activity()` method in api_server.py doesn't return error details in the response. The interface previously tried to access `response.errors`, `response.total_markets`, and `response.successful` fields that don't exist.

**Current API response:**
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTC",
      "regime": "trending_strong",
      // ... market data
    }
    // ... more markets
  ]
  // No errors, total_markets, or successful fields
}
```

**Required API change:**  
Enhance `get_activity()` to track and return error information:
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTC",
      "regime": "trending_strong",
      // ... market data
    }
  ],
  "errors": [
    {
      "symbol": "DOGE",
      "error": "Insufficient kline data",
      "timestamp": "2026-02-14T10:30:00Z"
    }
  ],
  "total_markets": 10,
  "successful": 9
}
```

**Implementation notes:**  
The `get_activity()` method should:
1. Track errors when individual market data fetch fails
2. Count total attempted vs successful
3. Include error details in response

---

## Summary of Required Changes

| Issue | Component(s) Affected | Priority | Estimated Effort |
|-------|----------------------|----------|-----------------|
| Grid Buy/Sell Fill Split | `GridLifecycleManager` | Medium | 2-3 hours |
| Grid Net Position | `GridLifecycleManager` | Medium | 1-2 hours |
| Grid Total P&L | `GridLifecycleManager` | Medium | 2-3 hours |
| Signal Quality | `StrategyManager`, Event Bus | Low | 1-2 hours |
| Signal Status | Event Bus, `TradingBot` | Low | 2-3 hours |
| Activity Error Tracking | `api_server.py` - `get_activity()` | Low | 1-2 hours |

**Total Estimated Effort:** 9-15 hours

---

## Recommendations

### Short-term (Interface Only)
The interface has been updated to work with the current API structure. It handles missing data gracefully with appropriate fallbacks and default values.

### Medium-term (API Enhancements)
1. **Grid improvements** should be prioritized as grids are a core trading feature
2. **Signal tracking** improvements will provide better visibility into bot decision-making
3. **Activity error tracking** is lower priority but improves user experience

### Long-term (Architecture)
Consider consolidating signal tracking so there's a single authoritative source (the signal logger) rather than having signals in both the event bus and signal logger.

---

## Appendix: API Response Reference

### Current Grid Response
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTC",
      "state": "active",
      "grid_levels": [...],
      "upper_price": 70000.00,
      "lower_price": 60000.00,
      "filled_count": 8,
      "total_orders": 16,
      "realized_pnl": 12.50,
      "created_at": "2026-02-14T10:00:00Z"
    }
  ]
}
```

### Current Signal Response (Basic)
```json
{
  "success": true,
  "data": [
    {
      "id": "signal-uuid",
      "timestamp": "2026-02-14T10:30:00Z",
      "symbol": "BTC",
      "strategy": "MeanReversion",
      "side": "buy",
      "entry_price": 65000.00,
      "stop_loss": 64000.00,
      "take_profit": 67000.00,
      "confidence": 75.0,
      "is_valid": true,
      "current_price": 65100.00
    }
  ],
  "count": 1
}
```

### Current Signal Response (Signal Logger)
```json
{
  "success": true,
  "data": [
    {
      "timestamp": "2026-02-14T10:30:00.123456",
      "symbol": "BTC",
      "strategy": "MeanReversion",
      "side": "buy",
      "entry_price": 65000.00,
      "stop_loss": 64000.00,
      "take_profit": 67000.00,
      "confidence": 75.0,
      "status": "executed",
      "quality": "HIGH_CONVICTION"
    }
  ],
  "count": 1
}
```

---

## Document Information

**Author:** Claude (AI Assistant)  
**Reviewed By:** N/A  
**Last Updated:** 2026-02-14  
**Version:** 1.0  
**Status:** Complete
