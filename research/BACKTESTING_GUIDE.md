# Backtesting Best Practices for Trading Bot v2 Strategies

## Overview
This guide provides comprehensive backtesting best practices for the 8 trading strategies currently implemented in your bot:

**Core Strategies:**
- Mean Reversion (RSI-based)
- MA Crossover (50/200 MA with pullback)
- Grid Trading (0.4x ATR spacing)
- Liquidation Capture (2.5% price move, 2.5x volume spike)

**Advanced Strategies:**
- VWAP Scalping (1.8% deviation threshold)
- Funding Arbitrage (Pacifica perpetuals)
- Momentum Scalping (EMA 9/21 crossover)
- Order Book Imbalance (Level 2 analysis)

## Data Requirements & Quality

### Essential Data Sources
```python
# Recommended data sources for comprehensive backtesting
from typing import Dict, List
import pandas as pd

# Primary data requirements
required_data = {
    'ohlcv': {
        'frequency': '1m',  # Minimum 1-minute candles
        'columns': ['timestamp', 'open', 'high', 'low', 'close', 'volume'],
        'coverage': '2018-present'  # Minimum 5+ years
    },
    'orderbook': {
        'depth': 10,  # 10 levels for order book imbalance
        'frequency': '1s',  # Real-time snapshots
        'coverage': '2020-present'
    },
    'funding_rates': {
        'frequency': 'hourly',  # Pacifica funding rates
        'coverage': '2020-present'
    },
    'volume_profile': {
        'frequency': 'daily',  # VWAP calculations
        'coverage': '2018-present'
    }
}
```

### Data Quality Considerations
1. **Survivorship Bias Prevention**: Include delisted/inactive tokens
2. **Liquidity Filtering**: Exclude periods with < $100K 24h volume
3. **Data Gaps**: Fill missing candles with interpolation
4. **Exchange Coverage**: Use multiple exchanges for redundancy

### Recommended Data Providers
- **CoinGecko API**: Comprehensive historical data (30M+ tokens)
- **CryptoQuant**: On-chain + market data
- **Kaiko**: Institutional-grade order book data
- **DWF Labs**: Liquidity analytics

## Strategy-Specific Backtesting Approaches

### 1. Mean Reversion (RSI-based)

#### Key Metrics to Track
```python
mean_reversion_metrics = {
    'rsi_periods': [14, 21, 30],  # Test different RSI periods
    'overbought_levels': [65, 70, 75],  # Test different thresholds
    'oversold_levels': [35, 30, 25],
    'confidence_thresholds': [0.45, 0.5, 0.6],  # Your current 0.45
    'lookback_periods': [50, 100, 200]  # For mean calculation
}
```

#### Backtesting Considerations
- **Regime Detection**: Test across trending/ranging markets
- **Volatility Adjustment**: Scale RSI thresholds with ATR
- **Signal Confirmation**: Require multiple timeframe confirmation
- **Drawdown Analysis**: Mean reversion can have extended drawdowns in trending markets

### 2. MA Crossover (50/200 MA with pullback)

#### Parameter Optimization
```python
ma_crossover_params = {
    'fast_ma': [20, 50, 100],
    'slow_ma': [200, 250, 300],
    'pullback_threshold': [0.5, 1.0, 1.5],  # % pullback from MA crossover
    'confirmation_periods': [3, 5, 10]  # Candles for confirmation
}
```

#### Market Regime Testing
- **Trending Markets**: Test during strong trends (ADX > 30)
- **Choppy Markets**: Test during ranging conditions
- **Transition Periods**: Test during regime changes

### 3. Grid Trading (0.4x ATR spacing)

#### Grid Configuration Testing
```python
grid_trading_params = {
    'atr_multiplier': [0.3, 0.4, 0.5],  # Your current 0.4x
    'grid_levels': [6, 8, 10, 12],  # Your current 8 levels
    'profit_target': [0.2, 0.4, 0.6],  # % per grid level
    'max_positions': [5, 10, 15],  # Your current 10
    'stop_loss': [2.0, 3.0, 4.0]  # % from entry
}
```

