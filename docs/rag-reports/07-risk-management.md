# Risk Management

<!--
RAG Metadata:
- Category: Risk
- Tags: risk-manager, kelly-criterion, position-sizing, exposure-limits, circuit-breaker
- Related: 01-core-trading-logic, 02-trading-strategies, 05-data-management
-->

## Overview

Risk management is centralized in the **RiskManager** class, which is the single source of truth for all risk calculations.

Key components:
1. **RiskManager** - Centralized risk validation
2. **KellyPositionSizer** - Kelly criterion position sizing
3. **Portfolio Risk Limits** - Exposure and loss limits

---

## RiskManager Class

**Location**: `trading_bot_v2/risk_manager.py`

### Purpose
Single source of truth for all risk calculations and validations.

### Key Principle

```python
"""
RISK MANAGEMENT WARNING
===============================
This is the ONLY place for risk calculations.
All other classes must delegate here.
===============================
"""
```

### Risk Profiles

```python
class RiskProfile(Enum):
    LOW = "low"      # 50% of base risk
    MEDIUM = "medium" # 100% of base risk
    HIGH = "high"    # 150% of base risk
```

### Configuration

```python
class RiskManager:
    def __init__(
        self,
        max_portfolio_risk_pct: float = 0.05,      # 5% max per trade
        max_portfolio_exposure_pct: float = 0.15,  # 15% max total exposure
        max_margin_utilization_pct: float = 0.75,  # 75% max margin usage
        maintenance_margin_buffer_pct: float = 0.15, # 15% buffer above MM
        risk_profiles: Optional[Dict[str, float]] = None,
        client: Optional["PacificaClient"] = None,
    ):
        # Default risk multipliers by profile
        self.risk_profiles = risk_profiles or {
            RiskProfile.LOW.value: 0.5,
            RiskProfile.MEDIUM.value: 1.0,
            RiskProfile.HIGH.value: 1.5,
        }
```

### Key Parameters (Jan 2026 - Updated)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `max_portfolio_risk_pct` | 5% | Maximum risk per trade |
| `max_portfolio_exposure_pct` | 15% | Maximum total exposure |
| `max_margin_utilization_pct` | 75% | Maximum margin utilization |
| `maintenance_margin_buffer_pct` | 15% | Buffer above maintenance margin |

---

## Position Sizing

### Core Method

```python
def get_position_size(
    self, 
    signal: Signal, 
    account_balance: float, 
    current_exposure: float
) -> float:
    """
    Calculate position quantity based on signal and risk profile.
    
    Formula:
        1. Base Risk = Account Balance × 5%
        2. Adjusted Risk = Base Risk × Profile Multiplier
        3. Position Size = Adjusted Risk / Stop Distance %
    
    Args:
        signal: Signal with entry_price, stop_loss, risk_profile
        account_balance: Current account balance
        current_exposure: Current portfolio exposure
        
    Returns:
        Position quantity (contracts/shares)
    """
```

### Calculation Steps

```
Position Sizing Flow:
─────────────────────

1. Validate Entry Price
   └─► If invalid → Return minimum safe quantity (1.0)

2. Calculate Base Risk Amount
   └─► base_risk = account_balance × 5%

3. Apply Risk Profile Multiplier
   └─► adjusted_risk = base_risk × multiplier
       (LOW: 0.5x | MEDIUM: 1.0x | HIGH: 1.5x)

4. Calculate Stop Distance
   └─► stop_distance = |entry_price - stop_loss| / entry_price

5. Calculate Position Size
   └─► notional_size = adjusted_risk / stop_distance

6. Apply Exposure Limits
   └─► If exposure + notional > max_exposure → Reduce size

7. Return Final Quantity
   └─► quantity = notional_size / entry_price
```

### Strategy Risk Profiles

| Strategy | Risk Profile | Multiplier |
|----------|--------------|------------|
| Mean Reversion | MEDIUM | 1.0x |
| MA Crossover | MEDIUM | 1.0x |
| Grid Trading | LOW | 0.5x |
| Liquidation Capture | HIGH | 1.5x |
| VWAP Scalping | LOW | 0.5x |
| Funding Arbitrage | LOW | 0.5x |
| Momentum Scalping | MEDIUM | 1.0x |
| Order Book Imbalance | MEDIUM | 1.0x |

---

## Exposure Limits

### Validation Method

```python
def validate_exposure(
    self,
    current_exposure: float,
    additional_exposure: float,
    account_balance: float
) -> Tuple[bool, str]:
    """
    Validate total exposure against limits.
    
    Args:
        current_exposure: Current portfolio exposure
        additional_exposure: Proposed additional exposure
        account_balance: Account balance for percentage calculation
        
    Returns:
        Tuple of (is_valid, reason)
    """
```

### Exposure Tracking

```python
# Grid exposure tracking per symbol
self.grid_exposure: Dict[str, float] = {}

# Migrated position tracking
self.migrated_positions: Dict[str, list] = {}
```

---

## Margin Safety

### Margin Data Cache

```python
# Margin data cache for reducing API calls
self._margin_data_cache: Optional[MarginData] = None
self._margin_cache_timestamp: float = 0.0
self._margin_cache_ttl: int = 30  # 30 seconds cache TTL
```

### Margin Validation

```python
async def validate_margin_safety(
    self,
    additional_margin_required: float
) -> Tuple[bool, str]:
    """
    Validate margin safety before opening position.
    
    Checks:
    1. Current margin utilization < 75%
    2. After trade utilization < 75%
    3. Maintenance margin buffer > 15%
    
    Args:
        additional_margin_required: Margin needed for new position
        
    Returns:
        Tuple of (is_safe, reason)
    """
```

