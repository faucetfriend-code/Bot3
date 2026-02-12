# Trading Bot v2 - Strategy Integration Guide

## Overview

This guide provides comprehensive documentation for integrating existing and new trading strategies into the Trading Bot v2 system. It covers the strategy architecture, implementation patterns, and step-by-step integration procedures.

---

## Strategy Architecture

### Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Strategy Manager                          │
│                   (Orchestration Layer)                      │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  Strategy   │  │   Signal     │  │    Position      │   │
│  │  Registry   │→ │  Processor   │→ │    Manager       │   │
│  └─────────────┘  └──────────────┘  └──────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                      Strategy Implementations                │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────┐     │
│  │Mean Reversion│ │ MA Crossover │ │   Grid Trading   │     │
│  └─────────────┘ └──────────────┘ └──────────────────┘     │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────┐     │
│  │Liquidation  │ │ VWAP Scalping│ │  Funding Arb     │     │
│  │ Capture     │ │              │ │                  │     │
│  └─────────────┘ └──────────────┘ └──────────────────┘     │
│  ┌─────────────┐ ┌──────────────┐                          │
│  │   OrderBook │ │   Momentum   │                          │
│  │  Imbalance  │ │   Scalping   │                          │
│  └─────────────┘ └──────────────┘                          │
└─────────────────────────────────────────────────────────────┘
```

### Signal Flow

```
Market Data → Strategy → Signal Validation → Risk Check → Execution
     │            │              │               │            │
     ↓            ↓              ↓               ↓            ↓
  WebSocket   Indicator    Confidence      Position    Order
  Price Feed  Analysis      Score          Sizing     Placement
```

---

## Built-in Strategies

### 1. Mean Reversion

**Description**: Identifies overbought/oversold conditions using RSI and enters positions expecting price reversion.

**Parameters**:
```python
{
    "rsi_period": 14,
    "oversold": 35,        # RSI threshold for long entry
    "overbought": 65,      # RSI threshold for short entry
    "confidence_threshold": 0.45
}
```

**Market Regimes**: Best in RANGING_CALM, RANGING_VOLATILE  
**Risk Level**: Medium  
**Timeframes**: 15m, 1h, 4h

**Integration Example**:
```python
from strategies.mean_reversion import MeanReversionStrategy

strategy = MeanReversionStrategy(
    rsi_period=14,
    oversold=35,
    overbought=65,
    confidence_threshold=0.45
)

