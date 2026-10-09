"""
Performance Benchmarking Suite for Bot 3.

Benchmarks critical operations (database queries, API calls, signal generation),
compares SQLite vs PostgreSQL performance, tracks regressions, and generates
performance reports.

Usage:
    from trading_bot_v2.performance_benchmark import PerformanceBenchmark

    bench = PerformanceBenchmark()
    bench.run_all()

    # Benchmark specific operation
    bench.benchmark_db_query("SELECT * FROM trades WHERE symbol = ?", ("BTC-USDC",))

    # Compare backends
    bench.compare_backends()

    # Generate report
    bench.save_report("benchmark_report.json")

    # CLI usage
    python -m trading_bot_v2.performance_benchmark --backend both --iterations 50

Environment Variables:
    BENCHMARK_ITERATIONS    = int (default: 100)
    BENCHMARK_WARMUP        = int (default: 5)
    BENCHMARK_OUTPUT_DIR    = str (default: "profiler_output")
"""

import json
import logging
import math
import os
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

BENCHMARK_ITERATIONS: int = int(os.getenv("BENCHMARK_ITERATIONS", "100"))
BENCHMARK_WARMUP: int = int(os.getenv("BENCHMARK_WARMUP", "5"))
BENCHMARK_OUTPUT_DIR: str = os.getenv("BENCHMARK_OUTPUT_DIR", "profiler_output")


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""

    name: str
    backend: str
    iterations: int
    times_ms: List[float] = field(default_factory=list)
    errors: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def min_ms(self) -> float:
        """Minimum execution time."""
        return min(self.times_ms) if self.times_ms else 0.0

    @property
    def max_ms(self) -> float:
        """Maximum execution time."""
        return max(self.times_ms) if self.times_ms else 0.0

    @property
    def mean_ms(self) -> float:
        """Mean execution time."""
        return statistics.mean(self.times_ms) if self.times_ms else 0.0

    @property
    def median_ms(self) -> float:
        """Median execution time (p50)."""
        return statistics.median(self.times_ms) if self.times_ms else 0.0

    @property
    def p95_ms(self) -> float:
        """95th percentile execution time."""
        if not self.times_ms:
            return 0.0
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99_ms(self) -> float:
        """99th percentile execution time."""
        if not self.times_ms:
            return 0.0
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def std_dev_ms(self) -> float:
        """Standard deviation."""
        if len(self.times_ms) < 2:
            return 0.0
        return statistics.stdev(self.times_ms)

    @property
    def ops_per_second(self) -> float:
        """Operations per second."""
        if self.mean_ms <= 0:
            return 0.0
        return 1000.0 / self.mean_ms

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "backend": self.backend,
            "iterations": self.iterations,
            "errors": self.errors,
            "min_ms": round(self.min_ms, 4),
            "max_ms": round(self.max_ms, 4),
            "mean_ms": round(self.mean_ms, 4),
            "median_ms": round(self.median_ms, 4),
            "p95_ms": round(self.p95_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "std_dev_ms": round(self.std_dev_ms, 4),
            "ops_per_second": round(self.ops_per_second, 2),
            "metadata": self.metadata,
        }


# ============================================================================
# Database Benchmarks
# ============================================================================


