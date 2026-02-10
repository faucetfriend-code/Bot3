# Implementation Prompt: Funding Rate Arbitrage Strategy

## Priority: 2 | Difficulty: Low | Effort: 2-4 days

---

## Task Overview

Implement a **Funding Rate Arbitrage** strategy that captures hourly funding payments on Pacifica perpetuals. This is a delta-neutral, passive income strategy with very low directional risk.

## Why This Strategy

- Pacifica has **hourly funding** (24x per day vs standard 8-hour)
- Delta-neutral = price movement doesn't affect P&L
- Passive income while other strategies run
- Low risk, consistent returns (10-30% APY in favorable conditions)

---

## Technical Requirements

### 1. New Files to Create

```
trading_bot_v2/strategies/funding_arb.py
```

### 2. Config Updates

Add to `.env`:
```
ENABLE_FUNDING_ARB=true
FUNDING_ARB_MIN_RATE=0.0001
FUNDING_ARB_MAX_ALLOCATION_PCT=0.20
FUNDING_ARB_REBALANCE_THRESHOLD=0.02
```

Add to `config.py`:
```python
self.enable_funding_arb: bool = os.getenv("ENABLE_FUNDING_ARB", "false").lower() == "true"
self.funding_arb_min_rate: float = float(os.getenv("FUNDING_ARB_MIN_RATE", "0.0001"))
self.funding_arb_max_allocation_pct: float = float(os.getenv("FUNDING_ARB_MAX_ALLOCATION_PCT", "0.20"))
```

Add to `Example files/core_logic/config.py` (StrategyType enum):
```python
FUNDING_ARB = "funding_arb"
```

### 3. Pacifica API Requirements

Need to fetch funding rate data. Check Pacifica API for:
- Current funding rate per symbol
- Next funding payment time
- Historical funding rates

Likely endpoints:
```
GET /funding_rate?symbol=BTC
GET /funding_history?symbol=BTC&limit=24
```

Or via WebSocket subscription to funding channel.

---

## Strategy Logic

### Core Concept: Delta-Neutral Position

```
When funding rate is POSITIVE (longs pay shorts):
  → Short perpetual + Long equivalent spot/stablecoin
  → Collect funding every hour

When funding rate is NEGATIVE (shorts pay longs):
  → Long perpetual + Short equivalent (or hold stablecoin)
  → Collect funding every hour
```

### Entry Conditions

1. Absolute funding rate > `FUNDING_ARB_MIN_RATE` (e.g., 0.01% = 0.0001)
2. Rate expected to persist (check 4-8 hour trend)
3. Available capital within `FUNDING_ARB_MAX_ALLOCATION_PCT`
4. No existing position in opposite direction

### Position Management

```python
def calculate_hedge_size(self, funding_rate: float, account_balance: float) -> float:
    """
    Determine position size for funding arb.
    Conservative: Use 10-20% of account for delta-neutral positions.
    """
    max_allocation = account_balance * self.max_allocation_pct

    # Scale with funding rate magnitude (higher rate = worth more capital)
    rate_multiplier = min(abs(funding_rate) / 0.0005, 1.0)  # Cap at 0.05%

    return max_allocation * rate_multiplier
```

### Exit Conditions

1. Funding rate flips sign (positive → negative or vice versa)
2. Funding rate drops below minimum threshold
3. Basis risk exceeds tolerance (spot-perp spread widens unexpectedly)
4. Manual override / circuit breaker

### Risk Management

- **Basis Risk**: Spot and perp prices can diverge temporarily
- **Liquidation Risk**: If only holding perp, need margin buffer
- **Funding Flip Risk**: Rates can change direction quickly
- **Execution Risk**: Need to enter/exit both legs simultaneously

---

## Implementation Skeleton

