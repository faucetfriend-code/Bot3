"""
Tests for Trading Loop Profiler and Performance Benchmark (Plan 02-03).

Tests profiling accuracy, latency statistics, memory tracking,
database benchmarks, and signal benchmarks.

Run: pytest trading_bot_v2/tests/test_profiler_benchmark.py -v
"""

import json
import math
import os
import tempfile
import time

import pytest

from trading_bot_v2.trading_loop_profiler import (
    CPUProfiler,
    IterationTiming,
    LatencyStats,
    PhaseTiming,
    TradingLoopProfiler,
    _get_memory_bytes,
    get_profiler,
    profile_phase,
)
from trading_bot_v2.performance_benchmark import (
    BenchmarkResult,
    DatabaseBenchmark,
    PerformanceBenchmark,
    SignalBenchmark,
)


# ============================================================================
# LatencyStats Tests
# ============================================================================


class TestLatencyStats:
    """Tests for LatencyStats aggregation."""

    def test_empty_stats(self):
        """Empty stats should have correct defaults."""
        stats = LatencyStats(phase="test")
        stats.compute()
        assert stats.count == 0
        assert stats.mean_ms == 0.0
        assert stats.p50_ms == 0.0

    def test_single_sample(self):
        """Single sample should produce identical statistics."""
        stats = LatencyStats(phase="test")
        stats.add_sample(5.0)
        stats.compute()
        assert stats.count == 1
        assert stats.min_ms == 5.0
        assert stats.max_ms == 5.0
        assert stats.mean_ms == 5.0
        assert stats.p50_ms == 5.0
        assert stats.p95_ms == 5.0
        assert stats.p99_ms == 5.0

    def test_multiple_samples(self):
        """Multiple samples should compute correct percentiles."""
        stats = LatencyStats(phase="test")
        for i in range(100):
            stats.add_sample(float(i))
        stats.compute()

        assert stats.count == 100
        assert stats.min_ms == 0.0
        assert stats.max_ms == 99.0
        assert stats.mean_ms == pytest.approx(49.5, abs=0.1)
        assert stats.p50_ms == pytest.approx(49.0, abs=1.0)
        assert stats.p95_ms >= 90.0
        assert stats.p99_ms >= 95.0

    def test_to_dict(self):
        """to_dict should return serializable dictionary."""
        stats = LatencyStats(phase="test")
        stats.add_sample(1.0)
        stats.add_sample(2.0)
        stats.compute()
        d = stats.to_dict()
        assert d["phase"] == "test"
        assert d["count"] == 2
        assert "min_ms" in d
        assert "max_ms" in d
        assert "mean_ms" in d
        assert "p50_ms" in d
        assert "p95_ms" in d
        assert "p99_ms" in d

    def test_std_dev(self):
        """Standard deviation should be computed correctly."""
        stats = LatencyStats(phase="test")
        for val in [10.0, 12.0, 14.0, 16.0, 18.0]:
            stats.add_sample(val)
        stats.compute()
        expected_std = math.sqrt(sum((x - 14.0) ** 2 for x in [10, 12, 14, 16, 18]) / 4)
        assert stats.std_dev_ms == pytest.approx(expected_std, abs=0.01)


# ============================================================================
# PhaseTiming Tests
# ============================================================================


class TestPhaseTiming:
    """Tests for PhaseTiming data class."""

    def test_finalize(self):
        """finalize() should compute derived fields."""
        timing = PhaseTiming(
            phase="test",
            start_time=100.0,
            memory_before_bytes=1000,
            memory_after_bytes=1200,
        )
        timing.end_time = 100.05
        timing.finalize()

        assert timing.duration_ms == pytest.approx(50.0, abs=0.1)
        assert timing.memory_delta_bytes == 200


# ============================================================================
# IterationTiming Tests
# ============================================================================


