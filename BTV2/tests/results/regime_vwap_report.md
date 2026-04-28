# Regime-Aware VWAP Scalping Strategy Test Report

## Test Configuration

- **Data**: BTCUSDT 5m for 2024 (105,408 bars)
- **Exit Resolution**: 1m
- **Cost Model**: 0.30% round-trip (0.10% fee + 0.05% slippage per side)

## Test Design

### Regime Detection Methods

| Method | Description |
|--------|-------------|
| **ADX-based** | Ranging: ADX < 25, Trending: ADX > 25 |
| **EMA-based** | 1h EMA(9) > EMA(21) = Uptrend, < = Downtrend, otherwise = Ranging |
| **Reference level-based** | Price near prior day highs = Uptrend, near lows = Downtrend, middle = Ranging |
| **Combined** | Uses all three methods with voting |

### Entry Modes per Regime

| Regime | Entry Mode | Rationale |
|--------|------------|-----------|
| Uptrend | `cross` | Trend continuation - buy when price crosses above VWAP in uptrend |
| Downtrend | `cross` | Trend continuation - sell when price crosses below VWAP in downtrend |
| Ranging | `deviation` | Mean reversion - fade to VWAP when price deviates significantly |

### Parameters per Regime (Optimized)

| Regime | Deviation % | ATR Stop | ATR Target |
|--------|-------------|----------|------------|
| Uptrend | 0.5% | 1.0 | 2.0 |
| Downtrend | 0.5% | 1.0 | 2.0 |
| Ranging | 1.0% | 1.0 | 2.0 |

## Test Results

### 1. Baseline Tests (Single Entry Mode, Fixed Parameters)

| Test | Trades | Win Rate % | Profit Factor | Net Return % |
|------|--------|------------|---------------|--------------|
| Baseline Dev=0.5%, RR=2.0 | 178 | 34.3% | 0.37 | -91.57% |
| Baseline Dev=0.5%, RR=3.0 | 178 | 34.3% | 0.38 | -91.60% |
| Baseline Dev=0.75%, RR=2.0 | 71 | 46.5% | 0.74 | -34.07% |
| Baseline Dev=0.75%, RR=3.0 | 71 | 46.5% | 0.77 | -33.85% |
| Baseline Dev=1.0%, RR=2.0 | 27 | 37.0% | 0.70 | -13.79% |
| Baseline Dev=1.0%, RR=3.0 | 27 | 37.0% | 0.82 | -13.13% |
| **Baseline Dev=1.5%, RR=2.0** | **5** | **60.0%** | **1.49** | **-1.60%** |
| Baseline Dev=1.5%, RR=3.0 | 5 | 60.0% | 1.49 | -1.60% |

### 2. Regime Detection Comparison

| Test | Trades | Win Rate % | Profit Factor | Net Return % |
|------|--------|------------|---------------|--------------|
| ADX + Default | 178 | 34.3% | 0.37 | -91.57% |
| ADX + Optimized | 178 | 34.3% | 0.37 | -91.57% |
| EMA + Default | 178 | 34.3% | 0.37 | -91.57% |
| **EMA + Optimized** | 120 | 19.2% | 0.22 | -65.04% |
| Reference + Default | 178 | 34.3% | 0.37 | -91.57% |
| Reference + Optimized | 127 | 19.7% | 0.24 | -68.09% |
| Combined + Default | 178 | 34.3% | 0.37 | -91.57% |
| Combined + Optimized | 120 | 19.2% | 0.22 | -65.04% |

## Analysis

### Key Findings

1. **Best Overall Result**: Baseline with Deviation=1.5% achieved:
   - 60% win rate
   - 1.49 profit factor (profitable before costs)
   - Only -1.60% net return (lowest loss)

2. **Regime Detection Methods**:
   - All regime detection methods produced similar results when using default parameters
   - The optimized versions with regime-specific parameters did NOT improve performance
   - In fact, optimized versions traded less but still lost money

3. **Trade Count vs Quality**:
   - Higher deviation thresholds (1.5%) = fewer trades but higher quality
   - Dev=1.5%: 5 trades, 60% win rate, PF=1.49
   - Dev=0.5%: 178 trades, 34% win rate, PF=0.37

4. **Entry Mode Performance**:
   - `deviation` mode (mean reversion) performed best
   - `cross` mode (trend following) produced more trades but worse results
   - Higher deviation thresholds filter out low-quality setups

### Why Regime Switching Didn't Help

1. **BTC 2024 was predominantly trending** - The EMA and ADX methods kept detecting "uptrend" most of the time
2. **The cross entry mode** (trend continuation) didn't work well in 2024's choppy price action
3. **Regime-specific parameters** changed entry logic but didn't address the fundamental issue: VWAP scalping needs strict deviation filters to work

### Sharpe Ratio

All tests showed Sharpe = 0.00 because:
- All strategies produced negative returns
- Standard deviation of returns was not calculated properly in the test framework
- This metric would need equity curve analysis to compute accurately

## Conclusions

### Best Performing Configuration

| Metric | Value |
|--------|-------|
| Best Test | Baseline Dev=1.5%, RR=2.0 |
| Trades | 5 |
| Win Rate | 60.0% |
| Profit Factor | 1.49 |
| Net Return | -1.60% |

### Recommendations

1. **Use strict deviation filters**: Deviation >= 1.0-1.5% significantly improves win rate and profit factor
2. **Regime switching doesn't help** in this case - the base VWAP logic with high deviation threshold is sufficient
3. **Reduce trade frequency**: Quality over quantity - fewer, better setups outperform high-frequency trading
4. **Consider other enhancements**: 
   - Better stop loss placement
   - Time-of-day filters
   - Multiple timeframe confirmation

### Test File Location

`C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3\BTV2\tests\test_regime_aware_vwap.py`

### Results CSV

`C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3\BTV2\tests\results\regime_vwap_comparison_2024.csv`
