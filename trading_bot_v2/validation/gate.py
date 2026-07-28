"""
Strategy Validation Gate (P5)
=============================

Codifies the standing house policy for promoting a strategy from
backtest to (testnet) deployment as a single structured verdict:

1. sample_adequacy   : the POOLED closed-trade sample is large enough
                       for the PSR test to resolve an edge of
                       GATE_REFERENCE_SR, and at least
                       GATE_MIN_CONSISTENT_SYMBOLS symbols cleared
                       GATE_MIN_TRADES_PER_SYMBOL so cross-symbol
                       consistency is testable at all
2. profit_factor     : pooled profit factor > GATE_MIN_PF (default 1.3)
3. psr_or_dsr        : pooled PSR >= GATE_MIN_PSR (default 0.95), OR
                       DSR >= GATE_MIN_PSR when the trial registry
                       knows how many configurations were tried
4. cross_symbol      : positive expectancy on at least 2 eligible
                       symbols

Why the trade-count check is derived, not hand-set
--------------------------------------------------

The old check demanded 30 closed trades PER SYMBOL. That number is a
measurement of BAR FREQUENCY, not of strategy quality: a 5m grid
strategy clears it in a fortnight while ma_crossover, which trades on 4h
bars, sees maybe 10-20 crossovers in a two-month window and so was
guaranteed to FAIL no matter how good it was. Uniform counts also rot -
they are exactly the kind of hand-set constant that has to be re-tuned
every time a timeframe changes.

The replacement asks a statistical question instead: how many
observations does the PSR need before an edge worth deploying
(GATE_REFERENCE_SR, a Sharpe PER TRADE) would be provable at
GATE_MIN_PSR confidence? That is ``min_observations_for_sharpe`` and it
is applied to the POOLED sample, because pooling across symbols and
windows is how a slow strategy accumulates evidence. The defaults
(sr 0.30, confidence 0.95) derive 33 - close to the legacy 30, so fast
strategies see essentially no change, while a slow strategy is judged on
its pooled record rather than on its bar frequency.

Three outcomes, not two
-----------------------

A three-trade sample cannot support a confident verdict in EITHER
direction. Collapsing that into FAIL is a lie that reads identically to
a genuine rejection, which matters most right after a strategy has been
repaired. So the gate reports a ``GateOutcome`` (mirroring
``diagnostics/outcomes.py``): PASS, FAIL, INSUFFICIENT_DATA or
NO_TRADES. When the sample is inadequate the quality checks are marked
``advisory`` - they are still computed and reported, but they are not
decisive, and ``passed`` is False either way so an inadequate sample can
never promote a strategy.

The remedy for INSUFFICIENT_DATA is MORE DATA, not a lower bar:
``GateVerdict.data_multiple_needed`` and ``required_window_months()``
say how much more, extrapolated from the trade rate actually observed
rather than from a guessed-at table of per-strategy frequencies.

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

import math
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .statistics import (
    bootstrap_profit_factor_bound,
    closed_trade_returns,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    profit_factor as _profit_factor,
    sample_adequacy,
)

#: Smallest Sharpe-per-trade considered worth deploying. The pooled
#: sample must be big enough to resolve an edge this size; it is NOT a
#: threshold the strategy has to beat (PSR/PF do that job).
DEFAULT_REFERENCE_SR = 0.30
#: Trades a single symbol needs before its expectancy is allowed to vote
#: in the cross-symbol consistency check.
DEFAULT_MIN_TRADES_PER_SYMBOL = 5
DEFAULT_MIN_PF = 1.3
DEFAULT_MIN_PSR = 0.95
MIN_CONSISTENT_SYMBOLS = 2
#: Ceiling on the window length the gate will recommend, so a strategy
#: that barely trades cannot ask for a century of history.
DEFAULT_MAX_WINDOW_MONTHS = 24


class GateOutcome(str, Enum):
    """What the gate actually concluded.

    PASS and FAIL are verdicts about strategy QUALITY and are only
    reachable on an adequate sample. INSUFFICIENT_DATA and NO_TRADES are
    statements about the EVIDENCE, and are the honest answer when the
    sample cannot support either verdict.
    """

    #: Adequate sample, every quality check cleared.
    PASS = "pass"
    #: Adequate sample, at least one quality check failed. A rejection.
    FAIL = "fail"
    #: The strategy traded, but not enough to decide. Not a rejection.
    INSUFFICIENT_DATA = "insufficient_data"
    #: Zero closed trades anywhere - a plumbing signal, not a quality
    #: one (the strategy may be misconfigured or structurally blocked).
    NO_TRADES = "no_trades"


def load_gate_policy() -> Dict[str, Any]:
    """Read the standing gate policy from the environment.

    Env vars (with defaults): GATE_REFERENCE_SR=0.30, GATE_MIN_PF=1.3,
    GATE_MIN_PSR=0.95, GATE_MIN_TRADES_PER_SYMBOL=5,
    GATE_PF_CONFIDENCE=0, GATE_MAX_WINDOW_MONTHS=24, and the legacy
    GATE_MIN_TRADES which - when set - pins the POOLED trade
    requirement to a literal count instead of deriving it.

    Returns:
        Dict with min_closed_trades (pooled, derived unless pinned),
        min_trades_per_symbol, reference_sr, min_profit_factor, min_psr,
        min_consistent_symbols, pf_confidence, max_window_months.
    """
    reference_sr = float(os.getenv("GATE_REFERENCE_SR", str(DEFAULT_REFERENCE_SR)))
    min_psr = float(os.getenv("GATE_MIN_PSR", str(DEFAULT_MIN_PSR)))
    pinned = os.getenv("GATE_MIN_TRADES")
    if pinned is not None and pinned.strip():
        min_pooled = int(pinned)
    else:
        _, min_pooled = sample_adequacy(0, reference_sr, confidence=min_psr)
    return {
        "min_closed_trades": min_pooled,
        "min_trades_per_symbol": int(
            os.getenv(
                "GATE_MIN_TRADES_PER_SYMBOL", str(DEFAULT_MIN_TRADES_PER_SYMBOL)
            )
        ),
        "reference_sr": reference_sr,
        "min_profit_factor": float(os.getenv("GATE_MIN_PF", str(DEFAULT_MIN_PF))),
        "min_psr": min_psr,
        "min_consistent_symbols": MIN_CONSISTENT_SYMBOLS,
        "pf_confidence": os.getenv("GATE_PF_CONFIDENCE", "0").strip().lower()
        in ("1", "true", "yes", "on"),
        "max_window_months": int(
            os.getenv("GATE_MAX_WINDOW_MONTHS", str(DEFAULT_MAX_WINDOW_MONTHS))
        ),
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
        advisory: True when the check was computed but is NOT decisive
            (the sample was too small for it to mean anything).
            Advisory checks never make a verdict pass.
    """

    name: str
    passed: bool
    value: str
    threshold: str
    detail: str = ""
    advisory: bool = False


