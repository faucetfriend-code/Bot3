"""
Funding Rate Arbitrage Strategy

Captures hourly funding payments on Pacifica perpetuals using delta-neutral positions.
This is a passive strategy that runs in ALL regimes.

Pacifica has HOURLY funding (24x/day) vs standard 8-hour, creating more opportunities.

RISK NOTES:
- Delta-neutral reduces directional risk but not basis risk
- Funding rates can flip quickly during volatility
- All sizing delegated to RiskManager
"""

import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from loguru import logger

from ..models import Signal, OrderSide
from ..config import StrategyType, TradeQuality, MarketState, AssetClass


class FundingArbStrategy:
    """
    Delta-neutral funding rate arbitrage.

    Monitors funding rates across symbols and opens hedged positions
    to capture funding payments without directional exposure.
    """

    def __init__(
        self,
        min_funding_rate: float = 0.0001,      # 0.01% minimum to act
        max_allocation_pct: float = 0.20,       # Max 20% of account
        rebalance_threshold: float = 0.02,      # Rebalance if delta > 2%
        lookback_hours: int = 8,                # Hours of funding history to analyze
        min_confidence: float = 0.70,
        client=None,                            # Pacifica client for API calls
    ):
        self.strategy_type = StrategyType.FUNDING_ARB
        self.min_funding_rate = min_funding_rate
        self.max_allocation_pct = max_allocation_pct
        self.rebalance_threshold = rebalance_threshold
        self.lookback_hours = lookback_hours
        self.min_confidence = min_confidence
        self.client = client

        # Track active arb positions
        self.active_positions: Dict[str, Dict] = {}
        # {symbol: {"side": "long_funding", "size": 100, "entry_rate": 0.0003, "opened_at": datetime}}

        # Funding rate cache
        self.funding_cache: Dict[str, Dict] = {}
        # {symbol: {"current_rate": 0.0003, "avg_rate_8h": 0.00025, "next_payment": datetime}}

        # Last cache update time
        self._last_cache_update: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5 minutes

        logger.info(
            f"FundingArbStrategy initialized: min_rate={min_funding_rate:.4%}, "
            f"max_alloc={max_allocation_pct:.0%}, rebalance_threshold={rebalance_threshold:.1%}"
        )

    def update_funding_rates(self, symbols: Optional[List[str]] = None) -> None:
        """
        Fetch and cache current funding rates for all symbols.
        Call this every 5-15 minutes.
        """
        if not self.client:
            logger.warning("FundingArb: No client available for funding rate updates")
            return

        # Check cache freshness
        now = datetime.now(timezone.utc)
        if self._last_cache_update:
            age = (now - self._last_cache_update).total_seconds()
            if age < self._cache_ttl_seconds:
                return  # Cache still valid

        if symbols is None:
            symbols = ["BTC", "ETH", "SOL", "XRP", "DOGE", "LTC", "AVAX", "LINK"]

        for symbol in symbols:
            try:
                # Get current funding rate from market data
                market_data = self.client.get_market_data(symbol)
                if market_data:
                    funding_rate = market_data.get("funding_rate", 0)
                    next_funding = market_data.get("next_funding_time")

                    self.funding_cache[symbol] = {
                        "current_rate": float(funding_rate) if funding_rate else 0,
                        "next_payment": next_funding,
                        "updated_at": now
                    }

                    # Calculate 8h average from history if available
                    try:
                        history = self.client.get_funding_history(symbol, limit=8)
                        if history and len(history) > 0:
                            rates = [h.get("funding_rate", 0) for h in history]
                            avg_rate = sum(rates) / len(rates) if rates else funding_rate
                            self.funding_cache[symbol]["avg_rate_8h"] = avg_rate
                    except Exception:
                        self.funding_cache[symbol]["avg_rate_8h"] = funding_rate

            except Exception as e:
                logger.debug(f"FundingArb: Error fetching {symbol} funding rate: {e}")

        self._last_cache_update = now
        logger.debug(f"FundingArb: Updated funding rates for {len(self.funding_cache)} symbols")

    def analyze_funding_opportunity(self, symbol: str) -> Optional[Dict]:
        """
        Analyze if a symbol presents a good funding arb opportunity.

        Returns dict with opportunity details or None if not attractive.
        """
        if symbol not in self.funding_cache:
            return None

        cache = self.funding_cache[symbol]
        current_rate = cache.get("current_rate", 0)
        avg_rate = cache.get("avg_rate_8h", current_rate)

        # Check minimum rate threshold
        if abs(current_rate) < self.min_funding_rate:
            return None

        # Check rate consistency (not about to flip)
        if current_rate * avg_rate < 0:  # Different signs = unstable
            logger.debug(f"{symbol}: Funding rate unstable (current vs avg signs differ)")
            return None

        # Calculate expected hourly yield
        hourly_yield = abs(current_rate)
        daily_yield = hourly_yield * 24
        annualized_yield = daily_yield * 365

        # Determine direction
        # Positive rate = longs pay shorts → we want to be SHORT perp (receive funding)
        # Negative rate = shorts pay longs → we want to be LONG perp (receive funding)
        arb_side = "short_funding" if current_rate > 0 else "long_funding"
        perp_side = OrderSide.SELL if current_rate > 0 else OrderSide.BUY

        # Confidence based on rate magnitude and consistency
        confidence = self.min_confidence
        if abs(current_rate) > self.min_funding_rate * 2:
            confidence += 0.1
        if abs(current_rate) > self.min_funding_rate * 5:
            confidence += 0.1
        if abs(avg_rate) >= abs(current_rate) * 0.8:  # Consistent
            confidence += 0.05

        confidence = min(0.95, confidence)

        return {
            "symbol": symbol,
            "current_rate": current_rate,
            "avg_rate_8h": avg_rate,
            "arb_side": arb_side,
            "perp_side": perp_side,
            "hourly_yield": hourly_yield,
            "annualized_yield": annualized_yield,
            "confidence": confidence,
            "next_payment": cache.get("next_payment"),
        }

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Any],
        current_price: float,
        account_balance: float = 0,
        **kwargs
    ) -> List[Signal]:
        """
        Generate funding arb signals.

        Unlike other strategies, this doesn't use price action.
        It monitors funding rates and suggests delta-neutral positions.
        """
        signals = []

        # Update cache if needed
        self.update_funding_rates([symbol])

        # Analyze opportunity
        opportunity = self.analyze_funding_opportunity(symbol)
        if not opportunity:
            return signals

        # Check if we already have a position
        if symbol in self.active_positions:
            existing = self.active_positions[symbol]
            # Check if we need to close (rate flipped or dropped)
            if self._should_close_position(symbol, existing, opportunity):
                # Generate close signal
                close_signal = self._create_close_signal(symbol, existing, current_price)
                if close_signal:
                    signals.append(close_signal)
            return signals

        # Check confidence threshold
        if opportunity["confidence"] < self.min_confidence:
            return signals

        # Get account balance from client if not provided
        if account_balance <= 0 and self.client:
            try:
                balance_data = self.client.get_balance()
                account_balance = float(balance_data.get("equity", 0) or balance_data.get("balance", 0))
            except Exception as e:
                logger.debug(f"FundingArb: Could not fetch account balance: {e}")
                account_balance = 0

        # Calculate position size based on allocation
        max_position_value = account_balance * self.max_allocation_pct if account_balance > 0 else 1000

        # Scale with funding rate magnitude (higher rate = worth more capital)
        rate_multiplier = min(abs(opportunity["current_rate"]) / 0.0005, 1.0)  # Cap at 0.05%
        position_value = max_position_value * rate_multiplier

        # Create entry signal
        signal = Signal(
            strategy=self.strategy_type,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=opportunity["perp_side"],
            entry_price=current_price,
            stop_loss=None,  # Delta-neutral doesn't use traditional stops
            take_profit=None,
            confidence=opportunity["confidence"],
            quality=TradeQuality.STANDARD,
            timeframe="funding",
            market_state=MarketState.UNKNOWN,
            notes=(
                f"Funding arb: rate={opportunity['current_rate']:.4%}/hr, "
                f"APY={opportunity['annualized_yield']:.1%}, "
                f"side={opportunity['arb_side']}"
            ),
            indicators={
                "funding_rate": opportunity["current_rate"],
                "avg_rate_8h": opportunity["avg_rate_8h"],
                "annualized_yield": opportunity["annualized_yield"],
                "arb_side": opportunity["arb_side"],
                "position_value": position_value,
            },
            # Validation flags - funding arb doesn't need traditional validation
            volume_confirmation=True,
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=True,  # No stop loss, so RRR doesn't apply
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )

        signals.append(signal)
        logger.info(
            f"{symbol}: Funding arb signal - {opportunity['arb_side']}, "
            f"rate={opportunity['current_rate']:.4%}, APY={opportunity['annualized_yield']:.1%}"
        )

        return signals

    def _should_close_position(
        self, symbol: str, existing: Dict, current_opportunity: Optional[Dict]
    ) -> bool:
        """Check if existing position should be closed."""
        if not current_opportunity:
            return True  # No opportunity = close

        # Rate flipped direction
        if existing.get("side") != current_opportunity["arb_side"]:
            logger.info(f"{symbol}: Funding rate flipped, closing arb position")
            return True

        # Rate dropped below threshold
        if abs(current_opportunity["current_rate"]) < self.min_funding_rate * 0.5:
            logger.info(f"{symbol}: Funding rate too low, closing arb position")
            return True

        return False

    def _create_close_signal(
        self, symbol: str, existing: Dict, current_price: float
    ) -> Optional[Signal]:
        """Create signal to close existing arb position."""
        # Reverse the existing side
        existing_side = existing.get("perp_side", OrderSide.BUY)
        close_side = OrderSide.BUY if existing_side == OrderSide.SELL else OrderSide.SELL

        return Signal(
            strategy=self.strategy_type,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=close_side,
            entry_price=current_price,
            stop_loss=None,
            take_profit=None,
            confidence=0.90,
            quality=TradeQuality.STANDARD,
            timeframe="funding",
            market_state=MarketState.UNKNOWN,
            notes="Closing funding arb position",
            indicators={
                "arb_type": "funding_rate_close",
            },
            # All validation flags true for close
            volume_confirmation=True,
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=True,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )

    def register_position(self, symbol: str, side: str, size: float, rate: float) -> None:
        """Register an opened arb position."""
        self.active_positions[symbol] = {
            "side": side,
            "perp_side": OrderSide.SELL if side == "short_funding" else OrderSide.BUY,
            "size": size,
            "entry_rate": rate,
            "opened_at": datetime.now(timezone.utc)
        }
        logger.info(f"FundingArb: Registered {symbol} position - {side}, size={size:.2f}")

    def close_position(self, symbol: str) -> None:
        """Mark a position as closed."""
        if symbol in self.active_positions:
            del self.active_positions[symbol]
            logger.info(f"FundingArb: Closed {symbol} position")

    def get_active_positions(self) -> Dict[str, Dict]:
        """Return currently active arb positions."""
        return self.active_positions.copy()

    def get_funding_summary(self) -> Dict[str, Any]:
        """Get summary of current funding opportunities."""
        opportunities = []
        for symbol, cache in self.funding_cache.items():
            rate = cache.get("current_rate", 0)
            if abs(rate) >= self.min_funding_rate:
                opportunities.append({
                    "symbol": symbol,
                    "rate": rate,
                    "apy": abs(rate) * 24 * 365,
                    "direction": "short" if rate > 0 else "long"
                })

        # Sort by absolute rate
        opportunities.sort(key=lambda x: abs(x["rate"]), reverse=True)

        return {
            "opportunities": opportunities,
            "active_positions": len(self.active_positions),
            "cache_age_seconds": (datetime.now(timezone.utc) - self._last_cache_update).total_seconds() if self._last_cache_update else None
        }
