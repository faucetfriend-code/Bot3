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
    build_window_spec,
    collect_regime_coverage,
    compute_chunk_windows,
    compute_spread_windows,
    discover_enabled_strategies,
    estimate_runtime_seconds,
    format_duration,
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
            runner, "_coverage_1m_start", lambda symbol, data_dir: None
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_end", lambda symbol, data_dir: None
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
        assert result["window_spec"] == "3x2mo@8y"
        # 2 symbols x 3 windows = 6 chunk records
        assert len(result["chunks"]) == 6
        # Pooled: 2 symbols x 3 chunks x 17 trades = 102 closed trades,
        # comfortably over the derived sample-adequacy requirement.
        verdict = result["verdict"]
        by_name = {c.name: c for c in verdict.checks}
        assert verdict.n_pooled == 102
        assert by_name["sample_adequacy"].passed
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


class TestAnchorClamping:
    """Chunk-window anchor is clamped to 1m data coverage.

    The 5m store auto-downloads up to now while the 1m store is topped
    up manually, so without clamping the newest window overruns 1m
    coverage and the engine's 1m guard fails it (runner_error/UNKNOWN).
    """

    def _patch_backtests(self, monkeypatch, m1_ends):
        """Stub 5m coverage, per-symbol 1m coverage ends, and backtests."""
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: (date(2024, 1, 1), date(2025, 1, 1)),
        )
        monkeypatch.setattr(
            runner,
            "_coverage_1m_end",
            lambda symbol, data_dir: m1_ends.get(symbol),
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_start", lambda symbol, data_dir: None
        )
        monkeypatch.setattr(
            runner,
            "_run_chunk_backtest",
            lambda strategy, symbol, start, end, capital: list(GOOD_CHUNK),
        )

    def _capture_info_logs(self):
        """Attach a loguru sink; returns (messages, remove_fn)."""
        from loguru import logger as loguru_logger

        messages = []
        sink_id = loguru_logger.add(
            lambda m: messages.append(str(m)), level="INFO"
        )
        return messages, lambda: loguru_logger.remove(sink_id)

    def test_anchor_clamped_to_min_1m_coverage_end(
        self, monkeypatch, tmp_db
    ):
        # BTC's 1m store lags the most: the anchor must clamp to it.
        self._patch_backtests(
            monkeypatch,
            {
                "SUI-USDC": date(2024, 12, 20),
                "BTC-USDC": date(2024, 12, 15),
            },
        )
        messages, remove = self._capture_info_logs()
        try:
            result = validate_strategy(
                "mean_reversion", ["SUI-USDC", "BTC-USDC"], 2, 3
            )
        finally:
            remove()
        assert result["data_end"] == "2024-12-15"
        # Newest window ends at the clamped anchor, not the 5m end.
        assert result["windows"][-1][1] == "2024-12-15"
        clamp_logs = [m for m in messages if "window anchor clamped" in m]
        assert len(clamp_logs) == 1
        assert "BTC-USDC" in clamp_logs[0]
        assert "2025-01-01" in clamp_logs[0]  # original anchor
        assert "2024-12-15" in clamp_logs[0]  # clamped anchor

    def test_anchor_not_clamped_when_1m_covers_boundary(
        self, monkeypatch, tmp_db
    ):
        self._patch_backtests(
            monkeypatch,
            {
                "SUI-USDC": date(2025, 3, 1),
                "BTC-USDC": date(2025, 1, 1),
            },
        )
        messages, remove = self._capture_info_logs()
        try:
            result = validate_strategy(
                "mean_reversion", ["SUI-USDC", "BTC-USDC"], 2, 3
            )
        finally:
            remove()
        assert result["data_end"] == "2025-01-01"
        assert result["windows"][-1][1] == "2025-01-01"
        assert not [m for m in messages if "window anchor clamped" in m]

    def test_no_1m_data_keeps_current_behavior(self, monkeypatch, tmp_db):
        # No 1m data for any symbol: window computation is unchanged
        # (the engine's 1m guard stays the loud failure path).
        self._patch_backtests(monkeypatch, {})
        result = validate_strategy(
            "mean_reversion", ["SUI-USDC", "BTC-USDC"], 2, 3
        )
        assert result["data_end"] == "2025-01-01"
        assert result["windows"][-1][1] == "2025-01-01"
        assert result["overall"] in ("PASS", "FAIL")  # no crash


