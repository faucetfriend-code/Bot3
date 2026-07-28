"""
Tests for gate-metric calibration.

The centrepiece is :class:`TestVWAPUnreachableThresholdRegression`, which
freezes the bug this whole mechanism exists for: VWAP_SD_ENTRY_THRESHOLD
was set to 4.037 against a metric that could not reach it, producing zero
signals for months with nothing in the system able to say so.

That test runs a checked-in miniature fixture of REAL BTC 15m candles
through the strategy's own hook, builds the artifact, and asserts the
feasibility check calls 4.037 unreachable. It needs no candle store,
which is not in git.
"""

import inspect
import json
from pathlib import Path

import pytest

from trading_bot_v2.diagnostics.gate_metrics import (
    ARTIFACT_KIND,
    GATE_AT_LEAST,
    GATE_AT_MOST,
    GATE_IN_BAND,
    NULL_GATE_METRICS,
    SCHEMA_VERSION,
    SEVERITY_NEAR_CEILING,
    SEVERITY_UNREACHABLE,
    GateMetric,
    GateMetricCollector,
    artifact_path,
    binding_metric,
    describe_gate,
    load_artifact,
    load_calibrations,
    near_miss,
    threshold_verdicts,
    write_artifact,
)
from trading_bot_v2.diagnostics.outcomes import TrialOutcome, score_for_outcome
from trading_bot_v2.diagnostics.report import render_funnel_report
from trading_bot_v2.optimization.search_spaces import (
    InfeasibleParamsError,
    calibration_warnings,
    check_param_feasibility,
    validate_params,
)
from trading_bot_v2.strategies.ma_crossover import MACrossoverStrategy
from trading_bot_v2.strategies.momentum_scalping import MomentumScalpingStrategy
from trading_bot_v2.strategies.vwap_scalping import VWAPScalpingStrategy

FIXTURE_DIR = Path(__file__).parent / "fixtures"
VWAP_FIXTURE = FIXTURE_DIR / "vwap_15m_mini.json"

#: The value that shipped in .env and made the strategy untradeable.
HISTORICAL_VWAP_THRESHOLD = 4.037

#: Rolling history budget the backtest engine supplies per timeframe.
LOOKBACK = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture_candles():
    """Return the miniature 15m candle bundle."""
    payload = json.loads(VWAP_FIXTURE.read_text(encoding="ascii"))
    return payload["candles"]


def _collect_vwap(threshold: float) -> GateMetricCollector:
    """Replay the fixture through the VWAP hook.

    Args:
        threshold: sd_entry_threshold the strategy is built with.

    Returns:
        A collector holding one observation per replayable bar.
    """
    candles = _load_fixture_candles()
    strategy = VWAPScalpingStrategy(sd_entry_threshold=threshold)
    collector = GateMetricCollector(strategy="vwap_scalping", symbol="BTC-USDC")
    total = len(candles["close"])
    for i in range(LOOKBACK - 1, total):
        low = max(0, i - LOOKBACK + 1)
        window = {k: v[low : i + 1] for k, v in candles.items()}
        collector.observe(
            strategy.describe_gate_metrics(
                "BTC-USDC", {"15m": window}, window["close"][-1]
            )
        )
    return collector


def _collect_vwap_at(threshold: float) -> GateMetricCollector:
    """Re-observe the fixture's deviations against an arbitrary threshold.

    ``validate_sd_entry_threshold`` refuses to construct the strategy at
    4.037 (that guard landed after the bug), so the historical
    configuration can only be reconstructed at the metric level. The
    values themselves are the hook's, unchanged.

    Args:
        threshold: The sd_entry_threshold to judge them against.

    Returns:
        A collector whose records carry ``threshold``.
    """
    collector = GateMetricCollector(strategy="vwap_scalping", symbol="BTC-USDC")
    for record in _collect_vwap(2.0)._metrics["deviation_sd"].values:
        collector.observe(
            [
                GateMetric(
                    name="deviation_sd",
                    value=record,
                    threshold=threshold,
                    direction=GATE_AT_LEAST,
                    param_key="sd_entry_threshold",
                )
            ]
        )
    return collector