class DatabaseBenchmark:
    """Benchmarks for database operations."""

    def __init__(self, db_path: str = ":memory:") -> None:
        """
        Initialize database benchmark.

        Args:
            db_path: SQLite database path (default: in-memory)
        """
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def setup(self) -> None:
        """Set up benchmark database with test data."""
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row

        # Create test tables
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                pnl REAL,
                strategy TEXT,
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                timestamp TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL,
                unrealized_pnl REAL,
                leverage REAL DEFAULT 1.0,
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf ON candles(symbol, timeframe);
            CREATE INDEX IF NOT EXISTS idx_candles_ts ON candles(timestamp);
        """
        )

        # Insert test data
        symbols = ["BTC-USDC", "ETH-USDC", "SOL-USDC", "SUI-USDC", "XRP-USDC"]
        strategies = [
            "mean_reversion",
            "ma_crossover",
            "grid_trading",
            "momentum_scalping",
        ]

        # Insert 10,000 trades
        trades_data = []
        for i in range(10000):
            symbol = symbols[i % len(symbols)]
            side = "long" if i % 2 == 0 else "short"
            quantity = 0.1 + (i % 10) * 0.05
            entry_price = 50000 + (i % 1000) * 10
            exit_price = entry_price + (i % 100 - 50) * 5
            pnl = (
                (exit_price - entry_price) * quantity
                if side == "long"
                else (entry_price - exit_price) * quantity
            )
            strategy = strategies[i % len(strategies)]
            trades_data.append(
                (symbol, side, quantity, entry_price, exit_price, pnl, strategy)
            )

        self._conn.executemany(
            "INSERT INTO trades (symbol, side, quantity, entry_price, exit_price, pnl, strategy) VALUES (?, ?, ?, ?, ?, ?, ?)",
            trades_data,
        )

        # Insert 50,000 candles
        candle_data = []
        base_ts = 1700000000
        for i in range(50000):
            symbol = symbols[i % len(symbols)]
            tf = "1m"
            price = 50000 + (i % 1000) * 10
            candle_data.append(
                (
                    symbol,
                    tf,
                    price,
                    price * 1.01,
                    price * 0.99,
                    price,
                    1000 + i % 500,
                    base_ts + i * 60,
                )
            )

        self._conn.executemany(
            "INSERT INTO candles (symbol, timeframe, open, high, low, close, volume, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            candle_data,
        )

        # Insert 100 positions
        pos_data = []
        for i in range(100):
            symbol = symbols[i % len(symbols)]
            side = "long" if i % 2 == 0 else "short"
            quantity = 0.5 + (i % 10) * 0.1
            entry_price = 50000 + (i % 100) * 10
            pos_data.append(
                (
                    f"pos_{i}",
                    symbol,
                    side,
                    quantity,
                    entry_price,
                    entry_price * 1.01,
                    0,
                    1.0,
                )
            )

        self._conn.executemany(
            "INSERT INTO positions (position_id, symbol, side, quantity, entry_price, current_price, unrealized_pnl, leverage) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            pos_data,
        )

        self._conn.commit()

    def teardown(self) -> None:
        """Clean up benchmark database."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def benchmark_select_trades(
        self, iterations: int = 100, warmup: int = 5
    ) -> BenchmarkResult:
        """Benchmark SELECT queries on trades table."""
        result = BenchmarkResult(
            name="select_trades",
            backend="sqlite",
            iterations=iterations,
        )
        conn = cast(sqlite3.Connection, self._conn)

        # Warmup
        for _ in range(warmup):
            conn.execute(
                "SELECT * FROM trades WHERE symbol = ?", ("BTC-USDC",)
            ).fetchall()

        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            conn.execute(
                "SELECT * FROM trades WHERE symbol = ?", ("BTC-USDC",)
            ).fetchall()
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["query"] = "SELECT * FROM trades WHERE symbol = ?"
        result.metadata["result_rows"] = 2000
        return result

    def benchmark_select_candles(
        self, iterations: int = 100, warmup: int = 5
    ) -> BenchmarkResult:
        """Benchmark SELECT queries on candles table."""
        result = BenchmarkResult(
            name="select_candles",
            backend="sqlite",
            iterations=iterations,
        )
        conn = cast(sqlite3.Connection, self._conn)

        # Warmup
        for _ in range(warmup):
            conn.execute(
                "SELECT * FROM candles WHERE symbol = ? AND timeframe = ? ORDER BY timestamp DESC LIMIT 100",
                ("BTC-USDC", "1m"),
            ).fetchall()

        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            conn.execute(
                "SELECT * FROM candles WHERE symbol = ? AND timeframe = ? ORDER BY timestamp DESC LIMIT 100",
                ("BTC-USDC", "1m"),
            ).fetchall()
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["query"] = "SELECT candles WHERE symbol=? AND tf=? LIMIT 100"
        result.metadata["result_rows"] = 100
        return result

    def benchmark_insert_trade(
        self, iterations: int = 100, warmup: int = 5
    ) -> BenchmarkResult:
        """Benchmark INSERT operations on trades table."""
        result = BenchmarkResult(
            name="insert_trade",
            backend="sqlite",
            iterations=iterations,
        )
        conn = cast(sqlite3.Connection, self._conn)

        # Warmup
        for _ in range(warmup):
            conn.execute(
                "INSERT INTO trades (symbol, side, quantity, entry_price, strategy) VALUES (?, ?, ?, ?, ?)",
                ("BTC-USDC", "long", 0.1, 50000.0, "test"),
            )
            conn.commit()

        # Benchmark
        for i in range(iterations):
            start = time.perf_counter()
            conn.execute(
                "INSERT INTO trades (symbol, side, quantity, entry_price, strategy) VALUES (?, ?, ?, ?, ?)",
                ("BTC-USDC", "long", 0.1, 50000.0 + i, "test"),
            )
            conn.commit()
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["operation"] = "INSERT + COMMIT"
        return result

    def benchmark_aggregation(
        self, iterations: int = 100, warmup: int = 5
    ) -> BenchmarkResult:
        """Benchmark aggregation queries."""
        result = BenchmarkResult(
            name="aggregation_pnl",
            backend="sqlite",
            iterations=iterations,
        )
        conn = cast(sqlite3.Connection, self._conn)

        # Warmup
        for _ in range(warmup):
            conn.execute(
                "SELECT symbol, SUM(pnl) as total_pnl, COUNT(*) as trade_count FROM trades GROUP BY symbol"
            ).fetchall()

        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            conn.execute(
                "SELECT symbol, SUM(pnl) as total_pnl, COUNT(*) as trade_count FROM trades GROUP BY symbol"
            ).fetchall()
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["query"] = "GROUP BY aggregation"
        result.metadata["result_rows"] = 5
        return result

    def benchmark_connection_overhead(
        self, iterations: int = 50, warmup: int = 3
    ) -> BenchmarkResult:
        """Benchmark connection creation/close overhead."""
        result = BenchmarkResult(
            name="connection_overhead",
            backend="sqlite",
            iterations=iterations,
        )

        # Warmup
        for _ in range(warmup):
            conn = sqlite3.connect(":memory:")
            conn.close()

        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            conn = sqlite3.connect(":memory:")
            conn.execute("SELECT 1")
            conn.close()
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["operation"] = "CREATE + CONNECT + CLOSE"
        return result

    def run_all(self, iterations: int = 100) -> List[BenchmarkResult]:
        """Run all database benchmarks."""
        self.setup()
        try:
            results = [
                self.benchmark_select_trades(iterations),
                self.benchmark_select_candles(iterations),
                self.benchmark_insert_trade(iterations),
                self.benchmark_aggregation(iterations),
                self.benchmark_connection_overhead(iterations),
            ]
            return results
        finally:
            self.teardown()


