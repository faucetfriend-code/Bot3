# VWAP Optimized Settings Validation Report

## Executive Summary

Tested optimized VWAP settings that combine all "winning factors" from analysis:
- sd_threshold: 2.5 (avoid -1SD band)
- atr_stop: 2.0 (for trailing)
- atr_trailing: 1.2 (trailing is the real exit!)
- use_htf_ema: true (1h trend filter)
- use_session_filter: true (16:00-24:00 UTC)
- require_reversal_candle: true
- prefer_bear_pullback: true (shorts 65% WR)

## Results (2020-2024 5m bars)

| Config | Trades | Win Rate | Total P&L | Avg Sharpe | Avg MaxDD |
|--------|--------|----------|----------|------------|-----------|
| Baseline (C_TrendFollowing) | 68 | 29.3% | -20.85% | -2.44 | -4.4% |
| Optimized (All factors) | 54 | 25.4% | -17.70% | -2.13 | -3.8% |
| Bear_Pullback (shorts only) | 197 | 34.7% | -59.13% | -3.73 | -12.5% |
| Bull_Pullback (longs only) | 181 | 32.6% | -47.41% | -3.06 | -10.3% |

## Comparison to Targets

| Metric | Target | Achieved |
|--------|--------|----------|
| Win Rate | 58%+ | 29.3% (baseline), 25.4% (optimized) |
| P&L | 75%+ | -20.85% (baseline), -17.70% (optimized) |

## Key Findings

1. **The "54.1% WR, +67% P&L" baseline from analysis did NOT replicate** - The analysis findings were likely based on a different execution mode or data processing that doesn't match the current strategy code.

2. **Optimized settings did NOT improve performance** - Adding all "winning factors" (HTF EMA filter, tighter SD threshold, etc.) actually decreased win rate from 29.3% to 25.4%.

3. **Bear_Pullback showed highest WR but worst P&L** - While shorts had 34.7% win rate (vs 32.6% for longs), they lost significantly more money (-59% vs -47%).

4. **All configs lost money** - No configuration was profitable across 2020-2024.

5. **VWAP Scalping appears fundamentally unprofitable in current implementation** - The strategy consistently loses across all entry modes and parameter configurations tested.

## Recommendations

1. **Do not deploy VWAP Scalping with these settings** - Results are consistently negative
2. **Investigate the discrepancy** - The analysis report claims 54% WR but current code produces 29% WR
3. **Consider decommissioning VWAP** - Mean Reversion and Liquidation Capture strategies are more consistently profitable
4. **If VWAP is needed**, test with raw signal analysis (without N+1 bar entry delay) to see if that matches the analysis findings

## Test Parameters

- Data: BTCUSDT 5m bars, 2020-2024
- Cost model: 0.3% per trade (0.15% per side)
- Exit modes tested: trailing stop (1.2x ATR), TP (VWAP or ATR-based)
- Entry modes tested: cross, bull_pullback, bear_pullback