def _write(collector: GateMetricCollector, directory: Path) -> Path:
    """Write a collector's artifact into a directory."""
    payload = collector.to_artifact({"window_start": "x", "window_end": "y"})
    return write_artifact(
        payload, artifact_path(collector.strategy, collector.symbol, directory)
    )


def _make_artifact(
    directory: Path,
    strategy: str,
    symbol: str,
    metric: str,
    values,
    threshold: float,
    direction: str = GATE_AT_LEAST,
    param_key: str = "some_threshold",
    threshold_low=None,
    param_key_low=None,
) -> Path:
    """Build a synthetic artifact from a list of values."""
    collector = GateMetricCollector(strategy=strategy, symbol=symbol)
    for value in values:
        collector.observe(
            [
                GateMetric(
                    name=metric,
                    value=value,
                    threshold=threshold,
                    direction=direction,
                    param_key=param_key,
                    threshold_low=threshold_low,
                    param_key_low=param_key_low,
                )
            ]
        )
    return write_artifact(
        collector.to_artifact({}), artifact_path(strategy, symbol, directory)
    )


# ---------------------------------------------------------------------------
# THE REGRESSION PIN
# ---------------------------------------------------------------------------


class TestVWAPUnreachableThresholdRegression:
    """4.037 against a metric that never reaches it must be infeasible."""

    def test_fixture_is_committed(self):
        assert VWAP_FIXTURE.exists(), (
            "the miniature candle fixture must be committed - the real "
            "candle store is gitignored, so without it this regression "
            "cannot be pinned in CI"
        )

    def test_observed_ceiling_is_below_the_historical_threshold(self):
        record = _collect_vwap(2.0).summary()["deviation_sd"]
        assert record["count"] > 100
        assert record["max"] < HISTORICAL_VWAP_THRESHOLD, (
            "the fixture must retain the property that made this a bug: "
            "the metric's ceiling sits below the configured threshold"
        )

    def test_historical_threshold_is_reported_unreachable(self, tmp_path):
        _write(_collect_vwap(2.0), tmp_path)
        hard, _ = threshold_verdicts(
            "vwap_scalping",
            {"sd_entry_threshold": HISTORICAL_VWAP_THRESHOLD},
            directory=tmp_path,
        )
        assert len(hard) == 1
        verdict = hard[0]
        assert verdict.severity == SEVERITY_UNREACHABLE
        assert verdict.param_key == "sd_entry_threshold"
        assert "sd_entry_threshold" in verdict.reason
        assert "observed maximum" in verdict.reason

    def test_feasibility_check_rejects_the_historical_threshold(
        self, tmp_path, monkeypatch
    ):
        _write(_collect_vwap(2.0), tmp_path)
        monkeypatch.setenv("GATE_CALIBRATION_DIR", str(tmp_path))

        params = {"sd_entry_threshold": HISTORICAL_VWAP_THRESHOLD}
        reasons = check_param_feasibility("vwap_scalping", params)
        assert reasons, "4.037 must not be accepted as a feasible threshold"
        assert any("sd_entry_threshold" in r for r in reasons)

        with pytest.raises(InfeasibleParamsError) as excinfo:
            validate_params("vwap_scalping", params)
        assert "sd_entry_threshold" in str(excinfo.value)

    def test_shipped_default_stays_feasible(self, tmp_path, monkeypatch):
        """The interim 2.0 default must NOT be flagged."""
        _write(_collect_vwap(2.0), tmp_path)
        monkeypatch.setenv("GATE_CALIBRATION_DIR", str(tmp_path))
        assert check_param_feasibility(
            "vwap_scalping", {"sd_entry_threshold": 2.0}
        ) == []

    def test_report_names_the_parameter_to_change(self):
        """A report must say WHICH knob to turn, not just that it failed."""
        summary = _collect_vwap_at(HISTORICAL_VWAP_THRESHOLD).summary()
        payload = {
            "label": "vwap_scalping/BTC-USDC",
            "stages": {"bars_evaluated": 200, "strategy_invoked": 200},
            "reasons": {},
            "notes": {"gate_metrics": summary},
        }
        text = render_funnel_report(payload)
        assert "GATE METRICS" in text
        assert "deviation_sd" in text
        assert "BINDING CONSTRAINT" in text
        assert "sd_entry_threshold" in text
        assert "UNREACHABLE" in text