# ============================================================================
# Signal Generation Benchmarks
# ============================================================================


class SignalBenchmark:
    """Benchmarks for signal generation and indicator calculations."""

    def __init__(self) -> None:
        """Initialize signal benchmark."""
        self._prices: List[float] = []

    def _generate_test_prices(self, count: int = 500) -> List[float]:
        """Generate realistic test price data."""
        import random

        random.seed(42)  # Deterministic for reproducibility
        prices = [50000.0]
        for _ in range(count - 1):
            change_pct = random.gauss(0, 0.02)  # 2% std dev
            prices.append(prices[-1] * (1 + change_pct))
        return prices

    def benchmark_rsi(
        self, iterations: int = 100, warmup: int = 5, period: int = 14
    ) -> BenchmarkResult:
        """Benchmark RSI calculation."""
        result = BenchmarkResult(
            name="calc_rsi",
            backend="cpu",
            iterations=iterations,
        )
        prices = self._generate_test_prices()

        def calc_rsi(prices: List[float], period: int = 14) -> float:
            if len(prices) < period + 1:
                return 50.0
            deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
            gains = [d if d > 0 else 0 for d in deltas[-period:]]
            losses = [-d if d < 0 else 0 for d in deltas[-period:]]
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))

        # Warmup
        for _ in range(warmup):
            calc_rsi(prices, period)

        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            calc_rsi(prices, period)
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["function"] = "calc_rsi"
        result.metadata["period"] = period
        result.metadata["data_points"] = len(prices)
        return result

    def benchmark_adx(
        self, iterations: int = 100, warmup: int = 5, period: int = 14
    ) -> BenchmarkResult:
        """Benchmark ADX calculation."""
        result = BenchmarkResult(
            name="calc_adx",
            backend="cpu",
            iterations=iterations,
        )
        prices = self._generate_test_prices()

        def calc_adx(
            highs: List[float], lows: List[float], closes: List[float], period: int = 14
        ) -> float:
            if len(closes) < period + 1:
                return 0.0
            plus_dm = []
            minus_dm = []
            tr_list = []
            for i in range(1, len(highs)):
                high_diff = highs[i] - highs[i - 1]
                low_diff = lows[i - 1] - lows[i]
                plus_dm.append(max(high_diff, 0) if high_diff > low_diff else 0)
                minus_dm.append(max(low_diff, 0) if low_diff > high_diff else 0)
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
                tr_list.append(tr)
            if len(tr_list) < period:
                return 0.0
            atr = sum(tr_list[:period]) / period
            plus_di = (sum(plus_dm[:period]) / period / atr * 100) if atr > 0 else 0
            minus_di = (sum(minus_dm[:period]) / period / atr * 100) if atr > 0 else 0
            dx = (
                abs(plus_di - minus_di) / (plus_di + minus_di) * 100
                if (plus_di + minus_di) > 0
                else 0
            )
            return dx

        highs = [p * 1.01 for p in prices]
        lows = [p * 0.99 for p in prices]

        # Warmup
        for _ in range(warmup):
            calc_adx(highs, lows, prices, period)

        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            calc_adx(highs, lows, prices, period)
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["function"] = "calc_adx"
        result.metadata["period"] = period
        result.metadata["data_points"] = len(prices)
        return result

    def benchmark_bollinger_bands(
        self, iterations: int = 100, warmup: int = 5, period: int = 20
    ) -> BenchmarkResult:
        """Benchmark Bollinger Bands calculation."""
        result = BenchmarkResult(
            name="calc_bollinger",
            backend="cpu",
            iterations=iterations,
        )
        prices = self._generate_test_prices()

        def calc_bollinger(
            prices: List[float], period: int = 20, num_std: float = 2.0
        ) -> Tuple[float, float, float]:
            if len(prices) < period:
                return (0.0, 0.0, 0.0)
            window = prices[-period:]
            sma = sum(window) / period
            variance = sum((x - sma) ** 2 for x in window) / period
            std_dev = math.sqrt(variance)
            return (sma - num_std * std_dev, sma, sma + num_std * std_dev)

        # Warmup
        for _ in range(warmup):
            calc_bollinger(prices, period)

        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            calc_bollinger(prices, period)
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)

        result.metadata["function"] = "calc_bollinger_bands"
        result.metadata["period"] = period
        result.metadata["data_points"] = len(prices)
        return result

    def run_all(self, iterations: int = 100) -> List[BenchmarkResult]:
        """Run all signal benchmarks."""
        return [
            self.benchmark_rsi(iterations),
            self.benchmark_adx(iterations),
            self.benchmark_bollinger_bands(iterations),
        ]


