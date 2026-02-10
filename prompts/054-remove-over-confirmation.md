# Prompt 054: Remove Over-Confirmation from StrategyManager

## Context
With the 3-phase architecture from Prompt 053 in place, we now refactor StrategyManager to remove redundant validation that was causing trade starvation.

## Problem
Current StrategyManager has multiple layers of validation:
- `_validate_signal()` with 8 validation flags
- Multi-timeframe alignment checks
- RSI/BB/MA validation at orchestrator level
- Confidence hard thresholds (60% minimum)
- Cooldown checks mixed with signal generation

This creates "over-confirmation" where strategy already validated its setup, then orchestrator re-validates with different criteria.

## Solution
Remove redundant checks from StrategyManager. The 3-phase architecture handles:
- Phase 1: Regime/permission (already checked before strategy runs)
- Phase 2: Strategy's own validation (trust the strategy)
- Phase 3: Execution safety (exposure, sizing)

## Implementation

### File: `trading_bot_v2/strategy_manager.py`

#### REMOVE These Methods/Checks

```python
# REMOVE: _validate_signal() - Phase 3 handles execution safety
# REMOVE: _check_multi_timeframe_alignment() - Strategy decides its own TF needs
# REMOVE: _validate_indicator_values() - Strategy already checked indicators
# REMOVE: confidence >= 0.6 hard threshold - Confidence affects sizing, not permission
```

#### BEFORE (Current Over-Confirmation)

```python
def generate_signals_for_market(self, symbol, market_data, current_price):
    signals = []
    regime = self.regime_detector.get_regime(market_data['4h'])

    for strategy in self._get_active_strategies(regime):
        raw_signals = strategy.generate_signals(symbol, market_data, current_price)

        for signal in raw_signals:
            # OVER-CONFIRMATION STARTS HERE
            if signal.confidence < 0.6:  # Hard threshold!
                continue

            if not self._check_multi_timeframe_alignment(signal, market_data):
                continue

            if not self._validate_indicator_values(signal, market_data):
                continue

            if not self._validate_signal(signal):  # 8 more checks!
                continue

            signals.append(signal)

    return signals
```

#### AFTER (Trusting 3-Phase Architecture)

```python
def generate_signals_for_market(self, symbol: str, market_data: Dict,
                                 current_price: float) -> List[Signal]:
    """
    Generate signals using 3-phase architecture.

    Phase 1 (Regime Permission) is checked BEFORE this method is called.
    Phase 2 (Strategy Attempt) happens in strategy.generate_signals().
    Phase 3 (Execution Filtering) happens AFTER this method returns.

    This method ONLY orchestrates Phase 2 - it does NOT second-guess strategies.
    """
    signals = []

    # Get regime for strategy routing (NOT for re-validation)
    regime = self.regime_detector.get_regime(market_data.get('4h', market_data))
    active_strategies = self._get_active_strategies(regime)

    for strategy in active_strategies:
        try:
            # Phase 2: Trust strategy to generate valid signals
            strategy_signals = strategy.generate_signals(symbol, market_data, current_price)

            if not strategy_signals:
                continue

            for signal in strategy_signals:
                # DO NOT re-validate here - that's what caused over-confirmation
                # Confidence will affect sizing in Phase 3, not permission here

                # Only add basic sanity check (not validation)
                if signal and signal.entry_price > 0:
                    signals.append(signal)
                    logger.debug(
                        f"Signal from {strategy.__class__.__name__}: "
                        f"{signal.side.value} {symbol} @ {signal.entry_price:.2f} "
                        f"confidence={signal.confidence:.2f}"
                    )

        except Exception as e:
            logger.error(f"Strategy {strategy.__class__.__name__} error: {e}")
            continue

    # Log signal count (for debugging)
    if signals:
        logger.info(f"Generated {len(signals)} signals for {symbol} in {regime.name}")

    return signals
```

#### KEEP These Methods

```python
# KEEP: _get_active_strategies(regime) - Routes to correct strategies per regime
# KEEP: get_regime() call - Needed for routing, not validation
# KEEP: max_concurrent_strategies limit - But apply AFTER signals generated
# KEEP: global emergency stop - Handled by Phase 1
```

### Update Strategy Routing

