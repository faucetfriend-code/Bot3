<objective>
Enhance WebSocket data management and persistence to resolve insufficient historical data issues that are blocking strategy signal generation. This is a Priority 2 high issue where multiple symbols (SUI, ADA, AVAX, BTC, ETH, LTC, LINK, DOGE, WLD, SOL) show "WS SYNC MISS: insufficient data: 0-3 < 50" warnings.

The diagnostic report identified that most symbols lack the required 50+ candles for analysis, causing strategies to skip markets due to data quality issues. This severely compromises the bot's ability to generate trading signals across all supported assets.
</objective>

<context>
This is a trading bot v2 system with WebSocket-based real-time market data collection. The system subscribes to multiple timeframes (1m, 5m, 15m, 1h, 4h) for various symbols and requires sufficient historical data for technical analysis and signal generation.

The system uses:
- Pacifica WebSocket client for real-time data streams
- Candle data subscriptions for multiple timeframes  
- Data persistence and caching mechanisms
- Technical indicators requiring historical data (RSI, MACD, ATR, etc.)
- Market regime detection using ADX and volatility analysis

@trading_bot_v2/pacifica_ws_client.py
@trading_bot_v2/multi_timeframe_fetcher.py
@trading_bot_v2/market_regime.py

The insufficient data warnings indicate that the WebSocket data collection is not maintaining adequate historical data buffers. When strategies attempt to analyze markets, they find insufficient candles and skip trading opportunities, significantly reducing the bot's trading coverage and potential profitability.
</context>

<requirements>
1. Improve candle data persistence to maintain longer historical buffers
2. Enhance data retention periods to ensure minimum 50+ candles per timeframe
3. Add data quality validation before strategy execution
4. Implement fallback mechanisms for missing timeframes
5. Optimize WebSocket data processing and storage efficiency
6. Add monitoring and alerting for data sufficiency levels
7. Create data restoration procedures for symbols with insufficient data
8. Ensure all 8 tracked symbols have adequate data for analysis

The fix must handle:
- Multiple timeframes (1m, 5m, 15m, 1h, 4h)
- Real-time data ingestion without losing historical context
- Memory-efficient storage of candle data
- Data validation and quality checks
- Error recovery and data gap filling
- Integration with existing technical indicator calculations

Why this matters: All 8 trading strategies depend on sufficient historical data for technical analysis. Without adequate data buffers, the bot cannot generate signals, detect market regimes, or execute informed trading decisions. This fundamentally limits the trading system's effectiveness.
</requirements>

<implementation>
Thoroughly analyze the WebSocket data management system in pacifica_ws_client.py and related data handling modules. Focus on:

1. **Data Retention Analysis**: Review current candle data storage and retention policies
2. **Buffer Management**: Examine how historical data is maintained in memory
3. **Data Loss Investigation**: Identify why candles are not being retained adequately
4. **Persistence Issues**: Review data saving mechanisms and disk caching
5. **Data Quality Checks**: Assess current validation for data sufficiency
6. **Memory Management**: Optimize data structures for efficient storage

Enhancement strategies to implement:
1. **Extended Data Retention**: Increase minimum buffer to 200+ candles per timeframe
2. **Persistent Storage**: Save candle data to disk/database for recovery
3. **Data Validation**: Add checks before strategy execution
4. **Gap Detection**: Monitor and alert on insufficient data situations
5. **Background Recovery**: Automatically refill data gaps when detected
6. **Efficient Indexing**: Optimize data structures for quick access
7. **Fallback Mechanisms**: Use REST API to fill critical gaps when needed

What to avoid:
- Changing WebSocket message parsing or subscription logic
- Modifying the core technical indicator calculations
- Breaking existing data structures used by strategies
- Removing existing real-time data processing
- Making major architectural changes without testing

Why these constraints matter: The WebSocket data collection is working correctly - it's receiving real-time data. The issue is in data retention and persistence, so we should focus on enhancing the storage and management of received data rather than changing the collection mechanism.
</implementation>

<output>
Modify the file: `trading_bot_v2/pacifica_ws_client.py`

Key enhancements required:

