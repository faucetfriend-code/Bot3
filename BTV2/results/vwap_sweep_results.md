# VWAP Scalping Parameter Sweep Results - 2024 BTCUSDT 5m

## Summary

After extensive testing of various parameter combinations, **the VWAP Scalping strategy as implemented does not meet the specified criteria** with BTCUSDT 5m data for 2024.

### Key Findings

1. **Win Rate**: Consistently 20-32% across all tested parameter combinations
2. **Trade Volume**: Can achieve 1000-7000+ trades with relaxed parameters
3. **Profitability**: All combinations show large losses

### Tested Parameter Ranges

- **SD Thresholds**: 1.0, 1.2, 1.5, 1.8, 2.0, 2.2, 2.5, 3.0, 3.5, 4.0
- **ATR Stops**: 0.3, 0.5, 0.7, 1.0, 1.5
- **ATR Targets**: 1.5, 2.0, 2.5, 3.0
- **ADX Thresholds**: 20, 25, 30, 35, 40, 50, 60, 70, 80, 100
- **RSI Thresholds**: 30, 40, 50, 60, 70, 80, 90, 99
- **Volume Multipliers**: 1.0, 1.2, 1.5, 2.0
- **Trend Filter**: True/False

### Best Results Observed

| SD | ADX | RSI | ATR | Vol | Trend | Trades | WR% | PF | Net% |
|----|-----|-----|-----|-----|-------|--------|-----|-----|------|
| 1.5 | 100 | 99 | 0.5 | 1.0 | False | 7777 | 23.2 | 0.18 | -2433% |
| 2.0 | 50 | 99 | 0.5 | 1.0 | False | 7397 | 24.2 | 0.20 | -2319% |
| 2.5 | 35 | 70 | 0.7 | 1.5 | False | 5512 | 31.0 | 0.29 | -1754% |

### Analysis

The fundamental issue is the **win rate is too low** (20-32%) compared to what's needed for profitability:
- With 2:1 R:R (ATR target=2.0, ATR stop=1.0), break-even win rate ≈ 33%
- With 0.30% costs per trade, break-even is even higher

The strategy produces many trades but loses on the majority, resulting in significant losses.

### Recommendations

1. **Different Strategy**: Consider alternatives like:
   - Mean Reversion (RSI + Bollinger Bands)
   - Momentum strategies with better signal quality
   - Grid Trading for ranging markets

2. **Higher R:R**: Try larger target-to-stop ratios (e.g., 3:1 or 4:1)

3. **Better Filters**: The current ADX/RSI/volume filters don't improve win rate enough

4. **Use Trailing Stops**: May help lock in profits on winning trades

### Conclusion

The VWAP Scalping strategy in its current form is **not suitable for BTCUSDT 5m trading in 2024** with the specified criteria. The strategy would need significant modifications to achieve >50% win rate while maintaining sufficient trade frequency.