class TestResolveChunkWindows:
    """The window resolver is shared with the chunked optimizer sweep.

    Both must evaluate the SAME window series, otherwise a sweep result
    and a gate verdict silently describe different periods.
    """

    def _patch(self, monkeypatch):
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: (date(2024, 1, 1), date(2025, 1, 1)),
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_end", lambda symbol, data_dir: None
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_start", lambda symbol, data_dir: None
        )

    def test_windows_abut_and_end_at_the_anchor(self, monkeypatch):
        self._patch(monkeypatch)
        out = runner.resolve_chunk_windows(
            ["BTC-USDC", "SUI-USDC"], 2, 3, mode="recent"
        )
        assert out["symbols"] == ["BTC-USDC", "SUI-USDC"]
        assert out["mode"] == "recent"
        assert len(out["windows"]) == 3
        assert out["windows"][-1][1] == "2025-01-01"
        for earlier, later in zip(out["windows"], out["windows"][1:]):
            assert earlier[1] == later[0]

    def test_anchor_end_moves_the_series_back(self, monkeypatch):
        """A requested anchor re-aims the series at a covered period.

        Needed when the automatic anchor lands in a hole of the
        timeframe the strategy actually reads - 5m coverage cannot see a
        gap in the 4h store.
        """
        self._patch(monkeypatch)
        out = runner.resolve_chunk_windows(
            ["BTC-USDC"], 1, 3, anchor_end="2024-04-01"
        )
        assert out["windows"] == [
            ("2024-01-01", "2024-02-01"),
            ("2024-02-01", "2024-03-01"),
            ("2024-03-01", "2024-04-01"),
        ]

    def test_anchor_end_never_moves_forward(self, monkeypatch):
        """An anchor past coverage is refused, not honoured."""
        self._patch(monkeypatch)
        out = runner.resolve_chunk_windows(
            ["BTC-USDC"], 2, 1, anchor_end="2026-01-01"
        )
        assert out["windows"][-1][1] == "2025-01-01"

    def test_no_data_reports_a_reason(self, monkeypatch):
        monkeypatch.setattr(
            runner, "_data_coverage", lambda symbol, data_dir: None
        )
        out = runner.resolve_chunk_windows(["NOPE-USDC"], 2, 3)
        assert out["windows"] == []
        assert "no candle data" in out["reason"]


class TestSpreadWindows:
    """Spread mode: N windows distributed across a multi-YEAR span."""

    def test_spread_across_eight_years(self):
        windows = compute_spread_windows(
            date(2026, 7, 1), 2, 6, data_start=date(2018, 7, 1)
        )
        assert len(windows) == 6
        # Oldest starts at the span start, newest ends at the anchor.
        assert windows[0][0] == "2018-07-01"
        assert windows[-1][1] == "2026-07-01"
        # Every window is the requested length and none overlap.
        for start, end in windows:
            s = date.fromisoformat(start)
            e = date.fromisoformat(end)
            assert runner._month_span(s, e) == 2
        for earlier, later in zip(windows, windows[1:]):
            assert earlier[1] < later[0]
        # Windows land in distinct calendar years - the whole point.
        years = {w[0][:4] for w in windows}
        assert len(years) >= 5

    def test_spread_degrades_to_contiguous_when_span_is_tight(self):
        # 3 x 2mo needs 6 months; only 6 are available -> packed series.
        spread = compute_spread_windows(
            date(2025, 1, 1), 2, 3, data_start=date(2024, 7, 1)
        )
        contiguous = compute_chunk_windows(
            date(2025, 1, 1), 2, 3, data_start=date(2024, 7, 1)
        )
        assert spread == contiguous

    def test_spread_clamps_to_short_history(self):
        # Only 4 months of data but 3 x 2mo requested: clamp, do not
        # invent windows before the data starts.
        windows = compute_spread_windows(
            date(2025, 1, 1), 2, 3, data_start=date(2024, 9, 1)
        )
        assert windows
        assert all(w[0] >= "2024-09-01" for w in windows)
        assert windows[-1][1] == "2025-01-01"

    def test_degenerate_inputs(self):
        assert compute_spread_windows(date(2025, 1, 1), 0, 3) == []
        assert compute_spread_windows(date(2025, 1, 1), 2, 0) == []

    def test_mode_selects_the_layout(self):
        recent = runner.cut_windows(
            date(2026, 7, 1), 2, 4, date(2018, 7, 1), mode="recent"
        )
        spread = runner.cut_windows(
            date(2026, 7, 1), 2, 4, date(2018, 7, 1), mode="spread"
        )
        # Recent packs into the last 8 months; spread reaches 2018.
        assert recent[0][0] > "2025-01-01"
        assert spread[0][0] == "2018-07-01"