@dataclass
class GateVerdict:
    """Structured verdict of the validation gate.

    Attributes:
        strategy: Strategy the verdict applies to.
        symbols: Symbols evaluated.
        checks: Individual checks (all non-advisory ones must pass).
        passed: True only when outcome is PASS. Promotion flag.
        n_trials: Registry trial count used for DSR (None = unknown).
        outcome: GateOutcome - PASS/FAIL vs INSUFFICIENT_DATA/NO_TRADES.
        outcome_reason: One line explaining a non-PASS outcome.
        n_pooled: Closed trades pooled across every symbol.
        required_pooled: Pooled trades the policy requires.
        data_multiple_needed: How many times the observed data volume
            would be needed to reach required_pooled (None when the
            sample is already adequate or nothing traded).
    """

    strategy: str
    symbols: List[str]
    checks: List[GateCheck] = field(default_factory=list)
    passed: bool = False
    n_trials: Optional[int] = None
    outcome: GateOutcome = GateOutcome.FAIL
    outcome_reason: str = ""
    n_pooled: int = 0
    required_pooled: int = 0
    data_multiple_needed: Optional[float] = None


def required_window_months(
    observed_trades: int,
    observed_calendar_months: float,
    required_trades: Optional[int] = None,
    max_months: Optional[int] = None,
) -> Optional[int]:
    """Calendar months of data needed to reach an adequate sample.

    Extrapolated from the trade rate the strategy ACTUALLY showed, not
    from a hand-maintained table of per-strategy frequencies - a table
    like that would rot the moment a strategy's timeframe changed. The
    runner can call this after a short window comes back
    INSUFFICIENT_DATA and re-run over a longer one.

    Args:
        observed_trades: Closed trades seen (pooled across symbols).
        observed_calendar_months: Total calendar months the observation
            covered (windows * months_per_window, per symbol).
        required_trades: Pooled trades needed; defaults to the policy's
            derived requirement.
        max_months: Cap on the recommendation; defaults to the policy's
            GATE_MAX_WINDOW_MONTHS.

    Returns:
        Calendar months needed (int, >= observed_calendar_months), or
        None when nothing traded (no rate to extrapolate) or the window
        length is unknown.
    """
    policy = load_gate_policy()
    if required_trades is None:
        required_trades = int(policy["min_closed_trades"])
    if max_months is None:
        max_months = int(policy["max_window_months"])
    if observed_trades <= 0 or observed_calendar_months <= 0:
        return None
    if observed_trades >= required_trades:
        return int(math.ceil(observed_calendar_months))
    needed = observed_calendar_months * required_trades / observed_trades
    return int(min(max_months, math.ceil(needed)))


