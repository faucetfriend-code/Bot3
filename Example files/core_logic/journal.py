"""
Trading journal and performance tracking.
Logs trades, calculates metrics, and provides performance analysis.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, date
from dataclasses import asdict
from config import get_config
from models import Trade, Account
from loguru import logger


class JournalError(Exception):
    """Base exception for journal errors."""

    pass


class TradeJournal:
    """
    Manages trade logging and performance metrics.

    Based on Trading Instructions Section VI.D - Trade Journal Entries.
    """

    def __init__(self, journal_path: Optional[Path] = None):
        """
        Initialize trade journal.

        Args:
            journal_path: Path to journal file
        """
        self.config = get_config()
        self.journal_path = journal_path or Path(self.config.logging.trade_journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

        self.trades: List[Trade] = []
        self.daily_stats: Dict[str, Dict[str, Any]] = {}
        self.performance_metrics: Dict[str, Any] = {}

        self._load_journal()

    def _load_journal(self):
        """Load existing journal from file."""
        if self.journal_path.exists():
            try:
                with open(self.journal_path, "r", encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        logger.warning(f"Journal file {self.journal_path} is empty")
                        return
                    data = json.loads(content)

                # Load trades
                for trade_data in data.get("trades", []):
                    # Convert timestamps
                    trade_data["entry_time"] = datetime.fromisoformat(
                        trade_data["entry_time"]
                    )
                    trade_data["exit_time"] = datetime.fromisoformat(
                        trade_data["exit_time"]
                    )
                    trade = Trade(**trade_data)
                    self.trades.append(trade)

                # Load daily stats
                self.daily_stats = data.get("daily_stats", {})

                # Load performance metrics
                self.performance_metrics = data.get("performance_metrics", {})

                logger.info(f"Loaded journal with {len(self.trades)} trades")

            except Exception as e:
                logger.error(f"Failed to load journal: {e}")
                # Start with empty journal
                self.trades = []
                self.daily_stats = {}
                self.performance_metrics = {}

    def _save_journal(self):
        """Save journal to file."""
        try:
            data = {
                "trades": [trade.to_dict() for trade in self.trades],
                "daily_stats": self.daily_stats,
                "performance_metrics": self.performance_metrics,
                "last_updated": datetime.now().isoformat(),
            }

            with open(self.journal_path, "w") as f:
                json.dump(data, f, indent=2, default=str)

            logger.debug("Journal saved")

        except Exception as e:
            logger.error(f"Failed to save journal: {e}")
            raise JournalError(f"Journal save failed: {e}")

    def log_trade(self, trade: Trade):
        """
        Log a completed trade.

        Args:
            trade: Completed trade to log
        """
        self.trades.append(trade)

        # Update daily stats
        trade_date = trade.exit_time.date().isoformat()
        if trade_date not in self.daily_stats:
            self.daily_stats[trade_date] = {
                "trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl": 0.0,
                "total_fees": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
            }

        daily = self.daily_stats[trade_date]
        daily["trades"] += 1
        daily["total_pnl"] += trade.pnl_dollar

        if trade.is_winner:
            daily["winning_trades"] += 1
            daily["largest_win"] = max(daily["largest_win"], trade.pnl_dollar)
        else:
            daily["losing_trades"] += 1
            daily["largest_loss"] = min(daily["largest_loss"], trade.pnl_dollar)

        # Update performance metrics
        self._update_performance_metrics()

        # Save journal
        self._save_journal()

        logger.info(f"Trade logged: {trade.id}, P&L: ${trade.pnl_dollar:.2f}")

    def _update_performance_metrics(self):
        """Update overall performance metrics."""
        if not self.trades:
            return

        # Basic metrics
        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t.is_winner]
        losing_trades = [t for t in self.trades if not t.is_winner]

        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0

        # P&L metrics
        total_pnl = sum(t.pnl_dollar for t in self.trades)
        avg_win = (
            sum(t.pnl_dollar for t in winning_trades) / len(winning_trades)
            if winning_trades
            else 0
        )
        avg_loss = (
            sum(t.pnl_dollar for t in losing_trades) / len(losing_trades)
            if losing_trades
            else 0
        )

        # RRR metrics
        avg_rrr = (
            sum(t.actual_rrr for t in self.trades) / total_trades
            if total_trades > 0
            else 0
        )

        # Expectancy
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))

        # Profit factor
        total_wins = sum(t.pnl_dollar for t in winning_trades)
        total_losses = abs(sum(t.pnl_dollar for t in losing_trades))
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        # Drawdown calculation
        cumulative_pnl = 0
        peak = 0
        max_drawdown = 0

        for trade in sorted(self.trades, key=lambda x: x.exit_time):
            cumulative_pnl += trade.pnl_dollar
            peak = max(peak, cumulative_pnl)
            drawdown = peak - cumulative_pnl
            max_drawdown = max(max_drawdown, drawdown)

        # Consecutive losses
        consecutive_losses = 0
        max_consecutive_losses = 0
        for trade in sorted(self.trades, key=lambda x: x.exit_time):
            if not trade.is_winner:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            else:
                consecutive_losses = 0

        self.performance_metrics = {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_rrr": avg_rrr,
            "expectancy": expectancy,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "max_consecutive_losses": max_consecutive_losses,
            "last_updated": datetime.now().isoformat(),
        }

    def get_daily_stats(self, target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Get daily statistics.

        Args:
            target_date: Specific date, or today if None

        Returns:
            Daily statistics
        """
        if target_date is None:
            target_date = date.today()

        date_str = target_date.isoformat()
        return self.daily_stats.get(
            date_str,
            {
                "trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl": 0.0,
                "win_rate": 0.0,
            },
        )

    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Get performance summary.

        Returns:
            Performance metrics
        """
        return self.performance_metrics.copy()

    def get_trades_by_date_range(self, start_date: date, end_date: date) -> List[Trade]:
        """
        Get trades within date range.

        Args:
            start_date: Start date
            end_date: End date

        Returns:
            List of trades in range
        """
        return [
            trade
            for trade in self.trades
            if start_date <= trade.exit_time.date() <= end_date
        ]

    def get_trades_by_strategy(self, strategy: str) -> List[Trade]:
        """
        Get trades by strategy.

        Args:
            strategy: Strategy name

        Returns:
            List of trades for strategy
        """
        return [trade for trade in self.trades if trade.strategy.value == strategy]

    def export_to_csv(self, filepath: Path):
        """
        Export trades to CSV.

        Args:
            filepath: Output file path
        """
        import csv

        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w", newline="") as f:
            if not self.trades:
                return

            # Get all fields from first trade
            fieldnames = list(self.trades[0].to_dict().keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for trade in self.trades:
                writer.writerow(trade.to_dict())

        logger.info(f"Exported {len(self.trades)} trades to {filepath}")


class PerformanceAnalyzer:
    """
    Advanced performance analysis and reporting.
    """

    def __init__(self, journal: TradeJournal):
        """
        Initialize performance analyzer.

        Args:
            journal: Trade journal instance
        """
        self.journal = journal

    def calculate_monthly_returns(self) -> Dict[str, float]:
        """
        Calculate monthly returns.

        Returns:
            Dict of month -> return percentage
        """
        monthly_pnl = {}

        for trade in self.journal.trades:
            month_key = f"{trade.exit_time.year}-{trade.exit_time.month:02d}"
            if month_key not in monthly_pnl:
                monthly_pnl[month_key] = 0
            monthly_pnl[month_key] += trade.pnl_dollar

        # Assume starting balance of $1000 for percentage calculations
        starting_balance = 1000.0
        monthly_returns = {}

        for month, pnl in monthly_pnl.items():
            monthly_returns[month] = (pnl / starting_balance) * 100

        return monthly_returns

    def calculate_strategy_performance(self) -> Dict[str, Dict[str, Any]]:
        """
        Calculate performance by strategy.

        Returns:
            Dict of strategy -> metrics
        """
        strategies = {}
        strategy_trades = {}

        # Group trades by strategy
        for trade in self.journal.trades:
            strategy = trade.strategy.value
            if strategy not in strategy_trades:
                strategy_trades[strategy] = []
            strategy_trades[strategy].append(trade)

        # Calculate metrics for each strategy
        for strategy, trades in strategy_trades.items():
            winning_trades = [t for t in trades if t.is_winner]
            losing_trades = [t for t in trades if not t.is_winner]

            metrics = {
                "total_trades": len(trades),
                "win_rate": len(winning_trades) / len(trades) if trades else 0,
                "total_pnl": sum(t.pnl_dollar for t in trades),
                "avg_win": (
                    sum(t.pnl_dollar for t in winning_trades) / len(winning_trades)
                    if winning_trades
                    else 0
                ),
                "avg_loss": (
                    sum(t.pnl_dollar for t in losing_trades) / len(losing_trades)
                    if losing_trades
                    else 0
                ),
                "avg_rrr": (
                    sum(t.actual_rrr for t in trades) / len(trades) if trades else 0
                ),
            }

            strategies[strategy] = metrics

        return strategies

    def generate_performance_report(self) -> str:
        """
        Generate comprehensive performance report.

        Returns:
            Formatted report string
        """
        metrics = self.journal.get_performance_summary()
        monthly_returns = self.calculate_monthly_returns()
        strategy_perf = self.calculate_strategy_performance()

        report = []
        report.append("=== TRADING PERFORMANCE REPORT ===")
        report.append("")

        report.append("OVERALL METRICS:")
        report.append(f"Total Trades: {metrics.get('total_trades', 0)}")
        report.append(".1%")
        report.append(".2f")
        report.append(".2f")
        report.append(".2f")
        report.append(".2f")
        report.append(".2f")
        report.append(".2f")
        report.append(f"Max Drawdown: ${metrics.get('max_drawdown', 0):.2f}")
        report.append(
            f"Max Consecutive Losses: {metrics.get('max_consecutive_losses', 0)}"
        )
        report.append("")

        report.append("MONTHLY RETURNS:")
        for month, return_pct in sorted(monthly_returns.items()):
            report.append(f"{month}: {return_pct:.2f}%")
        report.append("")

        report.append("STRATEGY PERFORMANCE:")
        for strategy, strat_metrics in strategy_perf.items():
            report.append(f"{strategy.upper()}:")
            report.append(f"  Trades: {strat_metrics['total_trades']}")
            report.append(".1%")
            report.append(".2f")
            report.append("")

        return "\n".join(report)


def create_journal(journal_path: Optional[Path] = None) -> TradeJournal:
    """
    Create or load trade journal.

    Args:
        journal_path: Path to journal file

    Returns:
        TradeJournal instance
    """
    return TradeJournal(journal_path)


def create_performance_analyzer(journal: TradeJournal) -> PerformanceAnalyzer:
    """
    Create performance analyzer.

    Args:
        journal: Trade journal instance

    Returns:
        PerformanceAnalyzer instance
    """
    return PerformanceAnalyzer(journal)
