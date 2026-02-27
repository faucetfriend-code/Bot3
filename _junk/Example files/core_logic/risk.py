"""
Risk management and validation functions.
All calculations based on Trading Bot Custom Instructions.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import math
from models import Account, MarketData, Signal, AssetClass, StrategyType, RiskValidation
from config import get_config, MarketState
from risk_params import TradeQuality
from loguru import logger

# Global circuit breaker state
circuit_breaker_enabled = True


def set_circuit_breaker_enabled(enabled: bool):
    """Enable or disable circuit breaker checks."""
    global circuit_breaker_enabled
    circuit_breaker_enabled = enabled


class BotError(Exception):
    """Base exception for bot errors."""

    pass


class RiskViolationError(BotError):
    """Raised when risk management rules are violated."""

    pass


class InsufficientFundsError(BotError):
    """Raised when account has insufficient funds."""

    pass


class ValidationError(BotError):
    """Raised when validation fails."""

    pass


def calculate_position_size(
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    trade_quality: TradeQuality,
    leverage: int = 15,
) -> Dict[str, float]:
    """
    Calculate position size based on risk management rules.

    Based on Trading Instructions Section I.A - Position Sizing Framework.

    Args:
        account_balance: Current account balance
        entry_price: Planned entry price
        stop_loss: Stop loss price
        trade_quality: Trade quality tier
        leverage: Leverage to use

    Returns:
        Dict with margin_allocation, position_value, quantity, etc.
    """
    config = get_config()

    # Get margin allocation based on quality
    quality_config = {
        TradeQuality.STANDARD: config.position_sizing.standard_margin_allocation,
        TradeQuality.HIGH_CONVICTION: config.position_sizing.high_conviction_margin,
        TradeQuality.EXCEPTIONAL: config.position_sizing.exceptional_margin,
    }
    margin_allocation = quality_config[trade_quality]

    # Cap at max per trade
    margin_allocation = min(
        margin_allocation, config.position_sizing.max_margin_per_trade
    )

    # Calculate position value
    position_value = margin_allocation * leverage

    # Protect against division by zero in quantity calculation
    if entry_price <= 0:
        raise ValueError("Entry price must be greater than zero")

    # Calculate quantity
    quantity = position_value / entry_price

    # Calculate stop distance
    stop_distance_pct = abs(entry_price - stop_loss) / entry_price

    # Calculate risk metrics
    dollar_risk = position_value * stop_distance_pct
    account_risk_pct = dollar_risk / account_balance
    margin_drawdown_pct = dollar_risk / margin_allocation

    return {
        "margin_allocation": margin_allocation,
        "position_value": position_value,
        "quantity": quantity,
        "dollar_risk": dollar_risk,
        "account_risk_pct": account_risk_pct,
        "margin_drawdown_pct": margin_drawdown_pct,
        "stop_distance_pct": stop_distance_pct,
    }


def calculate_liquidation_price(entry_price: float, leverage: int, side: str) -> float:
    """
    Calculate liquidation price.

    Based on Trading Instructions Section I.C - Liquidation Distance % = (1 ÷ Leverage) × 100

    Args:
        entry_price: Entry price
        leverage: Leverage used
        side: 'buy' or 'sell'

    Returns:
        Liquidation price
    """
    if leverage <= 1:
        return 0

    liquidation_distance = 1.0 / leverage

    if side.lower() == "buy":
        return entry_price * (1 - liquidation_distance)
    else:
        return entry_price * (1 + liquidation_distance)


def calculate_pacifica_liquidation_price(
    entry_price: float,
    leverage: int,
    side: str,
    margin_mode: str = "cross",
    current_margin: Optional[float] = None,
    position_size: Optional[float] = None,
    funding_rate: float = 0.0,
    hours_to_hold: int = 24
) -> Dict[str, float]:
    """
    Calculate Pacifica liquidation price accounting for hourly funding drain.

    ⚠️ CRITICAL for Pacifica Isolated Margin:
    Hourly funding payments reduce margin balance → moves liquidation price closer!
    Must recalculate liquidation price after each hourly funding payment.

    Args:
        entry_price: Position entry price
        leverage: Position leverage (5-50x)
        side: 'long' or 'short'
        margin_mode: 'cross' or 'isolated'
        current_margin: Current margin balance (for isolated)
        position_size: Position size (for funding calculation)
        funding_rate: Expected hourly funding rate
        hours_to_hold: Expected hold time in hours

    Returns:
        Dict with initial_liq_price, liq_price_after_funding, liq_price_per_hour, etc.
    """
    # Base liquidation calculation
    base_liquidation_distance = 1.0 / leverage
    maintenance_margin_rate = 0.005  # 0.5% typical
    fee_rate = 0.0005  # 0.05% typical taker fee

    if side.lower() in ["buy", "long"]:
        # Long liquidation
        initial_liq_price = entry_price * (1 - base_liquidation_distance + maintenance_margin_rate + fee_rate)
        price_direction = -1  # Liquidation below entry
    else:
        # Short liquidation
        initial_liq_price = entry_price * (1 + base_liquidation_distance - maintenance_margin_rate - fee_rate)
        price_direction = 1  # Liquidation above entry

    # For isolated margin, calculate funding impact
    if margin_mode == "isolated" and current_margin and position_size and funding_rate != 0:
        # Calculate cumulative funding drain
        position_value = position_size * entry_price
        hourly_funding_payment = position_value * funding_rate
        total_funding_payments = hourly_funding_payment * hours_to_hold

        # Funding reduces available margin (if positive funding rate for long, or negative for short)
        if (funding_rate > 0 and side.lower() in ["buy", "long"]) or (funding_rate < 0 and side.lower() in ["sell", "short"]):
            # Paying funding - margin decreases
            margin_after_funding = current_margin - abs(total_funding_payments)
        else:
            # Receiving funding - margin increases
            margin_after_funding = current_margin + abs(total_funding_payments)

        # Recalculate liquidation with reduced margin
        # New liquidation distance based on reduced margin
        if margin_after_funding > 0:
            # Liquidation moves proportionally to margin reduction
            margin_reduction_factor = margin_after_funding / current_margin
            # Adjust liquidation price
            liq_distance_adjustment = (1 - margin_reduction_factor) * base_liquidation_distance
            liq_price_after_funding = initial_liq_price + (price_direction * entry_price * liq_distance_adjustment)
        else:
            # Margin depleted - immediate liquidation risk
            liq_price_after_funding = entry_price

        # Calculate liquidation price movement per hour
        liq_price_change = liq_price_after_funding - initial_liq_price
        liq_price_per_hour = liq_price_change / hours_to_hold if hours_to_hold > 0 else 0

        return {
            "initial_liq_price": initial_liq_price,
            "liq_price_after_funding": liq_price_after_funding,
            "liq_price_movement": liq_price_change,
            "liq_price_per_hour": liq_price_per_hour,
            "margin_before": current_margin,
            "margin_after": margin_after_funding,
            "margin_drain": current_margin - margin_after_funding,
            "total_funding_payments": total_funding_payments,
            "hourly_funding_payment": hourly_funding_payment,
            "hours_until_critical": int(current_margin / abs(hourly_funding_payment)) if hourly_funding_payment != 0 else 999,
            "margin_mode": margin_mode
        }
    else:
        # Cross margin or no funding impact
        return {
            "initial_liq_price": initial_liq_price,
            "liq_price_after_funding": initial_liq_price,
            "liq_price_movement": 0.0,
            "liq_price_per_hour": 0.0,
            "margin_mode": margin_mode,
            "note": "Cross margin liquidation based on total account balance"
        }


def validate_stop_loss(
    asset_class: AssetClass, stop_distance_pct: float
) -> Tuple[bool, bool, str]:
    """
    Validate stop loss distance.

    Based on Trading Instructions Section I.B - Stop-Loss Distance Guidelines.

    Args:
        asset_class: Asset class (BTC_ETH, LARGE_CAP, SMALL_CAP)
        stop_distance_pct: Stop distance as percentage

    Returns:
        Tuple of (is_valid, is_optimal, message)
    """
    config = get_config()
    stop_config = config.stop_loss

    # Get limits based on asset class
    limits = {
        AssetClass.BTC_ETH: {
            "min": stop_config.btc_eth["min_stop_pct"],
            "optimal_min": stop_config.btc_eth["optimal_min"],
            "optimal_max": stop_config.btc_eth["optimal_max"],
            "max": stop_config.btc_eth["max_stop_pct"],
        },
        AssetClass.LARGE_CAP: {
            "min": stop_config.large_cap_alts["min_stop_pct"],
            "optimal_min": stop_config.large_cap_alts["optimal_min"],
            "optimal_max": stop_config.large_cap_alts["optimal_max"],
            "max": stop_config.large_cap_alts["max_stop_pct"],
        },
        AssetClass.SMALL_CAP: {
            "min": stop_config.small_cap_meme["min_stop_pct"],
            "optimal_min": stop_config.small_cap_meme["optimal_min"],
            "optimal_max": stop_config.small_cap_meme["optimal_max"],
            "max": stop_config.small_cap_meme["max_stop_pct"],
        },
    }

    asset_limits = limits[asset_class]

    if stop_distance_pct < asset_limits["min"]:
        return (
            False,
            False,
            f"Stop too tight: {stop_distance_pct:.2%} < {asset_limits['min']:.2%}",
        )

    if stop_distance_pct > asset_limits["max"]:
        return (
            False,
            False,
            f"Stop too wide: {stop_distance_pct:.2%} > {asset_limits['max']:.2%}",
        )

    is_optimal = (
        asset_limits["optimal_min"] <= stop_distance_pct <= asset_limits["optimal_max"]
    )

    if not is_optimal:
        msg = f"Stop outside optimal range [{asset_limits['optimal_min']:.2%}, {asset_limits['optimal_max']:.2%}]"
        return True, False, msg

    return True, True, "Stop distance valid and optimal"


def validate_liquidation_buffer(
    stop_distance_pct: float, leverage: int
) -> Tuple[bool, float, str]:
    """
    Validate liquidation buffer.

    Based on Trading Instructions Section I.C - Liquidation Safety Requirements.

    Args:
        stop_distance_pct: Stop distance as percentage
        leverage: Leverage used

    Returns:
        Tuple of (is_safe, buffer_multiplier, message)
    """
    config = get_config()
    required_buffer = config.stop_loss.liquidation_buffer_multiplier  # 1.5

    if leverage <= 1:
        return True, float("inf"), "No leverage, no liquidation risk"

    liquidation_distance = 1.0 / leverage
    actual_buffer = liquidation_distance - stop_distance_pct
    buffer_multiplier = (
        actual_buffer / stop_distance_pct if stop_distance_pct > 0 else 0
    )

    if actual_buffer < (stop_distance_pct * required_buffer):
        return (
            False,
            buffer_multiplier,
            f"Buffer {buffer_multiplier:.2f}x < required {required_buffer}x",
        )

    return True, buffer_multiplier, f"Buffer {buffer_multiplier:.2f}x adequate"


def validate_account_risk(
    dollar_risk: float, account_balance: float, trade_quality: TradeQuality
) -> Tuple[bool, float, str]:
    """
    Validate account risk per trade.

    Based on Trading Instructions Section I - Risk Tiers by Trade Quality.

    Args:
        dollar_risk: Dollar risk amount
        account_balance: Account balance
        trade_quality: Trade quality

    Returns:
        Tuple of (is_ok, risk_pct, message)
    """
    config = get_config()
    risk_pct = dollar_risk / account_balance

    # Get max risk based on quality
    quality_limits = {
        TradeQuality.STANDARD: config.risk_limits.max_account_risk_per_trade,  # 8%
        TradeQuality.HIGH_CONVICTION: 0.11,  # 11%
        TradeQuality.EXCEPTIONAL: 0.15,  # 15%
    }

    max_risk = quality_limits[trade_quality]

    if risk_pct > config.risk_limits.max_account_risk_per_trade:
        return (
            False,
            risk_pct,
            f"Risk {risk_pct:.2%} > max {config.risk_limits.max_account_risk_per_trade:.2%}",
        )

    if risk_pct > max_risk:
        return False, risk_pct, f"Risk {risk_pct:.2%} > quality limit {max_risk:.2%}"

    return True, risk_pct, f"Risk {risk_pct:.2%} acceptable"


def validate_margin_drawdown(
    dollar_risk: float, margin_allocation: float
) -> Tuple[bool, float, str]:
    """
    Validate margin drawdown if stopped out.

    Based on Trading Instructions Section I - Margin drawdown if stopped is under 80%.

    Args:
        dollar_risk: Dollar risk amount
        margin_allocation: Margin allocated

    Returns:
        Tuple of (is_ok, drawdown_pct, message)
    """
    config = get_config()
    drawdown_pct = dollar_risk / margin_allocation

    if drawdown_pct > config.risk_limits.max_margin_drawdown:
        return (
            False,
            drawdown_pct,
            f"Drawdown {drawdown_pct:.2%} > max {config.risk_limits.max_margin_drawdown:.2%}",
        )

    return True, drawdown_pct, f"Drawdown {drawdown_pct:.2%} acceptable"


def validate_rrr(
    entry_price: float,
    stop_loss: float,
    take_profit: Optional[float],
    strategy: StrategyType,
) -> Tuple[bool, float, str]:
    """
    Validate reward-to-risk ratio.

    Based on Trading Instructions Section I.D - Minimum acceptable RRR: 2:1

    Args:
        entry_price: Entry price
        stop_loss: Stop loss price
        take_profit: Take profit price
        strategy: Trading strategy

    Returns:
        Tuple of (is_ok, rrr, message)
    """
    config = get_config()

    if take_profit is None:
        return False, 0, "No take profit set"

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return False, 0, "Risk is zero - stop loss equals entry price"
    reward = abs(take_profit - entry_price)
    rrr = reward / risk

    min_rrr = config.rrr.minimum_rrr
    if strategy == StrategyType.LIQUIDATION_CAPTURE:
        min_rrr = config.rrr.liquidation_event_min

    if rrr < min_rrr:
        return False, rrr, f"RRR {rrr:.2f} < minimum {min_rrr}"

    return True, rrr, f"RRR {rrr:.2f} acceptable"


def validate_volume_confirmation(
    current_volume: float, avg_volume: float, strategy: StrategyType
) -> Tuple[bool, float, str]:
    """
    Validate volume confirmation.

    Based on Trading Instructions Section III.B.3 - Volume Confirmation.

    Args:
        current_volume: Current candle volume
        avg_volume: Average volume
        strategy: Trading strategy

    Returns:
        Tuple of (is_confirmed, volume_ratio, message)
    """
    config = get_config()

    if avg_volume <= 0:
        return False, 0, "No volume data available (average volume is zero or negative)"

    volume_ratio = current_volume / avg_volume

    min_ratio = config.volume.confirmation_volume_min
    if strategy == StrategyType.BREAKOUT:
        min_ratio = config.volume.breakout_volume_min
    elif strategy == StrategyType.LIQUIDATION_CAPTURE:
        min_ratio = config.volume.liquidation_volume_min

    if volume_ratio < min_ratio:
        return (
            False,
            volume_ratio,
            f"Volume {volume_ratio:.2f}x < required {min_ratio}x",
        )

    return True, volume_ratio, f"Volume {volume_ratio:.2f}x confirmed"


def check_forbidden_conditions(
    market_state: MarketState,
    volume_ratio: float,
    news_events: bool = False,
    account_risk_today: float = 0,
    consecutive_losses: int = 0,
) -> Tuple[bool, List[str]]:
    """
    Check for forbidden trading conditions.

    Based on Trading Instructions Section V - FORBIDDEN TRADE CONDITIONS.

    Args:
        market_state: Current market state
        volume_ratio: Current volume vs average
        news_events: High-impact news pending
        account_risk_today: Risk taken today
        consecutive_losses: Current losing streak

    Returns:
        Tuple of (is_clear, violations)
    """
    config = get_config()
    violations = []

    # Market state unclear
    if market_state == MarketState.TRANSITION:
        violations.append("Market in transition phase - unclear state")

    # Low volume
    if volume_ratio < config.forbidden_conditions.low_volume_threshold:
        violations.append(
            f"Volume {volume_ratio:.2f}x < threshold {config.forbidden_conditions.low_volume_threshold}"
        )

    # News events
    if news_events:
        violations.append("High-impact news event pending")

    # Daily loss limit
    if account_risk_today >= config.risk_limits.daily_loss_limit:
        violations.append(f"Daily loss limit reached: ${account_risk_today:.2f}")

    # Consecutive losses
    if consecutive_losses >= config.risk_limits.consecutive_loss_limit:
        violations.append(
            f"Consecutive losses: {consecutive_losses} >= {config.risk_limits.consecutive_loss_limit}"
        )

    return len(violations) == 0, violations


def validate_signal_comprehensive(signal: Signal, account: Account) -> RiskValidation:
    """
    Comprehensive signal validation.

    Based on Trading Instructions Section III.B - Entry Signal Requirements.

    Args:
        signal: Trading signal to validate
        account: Current account state

    Returns:
        RiskValidation result
    """
    validation = RiskValidation(
        is_valid=True,
        stop_in_range=False,
        stop_optimal=False,
        liquidation_safe=False,
        account_risk_ok=False,
        margin_drawdown_ok=False,
        rrr_meets_minimum=False,
        volume_confirmed=signal.volume_confirmation,
        multi_timeframe_aligned=signal.multi_timeframe_alignment,
        forbidden_conditions_clear=signal.forbidden_conditions_clear,
    )

    # Calculate stop distance
    stop_distance_pct = signal.stop_distance_pct

    # Map asset to class based on signal asset
    asset_upper = signal.asset.upper() if signal.asset else ""
    if asset_upper in ("BTC", "ETH", "BTC-PERP", "ETH-PERP", "BTCUSDT", "ETHUSDT"):
        asset_class = AssetClass.BTC_ETH
    elif asset_upper in ("SOL", "AVAX", "DOT", "MATIC", "LINK", "UNI", "AAVE",
                         "SOL-PERP", "AVAX-PERP", "DOT-PERP", "MATIC-PERP"):
        asset_class = AssetClass.LARGE_CAP
    else:
        # Small cap / meme for unknown assets (more conservative)
        asset_class = AssetClass.SMALL_CAP

    # Validate stop loss
    stop_valid, stop_optimal, stop_msg = validate_stop_loss(
        asset_class, stop_distance_pct
    )
    validation.stop_in_range = stop_valid
    validation.stop_optimal = stop_optimal
    if not stop_valid:
        validation.add_error(stop_msg)
    elif not stop_optimal:
        validation.add_warning(stop_msg)

    # Validate liquidation buffer
    liq_safe, buffer_mult, liq_msg = validate_liquidation_buffer(
        stop_distance_pct, signal.leverage or 15
    )
    validation.liquidation_safe = liq_safe
    if not liq_safe:
        validation.add_error(liq_msg)

    # Calculate position sizing
    pos_calc = calculate_position_size(
        account.balance,
        signal.entry_price,
        signal.stop_loss,
        signal.quality,
        signal.leverage or 15,
    )

    # Validate account risk
    risk_ok, risk_pct, risk_msg = validate_account_risk(
        pos_calc["dollar_risk"], account.balance, signal.quality
    )
    validation.account_risk_ok = risk_ok
    if not risk_ok:
        validation.add_error(risk_msg)

    # Validate margin drawdown
    drawdown_ok, drawdown_pct, drawdown_msg = validate_margin_drawdown(
        pos_calc["dollar_risk"], pos_calc["margin_allocation"]
    )
    validation.margin_drawdown_ok = drawdown_ok
    if not drawdown_ok:
        validation.add_error(drawdown_msg)

    # Validate RRR
    rrr_ok, rrr_value, rrr_msg = validate_rrr(
        signal.entry_price, signal.stop_loss, signal.take_profit, signal.strategy
    )
    validation.rrr_meets_minimum = rrr_ok
    if not rrr_ok:
        validation.add_error(rrr_msg)

    # Overall validation
    validation.is_valid = validation.all_checks_pass

    logger.info(
        f"Signal validation complete: valid={validation.is_valid}, errors={len(validation.errors)}"
    )
    return validation


def check_circuit_breakers(account: Account) -> Tuple[bool, str, str]:
    """
    Check for circuit breaker conditions.

    Based on Trading Instructions Section IX - Circuit Breakers.

    Args:
        account: Current account state

    Returns:
        Tuple of (should_stop, reason, action)
    """
    global circuit_breaker_enabled

    if not circuit_breaker_enabled:
        return False, "", ""

    config = get_config()

    # Daily loss limit
    if account.daily_pnl <= -config.risk_limits.daily_loss_limit:
        return True, "Daily loss limit reached", "cease_24h"

    # Consecutive losses
    if (
        account.consecutive_losses
        >= config.circuit_breakers.consecutive_losses["threshold"]
    ):
        return (
            True,
            f"Consecutive losses: {account.consecutive_losses}",
            "break_4h_reduce_50pct",
        )

    # Drawdown
    if account.drawdown_pct >= config.circuit_breakers.drawdown["threshold"]:
        return True, f"Drawdown: {account.drawdown_pct:.2%}", "cease_immediate"

    # Win rate collapse (need at least 20 trades)
    if len(account.trades) >= 20:
        if account.win_rate < config.circuit_breakers.win_rate_collapse["threshold"]:
            return True, f"Win rate: {account.win_rate:.2%}", "stop_intensive_review"

    return False, "", ""


def calculate_atr_based_stop(
    current_price: float, atr: float, multiplier: float = 2.0, side: str = "buy"
) -> float:
    """
    Calculate ATR-based stop loss.

    Based on Trading Instructions Section I.B - Volatility-Based Stops.

    Args:
        current_price: Current price
        atr: Average True Range
        multiplier: ATR multiplier (conservative: 2.0, aggressive: 1.5)
        side: 'buy' or 'sell'

    Returns:
        Stop loss price
    """
    stop_distance = atr * multiplier

    if side.lower() == "buy":
        return current_price - stop_distance
    else:
        return current_price + stop_distance


def calculate_pacifica_funding_rate(
    premium_index: float, interest_rate: float = 0.0001
) -> float:
    """
    Calculate Pacifica funding rate using their specific formula.

    Pacifica Formula: funding_rate = (premium_index + clamp(interest_rate - premium_index, -0.05%, 0.05%)) / 8

    Where:
    - premium_index = (impact_price / oracle_price) - 1
    - interest_rate = fixed 0.01% (0.0001)
    - clamp maintains ±0.05% bounds
    - Division by 8 scales 8-hour standard to 1-hour intervals

    Args:
        premium_index: Premium index (impact_price/oracle_price - 1)
        interest_rate: Fixed interest rate (default 0.01%)

    Returns:
        Hourly funding rate (as decimal)
    """
    # Clamp the difference between interest rate and premium index
    clamped_diff = max(-0.0005, min(0.0005, interest_rate - premium_index))

    # Apply Pacifica formula
    funding_rate = (premium_index + clamped_diff) / 8

    # Cap at ±4% per hour
    funding_rate = max(-0.04, min(0.04, funding_rate))

    return funding_rate


def calculate_funding_rate_impact(
    position_value: float, funding_rate: float, hours_held: float
) -> float:
    """
    Calculate funding rate cost/profit.

    ⚠️ CRITICAL for Pacifica: Funding is charged HOURLY (24 times per day)
    Standard exchanges charge every 8 hours (3 times per day)
    For the SAME funding rate, Pacifica costs 8x MORE!

    Args:
        position_value: Position value
        funding_rate: Hourly funding rate (decimal, e.g., 0.0001 = 0.01%)
        hours_held: Hours position held

    Returns:
        Funding cost (positive = cost, negative = profit)
    """
    # Pacifica: Hourly funding means 24 payments per day
    hourly_cost = position_value * funding_rate
    total_cost = hourly_cost * hours_held

    return total_cost


def calculate_pacifica_funding_cost(
    position_value: float,
    funding_rate: float,
    hours_held: float = 24
) -> Dict[str, float]:
    """
    Calculate comprehensive Pacifica funding costs with 8x comparison.

    ⚠️ CRITICAL: Pacifica charges funding 24 times per day (hourly)
    Standard exchanges charge 3 times per day (8-hour intervals)

    Args:
        position_value: Position value in USD
        funding_rate: Hourly funding rate (as decimal)
        hours_held: Hours to hold position (default 24 for full day)

    Returns:
        Dict with hourly_cost, total_cost, daily_cost, weekly_cost, monthly_cost,
        standard_exchange_cost, cost_multiplier, and cost_as_pct_of_position
    """
    # Pacifica: Hourly (24x per day)
    hourly_cost = position_value * funding_rate
    total_cost = hourly_cost * hours_held
    daily_cost = hourly_cost * 24
    weekly_cost = hourly_cost * 24 * 7
    monthly_cost = hourly_cost * 24 * 30

    # Standard exchanges: 8-hour intervals (3x per day)
    # Same rate applied less frequently
    standard_daily_cost = (position_value * funding_rate) * 3

    # Cost multiplier (should be 8x)
    cost_multiplier = daily_cost / standard_daily_cost if standard_daily_cost > 0 else 0

    # As percentage of position
    cost_as_pct = (total_cost / position_value) * 100 if position_value > 0 else 0

    return {
        "hourly_cost": hourly_cost,
        "total_cost": total_cost,
        "daily_cost": daily_cost,
        "weekly_cost": weekly_cost,
        "monthly_cost": monthly_cost,
        "standard_exchange_daily_cost": standard_daily_cost,
        "cost_multiplier": cost_multiplier,
        "cost_as_pct_of_position": cost_as_pct,
        "hours_held": hours_held,
        "funding_payments_count": hours_held  # One payment per hour
    }


def validate_pre_trade_checklist(
    signal: Signal, account: Account
) -> Tuple[bool, List[str]]:
    """
    Run complete pre-trade checklist.

    Based on Trading Instructions Section VI.A - Pre-Entry Checklist.

    Args:
        signal: Trading signal
        account: Account state

    Returns:
        Tuple of (can_trade, issues)
    """
    issues = []

    # All qualification criteria confirmed
    validation = validate_signal_comprehensive(signal, account)
    if not validation.is_valid:
        issues.extend(validation.errors)

    # Position size calculated
    # (Already done in validation)

    # Leverage selected
    config = get_config()
    if (
        signal.leverage < config.position_sizing.min_leverage
        or signal.leverage > config.position_sizing.max_leverage
    ):
        issues.append(
            f"Leverage {signal.leverage} outside range [{config.position_sizing.min_leverage}, {config.position_sizing.max_leverage}]"
        )

    # Stop-loss price calculated
    # (Already validated)

    # Profit target set
    if signal.take_profit is None:
        issues.append("No profit target set")

    # Liquidation distance verified
    # (Already done in validation)

    # Multi-timeframe alignment confirmed
    if not signal.multi_timeframe_alignment:
        issues.append("Multi-timeframe alignment not confirmed")

    # Account has available margin
    pos_calc = calculate_position_size(
        account.balance,
        signal.entry_price,
        signal.stop_loss,
        signal.quality,
        signal.leverage or 15,
    )
    if pos_calc["margin_allocation"] > account.available_margin:
        issues.append(
            f"Insufficient margin: need ${pos_calc['margin_allocation']:.2f}, available ${account.available_margin:.2f}"
        )

    return len(issues) == 0, issues


# ===========================
# SUBACCOUNT-SPECIFIC RISK VALIDATION
# ===========================

def validate_subaccount_position_size(
    position_value: float,
    subaccount_config: Any,
) -> Tuple[bool, str]:
    """
    Validate position size against subaccount-specific limits.

    Args:
        position_value: Proposed position value in USD
        subaccount_config: SubaccountConfig with max_position_size

    Returns:
        Tuple of (is_valid, message)
    """
    max_size = subaccount_config.max_position_size

    if position_value > max_size:
        return (
            False,
            f"Position ${position_value:.2f} exceeds subaccount limit ${max_size:.2f}"
        )

    return True, f"Position ${position_value:.2f} within subaccount limit ${max_size:.2f}"


def validate_subaccount_leverage(
    leverage: int,
    subaccount_config: Any,
) -> Tuple[bool, str]:
    """
    Validate leverage against subaccount-specific limits.

    Args:
        leverage: Proposed leverage
        subaccount_config: SubaccountConfig with max_leverage

    Returns:
        Tuple of (is_valid, message)
    """
    max_leverage = subaccount_config.max_leverage

    if leverage > max_leverage:
        return (
            False,
            f"Leverage {leverage}x exceeds subaccount limit {max_leverage}x"
        )

    return True, f"Leverage {leverage}x within subaccount limit {max_leverage}x"


def validate_subaccount_risk_per_trade(
    dollar_risk: float,
    account_balance: float,
    subaccount_config: Any,
) -> Tuple[bool, float, str]:
    """
    Validate risk per trade against subaccount-specific limits.

    Args:
        dollar_risk: Dollar risk amount
        account_balance: Subaccount balance
        subaccount_config: SubaccountConfig with risk_per_trade

    Returns:
        Tuple of (is_valid, risk_pct, message)
    """
    # Return max risk (reject trade) if account balance is zero or negative
    if account_balance <= 0:
        return (
            False,
            1.0,
            "Cannot calculate risk: account balance is zero or negative"
        )
    risk_pct = dollar_risk / account_balance
    max_risk_pct = subaccount_config.risk_per_trade

    if risk_pct > max_risk_pct:
        return (
            False,
            risk_pct,
            f"Risk {risk_pct:.2%} exceeds subaccount limit {max_risk_pct:.2%}"
        )

    return True, risk_pct, f"Risk {risk_pct:.2%} within subaccount limit {max_risk_pct:.2%}"


def calculate_subaccount_position_size(
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    subaccount_config: Any,
    leverage: Optional[int] = None,
) -> Dict[str, float]:
    """
    Calculate position size with subaccount-specific constraints.

    Applies subaccount's max_position_size, risk_per_trade, and max_leverage limits.

    Args:
        account_balance: Subaccount balance
        entry_price: Planned entry price
        stop_loss: Stop loss price
        subaccount_config: SubaccountConfig with risk parameters
        leverage: Leverage to use (uses subaccount max if None)

    Returns:
        Dict with margin_allocation, position_value, quantity, etc.
    """
    # Use subaccount's max leverage if not specified
    if leverage is None:
        leverage = subaccount_config.max_leverage

    # Ensure leverage doesn't exceed subaccount limit
    leverage = min(leverage, subaccount_config.max_leverage)

    # Calculate stop distance (protect against division by zero)
    if entry_price <= 0:
        raise ValueError("Entry price must be greater than zero")
    stop_distance_pct = abs(entry_price - stop_loss) / entry_price

    # Calculate max position based on risk per trade (protect against zero stop distance)
    if stop_distance_pct <= 0:
        raise ValueError("Stop distance must be greater than zero (stop loss equals entry price)")
    max_dollar_risk = account_balance * subaccount_config.risk_per_trade
    max_position_value_from_risk = max_dollar_risk / stop_distance_pct

    # Also cap by subaccount's max position size
    position_value = min(
        max_position_value_from_risk,
        subaccount_config.max_position_size
    )

    # Calculate margin needed
    margin_allocation = position_value / leverage

    # Calculate quantity
    quantity = position_value / entry_price

    # Calculate actual risk metrics
    dollar_risk = position_value * stop_distance_pct
    account_risk_pct = dollar_risk / account_balance
    margin_drawdown_pct = dollar_risk / margin_allocation

    return {
        "margin_allocation": margin_allocation,
        "position_value": position_value,
        "quantity": quantity,
        "dollar_risk": dollar_risk,
        "account_risk_pct": account_risk_pct,
        "margin_drawdown_pct": margin_drawdown_pct,
        "stop_distance_pct": stop_distance_pct,
        "leverage": leverage,
        "subaccount_id": subaccount_config.subaccount_id,
        "max_position_size": subaccount_config.max_position_size,
        "max_leverage": subaccount_config.max_leverage,
        "risk_per_trade_limit": subaccount_config.risk_per_trade,
    }


def validate_strategy_compatibility(
    signal: Signal,
    subaccount_config: Any,
) -> Tuple[bool, str]:
    """
    Validate if signal is compatible with subaccount's trading strategy.

    Checks strategy profile constraints like:
    - Allow overnight positions
    - Max hold time
    - Trading timeframe
    - Market conditions

    Args:
        signal: Trading signal
        subaccount_config: SubaccountConfig with assigned strategy

    Returns:
        Tuple of (is_valid, message)
    """
    try:
        from trading_strategies import get_strategy_profile

        strategy_profile = get_strategy_profile(subaccount_config.trading_strategy)

        issues = []

        # Check leverage compatibility
        if signal.leverage and signal.leverage > strategy_profile.max_leverage:
            issues.append(
                f"Signal leverage {signal.leverage}x exceeds strategy limit {strategy_profile.max_leverage}x"
            )

        # Check position size compatibility
        if hasattr(signal, 'position_value'):
            if signal.position_value > strategy_profile.max_position_size:
                issues.append(
                    f"Position ${signal.position_value:.2f} exceeds strategy limit ${strategy_profile.max_position_size:.2f}"
                )

        # Check signal confidence
        if hasattr(signal, 'confidence'):
            if signal.confidence < strategy_profile.min_signal_confidence:
                issues.append(
                    f"Signal confidence {signal.confidence:.2f} below strategy minimum {strategy_profile.min_signal_confidence:.2f}"
                )

        if issues:
            return False, "; ".join(issues)

        return True, f"Signal compatible with {strategy_profile.name} strategy"

    except Exception as e:
        logger.warning(f"Could not validate strategy compatibility: {e}")
        return True, "Strategy validation skipped (no profile available)"


def validate_subaccount_comprehensive(
    signal: Signal,
    account: Account,
    subaccount_config: Any,
    subaccount_balance: float,
) -> RiskValidation:
    """
    Comprehensive risk validation with subaccount-specific limits.

    Combines standard risk validation with subaccount constraints.

    Args:
        signal: Trading signal
        account: Account state (main account)
        subaccount_config: SubaccountConfig with risk parameters
        subaccount_balance: Current subaccount balance

    Returns:
        RiskValidation object with all checks
    """
    # Start with standard validation
    base_validation = validate_signal_comprehensive(signal, account)

    # Create modified account state for subaccount with COPIED positions list
    # to avoid shared mutable state issues
    subaccount = Account(
        balance=subaccount_balance,
        available_margin=subaccount_balance,  # Simplified
        # Note: We don't copy positions here to avoid complexity;
        # subaccount validation focuses on balance-based risk checks
        daily_pnl=0.0,  # Per-subaccount tracking would be ideal
        total_pnl=0.0,
        max_drawdown=0.0,
        margin_used=0.0,  # Start fresh for subaccount calculation
    )

    # Calculate position size with subaccount limits
    pos_calc = calculate_subaccount_position_size(
        subaccount_balance,
        signal.entry_price,
        signal.stop_loss,
        subaccount_config,
        signal.leverage,
    )

    errors = list(base_validation.errors)
    warnings = list(base_validation.warnings)

    # Validate subaccount-specific constraints
    size_ok, size_msg = validate_subaccount_position_size(
        pos_calc["position_value"],
        subaccount_config,
    )
    if not size_ok:
        errors.append(size_msg)

    leverage_ok, leverage_msg = validate_subaccount_leverage(
        signal.leverage or 15,
        subaccount_config,
    )
    if not leverage_ok:
        errors.append(leverage_msg)

    risk_ok, risk_pct, risk_msg = validate_subaccount_risk_per_trade(
        pos_calc["dollar_risk"],
        subaccount_balance,
        subaccount_config,
    )
    if not risk_ok:
        errors.append(risk_msg)
    else:
        warnings.append(risk_msg)

    # Validate strategy compatibility
    strategy_ok, strategy_msg = validate_strategy_compatibility(
        signal,
        subaccount_config,
    )
    if not strategy_ok:
        errors.append(strategy_msg)
    else:
        warnings.append(strategy_msg)

    # Check if subaccount is enabled
    if not subaccount_config.enabled:
        errors.append(f"Subaccount {subaccount_config.subaccount_id} is disabled for trading")

    is_valid = len(errors) == 0

    return RiskValidation(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        calculated_position=pos_calc,
    )


def get_subaccount_risk_summary(
    subaccount_config: Any,
    current_balance: float,
    open_positions_count: int = 0,
) -> Dict[str, Any]:
    """
    Get risk summary for a subaccount.

    Args:
        subaccount_config: SubaccountConfig
        current_balance: Current subaccount balance
        open_positions_count: Number of open positions

    Returns:
        Dict with risk metrics and limits
    """
    return {
        "subaccount_id": subaccount_config.subaccount_id,
        "subaccount_name": subaccount_config.subaccount_name,
        "strategy": subaccount_config.trading_strategy.value,
        "balance": current_balance,
        "enabled": subaccount_config.enabled,
        "risk_limits": {
            "max_position_size": subaccount_config.max_position_size,
            "risk_per_trade": subaccount_config.risk_per_trade,
            "max_leverage": subaccount_config.max_leverage,
        },
        "utilization": {
            "open_positions": open_positions_count,
            "max_open_positions": getattr(subaccount_config, "max_open_positions", None),
        },
        "available_for_trade": subaccount_config.enabled and current_balance > 0,
    }