class TestMultiYearResolution:
    """resolve_chunk_windows over years, with a late-listing symbol."""

    # BTC 1m starts 2018-01-01 though 5m reaches 2017-08-17;
    # SUI does not exist before its 2023-05-03 listing.
    COVERAGE = {
        "BTC-USDC": (date(2017, 8, 17), date(2026, 7, 28)),
        "SUI-USDC": (date(2023, 5, 3), date(2026, 7, 28)),
    }
    M1_START = {
        "BTC-USDC": date(2018, 1, 1),
        "SUI-USDC": date(2023, 5, 3),
    }
    M1_END = {
        "BTC-USDC": date(2026, 7, 28),
        "SUI-USDC": date(2026, 7, 28),
    }

    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: self.COVERAGE.get(symbol),
        )
        monkeypatch.setattr(
            runner,
            "_coverage_1m_start",
            lambda symbol, data_dir: self.M1_START.get(symbol),
        )
        monkeypatch.setattr(
            runner,
            "_coverage_1m_end",
            lambda symbol, data_dir: self.M1_END.get(symbol),
        )

    def test_per_symbol_series_uses_each_symbols_own_history(self):
        out = runner.resolve_chunk_windows(
            ["BTC-USDC", "SUI-USDC"], 2, 6, span_years=8
        )
        by_symbol = out["windows_by_symbol"]
        assert set(by_symbol) == {"BTC-USDC", "SUI-USDC"}
        # BTC reaches back to its 1m floor, not to SUI's listing.
        assert by_symbol["BTC-USDC"][0][0] == "2018-07-28"
        # SUI is clamped to its listing date.
        assert by_symbol["SUI-USDC"][0][0] >= "2023-05-03"
        # Both end at the shared anchor.
        assert by_symbol["BTC-USDC"][-1][1] == "2026-07-28"
        assert by_symbol["SUI-USDC"][-1][1] == "2026-07-28"

    def test_1m_start_beats_5m_start(self):
        """BTC 5m reaches 2017-08 but 1m does not - never use 5m."""
        out = runner.resolve_chunk_windows(
            ["BTC-USDC"], 2, 6, span_years=20
        )
        assert out["data_start"] == "2018-01-01"
        assert out["windows"][0][0] >= "2018-01-01"

    def test_shared_series_is_the_intersection(self):
        out = runner.resolve_chunk_windows(
            ["BTC-USDC", "SUI-USDC"], 2, 6, span_years=8
        )
        # The shared series can never predate the youngest symbol.
        assert out["data_start"] == "2023-05-03"
        assert out["windows"][0][0] >= "2023-05-03"

    def test_coverage_reports_asymmetry(self):
        out = runner.resolve_chunk_windows(
            ["BTC-USDC", "SUI-USDC"], 2, 6, span_years=8
        )
        coverage = out["coverage"]
        assert coverage["BTC-USDC"]["data_start"] == "2018-01-01"
        assert coverage["SUI-USDC"]["data_start"] == "2023-05-03"
        assert coverage["SUI-USDC"]["listing_limited"] is True
        assert coverage["BTC-USDC"]["listing_limited"] is False
        # Months are the evaluated span, not a guess.
        assert coverage["BTC-USDC"]["months"] > 100
        assert coverage["SUI-USDC"]["months"] < 45
        assert coverage["BTC-USDC"]["n_windows"] == 6

    def test_small_store_boundary_is_not_flagged_as_asymmetry(
        self, monkeypatch
    ):
        """BTC 1m starts 2018-01, ETH 1m 2017-08 - that is not a gap."""
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: (date(2017, 8, 17), date(2026, 7, 28)),
        )
        monkeypatch.setattr(
            runner,
            "_coverage_1m_start",
            lambda symbol, data_dir: {
                "BTC-USDC": date(2018, 1, 1),
                "ETH-USDC": date(2017, 8, 18),
            }[symbol],
        )
        out = runner.resolve_chunk_windows(
            ["BTC-USDC", "ETH-USDC"], 2, 6, span_years=8
        )
        assert out["coverage"]["BTC-USDC"]["listing_limited"] is False
        assert out["coverage"]["ETH-USDC"]["listing_limited"] is False

    def test_shared_windows_opt_out(self):
        out = runner.resolve_chunk_windows(
            ["BTC-USDC", "SUI-USDC"], 2, 6, span_years=8, per_symbol=False
        )
        assert out["windows_by_symbol"] == {}
        assert len(out["windows"]) == 6

    def test_span_years_bounds_the_reach(self):
        narrow = runner.resolve_chunk_windows(
            ["BTC-USDC"], 2, 4, span_years=2
        )
        wide = runner.resolve_chunk_windows(
            ["BTC-USDC"], 2, 4, span_years=8
        )
        assert narrow["windows"][0][0] > "2024-01-01"
        assert wide["windows"][0][0] < "2019-01-01"

    def test_env_defaults_drive_the_mode(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_WINDOW_MODE", "recent")
        monkeypatch.setenv("VALIDATION_SPAN_YEARS", "3")
        out = runner.resolve_chunk_windows(["BTC-USDC"], 2, 4)
        assert out["mode"] == "recent"
        assert out["span_years"] == 3
        # Recent mode = one contiguous 8-month block at the anchor.
        assert out["windows"][0][0] > "2025-01-01"


class TestListingBoundary:
    """A mid-day listing must not produce a window the engine rejects.

    SUI-USDC's first 1m candle is 2023-05-03T12:00:00, so a window
    starting 2023-05-03 is NOT covered and the engine's 1m guard fails
    it. The 1m start therefore rounds up to a whole day.
    """

    def test_day_ceiling_rounds_mid_day_starts_up(self):
        assert runner._day_ceiling("2023-05-03T12:00:00") == date(
            2023, 5, 4
        )
        assert runner._day_ceiling("2023-05-03T00:00:00") == date(
            2023, 5, 3
        )
        assert runner._day_ceiling("2023-05-03") == date(2023, 5, 3)
        assert runner._day_ceiling("2023-05-03T00:00:01") == date(
            2023, 5, 4
        )

    def test_window_start_clears_the_listing_timestamp(self, monkeypatch):
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: (date(2023, 5, 3), date(2026, 7, 28)),
        )
        monkeypatch.setattr(
            runner,
            "_coverage_1m_start",
            lambda symbol, data_dir: runner._day_ceiling(
                "2023-05-03T12:00:00"
            ),
        )
        monkeypatch.setattr(
            runner,
            "_coverage_1m_end",
            lambda symbol, data_dir: date(2026, 7, 28),
        )
        out = runner.resolve_chunk_windows(["SUI-USDC"], 2, 3, span_years=8)
        assert out["data_start"] == "2023-05-04"
        assert out["windows"][0][0] == "2023-05-04"


