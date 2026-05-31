# Kronos Integration Guide for Trading Bot System

## Overview

This document details how to integrate **Kronos** - a time-series foundation model for financial markets - into the existing trading bot backtesting and live trading system. Kronos predicts future candlestick (K-line) data and can be used as a signal generator or confidence modifier for existing strategies.

**Source:** https://github.com/shiyu-coder/Kronos  
**Paper:** https://arxiv.org/abs/2508.02739  
**Live Demo:** https://shiyu-coder.github.io/Kronos-demo/

---

## 1. What is Kronos?

### 1.1 Core Concept
Kronos is a **decoder-only Transformer** foundation model specifically trained on financial K-line (candlestick) data from 45+ global exchanges. Unlike LLMs (ChatGPT/Claude) that predict words, Kronos predicts numerical price sequences - making it suitable for financial forecasting.

### 1.2 Architecture
```
Two-Stage Framework:
1. Tokenizer: Quantizes OHLCV data into hierarchical discrete tokens (Binary Spherical Quantization)
2. Predictor: Autoregressive Transformer that generates future K-line sequences
```

### 1.3 Available Models

| Model | Context Length | Parameters | GPU Memory | Recommended Use |
|-------|---------------|------------|------------|-----------------|
| **Kronos-mini** | 2048 | 4.1M | ~1GB | Quick testing, limited hardware |
| **Kronos-small** | 512 | 24.7M | ~2-4GB | **Recommended for trading bots** |
| Kronos-base | 512 | 102.3M | ~4-8GB | Higher accuracy, GPU recommended |
| Kronos-large | 512 | 499.2M | ~16GB+ | Research/development only |

---

## 2. Installation

### 2.1 Dependencies

Create a `requirements_kronos.txt`:

```
numpy>=1.20
pandas>=1.3
torch>=2.0.0
einops==0.8.1
huggingface_hub>=0.20.0
matplotlib>=3.7.0
tqdm>=4.65.0
safetensors>=0.4.0
```

### 2.2 Installation Steps

```bash
# Option A: Clone and install locally
cd Bot\ 3
git clone https://github.com/shiyu-coder/Kronos.git kronos_repo
pip install -r kronos_repo/requirements.txt

# Option B: Direct installation (after copying model code)
pip install torch einops huggingface_hub safetensors
```

### 2.3 Project Structure Integration

```
Bot 3/
├── trading_bot_v2/
│   ├── kronos/                    # NEW: Kronos integration
│   │   ├── __init__.py
│   │   ├── model/
│   │   │   ├── __init__.py
│   │   │   ├── kronos.py          # Kronos model classes
│   │   │   └── module.py          # Helper modules
│   │   ├── predictor.py           # KronosPredictor wrapper
│   │   ├── signal_generator.py    # Converts predictions to signals
│   │   └── data_preprocessor.py   # Prepares data for Kronos
│   ├── strategies/
│   │   └── kronos_enhanced.py     # Kronos-aware strategy
│   └── ...
```

---

## 3. Core API Reference

### 3.1 Basic Usage

```python
from model import Kronos, KronosTokenizer, KronosPredictor

# 1. Load model and tokenizer from HuggingFace
tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
model = Kronos.from_pretrained("NeoQuasar/Kronos-small")

# 2. Create predictor
predictor = KronosPredictor(model, tokenizer, max_context=512)

# 3. Prepare data
import pandas as pd

df = pd.read_csv("your_data.csv")
df['timestamps'] = pd.to_datetime(df['timestamps'])

lookback = 400  # Historical candles
pred_len = 120  # Future candles to predict

x_df = df.loc[:lookback-1, ['open', 'high', 'low', 'close', 'volume', 'amount']]
x_timestamp = df.loc[:lookback-1, 'timestamps']
y_timestamp = df.loc[lookback:lookback+pred_len-1, 'timestamps']

# 4. Generate prediction
pred_df = predictor.predict(
    df=x_df,
    x_timestamp=x_timestamp,
    y_timestamp=y_timestamp,
    pred_len=pred_len,
    T=1.0,           # Temperature (lower = more deterministic)
    top_p=0.9,       # Nucleus sampling threshold
    sample_count=1,  # Number of samples to average
    verbose=True
)

# 5. Result contains predicted OHLCV
print(pred_df.head())
#                    open    high     low   close    volume   amount
# timestamps
# 2024-01-01 12:00  50123.4 50189.2 50098.1 50145.6    123.45  6189234.5
```

