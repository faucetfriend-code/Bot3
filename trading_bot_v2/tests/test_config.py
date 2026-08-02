import pytest
from trading_bot_v2.config import Config


class TestConfig:
    """Test suite for Config class."""

    def test_valid_configuration(self, monkeypatch):
        """Test configuration with all valid environment variables set."""
        env_vars = {
            "DATABASE_PATH": "custom.db",
            "AGENT_WALLET_PRIVATE_KEY": "private_key_123",
            "ACCOUNT_PUBLIC_KEY": "public_key_456",
            "TESTNET": "false",
            "MAX_POSITIONS": "10",
            "DEFAULT_LEVERAGE": "20",
            "MAX_RISK_PER_TRADE": "0.05",
            "LOG_LEVEL": "DEBUG",
        }
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        config = Config()
        config.validate()

        assert config.database_path == "custom.db"
        assert config.pacifica_private_key == "private_key_123"
        assert config.pacifica_public_key == "public_key_456"
        assert config.testnet is False
        assert config.max_positions == 10
        assert isinstance(config.max_positions, int)
        assert config.default_leverage == 20
        assert isinstance(config.default_leverage, int)
        assert config.max_risk_per_trade == 0.05
        assert isinstance(config.max_risk_per_trade, float)
        assert config.log_level == "DEBUG"

    def test_missing_private_key(self, monkeypatch):
        """Test that ValueError is raised when AGENT_WALLET_PRIVATE_KEY is missing."""
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        # config.py calls load_dotenv(override=True) at import, so the
        # operator's .env is already in os.environ. Not setting the key is
        # not the same as it being absent - it has to be removed.
        monkeypatch.delenv("AGENT_WALLET_PRIVATE_KEY", raising=False)

        config = Config()
        with pytest.raises(
            ValueError,
            match="AGENT_WALLET_PRIVATE_KEY environment variable is required",
        ):
            config.validate()

    def test_missing_public_key(self, monkeypatch):
        """Test that ValueError is raised when ACCOUNT_PUBLIC_KEY is missing."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.delenv("ACCOUNT_PUBLIC_KEY", raising=False)

        config = Config()
        with pytest.raises(
            ValueError, match="ACCOUNT_PUBLIC_KEY environment variable is required"
        ):
            config.validate()

    def test_default_values(self, monkeypatch):
        """Test the defaults Config falls back to when nothing is set.

        This asserts what config.py itself declares, not what the
        operator's .env happens to say. Every optional key is removed
        first because load_dotenv(override=True) has already populated
        os.environ from .env by the time this runs.
        """
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        for key in (
            "DATABASE_PATH",
            "TESTNET",
            "MAX_POSITIONS",
            "DEFAULT_LEVERAGE",
            "MAX_RISK_PER_TRADE",
            "LOG_LEVEL",
        ):
            monkeypatch.delenv(key, raising=False)

        config = Config()
        config.validate()

        assert config.database_path == "trading_bot.db"
        assert config.testnet is True
        # config.py declares 15, not 5; .env happens to agree.
        assert config.max_positions == 15
        assert config.default_leverage == 10
        assert config.max_risk_per_trade == 0.02
        assert config.log_level == "INFO"

    def test_pacifica_base_url_testnet(self, monkeypatch):
        """Test pacifica_base_url returns testnet URL when testnet is True."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("TESTNET", "true")

        config = Config()
        assert config.pacifica_base_url == "https://testnet.api.pacifica.network"

    def test_pacifica_base_url_mainnet(self, monkeypatch):
        """Test pacifica_base_url returns mainnet URL when testnet is False."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("TESTNET", "false")

        config = Config()
        assert config.pacifica_base_url == "https://api.pacifica.network"

    def test_type_conversions(self, monkeypatch):
        """Test that type conversions work correctly."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_POSITIONS", "7")
        monkeypatch.setenv("DEFAULT_LEVERAGE", "15")
        monkeypatch.setenv("MAX_RISK_PER_TRADE", "0.03")
        monkeypatch.setenv("TESTNET", "false")

        config = Config()

        assert isinstance(config.max_positions, int)
        assert config.max_positions == 7
        assert isinstance(config.default_leverage, int)
        assert config.default_leverage == 15
        assert isinstance(config.max_risk_per_trade, float)
        assert config.max_risk_per_trade == 0.03
        assert isinstance(config.testnet, bool)
        assert config.testnet is False

    @pytest.mark.parametrize(
        "testnet_value,expected",
        [
            ("true", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("YES", True),
            ("false", False),
            ("FALSE", False),
            ("0", False),
            ("no", False),
            ("NO", False),
            ("invalid", False),  # defaults to False for invalid
        ],
    )
    def test_boolean_parsing_testnet(self, monkeypatch, testnet_value, expected):
        """Test flexible boolean parsing for TESTNET environment variable."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("TESTNET", testnet_value)

        config = Config()
        assert config.testnet is expected

    def test_invalid_max_positions_env_var(self, monkeypatch):
        """Test that ValueError is raised for invalid MAX_POSITIONS."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_POSITIONS", "abc")

        with pytest.raises(ValueError, match="MAX_POSITIONS must be a valid integer"):
            Config()

    def test_invalid_default_leverage_env_var(self, monkeypatch):
        """Test that ValueError is raised for invalid DEFAULT_LEVERAGE."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("DEFAULT_LEVERAGE", "def")

        with pytest.raises(
            ValueError, match="DEFAULT_LEVERAGE must be a valid integer"
        ):
            Config()

    def test_invalid_max_risk_per_trade_env_var(self, monkeypatch):
        """Test that ValueError is raised for invalid MAX_RISK_PER_TRADE."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_RISK_PER_TRADE", "ghi")

        with pytest.raises(
            ValueError, match="MAX_RISK_PER_TRADE must be a valid float"
        ):
            Config()

    def test_validation_max_positions_zero(self, monkeypatch):
        """Test that ValueError is raised when MAX_POSITIONS is zero."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_POSITIONS", "0")

        config = Config()
        with pytest.raises(ValueError, match="MAX_POSITIONS must be positive"):
            config.validate()

    def test_validation_max_positions_negative(self, monkeypatch):
        """Test that ValueError is raised when MAX_POSITIONS is negative."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_POSITIONS", "-1")

        config = Config()
        with pytest.raises(ValueError, match="MAX_POSITIONS must be positive"):
            config.validate()

    def test_validation_default_leverage_zero(self, monkeypatch):
        """Test that ValueError is raised when DEFAULT_LEVERAGE is zero."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("DEFAULT_LEVERAGE", "0")

        config = Config()
        with pytest.raises(ValueError, match="DEFAULT_LEVERAGE must be positive"):
            config.validate()

    def test_validation_default_leverage_negative(self, monkeypatch):
        """Test that ValueError is raised when DEFAULT_LEVERAGE is negative."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("DEFAULT_LEVERAGE", "-5")

        config = Config()
        with pytest.raises(ValueError, match="DEFAULT_LEVERAGE must be positive"):
            config.validate()

    def test_validation_max_risk_zero(self, monkeypatch):
        """Test that ValueError is raised when MAX_RISK_PER_TRADE is zero."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_RISK_PER_TRADE", "0")

        config = Config()
        with pytest.raises(
            ValueError, match="MAX_RISK_PER_TRADE must be between 0 and 1"
        ):
            config.validate()

    def test_validation_max_risk_negative(self, monkeypatch):
        """Test that ValueError is raised when MAX_RISK_PER_TRADE is negative."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_RISK_PER_TRADE", "-0.1")

        config = Config()
        with pytest.raises(
            ValueError, match="MAX_RISK_PER_TRADE must be between 0 and 1"
        ):
            config.validate()

    def test_validation_max_risk_greater_than_one(self, monkeypatch):
        """Test that ValueError is raised when MAX_RISK_PER_TRADE is greater than 1."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("MAX_RISK_PER_TRADE", "1.5")

        config = Config()
        with pytest.raises(
            ValueError, match="MAX_RISK_PER_TRADE must be between 0 and 1"
        ):
            config.validate()

    def test_validation_invalid_log_level(self, monkeypatch):
        """Test that ValueError is raised for invalid LOG_LEVEL."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "private_key_123")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "public_key_456")
        monkeypatch.setenv("LOG_LEVEL", "INVALID")

        config = Config()
        with pytest.raises(ValueError, match="Invalid LOG_LEVEL: INVALID"):
            config.validate()