signal = await strategy.generate_signal(market_data)
```

### 2. Moving Average Crossover

**Description**: Uses fast and slow moving average crossovers with pullback confirmation for trend following.

**Parameters**:
```python
{
    "fast_ma": 50,         # Fast MA period
    "slow_ma": 200,        # Slow MA period
    "ma_type": "ema",      # "sma" or "ema"
    "pullback_confirm": True,
    "confidence_threshold": 0.65
}
```

**Market Regimes**: Best in TRENDING_STRONG, TRENDING_MODERATE  
**Risk Level**: Medium-High  
**Timeframes**: 1h, 4h, 1d

### 3. Grid Trading

**Description**: Places multiple buy/sell orders at predefined price levels to profit from ranging markets.

**Parameters**:
```python
{
    "grid_levels": 8,
    "atr_multiplier": 0.4,  # Grid spacing based on ATR
    "max_grids_per_symbol": 3,
    "emergency_stop_adx": 25.0
}
```

**Market Regimes**: Best in RANGING_CALM, RANGING_VOLATILE  
**Risk Level**: Low-Medium  
**Timeframes**: Continuous

### 4. Liquidation Capture

**Description**: Detects liquidation cascades and enters contrarian positions to capture mean reversion.

**Parameters**:
```python
{
    "price_move_threshold": 0.025,  # 2.5% price move
    "volume_spike_threshold": 2.5,  # 2.5x volume spike
    "cooldown_minutes": 30,
    "confidence_threshold": 0.70
}
```

**Market Regimes**: Works in all regimes  
**Risk Level**: High  
**Timeframes**: 1m, 5m

### 5. VWAP Scalping

**Description**: Enters positions when price deviates significantly from VWAP, expecting reversion.

**Parameters**:
```python
{
    "vwap_deviation_threshold": 0.005,  # 0.5% deviation
    "min_volume_percentile": 75,
    "max_hold_time": 300,  # 5 minutes
    "confidence_threshold": 0.55
}
```

**Market Regimes**: Best in TRENDING_MODERATE  
**Risk Level**: High  
**Timeframes**: 1m, 5m

### 6. Funding Rate Arbitrage

**Description**: Exploits funding rate differentials across perpetual futures markets.

**Parameters**:
```python
{
    "min_funding_rate": 0.001,      # 0.1% minimum
    "max_hold_hours": 8,
    "funding_check_interval": 3600,  # 1 hour
    "confidence_threshold": 0.60
}
```

**Market Regimes**: Best in RANGING_CALM  
**Risk Level**: Low  
**Timeframes**: 1h (funding intervals)

### 7. Momentum Scalping

**Description**: Captures short-term price momentum using breakouts and volume confirmation.

**Parameters**:
```python
{
    "lookback_periods": 20,
    "breakout_threshold": 0.008,  # 0.8% breakout
    "volume_confirm": True,
    "confidence_threshold": 0.50
}
```

**Market Regimes**: Best in TRENDING_STRONG, TRENDING_MODERATE  
**Risk Level**: High  
**Timeframes**: 1m, 5m, 15m

### 8. Order Book Imbalance

**Description**: Uses bid/ask imbalance in the order book to predict short-term price movements.

**Parameters**:
```python
{
    "imbalance_threshold": 0.6,   # 60% imbalance
    "depth_levels": 10,
    "min_orderbook_size": 100000,  # $100k
    "confidence_threshold": 0.65
}
```

**Market Regimes**: Works in all regimes  
**Risk Level**: Medium  
**Timeframes**: Real-time (WebSocket)

---

## Creating a Custom Strategy

### Step 1: Implement Strategy Interface

```python
# strategies/my_custom_strategy.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional
from datetime import datetime

@dataclass
class Signal:
    """Trading signal definition."""
    symbol: str
    side: str  # "buy" or "sell"
    confidence: float  # 0.0 to 1.0
    strategy: str
    timestamp: datetime
    metadata: Dict[str, Any]
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

class StrategyInterface(ABC):
    """Base interface for all trading strategies."""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.enabled = True
    
    @abstractmethod
    async def generate_signal(
        self,
        symbol: str,
        market_data: Dict[str, Any]
    ) -> Optional[Signal]:
        """
        Generate trading signal based on market data.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            market_data: Dictionary containing OHLCV data, indicators, etc.
            
        Returns:
            Signal object or None if no signal
        """
        pass
    
    @abstractmethod
    def validate_market_regime(self, regime: str) -> bool:
        """
        Check if strategy is suitable for current market regime.
        
        Args:
            regime: Current market regime from MarketRegimeDetector
            
        Returns:
            True if strategy should operate in this regime
        """
        pass
    
    def get_required_indicators(self) -> list:
        """
        Return list of required technical indicators.
        
        Returns:
            List of indicator names
        """
        return []
```

### Step 2: Implement Strategy Logic

```python
# strategies/my_custom_strategy.py (continued)
import numpy as np
from indicators import calculate_rsi, calculate_bollinger_bands

