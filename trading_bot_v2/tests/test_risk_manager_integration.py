"""
Comprehensive tests for RiskManager with all 8 strategies.
"""

import pytest
from unittest.mock import Mock, patch
from typing import Dict, Any, List, Optional
from datetime import datetime

from risk_manager import RiskManager, RiskProfile
from trading_bot_v2.models import Signal, OrderSide, StrategyType, AssetClass


class TestRiskManagerIntegration:
    """
    Test suite for RiskManager with all 8 strategies.
    """

    @pytest.fixture
    def risk_manager(self) -> RiskManager:
        """Create a RiskManager instance with default settings."""
        return RiskManager(
            max_portfolio_risk_pct=0.05,
            max_portfolio_exposure_pct=0.15,
        )

    @pytest.fixture
    def mock_signal(self) -> Signal:
        """Create a mock signal with default values."""
        return Signal(
            strategy=StrategyType.MEAN_REVERSION,
            asset="BTCUSDT",
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=50000.0,
            stop_loss=49000.0,
            confidence=0.7,
            quantity=0.001,
            timestamp=datetime.now(),
            risk_profile=None,
        )

    def test_strategy_risk_profile_assignment(self, risk_manager: RiskManager):
        """
        Test that all 8 strategies get correct risk profiles.
        """
        test_cases = [
            ("mean_reversion", RiskProfile.MEDIUM),
            ("meanreversion", RiskProfile.MEDIUM),  # No underscore
            ("ma_crossover", RiskProfile.MEDIUM),
            ("macrossover", RiskProfile.MEDIUM),  # No underscore
            ("ma crossover", RiskProfile.MEDIUM),  # Space
            ("grid_trading", RiskProfile.MEDIUM),  # Upgraded from LOW
            ("gridtrading", RiskProfile.MEDIUM),  # No underscore
            ("grid trading", RiskProfile.MEDIUM),  # Space
            ("liquidation_capture", RiskProfile.HIGH),
            ("liquidationcapture", RiskProfile.HIGH),  # No underscore
            ("liquidation capture", RiskProfile.HIGH),  # Space
            ("trend_following", RiskProfile.HIGH),
            ("trendfollowing", RiskProfile.HIGH),  # No underscore
            ("trend following", RiskProfile.HIGH),  # Space
            # Advanced Strategies - Feb 2026
            ("vwap_scalping", RiskProfile.MEDIUM),
            ("vwapscalping", RiskProfile.MEDIUM),  # No underscore
            ("vwap scalping", RiskProfile.MEDIUM),  # Space
            ("funding_arbitrage", RiskProfile.LOW),
            ("fundingarbitrage", RiskProfile.LOW),  # No underscore
            ("funding arbitrage", RiskProfile.LOW),  # Space
            ("momentum_scalping", RiskProfile.HIGH),
            ("momentumscalping", RiskProfile.HIGH),  # No underscore
            ("momentum scalping", RiskProfile.HIGH),  # Space
            ("order_book_imbalance", RiskProfile.MEDIUM),
            ("orderbookimbalance", RiskProfile.MEDIUM),  # No underscore
            ("order book imbalance", RiskProfile.MEDIUM),  # Space
        ]

        for strategy_name, expected_profile in test_cases:
            signal = Mock()
            signal.strategy = strategy_name
            signal.entry_price = 50000.0
            signal.stop_loss = 49000.0
            signal.asset = "BTCUSDT"

            # Test get_strategy_risk_profile directly
            profile = risk_manager.get_strategy_risk_profile(strategy_name)
            assert profile == expected_profile.value, \
                f"Strategy {strategy_name} should have {expected_profile.value} profile"

            # Test automatic assignment in get_position_size
            quantity = risk_manager.get_position_size(signal, 10000.0, 0.0)
            assert quantity > 0, f"Position size should be positive for {strategy_name}"

    def test_position_sizing_with_all_strategies(
        self, risk_manager: RiskManager, mock_signal: Signal
    ):
        """
        Test position sizing works correctly for all strategy types.
        """
        strategies = [
            "mean_reversion",
            "ma_crossover",
            "grid_trading",
            "liquidation_capture",
            "trend_following",
            "vwap_scalping",
            "funding_arbitrage",
            "momentum_scalping",
            "order_book_imbalance",
        ]

        account_balance = 10000.0
        current_exposure = 0.0

        for strategy in strategies:
            signal = Signal(
                strategy=StrategyType(strategy),
                asset="BTCUSDT",
                asset_class=AssetClass.PERPETUAL,
                side=OrderSide.BUY,
                entry_price=50000.0,
                stop_loss=49000.0,
                confidence=0.7,
                quantity=0.001,
                timestamp=datetime.now(),
                risk_profile=None,
            )

            quantity = risk_manager.get_position_size(signal, account_balance, current_exposure)
            assert quantity > 0, f"Position size should be positive for {strategy}"
            assert quantity >= 1.0, "Position size should respect minimum quantity"

            # Validate the position size
            is_valid = risk_manager.validate_position_size(
                quantity, account_balance, current_exposure, signal.entry_price
            )
            assert is_valid, f"Position size should be valid for {strategy}"

    def test_edge_cases_and_invalid_strategies(self, risk_manager: RiskManager):
        """
        Test edge cases and invalid strategy names.
        """
        # Test invalid strategy name
        invalid_strategy = "invalid_strategy_name"
        signal = Mock()
        signal.strategy = invalid_strategy
        signal.entry_price = 50000.0
        signal.stop_loss = 49000.0
        signal.asset = "BTCUSDT"

        # Should default to MEDIUM risk profile
        profile = risk_manager.get_strategy_risk_profile(invalid_strategy)
        assert profile == RiskProfile.MEDIUM.value, \
            "Invalid strategies should default to MEDIUM risk profile"

        # Test zero entry price
        signal.entry_price = 0
        quantity = risk_manager.get_position_size(signal, 10000.0, 0.0)
        assert quantity == 1.0, "Zero entry price should return minimum quantity"

        # Test zero stop loss
        signal.entry_price = 50000.0
        signal.stop_loss = 50000.0
        quantity = risk_manager.get_position_size(signal, 10000.0, 0.0)
        assert quantity == 1.0, "Zero stop distance should return minimum quantity"

        # Test negative entry price
        signal.entry_price = -100.0
        quantity = risk_manager.get_position_size(signal, 10000.0, 0.0)
        assert quantity == 1.0, "Negative entry price should return minimum quantity"

    def test_risk_profile_normalization(self, risk_manager: RiskManager):
        """
        Test that strategy names with different formats are normalized correctly.
        """
        test_cases = [
            ("mean_reversion", "meanreversion"),
            ("ma_crossover", "macrossover"),
            ("grid_trading", "gridtrading"),
            ("liquidation_capture", "liquidationcapture"),
            ("trend_following", "trendfollowing"),
            ("vwap_scalping", "vwapscalping"),
            ("funding_arbitrage", "fundingarbitrage"),
            ("momentum_scalping", "momentumscalping"),
            ("order_book_imbalance", "orderbookimbalance"),
        ]

        for canonical, alternative in test_cases:
            profile1 = risk_manager.get_strategy_risk_profile(canonical)
            profile2 = risk_manager.get_strategy_risk_profile(alternative)
            assert profile1 == profile2, \
                f"Different formats of {canonical} should return same profile"

    def test_capital_allocation_authoritative_mode(
        self, risk_manager: RiskManager
    ):
        """
        Test capital allocation in authoritative mode.
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

        # Test exposure limit
        result = risk_manager.request_capital_allocation(
            symbol="BTCUSDT",
            requested_amount=account_balance * 0.20,  # 20% requested
            strategy="mean_reversion",
            account_balance=account_balance,
            current_exposure=account_balance * 0.10,  # 10% already exposed
        )
        max_additional = account_balance * 0.15 - account_balance * 0.10
        assert result["allocated_amount"] <= max_additional, \
            "Allocation should respect exposure limits"

        # Test invalid request (negative amount)
        result = risk_manager.request_capital_allocation(
            symbol="BTCUSDT",
            requested_amount=-100.0,
            strategy="mean_reversion",
            account_balance=account_balance,
            current_exposure=current_exposure,
        )
        assert not result["approved"], "Negative amount should be rejected"

        # Test insufficient balance
        result = risk_manager.request_capital_allocation(
            symbol="BTCUSDT",
            requested_amount=100000.0,
            strategy="mean_reversion",
            account_balance=100.0,
            current_exposure=0.0,
        )
        assert not result["approved"], "Insufficient balance should be rejected"

    def test_migrated_position_validation(self, risk_manager: RiskManager):
        """
        Test migrated position validation with proper risk controls.
        """
        account_balance = 10000.0

        # Test valid migrated position
        position = {
            "side": "long",
            "qty": 0.1,
            "entry_price": 50000.0,
            "has_stop": True,
            "stop_price": 49500.0,
        }
        result = risk_manager.validate_migrated_position("BTCUSDT", position, account_balance)
        assert result["valid"], "Valid position should pass validation"
        assert not result["errors"], "Valid position should have no errors"

        # Test missing stop loss (should fail)
        position_no_stop = position.copy()
        position_no_stop["has_stop"] = False
        result = risk_manager.validate_migrated_position("BTCUSDT", position_no_stop, account_balance)
        assert not result["valid"], "Position without stop should fail"
        assert "MUST have stop loss" in result["errors"], \
            "Error message should mention stop loss requirement"

        # Test position size too large (trend-following allows 10%)
        large_position = position.copy()
        large_position["qty"] = 0.5  # 0.5 * 50000 = 25000 (25% of account)
        result = risk_manager.validate_migrated_position("BTCUSDT", large_position, account_balance)
        assert not result["valid"], "Position exceeding 10% should fail"
        assert "exceeds trend-following limit" in result["warnings"], \
            "Warning should mention trend-following limit"

        # Test total migrated exposure limit (20%)
        # Simulate existing migrated positions
        risk_manager.migrated_positions["BTCUSDT"] = [
            {"side": "long", "qty": 0.05, "entry_price": 50000.0, "has_stop": True}
        ]
        result = risk_manager.validate_migrated_position("BTCUSDT", position, account_balance)
        current_migrated = risk_manager.get_migrated_exposure()
        max_total = account_balance * 0.20
        if current_migrated + (position["qty"] * position["entry_price"]) > max_total:
            assert not result["valid"], "Total migrated exposure should be limited"

    def test_grid_capital_allocation(self, risk_manager: RiskManager):
        """
        Test grid capital allocation with proper limits.
        """
        account_balance = 10000.0
        current_exposure = 0.0

        # Test base grid capital (15% of account)
        grid_capital = risk_manager.get_grid_capital(account_balance, current_exposure, "BTCUSDT")
        assert grid_capital > 0, "Grid capital should be positive"
        assert grid_capital >= account_balance * 0.01, \
            "Grid capital should respect minimum 1% of account"

        # Test exposure limits
        max_grid_exposure = account_balance * risk_manager.max_portfolio_exposure_pct * 0.35
        available_for_grid = max_grid_exposure - risk_manager._get_total_grid_exposure()
        assert grid_capital <= available_for_grid, \
            "Grid capital should respect exposure limits"

        # Test symbol-specific grid limit (50% of allowed grid exposure)
        max_symbol_grid = risk_manager.get_grid_capital(account_balance, current_exposure, "BTCUSDT") * 0.5
        is_valid = risk_manager.validate_grid_exposure(
            "BTCUSDT",
            grid_capital * 1.1,  # 10% over
            account_balance,
            current_exposure,
        )
        assert not is_valid, "Grid capital exceeding symbol limit should be rejected"

        is_valid = risk_manager.validate_grid_exposure(
            "BTCUSDT",
            grid_capital * 0.9,  # 10% under
            account_balance,
            current_exposure,
        )
        assert is_valid, "Grid capital within limits should be accepted"

    def test_exposure_summary(self, risk_manager: RiskManager):
        """
        Test exposure summary provides correct information.
        """
        # Set up some test data
        risk_manager.grid_exposure["BTCUSDT"] = 1000.0
        risk_manager.grid_exposure["ETHUSDT"] = 500.0

        summary = risk_manager.get_exposure_summary()
        assert summary["total_grid_exposure"] == 1500.0, \
            "Total grid exposure should match sum of individual exposures"
        assert summary["grid_exposure_by_symbol"]["BTCUSDT"] == 1000.0, \
            "Grid exposure by symbol should be accurate"
        assert summary["approval_mode"] == "authoritative", \
            "Should be in authoritative mode by default"

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
        risk_manager.migrated_positions["BTCUSDT"] = [
            {"side": "long", "qty": 0.1, "entry_price": 50000.0, "has_stop": True}
        ]

        risk_manager.emergency_stop_all()

        assert len(risk_manager.grid_exposure) == 0, "Grid exposure should be cleared"
        assert len(risk_manager._pending_approvals) == 0, "Pending approvals should be cleared"
        assert len(risk_manager.migrated_positions) == 0, "Migrated positions should be cleared"

        # Check that emergency stop logs correctly
        # This would be verified by checking log output in real execution


if __name__ == "__main__":
    pytest.main([__file__, "-v"])