#### Critical Backtesting Scenarios
- **Trending Markets**: Test grid performance during strong trends
- **Sideways Markets**: Test optimal grid spacing
- **Volatility Regimes**: Test different ATR multipliers
- **Liquidity Conditions**: Test with varying order book depth

### 4. Liquidation Capture (2.5% price move, 2.5x volume spike)

#### Liquidation Detection Parameters
```python
liquidation_params = {
    'price_move_threshold': [2.0, 2.5, 3.0],  # Your current 2.5%
    'volume_spike_multiplier': [2.0, 2.5, 3.0],  # Your current 2.5x
    'liquidation_volume_threshold': [50, 100, 200],  # BTC value
    'confirmation_periods': [1, 2, 3]  # Candles for confirmation
}
```

#### Risk Considerations
- **Cascade Effects**: Test during liquidation cascades
- **Market Impact**: Test how large liquidations affect price
- **Timing**: Test entry timing relative to liquidation events

### 5. VWAP Scalping (1.8% deviation threshold)

#### VWAP Configuration
```python
vwap_params = {
    'deviation_threshold': [1.5, 1.8, 2.0],  # Your current 1.8%
    'lookback_period': [30, 60, 120],  # Minutes for VWAP calculation
    'confirmation_indicator': ['MACD', 'RSI', 'Volume'],
    'cooldown_period': [5, 8, 10]  # Your current 8 minutes
}
```

#### Intraday Testing
- **Session Analysis**: Test different trading sessions
- **Volume Profiles**: Test during high/low volume periods
- **Market Impact**: Test slippage during VWAP deviations

### 6. Funding Arbitrage (Pacifica perpetuals)

#### Funding Rate Parameters
```python
funding_params = {
    'min_rate': [0.005, 0.01, 0.02],  # Your current 0.01%
    'max_allocation': [0.1, 0.2, 0.3],  # Your current 20%
    'funding_check_interval': [1, 2, 4],  # Hours between checks
    'delta_neutral_threshold': [0.001, 0.005, 0.01]  # Position delta tolerance
}
```

#### Critical Testing
- **Funding Rate Cycles**: Test different market cycles
- **Basis Risk**: Test price divergence between spot/futures
- **Funding Rate Volatility**: Test during rate spikes

### 7. Momentum Scalping (EMA 9/21 crossover)

#### Scalping Parameters
```python
scalping_params = {
    'fast_ema': [9, 12, 15],
    'slow_ema': [21, 25, 30],
    'rsi_confirmation': [30, 40, 50],  # RSI levels for confirmation
    'macd_confirmation': ['bullish', 'hidden_bullish'],
    'cooldown_period': [3, 5, 7],  # Your current 5 minutes
    'timeframe': ['1m', '5m', '15m']  # Your current 5m
}
```

#### High-Frequency Testing
- **Latency Impact**: Test different execution delays
- **Slippage Modeling**: Test during high volatility
- **Transaction Costs**: Test with realistic fees

### 8. Order Book Imbalance (Level 2 analysis)

#### Order Flow Parameters
```python
orderbook_params = {
    'depth_levels': [5, 10, 15],  # Your current 10 levels
    'imbalance_threshold': [0.6, 0.7, 0.8],  # Buy/sell imbalance ratio
    'volume_filter': [1000, 5000, 10000],  # Minimum volume
    'cooldown_period': [15, 30, 60],  # Your current 30 seconds
    'price_impact_threshold': [0.1, 0.2, 0.3]  # Max acceptable slippage
}
```

#### Order Book Testing
- **Liquidity Depth**: Test with different order book depths
- **Market Impact**: Test how large orders affect price
- **Latency Effects**: Test real-time vs delayed data