class MyCustomStrategy(StrategyInterface):
    """
    Custom strategy combining RSI and Bollinger Bands.
    
    Enters long when RSI is oversold AND price touches lower Bollinger Band.
    Enters short when RSI is overbought AND price touches upper Bollinger Band.
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        default_config = {
            'rsi_period': 14,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'bb_period': 20,
            'bb_std': 2.0,
            'confidence_threshold': 0.60
        }
        default_config.update(config or {})
        super().__init__("my_custom", default_config)
    
    async def generate_signal(
        self,
        symbol: str,
        market_data: Dict[str, Any]
    ) -> Optional[Signal]:
        """Generate signal using RSI + Bollinger Bands."""
        
        # Extract price data
        close_prices = np.array(market_data['close'])
        high_prices = np.array(market_data['high'])
        low_prices = np.array(market_data['low'])
        
        # Calculate indicators
        rsi = calculate_rsi(close_prices, self.config['rsi_period'])
        bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(
            close_prices,
            self.config['bb_period'],
            self.config['bb_std']
        )
        
        current_price = close_prices[-1]
        current_rsi = rsi[-1]
        
        # Generate signal
        if current_rsi < self.config['rsi_oversold'] and current_price <= bb_lower[-1]:
            # Oversold + Lower BB touch = Long signal
            confidence = self._calculate_confidence(
                current_rsi, 
                self.config['rsi_oversold'],
                current_price,
                bb_lower[-1]
            )
            
            if confidence >= self.config['confidence_threshold']:
                return Signal(
                    symbol=symbol,
                    side="buy",
                    confidence=confidence,
                    strategy=self.name,
                    timestamp=datetime.now(),
                    metadata={
                        'rsi': current_rsi,
                        'bb_lower': bb_lower[-1],
                        'entry_reason': 'oversold_bb_touch'
                    },
                    entry_price=current_price,
                    stop_loss=bb_lower[-1] * 0.99,  # 1% below BB
                    take_profit=bb_middle[-1]  # Middle BB as target
                )
        
        elif current_rsi > self.config['rsi_overbought'] and current_price >= bb_upper[-1]:
            # Overbought + Upper BB touch = Short signal
            confidence = self._calculate_confidence(
                current_rsi,
                self.config['rsi_overbought'],
                current_price,
                bb_upper[-1],
                inverse=True
            )
            
            if confidence >= self.config['confidence_threshold']:
                return Signal(
                    symbol=symbol,
                    side="sell",
                    confidence=confidence,
                    strategy=self.name,
                    timestamp=datetime.now(),
                    metadata={
                        'rsi': current_rsi,
                        'bb_upper': bb_upper[-1],
                        'entry_reason': 'overbought_bb_touch'
                    },
                    entry_price=current_price,
                    stop_loss=bb_upper[-1] * 1.01,  # 1% above BB
                    take_profit=bb_middle[-1]  # Middle BB as target
                )
        
        return None
    
    def validate_market_regime(self, regime: str) -> bool:
        """Strategy works best in ranging markets."""
        suitable_regimes = [
            'RANGING_CALM',
            'RANGING_VOLATILE',
            'INDECISIVE'
        ]
        return regime in suitable_regimes
    
    def get_required_indicators(self) -> list:
        """Return required indicators."""
        return ['rsi', 'bollinger_bands']
    
    def _calculate_confidence(
        self,
        rsi: float,
        threshold: float,
        price: float,
        bb_level: float,
        inverse: bool = False
    ) -> float:
        """Calculate signal confidence score."""
        # RSI divergence from threshold
        rsi_divergence = abs(rsi - threshold) / threshold
        
        # Price distance from BB
        price_distance = abs(price - bb_level) / price
        
        # Combine factors
        confidence = 0.5 + (rsi_divergence * 0.3) + (price_distance * 0.2)
        
        return min(confidence, 0.95)  # Cap at 95%
```

### Step 3: Register Strategy

```python
# strategy_manager.py
from strategies.my_custom_strategy import MyCustomStrategy

class StrategyManager:
    def __init__(self, config: Dict[str, Any] = None):
        self.strategies: Dict[str, StrategyInterface] = {}
        self._register_default_strategies()
        self._register_custom_strategies()
    
    def _register_custom_strategies(self):
        """Register custom strategies."""
        # Register your custom strategy
        self.register_strategy(
            "my_custom",
            MyCustomStrategy(),
            enabled=True
        )
```

### Step 4: Enable via Configuration

```bash
# .env
ENABLE_MY_CUSTOM=true
MY_CUSTOM_RSI_PERIOD=14
MY_CUSTOM_RSI_OVERSOLD=30
MY_CUSTOM_RSI_OVERBOUGHT=70
MY_CUSTOM_CONFIDENCE=0.60
```

---

## Strategy Configuration

### Environment-Based Configuration

```python
# config.py
class Config:
    def __init__(self):
        # Strategy enable flags
        self.enable_mean_reversion = os.getenv('ENABLE_MEAN_REVERSION', 'true').lower() == 'true'
        self.enable_ma_crossover = os.getenv('ENABLE_MA_CROSSOVER', 'true').lower() == 'true'
        self.enable_grid_trading = os.getenv('ENABLE_GRID_TRADING', 'true').lower() == 'true'
        self.enable_my_custom = os.getenv('ENABLE_MY_CUSTOM', 'false').lower() == 'true'
        
        # Strategy-specific parameters
        self.mean_reversion_config = {
            'rsi_period': int(os.getenv('MR_RSI_PERIOD', '14')),
            'oversold': int(os.getenv('MR_OVERSOLD', '35')),
            'overbought': int(os.getenv('MR_OVERBOUGHT', '65')),
            'confidence_threshold': float(os.getenv('MR_CONFIDENCE', '0.45'))
        }
        
        self.my_custom_config = {
            'rsi_period': int(os.getenv('MY_CUSTOM_RSI_PERIOD', '14')),
            'rsi_oversold': int(os.getenv('MY_CUSTOM_RSI_OVERSOLD', '30')),
            'rsi_overbought': int(os.getenv('MY_CUSTOM_RSI_OVERBOUGHT', '70')),
            'confidence_threshold': float(os.getenv('MY_CUSTOM_CONFIDENCE', '0.60'))
        }
```

### Dynamic Strategy Configuration

```python
# Update strategy parameters at runtime
@app.post("/api/strategies/{name}/config")
async def update_strategy_config(name: str, config: Dict[str, Any]):
    """Update strategy configuration dynamically."""
    strategy_manager = get_strategy_manager()
    
    if name not in strategy_manager.strategies:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    strategy = strategy_manager.strategies[name]
    
    # Validate config
    valid_keys = strategy.config.keys()
    for key in config:
        if key not in valid_keys:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid config key: {key}"
            )
    
    # Update config
    strategy.config.update(config)
    
    return {
        "success": True,
        "message": f"Strategy {name} configuration updated",
        "config": strategy.config
    }
```

---

## Market Regime Integration

### Regime Detection Integration

```python
# market_regime.py
from enum import Enum

class MarketRegime(Enum):
    TRENDING_STRONG = "trending_strong"      # ADX > 30
    TRENDING_MODERATE = "trending_moderate"  # ADX 25-30
    RANGING_VOLATILE = "ranging_volatile"    # ADX <= 25, high volatility
    RANGING_CALM = "ranging_calm"            # ADX <= 25, low volatility
    INDECISIVE = "indecisive"                # Transitional

class MarketRegimeDetector:
    """Detect current market regime for strategy filtering."""
    
    def detect_regime(self, market_data: Dict[str, Any]) -> MarketRegime:
        """Detect market regime from price data."""
        # Calculate ADX
        adx = calculate_adx(market_data['high'], market_data['low'], market_data['close'])
        current_adx = adx[-1]
        
        # Calculate volatility
        returns = np.diff(np.log(market_data['close']))
        volatility = np.std(returns) * np.sqrt(365)
        
        # Determine regime
        if current_adx > 30:
            return MarketRegime.TRENDING_STRONG
        elif current_adx > 25:
            return MarketRegime.TRENDING_MODERATE
        elif volatility > 0.65:
            return MarketRegime.RANGING_VOLATILE
        elif current_adx < 20:
            return MarketRegime.RANGING_CALM
        else:
            return MarketRegime.INDECISIVE
```

### Strategy-Regime Mapping

```python
# strategy_regime_config.py
STRATEGY_REGIME_MAP = {
    'mean_reversion': [
        MarketRegime.RANGING_CALM,
        MarketRegime.RANGING_VOLATILE,
        MarketRegime.INDECISIVE
    ],
    'ma_crossover': [
        MarketRegime.TRENDING_STRONG,
        MarketRegime.TRENDING_MODERATE
    ],
    'grid_trading': [
        MarketRegime.RANGING_CALM,
        MarketRegime.RANGING_VOLATILE
    ],
    'liquidation_capture': [
        MarketRegime.TRENDING_STRONG,
        MarketRegime.RANGING_VOLATILE,
        MarketRegime.INDECISIVE
    ],
    'vwap_scalping': [
        MarketRegime.TRENDING_MODERATE
    ],
    'momentum_scalping': [
        MarketRegime.TRENDING_STRONG,
        MarketRegime.TRENDING_MODERATE
    ],
    'my_custom': [
        MarketRegime.RANGING_CALM,
        MarketRegime.INDECISIVE
    ]
}
```

---

## Risk Management Integration

### Position Sizing

```python
# risk_manager.py
class RiskManager:
    """Manages position sizing and risk."""
    
    def calculate_position_size(
        self,
        signal: Signal,
        account_balance: float,
        current_positions: List[Dict]
    ) -> float:
        """Calculate position size based on signal and risk parameters."""
        
        # Base sizing on confidence
        confidence_multiplier = signal.confidence
        
        # Apply Kelly Criterion (fractional)
        kelly_fraction = 0.5
        
        # Calculate max risk per trade
        max_risk = account_balance * 0.02  # 2% max risk
        
        # Adjust for existing exposure
        current_exposure = sum(
            pos['quantity'] * pos['entry_price'] 
            for pos in current_positions
        )
        max_exposure = account_balance * 0.5  # 50% max exposure
        available_exposure = max_exposure - current_exposure
        
        # Calculate position size
        if signal.stop_loss:
            risk_per_unit = abs(signal.entry_price - signal.stop_loss)
            position_size = (max_risk * confidence_multiplier * kelly_fraction) / risk_per_unit
        else:
            # Default sizing without stop loss
            position_size = max_risk * confidence_multiplier * 0.1
        
        # Cap at available exposure
        position_value = position_size * signal.entry_price
        if position_value > available_exposure:
            position_size = available_exposure / signal.entry_price
        
        return position_size
```

### Signal Validation

```python
# signal_validator.py
class SignalValidator:
    """Validates trading signals before execution."""
    
    def __init__(self, risk_manager: RiskManager):
        self.risk_manager = risk_manager
        self.min_confidence = 0.45
        self.max_daily_trades = 50
    
    async def validate_signal(
        self,
        signal: Signal,
        market_regime: MarketRegime,
        account_state: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Validate signal before execution.
        
        Returns:
            (is_valid, reason)
        """
        # Check confidence threshold
        if signal.confidence < self.min_confidence:
            return False, f"Confidence {signal.confidence:.2f} below threshold {self.min_confidence}"
        
        # Check market regime compatibility
        suitable_regimes = STRATEGY_REGIME_MAP.get(signal.strategy, [])
        if market_regime not in suitable_regimes:
            return False, f"Strategy {signal.strategy} not suitable for {market_regime.value}"
        
        # Check daily trade limit
        daily_trades = account_state.get('daily_trades', 0)
        if daily_trades >= self.max_daily_trades:
            return False, f"Daily trade limit reached: {daily_trades}/{self.max_daily_trades}"
        
        # Check balance sufficiency
        balance = account_state.get('balance', 0)
        if balance < 100:  # Minimum balance
            return False, f"Insufficient balance: ${balance:.2f}"
        
        return True, "Signal validated"