### 3.2 Batch Prediction (Multiple Symbols)

```python
# Predict multiple symbols in parallel
pred_df_list = predictor.predict_batch(
    df_list=[df_btc, df_eth, df_sui],
    x_timestamp_list=[x_ts_btc, x_ts_eth, x_ts_sui],
    y_timestamp_list=[y_ts_btc, y_ts_eth, y_ts_sui],
    pred_len=pred_len,
    T=1.0,
    top_p=0.9,
    sample_count=1,
    verbose=True
)
```

---

## 4. Integration with Existing Bot

### 4.1 KronosPredictorWrapper

```python
# trading_bot_v2/kronos/predictor.py

from typing import Dict, List, Optional, Tuple
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger

from model import Kronos, KronosTokenizer, KronosPredictor


class KronosPredictorWrapper:
    """
    Wraps Kronos for use in the trading bot system.
    
    Handles:
    - Model loading and caching
    - Data format conversion
    - Signal generation from predictions
    """
    
    def __init__(
        self,
        model_size: str = "small",  # "mini" or "small"
        max_context: int = 512,
        device: Optional[str] = None,  # "cuda:0", "cpu", None for auto
        clip: float = 5.0,
    ):
        self.model_size = model_size
        self.max_context = max_context
        self.clip = clip
        self.device = device
        
        self._predictor: Optional[KronosPredictor] = None
        self._loaded = False
        
        # Model mapping
        self.model_names = {
            "mini": "NeoQuasar/Kronos-mini",
            "small": "NeoQuasar/Kronos-small",
            "base": "NeoQuasar/Kronos-base",
        }
        
    def load(self) -> None:
        """Load Kronos model and tokenizer."""
        if self._loaded:
            return
            
        logger.info(f"Loading Kronos-{self.model_size} model...")
        
        # Load from HuggingFace
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained(self.model_names[self.model_size])
        
        self._predictor = KronosPredictor(
            model, 
            tokenizer, 
            max_context=self.max_context,
            clip=self.clip
        )
        
        self._loaded = True
        logger.info(f"Kronos-{self.model_size} loaded successfully")
        
    def predict(
        self,
        df: pd.DataFrame,
        timestamps: pd.Series,
        pred_len: int = 60,  # Predict next N candles
        T: float = 1.0,
        top_p: float = 0.9,
        sample_count: int = 1,
    ) -> pd.DataFrame:
        """
        Generate price predictions.
        
        Args:
            df: DataFrame with ['open', 'high', 'low', 'close', 'volume', 'amount']
            timestamps: DatetimeIndex for historical data
            pred_len: Number of future candles to predict
            T: Temperature for sampling (0.1-1.0, lower = more confident)
            top_p: Nucleus sampling threshold
            sample_count: Number of paths to average
            
        Returns:
            DataFrame with predicted OHLCV values
        """
        if not self._loaded:
            self.load()
            
        # Create future timestamps
        last_timestamp = timestamps.iloc[-1]
        freq = self._infer_frequency(timestamps)
        y_timestamps = pd.date_range(
            start=last_timestamp + freq,
            periods=pred_len,
            freq=freq
        )
        
        return self._predictor.predict(
            df=df,
            x_timestamp=timestamps,
            y_timestamp=y_timestamps,
            pred_len=pred_len,
            T=T,
            top_p=top_p,
            sample_count=sample_count,
            verbose=False
        )
        
    def predict_direction(
        self,
        df: pd.DataFrame,
        timestamps: pd.Series,
        horizon: int = 1,  # Candles ahead to predict
    ) -> Tuple[str, float]:
        """
        Predict next candle direction with confidence.
        
        Args:
            df: Historical data
            timestamps: Historical timestamps
            horizon: How many candles ahead to predict
            
        Returns:
            Tuple of (direction: 'up'/'down'/'neutral', confidence: 0-1)
        """
        pred_df = self.predict(df, timestamps, pred_len=horizon, T=0.5)
        
        last_close = df['close'].iloc[-1]
        pred_close = pred_df['close'].iloc[-1]
        
        change_pct = (pred_close - last_close) / last_close
        
        # Threshold for direction (configurable)
        if change_pct > 0.001:  # 0.1% threshold
            direction = "up"
            confidence = min(0.95, 0.5 + abs(change_pct) * 10)
        elif change_pct < -0.001:
            direction = "down"
            confidence = min(0.95, 0.5 + abs(change_pct) * 10)
        else:
            direction = "neutral"
            confidence = 0.5
            
        return direction, confidence
        
    def _infer_frequency(self, timestamps: pd.Series) -> str:
        """Infer data frequency from timestamps."""
        if len(timestamps) < 2:
            return '5T'  # Default 5-minute
            
        deltas = timestamps.diff().dropna()
        median_delta = deltas.median()
        
        minutes = median_delta.total_seconds() / 60
        
        if minutes <= 1:
            return '1T'
        elif minutes <= 5:
            return '5T'
        elif minutes <= 15:
            return '15T'
        elif minutes <= 60:
            return '1H'
        else:
            return '4H'
```

