"""
Validation Statistics (P5)
==========================

Pure statistical functions for judging whether a backtest result is
signal or a fluke of the search size.

All Sharpe ratios here are NON-annualized unless explicitly requested:
for a per-trade return series the natural unit is "Sharpe per trade",
and the PSR/DSR formulas below are defined on the same (non-annualized)
scale as the input series.

Moment conventions: population moments (ddof=0) are used for the
standard deviation, skewness and kurtosis, matching the plug-in
estimators in Bailey & Lopez de Prado (2012). Kurtosis is the
NON-excess form (normal distribution -> 3.0).

References:
    Bailey, D. H. and Lopez de Prado, M. (2012). "The Sharpe Ratio
    Efficient Frontier." Journal of Risk 15(2). (PSR, MinTRL)
    Bailey, D. H. and Lopez de Prado, M. (2014). "The Deflated Sharpe
    Ratio: Correcting for Selection Bias, Backtest Overfitting and
    Non-Normality." Journal of Portfolio Management 40(5). (DSR,
    expected max Sharpe)
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from scipy.stats import norm

# Euler-Mascheroni constant (used by the expected-max-Sharpe formula)
EULER_GAMMA = 0.5772156649015329


@dataclass
class PSRResult:
    """Result of a Probabilistic Sharpe Ratio computation.

    Attributes:
        value: PSR in [0, 1], or None when not computable.
        reason: Why the value is None (None when computable).
        sr: Non-annualized Sharpe ratio of the input series.
        benchmark_sr: Benchmark Sharpe the series was tested against.
        n: Number of observations in the series.
    """

    value: Optional[float]
    reason: Optional[str] = None
    sr: Optional[float] = None
    benchmark_sr: float = 0.0
    n: int = 0


@dataclass
class DSRResult:
    """Result of a Deflated Sharpe Ratio computation.

    Attributes:
        value: DSR in [0, 1], or None when not computable.
        benchmark_sr: The expected-max-Sharpe benchmark that was used.
        n_trials: Number of trials the benchmark was deflated for.
        var_sharpe: Variance of trial Sharpe ratios used for the
            benchmark (may be the documented fallback estimate).
        var_fallback: True when var_sharpe came from the 1/(n-1)
            heuristic rather than observed trial variance.
        passed: True when value >= 0.95, False when below, None when
            the value is not computable.
        reason: Why the value is None (None when computable).
    """

    value: Optional[float]
    benchmark_sr: float = 0.0
    n_trials: int = 1
    var_sharpe: float = 0.0
    var_fallback: bool = False
    passed: Optional[bool] = None
    reason: Optional[str] = None


def _moments(returns: Sequence[float]):
    """Return (mean, std, skew, kurt) population moments of a series.

    Kurtosis is non-excess (normal -> 3.0). Skew/kurt are 0.0/3.0 when
    the variance is zero (they are undefined; callers guard on std).

    Args:
        returns: Return observations.

    Returns:
        Tuple (mean, std, skewness, kurtosis).
    """
    n = len(returns)
    mean = sum(returns) / n
    m2 = sum((r - mean) ** 2 for r in returns) / n
    std = math.sqrt(m2)
    if m2 <= 0:
        return mean, 0.0, 0.0, 3.0
    m3 = sum((r - mean) ** 3 for r in returns) / n
    m4 = sum((r - mean) ** 4 for r in returns) / n
    skew = m3 / m2**1.5
    kurt = m4 / m2**2
    return mean, std, skew, kurt


def sharpe_ratio(
    returns: Sequence[float],
    periods_per_year: Optional[float] = None,
) -> float:
    """Plain Sharpe ratio of a return series (risk-free rate = 0).

    SR = mean(returns) / std(returns), with population std (ddof=0).
    Annualization is optional and OFF by default: for a per-trade
    series the non-annualized "Sharpe per trade" is the quantity the
    PSR/DSR formulas operate on.

    Formula source: Sharpe (1994), "The Sharpe Ratio", as restated in
    Bailey & Lopez de Prado (2012) eq. (1).

    Args:
        returns: Per-trade (or per-period) return observations.
        periods_per_year: When given, multiply by sqrt(periods_per_year)
            to annualize (e.g. 252 for daily returns).

    Returns:
        Sharpe ratio; 0.0 when fewer than 2 observations or zero
        variance.
    """
    if len(returns) < 2:
        return 0.0
    mean, std, _, _ = _moments(returns)
    if std <= 0:
        return 0.0
    sr = mean / std
    if periods_per_year is not None:
        sr *= math.sqrt(periods_per_year)
    return sr


def probabilistic_sharpe_ratio(
    returns: Sequence[float],
    benchmark_sr: float = 0.0,
) -> PSRResult:
    """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012).

    PSR(SR*) = Phi( ((sr - SR*) * sqrt(n - 1))
                    / sqrt(1 - skew*sr + ((kurt - 1) / 4) * sr^2) )

    where sr is the NON-annualized Sharpe of the series, skew and kurt
    are the sample skewness and non-excess kurtosis, n = len(returns),
    and Phi is the standard normal CDF. PSR is the probability that the
    true Sharpe exceeds benchmark_sr given estimation error and
    non-normality.

    Formula source: Bailey & Lopez de Prado (2012), eq. (11).

    Args:
        returns: Per-trade (or per-period) return observations.
        benchmark_sr: Benchmark Sharpe SR* on the same scale as sr.

    Returns:
        PSRResult; value is None (with a reason) when n < 3, the
        variance is zero, or the denominator term is non-positive.
    """
    n = len(returns)
    if n < 3:
        return PSRResult(
            value=None, reason=f"insufficient observations (n={n} < 3)", n=n,
            benchmark_sr=benchmark_sr,
        )
    _, std, skew, kurt = _moments(returns)
    if std <= 0:
        return PSRResult(
            value=None, reason="zero variance in returns", n=n,
            benchmark_sr=benchmark_sr,
        )
    sr = sharpe_ratio(returns)
    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr**2
    if denom_sq <= 0:
        return PSRResult(
            value=None,
            reason=(
                f"non-positive variance term ({denom_sq:.6f}) - "
                "skew/kurtosis too extreme for the PSR approximation"
            ),
            sr=sr,
            benchmark_sr=benchmark_sr,
            n=n,
        )
    stat = (sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom_sq)
    value = float(norm.cdf(stat))
    return PSRResult(
        value=value, reason=None, sr=sr, benchmark_sr=benchmark_sr, n=n
    )


def expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """Expected maximum Sharpe ratio across n_trials random trials.

    E[max SR] ~= sqrt(var_sharpe)
                 * ((1 - gamma) * z(1 - 1/N) + gamma * z(1 - 1/(N*e)))

    where gamma is the Euler-Mascheroni constant, z is the standard
    normal PPF (quantile function), and N = n_trials. This is the DSR
    benchmark: the Sharpe you would expect the BEST of N skill-less
    strategies to show purely from selection.

    Formula source: Bailey & Lopez de Prado (2014), "The Deflated
    Sharpe Ratio", eq. for E[max_n SR] (their eq. 6, from Embrechts
    et al. extreme-value approximation).

    Args:
        n_trials: Number of independent trials/configurations tried.
        var_sharpe: Variance of the Sharpe estimates across trials.

    Returns:
        Expected max Sharpe; 0.0 when n_trials <= 1 or var_sharpe <= 0.
    """
    if n_trials <= 1 or var_sharpe <= 0:
        return 0.0
    z1 = float(norm.ppf(1.0 - 1.0 / n_trials))
    z2 = float(norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
    return math.sqrt(var_sharpe) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe_ratio(
    returns: Sequence[float],
    n_trials: int,
    var_sharpe_across_trials: Optional[float] = None,
) -> DSRResult:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014).

    DSR = PSR(SR*) with SR* = expected_max_sharpe(n_trials, var), i.e.
    the PSR evaluated against the Sharpe that pure selection over
    n_trials configurations would produce. DSR >= 0.95 means less than
    5% probability that the observed Sharpe is a fluke of the search
    size.

    Variance fallback: when var_sharpe_across_trials is None (the trial
    Sharpe values were not recorded), the variance is estimated with
    the 1/(n-1) heuristic, where n = len(returns). This is the
    estimation variance of a single SR~0 estimate (Var[SR-hat] ~ 1/n
    for small SR) and is a CONSERVATIVE stand-in, not the observed
    cross-trial dispersion; results using it are marked with
    var_fallback=True.

    Args:
        returns: OOS per-trade (or per-period) return observations.
        n_trials: Total number of configurations tried while arriving
            at this strategy (from the trial registry).
        var_sharpe_across_trials: Variance of the trial Sharpe values
            when available.

    Returns:
        DSRResult with the deflated value, the benchmark used, and a
        pass flag (value >= 0.95).
    """
    n = len(returns)
    var_fallback = False
    if var_sharpe_across_trials is None:
        if n < 2:
            return DSRResult(
                value=None,
                n_trials=n_trials,
                reason=f"insufficient observations (n={n} < 2)",
            )
        var_sharpe = 1.0 / (n - 1)
        var_fallback = True
    else:
        var_sharpe = var_sharpe_across_trials

    benchmark = expected_max_sharpe(n_trials, var_sharpe)
    psr = probabilistic_sharpe_ratio(returns, benchmark_sr=benchmark)
    passed = None if psr.value is None else psr.value >= 0.95
    return DSRResult(
        value=psr.value,
        benchmark_sr=benchmark,
        n_trials=n_trials,
        var_sharpe=var_sharpe,
        var_fallback=var_fallback,
        passed=passed,
        reason=psr.reason,
    )