class TestRuntimeEstimate:
    def test_scales_with_total_bars(self):
        one = estimate_runtime_seconds(1, 1, 3, 2, seconds_per_month=10.0)
        assert one == 60.0
        # 2 strategies x 3 symbols x 6 windows x 2 months
        many = estimate_runtime_seconds(2, 3, 6, 2, seconds_per_month=10.0)
        assert many == 720.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_SECONDS_PER_MONTH", "5")
        assert estimate_runtime_seconds(1, 1, 2, 2) == 20.0

    def test_zero_factors(self):
        assert estimate_runtime_seconds(0, 3, 6, 2) == 0.0

    def test_format_duration(self):
        assert format_duration(45) == "45s"
        assert format_duration(600) == "10m"
        assert format_duration(3 * 3600 + 12 * 60) == "3h 12m"

    def test_window_spec_records_the_span(self):
        assert build_window_spec(6, 2, "spread", 8) == "6x2mo@8y"
        assert build_window_spec(3, 2, "recent", 8) == "3x2mo"


class TestRegimeCoverageReporting:
    """Per-window regime breakdown - the payoff of a multi-year span."""

    def _patch(self, monkeypatch, regimes_by_window):
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: (date(2018, 1, 1), date(2026, 1, 1)),
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_start", lambda symbol, data_dir: None
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_end", lambda symbol, data_dir: None
        )

        def fake_backtest(strategy, symbol, start, end, capital):
            return {
                "returns": list(GOOD_CHUNK),
                "regimes": dict(regimes_by_window(start)),
                "diagnosis": "traded",
            }

        monkeypatch.setattr(runner, "_run_chunk_backtest", fake_backtest)

    def test_regimes_recorded_per_chunk_and_pooled(
        self, monkeypatch, tmp_db
    ):
        self._patch(
            monkeypatch,
            lambda start: (
                {"trending_strong": 900, "ranging_calm": 100}
                if start < "2022-01-01"
                else {"ranging_volatile": 400, "ranging_calm": 600}
            ),
        )
        result = validate_strategy("mean_reversion", ["BTC-USDC"], 2, 4)
        assert result["chunks"]
        for chunk in result["chunks"]:
            assert sum(chunk["regimes"].values()) == 1000
            assert chunk["diagnosis"] == "traded"
        # Pooled histogram spans both epochs.
        pooled = result["regimes"]
        assert pooled["trending_strong"] > 0
        assert pooled["ranging_volatile"] > 0
        assert sum(pooled.values()) == 1000 * len(result["chunks"])

    def test_collect_regime_coverage_dedupes_across_strategies(self):
        chunk = {
            "symbol": "BTC-USDC",
            "start": "2020-01-01",
            "end": "2020-03-01",
            "n_trades": 3,
            "sum_return": 0.1,
            "regimes": {"trending_strong": 500, "ranging_calm": 500},
        }
        results = [
            {"chunks": [dict(chunk)]},
            {"chunks": [dict(chunk)]},
        ]
        rows, pooled = collect_regime_coverage(results)
        assert len(rows) == 1
        assert rows[0]["bars"] == 1000
        assert pooled == {"trending_strong": 500, "ranging_calm": 500}

    def test_regime_shares_formatting(self):
        text = runner.format_regime_shares(
            {"trending_strong": 700, "ranging_calm": 300}
        )
        assert "trending_strong 70%" in text
        assert runner.format_regime_shares({}) == "-"

    def test_single_regime_span_is_flagged(self, monkeypatch, capsys):
        results = [
            {
                "chunks": [
                    {
                        "symbol": "BTC-USDC",
                        "start": "2020-01-01",
                        "end": "2020-03-01",
                        "regimes": {
                            "ranging_calm": 950,
                            "trending_strong": 50,
                        },
                    }
                ]
            }
        ]
        runner.print_regime_coverage(results)
        out = capsys.readouterr().out
        assert "REGIME COVERAGE BY WINDOW" in out
        assert "single regime epoch" in out

    def test_data_spans_flag_asymmetry(self, capsys):
        results = [
            {
                "coverage": {
                    "BTC-USDC": {
                        "data_start": "2018-01-01",
                        "data_end": "2026-07-28",
                        "months": 102,
                        "listing_limited": False,
                    },
                    "SUI-USDC": {
                        "data_start": "2023-05-03",
                        "data_end": "2026-07-28",
                        "months": 38,
                        "listing_limited": True,
                    },
                }
            }
        ]
        runner.print_data_spans(results)
        out = capsys.readouterr().out
        assert "PER-SYMBOL DATA SPANS" in out
        assert "SHORTER HISTORY" in out
        assert "ASYMMETRIC" in out


