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

from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
from loguru import logger

# Use relative imports from trading_bot_v2 package
from .config import StrategyType


class CooldownType(Enum):
    """Types of cooldowns."""

    TRADE = "trade"  # After executing a trade
    SIGNAL = "signal"  # After generating a signal (even if not executed)
    LOSS = "loss"  # After a losing trade (longer cooldown)
    EMERGENCY = "emergency"  # Global emergency cooldown


@dataclass
class CooldownEntry:
    """A single cooldown entry."""

    strategy: StrategyType
    timeframe: str
    cooldown_type: CooldownType
    expires_at: datetime
    reason: str


class CooldownConfig:
    """Configuration for cooldowns by strategy and timeframe."""

    # Default cooldowns by timeframe (in seconds)
    TIMEFRAME_DEFAULTS = {
        "1m": 60,  # 1 minute cooldown for 1m trades
        "5m": 300,  # 5 minutes for 5m trades
        "15m": 900,  # 15 minutes for 15m trades
        "1h": 3600,  # 1 hour for 1h trades
        "4h": 14400,  # 4 hours for 4h trades
        "1d": 86400,  # 1 day for daily trades
    }

    # Strategy-specific multipliers (some strategies need shorter/longer cooldowns)
    STRATEGY_MULTIPLIERS = {
        StrategyType.GRID_TRADING: 0.5,  # Grids trade frequently, half cooldown
        StrategyType.LIQUIDATION_CAPTURE: 0.25,  # Very fast, quarter cooldown
        StrategyType.MEAN_REVERSION: 1.0,  # Normal
        StrategyType.MA_CROSSOVER: 2.0,  # Slow strategy, double cooldown
        StrategyType.TREND_FOLLOWING: 1.5,  # Somewhat slow
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

    def __init__(self, config: Optional[CooldownConfig] = None) -> None:
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

    def can_trade(
        self, symbol: str, strategy: StrategyType, timeframe: str
    ) -> Tuple[bool, str]:
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

    def can_generate_signal(
        self, symbol: str, strategy: StrategyType, timeframe: str
    ) -> Tuple[bool, str]:
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

    def set_cooldown(
        self,
        symbol: str,
        strategy: StrategyType,
        timeframe: str,
        cooldown_type: CooldownType,
        reason: Optional[str] = None,
    ) -> CooldownEntry:
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
            reason=reason or f"{cooldown_type.value} cooldown",
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

    def set_emergency_cooldown(self, duration_seconds: int, reason: str) -> None:
        """
        Set global emergency cooldown (blocks ALL trading).

        Args:
            duration_seconds: How long to block
            reason: Why emergency cooldown was triggered
        """
        self._emergency_cooldown = datetime.now() + timedelta(seconds=duration_seconds)
        logger.critical(f"EMERGENCY COOLDOWN: {reason} ({duration_seconds}s)")

    def clear_emergency_cooldown(self) -> None:
        """Clear emergency cooldown (resume trading)."""
        self._emergency_cooldown = None
        logger.warning("Emergency cooldown CLEARED - trading resumed")

    def clear_cooldown(
        self, symbol: str, strategy: StrategyType, timeframe: str
    ) -> None:
        """Manually clear a specific cooldown."""
        if (
            symbol in self._cooldowns
            and strategy in self._cooldowns[symbol]
            and timeframe in self._cooldowns[symbol][strategy]
        ):
            del self._cooldowns[symbol][strategy][timeframe]
            logger.debug(f"Cooldown cleared: {symbol} {strategy.value} {timeframe}")

    def get_all_cooldowns(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get all active cooldowns, optionally filtered by symbol."""
        now = datetime.now()
        result: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}

        for sym, strategies in self._cooldowns.items():
            if symbol and sym != symbol:
                continue

            for strat, timeframes in strategies.items():
                for tf, entry in timeframes.items():
                    if entry.expires_at > now:
                        if sym not in result:
                            result[sym] = {}
                        strat_key = (
                            strat.value if hasattr(strat, "value") else str(strat)
                        )
                        if strat_key not in result[sym]:
                            result[sym][strat_key] = {}
                        result[sym][strat_key][tf] = {
                            "expires_at": entry.expires_at.isoformat(),
                            "remaining_seconds": (entry.expires_at - now).seconds,
                            "type": entry.cooldown_type.value,
                            "reason": entry.reason,
                        }

        return result

    def _get_cooldown(
        self, symbol: str, strategy: StrategyType, timeframe: str
    ) -> Optional[CooldownEntry]:
        """Get cooldown entry if exists."""
        return self._cooldowns.get(symbol, {}).get(strategy, {}).get(timeframe)

    def _calculate_duration(
        self, strategy: StrategyType, timeframe: str, cooldown_type: CooldownType
    ) -> int:
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

    def cleanup_expired(self) -> None:
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
