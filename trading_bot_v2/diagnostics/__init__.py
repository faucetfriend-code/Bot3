"""
Diagnostics
===========

Observability for the signal pipeline: why a strategy did (or did not)
trade, expressed as a counter chain rather than a bare zero.

Modules:
    funnel:   SignalFunnel / NullFunnel stage counters and the diagnose()
              classifier.
    outcomes: TrialOutcome enum and the reserved score bands used by the
              Optuna objective so zero-trade trials are ranked by how far
              down the funnel they got.
    report:   Human-readable rendering in the validation-gate house style.
    explain:  CLI over stored Optuna trial user_attrs.
"""

from .funnel import (
    NULL_FUNNEL,
    NullFunnel,
    SignalFunnel,
    STAGES,
)
from .outcomes import SCORE_BANDS, TrialOutcome, score_for_outcome

__all__ = [
    "NULL_FUNNEL",
    "NullFunnel",
    "SignalFunnel",
    "STAGES",
    "SCORE_BANDS",
    "TrialOutcome",
    "score_for_outcome",
]
