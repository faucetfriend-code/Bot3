"""Shared pytest fixtures for the trading_bot_v2 suite.

The suite must never write to a real database. Most tests that need one
point DATABASE_PATH at a tmp file themselves, but any test that builds a
DatabaseManager without doing so falls through to the module-level
default - which is the file the live server uses. That used to be
``data/trading_bot.db`` (the unread second database) purely because
database.py and config.py disagreed about the default; now that the two
agree on one file, the fallthrough would land on the live database and
contend with the running server for the SQLite write lock.

So the whole session is redirected to a throwaway file up front.
Per-test fixtures that patch DATABASE_PATH themselves keep working - they
simply override this one.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolate_test_database(tmp_path_factory):
    """Point the session's default SQLite path at a throwaway file.

    Args:
        tmp_path_factory: pytest's session-scoped temp directory factory.

    Yields:
        The temporary database path, or None when the backend is not
        SQLite (nothing to redirect).
    """
    import trading_bot_v2.database as db_mod

    if getattr(db_mod, "_active_backend", "sqlite") != "sqlite":
        yield None
        return

    path = str(tmp_path_factory.mktemp("database") / "test_trading_bot.db")
    previous_env = os.environ.get("DATABASE_PATH")
    previous_path = db_mod.DATABASE_PATH

    os.environ["DATABASE_PATH"] = path
    db_mod.DATABASE_PATH = path
    db_mod.init_database()

    yield path

    db_mod.DATABASE_PATH = previous_path
    if previous_env is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = previous_env
