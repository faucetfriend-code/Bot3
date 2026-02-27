"""
Unit tests for configuration system.
"""

import pytest
from pathlib import Path
from config import (
    BotConfig,
    AccountConfig,
    PositionSizingConfig,
    StopLossConfig,
    RRRConfig,
    TimeStopConfig,
    MarketConfig,
    StrategyConfig,
    RiskLimitsConfig,
    VolumeConfig,
    IndicatorsConfig,
    APIConfig,
    create_default_config,
    get_config,
    TradingMode,
    AssetClass,
)


class TestAccountConfig:
    """Test account configuration."""

    def test_valid_account_config(self):
        """Test valid account configuration."""
        config = AccountConfig(
            initial_balance=1000.0, currency="USDC", mode=TradingMode.PAPER
        )
        assert config.initial_balance == 1000.0
        assert config.currency == "USDC"
        assert config.mode == TradingMode.PAPER

    def test_invalid_balance(self):
        """Test invalid balance raises error."""
        with pytest.raises(ValueError):
            AccountConfig(initial_balance=-100.0)

    def test_invalid_margin_allocation(self):
        """Test invalid margin allocation."""
        with pytest.raises(ValueError):
            AccountConfig(max_total_margin_allocation=1.5)


class TestPositionSizingConfig:
    """Test position sizing configuration."""

    def test_valid_position_sizing(self):
        """Test valid position sizing config."""
        config = PositionSizingConfig()
        assert config.min_margin_allocation == 100.0
        assert config.default_leverage == 15
        assert config.max_leverage == 20

    def test_invalid_leverage_range(self):
        """Test invalid leverage range."""
        with pytest.raises(ValueError):
            PositionSizingConfig(min_leverage=20, max_leverage=15)


class TestStopLossConfig:
    """Test stop-loss configuration."""

    def test_valid_stop_config(self):
        """Test valid stop-loss config."""
        config = StopLossConfig()
        assert "min_stop_pct" in config.btc_eth
        assert config.btc_eth["min_stop_pct"] == 0.025

    def test_stop_validation(self):
        """Test stop distance validation."""
        from risk import validate_stop_loss

        # Valid stop
        is_valid, is_optimal, msg = validate_stop_loss(AssetClass.BTC_ETH, 0.035)
        assert is_valid
        assert is_optimal

        # Invalid stop (too tight)
        is_valid, is_optimal, msg = validate_stop_loss(AssetClass.BTC_ETH, 0.01)
        assert not is_valid


class TestRRRConfig:
    """Test RRR configuration."""

    def test_valid_rrr_config(self):
        """Test valid RRR config."""
        config = RRRConfig()
        assert config.minimum_rrr == 2.0
        assert config.target_rrr == 3.0

    def test_invalid_rrr(self):
        """Test invalid RRR values."""
        with pytest.raises(ValueError):
            RRRConfig(minimum_rrr=0.5)


class TestStrategyConfig:
    """Test strategy configuration."""

    def test_valid_strategy_config(self):
        """Test valid strategy config."""
        config = StrategyConfig()
        assert config.trend_following["allocation"] == 0.75
        assert config.breakout["allocation"] == 0.20
        assert config.liquidation_capture["allocation"] == 0.05

    def test_invalid_allocation_sum(self):
        """Test invalid allocation sum."""
        with pytest.raises(ValueError):
            StrategyConfig(
                trend_following={"allocation": 0.8},
                breakout={"allocation": 0.3},
                liquidation_capture={"allocation": 0.1},
            )


class TestBotConfig:
    """Test main bot configuration."""

    def test_create_default_config(self):
        """Test creating default configuration."""
        config = create_default_config()
        assert isinstance(config, BotConfig)
        assert config.account.initial_balance == 1000.0
        assert config.market.primary_timeframe == "15min"

    def test_config_validation(self):
        """Test configuration validation."""
        config = create_default_config()
        # Should not raise any validation errors
        assert config.account.initial_balance > 0


class TestConfigIntegration:
    """Test configuration integration."""

    def test_get_config(self):
        """Test getting configuration instance."""
        config = get_config()
        assert isinstance(config, BotConfig)

    def test_config_persistence(self):
        """Test configuration persistence."""
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2  # Should return same instance
