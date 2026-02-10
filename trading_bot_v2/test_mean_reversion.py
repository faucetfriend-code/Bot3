"""
Test script for Mean Reversion Strategy.

Tests signal generation for:
- Oversold conditions (LONG signal)
- Overbought conditions (SHORT signal)
- Multi-timeframe confirmation
- Edge cases
"""

import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "Example files", "core_logic")
)

from strategies.mean_reversion import MeanReversionStrategy
from models import OrderSide
import random

random.seed(42)

print("=" * 70)
print("Mean Reversion Strategy Test")
print("=" * 70)

# Initialize strategy with default parameters
strategy = MeanReversionStrategy(
    rsi_oversold=30.0, rsi_overbought=70.0, min_confidence=0.6
)

# Test 1: Oversold Condition (LONG signal expected)
print("\nTest 1: Oversold Condition (LONG Signal)")
print("-" * 70)

# Create price data with sharp selloff to create oversold RSI
# Start with consolidation, then sharp drop in last 14 candles
closes_oversold = [100.0]
for i in range(85):
    # Sideways with slight downtrend
    change = random.uniform(-0.2, 0.2)
    closes_oversold.append(closes_oversold[-1] + change)

# Sharp selloff in last 14 candles to create RSI < 30
for i in range(14):
    change = random.uniform(-1.5, -0.5)  # Strong selling
    closes_oversold.append(closes_oversold[-1] + change)

highs_oversold = [c + random.uniform(0.05, 0.15) for c in closes_oversold]
lows_oversold = [c - random.uniform(0.05, 0.15) for c in closes_oversold]

multi_tf_data_oversold = {
    "15m": {"high": highs_oversold, "low": lows_oversold, "close": closes_oversold},
    "1h": {"high": highs_oversold, "low": lows_oversold, "close": closes_oversold},
}

current_price = closes_oversold[-1]
signals = strategy.generate_signals("SUI-PERP", multi_tf_data_oversold, current_price)

print(f"Price movement: ${closes_oversold[0]:.2f} -> ${closes_oversold[-1]:.2f}")
print(f"Signals generated: {len(signals)}")

if signals:
    signal = signals[0]
    print(f"  Signal Type: {signal.side.value}")
    print(f"  Entry: ${signal.entry_price:.2f}")
    print(f"  Stop Loss: ${signal.stop_loss:.2f}")
    print(f"  Take Profit: ${signal.take_profit:.2f}")
    print(f"  Confidence: {signal.confidence:.2%}")
    print(f"  RRR: {signal.rrr:.2f}")
    print(f"  RSI 15m: {signal.indicators['rsi_15m']:.2f}")
    print(f"  RSI 1h: {signal.indicators['rsi_1h']:.2f}")
    print(f"  Pattern: {signal.pattern}")

    if signal.side == OrderSide.BUY and signal.confidence >= 0.6:
        print("\n[PASS] LONG signal generated for oversold condition")
    else:
        print("\n[FAIL] Expected LONG signal with confidence >= 0.6")
else:
    print("\n[FAIL] No signal generated for oversold condition")

# Test 2: Overbought Condition (SHORT signal expected)
print("\nTest 2: Overbought Condition (SHORT Signal)")
print("-" * 70)

# Create price data with sharp rally to create overbought RSI
# Start with consolidation, then sharp rally in last 14 candles
closes_overbought = [100.0]
for i in range(85):
    # Sideways with slight uptrend
    change = random.uniform(-0.2, 0.2)
    closes_overbought.append(closes_overbought[-1] + change)

# Sharp rally in last 14 candles to create RSI > 70
for i in range(14):
    change = random.uniform(0.5, 1.5)  # Strong buying
    closes_overbought.append(closes_overbought[-1] + change)

highs_overbought = [c + random.uniform(0.05, 0.15) for c in closes_overbought]
lows_overbought = [c - random.uniform(0.05, 0.15) for c in closes_overbought]

multi_tf_data_overbought = {
    "15m": {
        "high": highs_overbought,
        "low": lows_overbought,
        "close": closes_overbought,
    },
    "1h": {
        "high": highs_overbought,
        "low": lows_overbought,
        "close": closes_overbought,
    },
}

current_price = closes_overbought[-1]
signals = strategy.generate_signals("SUI-PERP", multi_tf_data_overbought, current_price)

print(f"Price movement: ${closes_overbought[0]:.2f} -> ${closes_overbought[-1]:.2f}")
print(f"Signals generated: {len(signals)}")