class TestIterationTiming:
    """Tests for IterationTiming data class."""

    def test_finalize(self):
        """finalize() should compute total duration."""
        iteration = IterationTiming(iteration=1, start_time=100.0)
        iteration.end_time = 100.3
        iteration.finalize()

        assert iteration.total_duration_ms == pytest.approx(300.0, abs=0.1)

    def test_get_phase_duration(self):
        """get_phase_duration_ms() should return correct phase duration."""
        iteration = IterationTiming(iteration=1, start_time=0.0)
        iteration.phases = [
            PhaseTiming(phase="a", start_time=0.0, end_time=0.01),
            PhaseTiming(phase="b", start_time=0.01, end_time=0.03),
        ]
        iteration.phases[0].finalize()
        iteration.phases[1].finalize()

        assert iteration.get_phase_duration_ms("a") == pytest.approx(10.0, abs=0.1)
        assert iteration.get_phase_duration_ms("b") == pytest.approx(20.0, abs=0.1)
        assert iteration.get_phase_duration_ms("c") == 0.0


# ============================================================================
# TradingLoopProfiler Tests
# ============================================================================


class TestTradingLoopProfiler:
    """Tests for the main TradingLoopProfiler."""

    def test_basic_profiling(self):
        """Basic profiling should track iterations and phases."""
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=False, prometheus=False
        )
        profiler.start()

        for _ in range(5):
            profiler.begin_iteration()
            with profiler.phase("test_phase"):
                time.sleep(0.001)
            profiler.end_iteration()

        profiler.stop()

        report = profiler.get_report()
        assert report["summary"]["total_iterations"] == 5
        assert report["summary"]["enabled"] is True
        assert "test_phase" in report["phase_statistics"]

    def test_disabled_profiler(self):
        """Disabled profiler should not collect data."""
        profiler = TradingLoopProfiler(enabled=False)
        profiler.start()

        profiler.begin_iteration()
        with profiler.phase("test"):
            time.sleep(0.001)
        profiler.end_iteration()

        profiler.stop()
        report = profiler.get_report()
        assert report["summary"]["total_iterations"] == 0

    def test_enable_disable(self):
        """enable()/disable() should toggle profiling state."""
        profiler = TradingLoopProfiler(prometheus=False)
        assert profiler.enabled is False

        profiler.enable()
        assert profiler.enabled is True

        profiler.disable()
        assert profiler.enabled is False

    def test_bottleneck_detection(self):
        """Bottlenecks should be detected for slow phases."""
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=False, prometheus=False
        )
        profiler.start()

        for _ in range(10):
            profiler.begin_iteration()
            with profiler.phase("fast_phase"):
                time.sleep(0.001)
            with profiler.phase("slow_phase"):
                time.sleep(0.01)  # 10x slower
            profiler.end_iteration()

        profiler.stop()
        report = profiler.get_report()

        # slow_phase should be a bottleneck
        bottleneck_phases = [b["phase"] for b in report["bottlenecks"]]
        assert "slow_phase" in bottleneck_phases

    def test_reset(self):
        """reset() should clear all profiling data."""
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=False, prometheus=False
        )
        profiler.start()

        profiler.begin_iteration()
        with profiler.phase("test"):
            time.sleep(0.001)
        profiler.end_iteration()

        profiler.reset()
        report = profiler.get_report()
        assert report["summary"]["total_iterations"] == 0

    def test_text_summary(self):
        """get_text_summary() should return formatted string."""
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=False, prometheus=False
        )
        profiler.start()

        profiler.begin_iteration()
        with profiler.phase("test"):
            time.sleep(0.001)
        profiler.end_iteration()

        profiler.stop()
        summary = profiler.get_text_summary()
        assert "Trading Loop Profiler" in summary
        assert "test:" in summary

    def test_save_report_json(self):
        """save_report() should write JSON file."""
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=False, prometheus=False
        )
        profiler.start()
        profiler.begin_iteration()
        profiler.end_iteration()
        profiler.stop()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        try:
            profiler.save_report(output_path=output_path, format="json")
            assert os.path.exists(output_path)

            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "timestamp" in data
            assert "summary" in data
        finally:
            os.unlink(output_path)

    def test_save_report_text(self):
        """save_report() should write text file."""
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=False, prometheus=False
        )
        profiler.start()
        profiler.begin_iteration()
        profiler.end_iteration()
        profiler.stop()

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            output_path = f.name

        try:
            profiler.save_report(output_path=output_path, format="text")
            assert os.path.exists(output_path)

            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "TRADING LOOP PROFILER REPORT" in content
        finally:
            os.unlink(output_path)

    def test_global_profiler_singleton(self):
        """get_profiler() should return the same instance."""
        p1 = get_profiler()
        p2 = get_profiler()
        assert p1 is p2


