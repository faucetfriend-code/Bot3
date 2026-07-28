"""Data-coverage guard tests for the backtest engine.

The engine used to guard only 1m: every other timeframe could fall short
of the requested window and the run would silently truncate (5m) or
freeze on a stale bar (15m/1h/4h) instead of failing. These tests pin the
extended guard - per timeframe, per strictness mode, and across the
warmup prefix - on synthetic CSV stores so no network and no canonical
parquet store is touched.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from trading_bot_v2.backtesting.data_loader import (
    BacktestDataLoader,
    autodownload_lever,
)
from trading_bot_v2.backtesting.engine import (
    CONTEXT_TIMEFRAMES,
    COVERAGE_TIMEFRAMES,
    DEFAULT_COVERAGE_STRICTNESS,
    EXECUTION_TIMEFRAMES,
    BacktestEngine,
    validate_coverage_strictness,
)

SYMBOL = "GUARD-USDC"
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}

# Store spans 2024-01-01 .. 2024-01-11; the guarded window sits inside it
# with room on both sides so a shortfall can be introduced on either.
STORE_FIRST = datetime(2024, 1, 1)
STORE_LAST = datetime(2024, 1, 11)
WINDOW_START = "2024-01-03"
WINDOW_END = "2024-01-09"


def _write_timeframe(
    data_dir: Path,
    timeframe: str,
    first: datetime,
    last: datetime,
) -> None:
    """Write a synthetic CSV candle series for one timeframe."""
    step = timedelta(minutes=TF_MINUTES[timeframe])
    path = data_dir / f"{SYMBOL}_{timeframe}.csv"
    with path.open("w", newline="", encoding="ascii") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        stamp = first
        while stamp < last:
            writer.writerow(
                [
                    stamp.strftime("%Y-%m-%dT%H:%M:%S"),
                    100.0,
                    100.5,
                    99.5,
                    100.0,
                    1000.0,
                ]
            )
            stamp += step


def build_store(
    data_dir: Path,
    first: Optional[Dict[str, datetime]] = None,
    last: Optional[Dict[str, datetime]] = None,
) -> Path:
    """Build a full five-timeframe store, optionally short on some of them.

    Args:
        data_dir: Directory to write the CSV store into.
        first: Per-timeframe overrides for the first candle.
        last: Per-timeframe overrides for the last candle.

    Returns:
        The store directory.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    for timeframe in COVERAGE_TIMEFRAMES:
        _write_timeframe(
            data_dir,
            timeframe,
            (first or {}).get(timeframe, STORE_FIRST),
            (last or {}).get(timeframe, STORE_LAST),
        )
    return data_dir


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No network, and no strictness leaking in from the environment."""
    monkeypatch.setenv("DATA_AUTODOWNLOAD", "false")
    monkeypatch.delenv("BACKTEST_COVERAGE_STRICTNESS", raising=False)


@pytest.fixture
def logged():
    """Capture loguru WARNING+ records (the engine logs via loguru)."""
    from loguru import logger

    records: List[str] = []
    sink_id = logger.add(
        lambda m: records.append(m.record["level"].name + "|" + m.record["message"]),
        level="WARNING",
    )
    yield records
    logger.remove(sink_id)


def guard(data_dir: Path, warmup: int = 0, funnel=None) -> None:
    """Run the coverage guard against a store, with a fresh loader."""
    engine = BacktestEngine()
    loader = BacktestDataLoader(symbol=SYMBOL, data_dir=str(data_dir))
    kwargs = {"funnel": funnel} if funnel is not None else {}
    engine._check_data_coverage(
        loader, SYMBOL, WINDOW_START, WINDOW_END, warmup, **kwargs
    )


class TestGuardFiresPerTimeframe:
    @pytest.mark.parametrize("short_tf", COVERAGE_TIMEFRAMES)
    def test_trailing_shortfall_raises(self, tmp_path, short_tf):
        """Every timeframe is guarded, not just 1m."""
        store = build_store(tmp_path, last={short_tf: datetime(2024, 1, 6)})
        with pytest.raises(ValueError) as excinfo:
            guard(store)
        assert f"{short_tf} candle data for {SYMBOL}" in str(excinfo.value)

    @pytest.mark.parametrize("short_tf", COVERAGE_TIMEFRAMES)
    def test_leading_shortfall_raises(self, tmp_path, short_tf):
        """A store that starts inside the window is refused too."""
        store = build_store(tmp_path, first={short_tf: datetime(2024, 1, 5)})
        with pytest.raises(ValueError) as excinfo:
            guard(store)
        assert f"{short_tf} candle data for {SYMBOL}" in str(excinfo.value)

    @pytest.mark.parametrize("timeframe", COVERAGE_TIMEFRAMES)
    def test_covered_store_passes(self, tmp_path, timeframe, logged):
        """A store that covers the window raises nothing and warns nothing."""
        store = build_store(tmp_path)
        guard(store)
        assert not [r for r in logged if "candle data" in r or "warmup" in r]

    def test_all_short_timeframes_reported_at_once(self, tmp_path):
        """The error lists every failing timeframe, not just the first."""
        store = build_store(
            tmp_path,
            last={"5m": datetime(2024, 1, 6), "4h": datetime(2024, 1, 6)},
        )
        with pytest.raises(ValueError) as excinfo:
            guard(store)
        message = str(excinfo.value)
        assert "5m candle data" in message
        assert "4h candle data" in message


class TestErrorMessageShape:
    """The message keeps the shape of the original 1m guard."""

    @pytest.fixture
    def message(self, tmp_path):
        store = build_store(tmp_path, last={"1m": datetime(2024, 1, 6)})
        with pytest.raises(ValueError) as excinfo:
            guard(store)
        return str(excinfo.value)

    def test_names_symbol_and_window(self, message):
        assert SYMBOL in message
        assert f"{WINDOW_START} .. {WINDOW_END}" in message

    def test_names_actual_coverage_bounds(self, message):
        assert "2024-01-01T00:00:00 .. 2024-01-05T23:59:00" in message

    def test_names_backfill_command(self, message):
        assert "python -m trading_bot_v2.data_manager" in message
        assert f"--symbols {SYMBOL} --timeframes 1m" in message

    def test_quantifies_the_shortfall(self, message):
        # Last stored 1m candle is 2024-01-05T23:59; the window runs to
        # 2024-01-09T00:00, i.e. three whole days of missing minutes
        # (the check's one-step tolerance cancels the open interval).
        assert "missing 0 leading and 4320 trailing 1m candles" in message

    def test_names_the_autodownload_lever(self, message):
        assert "DATA_AUTODOWNLOAD" in message


class TestAutodownloadLever:
    def test_off_lever_points_at_the_switch(self, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "false")
        assert "DATA_AUTODOWNLOAD=false" in autodownload_lever("5m")

    def test_1m_is_named_as_excluded_when_enabled(self, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        lever = autodownload_lever("1m")
        assert "EXCLUDES '1m'" in lever
        assert "DATA_AUTODOWNLOAD_TIMEFRAMES" in lever

    def test_allowed_timeframe_says_the_fetch_already_ran(self, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        assert "already ran" in autodownload_lever("5m")

    def test_guard_fires_only_after_autodownload_had_its_chance(
        self, tmp_path, monkeypatch
    ):
        """A download that closes the gap must be able to save the run."""
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        store = build_store(tmp_path, last={"5m": datetime(2024, 1, 6)})

        calls: List[str] = []

        class FakeManager:
            def ensure(self, symbol, timeframe, start, end, **kwargs):
                calls.append(timeframe)
                _write_timeframe(store, timeframe, STORE_FIRST, STORE_LAST)
                return {"added": 1, "requested_ranges": [(start, end)]}

        engine = BacktestEngine()
        loader = BacktestDataLoader(
            symbol=SYMBOL,
            data_dir=str(store),
            download_manager=FakeManager(),
        )
        engine._check_data_coverage(loader, SYMBOL, WINDOW_START, WINDOW_END, 0)
        assert "5m" in calls

    def test_1m_gap_is_not_offered_to_the_downloader(self, tmp_path, monkeypatch):
        """1m is excluded from auto-download, so the guard must still fire."""
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        store = build_store(tmp_path, last={"1m": datetime(2024, 1, 6)})

        calls: List[str] = []

        class FakeManager:
            def ensure(self, symbol, timeframe, start, end, **kwargs):
                calls.append(timeframe)
                return {"added": 0}

        engine = BacktestEngine()
        loader = BacktestDataLoader(
            symbol=SYMBOL,
            data_dir=str(store),
            download_manager=FakeManager(),
        )
        with pytest.raises(ValueError, match="1m candle data"):
            engine._check_data_coverage(loader, SYMBOL, WINDOW_START, WINDOW_END, 0)
        assert "1m" not in calls


class TestStrictnessModes:
    @pytest.mark.parametrize("short_tf", EXECUTION_TIMEFRAMES)
    def test_execution_mode_still_fails_on_execution_timeframes(
        self, tmp_path, monkeypatch, short_tf
    ):
        monkeypatch.setenv("BACKTEST_COVERAGE_STRICTNESS", "execution")
        store = build_store(tmp_path, last={short_tf: datetime(2024, 1, 6)})
        with pytest.raises(ValueError, match=f"{short_tf} candle data"):
            guard(store)

    @pytest.mark.parametrize("short_tf", CONTEXT_TIMEFRAMES)
    def test_execution_mode_downgrades_context_timeframes(
        self, tmp_path, monkeypatch, short_tf, logged
    ):
        monkeypatch.setenv("BACKTEST_COVERAGE_STRICTNESS", "execution")
        store = build_store(tmp_path, last={short_tf: datetime(2024, 1, 6)})
        guard(store)  # must not raise
        errors = [r for r in logged if r.startswith("ERROR|")]
        assert any("DEGRADED BACKTEST" in r for r in errors)
        assert any(f"{short_tf} candle data" in r for r in errors)
        # The downgrade is loud about what the numbers now mean.
        assert any("FROZEN" in r for r in errors)

    @pytest.mark.parametrize("short_tf", COVERAGE_TIMEFRAMES)
    def test_warn_mode_never_raises_but_always_logs(
        self, tmp_path, monkeypatch, short_tf, logged
    ):
        monkeypatch.setenv("BACKTEST_COVERAGE_STRICTNESS", "warn")
        store = build_store(tmp_path, last={short_tf: datetime(2024, 1, 6)})
        guard(store)
        assert any(
            r.startswith("ERROR|") and f"{short_tf} candle data" in r for r in logged
        ), "warn mode must still be loud - it is not silent truncation"

    @pytest.mark.parametrize("short_tf", CONTEXT_TIMEFRAMES)
    def test_default_mode_is_strict_for_context_timeframes(self, tmp_path, short_tf):
        store = build_store(tmp_path, last={short_tf: datetime(2024, 1, 6)})
        with pytest.raises(ValueError, match=f"{short_tf} candle data"):
            guard(store)


class TestStrictnessValidation:
    @pytest.mark.parametrize("mode", ["all", "execution", "warn"])
    def test_known_modes_survive(self, mode):
        assert validate_coverage_strictness(mode) == mode

    @pytest.mark.parametrize("mode", ["ALL", " Execution ", "WARN"])
    def test_modes_are_normalised(self, mode):
        assert validate_coverage_strictness(mode) == mode.strip().lower()

    @pytest.mark.parametrize("value", ["lenient", "off", "0", 17, object()])
    def test_unknown_values_fall_back_to_the_strictest_mode(self, value):
        assert validate_coverage_strictness(value) == DEFAULT_COVERAGE_STRICTNESS

    @pytest.mark.parametrize("value", [None, ""])
    def test_unset_uses_the_default(self, value):
        assert validate_coverage_strictness(value) == DEFAULT_COVERAGE_STRICTNESS

    def test_default_is_the_strict_mode(self):
        assert DEFAULT_COVERAGE_STRICTNESS == "all"

    def test_typo_warns_so_it_cannot_disarm_the_guard_quietly(self, logged):
        validate_coverage_strictness("lenietn")
        assert any("BACKTEST_COVERAGE_STRICTNESS" in r for r in logged)


class TestWarmupInteraction:
    def test_short_warmup_prefix_warns_but_does_not_raise(self, tmp_path, logged):
        """The window is covered; only the pre-window prefix is short."""
        # 4h warmup of 60 candles reaches 10 days before WINDOW_START
        # (2023-12-24), which the store does not have.
        store = build_store(tmp_path)
        guard(store, warmup=60)
        warnings = [r for r in logged if r.startswith("WARNING|")]
        assert any("4h warmup prefix" in r for r in warnings)
        assert any("IS fully covered" in r for r in warnings)

    def test_warmup_warning_quantifies_the_shortfall(self, tmp_path, logged):
        store = build_store(tmp_path)
        guard(store, warmup=60)
        joined = "\n".join(logged)
        # 2023-12-24T00:00 .. 2024-01-01T00:00 is 8 days = 48 4h candles,
        # less the one-step tolerance.
        assert "4h warmup prefix" in joined
        assert "short by 47 candles" in joined

    def test_guard_reasons_about_the_loaded_span_not_only_the_window(
        self, tmp_path, logged
    ):
        """With warmup=0 the same store produces no prefix warning."""
        store = build_store(tmp_path)
        guard(store, warmup=0)
        assert not [r for r in logged if "warmup prefix" in r]

    def test_warmup_shortfall_is_never_fatal(self, tmp_path):
        """Fatal warmup would reject the earliest window of the 8-year
        campaign, whose BTC 1m store begins exactly at the window start."""
        store = build_store(tmp_path)
        guard(store, warmup=100000)  # nothing on disk reaches back that far

    def test_window_shortfall_still_fatal_when_warmup_is_short_too(self, tmp_path):
        store = build_store(tmp_path, last={"5m": datetime(2024, 1, 6)})
        with pytest.raises(ValueError, match="5m candle data"):
            guard(store, warmup=60)


class TestFunnelNote:
    def test_degradation_is_recorded_on_the_funnel(self, tmp_path, monkeypatch):
        from trading_bot_v2.diagnostics.funnel import SignalFunnel

        monkeypatch.setenv("BACKTEST_COVERAGE_STRICTNESS", "execution")
        store = build_store(tmp_path, last={"4h": datetime(2024, 1, 6)})
        funnel = SignalFunnel(label="coverage-test")
        guard(store, funnel=funnel)
        note = funnel.notes["data_coverage"]
        assert note["strictness"] == "execution"
        assert note["degraded"]["4h"]["window_covered"] is False
        assert note["degraded"]["4h"]["missing_trailing"] > 0

    def test_clean_run_records_an_empty_degradation_map(self, tmp_path):
        from trading_bot_v2.diagnostics.funnel import SignalFunnel

        store = build_store(tmp_path)
        funnel = SignalFunnel(label="coverage-test")
        guard(store, funnel=funnel)
        assert funnel.notes["data_coverage"]["degraded"] == {}


class TestCoverageShortfallReport:
    def test_covered_range_reports_no_shortfall(self, tmp_path):
        store = build_store(tmp_path)
        loader = BacktestDataLoader(symbol=SYMBOL, data_dir=str(store))
        report = loader.coverage_shortfall("1h", WINDOW_START, WINDOW_END)
        assert report["covered"] is True
        assert report["missing_leading"] == 0
        assert report["missing_trailing"] == 0
        assert report["requested"] == 144  # 6 days of 1h candles

    def test_counts_are_zero_exactly_when_covers_is_true(self, tmp_path):
        store = build_store(tmp_path, last={"1h": datetime(2024, 1, 6)})
        loader = BacktestDataLoader(symbol=SYMBOL, data_dir=str(store))
        report = loader.coverage_shortfall("1h", WINDOW_START, WINDOW_END)
        assert report["covered"] is False
        assert report["missing_trailing"] > 0
        assert loader.covers("1h", WINDOW_START, WINDOW_END) is False

    def test_leading_and_trailing_are_counted_separately(self, tmp_path):
        store = build_store(
            tmp_path,
            first={"4h": datetime(2024, 1, 4)},
            last={"4h": datetime(2024, 1, 6)},
        )
        loader = BacktestDataLoader(symbol=SYMBOL, data_dir=str(store))
        report = loader.coverage_shortfall("4h", WINDOW_START, WINDOW_END)
        assert report["missing_leading"] == 5  # 2024-01-03 .. 2024-01-04
        assert report["missing_trailing"] == 18  # 2024-01-05T20 .. 2024-01-09
        assert report["first"] == "2024-01-04T00:00:00"

    def test_missing_store_reports_uncovered_without_raising(self, tmp_path):
        loader = BacktestDataLoader(symbol="NOPE-USDC", data_dir=str(tmp_path))
        report = loader.coverage_shortfall("4h", WINDOW_START, WINDOW_END)
        assert report["covered"] is False
        assert report["first"] is None

    def test_allow_download_false_skips_the_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        store = build_store(tmp_path, last={"5m": datetime(2024, 1, 6)})
        calls: List[str] = []

        class FakeManager:
            def ensure(self, symbol, timeframe, start, end, **kwargs):
                calls.append(timeframe)
                return {"added": 0}

        loader = BacktestDataLoader(
            symbol=SYMBOL, data_dir=str(store), download_manager=FakeManager()
        )
        loader.coverage_shortfall("5m", WINDOW_START, WINDOW_END, allow_download=False)
        assert calls == []


class TestEndToEndRefusal:
    def test_engine_run_refuses_a_truncated_5m_store(self, tmp_path, monkeypatch):
        """The reported bug: a short 5m store truncated the replay instead
        of failing, so two different --end dates gave identical results."""
        from trading_bot_v2.config import config

        store = build_store(tmp_path, last={"5m": datetime(2024, 1, 6)})
        monkeypatch.setattr(config, "backtest_data_dir", str(store))
        engine = BacktestEngine()
        with pytest.raises(ValueError, match="5m candle data"):
            engine.run(
                start=WINDOW_START,
                end=WINDOW_END,
                symbol=SYMBOL,
                initial_capital=10000.0,
                strategy_filter="mean_reversion",
            )

    def test_engine_run_completes_on_a_covered_store(self, tmp_path, monkeypatch):
        from trading_bot_v2.config import config

        store = build_store(tmp_path)
        monkeypatch.setattr(config, "backtest_data_dir", str(store))
        engine = BacktestEngine()
        result = engine.run(
            start=WINDOW_START,
            end=WINDOW_END,
            symbol=SYMBOL,
            initial_capital=10000.0,
            strategy_filter="mean_reversion",
        )
        assert result.final_equity > 0
