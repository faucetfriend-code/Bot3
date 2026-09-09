"""
Signal Phases Architecture
==========================

3-phase signal processing to separate concerns and prevent over-filtering.

Phase 1: Regime Permission (Hard Gate)
- Regime allowed for strategy type?
- Global risk limits OK?
- Strategy enabled in config?

Phase 2: Strategy Attempt (Soft Gate)
- Strategy generates Signal based on its own logic
- NO second-guessing by orchestrator
- Returns Signal with confidence (0-1)

Phase 3: Execution Filtering (Safety)
- Position exposure limits
- Position sizing validation
- Order validity (min size, etc.)
- Timing (market hours, etc.)
"""

from typing import Dict, Any, List, Tuple
from enum import Enum
from dataclasses import dataclass, field
from loguru import logger

# Use relative imports from trading_bot_v2 package
from .models import Signal
from .config import StrategyType


class PhaseResult(Enum):
    """Result of a signal processing phase."""

    PASS = "pass"
    BLOCK = "block"
    SKIP = "skip"  # Not applicable


@dataclass
class PhaseDecision:
    """Decision from a signal phase."""

    result: PhaseResult
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


class Phase1RegimePermission:
    """
    Phase 1: Regime Permission (Hard Gate)

    Binary check - is trading allowed at all for this strategy in current regime?

    Checks:
    - Regime allows this strategy type
    - Global risk limits OK (circuit breaker not triggered)
    - Strategy is enabled in config

    Does NOT check:
    - Indicator values (that's Phase 2)
    - Position exposure (that's Phase 3)
    - Signal confidence (that's Phase 2's output)
    """

    def __init__(self, regime_detector, risk_manager, config, cooldown_manager=None):
        self.regime_detector = regime_detector
        self.risk_manager = risk_manager
        self.config = config
        self.cooldown_manager = cooldown_manager

        # Regime-to-strategy mapping
        self.regime_strategy_map = {
            "TRENDING_STRONG": [
                StrategyType.TREND_FOLLOWING,
                StrategyType.MA_CROSSOVER,
            ],
            "RANGING_VOLATILE": [StrategyType.GRID_TRADING],
            "RANGING_CALM": [StrategyType.MEAN_REVERSION],
            "INDECISIVE": [],  # No strategies active
        }

        # Strategies that run in ALL regimes
        self.always_active = [StrategyType.LIQUIDATION_CAPTURE]

    def check(
        self,
        symbol: str,
        strategy_type: StrategyType,
        market_data: Dict[str, Any],
        timeframe: str = "4h",
    ) -> PhaseDecision:
        """
        Check if strategy is permitted to attempt signal generation.

        Args:
            symbol: Trading symbol
            strategy_type: Type of strategy wanting to trade
            market_data: Market data dict with 'close', 'high', 'low', 'volume'
            timeframe: Signal timeframe for cooldown check

        Returns:
            PhaseDecision with PASS, BLOCK, or SKIP
        """
        # Check 1: Strategy enabled in config?
        if not self._is_strategy_enabled(strategy_type):
            return PhaseDecision(
                result=PhaseResult.BLOCK,
                reason=f"Strategy {strategy_type.value} disabled in config",
            )

        # Check 2: Global risk OK? (circuit breaker)
        if hasattr(self.risk_manager, "is_circuit_breaker_triggered"):
            if self.risk_manager.is_circuit_breaker_triggered():
                return PhaseDecision(
                    result=PhaseResult.BLOCK,
                    reason="Circuit breaker triggered - all trading halted",
                )

        # Check 3: Cooldown check (if manager provided)
        if self.cooldown_manager:
            can_trade, reason = self.cooldown_manager.can_trade(
                symbol, strategy_type, timeframe
            )
            if not can_trade:
                return PhaseDecision(
                    result=PhaseResult.BLOCK, reason=reason, details={"cooldown": True}
                )

        # Check 4: Always-active strategies bypass regime check
        if strategy_type in self.always_active:
            return PhaseDecision(
                result=PhaseResult.PASS,
                reason=f"{strategy_type.value} is always-active",
                details={"bypass_regime": True},
            )

        # Check 5: Regime allows this strategy?
        current_regime = self._get_regime_name(market_data)
        allowed_strategies = self.regime_strategy_map.get(current_regime, [])

        if strategy_type not in allowed_strategies:
            return PhaseDecision(
                result=PhaseResult.BLOCK,
                reason=f"Regime {current_regime} does not permit {strategy_type.value}",
                details={
                    "current_regime": current_regime,
                    "allowed": [s.value for s in allowed_strategies],
                },
            )

        # All checks passed
        return PhaseDecision(
            result=PhaseResult.PASS,
            reason=f"{strategy_type.value} permitted in {current_regime}",
            details={"current_regime": current_regime},
        )

    def _get_regime_name(self, market_data: Dict) -> str:
        """Get regime name from market data."""
        try:
            regime = self.regime_detector.get_regime(market_data)
            if hasattr(regime, "name"):
                return regime.name
            return str(regime)
        except Exception as e:
            logger.warning(f"Could not get regime: {e}")
            return "INDECISIVE"

    def _is_strategy_enabled(self, strategy_type: StrategyType) -> bool:
        """Check if strategy is enabled in config."""
        enable_map = {
            StrategyType.TREND_FOLLOWING: getattr(
                self.config, "enable_trend_following", True
            ),
            StrategyType.MA_CROSSOVER: getattr(
                self.config, "enable_ma_crossover", True
            ),
            StrategyType.MEAN_REVERSION: getattr(
                self.config, "enable_mean_reversion", True
            ),
            StrategyType.GRID_TRADING: getattr(
                self.config, "enable_grid_trading", True
            ),
            StrategyType.LIQUIDATION_CAPTURE: getattr(
                self.config, "enable_liquidation_capture", True
            ),
        }
        return enable_map.get(strategy_type, True)


