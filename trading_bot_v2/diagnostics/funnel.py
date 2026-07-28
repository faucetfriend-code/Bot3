"""
Signal Funnel
=============

A per-run counter chain over the signal pipeline, so "0 trades" stops
being a single undifferentiated answer.

Three completely different situations used to print the same thing:

  * a strategy that legitimately found no opportunities,
  * one that was structurally incapable of firing (a threshold above the
    metric's mathematical ceiling),
  * one that fired constantly and had every signal thrown away downstream.

The funnel separates them by counting every stage the pipeline passes
through and every reason a candidate was dropped, then classifying the
shape of the resulting chain (see :meth:`SignalFunnel.diagnose`).

Hot-path discipline (non-negotiable):
    * Rejection reasons are interned module constants - NEVER f-strings.
      A per-bar f-string allocates, and this runs tens of thousands of
      times per backtest.
    * No I/O, no logging, no formatting inside any counting method.
    * :class:`NullFunnel` is the default everywhere, so the live bot pays
      one attribute lookup and one no-op call per stage.

Usage:
    funnel = SignalFunnel(label="momentum_scalping/SUI-USDC")
    funnel.count(STAGE_BARS_EVALUATED)
    funnel.count_strategy("momentum_scalping", STAGE_RAW_SIGNALS)
    funnel.reject(REASON_VALIDITY["rrr_meets_minimum"], strategy="momentum")
    funnel.diagnose()   # -> "all_discarded_downstream"
"""

from typing import Any, Dict, List, Optional, Tuple

from ..models import SIGNAL_VALIDITY_FLAGS

# ---------------------------------------------------------------------------
# Stage names (interned constants - use these, never string literals)
# ---------------------------------------------------------------------------

STAGE_BARS_EVALUATED = "bars_evaluated"
STAGE_BARS_SKIPPED_WARMUP = "bars_skipped_warmup"
STAGE_DATA_REJECTED = "data_rejected"
STAGE_REGIME_BLOCKED = "regime_blocked"
STAGE_STRATEGY_INVOKED = "strategy_invoked"
STAGE_RAW_SIGNALS = "raw_signals"
STAGE_CONFIDENCE_DROPPED = "confidence_dropped"
STAGE_CONFLICT_DROPPED = "conflict_dropped"
STAGE_VALIDITY_DROPPED = "validity_dropped"
STAGE_EXECUTION_BLOCKED = "execution_blocked"
STAGE_ORDERS_PLACED = "orders_placed"
STAGE_FILLS = "fills"
STAGE_CLOSED_TRADES = "closed_trades"

#: Canonical stage order (also the order used when rendering reports).
STAGES: Tuple[str, ...] = (
    STAGE_BARS_EVALUATED,
    STAGE_BARS_SKIPPED_WARMUP,
    STAGE_DATA_REJECTED,
    STAGE_REGIME_BLOCKED,
    STAGE_STRATEGY_INVOKED,
    STAGE_RAW_SIGNALS,
    STAGE_CONFIDENCE_DROPPED,
    STAGE_CONFLICT_DROPPED,
    STAGE_VALIDITY_DROPPED,
    STAGE_EXECUTION_BLOCKED,
    STAGE_ORDERS_PLACED,
    STAGE_FILLS,
    STAGE_CLOSED_TRADES,
)

#: Stages that represent candidates being thrown away, in pipeline order.
DROP_STAGES: Tuple[str, ...] = (
    STAGE_CONFIDENCE_DROPPED,
    STAGE_CONFLICT_DROPPED,
    STAGE_VALIDITY_DROPPED,
    STAGE_EXECUTION_BLOCKED,
)

#: Depth ladder used by progress(): each milestone reached moves a
#: zero-trade trial's score up within its reserved band, so TPE can tell
#: "never fired" from "fired but was blocked at the last step" by rank.
DEPTH_LADDER: Tuple[str, ...] = (
    "bars_evaluated",
    "strategy_invoked",
    "raw_signals",
    "confidence_survived",
    "conflict_survived",
    "validity_survived",
    "orders_placed",
    "fills",
    "closed_trades",
)

# ---------------------------------------------------------------------------
# Rejection reasons (interned - NEVER build these with f-strings)
# ---------------------------------------------------------------------------