```

---

## Testing Strategies

### Unit Tests

```python
# tests/test_my_custom_strategy.py
import pytest
import numpy as np
from strategies.my_custom_strategy import MyCustomStrategy

@pytest.fixture
def strategy():
    return MyCustomStrategy()

@pytest.fixture
def market_data():
    """Generate sample market data."""
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    return {
        'open': prices[:-1],
        'high': prices[1:] + np.abs(np.random.randn(99)) * 0.5,
        'low': prices[1:] - np.abs(np.random.randn(99)) * 0.5,
        'close': prices[1:],
        'volume': np.random.randint(1000, 10000, 99)
    }

@pytest.mark.asyncio
async def test_generate_signal_oversold(strategy, market_data):
    """Test signal generation in oversold condition."""
    # Manually create oversold condition
    market_data['close'][-14:] = np.linspace(90, 80, 14)  # Downtrend
    
    signal = await strategy.generate_signal("BTC/USD", market_data)
    
    if signal:
        assert signal.side == "buy"
        assert signal.confidence >= strategy.config['confidence_threshold']
        assert signal.strategy == "my_custom"

@pytest.mark.asyncio
async def test_validate_market_regime(strategy):
    """Test regime validation."""
    from market_regime import MarketRegime
    
    assert strategy.validate_market_regime(MarketRegime.RANGING_CALM) is True
    assert strategy.validate_market_regime(MarketRegime.TRENDING_STRONG) is False
