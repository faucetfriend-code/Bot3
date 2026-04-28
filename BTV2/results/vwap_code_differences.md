# VWAP Code Differences Analysis
## What Made the Original +240% RANGING Results?

### Executive Summary

The original backtest that achieved **+240%** on RANGING regime with `mean_reversion` mode cannot be reproduced with the current `strategies.py`. This document analyzes the key differences.

---

## 1. Parameter Differences

### Original (from vwap_regime_entry_matrix.py - the one that got +240%)

```python
PARAMS = {
    "cutoff": 0.10,
    "sd_threshold": 3.0,
    "atr_stop": 2.0,
    "trailing_atr": 1.5,
    "adx_max": 30.0,
    "volume_mult": 1.0,
    "rsi_max": 55.0,
    "entry_mode": "mean_reversion",
    "use_htf_vwap": False,
    "use_htf_ema": False,
    "use_stoch_filter": False,
    "use_anchored_vwap": True,
    "use_session_filter": False,
    "require_reversal_candle": False,
    "tp_mode": "atr",
}
```

### Current (strategies.py defaults)

| Parameter | Original | Current Default |
|-----------|----------|-----------------|
| sd_threshold | 3.0 | 3.0 |
| atr_stop | 2.0 | 1.0 |
| trailing_atr | 1.5 | 1.2 |
| adx_max | 30.0 | 25.0 |
| volume_mult | 1.0 | 1.2 |
| rsi_max | 55.0 | 50.0 |
| use_htf_ema | False | True |
| use_session_filter | False | True |

---

## 2. Key Logic Differences

### 2.1 Cost Model (PRIMARY DIFFERENCE!)

**analyze_vwap_trade_details.py (the +240% one):**
```python
# Line 282: Only applies cost on exit!
curr_equity *= (1 + profit * 0.9997)  # 0.03% cost
```

**strategies.py (current):**
```python
COST_PER_SIDE = 0.0015  # 0.15% per side = 0.30% round trip!

# Applied on entry AND exit:
curr_equity *= (1.0 - COST_PER_SIDE)  # Entry
curr_equity *= (1.0 - COST_PER_SIDE)  # Exit
```

**Impact:** The original had ~0.03% per trade vs current 0.30% = **10x difference!**

### 2.2 Session Filter

**Original (+240%):**
```python
use_session_filter = False  # No session filter!
```

**Current:**
```python
use_session_filter = True   # Default is True (8:00-22:00 UTC)
```

### 2.3 HTF Filters

**Original (+240%):**
```python
use_htf_ema = False  # NO higher timeframe filtering!
```

**Current:**
```python
use_htf_ema = True   # Default is True
htf_adx_max = 30.0
```

### 2.4 Exit Logic

**Original had time-based exit:**
```python
bars_in_trade = i - signal_bar_idx[0]
if bars_in_trade > 50 and exit_reason is None:
    exit_reason = "time_expired"
```

**Current: NO time-based exit!**

---

## 3. What Was the Secret Sauce?

Based on the analysis, the original +240% likely came from:

1. **NO SESSION FILTER** - Trading all hours
2. **NO HTF FILTERS** - Pure 5m mean reversion  
3. **VERY LOW COSTS** - The 0.9997 multiplier (~0.03%) is 10x lower than current 0.30%
4. **Different ATR stops** - Original used atr_stop=2.0 vs current 1.0
5. **Time-based exit** - Original had 50-bar timeout

---

## 4. Why Current Code Produces Negative Results

From vwap_ranging_test_report.txt:
- RANGING + sd=3.0 + mean_reversion: **-58.6%** (not +240%)
- RANGING + sd=0.5 + cross: **-66.3%**

The differences that hurt:
1. Session filter removes ~58% of bars
2. HTF filters add additional restrictions
3. Full cost model (0.30% vs 0.03%)
4. Tighter default stops (1.0 vs 2.0)

---

## 5. Summary Table

| Parameter | Original (+240%) | Current (negative) | Impact |
|-----------|-----------------|-------------------|--------|
| use_session_filter | False | True | -58% bars |
| use_htf_ema | False | True | Extra restriction |
| COST_PER_SIDE | ~0.0003 | 0.0015 | 5x higher |
| atr_stop | 2.0 | 1.0 | Tighter SL |
| volume_mult | 1.0 | 1.2 | Stricter |
| rsi_max | 55 | 50 | Less permissive |
| adx_max | 30 | 25 | Stricter |
| Time exit | 50 bars | None | No timeout |

---

## 6. To Reproduce +240%

You would need to:

1. Set `use_session_filter = False`
2. Set `use_htf_ema = False`
3. Use `atr_stop = 2.0` (not 1.0)

**NOTE:** The original code has a BUG in cost calculation that significantly underestimated trading costs. This is likely the primary reason the +240% result is unattainable.

---

Generated: 2026-04-14