1. **Extended data retention** (increase buffer sizes):
```python
# Extend minimum candle retention
MIN_CANDLES_REQUIRED = 200  # Increase from 50
DATA_RETENTION_HOURS = 48   # Keep 2 days of data

class CandleDataBuffer:
    def __init__(self, symbol, timeframe, max_size=MIN_CANDLES_REQUIRED):
        self.symbol = symbol
        self.timeframe = timeframe
        self.max_size = max_size
        self.candles = deque(maxlen=max_size)
        self.last_updated = None
```

2. **Enhanced persistence and recovery**:
```python
async def save_candle_data_to_cache(self, symbol, timeframe, candles):
    """Save candle data to disk cache for recovery"""
    cache_file = f"data/candle_cache_{symbol}_{timeframe}.json"
    data = {
        'symbol': symbol,
        'timeframe': timeframe,
        'candles': [c.__dict__ for c in candles],
        'last_updated': datetime.utcnow().isoformat()
    }
    
    with open(cache_file, 'w') as f:
        json.dump(data, f)

async def load_candle_data_from_cache(self, symbol, timeframe):
    """Load cached candle data on startup"""
    cache_file = f"data/candle_cache_{symbol}_{timeframe}.json"
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            data = json.load(f)
        return data['candles']
    return []
```

3. **Data quality validation**:
```python
def validate_data_sufficiency(self, symbol, timeframe, required_candles=MIN_CANDLES_REQUIRED):
    """Check if symbol has sufficient data for analysis"""
    if symbol not in self.candle_buffers:
        return False, f"No data buffer for {symbol}"
    
    buffer = self.candle_buffers[symbol][timeframe]
    if len(buffer.candles) < required_candles:
        return False, f"Insufficient data: {len(buffer.candles)} < {required_candles}"
    
    return True, "Data sufficient"

def get_data_sufficiency_report(self):
    """Generate report of data sufficiency for all symbols"""
    report = {}
    for symbol in self.monitored_symbols:
        report[symbol] = {}
        for timeframe in self.timeframes:
            sufficient, message = self.validate_data_sufficiency(symbol, timeframe)
            report[symbol][timeframe] = {
                'sufficient': sufficient,
                'candle_count': len(self.candle_buffers.get(symbol, {}).get(timeframe, CandleDataBuffer()).candles),
                'message': message
            }
    return report
```

4. **Background data recovery**:
```python
async def background_data_recovery(self):
    """Recover missing data for symbols with insufficient data"""
    while self.running:
        report = self.get_data_sufficiency_report()
        
        for symbol in self.monitored_symbols:
            for timeframe in self.timeframes:
                if not report[symbol][timeframe]['sufficient']:
                    logger.warning(f"Data gap detected for {symbol} {timeframe}, initiating recovery")
                    # Use REST API to fill gap
                    await self.fill_data_gap_rest(symbol, timeframe)
        
        await asyncio.sleep(300)  # Check every 5 minutes
```

Focus on:
- Candle data buffer management
- Data persistence to disk cache
- Data sufficiency validation
- Background gap recovery
- Integration with existing WebSocket message handling
- Monitoring and alerting for data quality
</output>

<verification>
Before declaring complete, verify your work:

1. **Data Retention Test**: Confirm minimum 200 candles are retained per timeframe
2. **Persistence Test**: Verify candle data is saved and loaded from cache
3. **Quality Validation**: Test data sufficiency checks for all symbols
4. **Recovery Test**: Confirm background data recovery fills gaps
5. **Integration Test**: Ensure strategies can access sufficient historical data
6. **Performance Test**: Verify enhanced data management doesn't impact real-time processing
7. **Monitoring Test**: Check data sufficiency reporting and alerting

Test data sufficiency:
```python
# The enhanced system should provide:
# - Minimum 200 candles per timeframe for all symbols
# - Automatic gap detection and recovery
# - Persistent storage for data recovery after restart
# - Real-time monitoring of data quality
# - No more "insufficient data" warnings for tracked symbols
```

</verification>

<success_criteria>
1. Zero "WS SYNC MISS: insufficient data" warnings for tracked symbols
2. All 8 symbols maintain minimum 200+ candles per timeframe
3. Data persistence system working (save/load from cache)
4. Background gap recovery successfully fills data holes
5. Data quality validation working for all timeframes
6. Strategies can access sufficient historical data for analysis
7. Real-time monitoring of data sufficiency implemented
8. No regression in WebSocket real-time data processing
</success_criteria>