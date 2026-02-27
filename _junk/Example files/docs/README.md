# Trading Bot

A production-ready cryptocurrency trading bot implementing systematic trading strategies with strict risk management, based on proven trading principles.

## ⚠️ Important Disclaimer

**This software is for educational and research purposes only. Trading cryptocurrencies involves substantial risk of loss and is not suitable for every investor. Past performance does not guarantee future results.**

- The bot implements strict risk management but cannot eliminate trading risks
- Always test thoroughly on paper trading accounts before live deployment
- Never risk more than you can afford to lose
- Consult with financial advisors before engaging in trading activities

## Features

### 🛡️ Risk Management
- **Isolated Margin Model**: Each trade's maximum loss is capped at allocated margin
- **3-5% Stop Losses**: Optimal stop distances based on asset volatility
- **2:1 Minimum RRR**: Reward-to-Risk ratio ensures mathematical edge
- **Circuit Breakers**: Automatic shutdown on excessive losses or drawdowns
- **Position Sizing**: Dynamic sizing based on trade quality and account risk

### 📊 Trading Strategies
- **Trend Following (75%)**: Buy dips in uptrends, sell rallies in downtrends
- **Breakout Trading (20%)**: Capitalize on pattern breakouts with volume confirmation
- **Liquidation Capture (5%)**: Profit from extreme market moves and cascading liquidations

### 🔧 Technical Features
- **3D Charting**: Multi-timeframe analysis (Daily/15min/5min)
- **Real-time Monitoring**: Continuous position and risk monitoring
- **Comprehensive Logging**: Detailed trade journal with performance metrics
- **Async Execution**: High-performance async order execution
- **Modular Architecture**: Clean separation of concerns for maintainability

## Installation

### Prerequisites
- Python 3.8+
- pip package manager

### Setup
```bash
# Clone repository
git clone <repository-url>
cd trading-bot

# Install dependencies
pip install -r requirements.txt

# Copy configuration template
cp config.yaml.example config.yaml

# Edit configuration with your settings
nano config.yaml
```

### Dependencies
```
pydantic>=2.0.0      # Data validation
pyyaml>=6.0.0        # Configuration files
loguru>=0.7.0        # Logging
pandas>=2.0.0        # Data analysis
pytest>=8.0.0        # Testing
```

## Configuration

### Basic Configuration
```yaml
account:
  initial_balance: 1000.0
  currency: "USDC"
  mode: "paper"  # "live" or "paper"

position_sizing:
  default_leverage: 15
  max_margin_per_trade: 200.0

api:
  testnet: true
  # Add your API credentials for live trading
```

### Environment Variables
```bash
export BOT_CONFIG_PATH="config.yaml"
export BOT_API_KEY="your_api_key"
export BOT_API_SECRET="your_api_secret"
```

## Usage

### Paper Trading (Recommended First)
```bash
# Run in paper trading mode
python main.py --testnet --config config.yaml
```

### Live Trading (After Thorough Testing)
```bash
# Run in live mode with API credentials
python main.py --api-key YOUR_KEY --api-secret YOUR_SECRET
```

### Command Line Options
```
--config PATH        Path to configuration file
--api-key KEY        Exchange API key
--api-secret SECRET  Exchange API secret
--testnet            Use testnet (default: True)
--log-level LEVEL    Logging level (default: INFO)
```

## Architecture

### Core Modules

#### `config.py`
- **Purpose**: Configuration management with Pydantic validation
- **Features**: Environment variable overrides, schema validation
- **Key Classes**: `BotConfig`, `AccountConfig`, `StrategyConfig`

#### `models.py`
- **Purpose**: Data structures for trades, positions, signals
- **Features**: Type-safe dataclasses with validation
- **Key Classes**: `Trade`, `Position`, `Signal`, `Account`

#### `risk.py`
- **Purpose**: Risk management and validation
- **Features**: Position sizing, stop-loss validation, circuit breakers
- **Key Functions**: `validate_trade_risk()`, `calculate_position_size()`

#### `strategy.py`
- **Purpose**: Trading strategy implementations
- **Features**: Multi-timeframe analysis, pattern recognition
- **Key Classes**: `TrendFollowingStrategy`, `BreakoutStrategy`

#### `execution.py`
- **Purpose**: Order execution and exchange integration
- **Features**: Async order management, position monitoring
- **Key Classes**: `ExecutionEngine`, `PacificaExchange`

