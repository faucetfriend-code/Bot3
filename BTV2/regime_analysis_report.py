"""
Regime Detection Issues - Root Cause Analysis
===============================================
Based on accuracy testing results:
- Average accuracy: 34.7% (catastrophically low)
- Average flips/month: 1005 (extreme oscillation)
- All months exceed oscillation threshold (>5 flips)

Key Issues Identified:
1. Missing 5m timeframe parameters → uses 15m params which are too sensitive
2. Momentum score normalization uses 10% threshold which is too volatile for 5m
3. Combined scoring uses integer rounding which causes flip-flopping
4. No hysteresis/minimum duration requirement before regime change
5. EMA separation threshold (0.05 = 5%) too tight for 5-minute data
"""

from pathlib import Path

import pandas as pd
import numpy as np


# Root cause analysis based on code review
ROOT_CAUSES = """
=== ROOT CAUSE ANALYSIS ===

ISSUE 1: Missing 5m Timeframe Parameters
---------------------------------------
Location: regime_detector.py lines 87-112
Problem: TIMEFRAME_PARAMS doesn't include "5m", defaults to 15m params
Impact: ADX thresholds (18/10) are too sensitive for 5-minute data
Fix: Add "5m" parameters with higher thresholds (e.g., ADX 25/15)

ISSUE 2: Momentum Score Normalization Too Volatile
---------------------------------------------------
Location: regime_detector.py lines 403-421
Problem: Uses 10% (0.1) as normalization threshold for 5m data
Impact: Small price moves trigger strong momentum signals
Code: momentum = momentum.clip(-0.1, 0.1) / 0.1
Fix: Use higher threshold (e.g., 0.2 = 20%) for 5m

ISSUE 3: Aggressive Integer Rounding in Combined Scoring
--------------------------------------------------------
Location: regime_detector.py lines 497-502
Problem: Weighted score rounded to nearest integer causes flip-flopping
Impact: Score of 1.4 -> 1 (BULL_WEAK), 1.6 -> 2 (BULL_STRONG)
Code: rounded = int(round(weighted)); regime_arr[i] = reverse_score[rounded]
Fix: Use fractional scoring or require minimum threshold for change

ISSUE 4: No Hysteresis or Minimum Duration
------------------------------------------
Location: regime_detector.py (entire file)
Problem: No mechanism to prevent rapid regime changes
Impact: Regime can flip back and forth within minutes
Fix: Add min_bars_required parameter (e.g., require 30 bars = 2.5 hours)

ISSUE 5: EMA Separation Threshold Too Tight
--------------------------------------------
Location: regime_detector.py lines 345-350
Problem: 5% separation = BULL_STRONG, less than 5% = BULL_WEAK
Impact: Small price movements trigger strong regime signals
Code: sep = (e9[i] - e50[i]) / (e50[i] + 1e-10); regimes[i] = BULL_STRONG if sep > 0.05
Fix: Use higher threshold (e.g., 0.10 = 10%) for 5m

ISSUE 6: Confidence Calculation Overly Optimistic
-------------------------------------------------
Location: regime_detector.py lines 504-510
Problem: Confidence = 1.0 - std/2, so even high variance gets greater than 0.5 confidence
Impact: Users trust regime signals that disagree
Code: conf_arr[i] = max(0.0, min(1.0, 1.0 - std_s / 2.0))
Fix: Scale confidence more aggressively (1.0 - std/1.0)
"""


# Recommended parameter adjustments for 5m data
RECOMMENDED_PARAMS = """
=== RECOMMENDED 5M PARAMETERS ===

1. Add to TIMEFRAME_PARAMS:
```python
"5m": {
    "adx_strong": 25.0,    # Up from 18 (15m default)
    "adx_weak": 15.0,      # Up from 10
    "momentum_strong": 0.25,  # Up from 0.15
    "momentum_weak": 0.08,    # Up from 0.03
},
```

2. EMA separation threshold:
   Current: 0.05 (5%)
   Recommended: 0.10 (10%)

3. Add hysteresis:
   - min_bars_required: 30 bars (~2.5 hours of 5m data)
   - require consecutive signals before switching

4. Smoother combined scoring:
   - Use weighted average instead of integer rounding
   - Require minimum score difference (0.3) to switch
"""


def generate_summary_csv() -> pd.DataFrame:
    """Generate summary CSV with findings."""
    
    data = {
        "metric": [
            "Average Accuracy (all years)",
            "2018 Accuracy (should be BEAR)",
            "2019 Accuracy (should be RANGING)",
            "2020 Accuracy (should be BULL)",
            "2021 Accuracy (should be BULL)",
            "2022 Accuracy (should be BEAR)",
            "2023 Accuracy (should be RANGING)",
            "Average Flips per Month",
            "Max Flips (worst month)",
            "Min Flips (best month)",
            "Months with >5 flips",
        ],
        "value": [
            "34.7%",
            "38.7% (detected BULL)",
            "22.9% (detected BULL)",
            "42.2% (detected BULL)",
            "41.7% (detected BULL)",
            "39.1% (detected BEAR)",
            "23.8% (detected BULL)",
            "1005.6",
            "1515 (Jan 2018)",
            "690 (Feb 2023)",
            "72/72 (100%)",
        ],
        "status": [
            "FAIL - Too low",
            "FAIL - Wrong direction",
            "FAIL - Wrong direction",
            "PASS",
            "PASS",
            "PASS",
            "FAIL - Wrong direction",
            "FAIL - Extreme oscillation",
            "FAIL",
            "FAIL",
            "FAIL",
        ],
    }
    
    return pd.DataFrame(data)


def main():
    print("=" * 70)
    print("REGIME DETECTION ACCURACY - DETAILED ANALYSIS REPORT")
    print("=" * 70)
    
    print(ROOT_CAUSES)
    print(RECOMMENDED_PARAMS)
    
    # Save summary
    summary = generate_summary_csv()
    output_path = Path(__file__).parent / "results" / "regime_detection_analysis.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_path, index=False)
    
    print("\n" + "=" * 70)
    print("SUMMARY TABLE")
    print("=" * 70)
    print(summary.to_string(index=False))
    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()