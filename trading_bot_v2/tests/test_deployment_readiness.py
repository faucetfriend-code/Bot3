"""Offline deployment checks using disposable SQLite databases and mocked startup."""

import asyncio
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy/vps/sqlite_backup.py"
SPEC = importlib.util.spec_from_file_location("deployment_sqlite_backup", SCRIPT)
backup_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_tool)


def create_database(path, value):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE positions (quantity INTEGER)")
        connection.execute("INSERT INTO positions VALUES (?)", (value,))
        connection.commit()


def quantity(path):
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        return connection.execute("SELECT quantity FROM positions").fetchone()[0]


def test_backup_captures_committed_wal_and_restores(tmp_path):
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    restored = tmp_path / "restored.db"
    create_database(source, 1)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE positions SET quantity=7")
        connection.commit()
        assert Path(str(source) + "-wal").exists()
        backup_tool.backup(source, snapshot)
        assert quantity(snapshot) == 7
    backup_tool.restore(snapshot, restored)
    assert quantity(restored) == 7


def test_backup_refuses_overwrite(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    create_database(source, 1)
    create_database(target, 2)
    with pytest.raises(FileExistsError):
        backup_tool.backup(source, target)
    assert quantity(target) == 2


def test_restore_preserves_previous_database(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    create_database(source, 1)
    create_database(target, 2)
    with pytest.raises(FileExistsError):
        backup_tool.restore(source, target)
    backup_tool.restore(source, target, replace=True)
    preserved = list(tmp_path.glob("target.db.before-*"))
    assert len(preserved) == 1
    assert quantity(preserved[0]) == 2
    assert quantity(target) == 1


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_restore_refuses_active_database_sidecars(tmp_path, suffix):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    create_database(source, 1)
    create_database(target, 2)
    Path(str(target) + suffix).touch()
    with pytest.raises(ValueError, match="stop all writers"):
        backup_tool.restore(source, target, replace=True)
    assert target.exists()


def test_corrupt_backup_cannot_replace_good_database(tmp_path):
    source, target = tmp_path / "corrupt.db", tmp_path / "target.db"
    source.write_bytes(b"not a sqlite database")
    create_database(target, 2)
    with pytest.raises(sqlite3.DatabaseError):
        backup_tool.restore(source, target, replace=True)
    assert quantity(target) == 2


def test_server_lifespan_never_starts_strategy_loop(monkeypatch):
    from trading_bot_v2 import api_server

    integration = SimpleNamespace(
        initialize=Mock(),
        start=AsyncMock(),
        stop=AsyncMock(),
        shutdown=Mock(),
        _is_running=False,
    )
    monkeypatch.setattr(api_server, "bot_integration", integration)
    monkeypatch.setattr(api_server, "enforce_startup_policy", Mock())

    async def cycle():
        async with api_server.lifespan(api_server.app):
            assert integration._is_running is False

    asyncio.run(cycle())
    integration.initialize.assert_called_once()
    integration.start.assert_not_called()
    integration.stop.assert_not_called()
    integration.shutdown.assert_called_once()


def load_supervisor_module():
    path = SCRIPT.parents[2] / "trading_bot_v2/supervisor_control.py"
    spec = importlib.util.spec_from_file_location("isolated_supervisor_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supervisor_default_path_is_backward_compatible(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_STATE_PATH", raising=False)
    module = load_supervisor_module()
    assert module._PAUSE_FILE == SCRIPT.parents[2] / "supervisor_pause.json"


def test_supervisor_pause_survives_module_reload(tmp_path, monkeypatch):
    path = tmp_path / "persistent" / "pause.json"
    monkeypatch.setenv("SUPERVISOR_STATE_PATH", str(path))
    first = load_supervisor_module().SupervisorControl()
    assert first.is_paused() is False
    first.pause("deployment recovery check")
    second = load_supervisor_module().SupervisorControl()
    assert second.is_paused() is True
    second.resume()
    assert load_supervisor_module().SupervisorControl().is_paused() is False


@pytest.mark.parametrize(
    "contents", ["invalid json", "[]", "null", '{"paused": "false"}']
)
def test_supervisor_invalid_state_does_not_resume(tmp_path, monkeypatch, contents):
    path = tmp_path / "pause.json"
    path.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("SUPERVISOR_STATE_PATH", str(path))
    supervisor = load_supervisor_module().SupervisorControl()
    assert supervisor.is_paused() is True


def test_supervisor_unreadable_state_does_not_resume(tmp_path, monkeypatch):
    path = tmp_path / "pause.json"
    path.mkdir()
    monkeypatch.setenv("SUPERVISOR_STATE_PATH", str(path))
    assert load_supervisor_module().SupervisorControl().is_paused() is True


def image_validator():
    path = SCRIPT.with_name("image_smoke.py")
    spec = importlib.util.spec_from_file_location("isolated_image_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_layout


def image_layout(root):
    (root / "interface.html").write_text("<html>Dashboard</html>", encoding="utf-8")
    package = root / "trading_bot_v2"
    package.mkdir()
    # Valid code must be compiled, never executed during image validation.
    (package / "api_server.py").write_text(
        'raise RuntimeError("application must not start")\n', encoding="utf-8"
    )
    return package


def test_image_validation_never_executes_application(tmp_path):
    image_layout(tmp_path)
    assert image_validator()(tmp_path) == 1


@pytest.mark.parametrize("missing", ["dashboard", "empty_dashboard", "entrypoint"])
def test_image_validation_rejects_missing_artifacts(tmp_path, missing):
    package = image_layout(tmp_path)
    if missing == "entrypoint":
        (package / "api_server.py").unlink()
    elif missing == "empty_dashboard":
        (tmp_path / "interface.html").write_text("", encoding="utf-8")
    else:
        (tmp_path / "interface.html").unlink()
    with pytest.raises(RuntimeError, match="Missing"):
        image_validator()(tmp_path)


@pytest.mark.parametrize("name", [".env", ".env.production"])
def test_image_validation_rejects_packaged_environment_files(tmp_path, name):
    package = image_layout(tmp_path)
    (package / name).write_text("PLACEHOLDER=value", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Environment file"):
        image_validator()(tmp_path)


def test_image_validation_rejects_invalid_python(tmp_path):
    package = image_layout(tmp_path)
    (package / "broken.py").write_text("def broken(:", encoding="utf-8")
    with pytest.raises(SyntaxError):
        image_validator()(tmp_path)