#### `journal.py`
- **Purpose**: Trade logging and performance tracking
- **Features**: Performance metrics, CSV export
- **Key Classes**: `TradeJournal`, `PerformanceAnalyzer`

#### `main.py`
- **Purpose**: Main application loop
- **Features**: Signal processing, position monitoring, graceful shutdown

## Risk Management Rules

### Position Sizing
- **Standard Setup**: $100 margin (10% of account)
- **High Conviction**: $150 margin (15% of account)
- **Exceptional Setup**: $200 margin (20% of account)
- **Maximum per Trade**: 20% of account as margin

### Stop Losses
- **BTC/ETH**: 2.5-5% range (optimal 3-4%)
- **Large Cap Alts**: 3-6% range (optimal 4-5%)
- **Small Cap/Meme**: 4-7% range (optimal 5-6%)

### Risk Limits
- **Per Trade**: Maximum 8% account risk
- **Daily Loss**: $200 (20% of $1000 account)
- **Drawdown**: 25% from peak triggers circuit breaker
- **Consecutive Losses**: 3 losses triggers break

### Circuit Breakers
- **Daily Loss Limit**: Cease trading for 24 hours
- **Consecutive Losses**: 4-hour break, 50% size reduction
- **Drawdown**: Immediate cessation, full review required
- **Win Rate Collapse**: Intensive review if <30% over 20 trades

## Trading Strategies

### Trend Following
**Allocation**: 75% of trades
**Logic**: Trade in direction of established trends
**Entry**: Pullbacks to support in uptrends, rallies to resistance in downtrends
**Confirmation**: Volume exhaustion, RSI divergence

### Breakout Trading
**Allocation**: 20% of trades
**Logic**: Enter on decisive pattern breakouts
**Patterns**: Triangles, rectangles, flags
**Confirmation**: Volume 1.5-2x average, candle close beyond boundary

### Liquidation Capture
**Allocation**: 5% of trades
**Logic**: Profit from extreme market moves
**Triggers**: 3%+ price movement in 15 minutes, volume spikes
**Confirmation**: RSI extremes, Bollinger Band breaks

## Performance Monitoring

### Key Metrics
- **Win Rate**: Target >45%
- **Average RRR**: Target ≥2:1
- **Expectancy**: Target >0.5R per trade
- **Profit Factor**: Target >1.5
- **Max Drawdown**: Target <30%

### Reporting
- **Daily Stats**: Trades, P&L, win rate
- **Weekly Review**: Performance vs targets
- **Monthly Analysis**: Strategy effectiveness
- **Quarterly Review**: Major adjustments

## Testing

### Unit Tests
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_config.py

# Run with coverage
pytest --cov=. --cov-report=html
```

### Backtesting
```bash
# Run backtest on historical data
python backtest.py --data historical_data.csv --config config.yaml
```

### Paper Trading
Always validate strategies on paper trading before live deployment:
```bash
python main.py --testnet --config config.yaml
```

## Logging

### Log Files
- `logs/debug.log`: Detailed debug information
- `logs/errors.log`: Error messages and exceptions
- `logs/trade_journal.json`: Complete trade history

### Log Levels
- **DEBUG**: Detailed execution information
- **INFO**: General operational messages
- **WARNING**: Potential issues
- **ERROR**: Errors requiring attention
- **CRITICAL**: Critical failures

## Troubleshooting

### Common Issues

#### Connection Problems
```
ERROR: Failed to connect to exchange
```
- Check API credentials
- Verify network connectivity
- Confirm exchange status

#### Risk Violations
```
WARNING: Risk management rule violated
```
- Review position sizing calculations
- Check stop-loss distances
- Verify account balance

#### Strategy Errors
```
ERROR: Signal generation failed
```
- Check market data availability
- Verify indicator calculations
- Review strategy parameters

### Emergency Shutdown
If the bot encounters critical errors:
1. Automatic emergency shutdown activates
2. All positions are closed
3. Detailed error logs are generated
4. Manual review required before restart

## Development

### Code Style
- **Type Hints**: All functions use type annotations
- **Docstrings**: Google-style documentation
- **Linting**: Black formatting, flake8 compliance
- **Testing**: 100% test coverage target

### Contributing
1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For questions, issues, or contributions:
- Create an issue on GitHub
- Check the troubleshooting section
- Review the configuration examples

## Changelog

### v1.0.0
- Initial production release
- Complete risk management implementation
- All three trading strategies
- Comprehensive testing suite
- Full documentation

---

**Remember**: Trading involves risk. This bot implements best practices but cannot guarantee profits. Always trade responsibly.