```

### Backtesting

```python
# backtest.py
import pandas as pd
from typing import List, Dict

class StrategyBacktester:
    """Backtest strategies against historical data."""
    
    def __init__(self, strategy: StrategyInterface, initial_balance: float = 10000):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.trades: List[Dict] = []
    
    async def run_backtest(
        self,
        historical_data: pd.DataFrame,
        commission: float = 0.001
    ) -> Dict[str, Any]:
        """Run backtest on historical data."""
        balance = self.initial_balance
        position = None
        
        for i in range(50, len(historical_data)):
            # Get window of data
            window = historical_data.iloc[i-50:i]
            market_data = {
                'open': window['open'].values,
                'high': window['high'].values,
                'low': window['low'].values,
                'close': window['close'].values,
                'volume': window['volume'].values
            }
            
            current_price = historical_data.iloc[i]['close']
            
            # Generate signal
            signal = await self.strategy.generate_signal(
                "TEST/USD",
                market_data
            )
            
            # Execute signal
            if signal and not position:
                position = {
                    'side': signal.side,
                    'entry_price': current_price,
                    'size': balance * 0.1  # 10% position
                }
                balance -= position['size'] * commission
            
            # Check exit conditions
            elif position:
                pnl = 0
                if position['side'] == 'buy':
                    pnl = (current_price - position['entry_price']) / position['entry_price']
                else:
                    pnl = (position['entry_price'] - current_price) / position['entry_price']
                
                # Simple take profit / stop loss
                if pnl > 0.05 or pnl < -0.02:  # 5% TP, 2% SL
                    balance += position['size'] * (1 + pnl) * (1 - commission)
                    self.trades.append({
                        'entry': position['entry_price'],
                        'exit': current_price,
                        'pnl': pnl,
                        'side': position['side']
                    })
                    position = None
        
        # Calculate metrics
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t['pnl'] > 0)
        
        return {
            'total_return': (balance - self.initial_balance) / self.initial_balance,
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'win_rate': winning_trades / total_trades if total_trades > 0 else 0,
            'final_balance': balance
        }
