"""
Validation Stack (P5)
=====================

Statistical validation utilities for strategy results:

- statistics: Sharpe ratio, Probabilistic Sharpe Ratio (PSR), Deflated
  Sharpe Ratio (DSR), expected max Sharpe under multiple testing, and
  minimum track record length (MinTRL).
- gate: standing go/no-go policy for promoting a strategy
  (trade count, profit factor, PSR/DSR, cross-symbol consistency).

References:
    Bailey, D. H. and Lopez de Prado, M. (2012). "The Sharpe Ratio
    Efficient Frontier." Journal of Risk 15(2).
    Bailey, D. H. and Lopez de Prado, M. (2014). "The Deflated Sharpe
    Ratio: Correcting for Selection Bias, Backtest Overfitting and
    Non-Normality." Journal of Portfolio Management 40(5).
"""

from .statistics import (
    DSRResult,
    PSRResult,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)

__all__ = [
    "DSRResult",
    "PSRResult",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "min_track_record_length",
    "probabilistic_sharpe_ratio",
    "sharpe_ratio",
]
