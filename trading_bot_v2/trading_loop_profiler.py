"""
Trading Loop Profiler for Bot 3.

Profiles trading loop execution time, identifies bottlenecks in signal
generation, validation, and execution phases. Tracks latency percentiles
(p50, p95, p99), memory usage, and CPU profiling for optimization.

Usage:
    # As a context manager wrapping the trading loop
    from trading_bot_v2.trading_loop_profiler import TradingLoopProfiler

    profiler = TradingLoopProfiler()
    profiler.start()

    # In trading loop
    with profiler.phase("update_positions"):
        bot._update_positions()

    with profiler.phase("generate_signals"):
        bot._generate_and_publish_signals()

    profiler.stop()
    report = profiler.get_report()

    # CLI usage
    python -m trading_bot_v2.trading_loop_profiler --iterations 100 --output report.json

Environment Variables:
    PROFILER_ENABLED          = "true" | "false" (default: "false")
    PROFILER_MEMORY_TRACKING  = "true" | "false" (default: "true")
    PROFILER_CPU_PROFILING    = "true" | "false" (default: "false")
    PROFILER_OUTPUT_DIR       = str (default: "profiler_output")
    PROFILER_PROMETHEUS       = "true" | "false" (default: "true")
"""

import json
import logging
import math
import os
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

PROFILER_ENABLED: bool = os.getenv("PROFILER_ENABLED", "false").lower() in (
    "true",
    "1",
    "yes",
)
MEMORY_TRACKING: bool = os.getenv("PROFILER_MEMORY_TRACKING", "true").lower() in (
    "true",
    "1",
    "yes",
)
CPU_PROFILING: bool = os.getenv("PROFILER_CPU_PROFILING", "false").lower() in (
    "true",
    "1",
    "yes",
)
OUTPUT_DIR: str = os.getenv("PROFILER_OUTPUT_DIR", "profiler_output")
PROMETHEUS_ENABLED: bool = os.getenv("PROFILER_PROMETHEUS", "true").lower() in (
    "true",
    "1",
    "yes",
)

# Trading loop phases in execution order
TRADING_LOOP_PHASES: List[str] = [
    "update_positions",
    "monitor_grids",
    "manage_migrated_positions",
    "generate_signals",
    "monitor_risk",
    "cleanup_approvals",
    "websocket_maintenance",
]


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class PhaseTiming:
    """Timing data for a single phase measurement."""

    phase: str
    start_time: float
    end_time: float = 0.0
    duration_ms: float = 0.0
    memory_before_bytes: int = 0
    memory_after_bytes: int = 0
    memory_delta_bytes: int = 0
    iteration: int = 0

    def finalize(self) -> None:
        """Compute derived fields."""
        self.duration_ms = (self.end_time - self.start_time) * 1000
        self.memory_delta_bytes = self.memory_after_bytes - self.memory_before_bytes


@dataclass
class IterationTiming:
    """Timing data for a complete trading loop iteration."""

    iteration: int
    start_time: float
    end_time: float = 0.0
    total_duration_ms: float = 0.0
    phases: List[PhaseTiming] = field(default_factory=list)
    memory_before_bytes: int = 0
    memory_after_bytes: int = 0

    def finalize(self) -> None:
        """Compute derived fields."""
        self.total_duration_ms = (self.end_time - self.start_time) * 1000

    def get_phase_duration_ms(self, phase: str) -> float:
        """Get duration for a specific phase."""
        for p in self.phases:
            if p.phase == phase:
                return p.duration_ms
        return 0.0