# ---------------------------------------------------------------------------
# GateMetric / collector
# ---------------------------------------------------------------------------


class TestGateMetric:
    def test_at_least(self):
        assert GateMetric("m", 2.0, 1.5, GATE_AT_LEAST, "k").passes()
        assert not GateMetric("m", 1.0, 1.5, GATE_AT_LEAST, "k").passes()

    def test_at_most(self):
        assert GateMetric("m", 1.0, 1.5, GATE_AT_MOST, "k").passes()
        assert not GateMetric("m", 2.0, 1.5, GATE_AT_MOST, "k").passes()

    def test_in_band(self):
        metric = GateMetric("m", 3.0, 5.0, GATE_IN_BAND, "hi", 1.0, "lo")
        assert metric.passes()
        below = GateMetric("m", 0.5, 5.0, GATE_IN_BAND, "hi", 1.0, "lo")
        assert not below.passes()
        above = GateMetric("m", 9.0, 5.0, GATE_IN_BAND, "hi", 1.0, "lo")
        assert not above.passes()


class TestCollector:
    def test_summary_statistics(self):
        collector = GateMetricCollector(strategy="s", symbol="X")
        for value in range(101):  # 0..100
            collector.observe(
                [GateMetric("m", float(value), 50.0, GATE_AT_LEAST, "k")]
            )
        record = collector.summary()["m"]
        assert record["count"] == 101
        assert record["min"] == 0.0
        assert record["max"] == 100.0
        assert record["p50"] == 50.0
        assert record["n_pass"] == 51  # values 50..100
        assert record["pass_rate"] == pytest.approx(51 / 101, rel=1e-6)
        assert record["closest_approach"] == 100.0
        assert len(record["histogram"]["counts"]) == 20
        assert sum(record["histogram"]["counts"]) == 101

    def test_bars_without_metrics_still_count(self):
        collector = GateMetricCollector()
        collector.observe([])
        collector.observe([])
        assert collector.bars_observed == 2
        assert collector.summary() == {}

    def test_non_finite_values_are_dropped(self):
        collector = GateMetricCollector()
        collector.observe([GateMetric("m", float("nan"), 1.0, GATE_AT_LEAST, "k")])
        collector.observe([GateMetric("m", float("inf"), 1.0, GATE_AT_LEAST, "k")])
        collector.observe([GateMetric("m", 2.0, 1.0, GATE_AT_LEAST, "k")])
        record = collector.summary()["m"]
        assert record["count"] == 1
        assert record["max"] == 2.0

    def test_null_collector_is_inert(self):
        assert NULL_GATE_METRICS.enabled is False
        NULL_GATE_METRICS.observe(
            [GateMetric("m", 1.0, 1.0, GATE_AT_LEAST, "k")]
        )
        assert NULL_GATE_METRICS.summary() == {}
        assert NULL_GATE_METRICS.to_artifact({}) == {}


# ---------------------------------------------------------------------------
# Artifact IO
# ---------------------------------------------------------------------------