## Backtesting Methodology

### 1. Walk-Forward Analysis
```python
import numpy as np
from typing import Dict, List, Tuple

def walk_forward_analysis(data: pd.DataFrame, strategy, params: Dict) -> Dict:
    """
    Perform walk-forward analysis with rolling windows
    """
    results = []
    train_window = 6 * 30  # 6 months of daily data
    test_window = 1 * 30   # 1 month test period
    
    for i in range(0, len(data) - train_window - test_window, test_window):
        train_data = data.iloc[i:i + train_window]
        test_data = data.iloc[i + train_window:i + train_window + test_window]
        
        # Optimize parameters on training data
        best_params = optimize_parameters(train_data, strategy, params)
        
        # Test on unseen data
        test_result = backtest_strategy(test_data, strategy, best_params)
        results.append(test_result)
    
    return aggregate_results(results)
```

### 2. Monte Carlo Simulation
```python
def monte_carlo_simulation(strategy, num_simulations: int = 1000) -> Dict:
    """
    Run Monte Carlo simulations to test strategy robustness
    """
    simulation_results = []
    
    for _ in range(num_simulations):
        # Shuffle trade sequence to test different market conditions
        shuffled_trades = np.random.permutation(strategy.trades)
        
        # Simulate with different slippage and fee scenarios
        simulated_result = simulate_with_frictions(
            shuffled_trades,
            slippage_range=(0.1, 0.5),  # 0.1% to 0.5% slippage
            fee_multiplier=(1.0, 1.5)    # 100% to 150% of actual fees
        )
        simulation_results.append(simulated_result)
    
    return analyze_simulation_distribution(simulation_results)
```

### 3. Regime-Based Testing
```python
def regime_based_testing(data: pd.DataFrame, strategy) -> Dict:
    """
    Test strategy performance across different market regimes
    """
    regimes = {
        'trending_strong': data['adx'] > 30.0,
        'trending_moderate': (data['adx'] >= 25.0) & (data['adx'] <= 30.0),
        'ranging_volatile': (data['adx'] <= 25.0) & (data['volatility'] > 0.65),
        'ranging_calm': (data['adx'] <= 25.0) & (data['volatility'] <= 0.65),
        'indecisive': data['regime'] == 'INDECISIVE'
    }
    
    results = {}
    for regime_name, mask in regimes.items():
        regime_data = data[mask]
        if len(regime_data) > 100:  # Minimum sample size
            results[regime_name] = backtest_strategy(regime_data, strategy)
    
    return results
```

## Performance Metrics & Validation

### Essential Metrics
```python
performance_metrics = {
    'profitability': {
        'total_return': 'Total percentage return',
        'cagr': 'Compound Annual Growth Rate',
        'profit_factor': 'Gross profits / Gross losses (target > 1.5)',
        'win_rate': 'Percentage of profitable trades (target > 50%)',
        'avg_win': 'Average winning trade size',
        'avg_loss': 'Average losing trade size',
        'payoff_ratio': 'Avg win / Avg loss (target > 1.0)'
    },
    'risk': {
        'max_drawdown': 'Maximum peak-to-trough decline (target < 20%)',
        'max_drawdown_duration': 'Longest drawdown period',
        'volatility': 'Standard deviation of returns',
        'sharpe_ratio': 'Risk-adjusted return (target > 1.0)',
        'sortino_ratio': 'Downside risk-adjusted return',
        'calmar_ratio': 'Return / Max drawdown (target > 0.5)'
    },
    'consistency': {
        'profit_days': 'Percentage of profitable days',
        'profit_months': 'Percentage of profitable months',
        'profit_quarters': 'Percentage of profitable quarters',
        'profit_years': 'Percentage of profitable years'
    },
    'execution': {
        'slippage': 'Average price deviation from expected',
        'fill_rate': 'Percentage of orders filled',
        'latency': 'Average execution delay',
        'rejection_rate': 'Percentage of rejected orders'
    }
}
```

