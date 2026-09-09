"""
Trial Outcomes and Banded Scoring
=================================

Why bands and not pruning
-------------------------

The obvious fix for zero-trade Optuna trials is to prune them so the flat
score landscape stops teaching TPE nothing. That is wrong: pruning deletes
the points, leaving TPE with no signal about the region and free to
resample it (``TPESampler`` defaults to ``consider_pruned_trials=False``).

The fix is a reserved score band, monotone in funnel depth. TPE is
rank-based, so absolute magnitudes are irrelevant and disjoint bands are
safe. Within a band, ``+ progress`` orders trials by how far down the
pipeline they got, so "fired but everything was discarded at the last
gate" ranks above "never fired at all".

Only structurally infeasible params are pruned, because there the correct
action is to never burn the backtest at all.

Band layout (each band is 1.0 wide because progress() < 1.0):

    outcome                     Optuna state   score
    --------------------------  -------------  ---------------------------
    traded                      COMPLETE       max(-50.0, objective)
    all_discarded_downstream    COMPLETE       -100.0 + progress
    no_opportunities            COMPLETE       -200.0 + progress
    structurally_blocked        COMPLETE       -200.0 + progress
    never_invoked               COMPLETE       -300.0 + progress
    no_data                     COMPLETE       -400.0 + progress
    infeasible_config           PRUNED         n/a (pre-backtest)
    (backtest raised)           FAIL           n/a - never -inf
"""

from enum import Enum
from typing import Any, Dict, Optional

from .funnel import (
    DIAGNOSIS_ALL_DISCARDED,
    DIAGNOSIS_NEVER_INVOKED,
    DIAGNOSIS_NO_DATA,
    DIAGNOSIS_NO_OPPORTUNITIES,
    DIAGNOSIS_STRUCTURALLY_BLOCKED,
    DIAGNOSIS_TRADED,
)


class TrialOutcome(str, Enum):
    """What actually happened in an optimization trial."""

    TRADED = DIAGNOSIS_TRADED
    ALL_DISCARDED_DOWNSTREAM = DIAGNOSIS_ALL_DISCARDED
    NO_OPPORTUNITIES = DIAGNOSIS_NO_OPPORTUNITIES
    STRUCTURALLY_BLOCKED = DIAGNOSIS_STRUCTURALLY_BLOCKED
    NEVER_INVOKED = DIAGNOSIS_NEVER_INVOKED
    NO_DATA = DIAGNOSIS_NO_DATA
    #: Params that could never trade - pruned before the backtest runs.
    INFEASIBLE_CONFIG = "infeasible_config"

    @classmethod
    def from_diagnosis(cls, diagnosis: str) -> "TrialOutcome":
        """Map a funnel diagnosis string to an outcome.

        Args:
            diagnosis: Value returned by ``SignalFunnel.diagnose()``.

        Returns:
            The matching TrialOutcome, or NO_DATA when unrecognised.
        """
        try:
            return cls(diagnosis)
        except ValueError:
            return cls.NO_DATA


#: Score floor reserved for real (traded) results. Traded objectives are
#: clamped here so a genuinely terrible Sharpe can never fall into the
#: zero-trade bands and be mistaken for one.
TRADED_SCORE_FLOOR = -50.0

#: Reserved band base per outcome. progress() in [0, 1) is added on top.
SCORE_BANDS: Dict[TrialOutcome, float] = {
    TrialOutcome.TRADED: TRADED_SCORE_FLOOR,
    TrialOutcome.ALL_DISCARDED_DOWNSTREAM: -100.0,
    TrialOutcome.NO_OPPORTUNITIES: -200.0,
    TrialOutcome.STRUCTURALLY_BLOCKED: -200.0,
    TrialOutcome.NEVER_INVOKED: -300.0,
    TrialOutcome.NO_DATA: -400.0,
}


#: Width of the near-miss refinement, in units of the within-band offset.
#: progress() moves in steps of 1/(len(DEPTH_LADDER) + 1) = 0.1, so a
#: near-miss can never promote a trial past a trial that got a whole
#: milestone further down the funnel - it only orders trials that stalled
#: at the SAME depth.
NEAR_MISS_WEIGHT = 0.1