# ============================================================================
# Profile Phase Decorator Tests
# ============================================================================


class TestProfilePhaseDecorator:
    """Tests for @profile_phase decorator."""

    def test_decorator_profiles_function(self):
        """Decorated function should be profiled via global profiler."""
        import trading_bot_v2.trading_loop_profiler as mod

        # Replace global profiler with a fresh enabled one
        original = mod._global_profiler
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=False, prometheus=False
        )
        mod._global_profiler = profiler
        profiler.start()

        @profile_phase("decorated_func")
        def my_func():
            time.sleep(0.001)
            return 42

        profiler.begin_iteration()
        result = my_func()
        profiler.end_iteration()

        profiler.stop()
        assert result == 42
        report = profiler.get_report()
        assert "decorated_func" in report["phase_statistics"]

        # Restore original
        mod._global_profiler = original


# ============================================================================
# CPUProfiler Tests
# ============================================================================


class TestCPUProfiler:
    """Tests for CPU profiler."""

    def test_disabled_cpu_profiler(self):
        """Disabled profiler should be a no-op."""
        profiler = CPUProfiler(enabled=False)
        profiler.start()
        result = profiler.stop()
        assert result is None
        assert profiler.get_stats() is None

    def test_enabled_cpu_profiler(self):
        """Enabled profiler should collect stats."""
        profiler = CPUProfiler(enabled=True)
        profiler.start()
        # Do some work
        sum(range(10000))
        stats = profiler.get_stats()
        assert stats is not None
        assert "total_calls" in stats

    def test_dump_stats(self):
        """dump_stats should write profile file."""
        profiler = CPUProfiler(enabled=True)
        profiler.start()
        sum(range(10000))

        with tempfile.NamedTemporaryFile(suffix=".prof", delete=False) as f:
            output_path = f.name

        try:
            profiler.stop(output_path=output_path)
            assert os.path.exists(output_path)
        finally:
            os.unlink(output_path)


# ============================================================================
# BenchmarkResult Tests
# ============================================================================


class TestBenchmarkResult:
    """Tests for BenchmarkResult data class."""

    def test_empty_result(self):
        """Empty result should have zero defaults."""
        result = BenchmarkResult(name="test", backend="sqlite", iterations=0)
        assert result.min_ms == 0.0
        assert result.max_ms == 0.0
        assert result.mean_ms == 0.0
        assert result.ops_per_second == 0.0

    def test_with_samples(self):
        """Result with samples should compute statistics."""
        result = BenchmarkResult(name="test", backend="sqlite", iterations=5)
        result.times_ms = [1.0, 2.0, 3.0, 4.0, 5.0]

        assert result.min_ms == 1.0
        assert result.max_ms == 5.0
        assert result.mean_ms == 3.0
        assert result.median_ms == 3.0
        assert result.ops_per_second == pytest.approx(333.33, abs=1)

    def test_to_dict(self):
        """to_dict should return serializable dictionary."""
        result = BenchmarkResult(name="test", backend="sqlite", iterations=2)
        result.times_ms = [1.0, 2.0]
        d = result.to_dict()
        assert d["name"] == "test"
        assert d["backend"] == "sqlite"
        assert "mean_ms" in d
        assert "ops_per_second" in d