### Red Flag Metrics
- **Profit Factor < 1.5**: Strategy may not be profitable
- **Sharpe Ratio < 1.0**: Poor risk-adjusted returns
- **Max Drawdown > 30%**: Excessive risk
- **Win Rate < 40%**: May require very high payoff ratio
- **Average Trade Duration < 1 minute**: May indicate over-trading

## Realistic Cost Modeling

### Transaction Costs
```python
def calculate_realistic_costs(trade: Dict) -> Dict:
    """
    Calculate realistic trading costs including fees, slippage, and latency
    """
    # Base exchange fees (Pacifica example)
    taker_fee = 0.04 / 100  # 0.04%
    maker_fee = 0.02 / 100  # 0.02%
    
    # Slippage modeling based on liquidity
    base_slippage = 0.05 / 100  # 0.05% base slippage
    liquidity_adjustment = max(0, (1000000 - trade['volume_usd']) / 1000000) * 0.1
    total_slippage = base_slippage + liquidity_adjustment
    
    # Latency impact (100ms typical API delay)
    latency_impact = 0.02 / 100  # 0.02% per 100ms delay
    
    total_cost = {
        'fees': trade['size'] * trade['price'] * (taker_fee if trade['side'] == 'buy' else maker_fee),
        'slippage': trade['size'] * trade['price'] * total_slippage,
        'latency': trade['size'] * trade['price'] * latency_impact,
        'total': sum(total_cost.values())
    }
    
    return total_cost
```

### Realistic Assumptions
- **Slippage**: 0.1% - 0.5% depending on liquidity
- **Fees**: Include taker/maker fees, funding rates
- **Latency**: 50-200ms API response times
- **Partial Fills**: Model order book depth and fill rates
- **Market Impact**: Large orders affect price

## Validation Framework

### 1. Out-of-Sample Testing
```python
def out_of_sample_validation(data: pd.DataFrame, strategy) -> Dict:
    """
    Validate strategy on completely unseen data
    """
    # Split data: 70% training, 30% testing
    split_index = int(len(data) * 0.7)
    train_data = data.iloc[:split_index]
    test_data = data.iloc[split_index:]
    
    # Optimize on training data
    optimized_params = optimize_parameters(train_data, strategy)
    
    # Test on completely unseen data
    test_results = backtest_strategy(test_data, strategy, optimized_params)
    
    # Calculate out-of-sample performance decay
    performance_decay = (test_results['sharpe_ratio'] - 
                        strategy.backtest_sharpe_ratio) / strategy.backtest_sharpe_ratio
    
    return {
        'out_of_sample_sharpe': test_results['sharpe_ratio'],
        'performance_decay': performance_decay,
        'validation_passed': performance_decay > -0.5  # Allow 50% decay
    }
```

### 2. Cross-Asset Validation
```python
def cross_asset_validation(strategies: Dict[str, Strategy], assets: List[str]) -> Dict:
    """
    Test strategies across multiple assets to ensure robustness
    """
    validation_results = {}
    
    for asset in assets:
        asset_data = get_historical_data(asset)
        
        for strategy_name, strategy in strategies.items():
            # Test strategy on this asset
            results = backtest_strategy(asset_data, strategy)
            
            # Store results
            validation_results[f'{strategy_name}_{asset}'] = results
            
            # Check for consistency
            if results['sharpe_ratio'] < 0.5:
                print(f"Warning: {strategy_name} performs poorly on {asset}")
    
    return validation_results
```

### 3. Stress Testing
```python
def stress_test_strategy(strategy, scenarios: List[Dict]) -> Dict:
    """
    Test strategy under extreme market conditions
    """
    stress_results = {}
    
    for scenario in scenarios:
        # Apply scenario to historical data
        stressed_data = apply_market_scenario(strategy.data, scenario)
        
        # Test strategy performance
        results = backtest_strategy(stressed_data, strategy)
        stress_results[scenario['name']] = results
        
        # Check for critical failures
        if results['max_drawdown'] > 0.5:  # 50% drawdown
            print(f"Critical: Strategy failed under {scenario['name']} scenario")
    
    return stress_results
```