@dataclass
class LatencyStats:
    """Aggregated latency statistics for a phase."""

    phase: str
    count: int = 0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    mean_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    std_dev_ms: float = 0.0
    total_ms: float = 0.0
    _values: List[float] = field(default_factory=list, repr=False)

    def add_sample(self, value_ms: float) -> None:
        """Add a timing sample."""
        self._values.append(value_ms)
        self.count += 1
        self.min_ms = min(self.min_ms, value_ms)
        self.max_ms = max(self.max_ms, value_ms)
        self.total_ms += value_ms

    def compute(self) -> None:
        """Compute percentiles and statistics from collected samples."""
        if not self._values:
            return

        sorted_vals = sorted(self._values)
        n = len(sorted_vals)

        self.mean_ms = sum(sorted_vals) / n
        self.p50_ms = self._percentile(sorted_vals, 50)
        self.p95_ms = self._percentile(sorted_vals, 95)
        self.p99_ms = self._percentile(sorted_vals, 99)

        # Standard deviation
        variance = sum((x - self.mean_ms) ** 2 for x in sorted_vals) / max(n - 1, 1)
        self.std_dev_ms = math.sqrt(variance)

    @staticmethod
    def _percentile(sorted_data: List[float], percentile: float) -> float:
        """Compute percentile from sorted data."""
        if not sorted_data:
            return 0.0
        k = (len(sorted_data) - 1) * (percentile / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        return sorted_data[int(f)] * (c - k) + sorted_data[int(c)] * (k - f)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "phase": self.phase,
            "count": self.count,
            "min_ms": round(self.min_ms, 3) if self.min_ms != float("inf") else 0,
            "max_ms": round(self.max_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "std_dev_ms": round(self.std_dev_ms, 3),
            "total_ms": round(self.total_ms, 3),
        }


# ============================================================================
# Prometheus Integration
# ============================================================================

# Cache of already-registered collector attributes, keyed by id(registry).
# Prevents "Duplicated timeseries in CollectorRegistry" when
# ProfilerPrometheusMetrics is instantiated more than once against the same
# registry (e.g. the global default REGISTRY in tests).
_PROFILER_METRICS_CACHE: Dict[int, Dict[str, Any]] = {}


class ProfilerPrometheusMetrics:
    """Prometheus metrics for the trading loop profiler.

    Collectors are cached at module level keyed by the target registry so
    that repeated instantiation (e.g. across tests) reuses the existing
    collectors instead of raising ``ValueError: Duplicated timeseries`` on
    the default ``prometheus_client`` REGISTRY.
    """

    def __init__(self, registry: Optional[Any] = None) -> None:
        """Initialize Prometheus metrics. Graceful fallback if unavailable.

        When targeting the global default ``REGISTRY`` (either because no
        registry is passed, or the registry passed in IS the default one),
        the four trading-loop collectors are reused from the
        ``trading_bot_v2.metrics`` singleton instead of being recreated,
        since that module already registers them under the same names.
        Only the profiler-specific memory gauge is created fresh in that
        case. When an explicit custom (non-default) registry is passed,
        all five collectors are created fresh against that registry, as
        before.

        Args:
            registry: Optional prometheus_client CollectorRegistry. Defaults
                to the global REGISTRY when not provided.
        """
        try:
            from prometheus_client import REGISTRY, Counter, Gauge, Histogram

            target_registry = registry if registry is not None else REGISTRY
            cached = _PROFILER_METRICS_CACHE.get(id(target_registry))
            if cached is not None:
                self.__dict__.update(cached)
                self._available = True
                return

            if target_registry is REGISTRY:
                # The trading_bot_v2.metrics singleton already registers
                # these four collectors against the default REGISTRY at
                # import time. Reuse them instead of re-registering, which
                # would raise "Duplicated timeseries in CollectorRegistry".
                from trading_bot_v2.metrics import metrics as metrics_singleton

                self.loop_duration = metrics_singleton.loop_duration
                self.loop_iteration = metrics_singleton.loop_iteration
                self.phase_duration = metrics_singleton.loop_phase_duration
                self.phase_count = metrics_singleton.loop_phase_count
            else:
                self.loop_duration = Histogram(
                    "bot_trading_loop_duration_seconds",
                    "Total trading loop iteration duration",
                    buckets=[5, 10, 15, 20, 25, 30, 40, 50, 60, 90, 120],
                    registry=target_registry,
                )
                self.loop_iteration = Gauge(
                    "bot_trading_loop_iteration",
                    "Current trading loop iteration number",
                    registry=target_registry,
                )
                self.phase_duration = Histogram(
                    "bot_trading_loop_phase_duration_seconds",
                    "Duration of individual trading loop phases",
                    ["phase"],
                    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
                    registry=target_registry,
                )
                self.phase_count = Counter(
                    "bot_trading_loop_phase_count_total",
                    "Total phase executions",
                    ["phase"],
                    registry=target_registry,
                )

            self.memory_usage = Gauge(
                "bot_profiler_memory_bytes",
                "Process memory usage during profiling",
                ["measurement"],
                registry=target_registry,
            )
            self._available = True

            _PROFILER_METRICS_CACHE[id(target_registry)] = {
                "loop_duration": self.loop_duration,
                "loop_iteration": self.loop_iteration,
                "phase_duration": self.phase_duration,
                "phase_count": self.phase_count,
                "memory_usage": self.memory_usage,
            }
        except ImportError:
            logger.debug("prometheus_client not available, profiler metrics disabled")
            self._available = False

    def record_loop_duration(self, duration_seconds: float) -> None:
        """Record total loop duration."""
        if self._available:
            self.loop_duration.observe(duration_seconds)

    def set_iteration(self, iteration: int) -> None:
        """Set current iteration number."""
        if self._available:
            self.loop_iteration.set(iteration)

    def record_phase_duration(self, phase: str, duration_seconds: float) -> None:
        """Record phase duration."""
        if self._available:
            self.phase_duration.labels(phase=phase).observe(duration_seconds)
            self.phase_count.labels(phase=phase).inc()

    def record_memory(self, measurement: str, bytes_value: int) -> None:
        """Record memory usage."""
        if self._available:
            self.memory_usage.labels(measurement=measurement).set(bytes_value)


# ============================================================================
# Memory Tracker
# ============================================================================


def _get_memory_bytes() -> int:
    """Get current process memory usage in bytes."""
    try:
        import psutil

        process = psutil.Process(os.getpid())
        return process.memory_info().rss
    except (ImportError, psutil.Error):
        # Fallback: try resource module (Unix only)
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF)
            return usage.ru_maxrss * 1024  # Convert KB to bytes on Linux
        except (ImportError, ValueError):
            return 0


