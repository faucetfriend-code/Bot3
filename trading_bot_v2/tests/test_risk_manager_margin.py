"""
Test suite for RiskManager margin safety enhancements.

Tests the new margin safety functionality:
- Margin data retrieval and caching
- Margin safety checks
- Available margin calculations
- Integration with position validation
"""

import pytest
import time
from unittest.mock import MagicMock, patch
from typing import Dict, Any

# Import the RiskManager
from trading_bot_v2.risk_manager import RiskManager, RiskProfile


class MockPacificaClient:
    """Mock Pacifica client for testing margin functionality."""

    def __init__(self, balance_data: Dict[str, Any] = None):
        self._balance_data = balance_data or {}

    def get_balance(self) -> Dict[str, Any]:
        """Return mock balance data."""
        return self._balance_data


class TestRiskManagerMarginSafety:
    """Test cases for RiskManager margin safety enhancements."""

    def test_init_with_margin_params(self):
        """Test RiskManager initializes with margin safety parameters."""
        mock_client = MagicMock()
        rm = RiskManager(
            max_portfolio_risk_pct=0.05,
            max_portfolio_exposure_pct=0.15,
            client=mock_client,
            max_margin_utilization_pct=0.75,
            maintenance_margin_buffer_pct=0.15,
        )

        assert rm.client == mock_client
        assert rm.max_margin_utilization_pct == 0.75
        assert rm.maintenance_margin_buffer_pct == 0.15
        assert rm._margin_cache_ttl == 30

    def test_init_without_client(self):
        """Test RiskManager works without client (backward compatibility)."""
        rm = RiskManager()

        assert rm.client is None
        assert rm.max_margin_utilization_pct == 0.75  # Default value
        assert rm.maintenance_margin_buffer_pct == 0.15  # Default value

    def test_get_margin_data_with_mock_client(self):
        """Test margin data retrieval with mock client."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.50",
                "total_margin_used": "2500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        margin_data = rm.get_margin_data()

        assert margin_data is not None
        assert margin_data["account_equity"] == 10000.50
        assert margin_data["total_margin_used"] == 2500.00
        assert margin_data["cross_mmr"] == 1000.00
        assert margin_data["available_to_spend"] == 7500.00
        assert margin_data["balance"] == 8000.00
        assert abs(margin_data["margin_utilization_pct"] - 0.25) < 0.001  # 2500/10000

    def test_get_margin_data_caching(self):
        """Test margin data is cached properly."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        # First call should fetch from client
        data1 = rm.get_margin_data()
        assert data1 is not None

        # Second call should use cache
        data2 = rm.get_margin_data()
        assert data2 is data1  # Same object from cache

    def test_get_margin_data_force_refresh(self):
        """Test force refresh bypasses cache."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        # First call
        data1 = rm.get_margin_data()

        # Force refresh
        data2 = rm.get_margin_data(force_refresh=True)

        assert data2 is not data1  # Different object
        assert data2["account_equity"] == data1["account_equity"]

    def test_get_margin_data_no_client(self):
        """Test get_margin_data returns None without client."""
        rm = RiskManager()
        margin_data = rm.get_margin_data()

        assert margin_data is None

    def test_check_margin_safety_safe(self):
        """Test margin safety check passes with safe levels."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",  # 25% utilization
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        result = rm.check_margin_safety(proposed_margin=1000.0)

        assert result["safe"] is True
        assert result["utilization_after"] == 0.35  # (2500+1000)/10000
        assert result["available_margin"] > 0
        assert result["reason"] == ""

    def test_check_margin_safety_high_utilization(self):
        """Test margin safety check fails with high utilization."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "8000.00",  # 80% utilization (exceeds 75% max)
                "cross_mmr": "1000.00",
                "available_to_spend": "2000.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        result = rm.check_margin_safety()

        assert result["safe"] is False
        assert "80.0%" in result["reason"]
        assert "75.0%" in result["reason"]

    def test_check_margin_safety_proposed_exceeds_max(self):
        """Test margin safety check fails when proposed position exceeds max."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "6000.00",  # 60% utilization
                "cross_mmr": "1000.00",
                "available_to_spend": "4000.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        # Propose position that would exceed 75% max
        result = rm.check_margin_safety(proposed_margin=2000.0)

        assert result["safe"] is False
        assert "80.0%" in result["reason"]  # (6000+2000)/10000 = 80%

    def test_check_margin_safety_maintenance_margin(self):
        """Test margin safety check fails below maintenance margin buffer."""
        mock_balance = {
            "raw_response": {
                "account_equity": "1100.00",  # Only 10% above cross_mmr
                "total_margin_used": "500.00",
                "cross_mmr": "1000.00",  # Requires 15% buffer (1150)
                "available_to_spend": "600.00",
                "balance": "1000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        result = rm.check_margin_safety()

        assert result["safe"] is False
        assert "maintenance margin" in result["reason"].lower()

    def test_check_margin_safety_low_buffer_warning(self):
        """Test margin safety check warns on low maintenance margin buffer."""
        mock_balance = {
            "raw_response": {
                "account_equity": "1090.00",  # 9% above cross_mmr (>15% required fails, but <10% triggers warning)
                "total_margin_used": "500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "590.00",
                "balance": "1000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client, maintenance_margin_buffer_pct=0.05)  # Lower buffer to 5%

        result = rm.check_margin_safety()

        assert result["safe"] is True  # 9% > 5% required
        assert len(result["warnings"]) > 0
        assert "buffer" in result["warnings"][0].lower()

    def test_check_margin_safety_insufficient_available(self):
        """Test margin safety check fails when insufficient available margin."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "1000.00",  # Limited available
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        result = rm.check_margin_safety(proposed_margin=5000.0)

        assert result["safe"] is False
        assert "insufficient available margin" in result["reason"].lower()

    def test_calculate_margin_requirement(self):
        """Test margin requirement calculation."""
        rm = RiskManager()

        # Basic calculation
        margin = rm.calculate_margin_requirement(
            position_notional=10000.0,
            leverage=1.0,
            margin_factor=0.1,
        )
        # Base: 10000 * 0.1 = 1000
        # With 5% buffer: 1000 * 1.05 = 1050
        assert margin == 1050.0

    def test_calculate_margin_requirement_with_leverage(self):
        """Test margin requirement with leverage."""
        rm = RiskManager()

        margin = rm.calculate_margin_requirement(
            position_notional=10000.0,
            leverage=5.0,
            margin_factor=0.1,
        )
        # Base: 10000 * 0.1 = 1000
        # With leverage: 1000 / 5 = 200
        # With 5% buffer: 200 * 1.05 = 210
        assert margin == 210.0

    def test_get_available_margin(self):
        """Test available margin calculation."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",  # 25% used
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        available = rm.get_available_margin()

        # Max allowed: 10000 * 0.75 = 7500
        # Already used: 2500
        # Equity-based available: 7500 - 2500 = 5000
        # API available: 7500
        # Conservative: min(5000, 7500) = 5000
        assert available == 5000.0

    def test_get_margin_summary_healthy(self):
        """Test margin summary with healthy status."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",  # 25% - healthy
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        summary = rm.get_margin_summary()

        assert summary["status"] == "healthy"
        assert summary["utilization_pct"] == 25.0
        assert summary["account_equity"] == 10000.0
        assert summary["total_margin_used"] == 2500.0
        assert summary["safe_for_new_positions"] is True

    def test_get_margin_summary_warning(self):
        """Test margin summary with warning status."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "6200.00",  # 62% - warning (>60%)
                "cross_mmr": "1000.00",
                "available_to_spend": "3800.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        summary = rm.get_margin_summary()

        assert summary["status"] == "warning"
        assert summary["utilization_pct"] == 62.0
        assert summary["safe_for_new_positions"] is True  # Still safe

    def test_get_margin_summary_critical(self):
        """Test margin summary with critical status."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "8000.00",  # 80% - critical (>75%)
                "cross_mmr": "1000.00",
                "available_to_spend": "2000.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        summary = rm.get_margin_summary()

        assert summary["status"] == "critical"
        assert summary["utilization_pct"] == 80.0
        assert summary["safe_for_new_positions"] is False

    def test_get_margin_summary_unavailable(self):
        """Test margin summary when data unavailable."""
        rm = RiskManager()  # No client

        summary = rm.get_margin_summary()

        assert summary["status"] == "unavailable"
        assert "error" in summary

    def test_validate_margin_for_position(self):
        """Test validate_margin_for_position method."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        result = rm.validate_margin_for_position(
            position_notional=10000.0,
            leverage=5.0,  # Lower margin requirement due to leverage
        )

        assert "safe" in result
        assert "utilization_after" in result
        assert "available_margin" in result

    def test_parse_float_safe(self):
        """Test safe float parsing with various inputs."""
        rm = RiskManager()

        assert rm._parse_float_safe("1000.50") == 1000.50
        assert rm._parse_float_safe("1,000.50") == 1000.50  # With comma
        assert rm._parse_float_safe("$1000.50") == 1000.50  # With currency
        assert rm._parse_float_safe(1000.50) == 1000.50  # Already float
        assert rm._parse_float_safe(None, default=500.0) == 500.0  # None
        assert rm._parse_float_safe("invalid", default=500.0) == 500.0  # Invalid

    def test_backward_compatibility(self):
        """Test that RiskManager still works without margin features."""
        rm = RiskManager(
            max_portfolio_risk_pct=0.05,
            max_portfolio_exposure_pct=0.15,
        )

        # Existing methods should still work
        from unittest.mock import MagicMock
        signal = MagicMock()
        signal.entry_price = 100.0
        signal.stop_loss = 90.0
        signal.risk_profile = RiskProfile.MEDIUM.value

        quantity = rm.get_position_size(
            signal=signal,
            account_balance=10000.0,
            current_exposure=0.0,
        )

        assert quantity > 0

        is_valid = rm.validate_position_size(
            quantity=quantity,
            account_balance=10000.0,
            current_exposure=0.0,
            entry_price=100.0,
        )

        assert is_valid is True


class TestRiskManagerMarginIntegration:
    """Integration tests for margin safety with existing RiskManager features."""

    def test_margin_check_with_capital_allocation(self):
        """Test margin check works alongside capital allocation."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)

        # Request capital allocation (use low exposure to stay within limits)
        allocation = rm.request_capital_allocation(
            symbol="BTC",
            requested_amount=500.0,  # Request smaller amount
            strategy="TEST_STRATEGY",
            account_balance=10000.0,
            current_exposure=500.0,  # Low existing exposure
        )

        assert allocation["approved"] is True
        assert allocation["allocated_amount"] <= 500.0

        # Verify margin check would allow this allocation
        margin_check = rm.check_margin_safety(proposed_margin=allocation["allocated_amount"] * 0.1)
        assert margin_check["safe"] is True

    def test_margin_data_cache_ttl_expiration(self):
        """Test that cache expires after TTL."""
        mock_balance = {
            "raw_response": {
                "account_equity": "10000.00",
                "total_margin_used": "2500.00",
                "cross_mmr": "1000.00",
                "available_to_spend": "7500.00",
                "balance": "8000.00",
            }
        }
        mock_client = MockPacificaClient(mock_balance)
        rm = RiskManager(client=mock_client)
        rm._margin_cache_ttl = 0.1  # 100ms for testing

        # First call
        data1 = rm.get_margin_data()
        assert data1 is not None

        # Wait for cache to expire
        time.sleep(0.15)

        # Second call should fetch fresh data
        data2 = rm.get_margin_data()
        assert data2 is not None
        # Should be same values but different objects
        assert data2["account_equity"] == data1["account_equity"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