### 4.2 Kronos Signal Generator

```python
# trading_bot_v2/kronos/signal_generator.py

from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger

from ..models import Signal, OrderSide, TradeQuality, MarketState, AssetClass
from ..config import StrategyType
from .predictor import KronosPredictorWrapper


@dataclass
class KronosSignalConfig:
    """Configuration for Kronos signal generation."""
    
    # Prediction settings
    pred_len: int = 60           # Candles to predict
    horizon: int = 5              # Candles ahead to trade on
    min_confidence: float = 0.55   # Minimum confidence to generate signal
    
    # Direction thresholds
    up_threshold: float = 0.001  # 0.1% predicted move up
    down_threshold: float = -0.001 # 0.1% predicted move down
    
    # Sampling parameters
    temperature: float = 0.8      # Lower = more confident predictions
    top_p: float = 0.9
    sample_count: int = 3         # Average multiple predictions
    
    # Market regime filters
    min_volatility: float = 0.0005  # Min ATR as % of price
    max_adx: float = 25.0         # Max ADX (avoid sideways markets)


class KronosSignalGenerator:
    """
    Generates trading signals based on Kronos predictions.
    
    Combines Kronos price predictions with technical filters to produce
    high-quality trading signals.
    """
    
    def __init__(
        self,
        predictor: KronosPredictorWrapper,
        config: Optional[KronosSignalConfig] = None,
    ):
        self.predictor = predictor
        self.config = config or KronosSignalConfig()
        
        # Track last prediction per symbol
        self._last_prediction: Dict[str, datetime] = {}
        self._prediction_cache: Dict[str, pd.DataFrame] = {}
        
    def generate_signals(
        self,
        symbol: str,
        df: pd.DataFrame,
        timestamps: pd.Series,
        current_price: float,
        technical_filters: Optional[Dict] = None,
    ) -> List[Signal]:
        """
        Generate trading signals from Kronos predictions.
        
        Args:
            symbol: Trading pair (e.g., "BTC-USDC")
            df: Historical OHLCV data
            timestamps: DatetimeIndex
            current_price: Current market price
            technical_filters: Optional dict with ATR, ADX, etc.
            
        Returns:
            List of Signal objects
        """
        signals = []
        
        # Apply technical filters first
        if technical_filters:
            if self._check_filters(technical_filters):
                logger.debug(f"{symbol}: Failed technical filters, skipping Kronos")
                return signals
                
        # Get prediction
        pred_df = self.predictor.predict(
            df=df,
            timestamps=timestamps,
            pred_len=self.config.pred_len,
            T=self.config.temperature,
            top_p=self.config.top_p,
            sample_count=self.config.sample_count,
        )
        
        self._prediction_cache[symbol] = pred_df
        
        # Analyze prediction
        last_close = df['close'].iloc[-1]
        horizon_close = pred_df['close'].iloc[self.config.horizon - 1]
        
        change_pct = (horizon_close - last_close) / last_close
        
        # Determine direction and confidence
        if change_pct > self.config.up_threshold:
            direction = OrderSide.BUY
            confidence = min(0.95, 0.5 + change_pct * 50)
        elif change_pct < self.config.down_threshold:
            direction = OrderSide.SELL
            confidence = min(0.95, 0.5 + abs(change_pct) * 50)
        else:
            return signals  # No signal for neutral
            
        # Check minimum confidence
        if confidence < self.config.min_confidence:
            logger.debug(f"{symbol}: Confidence {confidence:.2%} below minimum")
            return signals
            
        # Calculate ATR-based stops
        atr = technical_filters.get('atr', current_price * 0.01) if technical_filters else current_price * 0.01
        stop_mult = 2.0
        target_mult = 2.5
        
        if direction == OrderSide.BUY:
            stop_loss = current_price - (atr * stop_mult)
            take_profit = current_price + (atr * target_mult)
        else:
            stop_loss = current_price + (atr * stop_mult)
            take_profit = current_price - (atr * target_mult)
            
        # Create signal
        signal = Signal(
            strategy=StrategyType.KRONOS_PREDICTION,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=direction,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH_CONVICTION if confidence > 0.70 else TradeQuality.STANDARD,
            timeframe=self._infer_timeframe(timestamps),
            market_state=self._determine_market_state(technical_filters),
            notes=f"Kronos prediction: {change_pct:+.2%} over {self.config.horizon} candles",
            indicators={
                "kronos_direction": direction.value,
                "predicted_change": change_pct,
                "horizon": self.config.horizon,
                "pred_horizon_close": horizon_close,
            }
        )
        
        signals.append(signal)
        logger.info(
            f"{symbol}: Kronos signal - {direction.value.upper()} @ ${current_price:.2f}, "
            f"pred: {change_pct:+.2%}, conf={confidence:.0%}"
        )
        
        return signals
        
    def _check_filters(self, filters: Dict) -> bool:
        """Check if market passes technical filters."""
        # Volatility filter
        if 'atr_pct' in filters:
            if filters['atr_pct'] < self.config.min_volatility:
                return True  # Fail - not enough volatility
                
        # Trend filter (ADX)
        if 'adx' in filters:
            if filters['adx'] > self.config.max_adx:
                return True  # Fail - too sideways
                
        return False
        
    def _infer_timeframe(self, timestamps: pd.Series) -> str:
        """Infer timeframe from timestamps."""
        deltas = timestamps.diff().dropna()
        median_minutes = deltas.median().total_seconds() / 60
        
        if median_minutes <= 1:
            return "1m"
        elif median_minutes <= 5:
            return "5m"
        elif median_minutes <= 15:
            return "15m"
        elif median_minutes <= 60:
            return "1h"
        else:
            return "4h"
            
    def _determine_market_state(self, filters: Optional[Dict]) -> MarketState:
        """Determine market state from filters."""
        if not filters:
            return MarketState.UNKNOWN
            
        adx = filters.get('adx', 0)
        
        if adx > 30:
            return MarketState.TREND
        elif adx > 25:
            return MarketState.TREND_MODERATE
        else:
            return MarketState.RANGING
```