# ============================================================================
# CPU Profiler (optional)
# ============================================================================


class CPUProfiler:
    """Optional CPU profiler using cProfile."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._profiler: Any = None

    def start(self) -> None:
        """Start CPU profiling."""
        if self._enabled:
            try:
                import cProfile

                self._profiler = cProfile.Profile()
                self._profiler.enable()
            except ImportError:
                logger.warning("cProfile not available")

    def stop(self, output_path: Optional[str] = None) -> Optional[str]:
        """Stop CPU profiling and optionally save results."""
        if self._profiler is not None:
            self._profiler.disable()
            if output_path:
                self._profiler.dump_stats(output_path)
                logger.info(f"CPU profile saved to {output_path}")
                return output_path
        return None

    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Get profiling statistics as dictionary."""
        if self._profiler is None:
            return None
        import pstats

        stats = pstats.Stats(self._profiler)
        return {
            "total_calls": stats.total_calls,
            "total_time": stats.total_tt,
            "top_functions": sorted(
                stats.stats.items(), key=lambda x: x[1][2], reverse=True
            )[:20],
        }


# ============================================================================
# Main Profiler
# ============================================================================


class TradingLoopProfiler:
    """
    Profiles the trading loop execution to identify bottlenecks and
    track performance over time.

    Features:
      - Phase-level timing with context manager
      - Latency percentile tracking (p50, p95, p99)
      - Memory usage delta per phase
      - Optional CPU profiling
      - Prometheus metrics export
      - JSON report generation

    Example:
        profiler = TradingLoopProfiler()
        profiler.start()

        for i in range(100):
            profiler.begin_iteration()
            with profiler.phase("update_positions"):
                time.sleep(0.01)  # simulate work
            with profiler.phase("generate_signals"):
                time.sleep(0.02)  # simulate work
            profiler.end_iteration()

        profiler.stop()
        report = profiler.get_report()
    """

    def __init__(
        self,
        enabled: Optional[bool] = None,
        memory_tracking: Optional[bool] = None,
        cpu_profiling: Optional[bool] = None,
        prometheus: Optional[bool] = None,
    ) -> None:
        """
        Initialize the profiler.

        Args:
            enabled: Override PROFILER_ENABLED env var
            memory_tracking: Override PROFILER_MEMORY_TRACKING env var
            cpu_profiling: Override PROFILER_CPU_PROFILING env var
            prometheus: Override PROFILER_PROMETHEUS env var
        """
        self._enabled = enabled if enabled is not None else PROFILER_ENABLED
        self._memory_tracking = (
            memory_tracking if memory_tracking is not None else MEMORY_TRACKING
        )
        self._prometheus_enabled = (
            prometheus if prometheus is not None else PROMETHEUS_ENABLED
        )

        # State
        self._iterations: List[IterationTiming] = []
        self._current_iteration: Optional[IterationTiming] = None
        self._current_phase: Optional[PhaseTiming] = None
        self._phase_stats: Dict[str, LatencyStats] = {}
        self._start_time: Optional[float] = None
        self._lock = threading.Lock()

        # CPU profiler
        self._cpu_profiler = CPUProfiler(
            enabled=cpu_profiling if cpu_profiling is not None else CPU_PROFILING
        )

        # Prometheus metrics
        self._prometheus: Optional[ProfilerPrometheusMetrics] = None
        if self._prometheus_enabled:
            self._prometheus = ProfilerPrometheusMetrics()

        # Initialize phase stats
        for phase in TRADING_LOOP_PHASES:
            self._phase_stats[phase] = LatencyStats(phase=phase)

    @property
    def enabled(self) -> bool:
        """Check if profiler is enabled."""
        return self._enabled

    def enable(self) -> None:
        """Enable profiling."""
        self._enabled = True

    def disable(self) -> None:
        """Disable profiling."""
        self._enabled = False

    def start(self) -> None:
        """Start profiling session."""
        self._start_time = time.monotonic()
        self._iterations.clear()
        for stats in self._phase_stats.values():
            stats.count = 0
            stats.min_ms = float("inf")
            stats.max_ms = 0.0
            stats.total_ms = 0.0
            stats._values.clear()
        self._cpu_profiler.start()
        logger.info("Trading loop profiler started")

    def stop(self) -> None:
        """Stop profiling session."""
        self._cpu_profiler.stop()
        # Compute final statistics
        for stats in self._phase_stats.values():
            stats.compute()
        logger.info("Trading loop profiler stopped")

    def begin_iteration(self) -> None:
        """Mark the start of a new trading loop iteration."""
        if not self._enabled:
            return

        with self._lock:
            # Finalize previous iteration if any
            if self._current_iteration is not None:
                self._current_iteration.memory_after_bytes = _get_memory_bytes()
                self._current_iteration.finalize()
                self._iterations.append(self._current_iteration)

            iteration_num = len(self._iterations) + 1
            self._current_iteration = IterationTiming(
                iteration=iteration_num,
                start_time=time.monotonic(),
                memory_before_bytes=_get_memory_bytes() if self._memory_tracking else 0,
            )

            if self._prometheus:
                self._prometheus.set_iteration(iteration_num)

    def end_iteration(self) -> None:
        """Mark the end of a trading loop iteration."""
        if not self._enabled or self._current_iteration is None:
            return

        with self._lock:
            self._current_iteration.end_time = time.monotonic()
            self._current_iteration.memory_after_bytes = (
                _get_memory_bytes() if self._memory_tracking else 0
            )
            self._current_iteration.finalize()

            # Record Prometheus metrics
            if self._prometheus:
                self._prometheus.record_loop_duration(
                    self._current_iteration.total_duration_ms / 1000
                )

            self._iterations.append(self._current_iteration)
            self._current_iteration = None

    @contextmanager
    def phase(self, phase_name: str) -> Generator[None, None, None]:
        """
        Context manager to profile a trading loop phase.

        Args:
            phase_name: Name of the phase being profiled

        Yields:
            None
        """
        if not self._enabled:
            yield
            return

        timing = PhaseTiming(
            phase=phase_name,
            start_time=time.monotonic(),
            memory_before_bytes=_get_memory_bytes() if self._memory_tracking else 0,
            iteration=len(self._iterations) + 1,
        )

        try:
            yield
        finally:
            timing.end_time = time.monotonic()
            timing.memory_after_bytes = (
                _get_memory_bytes() if self._memory_tracking else 0
            )
            timing.finalize()

            with self._lock:
                # Add to current iteration
                if self._current_iteration is not None:
                    self._current_iteration.phases.append(timing)

                # Update phase statistics
                if phase_name not in self._phase_stats:
                    self._phase_stats[phase_name] = LatencyStats(phase=phase_name)
                self._phase_stats[phase_name].add_sample(timing.duration_ms)

                # Record Prometheus metrics
                if self._prometheus:
                    self._prometheus.record_phase_duration(
                        phase_name, timing.duration_ms / 1000
                    )
                    if self._memory_tracking:
                        self._prometheus.record_memory(
                            f"phase_{phase_name}_before",
                            timing.memory_before_bytes,
                        )
                        self._prometheus.record_memory(
                            f"phase_{phase_name}_delta",
                            timing.memory_delta_bytes,
                        )

    def record_custom_metric(self, name: str, value: float) -> None:
        """Record a custom metric during profiling."""
        if not self._enabled:
            return

        with self._lock:
            if name not in self._phase_stats:
                self._phase_stats[name] = LatencyStats(phase=name)
            self._phase_stats[name].add_sample(value)

    def get_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive profiling report.

        Returns:
            Dictionary containing profiling statistics and analysis.
        """
        # Ensure final iteration is included
        if self._current_iteration is not None:
            self._current_iteration.end_time = time.monotonic()
            self._current_iteration.memory_after_bytes = (
                _get_memory_bytes() if self._memory_tracking else 0
            )
            self._current_iteration.finalize()

        # Compute phase statistics
        for stats in self._phase_stats.values():
            stats.compute()

        # Build report
        total_duration = 0.0
        if self._iterations:
            total_duration = sum(i.total_duration_ms for i in self._iterations)

        # Find bottlenecks
        bottlenecks = []
        total_phase_time = sum(
            s.total_ms for s in self._phase_stats.values() if s.count > 0
        )
        for phase_name, stats in self._phase_stats.items():
            if stats.count > 0 and total_phase_time > 0:
                pct = (stats.total_ms / total_phase_time) * 100
                if pct > 15.0:  # Phases taking > 15% of total time
                    bottlenecks.append(
                        {
                            "phase": phase_name,
                            "percentage": round(pct, 1),
                            "mean_ms": round(stats.mean_ms, 3),
                            "p95_ms": round(stats.p95_ms, 3),
                        }
                    )
        bottlenecks.sort(key=lambda x: x["percentage"], reverse=True)

        report: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_iterations": len(self._iterations),
                "total_duration_ms": round(total_duration, 3),
                "avg_iteration_ms": (
                    round(total_duration / len(self._iterations), 3)
                    if self._iterations
                    else 0
                ),
                "enabled": self._enabled,
                "memory_tracking": self._memory_tracking,
            },
            "phase_statistics": {
                name: stats.to_dict()
                for name, stats in self._phase_stats.items()
                if stats.count > 0
            },
            "bottlenecks": bottlenecks,
            "iteration_summary": {
                "min_ms": (
                    round(min(i.total_duration_ms for i in self._iterations), 3)
                    if self._iterations
                    else 0
                ),
                "max_ms": (
                    round(max(i.total_duration_ms for i in self._iterations), 3)
                    if self._iterations
                    else 0
                ),
            },
        }

        # Add CPU profiling stats
        cpu_stats = self._cpu_profiler.get_stats()
        if cpu_stats:
            report["cpu_profile"] = {
                "total_calls": cpu_stats["total_calls"],
                "total_time_seconds": round(cpu_stats["total_time"], 4),
                "top_functions": [
                    {
                        "function": f"{mod}:{func}",
                        "calls": info[0],
                        "total_time": round(info[2], 6),
                        "cumulative_time": round(info[3], 6),
                    }
                    for (mod, func, *_) , info in cpu_stats["top_functions"]
                ],
            }

        return report

    def save_report(
        self,
        output_path: Optional[str] = None,
        format: str = "json",
    ) -> str:
        """
        Save profiling report to file.

        Args:
            output_path: Output file path. Auto-generated if None.
            format: Output format ("json" or "text").

        Returns:
            Path to saved report file.
        """
        report = self.get_report()

        if output_path is None:
            output_dir = Path(OUTPUT_DIR)
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(output_dir / f"profile_report_{timestamp}.json")

        if format == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
        else:
            # Text format
            lines = [
                "=" * 70,
                "TRADING LOOP PROFILER REPORT",
                "=" * 70,
                f"Timestamp: {report['timestamp']}",
                f"Iterations: {report['summary']['total_iterations']}",
                f"Total Duration: {report['summary']['total_duration_ms']:.1f} ms",
                f"Avg Iteration: {report['summary']['avg_iteration_ms']:.1f} ms",
                "",
                "PHASE STATISTICS:",
                "-" * 70,
                f"{'Phase':<30} {'Count':>6} {'Mean':>10} {'P50':>10} {'P95':>10} {'P99':>10}",
                "-" * 70,
            ]
            for name, stats in report["phase_statistics"].items():
                lines.append(
                    f"{name:<30} {stats['count']:>6} "
                    f"{stats['mean_ms']:>10.1f} {stats['p50_ms']:>10.1f} "
                    f"{stats['p95_ms']:>10.1f} {stats['p99_ms']:>10.1f}"
                )
            lines.append("")

            if report["bottlenecks"]:
                lines.append("BOTTLENECKS (>15% of total time):")
                lines.append("-" * 70)
                for b in report["bottlenecks"]:
                    lines.append(
                        f"  {b['phase']}: {b['percentage']}% "
                        f"(mean={b['mean_ms']:.1f}ms, p95={b['p95_ms']:.1f}ms)"
                    )
                lines.append("")

            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

        logger.info(f"Profiler report saved to {output_path}")
        return output_path

    def get_text_summary(self) -> str:
        """Get a human-readable text summary of profiling results."""
        report = self.get_report()
        lines = [
            f"Trading Loop Profiler: {report['summary']['total_iterations']} iterations",
            f"Avg: {report['summary']['avg_iteration_ms']:.1f}ms | "
            f"Min: {report['iteration_summary']['min_ms']:.1f}ms | "
            f"Max: {report['iteration_summary']['max_ms']:.1f}ms",
        ]
        for name, stats in report["phase_statistics"].items():
            lines.append(
                f"  {name}: mean={stats['mean_ms']:.1f}ms p95={stats['p95_ms']:.1f}ms"
            )
        if report["bottlenecks"]:
            lines.append(
                f"Bottleneck: {report['bottlenecks'][0]['phase']} "
                f"({report['bottlenecks'][0]['percentage']}%)"
            )
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all profiling data."""
        with self._lock:
            self._iterations.clear()
            self._current_iteration = None
            self._current_phase = None
            for stats in self._phase_stats.values():
                stats.count = 0
                stats.min_ms = float("inf")
                stats.max_ms = 0.0
                stats.total_ms = 0.0
                stats._values.clear()
            logger.info("Profiler data reset")


