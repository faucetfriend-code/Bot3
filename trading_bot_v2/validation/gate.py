"""
Strategy Validation Gate (P5)
=============================

Codifies the standing house policy for promoting a strategy from
backtest to (testnet) deployment as a single structured verdict:

1. min_closed_trades : every symbol has at least GATE_MIN_TRADES
                       closed trades (default 30)
2. profit_factor     : pooled profit factor > GATE_MIN_PF (default 1.3)
3. psr_or_dsr        : pooled PSR >= GATE_MIN_PSR (default 0.95), OR
                       DSR >= GATE_MIN_PSR when the trial registry
                       knows how many configurations were tried
4. cross_symbol      : positive expectancy on at least 2 symbols

Usage:
    from trading_bot_v2.validation.gate import evaluate_strategy_gate

    verdict = evaluate_strategy_gate(
        strategy="mean_reversion",
        symbol_returns={"SUI-USDC": [...], "BTC-USDC": [...]},
        n_trials=120,
    )

CLI (runs one backtest per symbol, then evaluates):
    python -m trading_bot_v2.validation.gate \\
        --strategy mean_reversion --symbols SUI-USDC,BTC-USDC \\
        --start 2024-01-01 --end 2025-01-01
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .statistics import (
    closed_trade_returns,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)

DEFAULT_MIN_TRADES = 30
DEFAULT_MIN_PF = 1.3
DEFAULT_MIN_PSR = 0.95
MIN_CONSISTENT_SYMBOLS = 2


def load_gate_policy() -> Dict[str, float]:
    """Read the standing gate policy from the environment.

    Env vars (with defaults): GATE_MIN_TRADES=30, GATE_MIN_PF=1.3,
    GATE_MIN_PSR=0.95.

    Returns:
        Dict with min_closed_trades, min_profit_factor, min_psr,
        min_consistent_symbols.
    """
    return {
        "min_closed_trades": int(
            os.getenv("GATE_MIN_TRADES", str(DEFAULT_MIN_TRADES))
        ),
        "min_profit_factor": float(
            os.getenv("GATE_MIN_PF", str(DEFAULT_MIN_PF))
        ),
        "min_psr": float(os.getenv("GATE_MIN_PSR", str(DEFAULT_MIN_PSR))),
        "min_consistent_symbols": MIN_CONSISTENT_SYMBOLS,
    }


@dataclass
class GateCheck:
    """One pass/fail check of the validation gate.

    Attributes:
        name: Check identifier.
        passed: Whether the check passed.
        value: Observed value (formatted string).
        threshold: Required threshold (formatted string).
        detail: Extra context (per-symbol breakdown etc.).
    """

    name: str
    passed: bool
    value: str
    threshold: str
    detail: str = ""


@dataclass
class GateVerdict:
    """Structured verdict of the validation gate.

    Attributes:
        strategy: Strategy the verdict applies to.
        symbols: Symbols evaluated.
        checks: Individual checks (all must pass).
        passed: Overall verdict.
        n_trials: Registry trial count used for DSR (None = unknown).
    """

    strategy: str
    symbols: List[str]
    checks: List[GateCheck] = field(default_factory=list)
    passed: bool = False
    n_trials: Optional[int] = None


def _profit_factor(returns: List[float]) -> float:
    """Profit factor (gross profit / gross loss) of a return series."""
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return float("inf") if gross_profit > 0 else 0.0


def evaluate_strategy_gate(
    strategy: str,
    symbol_returns: Dict[str, List[float]],
    min_closed_trades: Optional[int] = None,
    min_pf: Optional[float] = None,
    min_psr: Optional[float] = None,
    n_trials: Optional[int] = None,
    sr_variance: Optional[float] = None,
) -> GateVerdict:
    """Evaluate the standing validation gate for a strategy.

    Args:
        strategy: Strategy name (snake_case).
        symbol_returns: Per-symbol lists of per-trade fractional
            returns (pnl / initial_capital) from untouched OOS data.
        min_closed_trades: Override for GATE_MIN_TRADES.
        min_pf: Override for GATE_MIN_PF.
        min_psr: Override for GATE_MIN_PSR.
        n_trials: Total configurations tried (from the trial
            registry); None when unknown - the DSR leg is then skipped
            and only PSR can satisfy check 3.
        sr_variance: Observed variance of trial Sharpe values, when
            known (passed through to the DSR).

    Returns:
        GateVerdict with one GateCheck per policy rule.
    """
    policy = load_gate_policy()
    min_trades = (
        min_closed_trades
        if min_closed_trades is not None
        else policy["min_closed_trades"]
    )
    min_pf = min_pf if min_pf is not None else policy["min_profit_factor"]
    min_psr = min_psr if min_psr is not None else policy["min_psr"]

    symbols = list(symbol_returns.keys())
    verdict = GateVerdict(strategy=strategy, symbols=symbols, n_trials=n_trials)
    pooled: List[float] = []
    for returns in symbol_returns.values():
        pooled.extend(returns)

    # --- Check 1: minimum closed trades per symbol ---
    counts = {sym: len(r) for sym, r in symbol_returns.items()}
    worst = min(counts.values()) if counts else 0
    verdict.checks.append(
        GateCheck(
            name="min_closed_trades",
            passed=bool(counts) and worst >= min_trades,
            value=str(worst),
            threshold=f">= {min_trades} per symbol",
            detail=", ".join(f"{s}: {c}" for s, c in counts.items()),
        )
    )

    # --- Check 2: pooled profit factor ---
    pf = _profit_factor(pooled)
    per_symbol_pf = {
        sym: _profit_factor(r) for sym, r in symbol_returns.items()
    }
    pf_detail = ", ".join(
        f"{s}: {('inf' if v == float('inf') else f'{v:.2f}')}"
        for s, v in per_symbol_pf.items()
    )
    verdict.checks.append(
        GateCheck(
            name="profit_factor",
            passed=pf > min_pf,
            value="inf" if pf == float("inf") else f"{pf:.2f}",
            threshold=f"> {min_pf}",
            detail=pf_detail,
        )
    )

    # --- Check 3: PSR >= threshold OR DSR >= threshold (when N known) ---
    psr = probabilistic_sharpe_ratio(pooled)
    psr_ok = psr.value is not None and psr.value >= min_psr
    dsr_value: Optional[float] = None
    dsr_ok = False
    if n_trials is not None and n_trials > 1:
        dsr = deflated_sharpe_ratio(
            pooled, n_trials=n_trials, var_sharpe_across_trials=sr_variance
        )
        dsr_value = dsr.value
        dsr_ok = dsr.value is not None and dsr.value >= min_psr
    psr_s = f"{psr.value:.4f}" if psr.value is not None else f"n/a ({psr.reason})"
    dsr_s = (
        f"{dsr_value:.4f}" if dsr_value is not None
        else ("n/a (N trials unknown)" if not n_trials or n_trials <= 1 else "n/a")
    )
    verdict.checks.append(
        GateCheck(
            name="psr_or_dsr",
            passed=psr_ok or dsr_ok,
            value=f"PSR {psr_s} / DSR {dsr_s}",
            threshold=f">= {min_psr}",
            detail=f"N trials = {n_trials if n_trials else 'unknown'}",
        )
    )

    # --- Check 4: cross-symbol consistency ---
    positive = [
        sym
        for sym, r in symbol_returns.items()
        if r and (sum(r) / len(r)) > 0
    ]
    detail = (
        "positive expectancy on: " + (", ".join(positive) or "none")
    )
    if len(symbols) < MIN_CONSISTENT_SYMBOLS:
        detail += f" (only {len(symbols)} symbol(s) supplied)"
    verdict.checks.append(
        GateCheck(
            name="cross_symbol_consistency",
            passed=len(positive) >= MIN_CONSISTENT_SYMBOLS,
            value=str(len(positive)),
            threshold=f">= {MIN_CONSISTENT_SYMBOLS} symbols with positive expectancy",
            detail=detail,
        )
    )

    verdict.passed = all(c.passed for c in verdict.checks)
    return verdict


def print_verdict(verdict: GateVerdict) -> None:
    """Print a gate verdict as a fixed-width table."""
    print(f"\n{'='*78}")
    print(
        f"VALIDATION GATE: {verdict.strategy} | "
        f"symbols: {', '.join(verdict.symbols)}"
    )
    print(f"{'='*78}")
    print(f"  {'Check':<26} {'Result':<7} {'Value':<28} Threshold")
    print(f"  {'-'*74}")
    for c in verdict.checks:
        tag = "PASS" if c.passed else "FAIL"
        print(f"  {c.name:<26} {tag:<7} {c.value:<28} {c.threshold}")
        if c.detail:
            print(f"  {'':<26} {'':<7} {c.detail}")
    print(f"  {'-'*74}")
    overall = "PASS" if verdict.passed else "FAIL"
    print(f"  OVERALL: {overall}")
    print(f"{'='*78}\n")


def main() -> int:
    """CLI: backtest each symbol and evaluate the gate."""
    import argparse

    from loguru import logger

    parser = argparse.ArgumentParser(
        description="Run the P5 validation gate for a strategy across symbols",
    )
    parser.add_argument(
        "--strategy", "-s", required=True,
        help="Strategy (snake_case, e.g. mean_reversion)",
    )
    parser.add_argument(
        "--symbols", required=True,
        help="Comma-separated symbols (e.g. SUI-USDC,BTC-USDC)",
    )
    parser.add_argument("--start", type=str, default=None,
                        help="Backtest start date (default: config)")
    parser.add_argument("--end", type=str, default=None,
                        help="Backtest end date (default: config)")
    parser.add_argument("--capital", "-c", type=float, default=10000.0,
                        help="Initial capital per symbol (default: 10000)")
    args = parser.parse_args()

    from ..backtesting.engine import BacktestEngine
    from ..config import config as cfg
    from ..database import DatabaseManager
    from ..regime_param_overlay import resolve_strategy_key

    start = args.start or cfg.backtest_start_date
    end = args.end or cfg.backtest_end_date
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    strategy_key = resolve_strategy_key(args.strategy) or args.strategy

    symbol_returns: Dict[str, List[float]] = {}
    for symbol in symbols:
        logger.info(f"Gate backtest: {strategy_key} on {symbol} {start}->{end}")
        engine = BacktestEngine()
        result = engine.run(
            start=start,
            end=end,
            symbol=symbol,
            initial_capital=args.capital,
            strategy_filter=strategy_key,
        )
        symbol_returns[symbol] = closed_trade_returns(
            result.trade_log, args.capital
        )

    n_trials: Optional[int] = None
    sr_variance: Optional[float] = None
    try:
        db = DatabaseManager()
        total = db.get_total_trials(strategy_key)
        if total > 0:
            n_trials = total
            sr_variance = db.get_trial_sr_variance(strategy_key)
    except Exception as e:
        logger.warning(f"Trial registry lookup failed: {e}")

    verdict = evaluate_strategy_gate(
        strategy=strategy_key,
        symbol_returns=symbol_returns,
        n_trials=n_trials,
        sr_variance=sr_variance,
    )
    print_verdict(verdict)
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
