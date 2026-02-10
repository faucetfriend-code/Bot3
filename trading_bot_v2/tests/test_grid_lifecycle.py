"""
Unit tests for GridLifecycleManager component.

Tests the authoritative grid state management functionality.
"""

import pytest
import sys
import os
from unittest.mock import Mock, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from grid_lifecycle_manager import GridLifecycleManager, GridState


class TestGridLifecycleManager:
    """Test suite for GridLifecycleManager."""

    @pytest.fixture
    def mock_client(self):
        """Mock Pacifica client."""
        return Mock()

    @pytest.fixture
    def mock_risk_manager(self):
        """Mock risk manager."""
        return Mock()

    @pytest.fixture
    def grid_manager(self, mock_client, mock_risk_manager):
        """Create GridLifecycleManager instance."""
        return GridLifecycleManager(mock_client, mock_risk_manager)

    def test_initialization(self, grid_manager):
        """Test proper initialization."""
        assert grid_manager.client is not None
        assert grid_manager.risk_manager is not None
        assert grid_manager._grids == {}

    def test_has_active_grid_no_grid(self, grid_manager):
        """Test has_active_grid returns False when no grid exists."""
        assert not grid_manager.has_active_grid("SUI")

    def test_register_new_grid_success(self, grid_manager):
        """Test successful grid registration."""
        symbol = "SUI"
        capital = 1000.0
        stop_price = 1.50

        grid_manager.register_new_grid(symbol, capital, stop_price)

        assert grid_manager.has_active_grid(symbol)
        grid = grid_manager._grids[symbol]
        assert grid.symbol == symbol
        assert grid.grid_capital == capital
        assert grid.emergency_stop_price == stop_price
        assert grid.status == "active"

    def test_register_duplicate_grid_fails(self, grid_manager):
        """Test that registering duplicate grid raises error."""
        symbol = "SUI"
        grid_manager.register_new_grid(symbol, 1000.0, 1.50)

        with pytest.raises(ValueError, match="Grid already exists"):
            grid_manager.register_new_grid(symbol, 2000.0, 1.60)

    def test_update_grid_levels(self, grid_manager):
        """Test updating grid level counts."""
        symbol = "SUI"
        grid_manager.register_new_grid(symbol, 1000.0, 1.50)

        grid_manager.update_grid_levels(symbol, 5, 10)

        grid = grid_manager._grids[symbol]
        assert grid.active_levels == 5
        assert grid.total_levels == 10

    def test_get_grid_status(self, grid_manager):
        """Test getting grid status information."""
        symbol = "SUI"
        capital = 1000.0
        stop_price = 1.50

        # No grid
        assert grid_manager.get_grid_status(symbol) is None

        # With grid
        grid_manager.register_new_grid(symbol, capital, stop_price)
        status = grid_manager.get_grid_status(symbol)

        assert status is not None
        assert status["symbol"] == symbol
        assert status["status"] == "active"
        assert status["grid_capital"] == capital
        assert status["emergency_stop_price"] == stop_price

    def test_get_all_active_grids(self, grid_manager):
        """Test getting all active grids."""
        # Register multiple grids
        grid_manager.register_new_grid("SUI", 1000.0, 1.50)
        grid_manager.register_new_grid("DOGE", 500.0, 0.10)

        active_grids = grid_manager.get_all_active_grids()
        assert len(active_grids) == 2

        symbols = {grid["symbol"] for grid in active_grids}
        assert symbols == {"SUI", "DOGE"}

    def test_validate_grid_creation(self, grid_manager):
        """Test grid creation validation."""
        # Valid creation
        assert grid_manager.validate_grid_creation("SUI", 1000.0)

        # Register grid
        grid_manager.register_new_grid("SUI", 1000.0, 1.50)

        # Duplicate should be invalid
        assert not grid_manager.validate_grid_creation("SUI", 2000.0)

    def test_emergency_stop_triggered(self, grid_manager, mock_client):
        """Test emergency stop handling."""
        symbol = "SUI"
        grid_manager.register_new_grid(symbol, 1000.0, 1.50)

        # Mock client methods
        mock_client.get_positions.return_value = [
            {"symbol": symbol, "side": "bid", "amount": 100}
        ]
        mock_client.place_order.return_value = {"id": "emergency_order"}

        grid_manager.on_emergency_stop_triggered(symbol)

        # Verify emergency actions
        mock_client.cancel_all_orders.assert_called_with(symbol)
        mock_client.place_order.assert_called_with(symbol, "sell", 100, "market")

        # Grid should be removed
        assert not grid_manager.has_active_grid(symbol)

    def test_regime_disallowed(self, grid_manager, mock_client):
        """Test regime change handling."""
        symbol = "SUI"
        grid_manager.register_new_grid(symbol, 1000.0, 1.50)

        # Mock client methods
        mock_client.get_positions.return_value = [
            {"symbol": symbol, "side": "bid", "amount": 100}
        ]
        mock_client.place_order.return_value = {"id": "close_order"}

        grid_manager.on_regime_disallowed(symbol)

        # Verify controlled exit
        mock_client.cancel_all_orders.assert_called_with(symbol)
        mock_client.place_order.assert_called_with(symbol, "sell", 100, "market")

        # Grid should be marked as disabled
        grid = grid_manager._grids.get(symbol)
        assert grid is not None
        assert grid.status == "paused"

    def test_cleanup_completed_grids(self, grid_manager):
        """Test cleanup of completed grids."""
        # Register a grid and mark it as closed
        symbol = "SUI"
        grid_manager.register_new_grid(symbol, 1000.0, 1.50)
        grid_manager._grids[symbol].status = "closed"

        # Should be cleaned up (mock age check)
        # Note: In real implementation, this would check timestamp
        grid_manager.cleanup_completed_grids()

        # Grid should still exist (age check not mocked)
        assert symbol in grid_manager._grids

    def test_get_grid_statistics(self, grid_manager):
        """Test grid statistics generation."""
        # Register grids
        grid_manager.register_new_grid("SUI", 1000.0, 1.50)
        grid_manager.register_new_grid("DOGE", 500.0, 0.10)

        stats = grid_manager.get_grid_statistics()

        assert stats["total_active_grids"] == 2
        assert stats["total_allocated_capital"] == 1500.0
        assert "SUI" in stats["grids_by_symbol"]
        assert "DOGE" in stats["grids_by_symbol"]