```python
def _get_active_strategies(self, regime) -> List:
    """
    Get strategies active for current regime.

    This is ROUTING, not VALIDATION.
    Strategies are trusted to generate signals appropriate for the regime.
    """
    # Map regime to allowed strategies
    regime_map = {
        'TRENDING_STRONG': [self.ma_crossover, self.trend_following],
        'RANGING_VOLATILE': [self.grid_trading],
        'RANGING_CALM': [self.mean_reversion],
        'INDECISIVE': [],  # No strategies when market unclear
    }

    # Get base strategies for regime
    strategies = regime_map.get(regime.name, [])

    # Always include liquidation capture (runs in all regimes)
    if self.liquidation_capture and self.liquidation_capture not in strategies:
        strategies.append(self.liquidation_capture)

    # Filter out disabled strategies
    return [s for s in strategies if s is not None and self._is_enabled(s)]

def _is_enabled(self, strategy) -> bool:
    """Check if strategy is enabled in config."""
    strategy_name = strategy.__class__.__name__
    enable_flags = {
        'MeanReversionStrategy': self.config.enable_mean_reversion,
        'MACrossoverStrategy': self.config.enable_ma_crossover,
        'GridTradingStrategy': self.config.enable_grid_trading,
        'TrendFollowingStrategy': self.config.enable_trend_following,
        'LiquidationCaptureStrategy': self.config.enable_liquidation_capture,
    }
    return enable_flags.get(strategy_name, True)
```

### Remove These Helper Methods

Delete these methods entirely (they cause over-confirmation):

```python
# DELETE THIS METHOD
def _validate_signal(self, signal: Signal) -> bool:
    """
    REMOVED: This validated 8 flags that strategies already checked.
    Phase 3 now handles execution safety.
    """
    pass  # DELETE ENTIRE METHOD

# DELETE THIS METHOD
def _check_multi_timeframe_alignment(self, signal, market_data) -> bool:
    """
    REMOVED: Each strategy decides its own timeframe requirements.
    RSI strategy might need 15m+1h alignment.
    MA strategy might only need 4h.
    Orchestrator should not impose uniform requirements.
    """
    pass  # DELETE ENTIRE METHOD

# DELETE THIS METHOD
def _validate_indicator_values(self, signal, market_data) -> bool:
    """
    REMOVED: Strategy already validated its indicators.
    Orchestrator re-checking with different thresholds causes conflicts.
    """
    pass  # DELETE ENTIRE METHOD
```

## Integration with SignalPipeline

In `trading_bot.py`, update `_check_signals()` to use the pipeline:

```python
def _check_signals(self, market_data_by_symbol: Dict):
    """
    Check for signals using 3-phase pipeline.
    """
    from signal_phases import SignalPipeline, Phase1RegimePermission, Phase3ExecutionFilter

    # Initialize phases (do once in __init__, shown here for clarity)
    phase1 = Phase1RegimePermission(
        self.regime_detector,
        self.risk_manager,
        self.config
    )
    phase3 = Phase3ExecutionFilter(
        self.risk_manager,
        self.client,
        self.config
    )
    pipeline = SignalPipeline(
        phase1,
        self.strategy_manager.strategies,
        phase3,
        self.position_sizer
    )

    for symbol, market_data in market_data_by_symbol.items():
        current_price = self._get_current_price(symbol)

        # Process through pipeline
        approved_signals = pipeline.process(symbol, market_data, current_price)

        for signal, size, decision in approved_signals:
            self._execute_signal(signal, size)
```

## Testing

```python
def test_no_over_confirmation():
    """Verify StrategyManager doesn't re-validate signals."""
    from strategy_manager import StrategyManager

    sm = StrategyManager()

    # Check that over-confirmation methods are removed
    assert not hasattr(sm, '_validate_signal'), "_validate_signal should be removed"
    assert not hasattr(sm, '_check_multi_timeframe_alignment'), "MTF alignment check should be removed"
    assert not hasattr(sm, '_validate_indicator_values'), "Indicator validation should be removed"

    print("Over-confirmation methods removed!")


def test_strategy_signals_not_filtered():
    """Verify generate_signals_for_market passes through strategy signals."""
    from strategy_manager import StrategyManager

    class MockStrategy:
        def generate_signals(self, symbol, data, price):
            from models import Signal, OrderSide
            return [Signal(
                symbol=symbol,
                side=OrderSide.BUY,
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                confidence=0.45,  # Below old 0.6 threshold!
                strategy=None
            )]

    sm = StrategyManager()
    sm.mean_reversion = MockStrategy()

    # Create ranging calm regime data
    market_data = {'4h': {'close': [100] * 200}}

    signals = sm.generate_signals_for_market('TEST', market_data, 100.0)

    # Signal with 0.45 confidence should NOT be filtered
    # (Old code would have blocked it)
    assert len(signals) > 0, "Low confidence signal should pass through"
    assert signals[0].confidence == 0.45, "Confidence should be preserved"

    print("Signals pass through without over-confirmation!")
```

## Verification Checklist

- [ ] `_validate_signal()` method removed
- [ ] `_check_multi_timeframe_alignment()` method removed
- [ ] `_validate_indicator_values()` method removed
- [ ] No confidence >= 0.6 hard threshold
- [ ] `generate_signals_for_market()` only does routing, not validation
- [ ] Strategies trusted to generate valid signals
- [ ] Tests pass

## Next Prompt
Prompt 055 will implement confidence-to-sizing transformation so low confidence reduces size instead of blocking.
