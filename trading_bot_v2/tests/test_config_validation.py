"""Tests for trading_bot_v2.config_validation (audit finding T1, part 6).

Every test builds its own .env under tmp_path; the repository .env is never
read. The secret strings below are made up and are asserted to be ABSENT
from every report, log line and exception message.
"""

import logging

import pytest

from trading_bot_v2.config_validation import (
    EnvValidationReport,
    StartupConfigError,
    check_api_binding,
    check_live_credentials,
    find_duplicate_env_keys,
    is_loopback_host,
    run_startup_validation,
    validate_env_file,
)

FAKE_KEY = "fake-key-0123456789abcdef01234567"
FAKE_SECRET = "fake-secret-0123456789abcdef0123"
FAKE_PASS = "fake-pass1"

SHADOWED_BLOFIN_ENV = f"""# Blofin account info
BLOFIN_API_KEY={FAKE_KEY}
BLOFIN_API_SECRET={FAKE_SECRET}
BLOFIN_PASSPHRASE={FAKE_PASS}
TESTNET=true

# ... many lines later ...
EXCHANGE=blofin
BLOFIN_API_KEY=
BLOFIN_API_SECRET=
BLOFIN_PASSPHRASE=
BLOFIN_DEMO=false
"""


class TestDuplicateKeyDetector:
    """find_duplicate_env_keys on raw dotenv text."""

    def test_reports_each_duplicated_key_once_sorted(self):
        """Keys assigned twice are listed once each, in sorted order."""
        text = "B=1\nA=1\nB=2\nC=3\nA=2\nA=3\n"
        assert find_duplicate_env_keys(text) == ["A", "B"]

    def test_ignores_comments_blank_lines_and_values(self):
        """A commented-out assignment or a value containing '=' is not a key."""
        text = "# A=commented\n\nA=x=y\nURL=http://h/?a=1&A=2\n"
        assert find_duplicate_env_keys(text) == []

    def test_handles_export_prefix_and_whitespace(self):
        """`export KEY=` and `KEY = value` count as assignments of KEY."""
        text = "export KEY=1\n  KEY = 2\n"
        assert find_duplicate_env_keys(text) == ["KEY"]

    def test_validate_env_file_reports_duplicates_without_values(self, tmp_path):
        """The report carries key names only; no credential value leaks."""
        dotenv = tmp_path / ".env"
        dotenv.write_text(SHADOWED_BLOFIN_ENV, encoding="utf-8")
        report = validate_env_file(str(dotenv))
        assert report.path == str(dotenv)
        assert report.duplicate_keys == [
            "BLOFIN_API_KEY",
            "BLOFIN_API_SECRET",
            "BLOFIN_PASSPHRASE",
        ]
        rendered = repr(report)
        for secret in (FAKE_KEY, FAKE_SECRET, FAKE_PASS):
            assert secret not in rendered

    def test_missing_file_is_an_empty_report(self, tmp_path):
        """A nonexistent .env is not an error at this layer."""
        report = validate_env_file(str(tmp_path / "absent.env"))
        assert report == EnvValidationReport(path=None)


class TestLiveCredentialCheck:
    """check_live_credentials on resolved (last-wins) mappings."""

    def test_blofin_live_with_empty_credentials_is_refused(self):
        """BLOFIN_DEMO=false with empty keys yields one error naming all three."""
        errors = check_live_credentials(
            {
                "EXCHANGE": "blofin",
                "BLOFIN_DEMO": "false",
                "BLOFIN_API_KEY": "",
                "BLOFIN_API_SECRET": "  ",
                "BLOFIN_PASSPHRASE": None,
            }
        )
        assert len(errors) == 1
        for name in ("BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BLOFIN_PASSPHRASE"):
            assert name in errors[0]

    def test_blofin_demo_does_not_need_live_credentials(self):
        """The demo account uses the BLOFIN_DEMO_* set; live keys may be empty."""
        errors = check_live_credentials(
            {"EXCHANGE": "blofin", "BLOFIN_DEMO": "true", "BLOFIN_API_KEY": ""}
        )
        assert errors == []

    def test_blofin_demo_defaults_to_true_when_absent(self):
        """An absent BLOFIN_DEMO means demo, matching the runtime default."""
        assert check_live_credentials({"EXCHANGE": "blofin"}) == []

    def test_blofin_live_with_credentials_passes(self):
        """Non-empty live credentials produce no error."""
        errors = check_live_credentials(
            {
                "EXCHANGE": "blofin",
                "BLOFIN_DEMO": "false",
                "BLOFIN_API_KEY": FAKE_KEY,
                "BLOFIN_API_SECRET": FAKE_SECRET,
                "BLOFIN_PASSPHRASE": FAKE_PASS,
            }
        )
        assert errors == []

    def test_pacifica_live_with_empty_keys_is_refused(self):
        """EXCHANGE=pacifica + TESTNET=false requires both wallet keys."""
        errors = check_live_credentials(
            {"EXCHANGE": "pacifica", "TESTNET": "false", "ACCOUNT_PUBLIC_KEY": "x"}
        )
        assert len(errors) == 1
        assert "AGENT_WALLET_PRIVATE_KEY" in errors[0]
        assert "ACCOUNT_PUBLIC_KEY" not in errors[0].split("resolve empty:")[1]

    def test_pacifica_testnet_is_not_checked(self):
        """Testnet is not live; the analogous check stays quiet."""
        assert check_live_credentials({"EXCHANGE": "pacifica", "TESTNET": "true"}) == []

    def test_exchange_name_is_trimmed_and_case_folded(self):
        """`EXCHANGE=Blofin ` (as .env has it, with trailing space) still matches."""
        errors = check_live_credentials({"EXCHANGE": "Blofin ", "BLOFIN_DEMO": "false"})
        assert len(errors) == 1

    def test_unknown_exchange_is_not_checked(self):
        """An exchange this module knows nothing about is left to its adapter."""
        assert check_live_credentials({"EXCHANGE": "other", "TESTNET": "false"}) == []