# ============================================================================
# Global Profiler Instance
# ============================================================================

_global_profiler: Optional[TradingLoopProfiler] = None


def get_profiler() -> TradingLoopProfiler:
    """Get the global profiler instance."""
    global _global_profiler
    if _global_profiler is None:
        _global_profiler = TradingLoopProfiler()
    return _global_profiler


# ============================================================================
# Decorator
# ============================================================================


def profile_phase(phase_name: str) -> Callable:
    """
    Decorator to profile a function as a trading loop phase.

    Usage:
        @profile_phase("my_operation")
        def my_operation():
            ...
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            profiler = get_profiler()
            with profiler.phase(phase_name):
                return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


# ============================================================================
# CLI Entry Point
# ============================================================================


def _cli_main() -> None:
    """Command-line interface for the profiler."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bot 3 Trading Loop Profiler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run profiler for 50 iterations
  python -m trading_bot_v2.trading_loop_profiler --iterations 50

  # Profile with CPU profiling enabled
  python -m trading_bot_v2.trading_loop_profiler --iterations 100 --cpu

  # Save report to specific file
  python -m trading_bot_v2.trading_loop_profiler --iterations 20 --output report.json

  # Profile a specific number of iterations with memory tracking
  python -m trading_bot_v2.trading_loop_profiler --iterations 500 --memory
        """,
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Number of trading loop iterations to profile (default: 100)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Simulated trading loop interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path for report (default: auto-generated)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Enable CPU profiling",
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        default=True,
        help="Enable memory tracking (default: True)",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Disable memory tracking",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print(f"Trading Loop Profiler - {args.iterations} iterations")
    print(f"CPU Profiling: {'enabled' if args.cpu else 'disabled'}")
    print(f"Memory Tracking: {'disabled' if args.no_memory else 'enabled'}")
    print()

    profiler = TradingLoopProfiler(
        enabled=True,
        memory_tracking=not args.no_memory,
        cpu_profiling=args.cpu,
    )
    profiler.start()

    for i in range(args.iterations):
        profiler.begin_iteration()

        # Simulate trading loop phases with realistic timing
        with profiler.phase("update_positions"):
            time.sleep(0.05)  # 50ms

        with profiler.phase("monitor_grids"):
            time.sleep(0.03)  # 30ms

        with profiler.phase("manage_migrated_positions"):
            time.sleep(0.01)  # 10ms

        with profiler.phase("generate_signals"):
            time.sleep(0.15)  # 150ms

        with profiler.phase("monitor_risk"):
            time.sleep(0.02)  # 20ms

        with profiler.phase("cleanup_approvals"):
            time.sleep(0.005)  # 5ms

        profiler.end_iteration()

        if (i + 1) % 10 == 0:
            print(f"  Completed {i + 1}/{args.iterations} iterations...")

    profiler.stop()

    # Print summary
    print()
    print(profiler.get_text_summary())
    print()

    # Save report
    output_path = profiler.save_report(
        output_path=args.output,
        format="json" if args.json else "text",
    )
    print(f"Full report saved to: {output_path}")


if __name__ == "__main__":
    _cli_main()
