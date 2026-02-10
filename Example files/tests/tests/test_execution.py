"""
Tests for execution.py module.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from execution import ExecutionEngine


@pytest.mark.asyncio
async def test_get_ticker_invalid_symbol():
    """Test that get_ticker returns None for empty/invalid symbols."""
    # Mock the ExecutionEngine to avoid complex initialization
    engine = MagicMock()
    engine.connected = True
    engine.client = MagicMock()

    # Import the actual method and bind it to the mock
    from execution import ExecutionEngine
    engine.get_ticker = ExecutionEngine.get_ticker.__get__(engine, ExecutionEngine)

    # Test empty symbol
    result = await engine.get_ticker('')
    assert result is None

    # Test whitespace symbol
    result = await engine.get_ticker('   ')
    assert result is None


@pytest.mark.asyncio
async def test_get_ticker_valid_symbol():
    """Test that get_ticker works for valid symbols."""
    engine = ExecutionEngine()
    engine.connected = True
    engine.client = MagicMock()
    engine.client.get_ticker.return_value = {
        'last_price': 50000.0,
        'best_bid': 49990.0,
        'best_ask': 50010.0
    }

    result = await engine.get_ticker('BTC/USD')
    assert result is not None
    assert result['last_price'] == 50000.0