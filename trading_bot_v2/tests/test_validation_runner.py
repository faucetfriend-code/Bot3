"""
Tests for the standalone validation runner (P5).

Covers: enabled-strategy discovery, chunk-window computation, pooled
gate aggregation over synthetic chunk results, validation_runs
persistence round-trip, skip-on-failure behavior, and live-bot import
independence.
"""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import trading_bot_v2.validation.runner as runner
from trading_bot_v2.validation.runner import (
    STRATEGY_ENV_FLAGS,
    compute_chunk_windows,
    discover_enabled_strategies,
    persist_result,
    run_once,
    validate_strategy,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Strongly positive per-chunk series: 15 wins of +1%, 2 losses of -0.2%
GOOD_CHUNK = [0.01] * 15 + [-0.002] * 2
# Clearly losing per-chunk series
BAD_CHUNK = [-0.01] * 12 + [0.002] * 5


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """DatabaseManager wired to a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    db_file = str(tmp_path / "validation_runner_test.db")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = db_file
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod.DatabaseManager()

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool


class TestStrategyDiscovery:
    def test_env_flags_respected(self, monkeypatch):
        monkeypatch.setenv("ENABLE_MEAN_REVERSION", "false")
        monkeypatch.setenv("ENABLE_FUNDING_ARB", "true")
        enabled = discover_enabled_strategies()
        assert "mean_reversion" not in enabled
        assert "funding_arb" in enabled

    def test_defaults_when_unset(self, monkeypatch):
        for env_var, _default in STRATEGY_ENV_FLAGS.values():
            monkeypatch.delenv(env_var, raising=False)
        enabled = discover_enabled_strategies()
        # Defaults mirror StrategyManager: these are on by default...
        for name in ("mean_reversion", "momentum_scalping", "grid_trading"):
            assert name in enabled
        # ...and these are off by default.
        for name in ("funding_arb", "session_range_breakout"):
            assert name not in enabled

    def test_map_covers_known_strategies(self):
        from trading_bot_v2.regime_param_overlay import (
            STRATEGY_KEY_TO_DISPLAY,
        )

        assert set(STRATEGY_ENV_FLAGS) == set(STRATEGY_KEY_TO_DISPLAY)


class TestChunkWindows:
    def test_three_by_two_months_walking_back(self):
        windows = compute_chunk_windows(date(2025, 1, 1), 2, 3)
        assert windows == [
            ("2024-07-01", "2024-09-01"),
            ("2024-09-01", "2024-11-01"),
            ("2024-11-01", "2025-01-01"),
        ]

    def test_partial_coverage_clamps_and_stops(self):
        windows = compute_chunk_windows(
            date(2025, 1, 1), 2, 3, data_start=date(2024, 10, 15)
        )
        assert windows == [
            ("2024-10-15", "2024-11-01"),
            ("2024-11-01", "2025-01-01"),
        ]

    def test_month_end_day_clamped(self):
        windows = compute_chunk_windows(date(2024, 3, 31), 1, 1)
        assert windows == [("2024-02-29", "2024-03-31")]

    def test_no_coverage_yields_empty(self):
        assert (
            compute_chunk_windows(
                date(2024, 1, 1), 2, 3, data_start=date(2024, 6, 1)
            )
            == []
        )
        assert compute_chunk_windows(date(2025, 1, 1), 0, 3) == []
        assert compute_chunk_windows(date(2025, 1, 1), 2, 0) == []


class TestPooledGateAggregation:
    def _patch_backtests(self, monkeypatch, chunk_returns):
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: (date(2024, 1, 1), date(2025, 1, 1)),
        )
        monkeypatch.setattr(
            runner,
            "_run_chunk_backtest",
            lambda strategy, symbol, start, end, capital: list(
                chunk_returns
            ),
        )

    def test_pass_case(self, monkeypatch, tmp_db):
        self._patch_backtests(monkeypatch, GOOD_CHUNK)
        result = validate_strategy(
            "mean_reversion", ["SUI-USDC", "BTC-USDC"], 2, 3
        )
        assert result["overall"] == "PASS"
        assert result["window_spec"] == "3x2mo"
        # 2 symbols x 3 windows = 6 chunk records
        assert len(result["chunks"]) == 6
        # Pooled per symbol: 3 chunks x 17 trades = 51 >= 30 min trades
        verdict = result["verdict"]
        by_name = {c.name: c for c in verdict.checks}
        assert by_name["min_closed_trades"].value == "51"
        assert by_name["min_closed_trades"].passed
        assert by_name["profit_factor"].passed
        assert by_name["cross_symbol_consistency"].passed

    def test_fail_case(self, monkeypatch, tmp_db):
        self._patch_backtests(monkeypatch, BAD_CHUNK)
        result = validate_strategy(
            "mean_reversion", ["SUI-USDC", "BTC-USDC"], 2, 3
        )
        assert result["overall"] == "FAIL"
        verdict = result["verdict"]
        by_name = {c.name: c for c in verdict.checks}
        assert not by_name["profit_factor"].passed
        assert not by_name["cross_symbol_consistency"].passed

    def test_no_data_yields_unknown(self, monkeypatch, tmp_db):
        monkeypatch.setattr(
            runner, "_data_coverage", lambda symbol, data_dir: None
        )
        result = validate_strategy(
            "mean_reversion", ["SUI-USDC", "BTC-USDC"], 2, 3
        )
        assert result["overall"] == "UNKNOWN"
        assert "no candle data" in result["reason"]


class TestPersistence:
    def _stub_result(self, strategy, overall="PASS"):
        return {
            "strategy": strategy,
            "symbols": ["SUI-USDC", "BTC-USDC"],
            "window_spec": "3x2mo",
            "windows": [("2024-11-01", "2025-01-01")],
            "chunks": [
                {
                    "symbol": "SUI-USDC",
                    "start": "2024-11-01",
                    "end": "2025-01-01",
                    "n_trades": 17,
                    "sum_return": 0.1464,
                }
            ],
            "verdict": None,
            "overall": overall,
            "data_start": "2024-01-01",
            "data_end": "2025-01-01",
        }

    def test_round_trip(self, tmp_db):
        row_id = persist_result(tmp_db, self._stub_result("mean_reversion"))
        assert row_id is not None

        rows = tmp_db.get_validation_runs(strategy="mean_reversion")
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy"] == "mean_reversion"
        assert row["symbols"] == "SUI-USDC,BTC-USDC"
        assert row["window_spec"] == "3x2mo"
        assert row["overall"] == "PASS"
        assert row["data_start"] == "2024-01-01"
        assert row["data_end"] == "2025-01-01"
        assert row["chunks"][0]["n_trades"] == 17
        assert row["run_at"] is not None

    def test_latest_per_strategy(self, tmp_db):
        persist_result(tmp_db, self._stub_result("mean_reversion", "FAIL"))
        persist_result(tmp_db, self._stub_result("mean_reversion", "PASS"))
        persist_result(
            tmp_db, self._stub_result("momentum_scalping", "FAIL")
        )

        latest = tmp_db.get_latest_validation_runs()
        assert len(latest) == 2
        by_strategy = {r["strategy"]: r for r in latest}
        # Newest row per strategy wins
        assert by_strategy["mean_reversion"]["overall"] == "PASS"
        assert by_strategy["momentum_scalping"]["overall"] == "FAIL"

    def test_limit_and_order(self, tmp_db):
        for overall in ("FAIL", "FAIL", "PASS"):
            persist_result(
                tmp_db, self._stub_result("mean_reversion", overall)
            )
        rows = tmp_db.get_validation_runs(
            strategy="mean_reversion", limit=2
        )
        assert len(rows) == 2
        assert rows[0]["overall"] == "PASS"  # newest first


class TestSkipOnFailure:
    def test_failing_strategy_skipped_run_continues(
        self, tmp_db, monkeypatch, caplog
    ):
        def fake_validate(strategy, *args, **kwargs):
            if strategy == "mean_reversion":
                raise RuntimeError("engine exploded")
            return {
                "strategy": strategy,
                "symbols": ["SUI-USDC", "BTC-USDC"],
                "window_spec": "3x2mo",
                "chunks": [],
                "verdict": None,
                "overall": "PASS",
                "data_start": "2024-01-01",
                "data_end": "2025-01-01",
            }

        monkeypatch.setattr(runner, "validate_strategy", fake_validate)

        results = run_once(
            ["mean_reversion", "momentum_scalping"],
            ["SUI-USDC", "BTC-USDC"],
            2,
            3,
        )
        assert len(results) == 2
        by_strategy = {r["strategy"]: r for r in results}
        assert by_strategy["mean_reversion"]["overall"] == "UNKNOWN"
        assert "engine exploded" in by_strategy["mean_reversion"]["reason"]
        assert by_strategy["momentum_scalping"]["overall"] == "PASS"

        # Both verdicts were persisted (UNKNOWN row carries the error)
        rows = tmp_db.get_validation_runs(limit=10)
        assert len(rows) == 2
        unknown = [r for r in rows if r["overall"] == "UNKNOWN"][0]
        assert unknown["checks"][0]["name"] == "runner_error"
        assert "engine exploded" in unknown["checks"][0]["value"]


class TestImportIndependence:
    def test_runner_import_pulls_no_live_bot_modules(self):
        """Importing validation.runner must not import the live bot."""
        code = (
            "import sys\n"
            "import trading_bot_v2.validation.runner\n"
            "banned = [m for m in sys.modules if m in ("
            "'trading_bot_v2.trading_bot', "
            "'trading_bot_v2.api_server', "
            "'trading_bot_v2.pacifica_ws_client')]\n"
            "assert not banned, f'live-bot modules imported: {banned}'\n"
            "print('CLEAN')\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert "CLEAN" in proc.stdout
