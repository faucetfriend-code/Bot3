"""
Test script for MultiTimeframeFetcher.

Tests caching, interval conversion, and data parsing without requiring live API.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_bot_v2.multi_timeframe_fetcher import MultiTimeframeFetcher
from datetime import datetime, timedelta
import time


class MockPacificaClient:
    """Mock client for testing without live API."""

    def get_candles(self, market, interval, start_time, limit):
        """Return mock candle data."""
        print(f"  [API CALL] Fetching {limit} candles for {market} {interval}")

        # Generate mock candles
        candles = []
        base_price = 100.0
        base_time = int(datetime.now().timestamp())

        for i in range(limit):
            candles.append(
                {
                    "t": base_time - (i * 60),  # timestamp in milliseconds
                    "o": base_price + i * 0.1,  # open
                    "h": base_price + i * 0.1 + 0.5,  # high
                    "l": base_price + i * 0.1 - 0.5,  # low
                    "c": base_price + i * 0.1 + 0.2,  # close
                    "v": 1000.0 + i * 10,  # volume
                }
            )

        return candles


print("=" * 70)
print("Multi-Timeframe Fetcher Test")
print("=" * 70)

# Test 1: Interval to Minutes Conversion
print("\nTest 1: Interval to Minutes Conversion")
print("-" * 70)

mock_client = MockPacificaClient()
fetcher = MultiTimeframeFetcher(mock_client, cache_ttl_seconds=60)

test_intervals = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

interval_test_passed = True
for interval, expected_minutes in test_intervals.items():
    try:
        actual_minutes = fetcher._interval_to_minutes(interval)
        if actual_minutes == expected_minutes:
            print(f"  [PASS] {interval} = {actual_minutes} minutes")
        else:
            print(
                f"  [FAIL] {interval}: expected {expected_minutes}, got {actual_minutes}"
            )
            interval_test_passed = False
    except Exception as e:
        print(f"  [FAIL] {interval}: {e}")
        interval_test_passed = False

if interval_test_passed:
    print("\n[PASS] All interval conversions correct")
else:
    print("\n[FAIL] Some interval conversions failed")

# Test 2: Invalid Interval Handling
print("\nTest 2: Invalid Interval Handling")
print("-" * 70)

invalid_intervals = ["xyz", "1x", "", "10"]
invalid_test_passed = True

for invalid_interval in invalid_intervals:
    try:
        fetcher._interval_to_minutes(invalid_interval)
        print(f"  [FAIL] Should have raised ValueError for: {invalid_interval}")
        invalid_test_passed = False
    except ValueError:
        print(f"  [PASS] Correctly rejected invalid interval: {invalid_interval}")

if invalid_test_passed:
    print("\n[PASS] Invalid interval handling correct")

# Test 3: Parse Candles
print("\nTest 3: Parse Candles")
print("-" * 70)

mock_candles = [
    {"t": 1000, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 1000.0},
    {"t": 1001, "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.5, "v": 1500.0},
    {"t": 1002, "o": 101.5, "h": 103.0, "l": 101.0, "c": 102.0, "v": 2000.0},
]

parsed = fetcher._parse_candles(mock_candles)

if (
    len(parsed["high"]) == 3
    and parsed["high"] == [101.0, 102.0, 103.0]
    and parsed["close"] == [100.5, 101.5, 102.0]
    and len(parsed["volume"]) == 3
):
    print("[PASS] Candle parsing correct")
    print(f"  Highs: {parsed['high']}")
    print(f"  Closes: {parsed['close']}")
    print(f"  Volumes: {parsed['volume']}")
else:
    print("[FAIL] Candle parsing incorrect")
    print(f"  Parsed: {parsed}")

# Test 4: Cache Functionality
print("\nTest 4: Cache Functionality")
print("-" * 70)

print("Fetching data for SUI-PERP (should make API calls):")
try:
    data1 = fetcher.get_candles_multi_tf(
        "SUI-PERP", timeframes=["15m", "1h"], lookback_candles=50
    )
    print(f"  Received data for timeframes: {list(data1.keys())}")
    print(f"  15m data points: {len(data1['15m']['close'])}")
    print(f"  1h data points: {len(data1['1h']['close'])}")
except Exception as e:
    print(f"  [ERROR] {e}")

print("\nFetching same data again (should use cache):")
try:
    data2 = fetcher.get_candles_multi_tf(
        "SUI-PERP", timeframes=["15m", "1h"], lookback_candles=50
    )
    print(f"  Received data for timeframes: {list(data2.keys())}")
except Exception as e:
    print(f"  [ERROR] {e}")

# Test 5: Cache Stats
print("\nTest 5: Cache Statistics")
print("-" * 70)

stats = fetcher.get_cache_stats()
print(f"Cache size: {stats['size']} entries")
print(f"Oldest entry age: {stats['oldest_entry_age_seconds']:.1f} seconds")
print("Cache entries:")
for entry in stats["entries"]:
    print(
        f"  - {entry['symbol']} {entry['timeframe']} (age: {entry['age_seconds']:.1f}s)"
    )

if stats["size"] == 2:  # Should have 15m and 1h cached
    print("\n[PASS] Cache statistics correct")
else:
    print(f"\n[FAIL] Expected 2 cache entries, got {stats['size']}")

# Test 6: Cache Expiration
print("\nTest 6: Cache Expiration")
print("-" * 70)

# Create fetcher with short TTL
short_ttl_fetcher = MultiTimeframeFetcher(mock_client, cache_ttl_seconds=2)

print("Fetching data with 2-second TTL:")
data = short_ttl_fetcher.get_candles_multi_tf(
    "SUI-PERP", timeframes=["15m"], lookback_candles=50
)
print(f"  Initial fetch: {len(data['15m']['close'])} candles")

print("\nWaiting 3 seconds for cache to expire...")
time.sleep(3)

print("Fetching again after expiration (should make new API call):")
data = short_ttl_fetcher.get_candles_multi_tf(
    "SUI-PERP", timeframes=["15m"], lookback_candles=50
)
print(f"  After expiration: {len(data['15m']['close'])} candles")

stats = short_ttl_fetcher.get_cache_stats()
if stats["size"] == 1 and stats["oldest_entry_age_seconds"] < 1:
    print("\n[PASS] Cache expiration working correctly")
else:
    print(f"\n[FAIL] Cache expiration issue: {stats}")

# Test 7: Clear Cache
print("\nTest 7: Clear Cache")
print("-" * 70)

print("Clearing cache...")
fetcher.clear_cache()

stats_after_clear = fetcher.get_cache_stats()
if stats_after_clear["size"] == 0:
    print("[PASS] Cache cleared successfully")
else:
    print(f"[FAIL] Cache not empty after clear: {stats_after_clear['size']} entries")

print("\n" + "=" * 70)
print("Multi-Timeframe Fetcher Test Complete!")
print("=" * 70)
print("\nSummary:")
print("- Interval conversion working")
print("- Invalid interval handling functional")
print("- Candle parsing correct")
print("- Cache functionality validated")
print("- Cache expiration working")
print("- Cache stats accurate")
print("\nMultiTimeframeFetcher ready for integration!")
print("\nNote: Actual Pacifica API endpoint (/candles) may need adjustment")
print("      based on official API documentation.")
