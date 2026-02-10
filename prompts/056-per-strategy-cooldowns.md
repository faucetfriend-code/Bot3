# Prompt 056: Per-Strategy Per-Timeframe Cooldowns

## Context
With 3-phase architecture (053) and confidence sizing (055), the final piece of Strategy Overhaul is smarter cooldowns.

## Problem
Current global cooldown system:
- Single cooldown per symbol across ALL strategies
- Same cooldown duration regardless of timeframe (1m scalp = 4h swing)
- Cooldowns applied BEFORE opportunity density exists
- A 4h MA crossover triggers cooldown that blocks 1m liquidation capture

## Solution
Implement per-strategy, per-timeframe cooldowns:
- Each strategy has its own cooldown
- Cooldown duration varies by timeframe (1m = 60s, 4h = 4 hours)
- Cooldowns only apply to same strategy + timeframe combination
- Fast strategies (grid, liquidation) need short cooldowns
- Slow strategies (MA crossover, trend) need longer cooldowns

## Implementation

### File: `trading_bot_v2/cooldown_manager.py` (NEW)

```python
"""
Cooldown Manager
================

Per-strategy, per-timeframe cooldown tracking.

Key principles:
1. Cooldowns are SEPARATE per strategy - one strategy's trade doesn't block another
2. Cooldowns scale with timeframe - 1m strategies need fast cooldowns
3. Cooldowns track both trades AND signal generation (to prevent rapid re-evaluation)
4. Emergency cooldown can block ALL strategies (circuit breaker support)

Cooldown Hierarchy:
    symbol -> strategy -> timeframe -> last_action_time
"""

from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Example files", "core_logic"))
from config import StrategyType


class CooldownType(Enum):
    """Types of cooldowns."""
    TRADE = "trade"           # After executing a trade
    SIGNAL = "signal"         # After generating a signal (even if not executed)
    LOSS = "loss"            # After a losing trade (longer cooldown)
    EMERGENCY = "emergency"   # Global emergency cooldown


@dataclass
class CooldownEntry:
    """A single cooldown entry."""
    strategy: StrategyType
    timeframe: str
    cooldown_type: CooldownType
    expires_at: datetime
    reason: str


@dataclass
class CooldownConfig:
    """Configuration for cooldowns by strategy and timeframe."""

    # Default cooldowns by timeframe (in seconds)
    TIMEFRAME_DEFAULTS = {
        '1m': 60,       # 1 minute cooldown for 1m trades
        '5m': 300,      # 5 minutes for 5m trades
        '15m': 900,     # 15 minutes for 15m trades
        '1h': 3600,     # 1 hour for 1h trades
        '4h': 14400,    # 4 hours for 4h trades
        '1d': 86400,    # 1 day for daily trades
    }

    # Strategy-specific multipliers (some strategies need shorter/longer cooldowns)
    STRATEGY_MULTIPLIERS = {
        StrategyType.GRID_TRADING: 0.5,           # Grids trade frequently, half cooldown
        StrategyType.LIQUIDATION_CAPTURE: 0.25,   # Very fast, quarter cooldown
        StrategyType.MEAN_REVERSION: 1.0,         # Normal
        StrategyType.MA_CROSSOVER: 2.0,           # Slow strategy, double cooldown
        StrategyType.TREND_FOLLOWING: 1.5,        # Somewhat slow
    }

    # Loss cooldown multiplier (longer cooldown after loss)
    LOSS_MULTIPLIER = 2.0

    # Minimum cooldown regardless of configuration (safety)
    MIN_COOLDOWN_SECONDS = 10


class CooldownManager:
    """
    Manages cooldowns per symbol, strategy, and timeframe.

    Usage:
        manager = CooldownManager()

        # Check if can trade
        if manager.can_trade('BTC', StrategyType.GRID_TRADING, '1m'):
            execute_trade()
            manager.set_cooldown('BTC', StrategyType.GRID_TRADING, '1m', CooldownType.TRADE)

        # Check if can generate signal
        if manager.can_generate_signal('ETH', StrategyType.MEAN_REVERSION, '4h'):
            signal = strategy.generate_signals()
            manager.set_cooldown('ETH', StrategyType.MEAN_REVERSION, '4h', CooldownType.SIGNAL)
    """

    def __init__(self, config: CooldownConfig = None):
        """
        Initialize CooldownManager.

        Args:
            config: Optional CooldownConfig, uses defaults if not provided
        """
        self.config = config or CooldownConfig()

        # Active cooldowns: {symbol: {strategy: {timeframe: CooldownEntry}}}
        self._cooldowns: Dict[str, Dict[StrategyType, Dict[str, CooldownEntry]]] = {}

        # Emergency global cooldown
        self._emergency_cooldown: Optional[datetime] = None

        logger.info("CooldownManager initialized")

    def can_trade(self, symbol: str, strategy: StrategyType, timeframe: str) -> Tuple[bool, str]:
        """
        Check if trading is allowed (not in cooldown).

        Args:
            symbol: Trading symbol
            strategy: Strategy type
            timeframe: Signal timeframe

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Check emergency cooldown first
        if self._emergency_cooldown and datetime.now() < self._emergency_cooldown:
            remaining = (self._emergency_cooldown - datetime.now()).seconds
            return False, f"Emergency cooldown active ({remaining}s remaining)"

        # Check specific cooldown
        entry = self._get_cooldown(symbol, strategy, timeframe)

        if entry and datetime.now() < entry.expires_at:
            remaining = (entry.expires_at - datetime.now()).seconds
            return False, f"Cooldown active: {entry.reason} ({remaining}s remaining)"

        return True, "No cooldown"

    def can_generate_signal(self, symbol: str, strategy: StrategyType,
                            timeframe: str) -> Tuple[bool, str]:
        """
        Check if signal generation is allowed.

        Uses shorter cooldown than trade execution to allow re-evaluation
        while still preventing rapid signal spam.

        Args:
            symbol: Trading symbol
            strategy: Strategy type
            timeframe: Signal timeframe

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Signal cooldown is 25% of trade cooldown
        # This allows strategies to re-evaluate more frequently than they trade
        entry = self._get_cooldown(symbol, strategy, timeframe)

        if entry and entry.cooldown_type == CooldownType.SIGNAL:
            if datetime.now() < entry.expires_at:
                remaining = (entry.expires_at - datetime.now()).seconds
                return False, f"Signal cooldown ({remaining}s remaining)"

        return True, "No signal cooldown"

    def set_cooldown(self, symbol: str, strategy: StrategyType, timeframe: str,
                     cooldown_type: CooldownType, reason: str = None) -> CooldownEntry:
        """
        Set a cooldown after action.

        Args:
            symbol: Trading symbol
            strategy: Strategy type
            timeframe: Signal timeframe
            cooldown_type: Type of cooldown (TRADE, SIGNAL, LOSS)
            reason: Optional reason string

        Returns:
            CooldownEntry that was set
        """
        duration = self._calculate_duration(strategy, timeframe, cooldown_type)
        expires_at = datetime.now() + timedelta(seconds=duration)

        entry = CooldownEntry(
            strategy=strategy,
            timeframe=timeframe,
            cooldown_type=cooldown_type,
            expires_at=expires_at,
            reason=reason or f"{cooldown_type.value} cooldown"
        )

        # Initialize nested dicts if needed
        if symbol not in self._cooldowns:
            self._cooldowns[symbol] = {}
        if strategy not in self._cooldowns[symbol]:
            self._cooldowns[symbol][strategy] = {}

        self._cooldowns[symbol][strategy][timeframe] = entry

        logger.debug(
            f"Cooldown set: {symbol} {strategy.value} {timeframe} "
            f"-> {cooldown_type.value} for {duration}s"
        )

        return entry

    def set_emergency_cooldown(self, duration_seconds: int, reason: str):
        """
        Set global emergency cooldown (blocks ALL trading).

        Args:
            duration_seconds: How long to block
            reason: Why emergency cooldown was triggered
        """
        self._emergency_cooldown = datetime.now() + timedelta(seconds=duration_seconds)
        logger.critical(f"EMERGENCY COOLDOWN: {reason} ({duration_seconds}s)")

    def clear_emergency_cooldown(self):
        """Clear emergency cooldown (resume trading)."""
        self._emergency_cooldown = None
        logger.warning("Emergency cooldown CLEARED - trading resumed")

    def clear_cooldown(self, symbol: str, strategy: StrategyType, timeframe: str):
        """Manually clear a specific cooldown."""
        if (symbol in self._cooldowns and
            strategy in self._cooldowns[symbol] and
            timeframe in self._cooldowns[symbol][strategy]):
            del self._cooldowns[symbol][strategy][timeframe]
            logger.debug(f"Cooldown cleared: {symbol} {strategy.value} {timeframe}")

    def get_all_cooldowns(self, symbol: str = None) -> Dict:
        """Get all active cooldowns, optionally filtered by symbol."""
        now = datetime.now()
        result = {}

        for sym, strategies in self._cooldowns.items():
            if symbol and sym != symbol:
                continue

            for strat, timeframes in strategies.items():
                for tf, entry in timeframes.items():
                    if entry.expires_at > now:
                        if sym not in result:
                            result[sym] = {}
                        if strat.value not in result[sym]:
                            result[sym][strat.value] = {}
                        result[sym][strat.value][tf] = {
                            'expires_at': entry.expires_at.isoformat(),
                            'remaining_seconds': (entry.expires_at - now).seconds,
                            'type': entry.cooldown_type.value,
                            'reason': entry.reason
                        }

        return result

    def _get_cooldown(self, symbol: str, strategy: StrategyType,
                      timeframe: str) -> Optional[CooldownEntry]:
        """Get cooldown entry if exists."""
        return (self._cooldowns
                .get(symbol, {})
                .get(strategy, {})
                .get(timeframe))

    def _calculate_duration(self, strategy: StrategyType, timeframe: str,
                            cooldown_type: CooldownType) -> int:
        """Calculate cooldown duration in seconds."""
        # Base duration from timeframe
        base = self.config.TIMEFRAME_DEFAULTS.get(timeframe, 300)  # Default 5 min

        # Apply strategy multiplier
        strategy_mult = self.config.STRATEGY_MULTIPLIERS.get(strategy, 1.0)
        duration = base * strategy_mult

        # Apply loss multiplier if applicable
        if cooldown_type == CooldownType.LOSS:
            duration *= self.config.LOSS_MULTIPLIER

        # Signal cooldowns are shorter (for re-evaluation)
        if cooldown_type == CooldownType.SIGNAL:
            duration *= 0.25

        # Enforce minimum
        duration = max(duration, self.config.MIN_COOLDOWN_SECONDS)

        return int(duration)

    def cleanup_expired(self):
        """Remove expired cooldowns (call periodically)."""
        now = datetime.now()
        cleaned = 0

        for symbol in list(self._cooldowns.keys()):
            for strategy in list(self._cooldowns[symbol].keys()):
                for timeframe in list(self._cooldowns[symbol][strategy].keys()):
                    entry = self._cooldowns[symbol][strategy][timeframe]
                    if entry.expires_at < now:
                        del self._cooldowns[symbol][strategy][timeframe]
                        cleaned += 1

                # Clean empty dicts
                if not self._cooldowns[symbol][strategy]:
                    del self._cooldowns[symbol][strategy]
            if not self._cooldowns[symbol]:
                del self._cooldowns[symbol]

        if cleaned > 0:
            logger.debug(f"Cleaned {cleaned} expired cooldowns")
```

