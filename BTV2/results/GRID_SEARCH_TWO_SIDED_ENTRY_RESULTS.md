# GRID SEARCH RESULTS - TWO-SIDED ENTRY MODES
## Comprehensive Backtest Analysis 2023-2025

---

## Executive Summary

**Test Period:** 2023-01-01 to 2026-01-01  
**Total Combinations Tested:** 72  
**Entry Modes:** mean_reversion, cross  
**Parameter Grid:** SD (2.5, 3.0, 3.5) × ATR Stop (1.5, 2.0, 2.5) × TP Mode (atr, pdh) × Trailing ATR (1.2, 1.5)

---

## Key Findings

### 1. Entry Mode Comparison

| Metric | Mean Reversion | Cross |
|--------|-----------------|-------|
| Avg Win Rate | **32.4%** | 28.9% |
| Avg Net % | **-26.5%** | -31.5% |
| Best WR | **42.7%** | 32.3% |
| Total Trades | **4,824** | 4,572 |

**Winner:** Mean Reversion - Higher WR and better net returns

### 2. WR >= 45% Target

**Result:** No combinations achieved WR >= 45% across all regimes  
**Best Achieved:** 42.7% with mean_reversion SD=3.0, ATR=1.5, TP=pdh, Trl=1.5

### 3. SD Threshold Impact

| SD | Avg WR | Max WR | Trades |
|----|--------|--------|--------|
| 2.5 | 30.6% | 34.3% | 5,127 |
| **3.0** | **33.8%** | **42.7%** | 2,517 |
| 3.5 | 27.6% | 32.3% | 1,752 |

**Optimal:** SD=3.0 - Best balance of WR and trade frequency

### 4. Trailing ATR Impact

| Trailing | Avg WR | Best WR |
|----------|--------|---------|
| 1.2 | 29.7% | 36.1% |
| **1.5** | **31.6%** | **42.7%** |

**Optimal:** Trailing ATR=1.5 - Tighter trailing improves WR

### 5. TP Mode Impact

| TP Mode | Avg WR | Best WR |
|---------|--------|---------|
| **atr** | **31.7%** | 39.8% |
| pdh | 29.6% | 42.7% |

**Optimal:** TP Mode=atr - More consistent WR

---

## Top 10 Combinations (by Win Rate)

| # | Entry Mode | SD | ATR | TP | Trl | WR% | Net% | Trades |
|---|------------|----|-----|----|-----|-----|------|--------|
| 1 | mean_reversion | 3.0 | 1.5 | pdh | 1.5 | 42.7 | -13.8 | 82 |
| 2 | mean_reversion | 3.0 | 2.0 | pdh | 1.5 | 42.7 | -13.8 | 82 |
| 3 | mean_reversion | 3.0 | 2.5 | pdh | 1.5 | 42.7 | -13.8 | 82 |
| 4 | mean_reversion | 3.0 | 1.5 | atr | 1.5 | 39.8 | -17.0 | 83 |
| 5 | mean_reversion | 3.0 | 2.0 | atr | 1.5 | 39.8 | -17.0 | 83 |
| 6 | mean_reversion | 3.0 | 2.5 | atr | 1.5 | 39.8 | -17.0 | 83 |
| 7 | mean_reversion | 3.0 | 1.5 | atr | 1.2 | 36.1 | -17.0 | 83 |
| 8 | mean_reversion | 3.0 | 1.5 | pdh | 1.2 | 36.1 | -15.7 | 83 |
| 9 | mean_reversion | 3.0 | 2.0 | atr | 1.2 | 36.1 | -17.0 | 83 |
| 10 | mean_reversion | 3.0 | 2.0 | pdh | 1.2 | 36.1 | -15.7 | 83 |

---

## Top 10 Combinations (by Net Return)

| # | Entry Mode | SD | ATR | TP | Trl | WR% | Net% | Trades |
|---|------------|----|-----|----|-----|-----|------|--------|
| 1 | mean_reversion | 3.5 | 1.5 | pdh | 1.2 | 26.3 | -4.3 | 19 |
| 2 | mean_reversion | 3.5 | 2.0 | pdh | 1.2 | 26.3 | -4.3 | 19 |
| 3 | mean_reversion | 3.5 | 2.5 | pdh | 1.2 | 26.3 | -4.3 | 19 |
| 4 | mean_reversion | 3.5 | 1.5 | atr | 1.2 | 26.3 | -4.4 | 19 |
| 5 | mean_reversion | 3.5 | 2.0 | atr | 1.2 | 26.3 | -4.4 | 19 |
| 6 | mean_reversion | 3.5 | 2.5 | atr | 1.2 | 26.3 | -4.4 | 19 |
| 7 | mean_reversion | 3.5 | 1.5 | pdh | 1.5 | 26.3 | -5.2 | 19 |
| 8 | mean_reversion | 3.5 | 2.0 | pdh | 1.5 | 26.3 | -5.2 | 19 |
| 9 | mean_reversion | 3.5 | 2.5 | pdh | 1.5 | 26.3 | -5.2 | 19 |
| 10 | mean_reversion | 3.5 | 1.5 | atr | 1.5 | 26.3 | -5.3 | 19 |

---

## Recommendations

### For Maximum Win Rate (WR > 40%):
```
Entry Mode: mean_reversion
SD Threshold: 3.0
ATR Stop: 1.5
TP Mode: pdh
Trailing ATR: 1.5
```
**Expected:** WR=42.7%, Net=-13.8%, Trades=82

### For Best Net Returns (Minimal Loss):
```
Entry Mode: mean_reversion
SD Threshold: 3.5
ATR Stop: 1.5
TP Mode: pdh
Trailing ATR: 1.2
```
**Expected:** WR=26.3%, Net=-4.3%, Trades=19

### Best Balance (WR + Net):
```
Entry Mode: mean_reversion
SD Threshold: 3.0
ATR Stop: 2.0
TP Mode: atr
Trailing ATR: 1.5
```
**Expected:** WR=39.8%, Net=-17.0%, Trades=83

---

## Conclusions

1. **Mean Reversion dominates** over Cross entry for both WR and Net returns
2. **SD=3.0** is optimal - best WR (42.7%) with reasonable trades
3. **Trailing ATR=1.5** improves WR vs tighter 1.2
4. **TP Mode=atr** more consistent than pdh for WR
5. **No combination achieved WR >= 45%** with positive returns
6. **Net returns are negative** - need further optimization of risk parameters

## Next Steps

1. Test regime-specific parameters (RANGING vs TRENDING separately)
2. Consider tighter stops to improve net returns
3. Test higher SD values (4.0+) for even more selective entries
4. Explore hybrid entry modes that switch based on regime

---
*Generated: 2026-04-16*
*Scripts: grid_search_two_sided_entry_fast.py, grid_search_two_sided_entry.py*
