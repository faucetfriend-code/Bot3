"""Pure performance-metric functions over trade return series.

Single source of truth for the metrics math previously duplicated in
``strategy_monitor`` (profit factor, max drawdown) and ``adaptive_weights``
(expectancy, recency-weighted expectancy).  Every function here is pure:
no database access, no logging, no globals.

Conventions:
    - ``returns`` are per-trade values, time-ordered oldest-first where
      order matters (max drawdown).  They may be percentage returns
      (pnl_pct) or dollar PnL depending on the caller; the math is
      agnostic except for :func:`max_drawdown_pct`, which compounds the
      values as percentages.
    - Profit factor is ``None`` when undefined (no trades, or no losing
      trades - an infinite PF is not JSON-serialisable) and ``0.0`` when
      there are losses but no wins.  This matches the semantics of the
      original ``StrategyMonitor._compute_profit_factor``.
"""

from typing import Optional, Sequence, Tuple

__all__ = [
    "profit_factor",
    "max_drawdown_pct",
    "win_rate",
    "expectancy",
    "recency_weighted_expectancy",
    "trade_pnl_pct",
]


def profit_factor(returns: Sequence[float]) -> Optional[float]:
    """Compute profit factor (gross profit / gross loss).

    Args:
        returns: Per-trade returns (pnl_pct or dollar PnL).

    Returns:
        ``None`` when there are no trades or no losing trades (an
        undefined/infinite profit factor is not JSON-serialisable),
        ``0.0`` when there are losses but no winning trades, otherwise
        the gross-profit / gross-loss ratio.
    """
    if not returns:
        return None
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    if gross_loss == 0:
        return None
    if gross_profit == 0:
        return 0.0
    return gross_profit / gross_loss


def max_drawdown_pct(returns: Sequence[float]) -> float:
    """Compute maximum drawdown from time-ordered percentage returns.

    Compounds an equity curve starting at 100.0 (``eq *= 1 + r / 100``
    per return), tracks the running peak, and returns the largest
    peak-to-trough decline as a positive percentage.

    Args:
        returns: Per-trade percentage returns, oldest first.

    Returns:
        Max drawdown as a positive percentage; ``0.0`` for an empty list.
    """
    if not returns:
        return 0.0
    equity = 100.0
    peak = equity
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r / 100.0
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd * 100.0


def win_rate(returns: Sequence[float]) -> float:
    """Compute the fraction of strictly positive returns.

    Args:
        returns: Per-trade returns.

    Returns:
        Wins / total in [0, 1]; ``0.0`` for an empty list.
    """
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    return wins / len(returns)


def expectancy(returns: Sequence[float]) -> float:
    """Compute the plain (unweighted) mean return per trade.

    Args:
        returns: Per-trade returns.

    Returns:
        Arithmetic mean; ``0.0`` for an empty list.
    """
    if not returns:
        return 0.0
    return sum(returns) / len(returns)


def recency_weighted_expectancy(
    samples: Sequence[Tuple[float, float]], half_life_days: float
) -> float:
    """Compute a recency-weighted mean return with exponential decay.

    Each sample is weighted ``0.5 ** (age_days / half_life_days)`` so a
    trade exactly one half-life old counts half as much as a trade made
    now.  Falls back to the plain mean when the total weight is zero
    (matches the original ``AdaptiveWeightManager`` semantics).

    Args:
        samples: ``(return, age_days)`` pairs; ``return`` is typically
            pnl_pct and ``age_days`` is non-negative.
        half_life_days: Decay half-life in days (must be > 0).

    Returns:
        Weighted mean return; ``0.0`` for an empty sequence.
    """
    if not samples:
        return 0.0
    lifetime = sum(r for r, _ in samples) / len(samples)
    weight_sum = 0.0
    weighted = 0.0
    for value, age_days in samples:
        w = 0.5 ** (age_days / half_life_days)
        weight_sum += w
        weighted += value * w
    return weighted / weight_sum if weight_sum > 0 else lifetime


def trade_pnl_pct(
    pnl: Optional[float],
    entry_price: Optional[float],
    quantity: Optional[float],
) -> Optional[float]:
    """Derive percentage PnL from dollar PnL and entry notional.

    pnl_pct = pnl / (entry_price * quantity) * 100.

    Args:
        pnl: Dollar PnL of the trade (``None`` treated as 0.0).
        entry_price: Entry price (``None`` treated as 0.0).
        quantity: Trade quantity (``None`` treated as 0.0).

    Returns:
        Percentage PnL, or ``None`` when the notional is non-positive or
        any value fails float conversion.  Callers decide whether a
        ``None`` means "skip the trade" (adaptive weights) or "count as
        0.0" (regime attribution).
    """
    try:
        pnl_val = float(pnl) if pnl is not None else 0.0
        notional = float(entry_price or 0.0) * float(quantity or 0.0)
    except (TypeError, ValueError):
        return None
    if notional <= 0:
        return None
    return (pnl_val / notional) * 100.0
