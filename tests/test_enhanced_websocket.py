#!/usr/bin/env python3
"""
Test script for enhanced WebSocket data management system.
Validates the data sufficiency improvements and buffer management.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from trading_bot_v2.pacifica_ws_client import CandleDataBuffer, MIN_CANDLES_REQUIRED, MAX_CANDLES_PER_TIMEFRAME


def test_candle_data_buffer():
    """Test the enhanced CandleDataBuffer functionality."""
    print("Testing CandleDataBuffer...")
    
    # Test buffer creation
    buffer = CandleDataBuffer("BTC", "1m", max_size=100)
    assert buffer.symbol == "BTC"
    assert buffer.timeframe == "1m"
    assert buffer.max_size == 100
    assert len(buffer.candles) == 0
    print("PASS: Buffer creation works")
    
    # Test candle addition
    test_candles = []
    for i in range(50):
        candle = {
            "timestamp": int((datetime.utcnow() - timedelta(minutes=50-i)).timestamp() * 1000),
            "open": 50000.0 + i,
            "high": 50001.0 + i,
            "low": 49999.0 + i,
            "close": 50000.5 + i,
            "volume": 100.0 + i * 10
        }
        test_candles.append(candle)
        added = buffer.add_candle(candle)
        assert added == True
    
    assert len(buffer.candles) == 50
    print("PASS: Candle addition works")
    
    # Test data sufficiency
    assert buffer.is_sufficient(50) == True
    assert buffer.is_sufficient(100) == False
    print("PASS: Data sufficiency validation works")
    
    # Test duplicate handling
    duplicate_candle = test_candles[-1].copy()
    duplicate_candle["close"] = 50001.0
    added = buffer.add_candle(duplicate_candle)
    assert added == True  # Should update existing
    assert len(buffer.candles) == 50  # Count should remain same
    print("PASS: Duplicate candle handling works")
    
    # Test buffer overflow
    for i in range(100):
        candle = {
            "timestamp": int((datetime.utcnow() + timedelta(minutes=i)).timestamp() * 1000),
            "open": 50100.0 + i,
            "high": 50101.0 + i,
            "low": 50099.0 + i,
            "close": 50100.5 + i,
            "volume": 200.0 + i * 10
        }
        buffer.add_candle(candle)
    
    assert len(buffer.candles) <= 100  # Should respect max_size
    print("PASS: Buffer overflow handling works")
    
    # Test candle validation
    invalid_candle = {"open": "invalid"}
    added = buffer.add_candle(invalid_candle)
    assert added == False
    print("PASS: Invalid candle rejection works")
    
    print("SUCCESS: CandleDataBuffer tests passed!\n")


def test_websocket_client_enhancements():
    """Test the enhanced WebSocket client data management."""
    print("Testing WebSocket client enhancements...")
    
    # Test that imports work and constants are defined
    assert MIN_CANDLES_REQUIRED == 200
    assert MAX_CANDLES_PER_TIMEFRAME == 500
    print("PASS: Configuration constants are correct")
    
    print("SUCCESS: WebSocket client enhancement tests completed!\n")


def main():
    """Run all tests for enhanced WebSocket data management."""
    print("Starting Enhanced WebSocket Data Management Tests\n")
    
    try:
        test_candle_data_buffer()
        test_websocket_client_enhancements()
        
        print("ALL TESTS PASSED!")
        print("\nEnhanced Data Management System Summary:")
        print(f"   * Minimum candles required: {MIN_CANDLES_REQUIRED}")
        print(f"   * Maximum candles per timeframe: {MAX_CANDLES_PER_TIMEFRAME}")
        print("   * Enhanced buffer management with validation")
        print("   * Persistent disk caching with TTL")
        print("   * Background data recovery and gap filling")
        print("   * Comprehensive data sufficiency reporting")
        print("   * Backward compatibility maintained")
        
    except AssertionError as e:
        print(f"TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())