---

## Kelly Criterion Position Sizing

**Location**: `trading_bot_v2/kelly_position_sizer.py`

### Purpose
Optimal position sizing based on historical win rate and risk/reward.

### Kelly Formula

```
Kelly % = W - [(1 - W) / R]

Where:
- W = Win probability (historical win rate)
- R = Win/loss ratio (average win / average loss)
```

### Implementation

```python
class KellyPositionSizer:
    """Kelly criterion position sizing with safety constraints."""
    
    def __init__(
        self,
        fraction: float = 0.5,       # Fractional Kelly (50%)
        max_position_pct: float = 0.10,  # Max 10% of portfolio
        min_trades: int = 50,        # Minimum trades for calculation
    ):
        self.fraction = fraction
        self.max_position_pct = max_position_pct
        self.min_trades = min_trades
    
    def calculate_kelly_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        account_balance: float,
    ) -> float:
        """
        Calculate Kelly-optimal position size.
        
        Args:
            win_rate: Historical win rate (0-1)
            avg_win: Average winning trade amount
            avg_loss: Average losing trade amount
            account_balance: Current account balance
            
        Returns:
            Optimal position size as fraction of portfolio
        """
        if win_rate <= 0 or avg_loss <= 0:
            return self.fallback_pct
        
        # Calculate R (win/loss ratio)
        r = avg_win / avg_loss
        
        # Kelly formula
        kelly = win_rate - ((1 - win_rate) / r)
        
        # Apply fractional Kelly
        adjusted_kelly = kelly * self.fraction
        
        # Cap at maximum
        return min(adjusted_kelly, self.max_position_pct)
```

### Fallback Strategy

```python
# Strategy-specific fallback percentages
FALLBACK_PCT = {
    'MeanReversion': 0.03,      # 3%
    'MACrossover': 0.04,        # 4%
    'GridTrading': 0.02,        # 2%
    'LiquidationCapture': 0.02, # 2%
    'VWAPScalping': 0.02,       # 2%
    'FundingArb': 0.01,         # 1%
    'MomentumScalping': 0.03,   # 3%
    'OrderBookImbalance': 0.02, # 2%
}
```

---

## Circuit Breaker Protection

### Bot-Level Circuit Breaker

```python
# In TradingBot class
self._circuit_breaker_triggered = False
self._circuit_breaker_loss_pct = 0.10  # 10% portfolio loss

def check_circuit_breaker(self, portfolio_pnl: float, 
                          initial_balance: float) -> bool:
    """
    Check if circuit breaker should trigger.
    
    Triggers when:
    - Portfolio loss > 10% (configurable)
    - Warning at 80% of threshold
    
    Returns:
        True if circuit breaker triggered
    """
```

### Grid Emergency Stop

```python
# Grid trading emergency stop conditions
GRID_EMERGENCY_STOPS = {
    'max_positions_per_symbol': 10,
    'adx_emergency_threshold': 25.0,
    'portfolio_emergency_pct': 0.05,  # 5%
}

# Trigger conditions:
# 1. Positions per symbol > 10
# 2. ADX rises above 25 (trend emerging)
# 3. Portfolio loss > 5%
```

---

## Risk Validation Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Risk Validation Flow                             │
└─────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────┐
                    │  Signal Generated │
                    └────────┬─────────┘
                             │
                             ▼
               ┌─────────────────────────┐
               │ 1. Validate Entry Price │
               └────────────┬────────────┘
                            │
                            ▼
               ┌─────────────────────────┐
               │ 2. Calculate Position   │
               │    Size (Kelly/Risk %)  │
               └────────────┬────────────┘
                            │
                            ▼
               ┌─────────────────────────┐
               │ 3. Check Exposure Limit │
               │    (< 15% total)        │
               └────────────┬────────────┘
                            │
                            ▼
               ┌─────────────────────────┐
               │ 4. Validate Margin      │
               │    Safety (< 75%)       │
               └────────────┬────────────┘
                            │
                            ▼
               ┌─────────────────────────┐
               │ 5. Check Circuit        │
               │    Breaker Status       │
               └────────────┬────────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
        ┌───────────┐              ┌───────────┐
        │  APPROVED │              │ REJECTED  │
        │  Execute  │              │  Log &    │
        │  Trade    │              │  Skip     │
        └───────────┘              └───────────┘
```

---

## Authoritative Approval System

```python
# Phase 1 Enhancement - All capital requests must be approved
self._approval_required = True
self._pending_approvals: Dict[str, Dict] = {}

async def request_capital(
    self,
    request_id: str,
    symbol: str,
    strategy: str,
    requested_amount: float,
) -> Tuple[bool, float]:
    """
    Request capital allocation for trade.
    
    All requests go through centralized validation.
    
    Args:
        request_id: Unique request identifier
        symbol: Trading symbol
        strategy: Strategy making request
        requested_amount: Amount requested
        
    Returns:
        Tuple of (approved, actual_amount)
    """
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `risk_manager.py` | 1062 | Centralized risk management |
| `kelly_position_sizer.py` | ~200 | Kelly criterion implementation |

---

## Related Reports

- [01-core-trading-logic.md](./01-core-trading-logic.md) - Signal execution
- [02-trading-strategies.md](./02-trading-strategies.md) - Strategy risk profiles
- [05-data-management.md](./05-data-management.md) - Position tracking