### 4.3 Integration with StrategyManager

```python
# trading_bot_v2/kronos/strategy.py

from typing import Dict, List, Optional
from loguru import logger

from ..models import Signal
from .predictor import KronosPredictorWrapper
from .signal_generator import KronosSignalGenerator, KronosSignalConfig


class KronosStrategy:
    """
    Standalone strategy using Kronos predictions.
    
    Can be added to StrategyManager alongside existing strategies.
    """
    
    def __init__(
        self,
        config: Optional[KronosSignalConfig] = None,
        model_size: str = "small",
    ):
        # Initialize predictor
        self.predictor = KronosPredictorWrapper(model_size=model_size)
        
        # Initialize signal generator
        self.config = config or KronosSignalConfig()
        self.signal_generator = KronosSignalGenerator(
            predictor=self.predictor,
            config=self.config
        )
        
        # Track state
        self._initialized = False
        
        logger.info(
            f"KronosStrategy initialized: model={model_size}, "
            f"pred_len={self.config.pred_len}, min_conf={self.config.min_confidence}"
        )
        
    def initialize(self):
        """Load model (call before live trading)."""
        if not self._initialized:
            self.predictor.load()
            self._initialized = True
            logger.info("Kronos model loaded and ready")
            
    def generate_signals(
        self,
        symbol: str,
        df: pd.DataFrame,
        timestamps: pd.Series,
        current_price: float,
        technical_filters: Optional[Dict] = None,
    ) -> List[Signal]:
        """Generate signals using Kronos predictions."""
        if not self._initialized:
            self.initialize()
            
        return self.signal_generator.generate_signals(
            symbol=symbol,
            df=df,
            timestamps=timestamps,
            current_price=current_price,
            technical_filters=technical_filters,
        )
        
    def get_prediction(
        self,
        symbol: str,
        df: pd.DataFrame,
        timestamps: pd.Series,
        horizon: int = 5,
    ) -> Dict:
        """Get raw prediction data for analysis."""
        if not self._initialized:
            self.initialize()
            
        pred_df = self.predictor.predict(
            df=df,
            timestamps=timestamps,
            pred_len=horizon,
        )
        
        last_close = df['close'].iloc[-1]
        pred_close = pred_df['close'].iloc[-1]
        
        return {
            "symbol": symbol,
            "last_close": last_close,
            "predicted_close": pred_close,
            "change_pct": (pred_close - last_close) / last_close,
            "prediction_df": pred_df,
        }
```