# ============================================================================
# DatabaseBenchmark Tests
# ============================================================================


class TestDatabaseBenchmark:
    """Tests for database benchmark suite."""

    @pytest.fixture
    def db_bench(self):
        """Create an in-memory database benchmark."""
        bench = DatabaseBenchmark(db_path=":memory:")
        bench.setup()
        yield bench
        bench.teardown()

    def test_setup_creates_tables(self, db_bench):
        """setup() should create test tables with data."""
        cursor = db_bench._conn.execute("SELECT COUNT(*) FROM trades")
        assert cursor.fetchone()[0] == 10000

        cursor = db_bench._conn.execute("SELECT COUNT(*) FROM candles")
        assert cursor.fetchone()[0] == 50000

        cursor = db_bench._conn.execute("SELECT COUNT(*) FROM positions")
        assert cursor.fetchone()[0] == 100

    def test_benchmark_select_trades(self, db_bench):
        """select_trades benchmark should complete."""
        result = db_bench.benchmark_select_trades(iterations=10, warmup=2)
        assert result.name == "select_trades"
        assert result.iterations == 10
        assert len(result.times_ms) == 10
        assert result.mean_ms > 0

    def test_benchmark_select_candles(self, db_bench):
        """select_candles benchmark should complete."""
        result = db_bench.benchmark_select_candles(iterations=10, warmup=2)
        assert result.name == "select_candles"
        assert len(result.times_ms) == 10

    def test_benchmark_insert_trade(self, db_bench):
        """insert_trade benchmark should complete."""
        result = db_bench.benchmark_insert_trade(iterations=10, warmup=2)
        assert result.name == "insert_trade"
        assert len(result.times_ms) == 10

    def test_benchmark_aggregation(self, db_bench):
        """aggregation benchmark should complete."""
        result = db_bench.benchmark_aggregation(iterations=10, warmup=2)
        assert result.name == "aggregation_pnl"
        assert len(result.times_ms) == 10

    def test_benchmark_connection_overhead(self, db_bench):
        """connection_overhead benchmark should complete."""
        result = db_bench.benchmark_connection_overhead(iterations=10, warmup=2)
        assert result.name == "connection_overhead"
        assert len(result.times_ms) == 10

    def test_run_all(self):
        """run_all() should return all benchmark results."""
        bench = DatabaseBenchmark(db_path=":memory:")
        results = bench.run_all(iterations=5)
        assert len(results) == 5
        names = [r.name for r in results]
        assert "select_trades" in names
        assert "select_candles" in names
        assert "insert_trade" in names
        assert "aggregation_pnl" in names
        assert "connection_overhead" in names


# ============================================================================
# SignalBenchmark Tests
# ============================================================================


class TestSignalBenchmark:
    """Tests for signal generation benchmarks."""

    def test_benchmark_rsi(self):
        """RSI benchmark should complete."""
        bench = SignalBenchmark()
        result = bench.benchmark_rsi(iterations=50, warmup=5)
        assert result.name == "calc_rsi"
        assert len(result.times_ms) == 50
        assert result.mean_ms > 0

    def test_benchmark_adx(self):
        """ADX benchmark should complete."""
        bench = SignalBenchmark()
        result = bench.benchmark_adx(iterations=50, warmup=5)
        assert result.name == "calc_adx"
        assert len(result.times_ms) == 50

    def test_benchmark_bollinger(self):
        """Bollinger benchmark should complete."""
        bench = SignalBenchmark()
        result = bench.benchmark_bollinger_bands(iterations=50, warmup=5)
        assert result.name == "calc_bollinger"
        assert len(result.times_ms) == 50

    def test_run_all(self):
        """run_all() should return all signal benchmarks."""
        bench = SignalBenchmark()
        results = bench.run_all(iterations=20)
        assert len(results) == 3
        names = [r.name for r in results]
        assert "calc_rsi" in names
        assert "calc_adx" in names
        assert "calc_bollinger" in names