### Integration with SignalPipeline

Update `signal_phases.py` to use CooldownManager:

```python
class Phase1RegimePermission:
    def __init__(self, regime_detector, risk_manager, config, cooldown_manager=None):
        # ... existing init ...
        self.cooldown_manager = cooldown_manager

    def check(self, symbol: str, strategy_type: StrategyType,
              market_data: Dict[str, Any], timeframe: str = '4h') -> PhaseDecision:
        """Check including cooldown."""
        # ... existing checks ...

        # Check cooldown (if manager provided)
        if self.cooldown_manager:
            can_trade, reason = self.cooldown_manager.can_trade(symbol, strategy_type, timeframe)
            if not can_trade:
                return PhaseDecision(
                    result=PhaseResult.BLOCK,
                    reason=reason,
                    details={'cooldown': True}
                )

        return PhaseDecision(result=PhaseResult.PASS, reason="All checks passed")
```

### Integration with TradingBot

Update `trading_bot.py`:

```python
class TradingBot:
    def __init__(self):
        # ... existing init ...
        from cooldown_manager import CooldownManager
        self.cooldown_manager = CooldownManager()

    def _execute_signal(self, signal, size):
        """Execute signal and set cooldown."""
        # ... existing execution ...

        # Set cooldown after trade
        self.cooldown_manager.set_cooldown(
            signal.symbol,
            signal.strategy,
            signal.timeframe or '4h',
            CooldownType.TRADE,
            reason=f"Executed {signal.side.value} @ {signal.entry_price}"
        )

    def _handle_trade_result(self, symbol, strategy, timeframe, pnl):
        """Handle trade result and set appropriate cooldown."""
        if pnl < 0:
            # Loss - set longer cooldown
            self.cooldown_manager.set_cooldown(
                symbol, strategy, timeframe,
                CooldownType.LOSS,
                reason=f"Loss: {pnl:.2f}"
            )
```