---

## 5. Backtesting Integration

### 5.1 Kronos Backtester

```python
# trading_bot_v2/kronos/backtester.py

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger

from ..models import Trade, Position, OrderSide
from ..strategies.risk_manager import RiskManager
from .predictor import KronosPredictorWrapper
from .signal_generator import KronosSignalGenerator, KronosSignalConfig


@dataclass
class BacktestConfig:
    """Configuration for Kronos backtesting."""
    
    initial_balance: float = 10000.0
    leverage: int = 10
    commission_rate: float = 0.0004  # 0.04% taker fee
    
    # Prediction settings
    pred_len: int = 60
    horizon: int = 5
    
    # Position sizing
    use_kelly: bool = True
    kelly_fraction: float = 0.5  # Fractional Kelly
    max_position_pct: float = 0.1  # Max 10% of balance
    
    # Filters
    min_confidence: float = 0.55
    use_technical_filters: bool = True


class KronosBacktester:
    """
    Backtester for Kronos-based strategies.
    
    Simulates trading with Kronos predictions on historical data.
    """
    
    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        model_size: str = "small",
    ):
        self.config = config or BacktestConfig()
        self.model_size = model_size
        
        # Results storage
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = []
        self.predictions: List[Dict] = []
        
        # State
        self.balance = self.config.initial_balance
        self.peak_balance = self.balance
        
    def run(
        self,
        df: pd.DataFrame,
        timestamps: pd.Series,
        symbol: str = "BTC-USDC",
    ) -> Dict:
        """
        Run backtest on historical data.
        
        Args:
            df: Historical OHLCV data with ['open', 'high', 'low', 'close', 'volume']
            timestamps: DatetimeIndex
            symbol: Trading pair name
            
        Returns:
            Dict with backtest results
        """
        logger.info(f"Starting Kronos backtest for {symbol}")
        
        # Initialize predictor
        predictor = KronosPredictorWrapper(model_size=self.model_size)
        predictor.load()
        
        signal_gen = KronosSignalGenerator(
            predictor=predictor,
            config=KronosSignalConfig(
                pred_len=self.config.pred_len,
                horizon=self.config.horizon,
                min_confidence=self.config.min_confidence,
            )
        )
        
        # Prepare data
        lookback = min(self.config.pred_len + 100, len(df) - self.config.horizon)
        
        self.balance = self.config.initial_balance
        self.peak_balance = self.balance
        self.trades = []
        self.equity_curve = []
        
        # Walk-forward backtest
        for i in range(lookback, len(df) - self.config.horizon):
            # Get historical data up to current point
            hist_df = df.iloc[:i].copy()
            hist_ts = timestamps.iloc[:i]
            
            current_price = df['close'].iloc[i]
            
            # Calculate technical filters if enabled
            filters = None
            if self.config.use_technical_filters:
                filters = self._calculate_filters(hist_df)
                
            # Generate signal
            signals = signal_gen.generate_signals(
                symbol=symbol,
                df=hist_df,
                timestamps=hist_ts,
                current_price=current_price,
                technical_filters=filters,
            )
            
            if not signals:
                self.equity_curve.append(self.balance)
                continue
                
            signal = signals[0]
            
            # Calculate position size
            position_size = self._calculate_position_size(signal, filters)
            
            # Simulate trade
            entry_price = current_price
            stop_loss = signal.stop_loss
            take_profit = signal.take_profit
            
            # Check which direction
            if signal.side == OrderSide.BUY:
                # Check if TP or SL hit first
                high_future = df['high'].iloc[i:i+self.config.horizon].max()
                low_future = df['low'].iloc[i:i+self.config.horizon].min()
                
                if high_future >= take_profit:
                    exit_price = take_profit
                    pnl_pct = (take_profit - entry_price) / entry_price
                elif low_future <= stop_loss:
                    exit_price = stop_loss
                    pnl_pct = (stop_loss - entry_price) / entry_price
                else:
                    exit_price = df['close'].iloc[i + self.config.horizon - 1]
                    pnl_pct = (exit_price - entry_price) / entry_price
            else:
                if low_future <= take_profit:
                    exit_price = take_profit
                    pnl_pct = (entry_price - take_profit) / entry_price
                elif high_future >= stop_loss:
                    exit_price = stop_loss
                    pnl_pct = (entry_price - stop_loss) / entry_price
                else:
                    exit_price = df['close'].iloc[i + self.config.horizon - 1]
                    pnl_pct = (entry_price - exit_price) / entry_price
                    
            # Calculate P&L
            position_value = position_size * entry_price
            commission = position_value * self.config.commission_rate
            pnl = (position_value * pnl_pct) - (commission * 2)  # Entry + exit
            
            # Update balance
            self.balance += pnl
            self.peak_balance = max(self.peak_balance, self.balance)
            
            # Record trade
            trade = Trade(
                id=f"bt_{i}",
                asset=symbol,
                asset_class=AssetClass.PERPETUAL,
                side=signal.side,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=position_size,
                entry_time=timestamps.iloc[i],
                exit_time=timestamps.iloc[i + self.config.horizon],
                stop_loss=stop_loss,
                take_profit=take_profit,
                actual_rrr=abs(pnl_pct / ((stop_loss - entry_price) / entry_price)) if signal.stop_loss else 0,
                pnl_dollar=pnl,
                pnl_percent=pnl_pct * 100,
                commission=commission * 2,
                leverage=self.config.leverage,
                strategy=StrategyType.KRONOS_PREDICTION,
                quality=signal.quality,
                market_state=signal.market_state,
                notes=f"Kronos conf={signal.confidence:.0%}",
            )
            self.trades.append(trade)
            self.equity_curve.append(self.balance)
            
        return self._generate_report()
        
    def _calculate_filters(self, df: pd.DataFrame) -> Dict:
        """Calculate technical filters from data."""
        from ..strategies.momentum_scalping import calculate_atr, calculate_adx
        
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        
        # ATR
        atr = calculate_atr(high, low, close, period=14)
        atr_value = atr[-1] if len(atr) > 0 else close[-1] * 0.01
        atr_pct = atr_value / close[-1]
        
        # ADX (simplified - use trend strength)
        if len(close) >= 28:
            adx = calculate_adx(high, low, close, period=14)
            adx_value = adx[-1] if len(adx) > 0 else 20
        else:
            adx_value = 20
            
        return {
            "atr": atr_value,
            "atr_pct": atr_pct,
            "adx": adx_value,
        }
        
    def _calculate_position_size(self, signal, filters: Optional[Dict]) -> float:
        """Calculate position size based on Kelly criterion."""
        if self.config.use_kelly and filters and len(self.trades) >= 20:
            # Calculate win rate from recent trades
            recent_trades = self.trades[-50:]
            wins = [t for t in recent_trades if t.pnl_dollar > 0]
            win_rate = len(wins) / len(recent_trades)
            
            # Calculate average win/loss
            avg_win = np.mean([t.pnl_dollar for t in wins]) if wins else 0
            losses = [t for t in recent_trades if t.pnl_dollar < 0]
            avg_loss = abs(np.mean([t.pnl_dollar for t in losses])) if losses else 1
            
            if avg_loss > 0:
                win_loss_ratio = avg_win / avg_loss
                kelly = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
                kelly = max(0, min(kelly, self.config.kelly_fraction))  # Fractional Kelly
            else:
                kelly = 0.05
        else:
            kelly = 0.05
            
        # Apply max position limit
        max_position = self.balance * self.config.max_position_pct
        position_value = min(self.balance * kelly, max_position)
        
        return position_value / signal.entry_price
        
    def _generate_report(self) -> Dict:
        """Generate backtest report."""
        if not self.trades:
            return {"error": "No trades generated"}
            
        wins = [t for t in self.trades if t.pnl_dollar > 0]
        losses = [t for t in self.trades if t.pnl_dollar <= 0]
        
        total_return = self.balance - self.config.initial_balance
        total_return_pct = (total_return / self.config.initial_balance) * 100
        
        return {
            "initial_balance": self.config.initial_balance,
            "final_balance": self.balance,
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "num_trades": len(self.trades),
            "num_wins": len(wins),
            "num_losses": len(losses),
            "win_rate": len(wins) / len(self.trades) if self.trades else 0,
            "avg_win": np.mean([t.pnl_dollar for t in wins]) if wins else 0,
            "avg_loss": np.mean([t.pnl_dollar for t in losses]) if losses else 0,
            "max_drawdown": self._calculate_max_drawdown(),
            "sharpe_ratio": self._calculate_sharpe(),
            "equity_curve": self.equity_curve,
        }
        
    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown."""
        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        return abs(np.min(drawdown)) * 100 if len(equity) > 0 else 0
        
    def _calculate_sharpe(self) -> float:
        """Calculate Sharpe ratio."""
        if len(self.equity_curve) < 2:
            return 0
            
        returns = np.diff(self.equity_curve) / self.equity_curve[:-1]
        if len(returns) == 0 or np.std(returns) == 0:
            return 0
            
        return np.mean(returns) / np.std(returns) * np.sqrt(252)
```

