# Trading Bot Issues Report - Feb 14, 2026

## Session Summary

Issues identified and fixes applied during this session.

---

## 1. Duplicate Function Definitions (FIXED)

| File | Issue | Fix |
|------|-------|-----|
| api_server.py | Duplicate `get_grids()` at line 1433 | Removed duplicate |
| database.py | Duplicate `get_trades()` at line 734 | Removed duplicate |
| indicators.py | Duplicate `calculate_rsi()` at line 421 | Removed duplicate |
| health_check.py | 5 duplicate methods (lines 395-529) | Removed duplicates |

---

## 2. Bare Except Clauses (FIXED)

| File | Line | Issue | Fix |
|------|------|-------|-----|
| grid_lifecycle_manager.py | 1318 | `except:` catches everything | Changed to `except (ValueError, TypeError, AttributeError)` |
| grid_lifecycle_manager.py | 1525 | `except:` catches everything | Changed to `except (ValueError, TypeError)` |
| grid_lifecycle_manager.py | 1713 | `except:` catches everything | Changed to `except (ValueError, TypeError)` |

---

## 3. Unused Variable (FIXED)

| File | Line | Issue |
|------|------|-------|
| api_server.py | 2321 | `regime_data` assigned but never used - removed |

---

## 4. Grid Refresh ATR Issue (FIXED)

**Problem:** When a grid was first created, the ATR calculation could fail (no 5m data yet), so `atr_at_creation` was saved as 0. When new signals came in, both current and stored ATR were 0, causing all signals to be rejected.

**Solution:** Added percentage-based drift fallback:
- If ATR unavailable and center price exists → use `(abs(proposed - current) / current) * 100` as % drift
- Logging now shows `%` or `x` depending on which method is used

---

## 5. Grid Position Update Status (FIXED)

**Problem:** Grid signals were showing as "executed" even when a grid already existed (should show "position_updated").

**Solution:** Added check for existing grid in GridLifecycleManager:
```python
# Check if grid already exists in GridLifecycleManager._grids
existing_grid = self.grid_lifecycle._grids.get(signal.asset)
if existing_grid:
    signal._is_position_update = True  # Logs as "position_updated"
```

---

## 6. Database Schema Fix (FIXED)

**Problem:** `no such column: exit_price` error on startup.

**Solution:** Added missing columns to positions table:
- exit_price
- realized_pnl
- status
- closed_at

---

## 7. Startup Optimizations Applied

| Change | Before | After |
|--------|--------|-------|
| Kline bootstrap | Done twice (api_server + trading_bot) | Done once in api_server |
| Max concurrent | 3 | 2 (more conservative) |
| Disk cache TTL | 4 hours | 12 hours |
| Market info cache | Fetched at __init__ | Lazy load on first use |
| Initial loop wait | 5 seconds | 2 seconds |

---

## 8. Position Sync Fix (FIXED)

**Problem:** Sync positions was showing 0 positions despite having positions in Pacifica.

**Solution:** Fixed field name detection in api_server.py:
- Before: Only checked `quantity` and `size`
- After: Checks all fields: `size`, `amount`, `quantity`, `position_size`, `pos_size`

---

## Notes

- Server could not be started from this environment (connection issues)
- All identified issues have been fixed in the code
- Recommend restarting server manually to test fixes

---

## Next Steps

1. Restart the server using `run_bot.bat`
2. Monitor for 5 trading cycles
3. Verify:
   - Grid signals show "position_updated" when grid exists
   - Grid refresh works with percentage-based drift fallback
   - Position sync correctly detects all positions
   - No duplicate execution errors
