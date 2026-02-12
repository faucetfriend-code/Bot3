"""
Universal Grid State Consistency Test Suite
=========================================

Comprehensive tests for the universal grid state consistency manager.
Tests all aspects of grid state synchronization, repair, and validation.

Author: Grid State Consistency System
Version: 1.0.0
"""

import pytest
import unittest.mock as mock
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import sys
import os

# Add the path to import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'trading_bot_v2'))

from universal_grid_state_consistency import (
    UniversalGridStateConsistencyManager,
    GridConsistencyIssue,
    GridRepairResult,
    GridValidationReport
)
from grid_lifecycle_manager import GridState


class TestUniversalGridStateConsistencyManager:
    """Test suite for UniversalGridStateConsistencyManager."""
    
    @pytest.fixture
    def mock_grid_manager(self):
        """Create a mock grid lifecycle manager."""
        manager = MagicMock()
        manager._grids = {}
        manager.save_grid_state = MagicMock()
        return manager
    
    @pytest.fixture
    def mock_database_manager(self):
        """Create a mock database manager."""
        manager = MagicMock()
        
        # Mock the context manager for database connections
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        manager.get_db_connection.return_value = mock_conn
        
        # Mock cursor results
        mock_cursor.fetchall.return_value = []
        mock_conn.execute.return_value = mock_cursor
        
        return manager
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock exchange client."""
        client = MagicMock()
        client.get_orders.return_value = []
        return client
    
    @pytest.fixture
    def consistency_manager(self, mock_grid_manager, mock_database_manager, mock_client):
        """Create consistency manager with mocked dependencies."""
        # Disable continuous monitoring for tests
        with patch.object(UniversalGridStateConsistencyManager, '_start_continuous_monitoring'):
            return UniversalGridStateConsistencyManager(
                grid_lifecycle_manager=mock_grid_manager,
                database_manager=mock_database_manager,
                client=mock_client
            )
    
    def test_ticker_normalization(self, consistency_manager):
        """Test ticker symbol normalization."""
        test_cases = [
            ('AVAX-USDT', 'AVAX'),
            ('BTC-USDT', 'BTC'),
            ('ETH/USDT', 'ETH'),
            ('SOL_PERP', 'SOL'),
            ('AVAX', 'AVAX'),
            ('btc-usdt', 'BTC'),
            ('ETH_USDT', 'ETH'),
            ('DOT-PERP', 'DOT'),
        ]
        
        for input_symbol, expected in test_cases:
            result = consistency_manager.normalize_ticker_symbol(input_symbol)
            assert result == expected, f"Expected {expected}, got {result} for {input_symbol}"
    
    def test_memory_grid_states_extraction(self, consistency_manager, mock_grid_manager):
        """Test extraction of memory grid states."""
        # Setup mock grid data
        mock_grid_manager._grids = {
            'AVAX': {
                'state': GridState.ACTIVE,
                'center_price': 35.5,
                'initial_center': 35.0,
                'grid_capital': 1000.0,
                'grid_spacing': 0.004,
                'num_levels': 8,
                'created_at': datetime.now(),
            }
        }
        
        states = consistency_manager._get_memory_grid_states()
        
        assert 'AVAX' in states
        assert states['AVAX']['state'] == GridState.ACTIVE
        assert states['AVAX']['center_price'] == 35.5
        assert states['AVAX']['source'] == 'memory'
    
    def test_database_grid_states_extraction(self, consistency_manager, mock_database_manager):
        """Test extraction of database grid states."""
        # Setup mock database response
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                'symbol': 'AVAX',
                'state': 'active',
                'grid_capital': 1000.0,
                'grid_spacing': 0.004,
                'num_levels': 8,
                'created_at': datetime.now(),
                'updated_at': datetime.now(),
                'emergency_stop': 0,
                'regime_on_creation': 'ranging_volatile',
                'atr_at_creation': 2.5
            }
        ]
        
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_database_manager.get_db_connection.return_value = mock_conn
        
        states = consistency_manager._get_database_grid_states()
        
        assert 'AVAX' in states
        assert states['AVAX']['state'] == 'active'
        assert states['AVAX']['source'] == 'database'
    
    def test_consistency_issues_identification(self, consistency_manager):
        """Test identification of grid consistency issues."""
        # Test case 1: Missing center price
        memory_state = {'state': 'active', 'grid_capital': 1000}
        database_state = {'state': 'active', 'grid_capital': 1000}
        
        issues = consistency_manager._identify_grid_consistency_issues('AVAX', memory_state, database_state)
        assert GridConsistencyIssue.MISSING_CENTER_PRICE in issues
        assert GridConsistencyIssue.MISSING_INITIAL_CENTER in issues
        
        # Test case 2: Invalid state
        memory_state = {'state': 'invalid_state', 'center_price': 35.0}
        database_state = {'state': 'active', 'grid_capital': 1000}
        
        issues = consistency_manager._identify_grid_consistency_issues('AVAX', memory_state, database_state)
        assert GridConsistencyIssue.INVALID_STATE in issues
        
        # Test case 3: Database mismatch
        memory_state = {'state': 'active', 'center_price': 35.0}
        database_state = {'state': 'disabled', 'grid_capital': 1000}
        
        issues = consistency_manager._identify_grid_consistency_issues('AVAX', memory_state, database_state)
        assert GridConsistencyIssue.DATABASE_MISMATCH in issues
    
    def test_center_price_reconstruction_from_exchange(self, consistency_manager, mock_client):
        """Test center price reconstruction from exchange orders."""
        # Setup mock exchange orders
        mock_client.get_orders.return_value = [
            {'symbol': 'AVAX', 'side': 'buy', 'price': 34.5},
            {'symbol': 'AVAX', 'side': 'sell', 'price': 36.5},
        ]
        
        center_price = consistency_manager._calculate_center_from_exchange_orders('AVAX')
        
        assert center_price == 35.5  # (34.5 + 36.5) / 2
    
    def test_center_price_reconstruction_fallback(self, consistency_manager):
        """Test center price reconstruction fallback to defaults."""
        center_price = consistency_manager._attempt_center_price_reconstruction('BTC', {}, {})
        
        assert center_price == 50000.0  # Default for BTC
    
    def test_grid_repair_missing_metadata(self, consistency_manager, mock_grid_manager):
        """Test repair of missing grid metadata."""
        # Setup incomplete grid data
        mock_grid_manager._grids = {
            'AVAX': {
                'state': 'active',
                'center_price': 35.0,
                # Missing grid_capital, grid_spacing, num_levels
            }
        }
        
        memory_state = {'state': 'active', 'center_price': 35.0}
        database_state = {'state': 'active', 'grid_capital': 1000.0}
        
        result = consistency_manager._repair_missing_metadata('AVAX', memory_state, database_state)
        
        assert result == True
        assert mock_grid_manager._grids['AVAX']['grid_spacing'] == 0.004
        assert mock_grid_manager._grids['AVAX']['num_levels'] == 8
        assert mock_grid_manager._grids['AVAX']['grid_capital'] == 1000.0
    
    def test_grid_repair_invalid_state(self, consistency_manager, mock_grid_manager):
        """Test repair of invalid grid state."""
        # Setup grid with invalid state
        mock_grid_manager._grids = {
            'AVAX': {
                'state': 'invalid_state',
                'center_price': 35.0,
            }
        }
        
        memory_state = {'state': 'invalid_state', 'center_price': 35.0}
        database_state = {'state': 'active', 'grid_capital': 1000.0}
        
        result = consistency_manager._repair_invalid_state('AVAX', memory_state, database_state)
        
        assert result == True
        assert mock_grid_manager._grids['AVAX']['state'] == 'active'  # Should use database state
    
    def test_comprehensive_validation_and_repair(self, consistency_manager, mock_grid_manager, mock_database_manager):
        """Test comprehensive validation and repair process."""
        # Setup inconsistent data
        mock_grid_manager._grids = {
            'AVAX': {
                'state': GridState.ACTIVE,
                'center_price': 35.0,
                'initial_center': 35.0,
                'grid_capital': 1000.0,
                'grid_spacing': 0.004,
                'num_levels': 8,
            }
        }
        
        # Mock database response
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                'symbol': 'AVAX',
                'state': 'active',
                'grid_capital': 1000.0,
                'grid_spacing': 0.004,
                'num_levels': 8,
                'created_at': datetime.now(),
                'updated_at': datetime.now(),
                'emergency_stop': 0,
                'regime_on_creation': 'ranging_volatile',
                'atr_at_creation': 2.5
            }
        ]
        
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_database_manager.get_db_connection.return_value = mock_conn
        
        # Run validation
        report = consistency_manager.validate_and_repair_all_grids(automatic=False)
        
        assert isinstance(report, GridValidationReport)
        assert report.total_symbols == 1
        assert report.consistent_symbols >= 0
        assert isinstance(report.repair_results, list)
        assert isinstance(report.recommendations, list)
    
    def test_force_full_repair(self, consistency_manager):
        """Test forced full repair operation."""
        with patch.object(consistency_manager, 'validate_and_repair_all_grids') as mock_validate:
            mock_validate.return_value = GridValidationReport(
                total_symbols=1,
                consistent_symbols=1,
                inconsistent_symbols=0,
                orphaned_memory_grids=[],
                orphaned_database_grids=[],
                ticker_format_issues=[],
                repair_results=[],
                validation_timestamp=datetime.now(),
                recommendations=[]
            )
            
            result = consistency_manager.force_full_repair(['AVAX'])
            
            assert 'validation_report' in result
            assert 'symbols_processed' in result
            assert result['symbols_processed'] == ['AVAX']
            mock_validate.assert_called_once_with(automatic=False)
    
    def test_emergency_cleanup(self, consistency_manager, mock_grid_manager, mock_database_manager):
        """Test emergency grid cleanup."""
        # Setup mock data
        mock_grid_manager._grids = {'AVAX': {}, 'BTC': {}}
        
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2  # 2 rows deleted
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_database_manager.get_db_connection.return_value = mock_conn
        
        result = consistency_manager.emergency_grid_cleanup()
        
        assert result['memory_cleared'] == 2
        assert result['database_cleared'] == 2
        assert len(mock_grid_manager._grids) == 0  # Should be empty after cleanup
    
    def test_consistency_statistics(self, consistency_manager):
        """Test consistency statistics collection."""
        stats = consistency_manager.get_consistency_statistics()
        
        assert 'total_validations' in stats
        assert 'total_repairs' in stats
        assert 'successful_repairs' in stats
        assert 'repair_success_rate' in stats
        assert 'last_validation' in stats
        assert 'last_repair' in stats
        assert 'validation_interval_minutes' in stats
        assert 'auto_repair_enabled' in stats
        assert 'continuous_monitoring_enabled' in stats
        assert 'recent_repairs' in stats
    
    def test_cross_reference_validation(self, consistency_manager, mock_grid_manager):
        """Test cross-reference validation between has_active_grid and get_all_active_grids."""
        # Setup test data
        mock_grid_manager._grids = {
            'AVAX': {'state': GridState.ACTIVE, 'center_price': 35.0},
            'BTC': {'state': GridState.DISABLED_BY_REGIME, 'center_price': 50000.0},
            'ETH': {'state': GridState.ACTIVE, 'center_price': 3000.0},
        }
        
        # Mock get_all_active_grids
        mock_grid_manager.get_all_active_grids.return_value = [
            {'symbol': 'AVAX', 'state': 'active'},
            {'symbol': 'ETH', 'state': 'active'},
        ]
        
        # Test has_active_grid consistency
        assert consistency_manager.has_active_grid('AVAX') == True
        assert consistency_manager.has_active_grid('BTC') == False
        assert consistency_manager.has_active_grid('ETH') == True
        assert consistency_manager.has_active_grid('SOL') == False
        
        # Test ticker normalization
        assert consistency_manager.has_active_grid('AVAX-USDT') == True
        assert consistency_manager.has_active_grid('avax-usdt') == True
    
    @pytest.mark.parametrize("symbol_format,expected", [
        ('AVAX', True),
        ('AVAX-USDT', True),
        ('AVAX/USDT', True),
        ('AVAX_USDT', True),
        ('avax', True),
        ('AVAX-PERP', True),
        ('BTC', False),
        ('ETH', False),
        ('SOL', False),
    ])
    def test_has_active_grid_ticker_formats(self, consistency_manager, mock_grid_manager, symbol_format, expected):
        """Test has_active_grid with various ticker formats."""
        mock_grid_manager._grids = {
            'AVAX': {'state': GridState.ACTIVE, 'center_price': 35.0},
        }
        
        result = consistency_manager.has_active_grid(symbol_format)
        assert result == expected


class TestIntegration:
    """Integration tests for grid state consistency."""
    
    @pytest.mark.integration
    def test_real_database_integration(self):
        """Test integration with real database (if available)."""
        # This would require a real database setup
        # Skip for now as it requires database fixtures
        pytest.skip("Integration test requires database setup")
    
    @pytest.mark.integration  
    def test_real_exchange_client_integration(self):
        """Test integration with real exchange client (if available)."""
        # This would require real API credentials
        pytest.skip("Integration test requires exchange API credentials")


if __name__ == '__main__':
    # Run tests
    pytest.main([__file__, '-v', '--tb=short'])