---

## 6. Usage Examples

### 6.1 Quick Start (Minimal Integration)

```python
# Example: Get a single prediction
import pandas as pd
from trading_bot_v2.kronos.predictor import KronosPredictorWrapper

# Initialize
predictor = KronosPredictorWrapper(model_size="mini")  # Use "mini" for fast testing
predictor.load()

# Prepare your data
df = pd.DataFrame({
    'open': [...],
    'high': [...],
    'low': [...],
    'close': [...],
    'volume': [...],
    'amount': [...]
})
timestamps = pd.to_datetime([...])

# Get prediction
pred_df = predictor.predict(df, timestamps, pred_len=60)

# Analyze
print(f"Current: ${df['close'].iloc[-1]:.2f}")
print(f"Predicted (60 candles ahead): ${pred_df['close'].iloc[-1]:.2f}")
```

### 6.2 Integration with Existing Strategy Framework

```python
# Add to your strategy_manager.py

from trading_bot_v2.kronos.strategy import KronosStrategy

class StrategyManager:
    def __init__(self, config):
        self.strategies = {}
        
        # Add Kronos strategy
        self.kronos_strategy = KronosStrategy(
            model_size="small",
            config=KronosSignalConfig(
                min_confidence=0.60,
                pred_len=60,
                horizon=5,
            )
        )
        
    def generate_signals(self, symbol, data, current_price):
        signals = []
        
        # Run existing strategies
        for strategy in self.strategies.values():
            signals.extend(strategy.generate_signals(...))
            
        # Run Kronos strategy
        kronos_signals = self.kronos_strategy.generate_signals(
            symbol=symbol,
            df=data['ohlcv'],
            timestamps=data['timestamps'],
            current_price=current_price,
            technical_filters=data.get('filters'),
        )
        signals.extend(kronos_signals)
        
        return signals
```