## Common Backtesting Pitfalls to Avoid

### 1. Look-Ahead Bias
```python
# ❌ WRONG - Using future information
if future_data['rsi'] < 30:
    generate_buy_signal()

# ✅ CORRECT - Using only available information
if current_data['rsi'] < 30 and not has_buy_signal_been_generated():
    generate_buy_signal()
```

### 2. Survivorship Bias
```python
# ❌ WRONG - Only testing current assets
assets = get_current_crypto_list()

# ✅ CORRECT - Including delisted/inactive assets
assets = get_complete_crypto_history_including_delisted()
```

### 3. Overfitting
```python
# ❌ WRONG - Testing too many parameter combinations
for rsi_period in range(5, 100):
    for overbought in range(50, 100):
        for oversold in range(0, 50):
            test_strategy(rsi_period, overbought, oversold)

# ✅ CORRECT - Use walk-forward optimization
best_params = walk_forward_optimization(data, strategy, param_space)
```

### 4. Ignoring Transaction Costs
```python
# ❌ WRONG - Perfect execution assumption
trade_result = execute_trade(symbol, size, price)

# ✅ CORRECT - Realistic cost modeling
trade_result = execute_trade_with_realistic_costs(
    symbol, size, price,
    slippage=0.0005, fees=0.0004, latency=0.1
)
```

## Implementation Recommendations

### 1. Backtesting Infrastructure
```python
class BacktestingEngine:
    def __init__(self):
        self.data_warehouse = DataWarehouse()
        self.strategy_registry = StrategyRegistry()
        self.cost_model = RealisticCostModel()
        self.validation_framework = ValidationFramework()
    
    def run_comprehensive_backtest(self, strategy_name: str) -> Dict:
        """
        Run complete backtest with all validation steps
        """
        # Load strategy
        strategy = self.strategy_registry.get(strategy_name)
        
        # Get historical data
        data = self.data_warehouse.get_historical_data(strategy.symbols)
        
        # Run base backtest
        base_results = self.backtest_strategy(data, strategy)
        
        # Run validations
        validations = self.run_validations(data, strategy, base_results)
        
        return {
            'base_results': base_results,
            'validations': validations,
            'recommendations': self.generate_recommendations(validations)
        }
```

### 2. Testing Checklist
- [ ] Data quality validation (missing data, outliers)
- [ ] Survivorship bias prevention
- [ ] Realistic cost modeling
- [ ] Walk-forward analysis
- [ ] Monte Carlo simulations
- [ ] Regime-based testing
- [ ] Out-of-sample validation
- [ ] Cross-asset testing
- [ ] Stress testing
- [ ] Performance metric analysis
- [ ] Parameter sensitivity analysis

### 3. Documentation Requirements
For each backtest, document:
- Data sources and time periods
- Parameter optimization methodology
- Cost assumptions and models
- Validation results and confidence levels
- Limitations and assumptions
- Next steps for live deployment

## Conclusion

Backtesting is not about finding the perfect strategy, but about understanding the limitations and risks of your trading approach. The goal is to build robust, adaptable strategies that can survive real market conditions.

**Key Principles:**
1. **Conservative Assumptions**: Always model worst-case scenarios
2. **Multiple Validation Methods**: Don't rely on single backtest results
3. **Continuous Monitoring**: Backtesting is ongoing, not one-time
4. **Risk First**: Focus on risk management over returns
5. **Transparency**: Document all assumptions and limitations

By following these best practices, you can develop trading strategies that are more likely to succeed in live trading environments while avoiding the common pitfalls that lead to backtest overfitting and unrealistic expectations.