def min_track_record_length(
    returns: Sequence[float],
    benchmark_sr: float,
    confidence: float = 0.95,
) -> Optional[float]:
    """Minimum track record length (Bailey & Lopez de Prado 2012).

    MinTRL = 1 + (1 - skew*sr + ((kurt - 1) / 4) * sr^2)
                 * (z(confidence) / (sr - SR*))^2

    The number of observations needed before the observed Sharpe sr is
    statistically above benchmark SR* at the given confidence.

    Formula source: Bailey & Lopez de Prado (2012), eq. (13).

    Args:
        returns: Per-trade (or per-period) return observations.
        benchmark_sr: Benchmark Sharpe SR* on the same scale as sr.
        confidence: Required confidence level (default 0.95).

    Returns:
        Required observation count (float), or None when sr <=
        benchmark_sr, n < 3, or the variance term is non-positive.
    """
    n = len(returns)
    if n < 3:
        return None
    _, std, skew, kurt = _moments(returns)
    if std <= 0:
        return None
    sr = sharpe_ratio(returns)
    if sr <= benchmark_sr:
        return None
    var_term = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr**2
    if var_term <= 0:
        return None
    z_conf = float(norm.ppf(confidence))
    return 1.0 + var_term * (z_conf / (sr - benchmark_sr)) ** 2


def closed_trade_returns(
    trade_log: List[dict], initial_capital: float
) -> List[float]:
    """Extract per-trade fractional returns from a backtest trade log.

    Closing fills carry pnl != 0 (opening fills have pnl == 0), the
    same convention PerformanceTracker uses for closed-trade stats.

    Args:
        trade_log: BacktestResult.trade_log rows (dicts with "pnl").
        initial_capital: Capital base used to express pnl as a return.

    Returns:
        List of pnl / initial_capital for each closed trade, in order.
    """
    if initial_capital <= 0:
        return []
    return [
        float(t.get("pnl", 0)) / initial_capital
        for t in trade_log
        if t.get("pnl", 0) != 0
    ]