class Phase3ExecutionFilter:
    """
    Phase 3: Execution Filtering (Safety)

    Final safety checks before order placement.

    Checks:
    - Position exposure within limits
    - Position size meets minimum
    - Order validity (price, quantity)
    - Timing (not during maintenance, etc.)

    Does NOT check:
    - Indicator values (Phase 2 already did that)
    - Regime (Phase 1 already did that)
    - Signal confidence (handled by sizing, not blocking)
    """

    def __init__(self, risk_manager, client, config):
        self.risk_manager = risk_manager
        self.client = client
        self.config = config

    def check(self, signal: Signal, proposed_size: float) -> PhaseDecision:
        """
        Check if signal execution is safe.

        Args:
            signal: Signal from Phase 2
            proposed_size: Position size after Kelly/sizing calculation

        Returns:
            PhaseDecision with PASS or BLOCK
        """
        # Check 1: Minimum position size
        min_size = getattr(self.config, "min_position_size", 0.001)
        if proposed_size < min_size:
            return PhaseDecision(
                result=PhaseResult.BLOCK,
                reason=f"Position size {proposed_size:.6f} below minimum {min_size}",
                details={"proposed_size": proposed_size, "min_size": min_size},
            )

        # Check 2: Maximum exposure per symbol
        max_exposure = getattr(
            self.config, "max_exposure_per_symbol", 0.25
        )  # 25% of portfolio
        account_balance = self._get_account_balance()

        # Get current exposure
        current_exposure = 0
        if hasattr(self.risk_manager, "get_total_exposure"):
            exp_data = self.risk_manager.get_total_exposure(signal.symbol)
            current_exposure = (
                exp_data.get("total_exposure", 0) if isinstance(exp_data, dict) else 0
            )

        new_exposure = current_exposure + (proposed_size * signal.entry_price)
        exposure_pct = new_exposure / account_balance if account_balance > 0 else 1.0

        if exposure_pct > max_exposure:
            return PhaseDecision(
                result=PhaseResult.BLOCK,
                reason=f"Exposure {exposure_pct:.1%} would exceed max {max_exposure:.1%}",
                details={
                    "current_exposure": current_exposure,
                    "proposed_add": proposed_size * signal.entry_price,
                },
            )

        # Check 3: Valid entry price
        if signal.entry_price <= 0:
            return PhaseDecision(
                result=PhaseResult.BLOCK, reason="Invalid entry price (<=0)"
            )

        # Check 4: Valid stop loss
        if signal.stop_loss and signal.stop_loss <= 0:
            return PhaseDecision(
                result=PhaseResult.BLOCK, reason="Invalid stop loss (<=0)"
            )

        # All checks passed
        return PhaseDecision(
            result=PhaseResult.PASS,
            reason="Execution checks passed",
            details={"final_size": proposed_size, "exposure_pct": exposure_pct},
        )

    def _get_account_balance(self) -> float:
        """Get current account balance."""
        try:
            if self.client:
                balance = self.client.get_balance()
                if isinstance(balance, dict):
                    return float(
                        balance.get("total", 0) or balance.get("equity", 0) or 10000
                    )
                return float(balance) if balance else 10000
        except Exception as e:
            logger.warning(f"Could not get balance: {e}")
        return 10000  # Fallback