## Configuration

Add to `.env`:

```bash
# Cooldown Configuration
COOLDOWN_1M_SECONDS=60
COOLDOWN_5M_SECONDS=300
COOLDOWN_15M_SECONDS=900
COOLDOWN_1H_SECONDS=3600
COOLDOWN_4H_SECONDS=14400

# Strategy multipliers (lower = shorter cooldown)
COOLDOWN_GRID_MULTIPLIER=0.5
COOLDOWN_LIQUIDATION_MULTIPLIER=0.25
COOLDOWN_MA_CROSSOVER_MULTIPLIER=2.0

# Loss cooldown multiplier
COOLDOWN_LOSS_MULTIPLIER=2.0
```

## Behavior Examples

### Before (Global Cooldown)
```
10:00 - MA Crossover trades BTC (4h signal)
        Global cooldown set: 4 hours

10:05 - Liquidation cascade detected on BTC!
        BLOCKED - global cooldown active
        Missed opportunity
```

### After (Per-Strategy Cooldowns)
```
10:00 - MA Crossover trades BTC (4h signal)
        MA Crossover cooldown: 4h * 2.0 = 8 hours
        Liquidation Capture cooldown: unaffected

10:05 - Liquidation cascade detected on BTC!
        Check MA Crossover cooldown: blocked (doesn't apply - different strategy)
        Check Liquidation Capture cooldown: clear!
        TRADE EXECUTED

10:06 - Another liquidation signal
        Liquidation cooldown: 4h * 0.25 = 1 hour
        BLOCKED - within liquidation cooldown
```

