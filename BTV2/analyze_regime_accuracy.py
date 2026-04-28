"""
Regime Detection Accuracy Analysis
===================================
Tests the regime_detector.py for:
1. Oscillation (too frequent regime flips)
2. Wrong regime assignments on known historical data
3. Transition edge cases
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
from collections import defaultdict

# Add BTV2 to path
sys.path.insert(0, str(Path(__file__).parent / "BTV2"))

from regime_detector import RegimeDetector, MarketRegime


def load_btc_data() -> pd.DataFrame:
    """Load BTC 5m data from 2018-2025."""
    # Go up from BTV2 folder to Bot3 root
    data_path = Path(__file__).parent.parent / "trading_bot_v2" / "backtesting" / "data" / "BTC-USDC_5m_all.csv"
    
    df = pd.read_csv(data_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    
    print(f"Loaded BTC 5m data: {len(df):,} bars")
    print(f"Date range: {df.index.min()} to {df.index.max()}")
    
    return df


def get_known_regimes() -> dict[int, str]:
    """
    Known historical BTC regimes by year.
    Simplified to BULL/BEAR/RANGING for comparison.
    """
    return {
        2018: "BEAR",      # Crypto winter
        2019: "RANGING",   # Mostly ranging after 2018 crash
        2020: "BULL",      # COVID pump + bull run
        2021: "BULL",      # Spring bull, then BEAR after May
        2022: "BEAR",      # Crypto winter again
        2023: "RANGING",   # Recovery/ranging
        2024: "BULL",      # Bull run
        2025: "BULL",      # Continuing bull (if data available)
    }


def simplify_regime(regime: MarketRegime) -> str:
    """Map detailed regime to simple BULL/BEAR/RANGING."""
    if regime in [MarketRegime.BULL_STRONG, MarketRegime.BULL_WEAK]:
        return "BULL"
    elif regime in [MarketRegime.BEAR_STRONG, MarketRegime.BEAR_WEAK]:
        return "BEAR"
    else:
        return "RANGING"


def analyze_regime_accuracy(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze regime detection accuracy by year."""
    
    # Run regime detection (combined method with 5m timeframe params)
    # Use 15m params since 5m not in TIMEFRAME_PARAMS
    detector = RegimeDetector(df, method="combined", timeframe="15m")
    regimes = detector.get_regimes()
    confidence = detector.get_confidence()
    
    # Create analysis DataFrame
    analysis = pd.DataFrame({
        "regime": regimes,
        "confidence": confidence,
    })
    analysis["year"] = analysis.index.year
    analysis["month"] = analysis.index.month
    analysis["simple_regime"] = analysis["regime"].apply(simplify_regime)
    
    # Known regimes by year
    known_regimes = get_known_regimes()
    
    # Accuracy by year
    yearly_accuracy = []
    
    for year, known in known_regimes.items():
        year_data = analysis[analysis["year"] == year]
        
        if len(year_data) == 0:
            continue
            
        # Count regime distribution
        regime_counts = year_data["simple_regime"].value_counts()
        dominant = regime_counts.idxmax() if len(regime_counts) > 0 else "UNKNOWN"
        dominant_pct = (regime_counts.max() / len(year_data)) * 100 if len(regime_counts) > 0 else 0
        
        # Calculate accuracy (what % matched known regime)
        correct = (year_data["simple_regime"] == known).sum()
        accuracy = (correct / len(year_data)) * 100
        
        yearly_accuracy.append({
            "year": year,
            "known_regime": known,
            "detected_dominant": dominant,
            "dominant_pct": round(dominant_pct, 1),
            "correct_bars": correct,
            "total_bars": len(year_data),
            "accuracy_pct": round(accuracy, 1),
            "avg_confidence": round(year_data["confidence"].mean(), 3),
        })
    
    return pd.DataFrame(yearly_accuracy), analysis