# ============================================================================
# PerformanceBenchmark Tests
# ============================================================================


class TestPerformanceBenchmark:
    """Tests for the main PerformanceBenchmark suite."""

    def test_run_all(self):
        """run_all() should execute all benchmarks."""
        bench = PerformanceBenchmark()
        results = bench.run_all(iterations=10)
        assert len(results) > 0
        # Should have DB + signal results
        backends = set(r.backend for r in results)
        assert "sqlite" in backends or "cpu" in backends

    def test_get_report(self):
        """get_report() should return structured report."""
        bench = PerformanceBenchmark()
        bench.run_all(iterations=5)
        report = bench.get_report()

        assert "timestamp" in report
        assert "summary" in report
        assert "results" in report
        assert report["summary"]["total_benchmarks"] > 0

    def test_save_report_json(self):
        """save_report() should write JSON file."""
        bench = PerformanceBenchmark()
        bench.run_all(iterations=5)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        try:
            bench.save_report(output_path=output_path, format="json")
            assert os.path.exists(output_path)

            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "results" in data
        finally:
            os.unlink(output_path)

    def test_save_report_text(self):
        """save_report() should write text file."""
        bench = PerformanceBenchmark()
        bench.run_all(iterations=5)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            output_path = f.name

        try:
            bench.save_report(output_path=output_path, format="text")
            assert os.path.exists(output_path)

            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "PERFORMANCE BENCHMARK REPORT" in content
        finally:
            os.unlink(output_path)

    def test_compare_backends(self):
        """compare_backends() should return backend comparison."""
        bench = PerformanceBenchmark()
        comparison = bench.compare_backends(iterations=5)

        assert "sqlite" in comparison
        assert len(comparison["sqlite"]) > 0

    @pytest.mark.perf
    def test_check_regression_end_to_end(self):
        """Wire run_all -> save_report -> check_regression on real timings.

        Opt-in (``-m perf``) because it measures the machine. Note it can
        only ever fail on noise: both sides run identical code in the same
        process, so the true change is 0% by construction. It is kept as a
        smoke test of the pipeline, not as a regression detector -- the
        tests in TestCheckRegression are what actually pin the detection
        behaviour.
        """
        bench = PerformanceBenchmark()
        bench.run_all(iterations=100)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            baseline_path = f.name

        try:
            bench.save_report(output_path=baseline_path, format="json")

            bench2 = PerformanceBenchmark()
            bench2.run_all(iterations=100)
            regressions = bench2.check_regression(baseline_path, threshold_pct=50.0)
            assert regressions == [], (
                "same-code comparison reported a regression; the machine was "
                f"too noisy to measure on: {regressions}"
            )
        finally:
            os.unlink(baseline_path)


# ============================================================================
# Regression Detection Tests
# ============================================================================


def _fixed_result(name: str, times_ms: list) -> BenchmarkResult:
    """Build a BenchmarkResult from injected timings.

    Args:
        name: Benchmark name.
        times_ms: Per-iteration timings in milliseconds.

    Returns:
        A BenchmarkResult carrying exactly those samples.
    """
    result = BenchmarkResult(name=name, backend="sqlite", iterations=len(times_ms))
    result.times_ms = list(times_ms)
    return result


