# RiskManager Margin Safety Enhancement - Implementation Summary

## Overview
Enhanced the RiskManager to use margin-related account statistics from the Pacifica API to prevent opening positions that would exceed safe margin limits. This adds an additional layer of protection against liquidation risk.

## Changes Made

### 1. `trading_bot_v2/risk_manager.py`

#### New Constructor Parameters
- `client`: Optional PacificaClient for margin data retrieval
- `max_margin_utilization_pct`: Maximum % of equity that can be used as margin (default: 75%)
- `maintenance_margin_buffer_pct`: Safety buffer above maintenance margin (default: 15%)

#### New Methods Added

**Margin Data Retrieval:**
- `get_margin_data(force_refresh: bool = False) -> Optional[MarginData]`
  - Fetches margin data from Pacifica API via client.get_balance()
  - Caches data for 30 seconds to reduce API calls
  - Returns dict with: account_equity, total_margin_used, cross_mmr, available_to_spend, balance, margin_utilization_pct

**Margin Safety Checks:**
- `check_margin_safety(proposed_margin: float = 0.0) -> Dict[str, Any]`
  - Validates margin levels are safe for opening positions
  - Checks current utilization against max threshold
  - Validates proposed position wouldn't exceed max utilization
  - Ensures maintenance margin buffer is maintained
  - Returns detailed result with safety status and warnings

**Position Validation:**
- `validate_margin_for_position(position_notional: float, leverage: float = 1.0) -> Dict[str, Any]`
  - Combines margin calculation with safety check
  - Returns comprehensive validation result

**Utility Methods:**
- `get_available_margin() -> float` - Returns available margin for new positions
- `calculate_margin_requirement(position_notional, leverage, margin_factor) -> float` - Estimates margin needed
- `get_margin_summary() -> Dict[str, Any]` - Returns comprehensive margin status
- `_parse_float_safe(value, default) -> float` - Safely parses float values with currency symbols

#### Cache Management
- Added `_margin_data_cache` and `_margin_cache_timestamp` for caching
- 30-second TTL to balance freshness with API efficiency

### 2. `trading_bot_v2/trading_bot.py`

#### RiskManager Initialization
Updated to pass the Pacifica client to RiskManager:
```python
self.risk_manager = RiskManager(
    max_portfolio_risk_pct=0.05,
    max_portfolio_exposure_pct=0.15,
    client=self.client,  # NEW: For margin data retrieval
    max_margin_utilization_pct=0.75,
    maintenance_margin_buffer_pct=0.15,
)
```

#### Signal Validation Enhancement
Updated `_should_execute_signal()` to include margin safety checks:
- Calculates proposed position notional
- Calls `risk_manager.validate_margin_for_position()`
- Rejects signals that would exceed safe margin limits
- Logs detailed margin status for monitoring

#### Execution Enhancement
Updated `_coordinate_signal_execution()` to include final margin validation:
- Validates margin safety after capital allocation but before execution
- Prevents race conditions where margin status changes between validation and execution

#### Risk Monitoring Enhancement
Updated `_monitor_risk_coordinated()` to include margin monitoring:
- Fetches margin summary from RiskManager
- Logs warnings at warning level (80% of max utilization)
- Logs critical alerts at critical level (> max utilization)
- Publishes margin data in risk events for external monitoring

### 3. `trading_bot_v2/tests/test_risk_manager_margin.py`

Comprehensive test suite with 24 tests covering:
- Initialization with/without client
- Margin data retrieval and caching
- Margin safety checks (safe, high utilization, exceeds max, maintenance margin)
- Available margin calculations
- Margin summary generation (healthy, warning, critical states)
- Position validation
- Backward compatibility
- Integration with capital allocation

## Margin Safety Logic

### Safe Conditions
A position is considered margin-safe if ALL of the following are true:

1. **Current Utilization Check**
   ```
   total_margin_used / account_equity < max_margin_utilization_pct (75%)
   ```

2. **Proposed Utilization Check**
   ```
   (total_margin_used + proposed_margin) / account_equity < max_margin_utilization_pct (75%)
   ```

3. **Maintenance Margin Buffer Check**
   ```
   account_equity > cross_mmr * (1 + maintenance_margin_buffer_pct)
   account_equity > cross_mmr * 1.15
   ```

4. **Available Margin Check**
   ```
   available_margin >= proposed_margin
   where available_margin = min(available_to_spend, equity_based_available)
   ```

### Warning Thresholds
- **Healthy**: < 60% of max utilization (< 45%)
- **Warning**: 60-100% of max utilization (45-75%)
- **Critical**: > 100% of max utilization (> 75%)

### Low Maintenance Margin Buffer Warning
- Triggered when safety buffer < 10% of cross_mmr
- Does not block positions but logs warning

## Backward Compatibility

The implementation maintains full backward compatibility:

1. **Client is Optional**: RiskManager works without a Pacifica client
2. **Graceful Degradation**: If margin data unavailable, validation passes with warning
3. **Existing Methods Unchanged**: All existing RiskManager methods work as before
4. **Default Parameters**: Sensible defaults for all new parameters

## Integration Points

### Signal Flow with Margin Checks
```
1. Signal Generated
   ↓
2. _should_execute_signal()
   - Validate signal validity
   - Check stop loss
   - Check positions
   - Check balance
   - Check exposure
   - MARGIN CHECK: validate_margin_for_position() ← NEW
   ↓
3. _coordinate_signal_execution()
   - Request capital allocation
   - MARGIN CHECK: Final validation ← NEW
   ↓
4. Execute order
```

### Risk Monitoring Flow
```
_monitor_risk_coordinated() (every 30 seconds)
   - Get balance
   - Get exposure
   - Calculate risk percentage
   - MARGIN MONITORING: get_margin_summary() ← NEW
   - Publish risk event with margin data ← NEW
```

## Configuration

### Default Values
```python
max_margin_utilization_pct = 0.75      # 75% of equity
maintenance_margin_buffer_pct = 0.15   # 15% buffer above maintenance
margin_cache_ttl = 30                  # 30 seconds
```

### Customization
These can be customized when initializing RiskManager:
```python
risk_manager = RiskManager(
    max_margin_utilization_pct=0.80,      # More aggressive: 80%
    maintenance_margin_buffer_pct=0.20,   # Safer: 20% buffer
)
```

## Benefits

1. **Prevents Liquidation**: Blocks positions that would push account into dangerous margin territory
2. **Maintains Buffer**: Ensures sufficient buffer above maintenance margin
3. **Reduces API Calls**: Caches margin data for 30 seconds
4. **Comprehensive Monitoring**: Logs detailed margin status for operational visibility
5. **Non-Breaking**: Fully backward compatible with existing code
6. **Configurable**: Parameters can be adjusted based on risk appetite

## Testing

All new functionality is covered by comprehensive tests:
- 24 new tests for margin safety features
- 23/24 existing RiskManager tests still pass
- 1 pre-existing test failure unrelated to these changes

Run tests with:
```bash
pytest trading_bot_v2/tests/test_risk_manager_margin.py -v
```
