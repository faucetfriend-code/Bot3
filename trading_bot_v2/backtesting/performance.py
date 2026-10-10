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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from datetime import datetime
from loguru import logger

if TYPE_CHECKING:
    from .simulated_exchange import SimulatedPosition


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
    total_trades: int = 0  # total fills (opens + closes)
    closed_trades: int = 0  # closing fills, including partial closes and breakevens
    avg_fee_per_trade: float = 0.0
    total_fees: float = 0.0
    total_funding_paid: float = 0.0

    # Breakdowns
    by_strategy: Dict[str, Any] = field(default_factory=dict)
    by_regime: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Signal-funnel diagnostics (SignalFunnel.to_dict()). Empty when the
    # run was not instrumented, so existing callers are unaffected.
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    # Raw data for report generation
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    trade_log: List[Dict[str, Any]] = field(default_factory=list)

    def print_summary(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"BACKTEST RESULTS: {self.symbol} | {self.start} -> {self.end}")
        print(f"{'=' * 60}")
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
        print(f"  Funding Paid    : ${self.total_funding_paid:,.2f}")
        print(f"{'=' * 60}\n")

    def save_html(self, path: str) -> None:
        """Generate a self-contained HTML report with equity curve chart."""
        from .reports.html_report import generate_html_report

        generate_html_report(self, path)
        logger.info(f"Report saved to {path}")


class PerformanceTracker:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self._snapshots: List[Dict[str, Any]] = []

    def record_snapshot(
        self, timestamp: str, equity: float, positions: Dict[str, "SimulatedPosition"]
    ) -> None:
        self._snapshots.append(
            {
                "timestamp": timestamp,
                "equity": equity,
                "open_positions": len(positions),
            }
        )

    def finalise(
        self,
        final_equity: float,
        trade_log: List[Dict[str, Any]],
        symbol: str,
        start: str,
        end: str,
        total_funding: Optional[float] = None,
    ) -> BacktestResult:
        """Assemble the run's metrics.

        Args:
            final_equity: Cash + unrealised at the last bar.
            trade_log: Every fill the exchange recorded.
            symbol: Symbol replayed.
            start: Window start (ISO date).
            end: Window end (ISO date).
            total_funding: Net funding cash-flow over the run, negative
                when funding was paid. Reported as ``total_funding_paid``
                (positive = paid out). Left at 0.0 when the caller does
                not supply it, which is what happened for the whole
                pre-2026-07 campaign - funding WAS charged to the
                balance but never surfaced in any report.
        """
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
        if total_funding is not None:
            result.total_funding_paid = -float(total_funding)

        # Days in backtest
        try:
            dt_start = datetime.fromisoformat(start)
            dt_end = datetime.fromisoformat(end)
            days = (dt_end - dt_start).days
            years = days / 365.25
            if years > 0 and final_equity > 0:
                result.cagr_pct = (
                    (final_equity / self.initial_capital) ** (1 / years) - 1
                ) * 100
        except Exception:
            pass

        # Arithmetic-return drift and residual variance per elapsed hour.
        # For unequal intervals, E[r_i] = drift * hours_i and variance scales
        # with hours_i. Equal spacing reduces exactly to mean/std * sqrt(N/y).
        # Preserve the historical conditional-downside Sortino convention.
        if len(equity_series) > 2:
            intervals: List[Tuple[float, float]] = []
            for previous, current in zip(self._snapshots, self._snapshots[1:]):
                hours = (
                    datetime.fromisoformat(current["timestamp"])
                    - datetime.fromisoformat(previous["timestamp"])
                ).total_seconds() / 3600.0
                # Percentage returns after zero/negative equity are undefined.
                if hours > 0 and previous["equity"] > 0:
                    change = current["equity"] / previous["equity"] - 1
                    intervals.append((change, hours))
            if len(intervals) >= 2:
                elapsed = sum(hours for _, hours in intervals)
                drift = sum(change for change, _ in intervals) / elapsed
                variance = (
                    sum((change - drift * hours) ** 2 for change, hours in intervals)
                    / elapsed
                )
                if variance > 0:
                    result.sharpe_ratio = drift / math.sqrt(variance) * math.sqrt(8760)
                downside = [(r, hours) for r, hours in intervals if r < 0]
                if downside:
                    downside_variance = sum(r**2 for r, _ in downside) / sum(
                        hours for _, hours in downside
                    )
                    result.sortino_ratio = (
                        drift / math.sqrt(downside_variance) * math.sqrt(8760)
                    )

        # Max drawdown
        peak = self.initial_capital
        max_dd = 0.0
        for eq in [*equity_series, final_equity]:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = max_dd * 100
        result.calmar_ratio = (
            result.cagr_pct / result.max_drawdown_pct
            if result.max_drawdown_pct > 0
            else 0.0
        )

        # Win/loss stats count closing fills and use fee-adjusted realised PnL.
        # New logs identify closes explicitly, including price breakevens.
        # Keep gross-PnL fallback for archived logs without fee allocation.
        closed_trades = [
            t for t in trade_log if t.get("closed_qty", abs(t.get("pnl", 0))) > 0
        ]
        wins = [t for t in closed_trades if t.get("net_pnl", t.get("pnl", 0)) > 0]
        losses = [t for t in closed_trades if t.get("net_pnl", t.get("pnl", 0)) < 0]
        gross_profit = sum(t.get("net_pnl", t.get("pnl", 0)) for t in wins)
        gross_loss = abs(sum(t.get("net_pnl", t.get("pnl", 0)) for t in losses))
        result.closed_trades = len(closed_trades)
        result.win_rate_pct = len(wins) / max(1, len(closed_trades)) * 100
        result.profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf")
        )

        # Per-regime breakdown (P4): closed trades carry the regime that
        # was confirmed at position entry (tagged by SimulatedExchange).
        by_regime: Dict[str, Dict[str, Any]] = {}
        for t in closed_trades:
            regime = t.get("regime") or "unknown"
            cell = by_regime.setdefault(
                regime, {"closed_trades": 0, "pnl": 0.0, "wins": 0}
            )
            cell["closed_trades"] += 1
            cell["pnl"] += t.get("net_pnl", t.get("pnl", 0))
            if t.get("net_pnl", t.get("pnl", 0)) > 0:
                cell["wins"] += 1
        result.by_regime = by_regime
        # Per-strategy breakdown: declared on BacktestResult since the
        # first version and read by the e2e tests, but never populated.
        result.by_strategy = per_strategy_breakdown(trade_log, closed_trades)

        return result