### 6.3 Running a Backtest

```python
# Example: Backtest Kronos on BTC 5-minute data

import pandas as pd
from trading_bot_v2.kronos.backtester import KronosBacktester, BacktestConfig

# Configure
config = BacktestConfig(
    initial_balance=10000,
    leverage=10,
    pred_len=60,
    horizon=5,
    min_confidence=0.55,
    use_kelly=True,
)

# Run backtest
backtester = KronosBacktester(config=config, model_size="small")
results = backtester.run(df, timestamps, symbol="BTC-USDC")

# Print results
print(f"Total Return: ${results['total_return']:+.2f} ({results['total_return_pct']:+.2f}%)")
print(f"Win Rate: {results['win_rate']:.1%}")
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
print(f"Max Drawdown: {results['max_drawdown']:.2f}%")
```

---

## 7. Performance Considerations

### 7.1 Hardware Requirements

| Model | Min RAM | GPU VRAM | CPU Only Time (est.) |
|-------|---------|----------|---------------------|
| Kronos-mini | 4GB | 1GB | ~30s/prediction |
| Kronos-small | 8GB | 2-4GB | ~2min/prediction |
| Kronos-base | 16GB | 4-8GB | ~5min/prediction |

### 7.2 Optimization Tips

1. **Batch Predictions**: Use `predict_batch()` for multiple symbols (GPU-parallelized)
2. **Reduce `sample_count`**: Use 1-3 for production, higher for analysis
3. **Increase `T` for noise**: Lower temperature (0.5-0.7) for more confident predictions
4. **Cache Predictions**: Store predictions for symbols that don't change candle-by-candle

