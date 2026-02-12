# Pacifica Market Data Compliance Fix - Implementation Summary

## Overview
Implemented comprehensive fix to use actual market data from Pacifica's `/info` API endpoint for `tick_size` and `lot_size` compliance, replacing all hardcoded values. This prevents 500 errors from order non-compliance.

## Changes Made

### 1. `trading_bot_v2/trading_bot.py`

#### Added Market Info Cache System
- **`_market_info_cache`**: Dictionary storing tick_size and lot_size per symbol
- **`_market_info_cache_timestamp`**: Tracks cache freshness
- **`_market_info_cache_ttl`**: 5-minute TTL for cache refresh
- **`_refresh_market_info_cache()`**: Fetches market data from `/info` endpoint
- **`_get_cached_market_info()`**: Retrieves cached specs with auto-refresh

#### New Public Methods
- **`get_symbol_tick_size(symbol)`**: Returns tick_size from API cache
- **`get_symbol_lot_size(symbol)`**: Returns lot_size from API cache
- **`round_price_to_tick_size(price, symbol)`**: Rounds price to tick_size multiples
- **`round_quantity_to_lot_size(quantity, symbol)`**: Rounds quantity to lot_size multiples
- **`validate_order_compliance(price, quantity, symbol)`**: Pre-flight validation with detailed error reporting

#### Updated Methods
- **`get_symbol_lot_size()`**: Now uses cached API data instead of hardcoded dict
- **`adjust_quantity_for_lot_size()`**: Delegates to `round_quantity_to_lot_size()`
- **`validate_lot_size_compliance()`**: Uses API data with proper None handling
- **`_calculate_grid_levels()`**: Uses actual tick_size/lot_size from API for grid calculations
- **`_execute_standard_signal_coordinated()`**: Applies tick_size to prices, validates before orders
- **`_execute_grid_signal_coordinated()`**: Validates and rounds all grid orders before placement

### 2. `trading_bot_v2/grid_lifecycle_manager.py`

#### Added Market Info Cache System
- **`_market_info_cache`**: Cache for market specs
- **`_market_info_cache_timestamp`**: Cache freshness tracker
- **`_market_info_cache_ttl`**: 5-minute TTL
- **`_refresh_market_info_cache()`**: Fetches market data
- **`_get_cached_market_info()`**: Retrieves cached specs

#### New Public Methods
- **`get_symbol_tick_size()`**: Returns tick_size from cache
- **`get_symbol_lot_size()`**: Returns lot_size from cache
- **`round_price_to_tick_size()`**: Rounds price to tick_size
- **`round_quantity_to_lot_size()`**: Rounds quantity to lot_size

#### Updated Methods
- **`_replenish_grid_order()`**: Replaced hardcoded tick_size/lot_size with API data
- **`recenter_grid()`**: Replaced hardcoded tick_size with API data

## API Integration

### Data Source
- **Endpoint**: `GET /api/v1/info` via `client.get_markets()`
- **Fields Used**: 
  - `tick_size`: Minimum price increment
  - `lot_size`: Minimum quantity increment

### Cache Behavior
- **Initial Load**: Populated during TradingBot initialization
- **Auto-Refresh**: 5-minute TTL triggers automatic refresh
- **On-Demand**: Refreshed immediately on cache miss

## Expected Values (from API Documentation)

| Symbol | tick_size | lot_size |
|--------|-----------|----------|
| BTC    | 1         | 0.00001  |
| ETH    | 0.1       | 0.0001   |

## Compliance Validation

### Pre-Flight Checks
All orders now undergo validation before placement:
1. Price checked against tick_size
2. Quantity checked against lot_size
3. Adjusted values calculated if non-compliant
4. Detailed error logging for debugging

### Validation Response Format
```python
{
    "valid": bool,
    "price_compliant": bool,
    "quantity_compliant": bool,
    "tick_size": float or None,
    "lot_size": float or None,
    "errors": List[str],
    "adjusted_price": float,
    "adjusted_quantity": float,
}
```

## Fallback Behavior

When API data is unavailable:
- Uses original values without rounding (for market orders)
- Logs warning about missing market specs
- Prevents order rejection by using conservative defaults only in GridLifecycleManager

## Benefits

1. **No More 500 Errors**: Orders comply with Pacifica's exact requirements
2. **Accurate Pricing**: Prices rounded to actual tick_size from API
3. **Precise Sizing**: Quantities rounded to actual lot_size from API
4. **Better Debugging**: Detailed logging of all adjustments
5. **Automatic Updates**: Cache refreshes automatically every 5 minutes
6. **Fallback Safety**: Graceful degradation when API unavailable

## Testing Recommendations

1. Verify cache population on bot startup
2. Check tick_size/lot_size values match API documentation
3. Test order placement with various symbols
4. Monitor logs for compliance adjustments
5. Verify fallback behavior when API unavailable

## Files Modified

- `trading_bot_v2/trading_bot.py`: ~350 lines added/modified
- `trading_bot_v2/grid_lifecycle_manager.py`: ~250 lines added/modified

## Backward Compatibility

All changes are backward compatible:
- Existing methods maintain same signatures
- Hardcoded values replaced with API calls
- Fallback behavior preserves original functionality
- No breaking changes to public interfaces
