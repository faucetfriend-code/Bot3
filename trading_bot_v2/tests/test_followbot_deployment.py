"""Offline checks for the paper-only follower deployment gate."""

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture
def launcher():
    path = Path(__file__).parents[2] / "deploy" / "followbot" / "launch.py"
    spec = importlib.util.spec_from_file_location("followbot_launch_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def settings():
    return {
        "EXCHANGE": "pacifica",
        "PAPER_MODE": "true",
        "PACIFICA_TESTNET": "true",
        "FOLLOWBOT_SESSION_CONFIRMED": "true",
        "LOCAL_LLM_BASE_URL": "http://model.invalid/v1",
        "LOCAL_LLM_MODEL": "text-test",
        "LOCAL_VISION_MODEL": "vision-test",
    }


@pytest.fixture
def source():
    # Keep fixtures outside the checkout: its real .env is correctly forbidden
    # as an ancestor of deployment source by the production gate.
    with TemporaryDirectory(prefix="followbot-deployment-") as temporary:
        folder = Path(temporary) / "app"
        folder.mkdir()
        (folder / "bot.py").write_text("raise RuntimeError('must never run')\n")
        yield folder


def test_safe_settings_validate_without_executing_source(launcher, settings, source):
    launcher.validate_settings(settings, source)


@pytest.mark.parametrize(
    ("name", "unsafe"),
    [
        ("EXCHANGE", "blofin"),
        ("PAPER_MODE", "false"),
        ("PACIFICA_TESTNET", "false"),
        ("FOLLOWBOT_SESSION_CONFIRMED", "false"),
        ("LOCAL_LLM_BASE_URL", ""),
        ("LOCAL_LLM_MODEL", ""),
        ("LOCAL_VISION_MODEL", ""),
        ("DASHBOARD_HOST", "0.0.0.0"),
    ],
)
def test_unsafe_settings_rejected(launcher, settings, source, name, unsafe):
    settings[name] = unsafe
    with pytest.raises(launcher.PreflightError):
        launcher.validate_settings(settings, source)


@pytest.mark.parametrize("ancestor", [False, True])
def test_dotenv_override_rejected(launcher, settings, source, ancestor):
    folder = source.parent if ancestor else source
    (folder / ".env").write_text("PAPER_MODE=false\n")
    with pytest.raises(launcher.PreflightError, match="ancestor"):
        launcher.validate_settings(settings, source)


def test_services_checked_only_through_mock(launcher, settings, monkeypatch):
    calls = []

    def read(url):
        calls.append(url)
        if url.endswith("/json"):
            return [{"url": "https://discord.com/channels/server/channel"}]
        return {"data": [{"id": "text-test"}, {"id": "vision-test"}]}

    monkeypatch.setattr(launcher, "read_json", read)
    launcher.verify_services(settings)
    assert calls == ["http://127.0.0.1:9222/json", "http://model.invalid/v1/models"]


@pytest.mark.parametrize("targets", [[], {}, [{"url": "https://discord.com/login"}]])
def test_missing_discord_session_stops_before_model_query(
    launcher, settings, monkeypatch, targets
):
    calls = []

    def read(url):
        calls.append(url)
        return targets

    monkeypatch.setattr(launcher, "read_json", read)
    with pytest.raises(launcher.PreflightError):
        launcher.verify_services(settings)
    assert len(calls) == 1


def test_blocked_main_never_executes_bot(launcher, monkeypatch, capsys):
    def refuse(*args):
        raise launcher.PreflightError("paper mode required")

    def forbidden(*args):
        pytest.fail("Blocked preflight must not probe services or execute bot")

    monkeypatch.setattr(launcher, "validate_settings", refuse)
    monkeypatch.setattr(launcher, "verify_services", forbidden)
    monkeypatch.setattr(launcher.os, "execv", forbidden)
    assert launcher.main() == 78
    assert "paper mode required" in capsys.readouterr().err