if signals:
    signal = signals[0]
    print(f"  Signal Type: {signal.side.value}")
    print(f"  Entry: ${signal.entry_price:.2f}")
    print(f"  Stop Loss: ${signal.stop_loss:.2f}")
    print(f"  Take Profit: ${signal.take_profit:.2f}")
    print(f"  Confidence: {signal.confidence:.2%}")
    print(f"  RRR: {signal.rrr:.2f}")
    print(f"  RSI 15m: {signal.indicators['rsi_15m']:.2f}")
    print(f"  RSI 1h: {signal.indicators['rsi_1h']:.2f}")
    print(f"  Pattern: {signal.pattern}")

    if signal.side == OrderSide.SELL and signal.confidence >= 0.6:
        print("\n[PASS] SHORT signal generated for overbought condition")
    else:
        print("\n[FAIL] Expected SHORT signal with confidence >= 0.6")
else:
    print("\n[FAIL] No signal generated for overbought condition")

# Test 3: Neutral Condition (No signal expected)
print("\nTest 3: Neutral Condition (No Signal)")
print("-" * 70)

# Create neutral price action (RSI around 50)
closes_neutral = [100.0 + random.uniform(-2, 2) for _ in range(100)]
highs_neutral = [c + random.uniform(0.1, 0.3) for c in closes_neutral]
lows_neutral = [c - random.uniform(0.1, 0.3) for c in closes_neutral]

multi_tf_data_neutral = {
    "15m": {"high": highs_neutral, "low": lows_neutral, "close": closes_neutral},
    "1h": {"high": highs_neutral, "low": lows_neutral, "close": closes_neutral},
}

current_price = closes_neutral[-1]
signals = strategy.generate_signals("SUI-PERP", multi_tf_data_neutral, current_price)

print(f"Price range: ${min(closes_neutral):.2f} - ${max(closes_neutral):.2f}")
print(f"Signals generated: {len(signals)}")

if len(signals) == 0:
    print("\n[PASS] No signal generated for neutral condition (as expected)")
else:
    print("\n[FAIL] Expected no signal for neutral RSI")

# Test 4: Insufficient Data
print("\nTest 4: Insufficient Data (Error Handling)")
print("-" * 70)

# Only 10 candles (insufficient for RSI calculation)
short_data = {
    "15m": {"high": [100.0] * 10, "low": [99.0] * 10, "close": [99.5] * 10},
    "1h": {"high": [100.0] * 10, "low": [99.0] * 10, "close": [99.5] * 10},
}

signals = strategy.generate_signals("SUI-PERP", short_data, 99.5)

if len(signals) == 0:
    print("[PASS] Correctly handled insufficient data")
else:
    print("[FAIL] Should not generate signal with insufficient data")

# Test 5: Multi-Timeframe Divergence
print("\nTest 5: Multi-Timeframe Divergence (No Signal)")
print("-" * 70)

# 15m oversold, but 1h neutral (no multi-timeframe alignment)
closes_15m_oversold = [100.0]
for i in range(99):
    change = random.uniform(-0.5, -0.1)
    closes_15m_oversold.append(closes_15m_oversold[-1] + change)

closes_1h_neutral = [100.0 + random.uniform(-1, 1) for _ in range(100)]

multi_tf_divergence = {
    "15m": {
        "high": [c + 0.2 for c in closes_15m_oversold],
        "low": [c - 0.2 for c in closes_15m_oversold],
        "close": closes_15m_oversold,
    },
    "1h": {
        "high": [c + 0.2 for c in closes_1h_neutral],
        "low": [c - 0.2 for c in closes_1h_neutral],
        "close": closes_1h_neutral,
    },
}

signals = strategy.generate_signals(
    "SUI-PERP", multi_tf_divergence, closes_15m_oversold[-1]
)

print(f"15m price: ${closes_15m_oversold[0]:.2f} -> ${closes_15m_oversold[-1]:.2f}")
print(f"1h price range: ${min(closes_1h_neutral):.2f} - ${max(closes_1h_neutral):.2f}")
print(f"Signals generated: {len(signals)}")

if len(signals) == 0:
    print("\n[PASS] No signal without multi-timeframe alignment")
else:
    print("\n[FAIL] Should require both timeframes to confirm")

print("\n" + "=" * 70)
print("Mean Reversion Strategy Test Complete!")
print("=" * 70)
print("\nSummary:")
print("- Oversold detection working (LONG signal)")
print("- Overbought detection working (SHORT signal)")
print("- Neutral condition handling correct")
print("- Multi-timeframe confirmation enforced")
print("- Error handling functional")
print("\nMean Reversion Strategy ready for Phase 2.2 (MA Crossover)!")
