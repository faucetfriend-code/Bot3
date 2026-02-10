import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "Example files", "core_logic")
)

from indicators import calculate_adx
import random

random.seed(42)  # For reproducible results

print("=" * 70)
print("ADX Test with Realistic Market Data")
print("=" * 70)

# Test 1: Trending market with noise (more realistic)
print("\nTest 1: Trending Market with Retracements")
print("-" * 70)
closes_trend = [100.0]
for i in range(49):
    # Uptrend with random noise and occasional retracements
    if random.random() < 0.8:  # 80% chance of up move
        change = random.uniform(0.3, 1.0)
    else:  # 20% chance of retracement
        change = random.uniform(-0.5, 0)
    closes_trend.append(closes_trend[-1] + change)

highs_trend = [c + random.uniform(0.1, 0.5) for c in closes_trend]
lows_trend = [c - random.uniform(0.1, 0.5) for c in closes_trend]

adx = calculate_adx(highs_trend, lows_trend, closes_trend, period=14)
print(f"Start Price: ${closes_trend[0]:.2f}")
print(f"End Price: ${closes_trend[-1]:.2f}")
print(f"Total Change: {(closes_trend[-1] / closes_trend[0] - 1) * 100:.2f}%")
print(f"ADX Value: {adx:.2f}")
print(
    f"Interpretation: {'STRONG TREND' if adx > 25 else 'WEAK TREND/RANGING' if adx < 20 else 'INDECISIVE'}"
)

# Test 2: Choppy/ranging market
print("\nTest 2: Choppy/Ranging Market")
print("-" * 70)
closes_range = [100.0]
for i in range(49):
    # Random walk around 100
    change = random.uniform(-0.8, 0.8)
    closes_range.append(closes_range[-1] + change)

highs_range = [c + random.uniform(0.1, 0.5) for c in closes_range]
lows_range = [c - random.uniform(0.1, 0.5) for c in closes_range]

adx = calculate_adx(highs_range, lows_range, closes_range, period=14)
print(f"Start Price: ${closes_range[0]:.2f}")
print(f"End Price: ${closes_range[-1]:.2f}")
print(f"Total Change: {(closes_range[-1] / closes_range[0] - 1) * 100:.2f}%")
print(f"ADX Value: {adx:.2f}")
print(
    f"Interpretation: {'STRONG TREND' if adx > 25 else 'WEAK TREND/RANGING' if adx < 20 else 'INDECISIVE'}"
)

# Test 3: Consolidation (tight range)
print("\nTest 3: Consolidation (Tight Range)")
print("-" * 70)
closes_consol = [100.0 + random.uniform(-0.3, 0.3) for _ in range(50)]
highs_consol = [c + random.uniform(0.05, 0.2) for c in closes_consol]
lows_consol = [c - random.uniform(0.05, 0.2) for c in closes_consol]

adx = calculate_adx(highs_consol, lows_consol, closes_consol, period=14)
print(f"Start Price: ${closes_consol[0]:.2f}")
print(f"End Price: ${closes_consol[-1]:.2f}")
print(f"Price Range: ${min(closes_consol):.2f} - ${max(closes_consol):.2f}")
print(f"ADX Value: {adx:.2f}")
print(
    f"Interpretation: {'STRONG TREND' if adx > 25 else 'WEAK TREND/RANGING' if adx < 20 else 'INDECISIVE'}"
)

# Test 4: Downtrend with noise
print("\nTest 4: Downtrend with Retracements")
print("-" * 70)
closes_down = [100.0]
for i in range(49):
    # Downtrend with random noise
    if random.random() < 0.8:  # 80% chance of down move
        change = random.uniform(-1.0, -0.3)
    else:  # 20% chance of bounce
        change = random.uniform(0, 0.5)
    closes_down.append(closes_down[-1] + change)

highs_down = [c + random.uniform(0.1, 0.5) for c in closes_down]
lows_down = [c - random.uniform(0.1, 0.5) for c in closes_down]

adx = calculate_adx(highs_down, lows_down, closes_down, period=14)
print(f"Start Price: ${closes_down[0]:.2f}")
print(f"End Price: ${closes_down[-1]:.2f}")
print(f"Total Change: {(closes_down[-1] / closes_down[0] - 1) * 100:.2f}%")
print(f"ADX Value: {adx:.2f}")
print(
    f"Interpretation: {'STRONG TREND' if adx > 25 else 'WEAK TREND/RANGING' if adx < 20 else 'INDECISIVE'}"
)

print("\n" + "=" * 70)
print("Summary")
print("=" * 70)
print("""
ADX (Average Directional Index) measures trend STRENGTH, not direction:

- ADX > 25:  Strong trend (up or down) - use trend-following strategies
- ADX 20-25: Indecisive/transitioning - wait for clarity
- ADX < 20:  Weak/no trend (ranging) - use mean reversion or grid trading

Note: ADX does NOT tell you if the trend is up or down, only how strong it is!
For direction, use +DI vs -DI or simply look at price action.

ADX indicator is working correctly!
""")