### 7.3 Latency Guidelines

- **5m candles**: Prediction completes in ~30s (small model, CPU)
- **15m candles**: Prediction completes in ~60s
- **1h candles**: Prediction completes in ~120s

For live trading, consider running predictions async and using cached results.

---

## 8. Signal Validation Pipeline

Based on the video, the recommended signal flow:

```
Raw Kronos Prediction
        ↓
ATR Filter (volatility gate)
   - Reject if ATR < 0.05% of price
        ↓
ADX Filter (trend gate)
   - Reject if ADX > 25 (sideways market)
        ↓
Confidence Filter
   - Reject if confidence < 0.55
        ↓
Edge Filter (video mentions 3% edge)
   - Reject if predicted move < 0.3%
        ↓
Kelly Position Sizing
        ↓
Signal Generated
```

---

## 9. Troubleshooting

### 9.1 Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| CUDA OOM | Not enough GPU memory | Use Kronos-mini or `device="cpu"` |
| Slow predictions | CPU mode on large model | Switch to `model_size="mini"` or use GPU |
| NaN in predictions | Input data has NaN | Fill missing values: `df.fillna(method='ffill')` |
| Low confidence | Market is sideways | Increase `up_threshold`/`down_threshold` |
| Prediction too far | Lookback too long | Reduce `pred_len` or use shorter lookback |

### 9.2 Model Loading Issues

```python
# If HuggingFace download fails, try:
import os
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"

# Or cache locally after first download
from pathlib import Path
cache_dir = Path.home() / ".cache" / "huggingface"
```

---

## 10. Integration Checklist

- [ ] Clone Kronos repo and add to project structure
- [ ] Install dependencies (`pip install -r requirements.txt`)
- [ ] Create `KronosPredictorWrapper` class
- [ ] Create `KronosSignalGenerator` class
- [ ] Integrate with StrategyManager
- [ ] Create backtester for validation
- [ ] Run backtests on historical data
- [ ] Tune parameters (confidence, thresholds, filters)
- [ ] Test in paper trading mode
- [ ] Deploy with monitoring

---

## 11. References

- **GitHub**: https://github.com/shiyu-coder/Kronos
- **Paper**: https://arxiv.org/abs/2508.02739
- **Live Demo**: https://shiyu-coder.github.io/Kronos-demo/
- **HuggingFace Models**:
  - NeoQuasar/Kronos-mini (4.1M params)
  - NeoQuasar/Kronos-small (24.7M params)
  - NeoQuasar/Kronos-base (102.3M params)
- **Tokenizer**: NeoQuasar/Kronos-Tokenizer-base

---

*Document created for Trading Bot System v3 integration.*
*Last updated: 2026-05-09*