class TestArtifactIO:
    def test_round_trip(self, tmp_path):
        path = _make_artifact(
            tmp_path, "vwap_scalping", "BTC-USDC", "m", [1.0, 2.0, 3.0], 2.0
        )
        payload = load_artifact(path)
        assert payload["kind"] == ARTIFACT_KIND
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["symbol"] == "BTC-USDC"
        assert payload["metrics"]["m"]["max"] == 3.0

    def test_future_schema_is_ignored(self, tmp_path):
        path = tmp_path / "X_s.json"
        path.write_text(
            json.dumps(
                {"kind": ARTIFACT_KIND, "schema_version": SCHEMA_VERSION + 1}
            ),
            encoding="ascii",
        )
        assert load_artifact(path) is None

    def test_foreign_json_is_ignored(self, tmp_path):
        path = tmp_path / "X_s.json"
        path.write_text(json.dumps({"hello": "world"}), encoding="ascii")
        assert load_artifact(path) is None

    def test_malformed_json_is_ignored(self, tmp_path):
        path = tmp_path / "X_s.json"
        path.write_text("{not json", encoding="ascii")
        assert load_artifact(path) is None

    def test_load_all_symbols(self, tmp_path):
        _make_artifact(tmp_path, "s", "BTC-USDC", "m", [1.0], 1.0)
        _make_artifact(tmp_path, "s", "ETH-USDC", "m", [1.0], 1.0)
        _make_artifact(tmp_path, "other", "BTC-USDC", "m", [1.0], 1.0)
        assert len(load_calibrations("s", directory=tmp_path)) == 2
        assert len(load_calibrations("s", "BTC-USDC", tmp_path)) == 1

    def test_missing_directory_is_not_fatal(self, tmp_path):
        assert load_calibrations("s", directory=tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


class TestThresholdVerdicts:
    def test_above_max_is_hard(self, tmp_path):
        _make_artifact(
            tmp_path, "s", "BTC-USDC", "m", list(range(101)), 50.0, param_key="t"
        )
        hard, warn = threshold_verdicts("s", {"t": 200.0}, directory=tmp_path)
        assert [v.severity for v in hard] == [SEVERITY_UNREACHABLE]
        assert warn == []

    def test_above_p99_is_a_warning_only(self, tmp_path):
        _make_artifact(
            tmp_path, "s", "BTC-USDC", "m", list(range(101)), 50.0, param_key="t"
        )
        hard, warn = threshold_verdicts("s", {"t": 99.7}, directory=tmp_path)
        assert hard == []
        assert [v.severity for v in warn] == [SEVERITY_NEAR_CEILING]

    def test_inside_the_distribution_is_silent(self, tmp_path):
        _make_artifact(
            tmp_path, "s", "BTC-USDC", "m", list(range(101)), 50.0, param_key="t"
        )
        assert threshold_verdicts("s", {"t": 40.0}, directory=tmp_path) == ([], [])

    def test_at_most_gate_fails_from_below(self, tmp_path):
        _make_artifact(
            tmp_path,
            "s",
            "BTC-USDC",
            "m",
            list(range(50, 101)),
            60.0,
            direction=GATE_AT_MOST,
            param_key="ceiling",
        )
        hard, _ = threshold_verdicts("s", {"ceiling": 10.0}, directory=tmp_path)
        assert len(hard) == 1
        assert "Raise ceiling" in hard[0].reason

    def test_band_lower_edge_above_everything_observed(self, tmp_path):
        """The ma_crossover bug: bars_since_cross pinned at 0, window >= 1."""
        _make_artifact(
            tmp_path,
            "ma_crossover",
            "SUI-USDC",
            "bars_since_cross",
            [0.0] * 500,
            5.0,
            direction=GATE_IN_BAND,
            param_key="max_entry_bars",
            threshold_low=1.0,
            param_key_low="min_entry_bars",
        )
        hard, _ = threshold_verdicts(
            "ma_crossover",
            {"min_entry_bars": 1, "max_entry_bars": 5},
            directory=tmp_path,
        )
        assert len(hard) == 1
        assert hard[0].param_key == "min_entry_bars"
        assert "zero signals" in hard[0].reason

    def test_unreachable_on_some_symbols_only_is_a_warning(self, tmp_path):
        _make_artifact(
            tmp_path, "s", "BTC-USDC", "m", [1.0, 2.0, 3.0], 1.0, param_key="t"
        )
        _make_artifact(
            tmp_path, "s", "ETH-USDC", "m", [1.0, 2.0, 90.0], 1.0, param_key="t"
        )
        hard, warn = threshold_verdicts("s", {"t": 50.0}, directory=tmp_path)
        assert hard == [], "reachable on ETH, so not structurally impossible"
        assert len(warn) == 1
        assert "1 of 2" in warn[0].reason

    def test_unreachable_on_every_symbol_is_hard(self, tmp_path):
        _make_artifact(
            tmp_path, "s", "BTC-USDC", "m", [1.0, 2.0, 3.0], 1.0, param_key="t"
        )
        _make_artifact(
            tmp_path, "s", "ETH-USDC", "m", [1.0, 2.0, 4.0], 1.0, param_key="t"
        )
        hard, _ = threshold_verdicts("s", {"t": 50.0}, directory=tmp_path)
        assert len(hard) == 1

    def test_symbol_scoping(self, tmp_path):
        _make_artifact(
            tmp_path, "s", "BTC-USDC", "m", [1.0, 2.0, 3.0], 1.0, param_key="t"
        )
        _make_artifact(
            tmp_path, "s", "ETH-USDC", "m", [1.0, 2.0, 90.0], 1.0, param_key="t"
        )
        hard, _ = threshold_verdicts(
            "s", {"t": 50.0}, symbol="BTC-USDC", directory=tmp_path
        )
        assert len(hard) == 1

    def test_parameters_not_in_the_set_are_skipped(self, tmp_path):
        _make_artifact(
            tmp_path, "s", "BTC-USDC", "m", [1.0], 1.0, param_key="not_searched"
        )
        assert threshold_verdicts("s", {"other": 99.0}, directory=tmp_path) == (
            [],
            [],
        )


class TestSearchSpaceIntegration:
    def test_missing_artifact_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GATE_CALIBRATION_DIR", str(tmp_path))
        params = {"sd_entry_threshold": 99}
        assert check_param_feasibility("vwap_scalping", params) == []

    def test_algebraic_constraints_still_apply(self, tmp_path, monkeypatch):
        """Calibration must ADD to the existing machinery, not replace it."""
        monkeypatch.setenv("GATE_CALIBRATION_DIR", str(tmp_path))
        reasons = check_param_feasibility(
            "momentum_scalping", {"atr_stop_mult": 2.0, "atr_target_mult": 2.4}
        )
        assert any("rrr_meets_minimum" in r.lower() or "RRR" in r for r in reasons)

    def test_warnings_are_exposed_separately(self, tmp_path, monkeypatch):
        _make_artifact(
            tmp_path,
            "vwap_scalping",
            "BTC-USDC",
            "deviation_sd",
            [i / 20 for i in range(101)],
            2.0,
            param_key="sd_entry_threshold",
        )
        monkeypatch.setenv("GATE_CALIBRATION_DIR", str(tmp_path))
        params = {"sd_entry_threshold": 4.99}
        assert check_param_feasibility("vwap_scalping", params) == []
        warnings = calibration_warnings("vwap_scalping", params)
        assert len(warnings) == 1
        assert "99th percentile" in warnings[0]


# ---------------------------------------------------------------------------
# Near-miss gradient
# ---------------------------------------------------------------------------


class TestNearMiss:
    def test_closer_approach_scores_higher(self):
        close = {
            "m": {
                "count": 10,
                "n_pass": 0,
                "closest_approach": 3.95,
                "threshold_at_calibration": 4.037,
                "direction": GATE_AT_LEAST,
            }
        }
        far = {
            "m": {
                "count": 10,
                "n_pass": 0,
                "closest_approach": 3.95,
                "threshold_at_calibration": 100.0,
                "direction": GATE_AT_LEAST,
            }
        }
        assert near_miss(close) > near_miss(far)
        assert near_miss(close) < 1.0

    def test_opened_gates_are_ignored(self):
        assert near_miss(
            {
                "m": {
                    "count": 10,
                    "n_pass": 4,
                    "closest_approach": 1.0,
                    "threshold_at_calibration": 1.0,
                    "direction": GATE_AT_LEAST,
                }
            }
        ) == 0.0

    def test_empty_summary(self):
        assert near_miss({}) == 0.0

    def test_score_band_is_refined_but_not_crossed(self):
        base = score_for_outcome(TrialOutcome.NO_OPPORTUNITIES, progress=0.3)
        refined = score_for_outcome(
            TrialOutcome.NO_OPPORTUNITIES, progress=0.3, near_miss=0.98
        )
        deeper = score_for_outcome(TrialOutcome.NO_OPPORTUNITIES, progress=0.4)
        assert base < refined <= deeper
        assert refined < score_for_outcome(TrialOutcome.ALL_DISCARDED_DOWNSTREAM)


# ---------------------------------------------------------------------------
# Binding-constraint reporting
# ---------------------------------------------------------------------------


class TestBindingMetric:
    def test_never_opened_beats_rarely_opened(self):
        summary = {
            "rare": {
                "count": 100,
                "n_pass": 1,
                "pass_rate": 0.01,
                "min": 0.0,
                "max": 5.0,
                "threshold_at_calibration": 4.9,
                "direction": GATE_AT_LEAST,
                "param_key": "a",
                "closest_approach": 5.0,
            },
            "blocked": {
                "count": 100,
                "n_pass": 0,
                "pass_rate": 0.0,
                "min": 0.0,
                "max": 1.0,
                "threshold_at_calibration": 9.0,
                "direction": GATE_AT_LEAST,
                "param_key": "b",
                "closest_approach": 1.0,
            },
        }
        name, _ = binding_metric(summary)
        assert name == "blocked"

    def test_describe_gate_names_the_knob(self):
        record = {
            "count": 100,
            "n_pass": 0,
            "min": 0.0,
            "max": 3.95,
            "threshold_at_calibration": 4.037,
            "direction": GATE_AT_LEAST,
            "param_key": "sd_entry_threshold",
        }
        text = describe_gate("deviation_sd", record)
        assert "sd_entry_threshold" in text
        assert "UNREACHABLE" in text

    def test_no_observations(self):
        assert binding_metric({}) is None
        assert describe_gate("m", {"count": 0}) == ""


# ---------------------------------------------------------------------------
# Hook contract
# ---------------------------------------------------------------------------


HOOKED_STRATEGIES = (
    VWAPScalpingStrategy,
    MomentumScalpingStrategy,
    MACrossoverStrategy,
)


class TestHookContract:
    @pytest.mark.parametrize("cls", HOOKED_STRATEGIES)
    def test_signature_is_uniform(self, cls):
        params = list(
            inspect.signature(cls.describe_gate_metrics).parameters
        )
        assert params == [
            "self",
            "symbol",
            "multi_tf_data",
            "current_price",
            "execution_tf_data",
        ]

    @pytest.mark.parametrize("cls", HOOKED_STRATEGIES)
    def test_empty_input_returns_empty_list(self, cls):
        assert cls().describe_gate_metrics("X", {}, 100.0) == []
        assert cls().describe_gate_metrics("X", None, 100.0) == []

    @pytest.mark.parametrize("cls", HOOKED_STRATEGIES)
    def test_generate_signals_never_calls_the_hook(self, cls):
        """Hot-path discipline: collection is calibration-only.

        The funnel's rule is that nothing in the per-bar path allocates
        or formats. describe_gate_metrics builds dataclasses, so it must
        stay out of generate_signals entirely.
        """
        source = inspect.getsource(cls.generate_signals)
        assert "describe_gate_metrics" not in source

    @pytest.mark.parametrize("key", ("param_key", "direction"))
    def test_every_metric_declares_its_gate(self, key):
        candles = _load_fixture_candles()
        window = {k: v[:60] for k, v in candles.items()}
        metrics = VWAPScalpingStrategy().describe_gate_metrics(
            "BTC-USDC", {"15m": window}, window["close"][-1]
        )
        assert metrics
        for metric in metrics:
            assert getattr(metric, key)

    def test_hook_is_side_effect_free(self):
        candles = _load_fixture_candles()
        window = {k: v[:60] for k, v in candles.items()}
        strategy = VWAPScalpingStrategy()
        first = strategy.describe_gate_metrics(
            "BTC-USDC", {"15m": window}, window["close"][-1]
        )
        second = strategy.describe_gate_metrics(
            "BTC-USDC", {"15m": window}, window["close"][-1]
        )
        assert first == second
        assert strategy._last_trade_time == {}

    def test_momentum_reports_its_constant_rrr(self):
        strategy = MomentumScalpingStrategy(
            atr_stop_mult=2.0, atr_target_mult=3.0, min_rrr=1.0
        )
        closes = [100.0 + i for i in range(60)]
        bundle = {
            "close": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "volume": [10.0] * 60,
        }
        metrics = {
            m.name: m
            for m in strategy.describe_gate_metrics("X", {"1h": bundle}, 150.0)
        }
        assert metrics["rrr"].value == pytest.approx(1.5)
        # The threshold is min_rrr, NOT a term of the metric - naming
        # atr_target_mult here would make the feasibility check compare a
        # parameter against a distribution it produced itself.
        assert metrics["rrr"].param_key == "min_rrr"
        assert "volume_ratio" in metrics
        assert "atr_pct" in metrics

    def test_ma_crossover_reports_bands(self):
        strategy = MACrossoverStrategy(fast_ma_period=3, slow_ma_period=6)
        # Down then up: guarantees a golden cross inside the window.
        closes = [100.0 - i for i in range(20)] + [80.0 + 2 * i for i in range(20)]
        bundle = {
            "close": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "volume": [10.0] * len(closes),
        }
        metrics = {
            m.name: m
            for m in strategy.describe_gate_metrics("X", {"4h": bundle}, closes[-1])
        }
        assert set(metrics) == {"bars_since_cross", "pullback_pct"}
        for metric in metrics.values():
            assert metric.direction == GATE_IN_BAND
            assert metric.param_key_low
        assert metrics["bars_since_cross"].value >= 0
        assert strategy.last_crossover == {}, "the hook must not mutate state"


# ---------------------------------------------------------------------------
# Committed artifacts
# ---------------------------------------------------------------------------


class TestCommittedArtifacts:
    """The repo must ship real numbers, not an empty mechanism."""

    STRATEGIES = ("vwap_scalping", "momentum_scalping", "ma_crossover")
    SYMBOLS = ("BTC-USDC", "ETH-USDC", "SUI-USDC")

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_every_calibrated_strategy_ships_artifacts(self, strategy):
        payloads = load_calibrations(strategy)
        assert len(payloads) == len(self.SYMBOLS)
        assert {p["symbol"] for p in payloads} == set(self.SYMBOLS)

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_artifacts_carry_provenance_and_a_distribution(self, strategy):
        for payload in load_calibrations(strategy):
            provenance = payload["provenance"]
            for field in (
                "generated_at",
                "window_start",
                "window_end",
                "history_lookback",
                "bars_observed",
                "params_at_calibration",
            ):
                assert field in provenance, field
            assert payload["metrics"]
            for name, record in payload["metrics"].items():
                assert record["count"] > 0, name
                assert record["min"] <= record["max"]
                assert record["param_key"]

    def test_vwap_ceiling_is_recorded(self):
        """The number that should have prevented months of wasted work."""
        for payload in load_calibrations("vwap_scalping"):
            record = payload["metrics"]["deviation_sd"]
            assert record["count"] > 10000
            # p99 is an order of magnitude away from the historical 4.037,
            # which is what makes it a warning even where the extreme tail
            # brushes past it.
            assert record["p99"] < HISTORICAL_VWAP_THRESHOLD
            assert record["p999"] < HISTORICAL_VWAP_THRESHOLD

    def test_shipped_thresholds_are_not_unreachable(self):
        """No CURRENTLY configured threshold may be structurally dead."""
        for strategy in self.STRATEGIES:
            for payload in load_calibrations(strategy):
                params = payload["provenance"]["params_at_calibration"]
                hard, _ = threshold_verdicts(
                    strategy, params, symbol=payload["symbol"]
                )
                assert hard == [], (
                    f"{strategy}/{payload['symbol']} ships an unreachable "
                    f"threshold: {[v.reason for v in hard]}"
                )