```python
# strategies/funding_arb.py

"""
Funding Rate Arbitrage Strategy

Captures hourly funding payments on Pacifica perpetuals using delta-neutral positions.
This is a passive strategy that runs in ALL regimes.

RISK NOTES:
- Delta-neutral reduces directional risk but not basis risk
- Funding rates can flip quickly during volatility
- All sizing delegated to RiskManager
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "Example files", "core_logic"))

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from loguru import logger

from models import Signal, OrderSide
from config import StrategyType, TradeQuality


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
    ):
        self.strategy_type = StrategyType.FUNDING_ARB
        self.min_funding_rate = min_funding_rate
        self.max_allocation_pct = max_allocation_pct
        self.rebalance_threshold = rebalance_threshold
        self.lookback_hours = lookback_hours
        self.min_confidence = min_confidence

        # Track active arb positions
        self.active_positions: Dict[str, Dict] = {}
        # {symbol: {"side": "long_funding", "size": 100, "entry_rate": 0.0003, "opened_at": datetime}}

        # Funding rate cache
        self.funding_cache: Dict[str, Dict] = {}
        # {symbol: {"current_rate": 0.0003, "avg_rate_8h": 0.00025, "next_payment": datetime}}

        logger.info(
            f"FundingArbStrategy initialized: min_rate={min_funding_rate:.4%}, "
            f"max_alloc={max_allocation_pct:.0%}, rebalance_threshold={rebalance_threshold:.1%}"
        )

    def update_funding_rates(self, client) -> None:
        """
        Fetch and cache current funding rates for all symbols.
        Call this every 5-15 minutes.
        """
        # TODO: Implement Pacifica API call
        # symbols = ["BTC", "ETH", "SOL", ...]
        # for symbol in symbols:
        #     rate_data = client.get_funding_rate(symbol)
        #     self.funding_cache[symbol] = {
        #         "current_rate": rate_data["rate"],
        #         "next_payment": rate_data["next_funding_time"],
        #         "updated_at": datetime.utcnow()
        #     }
        pass

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
        # Positive rate = longs pay shorts → we want to be SHORT perp
        # Negative rate = shorts pay longs → we want to be LONG perp
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

        # Create entry signal
        signal = Signal(
            strategy=self.strategy_type,
            asset=symbol,
            side=opportunity["perp_side"],
            entry_price=current_price,
            stop_loss=None,  # Delta-neutral doesn't use traditional stops
            take_profit=None,
            confidence=opportunity["confidence"],
            quality=TradeQuality.STANDARD,
            timeframe="funding",
            market_state="funding_arb",
            notes=(
                f"Funding arb: rate={opportunity['current_rate']:.4%}/hr, "
                f"APY={opportunity['annualized_yield']:.1%}, "
                f"side={opportunity['arb_side']}"
            ),
            # Custom fields for funding arb
            metadata={
                "arb_type": "funding_rate",
                "funding_rate": opportunity["current_rate"],
                "arb_side": opportunity["arb_side"],
                "requires_hedge": True,  # Signal to execution layer
            }
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
        if existing["side"] != current_opportunity["arb_side"]:
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
        close_side = OrderSide.BUY if existing.get("perp_side") == OrderSide.SELL else OrderSide.SELL

        return Signal(
            strategy=self.strategy_type,
            asset=symbol,
            side=close_side,
            entry_price=current_price,
            stop_loss=None,
            take_profit=None,
            confidence=0.90,
            quality=TradeQuality.STANDARD,
            timeframe="funding",
            market_state="funding_arb_close",
            notes="Closing funding arb position",
            metadata={
                "arb_type": "funding_rate_close",
                "requires_hedge": True,
            }
        )

    def get_active_positions(self) -> Dict[str, Dict]:
        """Return currently active arb positions."""
        return self.active_positions.copy()

    def calculate_total_yield(self) -> float:
        """Calculate total accumulated yield from funding payments."""
        # TODO: Track funding payments received
        return 0.0
```

---

## Integration Points

### 1. StrategyManager Registration

In `strategy_manager.py` `__init__`:
```python
if config.enable_funding_arb:
    from .strategies.funding_arb import FundingArbStrategy
    self.strategies["FundingArb"] = FundingArbStrategy(
        min_funding_rate=config.funding_arb_min_rate,
        max_allocation_pct=config.funding_arb_max_allocation_pct,
    )
    logger.info("Funding Arb strategy enabled")
```

### 2. Regime Mapping

In `market_regime.py`, add to ALL regimes (passive strategy):
```python
# FundingArb runs in ALL regimes as passive overlay
REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_STRONG: ["MACrossover", "FundingArb"],
    MarketRegime.RANGING_VOLATILE: ["GridTrading", "FundingArb"],
    MarketRegime.RANGING_CALM: ["MeanReversion", "FundingArb"],
    MarketRegime.INDECISIVE: ["FundingArb"],  # Only passive strategies
}
```

### 3. Execution Layer Handling

The ExecutionLayer needs to handle `requires_hedge: True` signals specially:
- Open perp position
- Simultaneously open offsetting spot position (or track as stablecoin hedge)
- Track combined P&L

### 4. Database Tracking

Add table for funding payments:
```sql
CREATE TABLE IF NOT EXISTS funding_payments (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    payment_time TIMESTAMP NOT NULL,
    rate REAL NOT NULL,
    amount REAL NOT NULL,
    position_size REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Testing Checklist

- [ ] Verify Pacifica funding rate API endpoint works
- [ ] Test funding rate parsing and caching
- [ ] Verify signal generation when rate > threshold
- [ ] Test position closing when rate flips
- [ ] Verify delta-neutral execution (both legs)
- [ ] Test P&L tracking across funding payments
- [ ] Dry-run on testnet for 24+ hours
- [ ] Monitor actual funding payment collection

---

## Risk Considerations

1. **Basis Risk**: Spot-perp spread can widen, causing temporary losses
2. **Execution Risk**: Need to enter both legs atomically
3. **Funding Flip**: Rates can change direction within minutes during volatility
4. **Capital Efficiency**: Ties up capital in hedged positions

## Expected Performance

- **Win Rate**: 80-90% (funding payments are predictable)
- **Monthly Return**: 1-3% (conservative)
- **Max Drawdown**: 2-5% (from basis risk)
- **Sharpe Ratio**: 2.0+ (low volatility returns)

---

## Commands to Run After Implementation

```bash
# Add to .env
echo "ENABLE_FUNDING_ARB=true" >> .env
echo "FUNDING_ARB_MIN_RATE=0.0001" >> .env

# Test in dry-run mode
python -c "
from trading_bot_v2.strategies.funding_arb import FundingArbStrategy
strategy = FundingArbStrategy()
print('Strategy initialized:', strategy.strategy_type)
"
```
