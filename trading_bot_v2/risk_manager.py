"""
Centralized Risk Management System

Single source of truth for all risk calculations and validations.
Eliminates duplicate risk logic across trading_bot.py, position sizing, and validation.
"""

import os
import time
from enum import Enum
from typing import Dict, Any, Optional, List, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from .pacifica_client import PacificaClient


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to default on error."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid float for {name}={raw!r}, using default {default}")
        return default


# Per-regime position size multipliers (env-overridable defaults).
# Keys match MarketRegime enum values (lowercase).
REGIME_SIZE_MULT_DEFAULTS: Dict[str, float] = {
    "trending_strong": 1.0,
    "trending_moderate": 0.85,
    "ranging_volatile": 0.6,
    "ranging_calm": 0.85,
    "indecisive": 0.4,
}

# Type hints for grid exposure tracking
GridExposure = Dict[str, float]

# Margin data cache structure
MarginData = Dict[str, Any]


class RiskProfile(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskManager:
    """
    Centralized risk management for all trading strategies.

    Single source of truth for:
    - Position sizing
    - Risk validation
    - Exposure limits
    - Strategy risk profiles

    RISK MANAGEMENT WARNING
    ===============================
    This is the ONLY place for risk calculations.
    All other classes must delegate here.
    ===============================
    """

    def __init__(
        self,
        max_portfolio_risk_pct: float = 0.05,  # Was 0.02 - 5% max per trade (increased for more trades)
        max_portfolio_exposure_pct: float = 0.15,  # Was 0.10 - 15% max total exposure
        risk_profiles: Optional[Dict[str, float]] = None,
        client: Optional["PacificaClient"] = None,
        max_margin_utilization_pct: float = 0.75,  # 75% max margin utilization
        maintenance_margin_buffer_pct: float = 0.15,  # 15% buffer above maintenance margin
    ):
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        self.max_portfolio_exposure_pct = max_portfolio_exposure_pct
        self.client = client  # Pacifica client for margin data retrieval

        # Margin safety parameters
        self.max_margin_utilization_pct = max_margin_utilization_pct
        self.maintenance_margin_buffer_pct = maintenance_margin_buffer_pct

        # Grid exposure tracking per Grid Trading Brief
        self.grid_exposure: GridExposure = {}  # Track grid exposure per symbol

        # Migrated position tracking (positions that moved from grid to trend-following)
        # Format: {symbol: [{side, qty, entry_price, migrated_at, original_grid_id, has_stop}]}
        self.migrated_positions: Dict[str, List[Dict[str, Any]]] = {}

        # AUTHORITATIVE APPROVAL SYSTEM - Phase 1 Enhancement
        self._approval_required = True  # All capital requests must be approved
        # Track pending approval requests
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}

        # Default risk multipliers by profile
        self.risk_profiles = risk_profiles or {
            RiskProfile.LOW.value: 0.5,  # 50% of base risk
            RiskProfile.MEDIUM.value: 1.0,  # 100% of base risk
            RiskProfile.HIGH.value: 1.5,  # 150% of base risk
        }

        # Margin data cache for reducing API calls
        self._margin_data_cache: Optional[MarginData] = None
        self._margin_cache_timestamp: float = 0.0
        self._margin_cache_ttl: int = 30  # 30 seconds cache TTL

        # Regime-conditional size multipliers (env REGIME_SIZE_MULT_<REGIME>)
        self.regime_size_multipliers: Dict[str, float] = {
            regime: _env_float(f"REGIME_SIZE_MULT_{regime.upper()}", default)
            for regime, default in REGIME_SIZE_MULT_DEFAULTS.items()
        }

        logger.info(
            f"RiskManager initialized (AUTHORITATIVE MODE): max_risk={max_portfolio_risk_pct * 100}%, "
            f"max_exposure={max_portfolio_exposure_pct * 100}%, "
            f"max_margin_util={max_margin_utilization_pct * 100}%, "
            f"mm_buffer={maintenance_margin_buffer_pct * 100}%"
        )

    def get_regime_size_multiplier(self, regime: Any = None) -> float:
        """
        Return the position size multiplier for a market regime.

        Args:
            regime: MarketRegime enum, regime value string (e.g.
                "trending_strong"), or None.

        Returns:
            Configured multiplier for the regime; 1.0 for None or unknown
            regimes (fully backward compatible).
        """
        if regime is None:
            return 1.0
        regime_key = str(getattr(regime, "value", regime)).lower()
        multiplier = self.regime_size_multipliers.get(regime_key)
        if multiplier is None:
            logger.debug(
                f"No size multiplier configured for regime '{regime_key}', using 1.0"
            )
            return 1.0
        return multiplier

    def get_position_size(
        self,
        signal: Any,
        account_balance: float,
        current_exposure: float,
        regime: Any = None,
    ) -> float:
        """
        Calculate position quantity based on signal and risk profile.

        Regime-conditional sizing: when a regime is supplied, the final
        quantity is scaled by the per-regime multiplier (env
        REGIME_SIZE_MULT_*; e.g. INDECISIVE defaults to 0.4x). The
        multiplier is applied AFTER all other sizing math (risk profile,
        stop distance, exposure caps) so RiskManager remains the single
        sizing authority - any upstream Kelly/confidence sizing feeds into
        the same final scaling. Overlay and time-gated strategies receive
        the same regime multiplier as regime-mapped strategies. Passing
        regime=None keeps legacy behavior (multiplier 1.0).

        Args:
            signal: Signal object with risk_profile and entry_price attributes
            account_balance: Current account balance
            current_exposure: Current portfolio exposure
            regime: Optional current confirmed MarketRegime (enum or value
                string) used for regime-conditional size scaling.

        Returns:
            Position quantity (number of contracts/shares)
        """

        # Validate entry price
        if not hasattr(signal, "entry_price") or signal.entry_price <= 0:
            logger.error(
                f"Invalid entry price for {signal.asset}: {getattr(signal, 'entry_price', 'missing')}"
            )
            return 1.0  # Minimum safe quantity

        # Get base risk amount
        base_risk_amount = account_balance * self.max_portfolio_risk_pct

        # Apply risk profile multiplier
        if not hasattr(signal, "risk_profile") or not signal.risk_profile:
            # Set risk profile based on strategy type with normalization
            strategy_name = getattr(signal, "strategy", None)
            if strategy_name:
                if hasattr(strategy_name, "value"):
                    strategy_name = strategy_name.value
                signal.risk_profile = self.get_strategy_risk_profile(str(strategy_name))
            else:
                signal.risk_profile = RiskProfile.MEDIUM.value

        risk_profile = signal.risk_profile
        risk_multiplier = self.risk_profiles.get(risk_profile, 1.0)
        adjusted_risk = base_risk_amount * risk_multiplier

        # Calculate position size based on stop loss distance
        if not signal.stop_loss:
            logger.warning(f"No stop loss for {signal.asset}, using minimum size")
            notional_size = adjusted_risk * 0.1  # Conservative minimum
        elif signal.entry_price <= 0:
            logger.warning(
                f"Invalid entry price ({signal.entry_price}) for {signal.asset}, using minimum size"
            )
            notional_size = adjusted_risk * 0.1  # Conservative minimum
        else:
            # Position notional = Risk Amount / Stop Distance Percentage
            stop_distance_pct = (
                abs(signal.entry_price - signal.stop_loss) / signal.entry_price
            )
            # Avoid division by zero if stop == entry
            if stop_distance_pct <= 0:
                logger.warning(
                    f"Zero stop distance for {signal.asset}, using minimum size"
                )
                notional_size = adjusted_risk * 0.1
            else:
                notional_size = adjusted_risk / stop_distance_pct

        # Apply exposure limits
        max_exposure = account_balance * self.max_portfolio_exposure_pct
        available_exposure = max_exposure - current_exposure
        notional_size = min(notional_size, available_exposure)

        # Convert notional to quantity using entry price
        quantity: float
        if signal.entry_price > 0:
            quantity = notional_size / signal.entry_price
        else:
            quantity = 1.0  # Fallback minimum

        # Regime-conditional scaling (applied after all other sizing math,
        # before the hard minimum quantity floor)
        regime_multiplier = self.get_regime_size_multiplier(regime)
        quantity *= regime_multiplier

        # Ensure minimum quantity
        quantity = max(quantity, 1.0)

        logger.debug(
            f"Position sizing: notional=${notional_size:.2f}, quantity={quantity:.4f} "
            f"@ ${signal.entry_price:.2f} (regime_mult={regime_multiplier:.2f})"
        )

        return quantity

    def validate_position_size(
        self,
        quantity: float,
        account_balance: float,
        current_exposure: float,
        entry_price: float = 0,
    ) -> bool:
        """
        Validate position quantity against all risk limits.

        Args:
            quantity: Position quantity to validate
            account_balance: Current account balance
            current_exposure: Current portfolio exposure
            entry_price: Entry price for notional calculation (if 0, assumes quantity is already notional)
        """

        # Convert quantity to notional if entry_price provided
        if entry_price > 0:
            position_notional = quantity * entry_price
        else:
            position_notional = quantity  # Assume already notional

        # Check risk percentage
        risk_pct = (position_notional / account_balance) if account_balance > 0 else 1.0
        if risk_pct > self.max_portfolio_risk_pct * 2:  # Allow some flexibility
            logger.warning(
                f"Position size exceeds risk limit: {risk_pct * 100:.1f}% > {self.max_portfolio_risk_pct * 100:.1f}%"
            )
            return False

        # Check exposure limit
        total_exposure = current_exposure + position_notional
        exposure_pct = (
            (total_exposure / account_balance) if account_balance > 0 else 1.0
        )
        if exposure_pct > self.max_portfolio_exposure_pct:
            logger.warning(
                f"Total exposure exceeds limit: {exposure_pct * 100:.1f}% > {self.max_portfolio_exposure_pct * 100:.1f}%"
            )
            return False

        return True

    def get_grid_capital(
        self, account_balance: float, current_exposure: float, symbol: str
    ) -> float:
        """Get capital allocation for grid trading per the Grid Trading Brief."""
        # Store last known balance for strategy access
        self.last_known_balance = account_balance

        # Grid gets 15% of account balance (increased from 10% for more trades - Jan 2026)
        grid_capital_pct = 0.15
        base_grid_capital = account_balance * grid_capital_pct

        # Ensure grid exposure doesn't exceed portfolio limits (35% of max exposure)
        max_grid_exposure = account_balance * self.max_portfolio_exposure_pct * 0.35
        available_for_grid = max_grid_exposure - self._get_total_grid_exposure()

        # Cap at available exposure
        grid_capital = min(base_grid_capital, available_for_grid)

        # Ensure positive and reasonable minimum (1% of account)
        grid_capital = max(grid_capital, account_balance * 0.01)

        logger.debug(
            f"Grid capital for {symbol}: ${grid_capital:.2f} ({grid_capital_pct * 100:.1f}% of account)"
        )
        return grid_capital

    def validate_grid_exposure(
        self,
        symbol: str,
        proposed_grid_capital: float,
        account_balance: float,
        current_exposure: float,
    ) -> bool:
        """Validate that proposed grid won't exceed exposure limits per Grid Trading Brief."""
        # Check symbol-specific grid limit (50% of allowed grid exposure)
        max_symbol_grid = (
            self.get_grid_capital(account_balance, current_exposure, symbol) * 0.5
        )

        if proposed_grid_capital > max_symbol_grid:
            logger.warning(
                f"Grid capital ${proposed_grid_capital:.2f} exceeds symbol limit ${max_symbol_grid:.2f}"
            )
            return False

        # Check total grid exposure (35% of portfolio max)
        total_grid_after = self._get_total_grid_exposure() + proposed_grid_capital
        max_total_grid = account_balance * self.max_portfolio_exposure_pct * 0.35

        if total_grid_after > max_total_grid:
            logger.warning(
                f"Total grid exposure ${total_grid_after:.2f} exceeds limit ${max_total_grid:.2f}"
            )
            return False

        return True

    def on_grid_emergency_exit(self, symbol: str) -> None:
        """Handle emergency grid exit - reset exposure tracking per Grid Trading Brief."""
        if hasattr(self, "grid_exposure") and symbol in self.grid_exposure:
            del self.grid_exposure[symbol]
            logger.info(f"Grid exposure reset for {symbol} after emergency exit")

    def normalize_grid_params(
        self, grid_levels: int, quantity: float, spacing: float
    ) -> Dict[str, float]:
        """
        Normalize and validate grid parameters per Grid Trading Brief.

        Treats strategy-provided values as hints, enforces safety limits.

        Args:
            grid_levels: Number of grid levels (strategy hint)
            quantity: Base quantity per level (strategy hint)
            spacing: Price spacing between levels (strategy hint)

        Returns:
            Dict with normalized parameters: grid_levels, quantity, spacing
        """
        # Enforce grid level limits (2-10 levels for safety)
        normalized_levels = max(2, min(10, grid_levels))

        # Enforce quantity limits (minimum 0.001, maximum 10.0)
        normalized_quantity = max(0.001, min(10.0, quantity))

        # Enforce spacing limits (0.1% to 5.0% ATR)
        normalized_spacing = max(0.001, min(0.05, spacing))

        logger.debug(
            f"Grid params normalized: levels {grid_levels}->{normalized_levels}, "
            f"quantity {quantity}->{normalized_quantity}, "
            f"spacing {spacing}->{normalized_spacing}"
        )

        return {
            "grid_levels": normalized_levels,
            "quantity": normalized_quantity,
            "spacing": normalized_spacing,
        }

    def _get_total_grid_exposure(self) -> float:
        """Get total current grid exposure across all symbols."""
        if hasattr(self, "grid_exposure"):
            return sum(self.grid_exposure.values())
        return 0.0

    def get_strategy_risk_profile(self, strategy_type: str) -> str:
        """
        Get default risk profile for strategy type.

        Handles case-insensitive matching and multiple naming formats.
        """
        # Normalize to lowercase for consistent lookup
        normalized_type = strategy_type.lower()

        # Log normalization if different
        if normalized_type != strategy_type:
            logger.debug(
                f"Normalized strategy type: '{strategy_type}' -> '{normalized_type}'"
            )

        # Strategy-specific risk profiles (all lowercase keys)
        # Handle multiple naming formats (with/without underscores)
        # Grid trading upgraded to MEDIUM (Jan 2026) for more aggressive sizing
        strategy_profiles = {
            "mean_reversion": RiskProfile.MEDIUM.value,
            "meanreversion": RiskProfile.MEDIUM.value,  # Handle no underscore
            "ma_crossover": RiskProfile.MEDIUM.value,
            "macrossover": RiskProfile.MEDIUM.value,  # Handle no underscore
            "ma crossover": RiskProfile.MEDIUM.value,  # Handle space
            "grid_trading": RiskProfile.MEDIUM.value,  # Was LOW - upgraded for more trades
            "gridtrading": RiskProfile.MEDIUM.value,  # Handle no underscore
            "grid trading": RiskProfile.MEDIUM.value,  # Handle space
            "liquidation_capture": RiskProfile.HIGH.value,
            "liquidationcapture": RiskProfile.HIGH.value,  # Handle no underscore
            "liquidation capture": RiskProfile.HIGH.value,  # Handle space
            "trend_following": RiskProfile.HIGH.value,
            "trendfollowing": RiskProfile.HIGH.value,  # Handle no underscore
            "trend following": RiskProfile.HIGH.value,  # Handle space
            # Advanced Strategies
            "vwap_scalping": RiskProfile.MEDIUM.value,
            "vwapscalping": RiskProfile.MEDIUM.value,
            "vwap scalping": RiskProfile.MEDIUM.value,
            "funding_arbitrage": RiskProfile.LOW.value,
            "fundingarbitrage": RiskProfile.LOW.value,
            "funding arbitrage": RiskProfile.LOW.value,
            "momentum_scalping": RiskProfile.HIGH.value,
            "momentum scalping": RiskProfile.HIGH.value,
            "momentumscalping": RiskProfile.HIGH.value,
            "order_book_imbalance": RiskProfile.MEDIUM.value,
            "orderbookimbalance": RiskProfile.MEDIUM.value,
            "order book imbalance": RiskProfile.MEDIUM.value,
        }

        profile = strategy_profiles.get(normalized_type, RiskProfile.MEDIUM.value)

        if normalized_type not in strategy_profiles:
            logger.warning(
                f"Unknown strategy type '{strategy_type}' (normalized: '{normalized_type}'), using MEDIUM risk profile"
            )

        return profile

    # =========================
    # AUTHORITATIVE APPROVAL SYSTEM - Phase 1 Enhancement
    # =========================

    def request_capital_allocation(
        self,
        symbol: str,
        requested_amount: float,
        strategy: str,
        account_balance: float,
        current_exposure: float,
    ) -> Dict[str, Any]:
        """
        Request capital allocation approval - AUTHORITATIVE GATEKEEPER.

        CRITICAL SAFETY: This is the ONLY way to allocate capital.
        All trading components must go through this approval process.

        Args:
            symbol: Trading symbol
            requested_amount: Amount of capital requested
            strategy: Strategy requesting capital
            account_balance: Current account balance
            current_exposure: Current portfolio exposure

        Returns:
            Dict with 'approved': bool, 'allocated_amount': float, 'reason': str
        """
        if not self._approval_required:
            # Legacy mode for backward compatibility
            return {
                "approved": True,
                "allocated_amount": requested_amount,
                "reason": "approval_not_required",
            }

        # Validate request parameters
        if requested_amount <= 0:
            return {
                "approved": False,
                "allocated_amount": 0.0,
                "reason": "invalid_request_amount",
            }

        if account_balance <= 0:
            return {
                "approved": False,
                "allocated_amount": 0.0,
                "reason": "insufficient_account_balance",
            }

        # Check exposure limits
        max_additional_exposure = (
            account_balance * self.max_portfolio_exposure_pct
        ) - current_exposure
        if max_additional_exposure <= 0:
            return {
                "approved": False,
                "allocated_amount": 0.0,
                "reason": "exposure_limit_exceeded",
            }

        # Apply risk-based allocation limits
        allocated_amount = min(requested_amount, max_additional_exposure)

        # Additional strategy-specific limits
        if strategy.lower() == "grid_trading":
            # Grid gets maximum 4% of account balance
            max_grid_allocation = account_balance * 0.04
            allocated_amount = min(allocated_amount, max_grid_allocation)

        # Create approval record
        approval_id = f"{symbol}_{strategy}_{int(time.time())}"
        self._pending_approvals[approval_id] = {
            "symbol": symbol,
            "strategy": strategy,
            "requested_amount": requested_amount,
            "allocated_amount": allocated_amount,
            "account_balance": account_balance,
            "current_exposure": current_exposure,
            "timestamp": time.time(),
            "status": "approved",
        }

        logger.info(
            f"Capital allocation APPROVED: {symbol} {strategy} ${allocated_amount:.2f} "
            f"(requested: ${requested_amount:.2f})"
        )

        return {
            "approved": True,
            "allocated_amount": allocated_amount,
            "approval_id": approval_id,
            "reason": "approved",
        }

    def validate_capital_usage(self, approval_id: str, actual_usage: float) -> bool:
        """
        Validate that capital usage matches approved allocation.

        Args:
            approval_id: ID from request_capital_allocation
            actual_usage: Actual amount being used

        Returns:
            True if usage is valid, False otherwise
        """
        if not self._approval_required:
            return True

        approval = self._pending_approvals.get(approval_id)
        if not approval:
            logger.error(f"Invalid approval ID: {approval_id}")
            return False

        if actual_usage > approval["allocated_amount"] * 1.01:  # 1% tolerance
            logger.error(
                f"Capital usage exceeds allocation: ${actual_usage:.2f} > ${approval['allocated_amount']:.2f}"
            )
            return False

        # Mark as used
        approval["status"] = "used"
        approval["actual_usage"] = actual_usage

        return True

    def get_allocation_status(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a capital allocation request.

        Args:
            approval_id: ID from request_capital_allocation

        Returns:
            Approval status dict or None if not found
        """
        return self._pending_approvals.get(approval_id)

    def cleanup_expired_approvals(self, max_age_seconds: int = 3600) -> None:
        """
        Clean up expired approval requests.

        Args:
            max_age_seconds: Maximum age for approvals (default 1 hour)
        """
        current_time = time.time()
        expired = []

        for approval_id, approval in self._pending_approvals.items():
            if current_time - approval["timestamp"] > max_age_seconds:
                expired.append(approval_id)

        for approval_id in expired:
            del self._pending_approvals[approval_id]

        if expired:
            logger.info(f"Cleaned up {len(expired)} expired capital approvals")

    def get_exposure_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive exposure summary across all strategies.

        Returns:
            Dict with exposure breakdown
        """
        total_grid_exposure = sum(self.grid_exposure.values())

        return {
            "total_grid_exposure": total_grid_exposure,
            "grid_exposure_by_symbol": dict(self.grid_exposure),
            "pending_approvals_count": len(self._pending_approvals),
            "approval_mode": "authoritative" if self._approval_required else "advisory",
        }

    # =========================
    # MIGRATED POSITION TRACKING
    # =========================

    def register_migrated_position(self, symbol: str, position: Dict[str, Any]) -> bool:
        """
        Register a position migrated from grid to trend-following.

        SAFETY: Migrated positions MUST have stops to be registered.

        Args:
            symbol: Trading symbol
            position: Dict with required keys:
                - side: 'long' or 'short'
                - qty: Position quantity
                - entry_price: Entry price (for exposure calculation)
                - original_grid_id: Optional grid ID for audit trail
                - has_stop: Boolean indicating if stop loss is set

        Returns:
            True if registered successfully, False otherwise
        """
        import time

        # Validate required fields
        required_fields = ["side", "qty", "entry_price"]
        for field in required_fields:
            if field not in position:
                logger.error(f"Migrated position missing required field: {field}")
                return False

        # SAFETY: Warn if no stop (but still register - stop can be added later)
        if not position.get("has_stop", False):
            logger.warning(
                f"⚠️ Migrated position for {symbol} has NO STOP LOSS! "
                f"Add stop immediately via MigratedPositionManager"
            )

        # Initialize symbol list if needed
        if symbol not in self.migrated_positions:
            self.migrated_positions[symbol] = []

        # Create position record
        migrated_record = {
            "side": position["side"],
            "qty": float(position["qty"]),
            "entry_price": float(position["entry_price"]),
            "migrated_at": time.time(),
            "original_grid_id": position.get("original_grid_id"),
            "has_stop": position.get("has_stop", False),
            "stop_price": position.get("stop_price"),
            "trend_direction": position.get("trend_direction"),
        }

        self.migrated_positions[symbol].append(migrated_record)

        # Calculate notional for logging
        notional = migrated_record["qty"] * migrated_record["entry_price"]

        logger.info(
            f"📊 Migrated position registered: {symbol} {position['side']} "
            f"qty={position['qty']:.6f} @ ${position['entry_price']:.2f} "
            f"(notional=${notional:.2f}, has_stop={migrated_record['has_stop']})"
        )

        return True

    def unregister_migrated_position(
        self, symbol: str, side: str, qty: Optional[float] = None
    ) -> bool:
        """
        Unregister a migrated position (when closed via TP/SL/manual).

        Args:
            symbol: Trading symbol
            side: Position side ('long' or 'short')
            qty: Optional quantity (if None, removes all matching positions)

        Returns:
            True if position was found and removed
        """
        if symbol not in self.migrated_positions:
            logger.warning(f"No migrated positions found for {symbol}")
            return False

        positions = self.migrated_positions[symbol]
        removed = False

        # Find and remove matching position(s)
        for i in range(len(positions) - 1, -1, -1):
            pos = positions[i]
            if pos["side"] == side:
                if qty is None or abs(pos["qty"] - qty) < 0.0001:
                    removed_pos = positions.pop(i)
                    logger.info(
                        f"📊 Migrated position unregistered: {symbol} {side} "
                        f"qty={removed_pos['qty']:.6f}"
                    )
                    removed = True
                    if qty is not None:
                        break  # Only remove one if qty specified

        # Clean up empty symbol entries
        if not self.migrated_positions[symbol]:
            del self.migrated_positions[symbol]

        return removed

    def get_migrated_exposure(self, symbol: Optional[str] = None) -> float:
        """
        Get total exposure from migrated positions.

        Args:
            symbol: Specific symbol (None for all symbols)

        Returns:
            Total notional exposure from migrated positions
        """
        total = 0.0

        if symbol:
            positions = self.migrated_positions.get(symbol, [])
            for pos in positions:
                total += pos["qty"] * pos["entry_price"]
        else:
            for sym_positions in self.migrated_positions.values():
                for pos in sym_positions:
                    total += pos["qty"] * pos["entry_price"]

        return total

    def get_total_exposure(self, symbol: Optional[str] = None) -> Dict[str, float]:
        """
        Get combined exposure for symbol (grid + migrated + other).

        Args:
            symbol: Specific symbol (None for all symbols)

        Returns:
            Dict with 'grid_exposure', 'migrated_exposure', 'total_exposure'
        """
        if symbol:
            grid_exp = self.grid_exposure.get(symbol, 0.0)
            migrated_exp = self.get_migrated_exposure(symbol)
        else:
            grid_exp = self._get_total_grid_exposure()
            migrated_exp = self.get_migrated_exposure()

        return {
            "grid_exposure": grid_exp,
            "migrated_exposure": migrated_exp,
            "total_exposure": grid_exp + migrated_exp,
        }

    def validate_migrated_position(
        self, symbol: str, position: Dict[str, Any], account_balance: float
    ) -> Dict[str, Any]:
        """
        Validate migrated position has proper risk controls.

        Migrated positions follow trend-following rules (higher limits than grid):
        - Max 10% of account per position (vs 5% for grid)
        - MUST have stop loss

        Args:
            symbol: Trading symbol
            position: Position dict with side, qty, entry_price, has_stop
            account_balance: Current account balance

        Returns:
            Dict with 'valid': bool, 'warnings': list, 'errors': list
        """
        result: Dict[str, Any] = {"valid": True, "warnings": [], "errors": []}

        # Check stop loss requirement (CRITICAL)
        if not position.get("has_stop", False):
            result["errors"].append("Migrated position MUST have stop loss")
            result["valid"] = False

        # Calculate position notional
        notional = position.get("qty", 0) * position.get("entry_price", 0)

        # Check position size (trend-following allows up to 10%)
        max_trend_position = account_balance * 0.10
        if notional > max_trend_position:
            result["warnings"].append(
                f"Position size ${notional:.2f} exceeds trend-following limit ${max_trend_position:.2f}"
            )

        # Check total migrated exposure (max 20% of account)
        current_migrated = self.get_migrated_exposure()
        max_migrated_total = account_balance * 0.20
        if current_migrated + notional > max_migrated_total:
            result["errors"].append(
                f"Total migrated exposure would exceed limit: "
                f"${current_migrated + notional:.2f} > ${max_migrated_total:.2f}"
            )
            result["valid"] = False

        return result

    def get_migrated_positions(
        self, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get list of migrated positions.

        Args:
            symbol: Specific symbol (None for all)

        Returns:
            List of migrated position records
        """
        if symbol:
            return list(self.migrated_positions.get(symbol, []))
        else:
            all_positions = []
            for sym, positions in self.migrated_positions.items():
                for pos in positions:
                    all_positions.append({**pos, "symbol": sym})
            return all_positions

    def has_migrated_positions(self, symbol: Optional[str] = None) -> bool:
        """Check if there are any migrated positions."""
        if symbol:
            return bool(self.migrated_positions.get(symbol))
        return bool(self.migrated_positions)

    def update_migrated_stop(self, symbol: str, side: str, new_stop: float) -> bool:
        """
        Update stop price for a migrated position.

        Args:
            symbol: Trading symbol
            side: Position side
            new_stop: New stop price

        Returns:
            True if updated successfully
        """
        if symbol not in self.migrated_positions:
            return False

        for pos in self.migrated_positions[symbol]:
            if pos["side"] == side:
                pos["stop_price"] = new_stop
                pos["has_stop"] = True
                logger.debug(
                    f"Updated migrated stop for {symbol} {side}: ${new_stop:.2f}"
                )
                return True

        return False

    # =========================
    # MARGIN SAFETY SYSTEM
    # =========================

    def get_margin_data(self, force_refresh: bool = False) -> Optional[MarginData]:
        """
        Fetch and cache margin data from Pacifica API.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Dict with margin data or None if client not available
        """
        if not self.client:
            logger.debug("No Pacifica client available for margin data retrieval")
            return None

        current_time = time.time()
        if (
            not force_refresh
            and self._margin_data_cache is not None
            and (current_time - self._margin_cache_timestamp) < self._margin_cache_ttl
        ):
            return self._margin_data_cache

        try:
            balance_response = self.client.get_balance()

            if not balance_response:
                logger.warning("Empty balance response from Pacifica API")
                return None

            raw_data = balance_response if isinstance(balance_response, dict) else {}

            margin_data: MarginData = {
                "account_equity": self._parse_float_safe(
                    raw_data.get("account_equity", "0")
                ),
                "total_margin_used": self._parse_float_safe(
                    raw_data.get("total_margin_used", "0")
                ),
                "cross_mmr": self._parse_float_safe(raw_data.get("cross_mmr", "0")),
                "available_to_spend": self._parse_float_safe(
                    raw_data.get(
                        "available_to_spend", raw_data.get("available_balance", "0")
                    )
                ),
                "balance": self._parse_float_safe(raw_data.get("balance", "0")),
                "timestamp": current_time,
            }

            if margin_data["account_equity"] > 0:
                margin_data["margin_utilization_pct"] = (
                    margin_data["total_margin_used"] / margin_data["account_equity"]
                )
            else:
                margin_data["margin_utilization_pct"] = 0.0

            self._margin_data_cache = margin_data
            self._margin_cache_timestamp = current_time

            logger.debug(
                f"Margin data refreshed: equity=${margin_data['account_equity']:.2f}, "
                f"margin_used=${margin_data['total_margin_used']:.2f}, "
                f"utilization={margin_data['margin_utilization_pct'] * 100:.1f}%"
            )

            return margin_data

        except Exception as e:
            logger.error(f"Error fetching margin data: {e}")
            if self._margin_data_cache is not None:
                logger.warning("Returning stale margin data due to fetch error")
                return self._margin_data_cache
            return None

    def _parse_float_safe(self, value: Any, default: float = 0.0) -> float:
        """Safely parse a value to float, handling strings and None."""
        if value is None:
            return default
        try:
            if isinstance(value, str):
                value = value.replace(",", "").replace("$", "").strip()
            return float(value)
        except (ValueError, TypeError):
            return default

    def check_margin_safety(self, proposed_margin: float = 0.0) -> Dict[str, Any]:
        """
        Check if margin levels are safe for opening new positions.

        Args:
            proposed_margin: Estimated margin required for proposed position

        Returns:
            Dict with 'safe', 'utilization_after', 'available_margin', 'reason', 'warnings'
        """
        result: Dict[str, Any] = {
            "safe": True,
            "utilization_after": 0.0,
            "available_margin": 0.0,
            "reason": "",
            "warnings": [],
        }

        margin_data = self.get_margin_data()

        if not margin_data:
            result["warnings"].append(
                "Unable to retrieve margin data - proceeding with caution"
            )
            logger.warning("Margin safety check skipped - no margin data available")
            return result

        account_equity = margin_data.get("account_equity", 0.0)
        total_margin_used = margin_data.get("total_margin_used", 0.0)
        cross_mmr = margin_data.get("cross_mmr", 0.0)

        # Check 1: Current utilization
        if account_equity > 0:
            current_utilization = total_margin_used / account_equity
            result["utilization_after"] = (
                total_margin_used + proposed_margin
            ) / account_equity
        else:
            current_utilization = 0.0
            result["utilization_after"] = 0.0

        if current_utilization >= self.max_margin_utilization_pct:
            result["safe"] = False
            result["reason"] = (
                f"Current margin utilization ({current_utilization * 100:.1f}%) "
                f"exceeds maximum ({self.max_margin_utilization_pct * 100:.1f}%)"
            )
            logger.warning(result["reason"])
            return result

        # Check 2: Proposed utilization
        if result["utilization_after"] > self.max_margin_utilization_pct:
            result["safe"] = False
            result["reason"] = (
                f"Position would exceed max margin utilization: "
                f"{result['utilization_after'] * 100:.1f}% > {self.max_margin_utilization_pct * 100:.1f}%"
            )
            logger.warning(result["reason"])
            return result

        # Check 3: Maintenance margin buffer
        if cross_mmr > 0 and account_equity > 0:
            required_equity = cross_mmr * (1.0 + self.maintenance_margin_buffer_pct)
            if account_equity < required_equity:
                result["safe"] = False
                result["reason"] = (
                    f"Account equity (${account_equity:.2f}) below safe level "
                    f"(${required_equity:.2f}) for maintenance margin"
                )
                logger.warning(result["reason"])
                return result

            safety_buffer = account_equity - cross_mmr
            if safety_buffer < cross_mmr * 0.1:
                result["warnings"].append(
                    f"Low maintenance margin buffer: ${safety_buffer:.2f}"
                )

        # Check 4: Available margin
        result["available_margin"] = self._calculate_available_margin(margin_data)

        if proposed_margin > 0 and result["available_margin"] < proposed_margin:
            result["safe"] = False
            result["reason"] = (
                f"Insufficient available margin: ${result['available_margin']:.2f} < "
                f"${proposed_margin:.2f} required"
            )
            logger.warning(result["reason"])
            return result

        return result

    def _calculate_available_margin(self, margin_data: MarginData) -> float:
        """Calculate available margin for new positions."""
        account_equity: float = margin_data.get("account_equity", 0.0)
        total_margin_used: float = margin_data.get("total_margin_used", 0.0)
        available_to_spend: float = margin_data.get("available_to_spend", 0.0)

        max_margin_allowed = account_equity * self.max_margin_utilization_pct
        equity_based_available = max(0.0, max_margin_allowed - total_margin_used)

        return min(available_to_spend, equity_based_available)

    def get_margin_summary(self) -> Dict[str, Any]:
        """Get comprehensive margin status summary."""
        margin_data = self.get_margin_data()

        if not margin_data:
            return {"status": "unavailable", "error": "Unable to retrieve margin data"}

        account_equity = margin_data.get("account_equity", 0.0)
        total_margin_used = margin_data.get("total_margin_used", 0.0)
        cross_mmr = margin_data.get("cross_mmr", 0.0)
        available_to_spend = margin_data.get("available_to_spend", 0.0)

        utilization_pct = (
            (total_margin_used / account_equity * 100) if account_equity > 0 else 0.0
        )
        available_margin = self._calculate_available_margin(margin_data)

        if utilization_pct >= self.max_margin_utilization_pct * 100:
            status = "critical"
        elif utilization_pct >= self.max_margin_utilization_pct * 100 * 0.8:
            status = "warning"
        else:
            status = "healthy"

        mm_buffer = (
            (account_equity - cross_mmr) / cross_mmr * 100 if cross_mmr > 0 else 100.0
        )

        return {
            "status": status,
            "account_equity": account_equity,
            "total_margin_used": total_margin_used,
            "cross_mmr": cross_mmr,
            "available_to_spend": available_to_spend,
            "available_margin": available_margin,
            "utilization_pct": utilization_pct,
            "max_utilization_pct": self.max_margin_utilization_pct * 100,
            "maintenance_margin_buffer_pct": mm_buffer,
            "safe_for_new_positions": status != "critical",
        }

    def emergency_stop_all(self) -> None:
        """
        Emergency stop - revoke all allocations and reset exposure tracking.

        CRITICAL SAFETY: Called during emergency situations.
        """
        logger.critical("EMERGENCY STOP: Revoking all capital allocations")

        # Clear all pending approvals
        self._pending_approvals.clear()

        # Reset grid exposure
        self.grid_exposure.clear()

        # Clear migrated positions tracking (positions still exist on exchange!)
        migrated_count = sum(len(pos) for pos in self.migrated_positions.values())
        if migrated_count > 0:
            logger.critical(
                f"⚠️ Clearing {migrated_count} migrated position records - "
                f"POSITIONS STILL EXIST ON EXCHANGE!"
            )
        self.migrated_positions.clear()

        # Clear margin data cache
        if self._margin_data_cache is not None:
            self._margin_data_cache = None
            self._margin_cache_timestamp = 0.0

        logger.critical("All capital allocations revoked and exposure reset")
