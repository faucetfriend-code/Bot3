from .indicators import calculate_adx
import math

# Test 1: Strong uptrend
print("Test 1: Strong Uptrend")
highs = [10 + i * 0.5 + (i % 3) * 0.2 for i in range(50)]
lows = [9.5 + i * 0.5 - (i % 3) * 0.1 for i in range(50)]
closes = [9.8 + i * 0.5 for i in range(50)]
adx = calculate_adx(highs, lows, closes, period=14)
print(f"ADX: {adx:.2f}")
print(f"Status: {'TRENDING' if adx > 25 else 'RANGING' if adx < 20 else 'INDECISIVE'}")
print()

# Test 2: Ranging market
print("Test 2: Ranging Market")
highs = [50 + 2 * math.sin(i * 0.3) + 0.5 for i in range(50)]
lows = [50 + 2 * math.sin(i * 0.3) - 0.5 for i in range(50)]
closes = [50 + 2 * math.sin(i * 0.3) for i in range(50)]
adx = calculate_adx(highs, lows, closes, period=14)
print(f"ADX: {adx:.2f}")
print(f"Status: {'TRENDING' if adx > 25 else 'RANGING' if adx < 20 else 'INDECISIVE'}")
print()

# Test 3: Strong downtrend
print("Test 3: Strong Downtrend")
highs = [100 - i * 0.8 + (i % 3) * 0.2 for i in range(50)]
lows = [99 - i * 0.8 - (i % 3) * 0.1 for i in range(50)]
closes = [99.5 - i * 0.8 for i in range(50)]
adx = calculate_adx(highs, lows, closes, period=14)
print(f"ADX: {adx:.2f}")
print(f"Status: {'TRENDING' if adx > 25 else 'RANGING' if adx < 20 else 'INDECISIVE'}")

print("\nADX Interpretation:")
print("  0-20:  Weak/No Trend (ranging)")
print("  20-25: Indecisive")
print("  25+:   Strong Trend")
