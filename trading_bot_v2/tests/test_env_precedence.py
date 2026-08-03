"""Step 1 of the staged .env precedence fix must measure, not change.

config.py has always let the .env FILE beat the process environment
(load_dotenv(override=True)), which is backwards from the usual
flag > env > file > default order and produces behaviour nobody can
predict from outside: a shell export works or is silently clobbered
depending on whether the key happens to appear in .env.

Flipping that is a live-bot change, so step 1 only instruments it. These
tests pin the two properties that make the instrumentation worth
trusting: it identifies exactly the keys whose resolution the setting
decides, and it never exposes a value - .env holds
AGENT_WALLET_PRIVATE_KEY, and this report is meant to be logged.
"""

import json

import pytest

from trading_bot_v2 import env_precedence as ep

SECRET = "s3cret-value-that-must-never-be-reported"


@pytest.fixture
def dotenv_file(tmp_path):
    """A throwaway .env, so the real one is never read or written."""
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "SHADOWED=from_file",
                "AGREES=same",
                "FILE_ONLY=only_here",
                f"AGENT_WALLET_PRIVATE_KEY={SECRET}",
            ]
        ),
        encoding="utf-8",
    )
    return str(path)


class TestShadowedKeys:
    def test_only_keys_that_differ_are_reported(self, dotenv_file):
        environ = {
            "SHADOWED": "from_shell",
            "AGREES": "same",
            "SHELL_ONLY": "only_here",
        }

        assert ep.shadowed_keys(dotenv_file, environ) == ["SHADOWED"]

    def test_a_key_in_one_source_only_is_not_shadowed(self, dotenv_file):
        """It resolves the same way under either precedence."""
        assert "FILE_ONLY" not in ep.shadowed_keys(dotenv_file, {})
        assert "SHELL_ONLY" not in ep.shadowed_keys(
            dotenv_file, {"SHELL_ONLY": "x"}
        )

    def test_matching_values_are_not_shadowed(self, dotenv_file):
        assert ep.shadowed_keys(dotenv_file, {"AGREES": "same"}) == []

    def test_empty_when_nothing_is_exported(self, dotenv_file):
        assert ep.shadowed_keys(dotenv_file, {}) == []


class TestNoValueLeaks:
    def test_report_never_contains_a_value(self, dotenv_file, monkeypatch):
        """The whole report gets logged; it must be secret-free."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "a-different-secret")
        monkeypatch.setenv("SHADOWED", "from_shell")

        report = ep.load_env(dotenv_file, override=False)

        serialized = json.dumps(report)
        assert SECRET not in serialized
        assert "a-different-secret" not in serialized
        assert "from_shell" not in serialized
        assert "from_file" not in serialized

    def test_the_secret_key_is_still_named_when_shadowed(
        self, dotenv_file, monkeypatch
    ):
        """Naming the key is the point; only the value is withheld."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "a-different-secret")

        report = ep.load_env(dotenv_file, override=False)

        assert "AGENT_WALLET_PRIVATE_KEY" in report["shadowed"]


class TestPrecedence:
    def test_override_true_lets_the_file_win(self, dotenv_file, monkeypatch):
        """The shipped behaviour, unchanged by step 1."""
        monkeypatch.setenv("SHADOWED", "from_shell")

        report = ep.load_env(dotenv_file, override=True)

        import os

        assert os.environ["SHADOWED"] == "from_file"
        assert report["winner"] == "dotenv_file"

    def test_override_false_lets_the_shell_win(self, dotenv_file, monkeypatch):
        """What step 2 would switch to."""
        monkeypatch.setenv("SHADOWED", "from_shell")

        report = ep.load_env(dotenv_file, override=False)

        import os

        assert os.environ["SHADOWED"] == "from_shell"
        assert report["winner"] == "process_environment"

    def test_default_is_still_the_historical_behaviour(self):
        """Step 1 changes nothing until someone flips this."""
        assert ep.DEFAULT_OVERRIDE is True

    def test_dotenv_override_env_var_selects_the_precedence(
        self, dotenv_file, monkeypatch
    ):
        monkeypatch.setenv(ep.OVERRIDE_ENV_VAR, "false")

        report = ep.load_env(dotenv_file)

        assert report["override"] is False
        assert report["override_source"] == "environment"

    def test_unset_override_var_reports_the_default(
        self, dotenv_file, monkeypatch
    ):
        monkeypatch.delenv(ep.OVERRIDE_ENV_VAR, raising=False)

        report = ep.load_env(dotenv_file)

        assert report["override"] is ep.DEFAULT_OVERRIDE
        assert report["override_source"] == "default"

    @pytest.mark.parametrize(
        "raw,expected",
        [("true", True), ("1", True), ("yes", True), ("false", False),
         ("0", False), ("no", False), ("", True), (None, True)],
    )
    def test_flag_parsing_matches_config_py(self, raw, expected):
        assert ep._truthy(raw, True) is expected


class TestConfigWiring:
    def test_config_exposes_the_report(self):
        """The check runs at every import, not only from the CLI."""
        from trading_bot_v2.config import ENV_LOAD_REPORT

        assert set(ENV_LOAD_REPORT) >= {
            "override",
            "override_source",
            "winner",
            "shadowed",
            "dotenv_key_count",
        }

    def test_main_exit_code_gates_step_two(self, dotenv_file, monkeypatch):
        """Non-zero while any key is shadowed, so a script can gate on it."""
        monkeypatch.setattr(ep, "load_env", lambda: {
            "override": True, "override_source": "default",
            "winner": "dotenv_file", "shadowed": ["DATABASE_PATH"],
            "dotenv_key_count": 1,
        })
        assert ep.main() == 1

        monkeypatch.setattr(ep, "load_env", lambda: {
            "override": True, "override_source": "default",
            "winner": "dotenv_file", "shadowed": [], "dotenv_key_count": 1,
        })
        assert ep.main() == 0