def per_strategy_breakdown(
    trade_log: List[Dict[str, Any]], closed_trades: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Aggregate fills and closed-trade outcomes per strategy.

    Args:
        trade_log: Every fill recorded by the exchange (carries ``strategy``).
        closed_trades: The subset that closed quantity (see ``finalise``).

    Returns:
        ``{strategy: {fills, closed_trades, wins, losses, net_pnl, fees,
        win_rate_pct, profit_factor}}``. ``profit_factor`` is None when
        there were no losing trades (undefined, not infinite, so the
        value stays JSON-safe).
    """
    cells: Dict[str, Dict[str, Any]] = {}

    def cell(name: str) -> Dict[str, Any]:
        return cells.setdefault(
            name or "unknown",
            {
                "fills": 0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "net_pnl": 0.0,
                "fees": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            },
        )

    for fill in trade_log:
        c = cell(fill.get("strategy", ""))
        c["fills"] += 1
        c["fees"] += float(fill.get("fee", 0) or 0)
    for trade in closed_trades:
        c = cell(trade.get("strategy", ""))
        pnl = float(trade.get("net_pnl", trade.get("pnl", 0)) or 0)
        c["closed_trades"] += 1
        c["net_pnl"] += pnl
        if pnl > 0:
            c["wins"] += 1
            c["gross_profit"] += pnl
        elif pnl < 0:
            c["losses"] += 1
            c["gross_loss"] += -pnl
    for c in cells.values():
        c["win_rate_pct"] = c["wins"] / max(1, c["closed_trades"]) * 100
        c["profit_factor"] = (
            c["gross_profit"] / c["gross_loss"] if c["gross_loss"] > 0 else None
        )
        c["net_pnl"] = round(c["net_pnl"], 6)
        c["fees"] = round(c["fees"], 6)
    return cells