REASON_DATA_QUALITY = "data:quality_check_failed"
REASON_NO_REGIME_DATA = "data:no_regime_timeframe"
REASON_NO_ACTIVE_STRATEGIES = "regime:no_active_strategies"
REASON_STRATEGY_MISSING = "strategy:not_initialized"
REASON_STRATEGY_EXCEPTION = "strategy:exception"
REASON_NO_ORDERBOOK = "strategy:no_orderbook_data"
REASON_CONFIDENCE_GATE = "confidence:below_regime_threshold"
REASON_CONFLICT_RESOLUTION = "conflict:lost_resolution"

# Execution-layer early returns in backtesting/engine.py::_execute_signal.
# Nobody counted these before; each is a plausible silent-zero cause.
REASON_EXEC_NO_PRICE = "exec:no_price"
REASON_EXEC_SAME_DIRECTION = "exec:same_direction_skip"
REASON_EXEC_HEDGE_MODE = "exec:hedge_mode_block"
REASON_EXEC_MIN_HOLD = "exec:min_hold_block"
REASON_EXEC_QTY_NON_POSITIVE = "exec:qty_non_positive"
REASON_EXEC_EXCEPTION = "exec:exception"

#: validity flag name -> interned reason string, built once at import.
REASON_VALIDITY: Dict[str, str] = {
    flag: "validity:" + flag for flag in SIGNAL_VALIDITY_FLAGS
}

#: Prefix marking a declared structural block (a gate that no observed
#: value could ever pass). Used by diagnose() to separate
#: "structurally_blocked" from "no_opportunities".
STRUCTURAL_PREFIX = "structural:"

#: Reasons kept as data but excluded from top_reasons() ranking. These
#: describe a deliberate configuration state rather than a candidate
#: being thrown away - a single-strategy backtest logs one per bar per
#: disabled strategy, which would otherwise bury the actionable reason.
NON_RANKING_REASONS = frozenset({REASON_STRATEGY_MISSING})

# ---------------------------------------------------------------------------
# Diagnoses
# ---------------------------------------------------------------------------

DIAGNOSIS_TRADED = "traded"
DIAGNOSIS_NO_OPPORTUNITIES = "no_opportunities"
DIAGNOSIS_STRUCTURALLY_BLOCKED = "structurally_blocked"
DIAGNOSIS_ALL_DISCARDED = "all_discarded_downstream"
DIAGNOSIS_NEVER_INVOKED = "never_invoked"
DIAGNOSIS_NO_DATA = "no_data"