def score_for_outcome(
    outcome: TrialOutcome,
    progress: float = 0.0,
    objective_value: Optional[float] = None,
    near_miss: Optional[float] = None,
) -> float:
    """Return the Optuna trial value for an outcome.

    Args:
        outcome: Classified trial outcome.
        progress: Funnel depth in [0, 1) - added to the band base so
            deeper trials rank higher within their band.
        objective_value: The real objective, required for TRADED.
        near_miss: Optional gradient in [0, 1) from
            :func:`trading_bot_v2.diagnostics.gate_metrics.near_miss`,
            saying how close the tightest gate came to opening. Without
            it every "never fired" trial in a region scores identically
            and TPE learns nothing from the region; with it, a threshold
            of 4.037 against a metric that reached 3.95 ranks above one
            that needed 100.

    Returns:
        A finite float. Never ``-inf`` (which would poison
        ``study.best_value`` and hide failures as COMPLETE).

    Raises:
        ValueError: If the outcome is INFEASIBLE_CONFIG, which has no
            score - it is pruned before the backtest runs.
    """
    if outcome is TrialOutcome.INFEASIBLE_CONFIG:
        raise ValueError("infeasible_config trials are pruned, not scored")

    if outcome is TrialOutcome.TRADED:
        if objective_value is None:
            raise ValueError("TRADED outcome requires an objective_value")
        value = float(objective_value)
        if value != value:  # NaN
            return TRADED_SCORE_FLOOR
        return max(TRADED_SCORE_FLOOR, value)

    base = SCORE_BANDS.get(outcome, SCORE_BANDS[TrialOutcome.NO_DATA])
    bounded = min(max(float(progress), 0.0), 0.999)
    if near_miss is not None:
        gradient = min(max(float(near_miss), 0.0), 0.999)
        bounded = min(bounded + gradient * NEAR_MISS_WEIGHT, 0.999)
    return base + bounded


def suggest_fix(
    funnel_dict: Dict[str, Any], params: Optional[Dict[str, Any]] = None
) -> str:
    """Produce an actionable one-line fix for a zero-trade funnel.

    Args:
        funnel_dict: ``SignalFunnel.to_dict()`` payload.
        params: The trial's sampled parameters, when available.

    Returns:
        A suggestion string; empty when nothing specific can be said.
    """
    params = params or {}
    stages = funnel_dict.get("stages") or {}
    binding = funnel_dict.get("binding_stage") or ""
    top = funnel_dict.get("top_reasons") or []
    top_reason = top[0][0] if top else ""

    if top_reason == "validity:rrr_meets_minimum":
        stop = params.get("atr_stop_mult")
        target = params.get("atr_target_mult")
        if stop and target:
            return (
                f"atr_target_mult/atr_stop_mult must clear the strategy's "
                f"min RRR; sampled {target:.2f}/{stop:.2f} = "
                f"{target / stop:.2f}"
            )
        return (
            "every signal failed rrr_meets_minimum - raise the take-profit "
            "distance or lower the strategy's min_rrr"
        )

    if top_reason.startswith("validity:"):
        flag = top_reason.split(":", 1)[1]
        return (
            f"every signal failed the {flag} validity flag - relax that gate "
            f"or fix the strategy so it stops emitting signals that cannot pass"
        )

    if top_reason.startswith("confidence:"):
        return (
            "signals were emitted but fell under the regime confidence "
            "threshold - lower MIN_SIGNAL_CONFIDENCE_FLOOR / the regime "
            "adjustment, or raise strategy confidence"
        )

    if top_reason.startswith("structural:"):
        return (
            f"a gate is unreachable on this data ({top_reason}) - the "
            f"threshold sits above the metric's observed range"
        )

    if top_reason.startswith("exec:"):
        return (
            f"signals reached execution but were blocked ({top_reason}) - "
            f"check hedge mode, min-hold candles and position sizing"
        )

    if binding == "raw_signals":
        invoked = stages.get("strategy_invoked", 0)
        return (
            f"the strategy ran on {invoked} bars and never emitted a signal - "
            f"loosen its entry thresholds or verify the gate is reachable"
        )

    if binding == "strategy_invoked":
        return (
            "the regime filter never selected this strategy - check the "
            "regime-to-strategy mapping for the regimes in this window"
        )

    if binding == "bars_evaluated":
        return "no bars were evaluated - check the data window and warmup"

    return ""
