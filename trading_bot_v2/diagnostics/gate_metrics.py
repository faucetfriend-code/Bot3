"""
Gate-Metric Calibration
=======================

The one defence against a threshold configured above the mathematical
ceiling of the metric it gates on.

The motivating bug: ``VWAP_SD_ENTRY_THRESHOLD`` was set to 4.037 against a
metric whose observed maximum over 52,041 real bars was 3.95. Not a bad
tuning choice - a mathematically unreachable one. Zero signals were
possible, and nothing in the system could say so. Everything else in
``diagnostics/`` catches failures DOWNSTREAM of signal generation; this
module catches the configuration being impossible in the first place.

The mechanism has three parts:

1. **The hook.** A strategy may implement::

       def describe_gate_metrics(
           self, symbol, multi_tf_data, current_price, execution_tf_data=None
       ) -> List[GateMetric]

   returning, for the bar it was just handed, the value of every metric
   that gates its entry, the threshold that metric is compared against,
   and - crucially - the SEARCH-SPACE KEY that sets that threshold, so a
   report can say "raise ``sd_entry_threshold``" rather than "something
   was too small". The hook must be side-effect free.

2. **The calibration pass.** :mod:`trading_bot_v2.diagnostics.calibrate`
   replays real candles, calls the hook on every bar, and writes the
   observed distribution of each metric to a committed JSON artifact
   under ``diagnostics/calibration/``. A change in ``max`` between two
   runs is a data-drift signal a reviewer should see in the PR.

3. **Consumption.** :func:`threshold_verdicts` compares a candidate
   parameter set against those artifacts.
   ``optimization/search_spaces.py`` wires it into the existing
   ``check_param_feasibility`` / ``validate_params`` /
   ``InfeasibleParamsError`` machinery: a threshold above the observed
   ``max`` is a HARD violation (the trial is pruned before the backtest
   burns), above ``p99`` is a warning the trial survives, and a missing
   artifact is skipped silently.

Hot-path discipline
-------------------

Collection is NOT free: :class:`GateMetricCollector` appends every
observation so exact percentiles can be computed at the end. It is
therefore OFF by default. :data:`NULL_GATE_METRICS` is the default
collector everywhere, and only the calibration pass and single-strategy
diagnostic runs construct a real one. Optimization trials never do.
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Gate directions
# ---------------------------------------------------------------------------

#: The metric must be >= threshold to pass (e.g. deviation_sd).
GATE_AT_LEAST = "at_least"

#: The metric must be <= threshold to pass (e.g. spread_pct).
GATE_AT_MOST = "at_most"

#: The metric must land inside [threshold_low, threshold] (e.g. the
#: post-crossover entry window, or the accepted pullback band). Half of
#: this codebase's silent-zero bugs were an empty or misplaced band, so
#: it gets a first-class direction rather than being split into two
#: one-sided gates that each look satisfiable on their own.
GATE_IN_BAND = "in_band"

DIRECTIONS: Tuple[str, ...] = (GATE_AT_LEAST, GATE_AT_MOST, GATE_IN_BAND)

# ---------------------------------------------------------------------------
# Artifact layout
# ---------------------------------------------------------------------------

#: Bumped whenever the artifact shape changes incompatibly. Readers
#: refuse anything newer than they understand.
SCHEMA_VERSION = 1

#: Discriminator, so a stray JSON file in the directory is not mistaken
#: for a calibration artifact.
ARTIFACT_KIND = "gate_metric_calibration"

#: Where committed artifacts live (one per symbol/strategy pair).
CALIBRATION_DIR = Path(__file__).resolve().parent / "calibration"

#: Bins in the artifact histogram. Enough to see the shape, few enough
#: that the file stays diffable.
HISTOGRAM_BINS = 20

#: Decimal places every stored float is rounded to. Keeps a re-run from
#: producing a diff in the 15th decimal place.
ROUND_DP = 6

#: Percentiles stored for every metric.
PERCENTILES: Tuple[int, ...] = (1, 5, 25, 50, 75, 95, 99)


# ---------------------------------------------------------------------------
# One observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateMetric:
    """One gating metric observed on one bar.

    Attributes:
        name: Metric name, stable across runs (e.g. "deviation_sd").
        value: The observed value on this bar.
        threshold: The configured threshold it is compared against. For
            GATE_IN_BAND this is the UPPER edge.
        direction: One of GATE_AT_LEAST / GATE_AT_MOST / GATE_IN_BAND.
        param_key: The search-space key that sets ``threshold``, so a
            report can name the knob to turn. A key that is not in the
            strategy's search space simply never matches a sampled
            parameter and is skipped by the feasibility check.

            INVARIANT: ``param_key`` names the parameter that sets the
            THRESHOLD, never a term of the metric itself. Naming a term
            would make the feasibility check compare a parameter against
            a distribution that parameter itself produced, and every
            large value would be flagged unreachable. Momentum's ``rrr``
            is the worked example: the knob a human would turn is
            ``atr_target_mult``, but the threshold is ``min_rrr``, so
            that is what is recorded.
        threshold_low: Lower edge, GATE_IN_BAND only.
        param_key_low: Search-space key setting ``threshold_low``.
        description: Short human-readable note about the gate.
    """

    name: str
    value: float
    threshold: float
    direction: str
    param_key: str
    threshold_low: Optional[float] = None
    param_key_low: Optional[str] = None
    description: str = ""

    def passes(self) -> bool:
        """Whether this observation clears its gate.

        Returns:
            True when the value satisfies the gate as configured.
        """
        if self.direction == GATE_AT_LEAST:
            return self.value >= self.threshold
        if self.direction == GATE_AT_MOST:
            return self.value <= self.threshold
        low = self.threshold_low if self.threshold_low is not None else -math.inf
        return low <= self.value <= self.threshold


# ---------------------------------------------------------------------------
# Accumulation
# ---------------------------------------------------------------------------


class _MetricAccumulator:
    """Running record of every observation of a single metric.

    Values are retained in full so the artifact can carry exact
    percentiles. A calibration pass over 50k bars costs a few hundred
    kilobytes, which is irrelevant for an offline pass and is exactly
    why this must never run inside an optimization trial.
    """

    __slots__ = (
        "name",
        "direction",
        "param_key",
        "param_key_low",
        "threshold",
        "threshold_low",
        "description",
        "values",
        "n_pass",
    )

    def __init__(self, metric: GateMetric) -> None:
        """Initialize from the first observation seen.

        Args:
            metric: The first GateMetric with this name.
        """
        self.name = metric.name
        self.direction = metric.direction
        self.param_key = metric.param_key
        self.param_key_low = metric.param_key_low
        self.threshold = float(metric.threshold)
        self.threshold_low = (
            None if metric.threshold_low is None else float(metric.threshold_low)
        )
        self.description = metric.description
        self.values: List[float] = []
        self.n_pass = 0

    def add(self, metric: GateMetric) -> None:
        """Record one observation.

        Args:
            metric: Observation to fold in.
        """
        value = float(metric.value)
        if value != value or value in (math.inf, -math.inf):
            return  # NaN/inf would poison every percentile
        self.values.append(value)
        if metric.passes():
            self.n_pass += 1

    def closest_approach(self) -> Optional[float]:
        """Observed value that came nearest to satisfying the gate.

        Returns:
            The nearest-miss value, or None when nothing was observed.
        """
        if not self.values:
            return None
        if self.direction == GATE_AT_LEAST:
            return max(self.values)
        if self.direction == GATE_AT_MOST:
            return min(self.values)
        low = self.threshold_low if self.threshold_low is not None else -math.inf
        best = None
        best_gap = math.inf
        for value in self.values:
            if low <= value <= self.threshold:
                return value
            gap = low - value if value < low else value - self.threshold
            if gap < best_gap:
                best_gap = gap
                best = value
        return best

    def summary(self) -> Dict[str, Any]:
        """Return the JSON-safe distribution record for this metric.

        Returns:
            Dict with the gate declaration, the observed distribution and
            a coarse histogram.
        """
        count = len(self.values)
        record: Dict[str, Any] = {
            "param_key": self.param_key,
            "direction": self.direction,
            "threshold_at_calibration": _round(self.threshold),
            "count": count,
            "n_pass": self.n_pass,
        }
        if self.param_key_low is not None:
            record["param_key_low"] = self.param_key_low
        if self.threshold_low is not None:
            record["threshold_low_at_calibration"] = _round(self.threshold_low)
        if self.description:
            record["description"] = self.description
        if count == 0:
            record["pass_rate"] = 0.0
            return record

        ordered = sorted(self.values)
        total = math.fsum(ordered)
        mean = total / count
        variance = (
            math.fsum((v - mean) ** 2 for v in ordered) / count if count > 1 else 0.0
        )
        record["pass_rate"] = _round(self.n_pass / count)
        record["min"] = _round(ordered[0])
        record["max"] = _round(ordered[-1])
        record["mean"] = _round(mean)
        record["std"] = _round(math.sqrt(max(0.0, variance)))
        for pct in PERCENTILES:
            record[f"p{pct:02d}"] = _round(_percentile(ordered, pct))
        # The extreme tail decides whether a threshold is merely rare or
        # effectively dead. VWAP's deviation_sd has p99 ~2.6 and a max of
        # ~4.5 over 2.5 years: the max alone makes 4.037 look reachable,
        # while p999 shows how far into the tail it really sits.
        record["p001"] = _round(_percentile(ordered, 0.1))
        record["p999"] = _round(_percentile(ordered, 99.9))
        approach = self.closest_approach()
        record["closest_approach"] = None if approach is None else _round(approach)
        record["histogram"] = _histogram(ordered)
        return record


class GateMetricCollector:
    """Aggregates :class:`GateMetric` observations for one run.

    Attributes:
        strategy: Strategy key (e.g. "vwap_scalping").
        symbol: Symbol the run covers (e.g. "BTC-USDC").
    """

    enabled = True

    __slots__ = ("strategy", "symbol", "_metrics", "_bars")

    def __init__(self, strategy: str = "", symbol: str = "") -> None:
        """Initialize an empty collector.

        Args:
            strategy: Strategy key, recorded in the artifact.
            symbol: Symbol, recorded in the artifact.
        """
        self.strategy = strategy
        self.symbol = symbol
        self._metrics: Dict[str, _MetricAccumulator] = {}
        self._bars = 0

    def observe(self, metrics: Iterable[GateMetric]) -> None:
        """Record every metric a strategy reported for one bar.

        Args:
            metrics: What ``describe_gate_metrics`` returned. An empty
                iterable still counts the bar as observed.
        """
        self._bars += 1
        for metric in metrics:
            slot = self._metrics.get(metric.name)
            if slot is None:
                slot = self._metrics[metric.name] = _MetricAccumulator(metric)
            slot.add(metric)

    @property
    def bars_observed(self) -> int:
        """Number of bars the collector was offered."""
        return self._bars

    def summary(self) -> Dict[str, Dict[str, Any]]:
        """Return the per-metric distribution records.

        Returns:
            Metric name -> distribution record.
        """
        return {name: acc.summary() for name, acc in sorted(self._metrics.items())}

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Alias of :meth:`summary`, for ``SignalFunnel.note``."""
        return self.summary()

    def to_artifact(
        self, provenance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Return the committed-artifact payload.

        Args:
            provenance: Run provenance (window, timeframes, params, ...).

        Returns:
            A JSON-safe dict matching the documented schema.
        """
        record = dict(provenance or {})
        record.setdefault("bars_observed", self._bars)
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": ARTIFACT_KIND,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "provenance": record,
            "metrics": self.summary(),
        }

    def __repr__(self) -> str:
        return (
            f"GateMetricCollector(strategy={self.strategy!r}, "
            f"symbol={self.symbol!r}, metrics={sorted(self._metrics)!r})"
        )


class NullGateMetricCollector:
    """No-op collector - the default, so nothing pays for calibration."""

    enabled = False

    __slots__ = ()

    strategy = ""
    symbol = ""
    bars_observed = 0

    def observe(self, metrics: Iterable[GateMetric]) -> None:
        """No-op."""

    def summary(self) -> Dict[str, Dict[str, Any]]:
        """Return an empty summary."""
        return {}

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Return an empty summary."""
        return {}

    def to_artifact(
        self, provenance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Return an empty artifact."""
        return {}

    def __repr__(self) -> str:
        return "NullGateMetricCollector()"


#: Shared immutable default. Never mutate (it has no state).
NULL_GATE_METRICS = NullGateMetricCollector()


# ---------------------------------------------------------------------------
# Artifact IO
# ---------------------------------------------------------------------------


def calibration_dir(directory: Optional[Path] = None) -> Path:
    """Return the directory holding calibration artifacts.

    ``GATE_CALIBRATION_DIR`` overrides the default, which is how tests
    point the feasibility check at a fixture store.

    Args:
        directory: Explicit override, wins over everything.

    Returns:
        The directory path (not guaranteed to exist).
    """
    if directory is not None:
        return Path(directory)
    env = os.getenv("GATE_CALIBRATION_DIR")
    if env:
        return Path(env)
    return CALIBRATION_DIR


def artifact_path(strategy: str, symbol: str, directory: Optional[Path] = None) -> Path:
    """Return the artifact path for one (symbol, strategy) pair.

    Args:
        strategy: Strategy key.
        symbol: Symbol (e.g. "BTC-USDC").
        directory: Optional directory override.

    Returns:
        Path to ``<symbol>_<strategy>.json``.
    """
    return calibration_dir(directory) / f"{symbol}_{strategy}.json"


def write_artifact(payload: Dict[str, Any], path: Path) -> Path:
    """Write an artifact as stable, diffable JSON.

    Args:
        payload: Artifact dict from :meth:`GateMetricCollector.to_artifact`.
        path: Destination file.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=False)
    path.write_text(text + "\n", encoding="ascii")
    return path


def load_artifact(path: Path) -> Optional[Dict[str, Any]]:
    """Load one artifact, returning None when it is unusable.

    A malformed or future-schema artifact is ignored rather than fatal:
    a diagnostic aid must never take down a run.

    Args:
        path: Artifact file.

    Returns:
        The payload, or None.
    """
    try:
        payload = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("kind") != ARTIFACT_KIND:
        return None
    version = payload.get("schema_version")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        return None
    return payload


def load_calibrations(
    strategy: str,
    symbol: Optional[str] = None,
    directory: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Load every calibration artifact for a strategy.

    Args:
        strategy: Strategy key.
        symbol: Restrict to one symbol; None loads all symbols.
        directory: Optional directory override.

    Returns:
        Loaded payloads, ordered by symbol. Empty when none exist.
    """
    root = calibration_dir(directory)
    if not root.is_dir():
        return []
    if symbol is not None:
        payload = load_artifact(artifact_path(strategy, symbol, root))
        return [payload] if payload else []
    found: List[Dict[str, Any]] = []
    for path in sorted(root.glob(f"*_{strategy}.json")):
        payload = load_artifact(path)
        if payload is not None:
            found.append(payload)
    return found


# ---------------------------------------------------------------------------
# Feasibility verdicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdVerdict:
    """One metric's verdict on one candidate parameter set.

    Attributes:
        metric: Metric name.
        param_key: The parameter that was checked.
        value: The candidate threshold.
        severity: "unreachable" (hard) or "near_ceiling" (warning).
        symbol: Symbol whose artifact produced the verdict.
        reason: Human-readable explanation naming the knob to turn.
    """

    metric: str
    param_key: str
    value: float
    severity: str
    symbol: str
    reason: str


#: A threshold no observed value could ever satisfy. Hard violation.
SEVERITY_UNREACHABLE = "unreachable"

#: A threshold beyond p99 (or below p01) of the observed distribution -
#: satisfiable, but by well under 1% of bars. Warning only.
SEVERITY_NEAR_CEILING = "near_ceiling"


def _verdicts_for_metric(
    metric_name: str,
    record: Dict[str, Any],
    params: Dict[str, Any],
    symbol: str,
) -> List[ThresholdVerdict]:
    """Verdicts produced by one metric record against one param set.

    Args:
        metric_name: Metric name.
        record: Distribution record from an artifact.
        params: Candidate parameters.
        symbol: Symbol the record came from.

    Returns:
        Zero or more verdicts.
    """
    if not record.get("count"):
        return []
    observed_min = record.get("min")
    observed_max = record.get("max")
    if observed_min is None or observed_max is None:
        return []
    p99 = record.get("p99", observed_max)
    p01 = record.get("p01", observed_min)
    direction = record.get("direction", GATE_AT_LEAST)
    verdicts: List[ThresholdVerdict] = []

    upper_key = record.get("param_key")
    lower_key = record.get("param_key_low")

    def _hard(key: str, value: float, text: str) -> None:
        verdicts.append(
            ThresholdVerdict(
                metric=metric_name,
                param_key=key,
                value=float(value),
                severity=SEVERITY_UNREACHABLE,
                symbol=symbol,
                reason=text,
            )
        )

    def _warn(key: str, value: float, text: str) -> None:
        verdicts.append(
            ThresholdVerdict(
                metric=metric_name,
                param_key=key,
                value=float(value),
                severity=SEVERITY_NEAR_CEILING,
                symbol=symbol,
                reason=text,
            )
        )

    if direction in (GATE_AT_LEAST, GATE_IN_BAND):
        # The lower edge of a band and a one-sided ">=" gate fail the
        # same way: the bar has to reach UP to them.
        key = lower_key if direction == GATE_IN_BAND else upper_key
        if key is not None and key in params:
            value = float(params[key])
            if value > observed_max:
                _hard(
                    key,
                    value,
                    f"{key}={value:g} is above the observed maximum of "
                    f"{metric_name} ({observed_max:g} over "
                    f"{record['count']} calibrated bars on {symbol}); no bar "
                    f"could ever satisfy the gate, so the strategy would emit "
                    f"zero signals. Lower {key} to at most {observed_max:g}",
                )
            elif value > p99:
                _warn(
                    key,
                    value,
                    f"{key}={value:g} is above the 99th percentile of "
                    f"{metric_name} on {symbol} (p99 {p99:g}, p99.9 "
                    f"{record.get('p999', observed_max):g}, max "
                    f"{observed_max:g} over {record['count']} bars); "
                    f"under 1% of bars can pass this gate",
                )

    if direction in (GATE_AT_MOST, GATE_IN_BAND):
        # A "<=" gate and the upper edge of a band both fail by sitting
        # BELOW everything that was observed.
        key = upper_key
        if key is not None and key in params:
            value = float(params[key])
            if value < observed_min:
                _hard(
                    key,
                    value,
                    f"{key}={value:g} is below the observed minimum of "
                    f"{metric_name} ({observed_min:g} over "
                    f"{record['count']} calibrated bars on {symbol}); no bar "
                    f"could ever satisfy the gate, so the strategy would emit "
                    f"zero signals. Raise {key} to at least {observed_min:g}",
                )
            elif value < p01:
                _warn(
                    key,
                    value,
                    f"{key}={value:g} is below the 1st percentile of "
                    f"{metric_name} on {symbol} (p01 {p01:g}, p0.1 "
                    f"{record.get('p001', observed_min):g}, min "
                    f"{observed_min:g} over {record['count']} bars); "
                    f"under 1% of bars can pass this gate",
                )

    return verdicts


def threshold_verdicts(
    strategy: str,
    params: Dict[str, Any],
    symbol: Optional[str] = None,
    directory: Optional[Path] = None,
) -> Tuple[List[ThresholdVerdict], List[ThresholdVerdict]]:
    """Check a candidate parameter set against the calibration artifacts.

    When no ``symbol`` is given, every calibrated symbol is consulted and
    a violation is only HARD if it holds on ALL of them - a threshold
    that is reachable on any real market is not structurally impossible,
    just badly suited to the rest. Partial violations are downgraded to
    warnings.

    A missing artifact yields no verdicts at all (see
    ``search_spaces.py`` for the log-once behaviour).

    Args:
        strategy: Strategy key.
        params: Candidate parameters.
        symbol: Restrict to one symbol's artifact.
        directory: Optional artifact directory override.

    Returns:
        ``(hard, warnings)`` - two lists of :class:`ThresholdVerdict`.
    """
    payloads = load_calibrations(strategy, symbol=symbol, directory=directory)
    if not payloads:
        return [], []

    # metric+param -> per-symbol verdicts, so a violation can be tested
    # for unanimity across symbols before being called structural.
    per_key: Dict[Tuple[str, str], List[ThresholdVerdict]] = {}
    symbols: List[str] = []
    for payload in payloads:
        sym = str(payload.get("symbol") or "?")
        symbols.append(sym)
        for metric_name, record in (payload.get("metrics") or {}).items():
            if not isinstance(record, dict):
                continue
            for verdict in _verdicts_for_metric(metric_name, record, params, sym):
                per_key.setdefault((verdict.metric, verdict.param_key), []).append(
                    verdict
                )

    n_symbols = len(symbols)
    hard: List[ThresholdVerdict] = []
    warnings: List[ThresholdVerdict] = []
    for verdicts in per_key.values():
        unreachable = [v for v in verdicts if v.severity == SEVERITY_UNREACHABLE]
        if unreachable and len(unreachable) == n_symbols:
            hard.append(unreachable[0])
        elif unreachable:
            first = unreachable[0]
            warnings.append(
                ThresholdVerdict(
                    metric=first.metric,
                    param_key=first.param_key,
                    value=first.value,
                    severity=SEVERITY_NEAR_CEILING,
                    symbol=", ".join(v.symbol for v in unreachable),
                    reason=(
                        f"{first.param_key}={first.value:g} is unreachable on "
                        f"{len(unreachable)} of {n_symbols} calibrated symbols "
                        f"({', '.join(v.symbol for v in unreachable)}); the "
                        f"strategy can only trade on the rest"
                    ),
                )
            )
        else:
            warnings.append(verdicts[0])
    return hard, warnings


def binding_metric(
    summary: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return the gate that most constrained a run.

    Args:
        summary: :meth:`GateMetricCollector.summary` output.

    Returns:
        ``(metric_name, record)`` for the tightest gate, or None when no
        metric was observed. Gates that never opened win over gates that
        merely opened rarely; among those, the one with the largest
        shortfall wins.
    """
    best: Optional[Tuple[str, Dict[str, Any]]] = None
    best_rank: Tuple[int, float] = (2, 2.0)
    for name, record in sorted((summary or {}).items()):
        if not isinstance(record, dict) or not record.get("count"):
            continue
        blocked = 0 if not record.get("n_pass") else 1
        rate = float(record.get("pass_rate") or 0.0)
        score = near_miss({name: record}) if blocked == 0 else rate
        rank = (blocked, score)
        if rank < best_rank:
            best_rank = rank
            best = (name, record)
    return best


def describe_gate(name: str, record: Dict[str, Any]) -> str:
    """One-line verdict on a calibrated gate, naming the knob to turn.

    Args:
        name: Metric name.
        record: Distribution record.

    Returns:
        A sentence, empty when the record carries no distribution.
    """
    if not record.get("count"):
        return ""
    observed_min = record.get("min")
    observed_max = record.get("max")
    threshold = record.get("threshold_at_calibration")
    low = record.get("threshold_low_at_calibration")
    key = record.get("param_key", "?")
    key_low = record.get("param_key_low")
    direction = record.get("direction", GATE_AT_LEAST)
    n_pass = record.get("n_pass", 0)

    if direction in (GATE_AT_LEAST, GATE_IN_BAND):
        floor = low if direction == GATE_IN_BAND else threshold
        floor_key = key_low if direction == GATE_IN_BAND else key
        if floor is not None and observed_max is not None and floor > observed_max:
            return (
                f"{name} never reaches {floor_key}={floor:g} (observed max "
                f"{observed_max:g}) - UNREACHABLE, lower {floor_key} to at "
                f"most {observed_max:g}"
            )
    if direction in (GATE_AT_MOST, GATE_IN_BAND):
        if (
            threshold is not None
            and observed_min is not None
            and threshold < observed_min
        ):
            return (
                f"{name} never drops to {key}={threshold:g} (observed min "
                f"{observed_min:g}) - UNREACHABLE, raise {key} to at least "
                f"{observed_min:g}"
            )
    if not n_pass:
        return (
            f"{name} never satisfied its gate over {record['count']} bars "
            f"(closest approach {record.get('closest_approach')}) - relax "
            f"{key}"
        )
    return (
        f"{name} passes on {record.get('pass_rate', 0.0):.2%} of {record['count']} bars"
    )


def near_miss(summary: Dict[str, Dict[str, Any]]) -> float:
    """How close the run came to opening its tightest gate, in [0, 1).

    The near-miss gradient term for zero-signal trials: a config needing
    4.037 against a metric that reached 3.95 scores 0.98 and ranks just
    below one that reached 4.0, while a config needing 100 scores ~0.04.
    TPE is rank-based, so this turns a flat "never fired" plateau into a
    surface it can descend.

    Args:
        summary: :meth:`GateMetricCollector.summary` output, or the
            ``gate_metrics`` note stored on a funnel.

    Returns:
        The worst (smallest) per-metric approach ratio, or 0.0 when
        nothing usable is present. Always strictly below 1.0.
    """
    worst = 1.0
    seen = False
    for record in (summary or {}).values():
        if not isinstance(record, dict) or not record.get("count"):
            continue
        if record.get("n_pass"):
            continue  # this gate opened; it is not what blocked the run
        approach = record.get("closest_approach")
        threshold = record.get("threshold_at_calibration")
        if approach is None or threshold is None:
            continue
        direction = record.get("direction", GATE_AT_LEAST)
        if direction == GATE_AT_LEAST:
            if threshold <= 0:
                continue
            ratio = approach / threshold
        else:
            # For "<=" and band gates the approach is above the ceiling;
            # invert so that "just over" still scores near 1.
            if approach <= 0:
                continue
            ratio = threshold / approach
        if ratio != ratio:  # NaN
            continue
        seen = True
        worst = min(worst, max(0.0, ratio))
    if not seen:
        return 0.0
    return min(worst, 0.999)


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------


def _round(value: float) -> float:
    """Round for storage, collapsing -0.0 to 0.0.

    Args:
        value: Value to round.

    Returns:
        The rounded float.
    """
    rounded = round(float(value), ROUND_DP)
    return 0.0 if rounded == 0 else rounded


def _percentile(ordered: List[float], pct: float) -> float:
    """Linear-interpolated percentile of a pre-sorted list.

    Args:
        ordered: Ascending values, non-empty.
        pct: Percentile in [0, 100].

    Returns:
        The percentile value.
    """
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (pct / 100.0)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _histogram(ordered: List[float]) -> Dict[str, List[float]]:
    """Fixed-bin histogram over the observed range.

    Args:
        ordered: Ascending values, non-empty.

    Returns:
        Dict with ``bin_edges`` (HISTOGRAM_BINS + 1 entries) and
        ``counts`` (HISTOGRAM_BINS entries).
    """
    low, high = ordered[0], ordered[-1]
    if high <= low:
        return {
            "bin_edges": [_round(low), _round(low)],
            "counts": [len(ordered)],
        }
    width = (high - low) / HISTOGRAM_BINS
    counts: List[float] = [0] * HISTOGRAM_BINS
    for value in ordered:
        index = int((value - low) / width)
        if index >= HISTOGRAM_BINS:
            index = HISTOGRAM_BINS - 1
        counts[index] += 1
    edges = [_round(low + width * i) for i in range(HISTOGRAM_BINS + 1)]
    return {"bin_edges": edges, "counts": counts}