class TestStartupValidation:
    """run_startup_validation end to end on temp .env files."""

    def test_shadowed_live_credentials_refuse_startup(self, tmp_path, caplog):
        """The exact CLAUDE.md scenario: later empty duplicates shadow real keys.

        With BLOFIN_DEMO=false the resolved credentials are empty, so startup
        is refused; the duplicate warning fires too; no value is disclosed.
        """
        dotenv = tmp_path / ".env"
        dotenv.write_text(SHADOWED_BLOFIN_ENV, encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            with pytest.raises(StartupConfigError) as exc_info:
                run_startup_validation(str(dotenv))
        message = str(exc_info.value)
        assert "BLOFIN_API_KEY" in message
        assert "BLOFIN_DEMO=false" in message
        assert any("more than once" in rec.message for rec in caplog.records)
        for secret in (FAKE_KEY, FAKE_SECRET, FAKE_PASS):
            assert secret not in message
            assert secret not in caplog.text

    def test_shadowed_demo_credentials_only_warn(self, tmp_path, caplog):
        """The same file on demo starts, but the duplicate warning still fires."""
        dotenv = tmp_path / ".env"
        dotenv.write_text(
            SHADOWED_BLOFIN_ENV.replace("BLOFIN_DEMO=false", "BLOFIN_DEMO=true"),
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING):
            report = run_startup_validation(str(dotenv))
        assert report.errors == []
        assert report.duplicate_keys == [
            "BLOFIN_API_KEY",
            "BLOFIN_API_SECRET",
            "BLOFIN_PASSPHRASE",
        ]
        warning = [r for r in caplog.records if "more than once" in r.message]
        assert len(warning) == 1
        assert "BLOFIN_PASSPHRASE" in warning[0].getMessage()
        assert FAKE_PASS not in caplog.text

    def test_clean_file_returns_quiet_report(self, tmp_path, caplog):
        """A well-formed file produces no warning and no error."""
        dotenv = tmp_path / ".env"
        dotenv.write_text("EXCHANGE=blofin\nBLOFIN_DEMO=true\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            report = run_startup_validation(str(dotenv), host="127.0.0.1", token=None)
        assert report.duplicate_keys == []
        assert report.errors == []
        assert not [r for r in caplog.records if "more than once" in r.message]

    def test_non_loopback_without_token_is_refused(self, tmp_path):
        """API binding is part of the same startup gate."""
        dotenv = tmp_path / ".env"
        dotenv.write_text("EXCHANGE=blofin\nBLOFIN_DEMO=true\n", encoding="utf-8")
        with pytest.raises(StartupConfigError, match="API_HOST"):
            run_startup_validation(str(dotenv), host="0.0.0.0", token=None)
        report = run_startup_validation(str(dotenv), host="0.0.0.0", token="tok")
        assert report.errors == []


class TestLoopback:
    """is_loopback_host and check_api_binding."""

    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "localhost", "::1", "127.1.2.3", " LOCALHOST "]
    )
    def test_loopback_hosts(self, host):
        """Names and addresses that stay on this machine."""
        assert is_loopback_host(host) is True

    @pytest.mark.parametrize(
        "host", ["0.0.0.0", "", None, "::", "10.0.0.5", "example.com"]
    )
    def test_non_loopback_hosts(self, host):
        """Anything reachable off-box, unparsable, or empty is not loopback."""
        assert is_loopback_host(host) is False

    def test_check_api_binding_never_echoes_the_token(self):
        """The refusal message names the key, never the token value."""
        assert check_api_binding("0.0.0.0", "s3cret") is None
        message = check_api_binding("0.0.0.0", None)
        assert message is not None and "s3cret" not in message