# ============================================================================
# Main Benchmark Suite
# ============================================================================


class PerformanceBenchmark:
    """
    Main performance benchmarking suite for Bot 3.

    Coordinates database, signal, and system benchmarks.
    Supports comparing SQLite vs PostgreSQL performance.

    Example:
        bench = PerformanceBenchmark()
        bench.run_all()

        # Compare backends
        bench.compare_backends()

        # Save report
        bench.save_report()
    """

    def __init__(self, output_dir: Optional[str] = None) -> None:
        """
        Initialize the benchmark suite.

        Args:
            output_dir: Directory for output files
        """
        self._output_dir = Path(output_dir or BENCHMARK_OUTPUT_DIR)
        self._results: List[BenchmarkResult] = []
        self._comparison_results: Dict[str, List[BenchmarkResult]] = {}

    def run_all(
        self,
        iterations: int = BENCHMARK_ITERATIONS,
        warmup: int = BENCHMARK_WARMUP,
    ) -> List[BenchmarkResult]:
        """
        Run all benchmarks.

        Args:
            iterations: Number of iterations per benchmark
            warmup: Number of warmup iterations

        Returns:
            List of benchmark results
        """
        logger.info(f"Starting performance benchmarks ({iterations} iterations)")
        self._results.clear()

        # Database benchmarks
        logger.info("Running database benchmarks...")
        db_bench = DatabaseBenchmark()
        db_results = db_bench.run_all(iterations)
        self._results.extend(db_results)

        # Signal benchmarks
        logger.info("Running signal generation benchmarks...")
        signal_bench = SignalBenchmark()
        signal_results = signal_bench.run_all(iterations)
        self._results.extend(signal_results)

        logger.info(f"Benchmarks complete: {len(self._results)} results")
        return self._results

    def benchmark_db_query(
        self, query: str, params: Tuple[Any, ...] = (), iterations: int = 100
    ) -> BenchmarkResult:
        """Benchmark a specific database query."""
        db_bench = DatabaseBenchmark()
        db_bench.setup()
        conn = cast(sqlite3.Connection, db_bench._conn)
        try:
            result = BenchmarkResult(
                name="custom_query",
                backend="sqlite",
                iterations=iterations,
            )

            # Warmup
            for _ in range(5):
                conn.execute(query, params).fetchall()

            # Benchmark
            for _ in range(iterations):
                start = time.perf_counter()
                conn.execute(query, params).fetchall()
                elapsed = (time.perf_counter() - start) * 1000
                result.times_ms.append(elapsed)

            result.metadata["query"] = query
            return result
        finally:
            db_bench.teardown()

    def compare_backends(
        self, iterations: int = 50
    ) -> Dict[str, List[BenchmarkResult]]:
        """
        Compare SQLite vs PostgreSQL performance.

        This method benchmarks SQLite and provides a framework for
        PostgreSQL comparison when available.

        Returns:
            Dictionary mapping backend names to results
        """
        logger.info("Comparing database backends...")

        # SQLite benchmarks
        sqlite_bench = DatabaseBenchmark(db_path=":memory:")
        sqlite_results = sqlite_bench.run_all(iterations)

        # PostgreSQL benchmarks (placeholder - requires live PG connection)
        pg_results: List[BenchmarkResult] = []
        try:
            import psycopg2
            from trading_bot_v2.config import config

            if config.database_backend == "postgres":
                conn = psycopg2.connect(
                    host=config.pg_host,
                    port=config.pg_port,
                    dbname=config.pg_database,
                    user=config.pg_user,
                    password=config.pg_password,
                )
                # Would run equivalent PG benchmarks here
                conn.close()
        except (ImportError, Exception) as e:
            logger.info(f"PostgreSQL not available for comparison: {e}")
            # Create placeholder results
            for sqlite_result in sqlite_results:
                placeholder = BenchmarkResult(
                    name=sqlite_result.name,
                    backend="postgresql",
                    iterations=0,
                    metadata={"status": "not_available", "reason": str(e)},
                )
                pg_results.append(placeholder)

        self._comparison_results = {
            "sqlite": sqlite_results,
            "postgresql": pg_results,
        }

        return self._comparison_results

    def get_report(self) -> Dict[str, Any]:
        """Generate comprehensive benchmark report."""
        report: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_benchmarks": len(self._results),
                "backends_tested": list(set(r.backend for r in self._results)),
            },
            "results": [r.to_dict() for r in self._results],
        }

        # Add comparison if available
        if self._comparison_results:
            report["comparison"] = {}
            for backend, results in self._comparison_results.items():
                report["comparison"][backend] = [r.to_dict() for r in results]

        return report

    def save_report(
        self,
        output_path: Optional[str] = None,
        format: str = "json",
    ) -> str:
        """
        Save benchmark report to file.

        Args:
            output_path: Output file path. Auto-generated if None.
            format: Output format ("json" or "text").

        Returns:
            Path to saved report file.
        """
        report = self.get_report()

        if output_path is None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = "json" if format == "json" else "txt"
            output_path = str(self._output_dir / f"benchmark_report_{timestamp}.{ext}")

        if format == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
        else:
            lines = [
                "=" * 70,
                "BOT 3 PERFORMANCE BENCHMARK REPORT",
                "=" * 70,
                f"Timestamp: {report['timestamp']}",
                f"Total Benchmarks: {report['summary']['total_benchmarks']}",
                f"Backends: {', '.join(report['summary']['backends_tested'])}",
                "",
                "RESULTS:",
                "-" * 70,
                f"{'Name':<25} {'Backend':<12} {'Mean':>10} {'P50':>10} {'P95':>10} {'P99':>10} {'Ops/s':>10}",
                "-" * 70,
            ]
            for r in report["results"]:
                lines.append(
                    f"{r['name']:<25} {r['backend']:<12} "
                    f"{r['mean_ms']:>10.3f} {r['median_ms']:>10.3f} "
                    f"{r['p95_ms']:>10.3f} {r['p99_ms']:>10.3f} "
                    f"{r['ops_per_second']:>10.1f}"
                )
            lines.append("")

            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

        logger.info(f"Benchmark report saved to {output_path}")
        return output_path

    def print_summary(self) -> None:
        """Print a summary table of benchmark results."""
        print()
        print("=" * 80)
        print("BOT 3 PERFORMANCE BENCHMARK SUMMARY")
        print("=" * 80)
        print(
            f"{'Name':<25} {'Backend':<12} {'Mean(ms)':>10} {'P95(ms)':>10} "
            f"{'P99(ms)':>10} {'Ops/s':>10} {'Errors':>8}"
        )
        print("-" * 80)
        for r in self._results:
            print(
                f"{r.name:<25} {r.backend:<12} "
                f"{r.mean_ms:>10.3f} {r.p95_ms:>10.3f} "
                f"{r.p99_ms:>10.3f} {r.ops_per_second:>10.1f} "
                f"{r.errors:>8}"
            )
        print("-" * 80)
        print()

    def check_regression(
        self,
        baseline_path: str,
        threshold_pct: float = 10.0,
        statistic: str = "median",
    ) -> List[Dict[str, Any]]:
        """
        Check for performance regressions against a baseline.

        Compares medians by default. The mean is not a safe statistic here:
        the benchmarked operations run in microseconds, so a single
        descheduled iteration is a 100x sample, and it shifts the mean by
        outlier/n regardless of how large n is. Measured over 224
        comparisons of a benchmark against *itself* (same code, same
        process, so the true change is 0%), the mean drifted as far as
        +212% while the median stayed within +19%. A mean-based check
        against a stored baseline therefore reports regressions that are
        not there, which is worse than not checking at all.

        Args:
            baseline_path: Path to baseline JSON report.
            threshold_pct: Regression threshold percentage.
            statistic: Statistic to compare, either "median" or "mean".

        Returns:
            List of detected regressions, each naming the benchmark, the
            baseline and current values, and the percentage change.

        Raises:
            ValueError: If statistic is neither "median" nor "mean".
        """
        if statistic not in ("median", "mean"):
            raise ValueError(f"statistic must be 'median' or 'mean', got {statistic!r}")
        stat_key = f"{statistic}_ms"

        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)

        regressions = []
        baseline_map = {r["name"]: r for r in baseline.get("results", [])}

        for result in self._results:
            if result.name not in baseline_map:
                continue
            base_value = baseline_map[result.name].get(stat_key, 0)
            if base_value <= 0:
                continue
            current_value = getattr(result, stat_key)
            change_pct = ((current_value - base_value) / base_value) * 100
            if change_pct > threshold_pct:
                regressions.append(
                    {
                        "benchmark": result.name,
                        "statistic": statistic,
                        "baseline_ms": base_value,
                        "current_ms": round(current_value, 3),
                        "change_pct": round(change_pct, 1),
                        "threshold_pct": threshold_pct,
                    }
                )

        if regressions:
            logger.warning(
                f"Detected {len(regressions)} performance regressions above "
                f"{threshold_pct}% threshold ({statistic})"
            )
            for reg in regressions:
                logger.warning(
                    f"  {reg['benchmark']}: {reg['change_pct']:+.1f}% "
                    f"({reg['baseline_ms']:.3f}ms -> {reg['current_ms']:.3f}ms)"
                )

        return regressions