class SignalFunnel:
    """Stage counters for one backtest / optimization run.

    All counting methods are allocation-free on the common path: they do
    a dict lookup and an integer add. Nothing here logs or formats.

    Attributes:
        label: Free-form run label (strategy/symbol), for reports only.
        stages: Stage name -> count.
        reasons: Interned rejection reason -> count.
        by_strategy: Strategy name -> {stage: count}.
        regimes: Regime value -> bars observed in that regime.
        notes: Free-form structured extras (e.g. gate metrics), JSON-safe.
    """

    enabled = True

    __slots__ = ("label", "stages", "reasons", "by_strategy", "regimes", "notes")

    def __init__(self, label: str = "") -> None:
        """Initialize an empty funnel.

        Args:
            label: Free-form run label used in reports.
        """
        self.label = label
        self.stages: Dict[str, int] = {}
        self.reasons: Dict[str, int] = {}
        self.by_strategy: Dict[str, Dict[str, int]] = {}
        self.regimes: Dict[str, int] = {}
        self.notes: Dict[str, Any] = {}

    # -- counting (hot path) ------------------------------------------------

    def count(self, stage: str, n: int = 1) -> None:
        """Add ``n`` to a stage counter.

        Args:
            stage: One of the STAGE_* constants.
            n: Increment (default 1).
        """
        self.stages[stage] = self.stages.get(stage, 0) + n

    def count_strategy(self, strategy: str, stage: str, n: int = 1) -> None:
        """Add ``n`` to both the global and the per-strategy stage counter.

        Args:
            strategy: Strategy key (e.g. "momentum_scalping").
            stage: One of the STAGE_* constants.
            n: Increment (default 1).
        """
        self.stages[stage] = self.stages.get(stage, 0) + n
        cell = self.by_strategy.get(strategy)
        if cell is None:
            cell = self.by_strategy[strategy] = {}
        cell[stage] = cell.get(stage, 0) + n

    def reject(
        self, reason: str, n: int = 1, strategy: Optional[str] = None
    ) -> None:
        """Record ``n`` rejections attributed to an interned reason.

        Args:
            reason: One of the REASON_* constants (or REASON_VALIDITY[flag]).
                Must be an interned constant - never an f-string.
            n: Increment (default 1).
            strategy: Optional strategy key for per-strategy attribution.
        """
        self.reasons[reason] = self.reasons.get(reason, 0) + n
        if strategy is not None:
            cell = self.by_strategy.get(strategy)
            if cell is None:
                cell = self.by_strategy[strategy] = {}
            cell[reason] = cell.get(reason, 0) + n

    def record_regime(self, regime: str) -> None:
        """Count one bar observed under a regime.

        Args:
            regime: Regime value string (e.g. "trending_strong").
        """
        self.regimes[regime] = self.regimes.get(regime, 0) + 1

    # -- terminal / out-of-band setters -------------------------------------

    def set_stage(self, stage: str, value: int) -> None:
        """Set a stage counter to an absolute value.

        Used for terminal counts (fills, closed trades) that the exchange
        knows only at finalisation.

        Args:
            stage: One of the STAGE_* constants.
            value: Absolute value.
        """
        self.stages[stage] = int(value)

    def note(self, key: str, value: Any) -> None:
        """Attach a JSON-safe structured extra to the funnel.

        Args:
            key: Note key (e.g. "gate_metrics").
            value: JSON-safe value.
        """
        self.notes[key] = value

    def mark_structural_block(self, detail: str) -> None:
        """Declare that a gate is unreachable on this data.

        Recorded as a ``structural:`` reason so :meth:`diagnose` can
        return ``structurally_blocked`` instead of ``no_opportunities``
        for a strategy that was invoked but never emitted a signal.

        Args:
            detail: Short, already-interned or run-scoped description
                (called once per run, not per bar).
        """
        self.reject(STRUCTURAL_PREFIX + detail)

    # -- derived views ------------------------------------------------------

    def get(self, stage: str) -> int:
        """Return a stage counter (0 when absent).

        Args:
            stage: One of the STAGE_* constants.

        Returns:
            The counter value.
        """
        return self.stages.get(stage, 0)

    def survivors(self) -> Dict[str, int]:
        """Return derived per-stage survivor counts.

        Returns:
            Dict with ``confidence_survived``, ``conflict_survived`` and
            ``validity_survived``, each clamped at 0.
        """
        raw = self.get(STAGE_RAW_SIGNALS)
        after_conf = max(0, raw - self.get(STAGE_CONFIDENCE_DROPPED))
        after_conflict = max(0, after_conf - self.get(STAGE_CONFLICT_DROPPED))
        after_validity = max(
            0, after_conflict - self.get(STAGE_VALIDITY_DROPPED)
        )
        return {
            "confidence_survived": after_conf,
            "conflict_survived": after_conflict,
            "validity_survived": after_validity,
        }

    def progress(self) -> float:
        """Return funnel depth as a fraction in [0, 1).

        The score bands add this to a reserved constant, so it must stay
        strictly below 1.0 or adjacent bands would collide.

        Returns:
            Fraction of DEPTH_LADDER milestones with a non-zero count.
        """
        derived = self.survivors()
        reached = 0
        for milestone in DEPTH_LADDER:
            value = derived.get(milestone)
            if value is None:
                value = self.get(milestone)
            if value > 0:
                reached += 1
        # Strictly < 1.0: a full ladder means it traded, which is scored
        # in a different band entirely.
        return min(reached / (len(DEPTH_LADDER) + 1.0), 0.999)

    def top_reasons(self, limit: int = 5) -> List[List[Any]]:
        """Return the most frequent rejection reasons.

        Reasons in NON_RANKING_REASONS are excluded (they describe
        configuration, not a dropped candidate).

        Args:
            limit: Maximum entries returned.

        Returns:
            List of ``[reason, count]`` pairs, descending by count
            (JSON-safe lists, not tuples).
        """
        ordered = sorted(
            (
                item
                for item in self.reasons.items()
                if item[0] not in NON_RANKING_REASONS
            ),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return [[name, count] for name, count in ordered[:limit]]

    def binding_stage(self) -> str:
        """Return the stage where the pipeline lost the most candidates.

        For a zero-trade run this is the stage that actually blocked it.
        For a profitable run it still reports the largest attrition, which
        is actionable even at a good Sharpe.

        Returns:
            A stage name from STAGES.
        """
        if self.get(STAGE_BARS_EVALUATED) == 0:
            return STAGE_BARS_EVALUATED
        if self.get(STAGE_STRATEGY_INVOKED) == 0:
            return STAGE_STRATEGY_INVOKED
        if self.get(STAGE_RAW_SIGNALS) == 0:
            return STAGE_RAW_SIGNALS

        worst_stage = ""
        worst_count = 0
        for stage in DROP_STAGES:
            count = self.get(stage)
            if count > worst_count:
                worst_stage = stage
                worst_count = count
        if worst_count > 0:
            return worst_stage
        if self.get(STAGE_ORDERS_PLACED) == 0:
            return STAGE_ORDERS_PLACED
        if self.get(STAGE_CLOSED_TRADES) == 0:
            return STAGE_CLOSED_TRADES
        return STAGE_CLOSED_TRADES

    def diagnose(self) -> str:
        """Classify the shape of the funnel.

        Returns:
            One of: ``traded``, ``no_opportunities``,
            ``structurally_blocked``, ``all_discarded_downstream``,
            ``never_invoked``, ``no_data``.
        """
        if self.get(STAGE_BARS_EVALUATED) == 0:
            return DIAGNOSIS_NO_DATA
        if self.get(STAGE_CLOSED_TRADES) > 0:
            return DIAGNOSIS_TRADED
        if self.get(STAGE_STRATEGY_INVOKED) == 0:
            return DIAGNOSIS_NEVER_INVOKED
        if self.get(STAGE_RAW_SIGNALS) == 0:
            for reason in self.reasons:
                if reason.startswith(STRUCTURAL_PREFIX):
                    return DIAGNOSIS_STRUCTURALLY_BLOCKED
            return DIAGNOSIS_NO_OPPORTUNITIES
        return DIAGNOSIS_ALL_DISCARDED

    def headline(self) -> str:
        """Return a one-line plain-English summary of the funnel.

        Returns:
            Human-readable sentence naming the binding stage and, when
            relevant, the dominant rejection reason.
        """
        diagnosis = self.diagnose()
        raw = self.get(STAGE_RAW_SIGNALS)
        invoked = self.get(STAGE_STRATEGY_INVOKED)
        bars = self.get(STAGE_BARS_EVALUATED)
        top = self.top_reasons(1)
        top_reason = top[0][0] if top else ""
        top_count = top[0][1] if top else 0

        if diagnosis == DIAGNOSIS_NO_DATA:
            return "no bars were evaluated - the run never reached signal generation"
        if diagnosis == DIAGNOSIS_TRADED:
            closed = self.get(STAGE_CLOSED_TRADES)
            return (
                f"{raw} raw signals over {bars} bars produced "
                f"{closed} closed trades; largest attrition at "
                f"{self.binding_stage()}"
            )
        if diagnosis == DIAGNOSIS_NEVER_INVOKED:
            return (
                f"{bars} bars evaluated but the strategy was never invoked - "
                f"the regime filter never selected it"
            )
        if diagnosis == DIAGNOSIS_STRUCTURALLY_BLOCKED:
            return (
                f"invoked on {invoked} bars, 0 signals emitted - a gate is "
                f"unreachable on this data ({top_reason})"
            )
        if diagnosis == DIAGNOSIS_NO_OPPORTUNITIES:
            return (
                f"invoked on {invoked} bars, 0 signals emitted - no setup "
                f"ever matched"
            )
        # all_discarded_downstream
        stage = self.binding_stage()
        if top_reason:
            return (
                f"{raw} signals generated, 0 survived to a closed trade - "
                f"dropped at {stage}, top reason {top_reason} "
                f"({top_count} times)"
            )
        return f"{raw} signals generated, 0 survived - dropped at {stage}"

    # -- aggregation / serialisation ---------------------------------------

    def merge(self, other: "SignalFunnel") -> "SignalFunnel":
        """Merge another funnel into this one (chunk aggregation).

        Args:
            other: Funnel to fold in. A NullFunnel is ignored.

        Returns:
            ``self``, for chaining.
        """
        if other is None or not getattr(other, "enabled", False):
            return self
        for stage, value in other.stages.items():
            self.stages[stage] = self.stages.get(stage, 0) + value
        for reason, value in other.reasons.items():
            self.reasons[reason] = self.reasons.get(reason, 0) + value
        for regime, value in other.regimes.items():
            self.regimes[regime] = self.regimes.get(regime, 0) + value
        for strategy, cell in other.by_strategy.items():
            mine = self.by_strategy.setdefault(strategy, {})
            for key, value in cell.items():
                mine[key] = mine.get(key, 0) + value
        for key, value in other.notes.items():
            self.notes.setdefault(key, value)
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe snapshot of the funnel.

        Returns:
            Dict with label, stages, reasons, by_strategy, regimes,
            notes, plus the derived diagnosis/headline/binding_stage/
            progress/top_reasons fields consumers need.
        """
        return {
            "label": self.label,
            "stages": dict(self.stages),
            "reasons": dict(self.reasons),
            "by_strategy": {k: dict(v) for k, v in self.by_strategy.items()},
            "regimes": dict(self.regimes),
            "notes": dict(self.notes),
            "diagnosis": self.diagnose(),
            "headline": self.headline(),
            "binding_stage": self.binding_stage(),
            "progress": self.progress(),
            "top_reasons": self.top_reasons(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignalFunnel":
        """Rebuild a funnel from :meth:`to_dict` output.

        Derived fields in the payload are ignored - they are recomputed.

        Args:
            data: Mapping produced by to_dict() (or a subset of it).

        Returns:
            A new SignalFunnel.
        """
        funnel = cls(label=str(data.get("label", "") or ""))
        funnel.stages = {str(k): int(v) for k, v in (data.get("stages") or {}).items()}
        funnel.reasons = {
            str(k): int(v) for k, v in (data.get("reasons") or {}).items()
        }
        funnel.regimes = {
            str(k): int(v) for k, v in (data.get("regimes") or {}).items()
        }
        funnel.by_strategy = {
            str(name): {str(k): int(v) for k, v in (cell or {}).items()}
            for name, cell in (data.get("by_strategy") or {}).items()
        }
        funnel.notes = dict(data.get("notes") or {})
        return funnel

    def __repr__(self) -> str:
        return (
            f"SignalFunnel(label={self.label!r}, "
            f"diagnosis={self.diagnose()!r}, stages={self.stages!r})"
        )


class NullFunnel:
    """No-op funnel: the default everywhere, so the live bot pays nothing.

    Every counting method is an empty function. Read methods return the
    empty/zero answer so callers never need a None check.
    """

    enabled = False

    __slots__ = ()

    label = ""

    def count(self, stage: str, n: int = 1) -> None:
        """No-op."""

    def count_strategy(self, strategy: str, stage: str, n: int = 1) -> None:
        """No-op."""

    def reject(
        self, reason: str, n: int = 1, strategy: Optional[str] = None
    ) -> None:
        """No-op."""

    def record_regime(self, regime: str) -> None:
        """No-op."""

    def set_stage(self, stage: str, value: int) -> None:
        """No-op."""

    def note(self, key: str, value: Any) -> None:
        """No-op."""

    def mark_structural_block(self, detail: str) -> None:
        """No-op."""

    def get(self, stage: str) -> int:
        """Return 0."""
        return 0

    def survivors(self) -> Dict[str, int]:
        """Return zeroed survivor counts."""
        return {
            "confidence_survived": 0,
            "conflict_survived": 0,
            "validity_survived": 0,
        }

    def progress(self) -> float:
        """Return 0.0."""
        return 0.0

    def top_reasons(self, limit: int = 5) -> List[List[Any]]:
        """Return an empty list."""
        return []

    def binding_stage(self) -> str:
        """Return the first stage (nothing was ever counted)."""
        return STAGE_BARS_EVALUATED

    def diagnose(self) -> str:
        """Return ``no_data``."""
        return DIAGNOSIS_NO_DATA

    def headline(self) -> str:
        """Return the no-data headline."""
        return "diagnostics disabled (NullFunnel)"

    def merge(self, other: Any) -> "NullFunnel":
        """No-op merge."""
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Return an empty dict - nothing was recorded."""
        return {}

    def __repr__(self) -> str:
        return "NullFunnel()"


#: Shared immutable default. Never mutate (it has no state to mutate).
NULL_FUNNEL = NullFunnel()
