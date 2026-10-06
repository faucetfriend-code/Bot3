<objective>
Implement comprehensive grid trading risk controls according to the Grid Trading Strategy specification. This is QUALITY CONTROL ISSUE #5 - grid trading must be tightly constrained to prevent account liquidation.
</objective>

<context>
Grid execution currently bypasses RiskManager completely, allowing unlimited position accumulation and silent leverage build-up. The Grid Trading Brief specifies mandatory risk controls, regime constraints, and execution rules to make grid trading safe.

References:
- "G:\ai-workspace\Bot 3\research\quality control for the fixes.txt" - Issue #9
- "G:\ai-workspace\Bot 3\research\Grid trading brief.txt" - Complete specification

Current dangerous issue:
```python
if signal.strategy == StrategyType.GRID_TRADING:
    self._execute_grid_signal(signal)  # Bypasses RiskManager - UNLIMITED RISK!
```

Grid trading is inherently dangerous and will "blow up the account" without proper controls.
</context>

<requirements>
**Regime Constraints (MANDATORY):**
1. Grid trading ONLY allowed in: RANGING_CALM, RANGING_VOLATILE, INDECISIVE
2. HARD DISABLE in: TRENDING_MODERATE, TRENDING_STRONG
3. StrategyManager must enforce regime validation before emitting grid signals

**RiskManager Integration (MANDATORY):**
4. GridTradingStrategy must request capital from RiskManager, not bypass it
5. RiskManager must provide get_grid_capital() method
6. RiskManager must validate_grid_exposure() before grid placement
7. RiskManager must track grid exposure separately from regular positions

**Capital Allocation (MANDATORY):**
8. Grid capital = account_balance * 0.03-0.05 (3-5% of account)
9. Capital divided equally across grid levels (no martingale/pyramiding)
10. Quantity = capital_per_level / grid_price for each order

**Emergency Stops (MANDATORY):**
11. Every grid must have one emergency stop per symbol
12. Emergency stop distance = X% from grid midpoint
13. Trigger on: price breaks range, ADX > threshold, regime changes to TRENDING
14. On trigger: cancel all orders, close all positions, disable grid for symbol

**Order Requirements (MANDATORY):**
15. Every grid order must have: entry price, take-profit (next grid level)
16. NO individual stop-loss per leg (use emergency stop instead)
17. All orders must be limit orders, not market orders

**Dynamic Controls:**
18. Volatility-based adjustments: increase spacing, reduce levels when volatile
19. Cooldown after emergency exit: ≥ 2 × grid lookback period
20. Prevent re-arming until regime valid and volatility normalized

**Failure Handling:**
21. If emergency stop cannot be placed: do not place grid, log CRITICAL, abort
22. Partial fills: ensure exposure ≤ grid_capital, cancel remaining orders if cap hit
23. Exchange disconnect: freeze new grids, allow only exits, resume after regime check

**Logging Requirements:**
24. Every grid must log: Grid ID, Symbol, Regime, Total capital, Levels placed, Emergency stop, Exit reason
</requirements>

<implementation>
**1. Update StrategyManager for Regime Enforcement:**

```python
def generate_signals_for_market(self, symbol: str, multi_tf_data: Dict[str, Dict[str, List[float]]], current_price: float):
    # ... existing regime detection ...
    regime = self.regime_detector.detect_regime_cached(symbol, regime_data)

    # ENFORCE GRID REGIME CONSTRAINTS (MANDATORY)
    if regime in [MarketRegime.TRENDING_STRONG, MarketRegime.TRENDING_MODERATE]:
        # HARD DISABLE: Remove GridTrading from active strategies
        active_strategy_names = [name for name in active_strategy_names if name != "GridTrading"]
        logger.info(f"Grid trading disabled in {regime.value} regime for {symbol}")

    # Only proceed with allowed strategies
    # ... rest of method ...
```

**2. Extend RiskManager with Grid-Specific Methods:**

```python
class RiskManager:
    def __init__(self, ...):
        # ... existing init ...
        self.grid_exposure: Dict[str, float] = {}  # Track grid exposure per symbol

    def get_grid_capital(self, account_balance: float, current_exposure: float, symbol: str) -> float:
        """Get capital allocation for grid trading."""
        # Grid gets 3-5% of account balance
        grid_capital_pct = 0.04  # 4% default
        base_grid_capital = account_balance * grid_capital_pct

        # Ensure grid exposure doesn't exceed portfolio limits
        max_grid_exposure = account_balance * self.max_portfolio_exposure_pct * 0.35  # 35% of max exposure
        available_for_grid = max_grid_exposure - self._get_total_grid_exposure()

        # Cap at available exposure
        grid_capital = min(base_grid_capital, available_for_grid)

        # Ensure positive and reasonable minimum
        grid_capital = max(grid_capital, account_balance * 0.01)  # Minimum 1%

        return grid_capital

    def validate_grid_exposure(self, symbol: str, proposed_grid_capital: float, current_exposure: float) -> bool:
        """Validate that proposed grid won't exceed exposure limits."""
        # Check symbol-specific grid limit (50% of allowed grid exposure)
        max_symbol_grid = self.get_grid_capital(self._account_balance, current_exposure, symbol) * 0.5
        if proposed_grid_capital > max_symbol_grid:
            return False

        # Check total grid exposure
        total_grid_after = self._get_total_grid_exposure() + proposed_grid_capital
        max_total_grid = self._account_balance * self.max_portfolio_exposure_pct * 0.35
        if total_grid_after > max_total_grid:
            return False

        return True

    def on_grid_emergency_exit(self, symbol: str):
        """Handle emergency grid exit - reset exposure tracking."""
        if symbol in self.grid_exposure:
            del self.grid_exposure[symbol]
            logger.info(f"Grid exposure reset for {symbol} after emergency exit")

    def _get_total_grid_exposure(self) -> float:
        """Get total current grid exposure across all symbols."""
        return sum(self.grid_exposure.values())
```