## Testing

```python
def test_separate_strategy_cooldowns():
    """Test that strategy cooldowns are independent."""
    from cooldown_manager import CooldownManager, CooldownType
    from config import StrategyType

    manager = CooldownManager()

    # Set cooldown for MA crossover
    manager.set_cooldown('BTC', StrategyType.MA_CROSSOVER, '4h', CooldownType.TRADE)

    # MA crossover should be blocked
    can_trade, reason = manager.can_trade('BTC', StrategyType.MA_CROSSOVER, '4h')
    assert not can_trade, "MA crossover should be in cooldown"

    # Grid trading should NOT be blocked
    can_trade, reason = manager.can_trade('BTC', StrategyType.GRID_TRADING, '4h')
    assert can_trade, "Grid trading should NOT be blocked by MA cooldown"

    # Mean reversion should NOT be blocked
    can_trade, reason = manager.can_trade('BTC', StrategyType.MEAN_REVERSION, '4h')
    assert can_trade, "Mean reversion should NOT be blocked"

    print("Strategy cooldowns are independent!")


def test_timeframe_scaling():
    """Test cooldown duration scales with timeframe."""
    from cooldown_manager import CooldownManager, CooldownType, CooldownConfig
    from config import StrategyType

    config = CooldownConfig()
    manager = CooldownManager(config)

    # 1m cooldown should be short
    entry_1m = manager.set_cooldown('TEST', StrategyType.MEAN_REVERSION, '1m', CooldownType.TRADE)
    duration_1m = (entry_1m.expires_at - datetime.now()).seconds

    # 4h cooldown should be longer
    entry_4h = manager.set_cooldown('TEST', StrategyType.MEAN_REVERSION, '4h', CooldownType.TRADE)
    duration_4h = (entry_4h.expires_at - datetime.now()).seconds

    assert duration_4h > duration_1m * 10, "4h cooldown should be much longer than 1m"
    print(f"1m cooldown: {duration_1m}s, 4h cooldown: {duration_4h}s")


def test_loss_longer_cooldown():
    """Test that loss trades get longer cooldowns."""
    from cooldown_manager import CooldownManager, CooldownType
    from config import StrategyType
    from datetime import datetime

    manager = CooldownManager()

    # Normal trade cooldown
    entry_trade = manager.set_cooldown('TEST', StrategyType.MEAN_REVERSION, '1h', CooldownType.TRADE)
    duration_trade = (entry_trade.expires_at - datetime.now()).seconds

    # Clear and set loss cooldown
    manager.clear_cooldown('TEST', StrategyType.MEAN_REVERSION, '1h')
    entry_loss = manager.set_cooldown('TEST', StrategyType.MEAN_REVERSION, '1h', CooldownType.LOSS)
    duration_loss = (entry_loss.expires_at - datetime.now()).seconds

    assert duration_loss > duration_trade, "Loss cooldown should be longer"
    print(f"Trade cooldown: {duration_trade}s, Loss cooldown: {duration_loss}s")


if __name__ == "__main__":
    test_separate_strategy_cooldowns()
    test_timeframe_scaling()
    test_loss_longer_cooldown()
    print("\nAll cooldown tests passed!")
```

## Verification Checklist

- [ ] `cooldown_manager.py` created
- [ ] Cooldowns are per-symbol, per-strategy, per-timeframe
- [ ] Timeframe scaling implemented
- [ ] Strategy multipliers applied
- [ ] Loss trades get longer cooldowns
- [ ] Emergency cooldown support
- [ ] Integration with SignalPipeline
- [ ] Tests pass

## Next Prompt
Prompt 057 begins "Too Tight" optimizations - loosening grid trading thresholds.