class TestDryRun:
    def test_dry_run_prints_plan_and_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(
            runner,
            "_data_coverage",
            lambda symbol, data_dir: (date(2018, 1, 1), date(2026, 1, 1)),
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_start", lambda symbol, data_dir: None
        )
        monkeypatch.setattr(
            runner, "_coverage_1m_end", lambda symbol, data_dir: None
        )

        def boom(*args, **kwargs):
            raise AssertionError("dry run must not backtest")

        monkeypatch.setattr(runner, "run_once", boom)
        code = runner.main(
            [
                "--dry-run",
                "--strategies",
                "mean_reversion",
                "--symbols",
                "BTC-USDC",
                "--windows",
                "6",
                "--window-months",
                "2",
                "--span-years",
                "8",
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "VALIDATION PLAN (dry run) - 6x2mo@8y" in out
        assert "estimated runtime" in out
        assert "2018-" in out  # the plan really reaches back


class TestRefreshData:
    """Opt-in --refresh-data / VALIDATION_REFRESH_DATA behavior."""

    class _FakeManager:
        """Records CandleDownloadManager calls; never hits the network."""

        instances = []

        def __init__(self, data_dir=None, **kwargs):
            self.data_dir = data_dir
            self.coverage_calls = []
            self.ensure_calls = []
            TestRefreshData._FakeManager.instances.append(self)

        def coverage(self, symbol, tf):
            self.coverage_calls.append((symbol, tf))

            class _Cov:
                end = "2025-01-01T00:00"

            return _Cov()

        def ensure(self, symbol, tf, start, end=None, **kwargs):
            self.ensure_calls.append(
                (symbol, tf, start, end, kwargs)
            )
            return {"added": 5}

    @pytest.fixture(autouse=True)
    def _reset_fake_manager(self):
        TestRefreshData._FakeManager.instances = []
        yield
        TestRefreshData._FakeManager.instances = []

    def _stub_validate(self, monkeypatch):
        def fake_validate(strategy, *args, **kwargs):
            return {
                "strategy": strategy,
                "symbols": ["SUI-USDC"],
                "window_spec": "3x2mo",
                "chunks": [],
                "verdict": None,
                "overall": "PASS",
                "data_start": "2024-01-01",
                "data_end": "2025-01-01",
            }

        monkeypatch.setattr(runner, "validate_strategy", fake_validate)

    def test_refresh_data_flag_parses(self, monkeypatch):
        monkeypatch.delenv("VALIDATION_REFRESH_DATA", raising=False)
        calls = []

        def fake_run_once(*args, **kwargs):
            calls.append(kwargs)
            return []

        monkeypatch.setattr(runner, "run_once", fake_run_once)
        assert (
            runner.main(
                ["--once", "--strategies", "mean_reversion",
                 "--refresh-data"]
            )
            == 0
        )
        assert calls[0]["refresh_data"] is True

        calls.clear()
        assert (
            runner.main(["--once", "--strategies", "mean_reversion"]) == 0
        )
        assert calls[0]["refresh_data"] is False

    def test_refresh_data_env_var_default(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_REFRESH_DATA", "true")
        calls = []

        def fake_run_once(*args, **kwargs):
            calls.append(kwargs)
            return []

        monkeypatch.setattr(runner, "run_once", fake_run_once)
        assert (
            runner.main(["--once", "--strategies", "mean_reversion"]) == 0
        )
        assert calls[0]["refresh_data"] is True

    def test_refresh_skipped_by_default(self, monkeypatch, tmp_db):
        self._stub_validate(monkeypatch)
        refreshed = []
        monkeypatch.setattr(
            runner,
            "refresh_market_data",
            lambda symbols, data_dir=None: refreshed.append(symbols),
        )
        run_once(["mean_reversion"], ["SUI-USDC"], 2, 3)
        assert refreshed == []

    def test_refresh_invoked_when_enabled(self, monkeypatch, tmp_db):
        self._stub_validate(monkeypatch)
        refreshed = []
        monkeypatch.setattr(
            runner,
            "refresh_market_data",
            lambda symbols, data_dir=None: refreshed.append(symbols),
        )
        run_once(
            ["mean_reversion"], ["SUI-USDC"], 2, 3, refresh_data=True
        )
        assert refreshed == [["SUI-USDC"]]

    def test_refresh_uses_update_mode_1m(self, monkeypatch, tmp_path):
        import trading_bot_v2.data_manager as dm

        monkeypatch.setattr(
            dm, "CandleDownloadManager", TestRefreshData._FakeManager
        )
        runner.refresh_market_data(
            ["SUI-USDC", "BTC-USDC"], data_dir=str(tmp_path)
        )
        assert len(TestRefreshData._FakeManager.instances) == 1
        mgr = TestRefreshData._FakeManager.instances[0]
        assert mgr.coverage_calls == [
            ("SUI-USDC", "1m"),
            ("BTC-USDC", "1m"),
        ]
        # Update mode: start = last stored candle, end = now (None),
        # internal gaps skipped.
        assert mgr.ensure_calls == [
            (
                "SUI-USDC", "1m", "2025-01-01T00:00", None,
                {"include_internal_gaps": False},
            ),
            (
                "BTC-USDC", "1m", "2025-01-01T00:00", None,
                {"include_internal_gaps": False},
            ),
        ]


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