**3. Update GridTradingStrategy for RiskManager Integration:**

```python
class GridTradingStrategy:
    def __init__(self, ..., risk_manager=None, grid_levels=5, emergency_stop_pct=0.05):
        # ... existing init ...
        self.risk_manager = risk_manager
        self.grid_levels = grid_levels
        self.emergency_stop_pct = emergency_stop_pct

    def generate_signals(self, symbol: str, multi_tf_data: Dict[str, Dict[str, List[float]]], current_price: float):
        # ... existing signal logic ...

        # Only generate signals if we have RiskManager
        if not self.risk_manager:
            return []

        # Request grid capital from RiskManager
        account_balance = self._get_account_balance()  # Need to pass this
        current_exposure = self._get_current_exposure()  # Need to pass this
        grid_capital = self.risk_manager.get_grid_capital(account_balance, current_exposure, symbol)

        if grid_capital <= 0:
            logger.debug(f"Insufficient grid capital for {symbol}")
            return []

        # Create signal with grid parameters
        signal = Signal(
            strategy=StrategyType.GRID_TRADING,
            asset=symbol,
            side=OrderSide.BUY,  # Grid is neutral
            entry_price=current_price,
            quantity=grid_capital / current_price,  # Total grid quantity
            risk_profile="low",
            grid_levels=self.grid_levels,
            grid_capital=grid_capital,
            emergency_stop_pct=self.emergency_stop_pct
        )

        return [signal]
```

**4. Implement Comprehensive Grid Execution:**

```python
def _execute_grid_signal(self, signal: Signal):
    """Execute grid with full risk controls and emergency stops."""

    symbol = signal.asset

    # Validate grid capital with RiskManager
    account_balance = self._get_account_balance()
    current_exposure = self._get_current_exposure()

    if not self.risk_manager.validate_grid_exposure(symbol, signal.grid_capital, current_exposure):
        logging.warning(f"Grid exposure validation failed for {symbol}")
        return

    # Calculate capital per level (equal allocation - no martingale)
    capital_per_level = signal.grid_capital / signal.grid_levels

    # Calculate grid levels around current price
    grid_levels = self._calculate_grid_levels(signal, capital_per_level)

    # Place EMERGENCY STOP first (MANDATORY)
    emergency_stop_price = self._calculate_emergency_stop(signal)
    try:
        emergency_order = self.client.place_order(
            symbol,
            'sell' if signal.side == OrderSide.BUY else 'buy',  # Opposite side
            signal.quantity,  # Full grid quantity
            'stop',
            stop_price=emergency_stop_price
        )
        logging.info(f"EMERGENCY STOP placed for {symbol} grid @ {emergency_stop_price}")
    except Exception as e:
        logging.critical(f"CRITICAL: Cannot place emergency stop for {symbol} grid: {e}")
        return  # ABORT GRID - cannot proceed without emergency stop

    # Register grid with RiskManager
    self.risk_manager.grid_exposure[symbol] = signal.grid_capital

    # Place grid levels
    successful_orders = []
    for level in grid_levels:
        try:
            order = self.client.place_order(
                symbol,
                level['side'],
                level['quantity'],
                'limit',
                price=level['price']
            )

            if order and order.get('id'):
                successful_orders.append(order)
                self._save_grid_position(symbol, signal.strategy.value, level, order['id'])

                # Update RiskManager exposure
                level_exposure = level['quantity'] * level['price']
                self.risk_manager.grid_exposure[symbol] += level_exposure

                logging.info(f"Grid level {level['level']} placed: {symbol} {level['side']} {level['quantity']} @ {level['price']}")

        except Exception as e:
            logging.error(f"Failed to place grid level {level['level']}: {e}")
            break

    # Log grid creation
    grid_id = f"{symbol}_{int(signal.entry_time.timestamp())}"
    logging.info(f"Grid created: ID={grid_id}, Symbol={symbol}, Capital=${signal.grid_capital:.2f}, "
                f"Levels={len(successful_orders)}/{len(grid_levels)}, EmergencyStop=${emergency_stop_price}")

    # Store grid metadata for monitoring
    self._active_grids[grid_id] = {
        'symbol': symbol,
        'capital': signal.grid_capital,
        'levels': len(successful_orders),
        'emergency_stop': emergency_stop_price,
        'emergency_order_id': emergency_order.get('id'),
        'created_at': signal.entry_time
    }
```