class SignalPipeline:
    """
    Main pipeline orchestrating all three phases.

    Usage:
        pipeline = SignalPipeline(phase1, strategies, phase3, sizer)
        results = pipeline.process(symbol, market_data)
    """

    def __init__(
        self,
        phase1: Phase1RegimePermission,
        strategies: Dict[StrategyType, Any],
        phase3: Phase3ExecutionFilter,
        position_sizer,
        confidence_sizer=None,
    ):
        self.phase1 = phase1
        self.strategies = strategies  # {StrategyType: strategy_instance}
        self.phase3 = phase3
        self.position_sizer = position_sizer
        self.confidence_sizer = confidence_sizer

    def process(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        current_price: float,
        account_balance: float = None,
    ) -> List[Tuple[Signal, float, PhaseDecision]]:
        """
        Process symbol through all phases.

        Args:
            symbol: Trading symbol
            market_data: Multi-timeframe market data
            current_price: Current market price
            account_balance: Account balance for sizing

        Returns:
            List of (Signal, size, final_decision) tuples for signals that passed
        """
        results = []
        account_balance = account_balance or 10000

        for strategy_type, strategy in self.strategies.items():
            # Phase 1: Permission check
            p1_decision = self.phase1.check(
                symbol,
                strategy_type,
                market_data.get("4h", market_data),
                timeframe="4h",
            )

            if p1_decision.result == PhaseResult.BLOCK:
                logger.debug(
                    f"Phase 1 BLOCK: {symbol} {strategy_type.value} - {p1_decision.reason}"
                )
                continue

            # Phase 2: Strategy generates signal
            try:
                signals = strategy.generate_signals(symbol, market_data, current_price)
            except Exception as e:
                logger.error(f"Phase 2 error: {symbol} {strategy_type.value} - {e}")
                continue

            if not signals:
                continue

            for signal in signals:
                # Calculate position size
                base_size = self._calculate_base_size(signal, account_balance)

                # Apply confidence multiplier (confidence affects size, not permission)
                if self.confidence_sizer:
                    sizing_result = self.confidence_sizer.apply_to_size(
                        base_size, signal
                    )
                    adjusted_size = sizing_result.effective_size
                else:
                    # Fallback: simple confidence multiplier with 0.3 floor
                    confidence_multiplier = max(0.3, signal.confidence)
                    adjusted_size = base_size * confidence_multiplier

                # Phase 3: Execution safety
                p3_decision = self.phase3.check(signal, adjusted_size)

                if p3_decision.result == PhaseResult.BLOCK:
                    logger.debug(
                        f"Phase 3 BLOCK: {symbol} {signal.side} - {p3_decision.reason}"
                    )
                    continue

                # Signal passed all phases
                logger.info(
                    f"Signal APPROVED: {symbol} {signal.side.value} "
                    f"confidence={signal.confidence:.2f} size={adjusted_size:.6f}"
                )
                results.append((signal, adjusted_size, p3_decision))

        return results

    def _calculate_base_size(self, signal: Signal, account_balance: float) -> float:
        """Calculate base position size from Kelly or fixed percentage."""
        try:
            if self.position_sizer and hasattr(
                self.position_sizer, "calculate_position_size"
            ):
                result = self.position_sizer.calculate_position_size(
                    strategy_type=signal.strategy,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    account_balance=account_balance,
                )
                if isinstance(result, dict):
                    return result.get("position_size", 0)
                return result
        except Exception as e:
            logger.warning(f"Position sizer error: {e}")

        # Fallback: 2% of account / entry price
        return (
            (account_balance * 0.02) / signal.entry_price
            if signal.entry_price > 0
            else 0
        )
