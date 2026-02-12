"""
Simple tests for RiskManager functionality.
"""

import pytest
from unittest.mock import Mock
from typing import Dict, Any, List, Optional
from datetime import datetime

from risk_manager import RiskManager, RiskProfile


class TestRiskManagerBasic:
    """
    Basic tests for RiskManager functionality.
    """

    @pytest.fixture
    def risk_manager(self) -> RiskManager:
        """Create a RiskManager instance with default settings."""
        return RiskManager(
            max_portfolio_risk_pct=0.05,
            max_portfolio_exposure_pct=0.15,
        )

    def test_strategy_risk_profile_assignment(self, risk_manager: RiskManager):
        """
        Test that all 8 strategies get correct risk profiles.
        """
        test_cases = [
            ("mean_reversion", RiskProfile.MEDIUM),
            ("ma_crossover", RiskProfile.MEDIUM),
            ("grid_trading", RiskProfile.MEDIUM),  # Upgraded from LOW
            ("liquidation_capture", RiskProfile.HIGH),
            ("trend_following", RiskProfile.HIGH),
            # Advanced Strategies - Feb 2026
            ("vwap_scalping", RiskProfile.MEDIUM),
            ("funding_arbitrage", RiskProfile.LOW),
            ("momentum_scalping", RiskProfile.HIGH),
            ("order_book_imbalance", RiskProfile.MEDIUM),
        ]

        for strategy_name, expected_profile in test_cases:
            profile = risk_manager.get_strategy_risk_profile(strategy_name)
            assert profile == expected_profile.value, \
                f"Strategy {strategy_name} should have {expected_profile.value} profile"

    def test_position_sizing_basic(self, risk_manager: RiskManager):
        """
        Test basic position sizing functionality.
        """
        # Create a mock signal
        signal = Mock()
        signal.strategy = "mean_reversion"
        signal.entry_price = 50000.0
        signal.stop_loss = 49000.0
        signal.asset = "BTCUSDT"
        signal.risk_profile = None  # Should be auto-assigned

        account_balance = 10000.0
        current_exposure = 0.0

        # Test position sizing
        quantity = risk_manager.get_position_size(signal, account_balance, current_exposure)
        assert quantity > 0, "Position size should be positive"
        assert quantity >= 1.0, "Position size should respect minimum quantity"

        # Test validation
        is_valid = risk_manager.validate_position_size(
            quantity, account_balance, current_exposure, signal.entry_price
        )
        assert is_valid, "Position size should be valid"

    def test_edge_cases(self, risk_manager: RiskManager):
        """
        Test edge cases and error handling.
        """
        # Test invalid strategy name
        invalid_strategy = "invalid_strategy_name"
        profile = risk_manager.get_strategy_risk_profile(invalid_strategy)
        assert profile == RiskProfile.MEDIUM.value, \
            "Invalid strategies should default to MEDIUM risk profile"

        # Test zero entry price
        signal = Mock()
        signal.strategy = "mean_reversion"
        signal.entry_price = 0
        signal.stop_loss = 49000.0
        signal.asset = "BTCUSDT"

        quantity = risk_manager.get_position_size(signal, 10000.0, 0.0)
        assert quantity == 1.0, "Zero entry price should return minimum quantity"

        # Test zero stop loss
        signal.entry_price = 50000.0
        signal.stop_loss = 50000.0
        quantity = risk_manager.get_position_size(signal, 10000.0, 0.0)
        assert quantity == 1.0, "Zero stop distance should return minimum quantity"

    def test_capital_allocation(self, risk_manager: RiskManager):
        """
        Test capital allocation functionality.
        """
        account_balance = 10000.0
        current_exposure = 0.0

        # Test valid allocation request
        result = risk_manager.request_capital_allocation(
            symbol="BTCUSDT",
            requested_amount=1000.0,
            strategy="grid_trading",
            account_balance=account_balance,
            current_exposure=current_exposure,
        )
        assert result["approved"], "Valid allocation should be approved"
        assert result["allocated_amount"] <= 1000.0, \
            "Allocated amount should not exceed requested"

        # Test grid trading specific limit (4% of account)
        max_grid_allocation = account_balance * 0.04
        result = risk_manager.request_capital_allocation(
            symbol="BTCUSDT",
            requested_amount=account_balance * 0.10,  # 10% requested
            strategy="grid_trading",
            account_balance=account_balance,
            current_exposure=current_exposure,
        )
        assert result["allocated_amount"] <= max_grid_allocation, \
            "Grid trading should be limited to 4% of account"

    def test_grid_capital(self, risk_manager: RiskManager):
        """
        Test grid capital allocation.
        """
        account_balance = 10000.0
        current_exposure = 0.0

        grid_capital = risk_manager.get_grid_capital(account_balance, current_exposure, "BTCUSDT")
        assert grid_capital > 0, "Grid capital should be positive"
        assert grid_capital >= account_balance * 0.01, \
            "Grid capital should respect minimum 1% of account"

    def test_exposure_summary(self, risk_manager: RiskManager):
        """
        Test exposure summary functionality.
        """
        summary = risk_manager.get_exposure_summary()
        assert "total_grid_exposure" in summary, "Summary should include total grid exposure"
        assert "approval_mode" in summary, "Summary should include approval mode"

    def test_emergency_stop(self, risk_manager: RiskManager):
        """
        Test emergency stop functionality.
        """
        # Set up some test data
        risk_manager.grid_exposure["BTCUSDT"] = 1000.0
        risk_manager._pending_approvals["test_approval"] = {
            "symbol": "BTCUSDT",
            "strategy": "mean_reversion",
            "requested_amount": 1000.0,
            "allocated_amount": 1000.0,
            "account_balance": 10000.0,
            "current_exposure": 0.0,
            "timestamp": 0,
            "status": "approved",
        }

        risk_manager.emergency_stop_all()

        assert len(risk_manager.grid_exposure) == 0, "Grid exposure should be cleared"
        assert len(risk_manager._pending_approvals) == 0, "Pending approvals should be cleared"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
