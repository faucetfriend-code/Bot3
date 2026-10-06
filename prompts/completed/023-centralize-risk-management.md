<objective>
Create a centralized RiskManager class to eliminate risk being defined in three different places. This is PRIORITY #3 from the trade management updates.
</objective>

<context>
Risk management is currently implemented inconsistently across three different locations:

1. Position sizing in _calculate_position_size()
2. Validation in _validate_position_size() 
3. Strategy confidence (implicit)

This causes inconsistent sizing, strategy overrides, and silent over-risking, especially with grid + liquidation strategies.

Reference: "G:\ai-workspace\Bot 3\research\trade managemet updates.txt" - Section 3

The bot needs a single source of truth for risk management.
</context>

<requirements>
1. Create a new RiskManager class that centralizes all risk logic
2. RiskManager provides position sizing for all strategies
3. Strategies declare their risk profile (LOW/MEDIUM/HIGH)
4. TradingBot uses RiskManager exclusively for all risk decisions
5. Remove direct risk math from _execute_signal() and other locations
</requirements>

<implementation>
Create new file: risk_manager.py

```python
from enum import Enum
from typing import Dict, Any, Optional
from loguru import logger

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
    """
    
    def __init__(self, 
                 max_portfolio_risk_pct: float = 0.02,  # 2% max per trade
                 max_portfolio_exposure_pct: float = 0.10,  # 10% max total exposure
                 risk_profiles: Optional[Dict[str, float]] = None):
        
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        self.max_portfolio_exposure_pct = max_portfolio_exposure_pct
        
        # Default risk multipliers by profile
        self.risk_profiles = risk_profiles or {
            RiskProfile.LOW.value: 0.5,      # 50% of base risk
            RiskProfile.MEDIUM.value: 1.0,   # 100% of base risk  
            RiskProfile.HIGH.value: 1.5,     # 150% of base risk
        }
        
        logger.info(f"RiskManager initialized: max_risk={max_portfolio_risk_pct*100}%, max_exposure={max_portfolio_exposure_pct*100}%")
    
    def get_position_size(self, 
                         signal: Any, 
                         account_balance: float, 
                         current_exposure: float) -> float:
        """
        Calculate position size based on signal and risk profile.
        
        Args:
            signal: Signal object with risk_profile attribute
            account_balance: Current account balance
            current_exposure: Current portfolio exposure
            
        Returns:
            Position size in base currency
        """
        
        # Get base risk amount
        base_risk_amount = account_balance * self.max_portfolio_risk_pct
        
        # Apply risk profile multiplier
        risk_profile = getattr(signal, 'risk_profile', RiskProfile.MEDIUM.value)
        risk_multiplier = self.risk_profiles.get(risk_profile, 1.0)
        adjusted_risk = base_risk_amount * risk_multiplier
        
        # Calculate position size based on stop loss distance
        if not signal.stop_loss:
            logger.warning(f"No stop loss for {signal.asset}, using minimum size")
            return adjusted_risk * 0.1  # Conservative minimum
        
        # Position size = Risk Amount / Stop Distance
        stop_distance_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price
        position_size = adjusted_risk / stop_distance_pct
        
        # Apply exposure limits
        max_exposure = account_balance * self.max_portfolio_exposure_pct
        available_exposure = max_exposure - current_exposure
        position_size = min(position_size, available_exposure)
        
        # Ensure positive size
        position_size = max(position_size, 0)
        
        logger.debug(f"Position size calc: risk={adjusted_risk:.2f}, stop_dist={stop_distance_pct:.4f}, size={position_size:.2f}")
        
        return position_size
    
    def validate_position_size(self, 
                              position_size: float, 
                              account_balance: float,
                              current_exposure: float) -> bool:
        """
        Validate position size against all risk limits.
        """
        
        # Check risk percentage
        risk_pct = (position_size / account_balance) if account_balance > 0 else 1.0
        if risk_pct > self.max_portfolio_risk_pct * 2:  # Allow some flexibility
            logger.warning(f"Position size exceeds risk limit: {risk_pct*100:.1f}% > {self.max_portfolio_risk_pct*100:.1f}%")
            return False
            
        # Check exposure limit
        total_exposure = current_exposure + position_size
        exposure_pct = (total_exposure / account_balance) if account_balance > 0 else 1.0
        if exposure_pct > self.max_portfolio_exposure_pct:
            logger.warning(f"Total exposure exceeds limit: {exposure_pct*100:.1f}% > {self.max_portfolio_exposure_pct*100:.1f}%")
            return False
            
        return True
    
    def get_strategy_risk_profile(self, strategy_type: str) -> str:
        """
        Get default risk profile for strategy type.
        """
        # Strategy-specific risk profiles
        strategy_profiles = {
            'mean_reversion': RiskProfile.MEDIUM.value,
            'ma_crossover': RiskProfile.MEDIUM.value, 
            'grid_trading': RiskProfile.LOW.value,      # Conservative grid
            'liquidation_capture': RiskProfile.HIGH.value, # Higher risk/reward
            'trend_following': RiskProfile.HIGH.value,
        }
        
        return strategy_profiles.get(strategy_type, RiskProfile.MEDIUM.value)
```

Update trading_bot.py to use RiskManager:

```python
from risk_manager import RiskManager, RiskProfile

class TradingBot:
    def __init__(self, ...):
        # ...
        self.risk_manager = RiskManager()
        
    def _calculate_position_size(self, signal):
        """Delegate to RiskManager."""
        account_balance = self._get_account_balance()
        current_exposure = self._get_current_exposure()
        
        # Set risk profile on signal if not set
        if not hasattr(signal, 'risk_profile'):
            signal.risk_profile = self.risk_manager.get_strategy_risk_profile(signal.strategy.value)
        
        return self.risk_manager.get_position_size(signal, account_balance, current_exposure)
    
    def _validate_position_size(self, position_size):
        """Delegate to RiskManager."""
        account_balance = self._get_account_balance()
        current_exposure = self._get_current_exposure()
        
        return self.risk_manager.validate_position_size(position_size, account_balance, current_exposure)
```
</implementation>

<output>
Create risk_manager.py with centralized RiskManager class:
- Single source of truth for all risk calculations
- Strategy-based risk profiles (LOW/MEDIUM/HIGH)
- Position sizing and validation methods

Update trading_bot.py to use RiskManager exclusively:
- Remove direct risk math from _calculate_position_size()
- Remove direct risk math from _validate_position_size()
- Delegate all risk decisions to RiskManager

Test that risk calculations are consistent across different strategies.
</output>

<verification>
After implementation:
1. Test position sizing for different strategies (should vary by risk profile)
2. Verify risk limits are enforced consistently
3. Check that grid/liquidation strategies use appropriate risk profiles
4. Ensure no direct risk math remains in trading_bot.py
</verification>

<success_criteria>
- Single RiskManager class handles all risk logic
- Strategies declare risk profiles (LOW/MEDIUM/HIGH)
- Position sizing is consistent across all strategies
- Risk validation is centralized
- No direct risk calculations in trading_bot.py
</success_criteria>