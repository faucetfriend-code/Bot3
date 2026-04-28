# VWAP Trade Analysis Report (2020-2024)

## Executive Summary

The VWAP strategy generates **54.1% win rate** with positive returns (~67% total P&L). Key finding: **Most exits are via trailing stop (56%)**, not fixed TP/SL. This explains why the strategy is profitable despite TP rarely being hit.

## Overall Performance

| Metric | Value |
|--------|-------|
| Total Trades | 464 |
| Wins | 251 (54.1%) |
| Losses | 213 |
| Avg Win | +0.59% |
| Avg Loss | -0.38% |
| Total P&L | +67.31% |

---

## Entry Analysis

### Entry Type Breakdown

| Entry Type | Trade Count | Win Rate |
|-----------|-----------|---------|
| bull_pullback | ~350 | 51.6% |
| bear_pullback | ~110 | 65.1% |

**Key Insight**: Bear pullbacks (shorting in downtrends) have HIGHER win rate than bull pullbacks.

### VWAP Band Position at Entry

| Band | Trade Count | Win Rate |
|------|-----------|---------|
| -2SD | ~5 | 100% |
| +2SD | ~5 | 100% |
| -1SD | ~60 | 43.3% |
| +1SD | ~10 | 100% |
| below_vwap | ~85 | 64.9% |
| above_vwap | ~30 | 66.7% |

**Key Insights**:
1. Entries at extreme bands (-2SD/+2SD) have 100% win rate (but few samples)
2. Entries in the middle of VWAP (+1SD, above_vwap, below_vwap) work better than -1SD
3. -1SD entries underperform - this is counterintuitive

### Trend Direction at Entry

| Trend | Trade Count | Win Rate |
|-------|-----------|---------|
| Bull | ~350 | 51.6% |
| Bear | ~110 | 65.1% |

**Key Insight**: Short trades in bear markets work better than long trades in bull markets.

### Time of Day (Hour Buckets)

| Hour Range | Win Rate |
|-----------|---------|
| 08:00-12:00 | 48.3% |
| 12:00-16:00 | 54.9% |
| 16:00-20:00 | 57.0% |
| 20:00-24:00 | 57.8% |

**Key Insight**: Afternoon/evening sessions (16:00-24:00 UTC) perform best - likely when US markets are most active.

---

## Exit Analysis

### Exit Reason Breakdown

| Exit Reason | Count | Percentage |
|------------|-------|-----------|
| Trailing Stop | 261 | 56.3% |
| SL | 168 | 36.2% |
| TP | 31 | 6.7% |
| Time Expired | 4 | 0.9% |

**Critical Finding**: 
- Only 6.7% of trades hit TP (the intended target)
- 36.2% hit SL
- **56.3% exit via trailing stop** - this is why the strategy is profitable!

### Time in Trade

| Metric | Bars (5m) |
|--------|-----------|
| Average | 7.1 |
| Winners | 9.2 |
| Losers | 4.6 |

**Key Insight**: Winners stay in ~2x longer than losers. Quick exits (within 1-2 bars) are typically losers.

---

## Winning vs Losing Trade Comparison

### What Winners Do Differently

1. **Stay in longer**: 9.2 bars vs 4.6 bars average
2. **More likely in bear markets**: 65% win rate for shorts
3. **Trailing stop captures profit**: Most winners exit via trailing, not TP

### What Losers Do Differently

1. **Exit quickly**: Average 4.6 bars (~23 minutes on 5m)
2. **Bull trades underperform**: 51.6% for longs
3. **-1SD band underperforms**: Only 43.3% win rate

---

## THE Winning Combination

Based on this analysis, the exact combination that works:

### Entry Conditions (OPTIMAL):
```
1. Entry Type: bear_pullback (short) OR bull_pullback (long)
2. VWAP Band: at extremes (-2SD/+2SD) OR near VWAP (above_vwap/below_vwap) - AVOID -1SD
3. Trend: MUST be aligned with 1h trend
4. Time: 16:00-24:00 UTC (US session)
5. Reversal candle: Must have wick past band then close back inside
```

### Exit Strategy (OPTIMAL):
```
1. Use trailing stop (1.2 ATR) - NOT fixed TP/SL
2. Let winners run - 9+ bars average for winners
3. Cut losers quick - 4-5 bars max
```

### Why It Works:
- The trailing stop allows profits to run while limiting losses
- Most "wins" are actually trailing stop exits at profit
- Only 6.7% hit fixed TP but strategy still makes +67%
- This is the OPPOSITE of the intended design (targeting mean reversion to VWAP)

---

## Recommendations

### 1. Fix the Exit Strategy
The strategy targets TP at VWAP (mean reversion) but exits via trailing stop. Either:
- A) Embrace trailing as primary exit
- B) Use tighter TP (closer to 1x ATR vs 3.5x ATR)

### 2. Focus on Bear Pullbacks
Short entries in downtrends have 65% win rate vs 52% for longs. Consider:
- Skipping bull_pullback in bull markets
- Adding strength filter for longs

### 3. Avoid -1SD Band Entries
-1SD entries underperform (43.3% win rate) vs other bands. Likely because:
- Not enough deviation to mean revert
- More likely to continue against you

### 4. Time Filtering
- Best performance: 16:00-24:00 UTC
- Consider tightening session filter to just NY hours

### 5. Position Sizing
With 54% win rate but small avg win (0.59%) vs loss (-0.38%), position sizing should favor winners to maintain positive expectancy.