"""
Performance Tracker
===================

Accumulates equity snapshots during the backtest and computes
the full suite of performance metrics on finalise().

Metrics:
  Profitability:  Total return %, CAGR, Profit factor, Win rate
  Risk:           Sharpe ratio, Sortino ratio, Max drawdown %, Calmar ratio
  Execution:      Trade count, Avg hold time, Avg fee per trade
  Per-strategy:   All of the above broken down by strategy
  Per-regime:     PnL and trade count per market regime
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger


@dataclass
class BacktestResult:
    # Summary
    symbol: str
    start: str
    end: str
    initial_capital: float
    final_equity: float

    # Profitability
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate_pct: float = 0.0

    # Risk
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    avg_drawdown_duration_days: float = 0.0

    # Execution
    total_trades: int = 0       # total fills (opens + closes)
    closed_trades: int = 0      # completed round-trips with realised PnL
    avg_fee_per_trade: float = 0.0
    total_fees: float = 0.0
    total_funding_paid: float = 0.0

    # Breakdowns
    by_strategy: Dict = field(default_factory=dict)
    by_regime: Dict = field(default_factory=dict)

    # Signal-funnel diagnostics (SignalFunnel.to_dict()). Empty when the
    # run was not instrumented, so existing callers are unaffected.
    diagnostics: Dict = field(default_factory=dict)

    # Raw data for report generation
    equity_curve: List = field(default_factory=list)
    trade_log: List = field(default_factory=list)

    def print_summary(self) -> None:
        print(f"\n{'='*60}")
        print(f"BACKTEST RESULTS: {self.symbol} | {self.start} -> {self.end}")
        print(f"{'='*60}")
        print(f"  Initial Capital : ${self.initial_capital:,.2f}")
        print(f"  Final Equity    : ${self.final_equity:,.2f}")
        print(f"  Total Return    : {self.total_return_pct:+.1f}%")
        print(f"  CAGR            : {self.cagr_pct:+.1f}%")
        print(f"  Sharpe Ratio    : {self.sharpe_ratio:.2f}")
        print(f"  Sortino Ratio   : {self.sortino_ratio:.2f}")
        print(f"  Max Drawdown    : {self.max_drawdown_pct:.1f}%")
        print(f"  Win Rate        : {self.win_rate_pct:.1f}%")
        print(f"  Profit Factor   : {self.profit_factor:.2f}")
        print(f"  Total Fills     : {self.total_trades}")
        print(f"  Closed Trades   : {self.closed_trades}")
        print(f"  Total Fees      : ${self.total_fees:,.2f}")
        print(f"{'='*60}\n")

    def save_html(self, path: str) -> None:
        """Generate a self-contained HTML report with equity curve chart."""
        from .reports.html_report import generate_html_report
        generate_html_report(self, path)
        logger.info(f"Report saved to {path}")


class PerformanceTracker:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self._snapshots: List[Dict] = []

    def record_snapshot(self, timestamp: str, equity: float, positions: Dict) -> None:
        self._snapshots.append({
            "timestamp": timestamp,
            "equity": equity,
            "open_positions": len(positions),
        })

    def finalise(
        self,
        final_equity: float,
        trade_log: List[Dict],
        symbol: str,
        start: str,
        end: str,
    ) -> BacktestResult:
        equity_series = [s["equity"] for s in self._snapshots]
        result = BacktestResult(
            symbol=symbol,
            start=start,
            end=end,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            equity_curve=self._snapshots,
            trade_log=trade_log,
            total_trades=len(trade_log),
        )

        result.total_return_pct = (final_equity / self.initial_capital - 1) * 100
        result.total_fees = sum(t.get("fee", 0) for t in trade_log)
        result.avg_fee_per_trade = result.total_fees / max(1, result.total_trades)

        # Days in backtest
        try:
            dt_start = datetime.fromisoformat(start)
            dt_end = datetime.fromisoformat(end)
            days = (dt_end - dt_start).days
            years = days / 365.25
            if years > 0 and final_equity > 0:
                result.cagr_pct = ((final_equity / self.initial_capital) ** (1 / years) - 1) * 100
        except Exception:
            pass

        # Sharpe (annualised, using hourly snapshots)
        if len(equity_series) > 2:
            returns = [
                (equity_series[i] - equity_series[i - 1]) / equity_series[i - 1]
                for i in range(1, len(equity_series))
            ]
            mean_r = sum(returns) / len(returns)
            std_r = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5
            result.sharpe_ratio = (mean_r / std_r * math.sqrt(8760)) if std_r > 0 else 0.0

            # Sortino (downside deviation only)
            downside = [r for r in returns if r < 0]
            if downside:
                downside_std = (sum(r ** 2 for r in downside) / len(downside)) ** 0.5
                result.sortino_ratio = (mean_r / downside_std * math.sqrt(8760)) if downside_std > 0 else 0.0

        # Max drawdown
        peak = self.initial_capital
        max_dd = 0.0
        for eq in equity_series:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = max_dd * 100
        result.calmar_ratio = (result.cagr_pct / result.max_drawdown_pct
                                if result.max_drawdown_pct > 0 else 0.0)

        # Win/loss stats — only count closing fills (pnl != 0); opening fills have pnl=0
        closed_trades = [t for t in trade_log if t.get("pnl", 0) != 0]
        wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
        losses = [t for t in closed_trades if t.get("pnl", 0) < 0]
        gross_profit = sum(t.get("pnl", 0) for t in wins)
        gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
        result.closed_trades = len(closed_trades)
        result.win_rate_pct = len(wins) / max(1, len(closed_trades)) * 100
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Per-regime breakdown (P4): closed trades carry the regime that
        # was confirmed at position entry (tagged by SimulatedExchange).
        by_regime: Dict[str, Dict] = {}
        for t in closed_trades:
            regime = t.get("regime") or "unknown"
            cell = by_regime.setdefault(
                regime, {"closed_trades": 0, "pnl": 0.0, "wins": 0}
            )
            cell["closed_trades"] += 1
            cell["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                cell["wins"] += 1
        result.by_regime = by_regime

        return result
