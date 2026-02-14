#!/usr/bin/env python3
"""
Test script to reproduce the VWAP_SCALPING "success" error.

This script simulates the exact scenario to identify the root cause
of the mysterious '"success"' error in VWAP_SCALPING strategy execution.
"""

import logging
import sys
import os
import traceback
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch

# Add the trading_bot_v2 directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "trading_bot_v2"))

# Change to the trading_bot_v2 directory to avoid relative import issues
os.chdir(Path(__file__).parent / "trading_bot_v2")

from config import StrategyType, TradeQuality, AssetClass, MarketState
from models import OrderSide, Signal

# Set up logging to see all debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('vwap_scalping_test.log')
    ]
)

logger = logging.getLogger(__name__)

def create_vwap_scalping_signal():
    """Create a VWAP_SCALPING test signal."""
    return Signal(
        asset="LTC",
        strategy=StrategyType.VWAP_SCALPING,
        asset_class=AssetClass.PERPETUAL,
        side=OrderSide.BUY,
        entry_price=51.214588,
        stop_loss=51.094588,
        confidence=0.7412419049325152,
        quality=TradeQuality.HIGH_CONVICTION,
        market_state=MarketState.RANGE,
        timeframe="15min",
        pattern="test",
        volume_confirmation=True,
        multi_timeframe_alignment=True,
        support_resistance_valid=True,
        rrr_meets_minimum=True,
        liquidation_buffer_safe=True,
        account_risk_ok=True,
        margin_drawdown_ok=True,
        forbidden_conditions_clear=True,
        timestamp=datetime.fromtimestamp(1234567890)
    )

def test_response_handler_with_success_string():
    """Test ResponseHandler with various 'success' string responses."""
    logger.info("=== Testing ResponseHandler with 'success' strings ===")
    
    # Import after path setup
    from trading_bot import ResponseHandler
    
    test_cases = [
        '"success"',
        'success',
        '"SUCCESS"',
        'SUCCESS',
        '{"success": true}',
        '{"status": "success"}',
        True,
        {'success': True, 'data': {'order_id': '12345'}}
    ]
    
    for i, test_response in enumerate(test_cases):
        logger.info(f"Test case {i+1}: {test_response} (type: {type(test_response)})")
        try:
            result = ResponseHandler.validate_order_response(test_response)
            logger.info(f"✅ Result: {result}")
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
        logger.info("-" * 50)

def test_signal_strategy_routing():
    """Test VWAP_SCALPING strategy routing logic."""
    logger.info("=== Testing Strategy Routing Logic ===")
    
    signal = create_vwap_scalping_signal()
    
    logger.info(f"Signal strategy: {signal.strategy}")
    logger.info(f"Strategy type: {type(signal.strategy)}")
    logger.info(f"Strategy enum comparison: {signal.strategy == StrategyType.VWAP_SCALPING}")
    logger.info(f"Is GRID_TRADING? {signal.strategy == StrategyType.GRID_TRADING}")
    logger.info(f"Should use standard execution? {signal.strategy != StrategyType.GRID_TRADING}")
    
    # Test the actual routing logic
    if signal.strategy == StrategyType.GRID_TRADING:
        logger.error("🚨 VWAP_SCALPING incorrectly routed to GRID execution!")
    else:
        logger.info("✅ VWAP_SCALPING correctly routed to STANDARD execution")

def simulate_full_execution_flow():
    """Simulate the full VWAP_SCALPING execution flow."""
    logger.info("=== Simulating Full VWAP_SCALPING Execution Flow ===")
    
    try:
        # Create signal
        signal = create_vwap_scalping_signal()
        logger.info(f"Created signal: {signal}")
        
        # Import TradingBot (this might fail due to dependencies)
        from trading_bot import TradingBot
        
        # Mock the dependencies
        mock_client = Mock()
        mock_client.place_order.return_value = '"success"'  # This is the problematic response
        
        mock_risk_manager = Mock()
        mock_risk_manager.get_position_size.return_value = 0.1
        mock_risk_manager.request_capital_allocation.return_value = {
            "approved": True,
            "allocated_amount": 1000.0
        }
        
        mock_event_bus = Mock()
        mock_signal_logger = Mock()
        
        # Create bot instance with mocked dependencies
        bot = TradingBot.__new__(TradingBot)
        bot.client = mock_client
        bot.risk_manager = mock_risk_manager
        bot.event_bus = mock_event_bus
        bot.signal_logger = mock_signal_logger
        bot.execution_layer = None
        
        # Mock supporting methods
        bot._get_account_balance = Mock(return_value=10000.0)
        bot._get_current_exposure = Mock(return_value=0.0)
        
        logger.info("Starting execution simulation...")
        
        # Call the coordination method directly
        bot._coordinate_signal_execution(signal)
        
        logger.info("✅ Execution completed successfully")
        
    except Exception as e:
        logger.error(f"❌ Execution failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Check if this is the 'success' error
        if '"success"' in str(e):
            logger.error("🚨 REPRODUCED THE 'SUCCESS' ERROR!")
            logger.error("🔍 This confirms the issue exists in the current code")

def test_client_place_order_responses():
    """Test different Pacifica client response scenarios."""
    logger.info("=== Testing Pacifica Client Response Scenarios ===")
    
    # Simulate different client responses
    test_responses = [
        '"success"',  # The problematic response
        '{"success": true, "data": {"order_id": "12345"}}',  # Expected JSON
        True,  # Boolean response
        {'success': True, 'data': {'order_id': '12345'}},  # Dict response
    ]
    
    for i, response in enumerate(test_responses):
        logger.info(f"Test {i+1}: Simulating client response: {response}")
        
        # Mock client
        mock_client = Mock()
        mock_client.place_order.return_value = response
        
        # Test ResponseHandler directly
        try:
            from trading_bot import ResponseHandler
            result = ResponseHandler.validate_order_response(response)
            logger.info(f"✅ ResponseHandler result: {result}")
        except Exception as e:
            logger.error(f"❌ ResponseHandler error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    logger.info("Starting VWAP_SCALPING debugging test...")
    
    # Run individual tests
    test_response_handler_with_success_string()
    test_signal_strategy_routing()
    test_client_place_order_responses()
    
    # Try the full simulation (might fail due to missing dependencies)
    try:
        simulate_full_execution_flow()
    except ImportError as e:
        logger.warning(f"Could not run full simulation due to import error: {e}")
    except Exception as e:
        logger.error(f"Full simulation failed: {e}")
    
    logger.info("VWAP_SCALPING debugging test completed. Check vwap_scalping_test.log for details.")