def _fmt_pf(value: float) -> str:
    """Format a profit factor, rendering an infinite one as 'inf'."""
    return "inf" if value == float("inf") else f"{value:.2f}"


def evaluate_strategy_gate(
    strategy: str,
    symbol_returns: Dict[str, List[float]],
    min_closed_trades: Optional[int] = None,
    min_pf: Optional[float] = None,
    min_psr: Optional[float] = None,
    n_trials: Optional[int] = None,
    sr_variance: Optional[float] = None,
    min_trades_per_symbol: Optional[int] = None,
    window_months: Optional[float] = None,
    n_windows: int = 1,
) -> GateVerdict:
    """Evaluate the standing validation gate for a strategy.

    The sample-adequacy check runs FIRST and decides whether the other
    three checks are decisive or merely advisory. An inadequate sample
    yields GateOutcome.INSUFFICIENT_DATA (or NO_TRADES) rather than a
    FAIL, so "we do not know yet" never reads as "this strategy is
    bad" - but ``passed`` stays False either way, so an inadequate
    sample can never promote anything.

    Args:
        strategy: Strategy name (snake_case).
        symbol_returns: Per-symbol lists of per-trade fractional
            returns (pnl / initial_capital) from untouched OOS data.
        min_closed_trades: Override for the POOLED trade requirement
            (normally derived from GATE_REFERENCE_SR / GATE_MIN_PSR).
        min_pf: Override for GATE_MIN_PF.
        min_psr: Override for GATE_MIN_PSR.
        n_trials: Total configurations tried (from the trial
            registry); None when unknown - the DSR leg is then skipped
            and only PSR can satisfy check 3.
        sr_variance: Observed variance of trial Sharpe values, when
            known (passed through to the DSR).
        min_trades_per_symbol: Override for GATE_MIN_TRADES_PER_SYMBOL,
            the floor a symbol must clear to vote on consistency.
        window_months: Months per validation window, when known - used
            only to turn an inadequate sample into a concrete
            "re-run over N months" recommendation.
        n_windows: Number of windows the returns were pooled from.

    Returns:
        GateVerdict with one GateCheck per policy rule and a
        GateOutcome distinguishing quality verdicts from evidence ones.
    """
    policy = load_gate_policy()
    min_pf = min_pf if min_pf is not None else policy["min_profit_factor"]
    min_psr = min_psr if min_psr is not None else policy["min_psr"]
    per_symbol_floor = (
        min_trades_per_symbol
        if min_trades_per_symbol is not None
        else int(policy["min_trades_per_symbol"])
    )
    if min_closed_trades is not None:
        required_pooled = int(min_closed_trades)
    else:
        _, required_pooled = sample_adequacy(
            0, float(policy["reference_sr"]), confidence=min_psr
        )

    symbols = list(symbol_returns.keys())
    verdict = GateVerdict(strategy=strategy, symbols=symbols, n_trials=n_trials)
    pooled: List[float] = []
    for returns in symbol_returns.values():
        pooled.extend(returns)
    verdict.n_pooled = len(pooled)
    verdict.required_pooled = required_pooled

    # --- Check 1: sample adequacy (pooled power + eligible symbols) ---
    counts = {sym: len(r) for sym, r in symbol_returns.items()}
    eligible = [s for s, c in counts.items() if c >= per_symbol_floor]
    pooled_ok = len(pooled) >= required_pooled
    symbols_ok = len(eligible) >= MIN_CONSISTENT_SYMBOLS
    adequate = pooled_ok and symbols_ok
    shortfalls: List[str] = []
    if not pooled_ok:
        shortfalls.append(f"pooled {len(pooled)} < {required_pooled}")
    if not symbols_ok:
        shortfalls.append(
            f"only {len(eligible)} symbol(s) with >= {per_symbol_floor} trades"
        )
    adequacy_detail = ", ".join(f"{s}: {c}" for s, c in counts.items())
    if shortfalls:
        adequacy_detail += " | short: " + "; ".join(shortfalls)
    verdict.checks.append(
        GateCheck(
            name="sample_adequacy",
            passed=adequate,
            value=f"{len(pooled)} pooled / {len(eligible)} eligible symbols",
            threshold=(
                f">= {required_pooled} pooled trades and "
                f">= {MIN_CONSISTENT_SYMBOLS} symbols with "
                f">= {per_symbol_floor} trades"
            ),
            detail=adequacy_detail,
        )
    )

    # Quality checks are advisory when the evidence cannot support them.
    advisory = not adequate

    # --- Check 2: pooled profit factor ---
    pf = _profit_factor(pooled)
    per_symbol_pf = {sym: _profit_factor(r) for sym, r in symbol_returns.items()}
    pf_detail = ", ".join(f"{s}: {_fmt_pf(v)}" for s, v in per_symbol_pf.items())
    pf_value = _fmt_pf(pf)
    pf_passed = pf > min_pf
    if policy["pf_confidence"]:
        # PF is a point estimate with no confidence interval: on four
        # trades a PF of 5.0 says nothing. Gate on the bootstrap lower
        # bound instead, which shrinks toward 1.0 as the sample shrinks.
        bound = bootstrap_profit_factor_bound(pooled, confidence=min_psr)
        if bound is not None:
            pf_passed = bound > min_pf
            pf_value = f"{_fmt_pf(pf)} (lower bound {_fmt_pf(bound)})"
            pf_detail += f" | {min_psr:.0%} one-sided bootstrap lower bound"
    verdict.checks.append(
        GateCheck(
            name="profit_factor",
            passed=pf_passed,
            value=pf_value,
            threshold=f"> {min_pf}",
            detail=pf_detail,
            advisory=advisory,
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
        f"{dsr_value:.4f}"
        if dsr_value is not None
        else ("n/a (N trials unknown)" if not n_trials or n_trials <= 1 else "n/a")
    )
    verdict.checks.append(
        GateCheck(
            name="psr_or_dsr",
            passed=psr_ok or dsr_ok,
            value=f"PSR {psr_s} / DSR {dsr_s}",
            threshold=f">= {min_psr}",
            detail=f"N trials = {n_trials if n_trials else 'unknown'}",
            advisory=advisory,
        )
    )

    # --- Check 4: cross-symbol consistency (eligible symbols only) ---
    positive = [
        sym
        for sym in eligible
        if symbol_returns[sym]
        and (sum(symbol_returns[sym]) / len(symbol_returns[sym])) > 0
    ]
    detail = "positive expectancy on: " + (", ".join(positive) or "none")
    if len(symbols) < MIN_CONSISTENT_SYMBOLS:
        detail += f" (only {len(symbols)} symbol(s) supplied)"
    elif len(eligible) < len(symbols):
        thin = [s for s in symbols if s not in eligible]
        detail += (
            f" (ignored, under {per_symbol_floor} trades: {', '.join(thin)})"
        )
    verdict.checks.append(
        GateCheck(
            name="cross_symbol_consistency",
            passed=len(positive) >= MIN_CONSISTENT_SYMBOLS,
            value=str(len(positive)),
            threshold=f">= {MIN_CONSISTENT_SYMBOLS} symbols with positive expectancy",
            detail=detail,
            advisory=advisory,
        )
    )

    _finalize_outcome(
        verdict,
        adequate=adequate,
        shortfalls=shortfalls,
        window_months=window_months,
        n_windows=n_windows,
        max_window_months=int(policy["max_window_months"]),
    )
    return verdict


def _finalize_outcome(
    verdict: GateVerdict,
    adequate: bool,
    shortfalls: List[str],
    window_months: Optional[float],
    n_windows: int,
    max_window_months: int,
) -> None:
    """Set outcome, reason, passed flag and the data-volume advice.

    Args:
        verdict: Verdict to complete (mutated in place).
        adequate: Whether the sample-adequacy check passed.
        shortfalls: Human-readable reasons adequacy failed.
        window_months: Months per window, when known.
        n_windows: Number of windows pooled.
        max_window_months: Ceiling on any window recommendation.
    """
    if verdict.n_pooled == 0:
        verdict.outcome = GateOutcome.NO_TRADES
        verdict.outcome_reason = (
            "no closed trades on any symbol - the strategy never traded, "
            "which is a configuration/plumbing question, not a quality one"
        )
    elif not adequate:
        verdict.outcome = GateOutcome.INSUFFICIENT_DATA
        if verdict.required_pooled > verdict.n_pooled:
            verdict.data_multiple_needed = round(
                verdict.required_pooled / verdict.n_pooled, 2
            )
        advice = ""
        if verdict.data_multiple_needed:
            advice = f" - needs ~{verdict.data_multiple_needed}x more data"
            if window_months:
                total = float(window_months) * max(1, n_windows)
                months = required_window_months(
                    verdict.n_pooled,
                    total,
                    required_trades=verdict.required_pooled,
                    max_months=max_window_months,
                )
                if months:
                    per_window = int(math.ceil(months / max(1, n_windows)))
                    advice += (
                        f" (about {months} calendar months, i.e. "
                        f"{n_windows}x{per_window}mo)"
                    )
        verdict.outcome_reason = (
            "sample too small to decide: "
            + "; ".join(shortfalls)
            + advice
            + " - lengthen the window rather than lowering the bar"
        )
    else:
        failed = [c.name for c in verdict.checks if not c.passed and not c.advisory]
        if failed:
            verdict.outcome = GateOutcome.FAIL
            verdict.outcome_reason = "failed on an adequate sample: " + ", ".join(
                failed
            )
        else:
            verdict.outcome = GateOutcome.PASS
            verdict.outcome_reason = ""
    verdict.passed = verdict.outcome is GateOutcome.PASS

    # Consumers that only see the serialized checks (the validation_runs
    # checks_json column, which carries name/passed/value/threshold/
    # detail and nothing else) would otherwise read INSUFFICIENT_DATA as
    # a plain FAIL. Fold the outcome and the advisory marks into the
    # detail strings so the distinction survives serialization.
    for check in verdict.checks:
        if check.name == "sample_adequacy":
            check.detail = f"outcome={verdict_label(verdict)} | {check.detail}"
            if verdict.outcome_reason:
                check.detail += f" | {verdict.outcome_reason}"
        elif check.advisory:
            check.detail = f"[advisory] {check.detail}".rstrip()


def verdict_label(verdict: GateVerdict) -> str:
    """Uppercase outcome label for tables and the validation_runs row.

    Args:
        verdict: Evaluated gate verdict.

    Returns:
        One of PASS, FAIL, INSUFFICIENT_DATA, NO_TRADES.
    """
    return verdict.outcome.value.upper()


def print_verdict(verdict: GateVerdict) -> None:
    """Print a gate verdict as a fixed-width table."""
    print(f"\n{'='*78}")
    print(
        f"VALIDATION GATE: {verdict.strategy} | "
        f"symbols: {', '.join(verdict.symbols)}"
    )
    print(f"{'='*78}")
    print(f"  {'Check':<26} {'Result':<9} {'Value':<28} Threshold")
    print(f"  {'-'*74}")
    for c in verdict.checks:
        tag = "PASS" if c.passed else "FAIL"
        if c.advisory:
            tag = f"{tag}*"
        print(f"  {c.name:<26} {tag:<9} {c.value:<28} {c.threshold}")
        if c.detail:
            print(f"  {'':<26} {'':<9} {c.detail}")
    print(f"  {'-'*74}")
    if any(c.advisory for c in verdict.checks):
        print("  * advisory - computed but not decisive on this sample size")
    print(f"  OVERALL: {verdict_label(verdict)}")
    if verdict.outcome_reason:
        print(f"  REASON:  {verdict.outcome_reason}")
    print(f"{'='*78}\n")


def _span_months(start: str, end: str) -> Optional[float]:
    """Calendar months between two ISO dates, or None if unparseable."""
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            a = datetime.strptime(start[:19], fmt)
            b = datetime.strptime(end[:19], fmt)
        except (ValueError, TypeError):
            continue
        days = (b - a).days
        return days / 30.44 if days > 0 else None
    return None


def main() -> int:
    """CLI: backtest each symbol and evaluate the gate.

    Returns:
        0 on PASS, 1 on FAIL, 2 on INSUFFICIENT_DATA/NO_TRADES so a
        "not enough evidence" run is distinguishable from a rejection.
    """
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
        window_months=_span_months(str(start), str(end)),
        n_windows=1,
    )
    print_verdict(verdict)
    if verdict.outcome in (GateOutcome.INSUFFICIENT_DATA, GateOutcome.NO_TRADES):
        return 2
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
