"""
Walk-Forward Analysis
=====================

Splits the full date range into rolling train/test windows.
For each window: trains on N months, tests on M months, steps forward M months.

Default: 6-month train, 1-month test.

This prevents overfitting to a single time period and gives a
distribution of performance across different market conditions.

Usage:
    wf = WalkForwardAnalyzer(engine, config)
    results = wf.run(start="2023-01-01", end="2024-12-31", symbol="SUI-USDC")
    wf.print_summary(results)
"""

from datetime import datetime, timedelta
from typing import List
from loguru import logger

from .engine import BacktestEngine
from .performance import BacktestResult


class WalkForwardAnalyzer:
    def __init__(self, engine: BacktestEngine, config=None):
        self.engine = engine
        self.cfg = config or engine.cfg

    def run(
        self,
        start: str,
        end: str,
        symbol: str,
        initial_capital: float = 10000.0,
    ) -> List[BacktestResult]:
        train_months = self.cfg.backtest_walk_forward_train_months
        test_months = self.cfg.backtest_walk_forward_test_months

        windows = self._build_windows(start, end, train_months, test_months)
        results = []

        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            logger.info(f"Window {i+1}/{len(windows)}: test {test_start} -> {test_end}")
            # Note: training window is reserved for parameter optimisation
            # (future enhancement -- currently runs test with default params)
            result = self.engine.run(
                start=test_start,
                end=test_end,
                symbol=symbol,
                initial_capital=initial_capital,
            )
            result.start = test_start  # Label by test window
            results.append(result)

        self.print_summary(results)
        return results

    def print_summary(self, results: List[BacktestResult]) -> None:
        print(f"\n{'='*60}")
        print(f"WALK-FORWARD SUMMARY ({len(results)} windows)")
        print(f"{'='*60}")
        returns = [r.total_return_pct for r in results]
        sharpes = [r.sharpe_ratio for r in results]
        drawdowns = [r.max_drawdown_pct for r in results]
        win_rates = [r.win_rate_pct for r in results]
        profitable = sum(1 for r in returns if r > 0)

        print(f"  Profitable windows : {profitable}/{len(results)}")
        print(f"  Avg return         : {sum(returns)/len(returns):+.1f}%")
        print(f"  Avg Sharpe         : {sum(sharpes)/len(sharpes):.2f}")
        print(f"  Avg Max DD         : {sum(drawdowns)/len(drawdowns):.1f}%")
        print(f"  Avg Win Rate       : {sum(win_rates)/len(win_rates):.1f}%")
        print(f"{'='*60}\n")

    @staticmethod
    def _build_windows(
        start: str,
        end: str,
        train_months: int,
        test_months: int,
    ) -> List:
        windows = []
        dt_start = datetime.fromisoformat(start)
        dt_end = datetime.fromisoformat(end)

        # First test window starts after initial training period
        test_start = dt_start + timedelta(days=train_months * 30)

        while test_start < dt_end:
            train_start = test_start - timedelta(days=train_months * 30)
            train_end = test_start - timedelta(days=1)
            test_end = min(test_start + timedelta(days=test_months * 30 - 1), dt_end)
            windows.append((
                train_start.date().isoformat(),
                train_end.date().isoformat(),
                test_start.date().isoformat(),
                test_end.date().isoformat(),
            ))
            test_start += timedelta(days=test_months * 30)

        return windows