**5. Add Emergency Stop Management:**

```python
def _calculate_emergency_stop(self, signal: Signal) -> float:
    """Calculate emergency stop price for grid."""
    # Emergency stop at X% from grid midpoint
    midpoint = signal.entry_price
    stop_distance = midpoint * signal.emergency_stop_pct

    if signal.side == OrderSide.BUY:
        return midpoint - stop_distance  # Stop below for long grid
    else:
        return midpoint + stop_distance  # Stop above for short grid

def _handle_grid_emergency(self, symbol: str, reason: str):
    """Handle grid emergency exit."""
    logging.critical(f"Grid emergency exit for {symbol}: {reason}")

    # Cancel all grid orders
    # Close all grid positions
    # Disable grid for symbol
    # Reset RiskManager exposure

    self.risk_manager.on_grid_emergency_exit(symbol)

    # Find and cancel grid orders
    for grid_id, grid_data in self._active_grids.items():
        if grid_data['symbol'] == symbol:
            # Cancel emergency stop
            if grid_data.get('emergency_order_id'):
                try:
                    self.client.cancel_order(grid_data['emergency_order_id'])
                except Exception as e:
                    logging.error(f"Failed to cancel emergency order: {e}")

            # Cancel all grid level orders
            # Close any filled positions at market

            del self._active_grids[grid_id]
            break
```

**6. Add Grid Monitoring and Dynamic Controls:**

```python
def _monitor_grid_health(self):
    """Monitor active grids for emergency conditions."""
    for grid_id, grid_data in list(self._active_grids.items()):
        symbol = grid_data['symbol']

        # Check regime change
        regime = self.regime_detector.detect_regime_cached(symbol, {})  # Simplified
        if regime in [MarketRegime.TRENDING_STRONG, MarketRegime.TRENDING_MODERATE]:
            self._handle_grid_emergency(symbol, f"Regime changed to {regime.value}")

        # Check ADX threshold
        # Check volatility spikes
        # Implement dynamic spacing adjustments
```
</implementation>

<output>
Implement comprehensive grid trading risk controls according to specification:

**RiskManager Extensions:**
- Add get_grid_capital(), validate_grid_exposure(), on_grid_emergency_exit() methods
- Track grid exposure separately from regular positions
- Prevent overlapping grids and enforce portfolio caps

**StrategyManager Updates:**
- Enforce regime constraints (only RANGING_CALM/VOLATILE/INDECISIVE)
- Hard disable grid in TRENDING regimes
- Validate regime before emitting grid signals

**GridTradingStrategy Updates:**
- Request capital from RiskManager instead of bypassing
- Include grid parameters in signals (levels, capital, emergency stop %)
- Equal capital allocation across levels (no martingale)

**TradingBot Grid Execution:**
- Mandatory emergency stop placement before grid orders
- Capital allocation: grid_capital / grid_levels
- Quantity calculation: capital_per_level / grid_price
- Comprehensive logging with Grid ID, capital, levels, emergency stop
- Emergency exit handling for regime changes, ADX spikes, range breaks

**Dynamic Controls:**
- Volatility-based spacing and level adjustments
- Cooldown periods after emergency exits
- Prevent re-arming until conditions normalize

**Failure Handling:**
- Abort grid if emergency stop cannot be placed
- Handle partial fills and exchange disconnects
- Comprehensive error logging and recovery

Grid trading is now capital-aware, regime-aware, and fail-safe instead of dangerous.
</output>

<verification>
After implementation:
1. Test regime enforcement - grid signals only in allowed regimes
2. Verify RiskManager capital allocation (3-5% of account)
3. Check emergency stop placement (mandatory before grid orders)
4. Test grid capital division across levels (equal allocation)
5. Verify exposure limits (35% of portfolio max, 50% per symbol)
6. Test emergency exit triggers (regime change, ADX threshold)
7. Confirm comprehensive logging (Grid ID, capital, levels, exit reasons)
8. Test failure scenarios (stop placement failure, partial fills)
</verification>

<success_criteria>
- ✅ Grid trading ONLY in RANGING_CALM/VOLATILE/INDECISIVE regimes
- ✅ RiskManager provides get_grid_capital() and validate_grid_exposure()
- ✅ Emergency stops placed before any grid orders (MANDATORY)
- ✅ Equal capital allocation across grid levels (no martingale)
- ✅ Grid exposure capped at 35% of portfolio max, 50% per symbol
- ✅ Comprehensive logging with Grid ID and exit reasons
- ✅ Emergency exits on regime changes, ADX spikes, range breaks
- ✅ Proper failure handling (abort if stop fails, handle disconnects)
- ✅ Grid trading is now controlled and safe, not dangerous
</success_criteria>