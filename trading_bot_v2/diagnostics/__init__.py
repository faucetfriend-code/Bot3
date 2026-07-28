"""
Diagnostics
===========

Observability for the signal pipeline: why a strategy did (or did not)
trade, expressed as a counter chain rather than a bare zero.

Modules:
    funnel:       SignalFunnel / NullFunnel stage counters and the
                  diagnose() classifier.
    outcomes:     TrialOutcome enum and the reserved score bands used by
                  the Optuna objective so zero-trade trials are ranked by
                  how far down the funnel they got.
    gate_metrics: The describe_gate_metrics() hook contract, the observed
                  distribution artifacts, and the feasibility verdicts
                  that catch a threshold above its metric's ceiling.
    calibrate:    CLI that replays real candles to build those artifacts.
    report:       Human-readable rendering in the validation-gate house
                  style.
    explain:      CLI over stored Optuna trial user_attrs.
"""

from .funnel import (
    NULL_FUNNEL,
    NullFunnel,
    SignalFunnel,
    STAGES,
)
from .gate_metrics import (
    GATE_AT_LEAST,
    GATE_AT_MOST,
    GATE_IN_BAND,
    NULL_GATE_METRICS,
    GateMetric,
    GateMetricCollector,
    NullGateMetricCollector,
    ThresholdVerdict,
    load_calibrations,
    near_miss,
    threshold_verdicts,
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
    "GATE_AT_LEAST",
    "GATE_AT_MOST",
    "GATE_IN_BAND",
    "NULL_GATE_METRICS",
    "GateMetric",
    "GateMetricCollector",
    "NullGateMetricCollector",
    "ThresholdVerdict",
    "load_calibrations",
    "near_miss",
    "threshold_verdicts",
]