# ============================================================================
# CLI Entry Point
# ============================================================================


def _cli_main() -> None:
    """Command-line interface for the benchmark suite."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bot 3 Performance Benchmark Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all benchmarks
  python -m trading_bot_v2.performance_benchmark

  # Run with custom iterations
  python -m trading_bot_v2.performance_benchmark --iterations 200

  # Compare SQLite vs PostgreSQL
  python -m trading_bot_v2.performance_benchmark --compare

  # Check for regressions against baseline
  python -m trading_bot_v2.performance_benchmark --check-regression baseline.json

  # Save report in text format
  python -m trading_bot_v2.performance_benchmark --format text --output report.txt
        """,
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=BENCHMARK_ITERATIONS,
        help=f"Iterations per benchmark (default: {BENCHMARK_ITERATIONS})",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=BENCHMARK_WARMUP,
        help=f"Warmup iterations (default: {BENCHMARK_WARMUP})",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare SQLite vs PostgreSQL performance",
    )
    parser.add_argument(
        "--check-regression",
        type=str,
        default=None,
        help="Check for regressions against baseline file",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="Regression threshold percentage (default: 10.0)",
    )
    parser.add_argument(
        "--statistic",
        choices=["median", "mean"],
        default="median",
        help=(
            "Statistic compared against the baseline (default: median). "
            "The mean is outlier-dominated at microsecond scale and will "
            "report regressions that are not real."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Output format (default: json)",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    bench = PerformanceBenchmark()

    if args.compare:
        bench.compare_backends(args.iterations)
    else:
        bench.run_all(args.iterations, args.warmup)

    bench.print_summary()

    # Save report
    output_path = bench.save_report(
        output_path=args.output,
        format=args.format,
    )
    print(f"Full report saved to: {output_path}")

    # Check regressions
    if args.check_regression:
        regressions = bench.check_regression(
            args.check_regression, args.threshold, args.statistic
        )
        if regressions:
            print(f"\nWARNING: {len(regressions)} regressions detected!")
            for reg in regressions:
                print(
                    f"  {reg['benchmark']}: +{reg['change_pct']:.1f}% "
                    f"({reg['baseline_ms']:.3f}ms -> {reg['current_ms']:.3f}ms)"
                )
            sys.exit(1)
        else:
            print("\nNo regressions detected.")


if __name__ == "__main__":
    _cli_main()