```

---

## Performance Monitoring

### Strategy Performance Metrics

```python
# strategy_performance.py
from typing import Dict, List
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class StrategyPerformance:
    """Strategy performance metrics."""
    strategy_name: str
    total_signals: int
    executed_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    avg_trade_pnl: float
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown: float

class StrategyPerformanceTracker:
    """Track and analyze strategy performance."""
    
    def __init__(self):
        self.trades: Dict[str, List[Dict]] = {}
    
    def record_trade(self, strategy: str, trade: Dict):
        """Record a completed trade."""
        if strategy not in self.trades:
            self.trades[strategy] = []
        self.trades[strategy].append(trade)
    
    def get_performance(
        self,
        strategy: str,
        timeframe: str = "7d"
    ) -> StrategyPerformance:
        """Get performance metrics for strategy."""
        trades = self.trades.get(strategy, [])
        
        if not trades:
            return StrategyPerformance(
                strategy_name=strategy,
                total_signals=0,
                executed_trades=0,
                winning_trades=0,
                losing_trades=0,
                total_pnl=0.0,
                avg_trade_pnl=0.0,
                win_rate=0.0,
                profit_factor=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0
            )
        
        # Calculate metrics
        winning = [t for t in trades if t['pnl'] > 0]
        losing = [t for t in trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in trades)
        avg_pnl = total_pnl / len(trades)
        win_rate = len(winning) / len(trades) if trades else 0
        
        gross_profit = sum(t['pnl'] for t in winning)
        gross_loss = abs(sum(t['pnl'] for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        return StrategyPerformance(
            strategy_name=strategy,
            total_signals=len(trades),
            executed_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            total_pnl=total_pnl,
            avg_trade_pnl=avg_pnl,
            win_rate=win_rate,
            profit_factor=profit_factor,
            sharpe_ratio=0.0,  # Calculate from returns
            max_drawdown=0.0   # Calculate from equity curve
        )
```

---

**For additional information on strategy development, see [DEVELOPMENT_GUIDE.md](./DEVELOPMENT_GUIDE.md).**
