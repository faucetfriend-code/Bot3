"""One default for DATABASE_PATH, pinned so it cannot drift again.

``database.py`` used to default to ``data/trading_bot.db`` while
``config.py`` defaulted to ``trading_bot.db``. Because ``database.py``
resolves the path at import time and only ``config.py`` loads the
``.env``, the file a process actually opened depended on which module
resolved it first:

- ``import trading_bot_v2.database`` alone (any standalone CLI or
  script) -> the module default, ``<cwd>/data/trading_bot.db``
- anything importing ``config`` first (api_server, trading_bot) ->
  ``DATABASE_PATH`` from ``.env``

Both files exist in this repo and both hold real rows, which is how
``config/regime_param_overlays.json`` came to mirror one database while
the server used the other, and how a hazardous overlay row sat in a file
nothing was reading.

The fix is a single constant, ``database.DEFAULT_DATABASE_PATH``, which
``config.py`` imports. These tests pin that so a future edit to either
module cannot reintroduce two defaults.
"""

import ast
import pathlib

import trading_bot_v2.config as config_mod
import trading_bot_v2.database as db_mod


def _module_source(module):
    return pathlib.Path(module.__file__).read_text(encoding="utf-8")


def test_config_default_is_the_database_module_default():
    """config.Config must fall back to database.DEFAULT_DATABASE_PATH."""
    assert config_mod.DEFAULT_DATABASE_PATH is db_mod.DEFAULT_DATABASE_PATH


def test_default_resolves_to_the_same_file_from_both_modules(monkeypatch):
    """With DATABASE_PATH unset, both modules mean the same file."""
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    cfg = config_mod.Config()

    from_config = pathlib.Path(cfg.database_path).resolve()
    from_database = pathlib.Path(db_mod.DEFAULT_DATABASE_PATH).resolve()
    assert from_config == from_database


def test_explicit_env_value_still_wins(monkeypatch):
    """The shared default is a fallback, not an override."""
    monkeypatch.setenv("DATABASE_PATH", "somewhere/else.db")
    assert config_mod.Config().database_path == "somewhere/else.db"


def test_no_second_literal_default_anywhere():
    """Neither module may hardcode a fallback path of its own.

    The literal must appear exactly once, in the DEFAULT_DATABASE_PATH
    assignment. Any other ``os.getenv("DATABASE_PATH", "...")`` is a new
    second source of truth.
    """
    for module in (db_mod, config_mod):
        tree = ast.parse(_module_source(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv") or (
                isinstance(func, ast.Name) and func.id == "getenv"
            )
            if not is_getenv or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and first.value == "DATABASE_PATH"):
                continue
            assert len(node.args) > 1, (
                f"{module.__name__}: os.getenv('DATABASE_PATH') with no default"
            )
            fallback = node.args[1]
            assert isinstance(fallback, ast.Name) and (
                fallback.id == "DEFAULT_DATABASE_PATH"
            ), (
                f"{module.__name__} hardcodes its own DATABASE_PATH "
                f"default; use database.DEFAULT_DATABASE_PATH so the two "
                f"modules cannot disagree about which file the setting "
                f"names."
            )
