"""
Comprehensive tests for GridLifecycleManager integration with orphaned grid repair
and refresh opportunity logic.

Tests the startup sequence, refresh logic, dynamic spacing, and safety mechanisms.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, List, Optional
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grid_lifecycle_manager import GridLifecycleManager, GridState, GridMetrics
from strategies.grid_trading import GridTradingStrategy
from config import config


class TestGridLifecycleIntegration:
    """Test suite for GridLifecycleManager integration features."""
    
    @pytest.fixture
    def mock_client(self):
        """Mock Pacifica client for testing."""
        client = Mock()
        client.get_balance.return_value = {"balance": "10000.0"}
        client.get_orders.return_value = []
        client.cancel_order.return_value = True
        client.place_order.return_value = {"id": "test_order_123"}
        return client
    
    @pytest.fixture
    def mock_risk_manager(self):
        """Mock risk manager for testing."""
        risk_manager = Mock()
        risk_manager.get_grid_capital.return_value = 1000.0
        risk_manager.validate_grid_exposure.return_value = True
        return risk_manager
    
    @pytest.fixture
    def grid_manager(self, mock_client, mock_risk_manager):
        """Create GridLifecycleManager instance for testing."""
        return GridLifecycleManager(client=mock_client, risk_manager=mock_risk_manager)
    
    @pytest.fixture
    def sample_grid_data(self):
        """Sample grid data for testing."""
        return {
            "state": GridState.ACTIVE,
            "center_price": 100.0,
            "initial_center": 100.0,
            "grid_capital": 1000.0,
            "created_at": datetime.utcnow(),
            "placed_orders": {
                "order_1": {"price": 99.0, "side": "buy", "quantity": 10, "status": "open"},
                "order_2": {"price": 101.0, "side": "sell", "quantity": 10, "status": "open"}
            },
            "last_refresh": datetime.utcnow() - timedelta(hours=2)
        }
    
    @pytest.fixture
    def orphaned_grid_data(self):
        """Orphaned grid data missing center price."""
        return {
            "state": GridState.ACTIVE,
            "grid_capital": 1000.0,
            "created_at": datetime.utcnow() - timedelta(hours=24),
            "placed_orders": {
                "order_1": {"price": 99.0, "side": "buy", "quantity": 10, "status": "open"}
            }
            # Missing center_price and initial_center
        }

    # ==================== STARTUP REPAIR TESTS ====================
    
    def test_startup_grid_repair_normal(self, grid_manager, sample_grid_data):
        """Test that normal grids are unaffected by startup repair."""
        # Add a normal grid
        grid_manager._grids["BTC"] = sample_grid_data
        
        # Run startup repair
        grid_manager._initialize_grid_system()
        
        # Grid should be unchanged
        assert "BTC" in grid_manager._grids
        assert grid_manager._grids["BTC"]["center_price"] == 100.0
        assert grid_manager._grids["BTC"]["state"] == GridState.ACTIVE
    
    def test_detect_orphaned_grids(self, grid_manager, orphaned_grid_data):
        """Test detection of orphaned grids with missing center prices."""
        # Add orphaned grid
        grid_manager._grids["AVAX"] = orphaned_grid_data
        
        # Detect orphaned grids
        orphaned = grid_manager._detect_orphaned_grids()
        
        assert len(orphaned) == 1
        assert "AVAX" in orphaned
        assert orphaned["AVAX"]["issue"] == "missing_center_price"
    
    def test_auto_repair_orphaned_grid(self, grid_manager, orphaned_grid_data, mock_client):
        """Test auto-repair of orphaned grids using exchange data."""
        # Add orphaned grid
        grid_manager._grids["AVAX"] = orphaned_grid_data
        
        # Mock exchange order data for repair
        mock_orders = [
            {"price": 8.5, "side": "buy", "quantity": 100, "status": "open"},
            {"price": 8.7, "side": "sell", "quantity": 100, "status": "open"}
        ]
        mock_client.get_orders.return_value = mock_orders
        
        # Auto-repair
        repaired = grid_manager._auto_repair_orphaned_grid("AVAX")
        
        assert repaired
        # Should have calculated center price from orders
        assert grid_manager._grids["AVAX"]["center_price"] is not None
        assert grid_manager._grids["AVAX"]["center_price"] == 8.6  # (8.5 + 8.7) / 2
    
    def test_remove_unrecoverable_grid(self, grid_manager, orphaned_grid_data):
        """Test removal of unrecoverable orphaned grids."""
        # Add very old orphaned grid
        orphaned_grid_data["created_at"] = datetime.utcnow() - timedelta(days=7)
        grid_manager._grids["AVAX"] = orphaned_grid_data
        
        # Should remove old unrecoverable grid
        removed = grid_manager._auto_repair_orphaned_grid("AVAX")
        
        assert not removed  # Repair failed
        assert "AVAX" not in grid_manager._grids  # Grid was removed
    
    # ==================== REFRESH OPPORTUNITY TESTS ====================
    
    def test_refresh_opportunity_detection(self, grid_manager, sample_grid_data):
        """Test refresh opportunity evaluation with proper thresholds."""
        # Add grid
        grid_manager._grids["BTC"] = sample_grid_data
        
        # Mock signal with high confidence and significant drift
        mock_signal = Mock()
        mock_signal.entry_price = 105.0  # 5% drift from 100.0
        mock_signal.confidence = 0.80  # High confidence
        
        # Mock ATR calculation
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            opportunity = grid_manager._evaluate_refresh_opportunity("BTC", mock_signal)
        
        assert opportunity["should_refresh"]
        assert opportunity["drift_atr"] >= 1.8  # Should meet threshold
        assert opportunity["confidence_improvement"] >= 0.08
    
    def test_refresh_opportunity_rejected_low_drift(self, grid_manager, sample_grid_data):
        """Test refresh rejection due to insufficient drift."""
        grid_manager._grids["BTC"] = sample_grid_data
        
        # Signal with small drift
        mock_signal = Mock()
        mock_signal.entry_price = 101.0  # Only 1% drift
        mock_signal.confidence = 0.80
        
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            opportunity = grid_manager._evaluate_refresh_opportunity("BTC", mock_signal)
        
        assert not opportunity["should_refresh"]
        assert opportunity["drift_atr"] < 1.8
    
    def test_refresh_opportunity_rejected_low_confidence(self, grid_manager, sample_grid_data):
        """Test refresh rejection due to low confidence."""
        grid_manager._grids["BTC"] = sample_grid_data
        
        # Signal with high drift but low confidence
        mock_signal = Mock()
        mock_signal.entry_price = 105.0  # 5% drift
        mock_signal.confidence = 0.65  # Low confidence
        
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            opportunity = grid_manager._evaluate_refresh_opportunity("BTC", mock_signal)
        
        assert not opportunity["should_refresh"]
        assert opportunity["confidence_improvement"] < 0.08
    
    def test_refresh_cooldown_enforcement(self, grid_manager, sample_grid_data):
        """Test that refresh cooldown is properly enforced."""
        grid_manager._grids["BTC"] = sample_grid_data
        # Set recent refresh
        grid_manager._grids["BTC"]["last_refresh"] = datetime.utcnow() - timedelta(minutes=10)
        
        mock_signal = Mock()
        mock_signal.entry_price = 105.0
        mock_signal.confidence = 0.80
        
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            opportunity = grid_manager._evaluate_refresh_opportunity("BTC", mock_signal)
        
        assert not opportunity["should_refresh"]
        assert "cooldown" in opportunity["rejection_reason"]
    
    def test_daily_refresh_limit_enforcement(self, grid_manager, sample_grid_data):
        """Test daily refresh limit enforcement."""
        grid_manager._grids["BTC"] = sample_grid_data
        # Set 3 refreshes already today
        grid_manager._grids["BTC"]["daily_refresh_count"] = 3
        
        mock_signal = Mock()
        mock_signal.entry_price = 105.0
        mock_signal.confidence = 0.80
        
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            opportunity = grid_manager._evaluate_refresh_opportunity("BTC", mock_signal)
        
        assert not opportunity["should_refresh"]
        assert "daily_limit" in opportunity["rejection_reason"]
    
    # ==================== DYNAMIC SPACING TESTS ====================
    
    def test_dynamic_spacing_calculation(self, grid_manager):
        """Test dynamic spacing calculation based on ATR."""
        # Mock market data
        market_data = {
            "5m": Mock()
        }
        # Mock ATR calculation
        with patch('trading_bot_v2.strategies.grid_trading.calculate_atr', return_value=[2.5]):
            spacing = grid_manager._calculate_dynamic_spacing("BTC", market_data)
        
        # Should calculate spacing based on ATR multiplier
        assert spacing > 0
        assert spacing <= config.GRID_MAX_SPACING_PCT
        assert spacing >= config.GRID_MIN_SPACING_PCT
    
    def test_dynamic_spacing_volatility_adjustment(self, grid_manager):
        """Test spacing adjustment based on volatility."""
        # High volatility scenario
        high_vol_data = {"5m": Mock()}
        with patch('trading_bot_v2.strategies.grid_trading.calculate_atr', return_value=[5.0]):  # High ATR
            high_spacing = grid_manager._calculate_dynamic_spacing("BTC", high_vol_data)
        
        # Low volatility scenario  
        low_vol_data = {"5m": Mock()}
        with patch('trading_bot_v2.strategies.grid_trading.calculate_atr', return_value=[1.0]):  # Low ATR
            low_spacing = grid_manager._calculate_dynamic_spacing("BTC", low_vol_data)
        
        # High volatility should have wider spacing
        assert high_spacing > low_spacing
    
    def test_dynamic_spacing_periodic_recalc(self, grid_manager):
        """Test periodic recalculation of dynamic spacing."""
        market_data = {"5m": Mock()}
        
        # First calculation
        with patch('trading_bot_v2.strategies.grid_trading.calculate_atr', return_value=[2.0]):
            spacing1 = grid_manager._calculate_dynamic_spacing("BTC", market_data)
        
        # Should not recalculate if within interval
        with patch('trading_bot_v2.strategies.grid_trading.calculate_atr', return_value=[3.0]) as mock_atr:
            spacing2 = grid_manager._calculate_dynamic_spacing("BTC", market_data)
            mock_atr.assert_not_called()  # Should not recalculate
        
        assert spacing1 == spacing2
    
    # ==================== SAFETY MECHANISM TESTS ====================
    
    def test_emergency_drift_detection(self, grid_manager, sample_grid_data):
        """Test emergency drift detection and halt."""
        grid_manager._grids["BTC"] = sample_grid_data
        
        # Extreme drift signal
        mock_signal = Mock()
        mock_signal.entry_price = 120.0  # 20% drift
        mock_signal.confidence = 0.90
        
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.0):
            opportunity = grid_manager._evaluate_refresh_opportunity("BTC", mock_signal)
        
        assert opportunity["emergency_drift"]
        assert opportunity["drift_atr"] > 3.0  # Should trigger emergency
    
    def test_order_preservation_during_refresh(self, grid_manager, sample_grid_data, mock_client):
        """Test that filled positions are preserved during refresh."""
        # Add grid with filled position
        sample_grid_data["filled_positions"] = [
            {"price": 99.0, "side": "buy", "quantity": 10, "status": "filled"}
        ]
        grid_manager._grids["BTC"] = sample_grid_data
        
        # Mock refresh
        mock_signal = Mock()
        mock_signal.entry_price = 105.0
        mock_signal.confidence = 0.80
        
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            success = grid_manager._recenter_grid("BTC", 105.0, "test_refresh")
        
        assert success
        # Filled position should be preserved
        assert len(grid_manager._grids["BTC"]["filled_positions"]) == 1
        assert grid_manager._grids["BTC"]["filled_positions"][0]["status"] == "filled"
    
    def test_audit_trail_logging(self, grid_manager, sample_grid_data, mock_client):
        """Test comprehensive audit trail logging."""
        grid_manager._grids["BTC"] = sample_grid_data
        
        mock_signal = Mock()
        mock_signal.entry_price = 105.0
        mock_signal.confidence = 0.80
        
        with patch.object(grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            success = grid_manager._recenter_grid("BTC", 105.0, "test_refresh")
        
        assert success
        # Check audit trail
        assert "last_refresh" in grid_manager._grids["BTC"]
        assert "last_refresh_reason" in grid_manager._grids["BTC"]
        assert grid_manager._grids["BTC"]["last_refresh_reason"] == "test_refresh"
    
    # ==================== INTEGRATION TESTS ====================
    
    def test_startup_sequence_integration(self, grid_manager, orphaned_grid_data, mock_client):
        """Test complete startup sequence with orphaned grid repair."""
        # Add orphaned grid
        grid_manager._grids["AVAX"] = orphaned_grid_data
        
        # Mock exchange data for repair
        mock_orders = [{"price": 8.5, "side": "buy", "quantity": 100, "status": "open"}]
        mock_client.get_orders.return_value = mock_orders
        
        # Run complete startup sequence
        grid_manager._initialize_grid_system()
        
        # Orphaned grid should be repaired
        assert "AVAX" in grid_manager._grids
        assert grid_manager._grids["AVAX"]["center_price"] is not None
        assert grid_manager._grids["AVAX"]["center_price"] == 8.5
    
    def test_backward_compatibility(self, grid_manager, sample_grid_data):
        """Test that existing functionality remains unchanged."""
        # Test existing methods still work
        grid_manager._grids["BTC"] = sample_grid_data
        
        assert grid_manager.has_active_grid("BTC")
        assert grid_manager.get_grid_center("BTC") == 100.0
        assert grid_manager.get_grid_state("BTC") == GridState.ACTIVE
    
    def test_configuration_integration(self, grid_manager):
        """Test that configuration parameters are properly integrated."""
        # Test refresh thresholds
        assert hasattr(config, 'GRID_REFRESH_MIN_ATR_DRIFT')
        assert hasattr(config, 'GRID_REFRESH_COOLDOWN_MINUTES')
        assert hasattr(config, 'GRID_MAX_REFRESH_PER_DAY')
        
        # Test dynamic spacing config
        assert hasattr(config, 'GRID_DYNAMIC_SPACING')
        assert hasattr(config, 'GRID_BASE_K')
        assert hasattr(config, 'GRID_MIN_SPACING_PCT')
        assert hasattr(config, 'GRID_MAX_SPACING_PCT')


class TestGridRefreshWorkflow:
    """Test complete grid refresh workflow scenarios."""
    
    @pytest.fixture
    def workflow_grid_manager(self):
        """Grid manager setup for workflow testing."""
        mock_client = Mock()
        mock_risk_manager = Mock()
        return GridLifecycleManager(client=mock_client, risk_manager=mock_risk_manager)
    
    def test_complete_refresh_workflow(self, workflow_grid_manager):
        """Test complete refresh workflow from signal to recenter."""
        # Setup initial grid
        grid_data = {
            "state": GridState.ACTIVE,
            "center_price": 100.0,
            "created_at": datetime.utcnow() - timedelta(hours=2),
            "placed_orders": {
                "order_1": {"price": 99.0, "side": "buy", "quantity": 10, "status": "open"},
                "order_2": {"price": 101.0, "side": "sell", "quantity": 10, "status": "open"}
            }
        }
        workflow_grid_manager._grids["BTC"] = grid_data
        
        # Create refresh opportunity signal
        mock_signal = Mock()
        mock_signal.entry_price = 105.0
        mock_signal.confidence = 0.80
        mock_signal.asset = "BTC"
        
        # Mock ATR and client responses
        with patch.object(workflow_grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            with patch.object(workflow_grid_manager.client, 'cancel_order', return_value=True):
                with patch.object(workflow_grid_manager.client, 'place_order', return_value={"id": "new_order"}):
                    
                    # Step 1: Evaluate opportunity
                    opportunity = workflow_grid_manager._evaluate_refresh_opportunity("BTC", mock_signal)
                    assert opportunity["should_refresh"]
                    
                    # Step 2: Execute recenter
                    success = workflow_grid_manager._recenter_grid("BTC", 105.0, "workflow_test")
                    assert success
                    
                    # Step 3: Verify results
                    assert workflow_grid_manager._grids["BTC"]["center_price"] == 105.0
                    assert workflow_grid_manager._grids["BTC"]["last_refresh_reason"] == "workflow_test"
    
    def test_refresh_failure_recovery(self, workflow_grid_manager):
        """Test recovery from refresh operation failure."""
        # Setup grid
        grid_data = {
            "state": GridState.ACTIVE,
            "center_price": 100.0,
            "placed_orders": {
                "order_1": {"price": 99.0, "side": "buy", "quantity": 10, "status": "open"}
            }
        }
        workflow_grid_manager._grids["BTC"] = grid_data
        
        mock_signal = Mock()
        mock_signal.entry_price = 105.0
        mock_signal.confidence = 0.80
        
        # Mock order cancellation failure
        with patch.object(workflow_grid_manager, '_calculate_atr_for_symbol', return_value=2.5):
            with patch.object(workflow_grid_manager.client, 'cancel_order', side_effect=Exception("Cancel failed")):
                
                # Refresh should fail gracefully
                success = workflow_grid_manager._recenter_grid("BTC", 105.0, "failure_test")
                assert not success
                
                # Original grid should remain intact
                assert workflow_grid_manager._grids["BTC"]["center_price"] == 100.0
                assert "order_1" in workflow_grid_manager._grids["BTC"]["placed_orders"]


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v", "--tb=short"])