class TestCheckRegression:
    """Pins what check_regression does and does not flag.

    Timings are injected rather than measured, so these tests exercise the
    comparison itself instead of the machine's mood. The previous version
    of this test ran the real benchmarks twice in one process and asserted
    no regression appeared -- which compared identical code against
    itself, giving it no power to detect a real slowdown and leaving
    timing noise as its only possible failure mode. It failed roughly one
    full-suite run in four, naming a different benchmark each time.
    """

    @staticmethod
    def _bench_with(samples: dict) -> PerformanceBenchmark:
        """Build a benchmark whose results are the given fixed timings.

        Args:
            samples: Mapping of benchmark name to list of timings in ms.

        Returns:
            A PerformanceBenchmark preloaded with those results.
        """
        bench = PerformanceBenchmark()
        bench._results = [_fixed_result(name, times) for name, times in samples.items()]
        return bench

    @pytest.fixture
    def baseline_path(self):
        """Write a baseline report of known timings and yield its path."""
        bench = self._bench_with({"fast_op": [1.0] * 20, "slow_op": [10.0] * 20})
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        bench.save_report(output_path=path, format="json")
        try:
            yield path
        finally:
            os.unlink(path)

    def test_flags_genuine_slowdown(self, baseline_path):
        """A 2x slowdown is reported, with the right magnitude."""
        current = self._bench_with({"fast_op": [2.0] * 20, "slow_op": [10.0] * 20})
        regressions = current.check_regression(baseline_path, threshold_pct=50.0)

        assert [r["benchmark"] for r in regressions] == ["fast_op"]
        assert regressions[0]["change_pct"] == pytest.approx(100.0)
        assert regressions[0]["statistic"] == "median"

    def test_detects_slowdown_just_over_threshold(self, baseline_path):
        """An 11% slowdown is caught at a 10% threshold."""
        current = self._bench_with({"fast_op": [1.11] * 20, "slow_op": [10.0] * 20})
        regressions = current.check_regression(baseline_path, threshold_pct=10.0)

        assert [r["benchmark"] for r in regressions] == ["fast_op"]

    def test_ignores_change_below_threshold(self, baseline_path):
        """A 5% drift is not reported at a 10% threshold."""
        current = self._bench_with({"fast_op": [1.05] * 20, "slow_op": [10.0] * 20})

        assert current.check_regression(baseline_path, threshold_pct=10.0) == []

    def test_ignores_speedups(self, baseline_path):
        """Getting faster is not a regression."""
        current = self._bench_with({"fast_op": [0.2] * 20, "slow_op": [1.0] * 20})

        assert current.check_regression(baseline_path, threshold_pct=10.0) == []

    def test_median_survives_a_single_outlier(self, baseline_path):
        """One descheduled iteration must not be reported as a regression.

        This is the exact shape of the flake: 19 clean samples plus one
        that took 100x as long. The median does not move; the mean moves
        by roughly 500%, which is why the mean is no longer the default.
        """
        noisy = [1.0] * 19 + [100.0]
        current = self._bench_with({"fast_op": noisy, "slow_op": [10.0] * 20})

        assert current.check_regression(baseline_path, threshold_pct=50.0) == []
        assert current.check_regression(
            baseline_path, threshold_pct=50.0, statistic="mean"
        ), "the mean-based comparison should have been fooled by the outlier"

    def test_benchmarks_absent_from_baseline_are_skipped(self, baseline_path):
        """A newly added benchmark has nothing to compare against."""
        current = self._bench_with({"brand_new_op": [999.0] * 20})

        assert current.check_regression(baseline_path, threshold_pct=10.0) == []

    def test_rejects_unknown_statistic(self, baseline_path):
        """An unsupported statistic is a programming error, not a default."""
        current = self._bench_with({"fast_op": [1.0] * 20})

        with pytest.raises(ValueError, match="median.*mean"):
            current.check_regression(baseline_path, statistic="p95")


# ============================================================================
# Memory Tracking Tests
# ============================================================================


class TestMemoryTracking:
    """Tests for memory usage tracking."""

    def test_get_memory_bytes(self):
        """_get_memory_bytes() should return positive value."""
        mem = _get_memory_bytes()
        assert mem >= 0

    def test_memory_tracking_in_profiler(self):
        """Profiler should track memory when enabled."""
        profiler = TradingLoopProfiler(
            enabled=True, memory_tracking=True, prometheus=False
        )
        profiler.start()

        profiler.begin_iteration()
        # Allocate some memory
        data = [0] * 100000
        profiler.end_iteration()

        profiler.stop()
        report = profiler.get_report()
        # Memory data should be present (may be 0 if psutil unavailable)
        assert "summary" in report
        del data