def analyze_oscillation(analysis: pd.DataFrame) -> pd.DataFrame:
    """Analyze regime flips per month to find oscillation."""
    
    # Calculate regime changes
    analysis = analysis.sort_index()
    analysis["regime_changed"] = analysis["regime"] != analysis["regime"].shift(1)
    
    # Group by year-month
    monthly_flips = analysis.groupby(["year", "month"]).agg({
        "regime_changed": "sum",
        "regime": "count",
    }).reset_index()
    
    monthly_flips.columns = ["year", "month", "flips", "total_bars"]
    monthly_flips["flip_rate"] = monthly_flips["flips"] / monthly_flips["total_bars"] * 100
    
    # Identify oscillation months (>5 flips per month is concerning for 5m data)
    # 5 bars/minute * 60 min/hour * 24 hours/day * 30 days = ~216,000 bars/month
    # So >5 flips is very oscillatory
    oscillation_threshold = 5
    oscillating = monthly_flips[monthly_flips["flips"] > oscillation_threshold]
    
    print(f"\n=== OSCILLATION ANALYSIS ===")
    print(f"Total months analyzed: {len(monthly_flips)}")
    print(f"Months with >{oscillation_threshold} flips: {len(oscillating)}")
    
    if len(oscillating) > 0:
        print("\nOscillating months:")
        print(oscillating.to_string())
    
    # Average flips per month
    avg_flips = monthly_flips["flips"].mean()
    print(f"\nAverage flips per month: {avg_flips:.2f}")
    
    return monthly_flips, oscillating


def analyze_transition_periods(analysis: pd.DataFrame) -> list[dict]:
    """Analyze regime transition edge cases."""
    
    analysis = analysis.sort_index()
    analysis["regime_changed"] = analysis["regime"] != analysis["regime"].shift(1)
    
    # Find all transitions
    transitions = analysis[analysis["regime_changed"]].copy()
    
    # Group consecutive transitions (rapid flipping)
    transitions["consecutive"] = (
        transitions.index.to_series().diff().dt.total_seconds() < 300  # Within 5 min
    ).cumsum()
    
    rapid_flips = transitions.groupby("consecutive").filter(
        lambda x: len(x) >= 3 and (x.index[-1] - x.index[0]).total_seconds() < 3600  # 3+ flips in 1 hour
    )
    
    print(f"\n=== TRANSITION EDGE CASES ===")
    print(f"Total regime changes: {len(transitions)}")
    print(f"Rapid flip sequences (3+ in 1 hour): {len(rapid_flips) if len(rapid_flips) > 0 else 0}")
    
    # Show sample of problematic transitions
    if len(rapid_flips) > 0:
        print("\nSample rapid flip periods:")
        print(rapid_flips[["regime", "confidence"]].head(10))
    
    return []


def main():
    print("=" * 60)
    print("REGIME DETECTION ACCURACY ANALYSIS")
    print("=" * 60)
    
    # Load data
    df = load_btc_data()
    
    # Analyze accuracy
    print("\n=== REGIME ACCURACY BY YEAR ===")
    yearly_accuracy, analysis = analyze_regime_accuracy(df)
    
    print(yearly_accuracy.to_string(index=False))
    
    # Analyze oscillation
    monthly_flips, oscillating = analyze_oscillation(analysis)
    
    # Analyze transitions
    analyze_transition_periods(analysis)
    
    # Save results
    output_path = Path(__file__).parent / "BTV2" / "results" / "regime_detection_accuracy.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    yearly_accuracy.to_csv(output_path, index=False)
    print(f"\nResults saved to: {output_path}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    avg_accuracy = yearly_accuracy["accuracy_pct"].mean()
    print(f"Average accuracy across years: {avg_accuracy:.1f}%")
    
    # Identify problem years
    problem_years = yearly_accuracy[yearly_accuracy["accuracy_pct"] < 50]
    if len(problem_years) > 0:
        print(f"\nProblem years (<50% accuracy):")
        print(problem_years[["year", "known_regime", "detected_dominant", "accuracy_pct"]].to_string(index=False))
    
    return yearly_accuracy


if __name__ == "__main__":
    main()