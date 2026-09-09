"""
Tests for the signal-funnel diagnostics package.

The regression pins in TestRegressionPins are the point of the whole
package: each reproduces the *shape* of a real bug that took a multi-hour
manual investigation to diagnose, and asserts the funnel now names it.
"""

import json
import time

import pytest

from trading_bot_v2.diagnostics.funnel import (
    NULL_FUNNEL,
    REASON_CONFIDENCE_GATE,
    REASON_EXEC_HEDGE_MODE,
    REASON_VALIDITY,
    STAGE_BARS_EVALUATED,
    STAGE_CLOSED_TRADES,
    STAGE_CONFIDENCE_DROPPED,
    STAGE_EXECUTION_BLOCKED,
    STAGE_ORDERS_PLACED,
    STAGE_RAW_SIGNALS,
    STAGE_STRATEGY_INVOKED,
    STAGE_VALIDITY_DROPPED,
    NullFunnel,
    SignalFunnel,
)
from trading_bot_v2.diagnostics.outcomes import (
    SCORE_BANDS,
    TRADED_SCORE_FLOOR,
    TrialOutcome,
    score_for_outcome,
    suggest_fix,
)
from trading_bot_v2.diagnostics.report import (
    funnel_one_liner,
    render_funnel_report,
)


# ---------------------------------------------------------------------------
# Funnel state builders (hand-constructed - no parquet/CSV data needed)
# ---------------------------------------------------------------------------


def momentum_shape(n_signals: int = 136) -> SignalFunnel:
    """Funnel matching the momentum_scalping bug.

    136 valid signals emitted, every one discarded by the
    rrr_meets_minimum validity flag before an order was ever placed.

    Args:
        n_signals: Number of raw signals emitted.

    Returns:
        A populated SignalFunnel.
    """
    funnel = SignalFunnel(label="momentum_scalping/SUI-USDC")
    funnel.count(STAGE_BARS_EVALUATED, 52041)
    funnel.count_strategy("momentum_scalping", STAGE_STRATEGY_INVOKED, 18220)
    funnel.count_strategy("momentum_scalping", STAGE_RAW_SIGNALS, n_signals)
    for _ in range(n_signals):
        funnel.count_strategy("momentum_scalping", STAGE_VALIDITY_DROPPED)
        funnel.reject(
            REASON_VALIDITY["rrr_meets_minimum"], strategy="momentum_scalping"
        )
    funnel.set_stage(STAGE_ORDERS_PLACED, 0)
    funnel.set_stage(STAGE_CLOSED_TRADES, 0)
    return funnel


def ma_crossover_shape() -> SignalFunnel:
    """Funnel matching the ma_crossover bug.

    The strategy ran on every bar and never emitted a single signal,
    because its entry gate could never open.

    Returns:
        A populated SignalFunnel.
    """
    funnel = SignalFunnel(label="ma_crossover/BTC-USDC")
    funnel.count(STAGE_BARS_EVALUATED, 52041)
    funnel.count_strategy("ma_crossover", STAGE_STRATEGY_INVOKED, 18220)
    funnel.record_regime("trending_strong")
    funnel.set_stage(STAGE_CLOSED_TRADES, 0)
    return funnel


def traded_shape() -> SignalFunnel:
    """Funnel for a normal, trading run.

    Returns:
        A populated SignalFunnel.
    """
    funnel = SignalFunnel(label="mean_reversion/SUI-USDC")
    funnel.count(STAGE_BARS_EVALUATED, 5000)
    funnel.count_strategy("mean_reversion", STAGE_STRATEGY_INVOKED, 4200)
    funnel.count_strategy("mean_reversion", STAGE_RAW_SIGNALS, 90)
    funnel.count_strategy("mean_reversion", STAGE_VALIDITY_DROPPED, 20)
    funnel.reject(REASON_VALIDITY["volume_confirmation"], 20, "mean_reversion")
    funnel.set_stage(STAGE_ORDERS_PLACED, 70)
    funnel.set_stage(STAGE_CLOSED_TRADES, 62)
    return funnel


# ---------------------------------------------------------------------------
# Core funnel behaviour
# ---------------------------------------------------------------------------


class TestSignalFunnel:
    def test_counters_accumulate(self):
        funnel = SignalFunnel()
        funnel.count(STAGE_BARS_EVALUATED)
        funnel.count(STAGE_BARS_EVALUATED, 4)
        assert funnel.get(STAGE_BARS_EVALUATED) == 5
        assert funnel.get("never_touched") == 0

    def test_count_strategy_updates_global_and_breakdown(self):
        funnel = SignalFunnel()
        funnel.count_strategy("vwap_scalping", STAGE_RAW_SIGNALS, 3)
        funnel.count_strategy("mean_reversion", STAGE_RAW_SIGNALS, 2)
        assert funnel.get(STAGE_RAW_SIGNALS) == 5
        assert funnel.by_strategy["vwap_scalping"][STAGE_RAW_SIGNALS] == 3
        assert funnel.by_strategy["mean_reversion"][STAGE_RAW_SIGNALS] == 2

    def test_reject_attributes_reasons(self):
        funnel = SignalFunnel()
        funnel.reject(REASON_CONFIDENCE_GATE, 2, strategy="grid_trading")
        funnel.reject(REASON_EXEC_HEDGE_MODE)
        assert funnel.reasons[REASON_CONFIDENCE_GATE] == 2
        assert funnel.by_strategy["grid_trading"][REASON_CONFIDENCE_GATE] == 2
        assert funnel.reasons[REASON_EXEC_HEDGE_MODE] == 1

    def test_reason_constants_are_interned_not_fstrings(self):
        """Every validity reason is a module-level constant, not built per call."""
        first = REASON_VALIDITY["rrr_meets_minimum"]
        second = REASON_VALIDITY["rrr_meets_minimum"]
        assert first is second

    def test_merge_sums_every_dimension(self):
        a = momentum_shape(10)
        b = momentum_shape(5)
        a.merge(b)
        assert a.get(STAGE_RAW_SIGNALS) == 15
        assert a.get(STAGE_BARS_EVALUATED) == 52041 * 2
        assert a.reasons[REASON_VALIDITY["rrr_meets_minimum"]] == 15
        assert a.by_strategy["momentum_scalping"][STAGE_VALIDITY_DROPPED] == 15
        # set_stage values are absolute per-chunk but still additive on merge
        assert a.get(STAGE_CLOSED_TRADES) == 0

    def test_merge_ignores_null_funnel(self):
        funnel = traded_shape()
        before = dict(funnel.stages)
        funnel.merge(NULL_FUNNEL)
        assert funnel.stages == before

    def test_to_dict_is_json_safe(self):
        payload = momentum_shape().to_dict()
        text = json.dumps(payload)  # must not raise
        assert "validity:rrr_meets_minimum" in text
        assert payload["diagnosis"] == "all_discarded_downstream"
        assert payload["stages"][STAGE_RAW_SIGNALS] == 136

    def test_round_trip_from_dict(self):
        original = momentum_shape()
        rebuilt = SignalFunnel.from_dict(original.to_dict())
        assert rebuilt.stages == original.stages
        assert rebuilt.reasons == original.reasons
        assert rebuilt.by_strategy == original.by_strategy
        assert rebuilt.diagnose() == original.diagnose()

    def test_progress_stays_below_one(self):
        """Bands are 1.0 apart, so progress must never reach 1.0."""
        for funnel in (momentum_shape(), ma_crossover_shape(), traded_shape()):
            assert 0.0 <= funnel.progress() < 1.0

    def test_progress_is_monotone_in_depth(self):
        assert ma_crossover_shape().progress() < momentum_shape().progress()

    def test_top_reasons_sorted_desc(self):
        funnel = SignalFunnel()
        funnel.reject(REASON_VALIDITY["volume_confirmation"], 3)
        funnel.reject(REASON_VALIDITY["rrr_meets_minimum"], 9)
        top = funnel.top_reasons(2)
        assert top[0] == ["validity:rrr_meets_minimum", 9]
        assert top[1] == ["validity:volume_confirmation", 3]


class TestDiagnose:
    def test_no_data(self):
        assert SignalFunnel().diagnose() == "no_data"

    def test_never_invoked(self):
        funnel = SignalFunnel()
        funnel.count(STAGE_BARS_EVALUATED, 100)
        assert funnel.diagnose() == "never_invoked"

    def test_no_opportunities(self):
        assert ma_crossover_shape().diagnose() == "no_opportunities"

    def test_structurally_blocked(self):
        funnel = ma_crossover_shape()
        funnel.mark_structural_block("vwap_sd_entry_threshold_above_max")
        assert funnel.diagnose() == "structurally_blocked"

    def test_all_discarded_downstream(self):
        assert momentum_shape().diagnose() == "all_discarded_downstream"

    def test_traded(self):
        assert traded_shape().diagnose() == "traded"

    def test_binding_stage_on_a_profitable_run(self):
        """Even a traded run reports where the biggest attrition was."""
        assert traded_shape().binding_stage() == STAGE_VALIDITY_DROPPED

    def test_execution_block_is_the_binding_stage(self):
        funnel = SignalFunnel()
        funnel.count(STAGE_BARS_EVALUATED, 10)
        funnel.count(STAGE_STRATEGY_INVOKED, 10)
        funnel.count(STAGE_RAW_SIGNALS, 8)
        funnel.count(STAGE_EXECUTION_BLOCKED, 8)
        funnel.reject(REASON_EXEC_HEDGE_MODE, 8)
        assert funnel.diagnose() == "all_discarded_downstream"
        assert funnel.binding_stage() == STAGE_EXECUTION_BLOCKED


class TestNullFunnel:
    def test_all_methods_are_noops(self):
        funnel = NullFunnel()
        funnel.count(STAGE_BARS_EVALUATED, 5)
        funnel.count_strategy("x", STAGE_RAW_SIGNALS)
        funnel.reject(REASON_CONFIDENCE_GATE)
        funnel.record_regime("trending_strong")
        funnel.set_stage(STAGE_CLOSED_TRADES, 9)
        funnel.note("gate_metrics", {"rrr": 1})
        funnel.mark_structural_block("nope")
        assert funnel.get(STAGE_BARS_EVALUATED) == 0
        assert funnel.to_dict() == {}
        assert funnel.diagnose() == "no_data"
        assert funnel.enabled is False

    def test_is_the_strategy_manager_default(self):
        from trading_bot_v2.strategy_manager import StrategyManager

        manager = StrategyManager.__new__(StrategyManager)
        manager._funnel = NULL_FUNNEL
        assert manager.get_funnel().enabled is False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_bands_are_disjoint_and_ordered(self):
        traded = score_for_outcome(TrialOutcome.TRADED, objective_value=-999.0)
        discarded = score_for_outcome(
            TrialOutcome.ALL_DISCARDED_DOWNSTREAM, progress=0.999
        )
        none_fired = score_for_outcome(TrialOutcome.NO_OPPORTUNITIES, progress=0.999)
        never = score_for_outcome(TrialOutcome.NEVER_INVOKED, progress=0.999)
        assert traded > discarded > none_fired > never

    def test_traded_clamps_at_floor(self):
        assert (
            score_for_outcome(TrialOutcome.TRADED, objective_value=-500.0)
            == TRADED_SCORE_FLOOR
        )
        assert score_for_outcome(TrialOutcome.TRADED, objective_value=1.4) == 1.4

    def test_infeasible_has_no_score(self):
        with pytest.raises(ValueError):
            score_for_outcome(TrialOutcome.INFEASIBLE_CONFIG)

    def test_from_diagnosis_round_trip(self):
        for outcome in SCORE_BANDS:
            assert TrialOutcome.from_diagnosis(outcome.value) is outcome
        assert TrialOutcome.from_diagnosis("garbage") is TrialOutcome.NO_DATA

    def test_never_returns_negative_infinity(self):
        for outcome in SCORE_BANDS:
            if outcome is TrialOutcome.TRADED:
                value = score_for_outcome(outcome, objective_value=float("-inf"))
            else:
                value = score_for_outcome(outcome, progress=0.5)
            assert value > float("-inf")


class TestSuggestFix:
    def test_rrr_fix_names_the_sampled_ratio(self):
        fix = suggest_fix(
            momentum_shape().to_dict(),
            {"atr_stop_mult": 2.0, "atr_target_mult": 2.4},
        )
        assert "2.40/2.00" in fix and "1.20" in fix

    def test_no_signal_fix_mentions_entry_thresholds(self):
        fix = suggest_fix(ma_crossover_shape().to_dict(), {})
        assert "never emitted a signal" in fix


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


class TestReport:
    def test_renders_binding_constraint_and_fix(self):
        text = render_funnel_report(
            momentum_shape(),
            title="momentum_scalping",
            params={"atr_stop_mult": 2.0, "atr_target_mult": 2.4},
        )
        assert "SIGNAL FUNNEL: momentum_scalping" in text
        assert "BINDING CONSTRAINT: validity_dropped" in text
        assert "validity:rrr_meets_minimum" in text
        assert "SUGGESTED FIX:" in text
        assert "100.0% of raw" in text

    def test_shown_on_a_passing_run_too(self):
        text = render_funnel_report(traded_shape(), title="mean_reversion")
        assert "traded" in text
        assert "BINDING CONSTRAINT: validity_dropped" in text

    def test_accepts_a_stored_payload_without_derived_fields(self):
        payload = momentum_shape().to_dict()
        for key in ("diagnosis", "headline", "binding_stage", "top_reasons"):
            payload.pop(key, None)
        text = render_funnel_report(payload)
        assert "all_discarded_downstream" in text

    def test_empty_diagnostics_degrade_gracefully(self):
        text = render_funnel_report({})
        assert "No diagnostics recorded" in text
        assert funnel_one_liner({}) == "n/a"
        assert funnel_one_liner(None) == "n/a"

    def test_gate_metrics_section_omitted_without_calibration(self):
        """Phase 4 data is optional - the section must simply not appear."""
        text = render_funnel_report(momentum_shape())
        assert "GATE METRICS" not in text

    def test_gate_metrics_section_appears_when_present(self):
        funnel = momentum_shape()
        funnel.note("gate_metrics", {"rrr": {"min": 1.2, "max": 1.2}})
        assert "GATE METRICS" in render_funnel_report(funnel)

    def test_lines_stay_within_the_house_width(self):
        text = render_funnel_report(
            momentum_shape(), title="momentum_scalping | SUI-USDC"
        )
        assert all(len(line) <= 90 for line in text.splitlines())


# ---------------------------------------------------------------------------
# Regression pins - each maps to a real, previously invisible bug
# ---------------------------------------------------------------------------


class TestRegressionPins:
    def test_momentum_shape_is_attributed_to_the_rrr_flag(self):
        """136 signals, 0 trades: must not look like 'found nothing'."""
        funnel = momentum_shape()
        assert funnel.get(STAGE_RAW_SIGNALS) > 0
        assert funnel.get(STAGE_CLOSED_TRADES) == 0
        assert funnel.diagnose() == "all_discarded_downstream"
        assert funnel.binding_stage() == STAGE_VALIDITY_DROPPED
        assert funnel.top_reasons(1)[0][0] == "validity:rrr_meets_minimum"
        assert "rrr_meets_minimum" in funnel.headline()

    def test_ma_crossover_shape_is_not_confused_with_a_discard(self):
        """Invoked constantly, 0 raw signals: a different diagnosis."""
        funnel = ma_crossover_shape()
        assert funnel.get(STAGE_STRATEGY_INVOKED) > 0
        assert funnel.get(STAGE_RAW_SIGNALS) == 0
        assert funnel.diagnose() in (
            "no_opportunities",
            "structurally_blocked",
        )
        assert funnel.binding_stage() == STAGE_RAW_SIGNALS

    def test_the_two_bug_shapes_never_collide(self):
        assert momentum_shape().diagnose() != ma_crossover_shape().diagnose()

    def test_zero_trade_trial_scores_in_its_band_never_zero_never_inf(self):
        funnel = momentum_shape()
        outcome = TrialOutcome.from_diagnosis(funnel.diagnose())
        value = score_for_outcome(outcome, progress=funnel.progress())
        assert -100.0 <= value < -99.0
        assert value != 0.0
        assert value != float("-inf")

    def test_zero_trade_ranks_below_every_traded_result(self):
        worst_traded = score_for_outcome(TrialOutcome.TRADED, objective_value=-10_000.0)
        zero_trade = score_for_outcome(
            TrialOutcome.ALL_DISCARDED_DOWNSTREAM, progress=0.999
        )
        assert zero_trade < worst_traded

    def test_confidence_gate_shape_names_the_gate(self):
        """The stage nobody counted before: dropped for low confidence."""
        funnel = SignalFunnel()
        funnel.count(STAGE_BARS_EVALUATED, 100)
        funnel.count_strategy("vwap_scalping", STAGE_STRATEGY_INVOKED, 100)
        funnel.count_strategy("vwap_scalping", STAGE_RAW_SIGNALS, 40)
        funnel.count_strategy("vwap_scalping", STAGE_CONFIDENCE_DROPPED, 40)
        funnel.reject(REASON_CONFIDENCE_GATE, 40, "vwap_scalping")
        funnel.set_stage(STAGE_CLOSED_TRADES, 0)
        assert funnel.binding_stage() == STAGE_CONFIDENCE_DROPPED
        assert "MIN_SIGNAL_CONFIDENCE_FLOOR" in suggest_fix(funnel.to_dict())


# ---------------------------------------------------------------------------
# StrategyManager wiring (consumes the existing discard counters)
# ---------------------------------------------------------------------------


def _signal(**flags):
    """Build a Signal with all validity flags set except the overrides.

    Args:
        **flags: Validity flag overrides (e.g. rrr_meets_minimum=False).

    Returns:
        A Signal instance.
    """
    from trading_bot_v2.config import AssetClass, StrategyType
    from trading_bot_v2.models import OrderSide, Signal

    kwargs = {
        "volume_confirmation": True,
        "multi_timeframe_alignment": True,
        "support_resistance_valid": True,
        "rrr_meets_minimum": True,
        "liquidation_buffer_safe": True,
        "account_risk_ok": True,
        "margin_drawdown_ok": True,
        "forbidden_conditions_clear": True,
    }
    kwargs.update(flags)
    return Signal(
        strategy=StrategyType.MOMENTUM_SCALPING,
        asset="SUI-USDC",
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=102.4,
        confidence=0.6,
        **kwargs,
    )


class TestStrategyManagerWiring:
    def _manager(self, funnel):
        """Build a bare StrategyManager wired to a funnel.

        Args:
            funnel: The funnel to attach.

        Returns:
            A StrategyManager with only the counters initialised.
        """
        from trading_bot_v2.strategy_manager import StrategyManager

        manager = StrategyManager.__new__(StrategyManager)
        manager._signal_generated_counts = {}
        manager._signal_discarded_counts = {}
        manager._signal_discard_flags = {}
        manager._discard_alert_interval = 1000
        manager._funnel = NULL_FUNNEL
        manager.set_funnel(funnel)
        return manager

    def test_discard_feeds_both_the_counters_and_the_funnel(self):
        """The funnel consumes the existing discard accounting, not a copy."""
        funnel = SignalFunnel()
        manager = self._manager(funnel)
        signal = _signal(rrr_meets_minimum=False)

        failed = manager._record_discarded_signal(signal, "SUI-USDC")

        assert failed == ["rrr_meets_minimum"]
        stats = manager.get_signal_discard_stats()
        assert stats["momentum_scalping"]["discarded"] == 1
        assert funnel.get(STAGE_VALIDITY_DROPPED) == 1
        assert funnel.reasons[REASON_VALIDITY["rrr_meets_minimum"]] == 1
        assert funnel.by_strategy["momentum_scalping"][STAGE_VALIDITY_DROPPED] == 1

    def test_discard_with_null_funnel_still_counts_normally(self):
        manager = self._manager(NULL_FUNNEL)
        manager._record_discarded_signal(_signal(account_risk_ok=False), "S")
        stats = manager.get_signal_discard_stats()
        assert stats["momentum_scalping"]["failed_flags"] == {"account_risk_ok": 1}

    def test_conflict_drops_are_attributed(self):
        funnel = SignalFunnel()
        manager = self._manager(funnel)
        kept = _signal()
        dropped = _signal()
        manager._count_conflict_drops([kept, dropped], [kept])
        assert funnel.get("conflict_dropped") == 1
        assert funnel.by_strategy["momentum_scalping"]["conflict_dropped"] == 1

    def test_conflict_drops_are_free_with_a_null_funnel(self):
        manager = self._manager(NULL_FUNNEL)
        signal = _signal()
        manager._count_conflict_drops([signal, _signal()], [signal])
        assert manager.get_funnel().to_dict() == {}


# ---------------------------------------------------------------------------
# Phase 3 wiring
# ---------------------------------------------------------------------------


class _StubTrial:
    """Stand-in for an annotated Optuna trial."""

    def __init__(self, number, attrs, value=None):
        self.number = number
        self.user_attrs = attrs
        self.value = value
        self.params = {"atr_stop_mult": 2.0, "atr_target_mult": 2.4}


def _attrs_from(funnel: SignalFunnel) -> dict:
    """Build the user_attrs payload OptunaRunner would store.

    Args:
        funnel: Source funnel.

    Returns:
        A user_attrs-shaped dict.
    """
    payload = funnel.to_dict()
    return {
        "outcome": payload["diagnosis"],
        "headline": payload["headline"],
        "binding_stage": payload["binding_stage"],
        "funnel": payload["stages"],
        "top_reasons": payload["top_reasons"],
        "by_strategy": payload["by_strategy"],
        "regimes": payload["regimes"],
        "progress": payload["progress"],
        "suggested_fix": "",
    }


class TestReportWiring:
    def test_deepest_trial_picks_the_most_informative(self):
        from trading_bot_v2.optimization.run_optimize import deepest_trial

        shallow = _StubTrial(0, _attrs_from(ma_crossover_shape()))
        deep = _StubTrial(1, _attrs_from(momentum_shape()))
        uninstrumented = _StubTrial(2, {})

        class Study:
            trials = [shallow, deep, uninstrumented]

        assert deepest_trial(Study()).number == 1

    def test_deepest_trial_is_none_without_diagnostics(self):
        from trading_bot_v2.optimization.run_optimize import deepest_trial

        class Study:
            trials = [_StubTrial(0, {}), _StubTrial(1, {})]

        assert deepest_trial(Study()) is None

    def test_trial_payload_renders(self):
        from trading_bot_v2.optimization.run_optimize import (
            trial_funnel_payload,
        )

        payload = trial_funnel_payload(_StubTrial(7, _attrs_from(momentum_shape())))
        text = render_funnel_report(payload)
        assert "all_discarded_downstream" in text
        assert "BINDING CONSTRAINT: validity_dropped" in text

    def test_explain_payload_matches_the_optimizer_attrs(self):
        from trading_bot_v2.diagnostics.explain import (
            payload_from_trial,
            rank_trials,
            summarise,
        )

        deep = _StubTrial(1, _attrs_from(momentum_shape()), value=-99.5)
        shallow = _StubTrial(0, _attrs_from(ma_crossover_shape()), value=-199.5)
        assert payload_from_trial(_StubTrial(2, {})) == {}
        assert rank_trials([shallow, deep])[0] is deep
        counts = summarise([deep, shallow, _StubTrial(3, {})])
        assert counts["all_discarded_downstream"] == 1
        assert counts["no_opportunities"] == 1
        assert counts["unknown"] == 1

    def test_sweep_row_shows_the_outcome_for_a_silent_strategy(self):
        from trading_bot_v2.backtesting.run_strategy_sweep import (
            StrategyResult,
            _row,
        )

        result = StrategyResult("MomentumScalping")
        result.ok = True
        result.closed_trades = 0
        result.diagnostics = momentum_shape().to_dict()
        row = _row(result)
        assert "all_discarded_downstream" in row

    def test_sweep_row_without_diagnostics_says_na(self):
        from trading_bot_v2.backtesting.run_strategy_sweep import (
            StrategyResult,
            _row,
        )

        result = StrategyResult("MeanReversion")
        result.ok = True
        result.closed_trades = 0
        assert "n/a" in _row(result)

    def test_backtest_result_carries_diagnostics_by_default(self):
        from trading_bot_v2.backtesting.performance import BacktestResult

        result = BacktestResult(
            symbol="SUI-USDC",
            start="2024-01-01",
            end="2024-02-01",
            initial_capital=10000.0,
            final_equity=10000.0,
        )
        assert result.diagnostics == {}


# ---------------------------------------------------------------------------
# Hot-path overhead
# ---------------------------------------------------------------------------


class TestFunnelOverhead:
    """Pins the funnel's cost on the per-bar hot path.

    The funnel runs inside generate_signals_for_market, which a backtest
    calls once per 5m candle (tens of thousands of times). The budget is
    3% of a bar's work.

    The funnel calls are timed directly rather than by diffing two
    instrumented bar loops. The previous estimator ran a bar loop with and
    without the funnel and took the difference: it derived a ~0.06% answer
    from two ~0.35s wall-clock measurements, so each measurement's ~5%
    reproducibility noise landed on the result amplified roughly 100x. A
    NULL-vs-NULL control, whose true overhead is 0% by construction,
    reported as much as +5.5% -- the estimator could not resolve its own
    3% budget, and the test failed about one full-suite run in six.

    Timing the calls at a repeat count where they *are* the measurement,
    rather than a perturbation of one, drops that control to
    0.000% +/- 0.001% and holds under heavy CPU contention.
    """

    # Sized so each timed block runs ~0.15s: long enough to average out
    # scheduler noise, short enough to keep the whole class near a second.
    FUNNEL_REPS = 20000
    BAR_REPS = 60
    BAR_INNER = 10000
    REPEATS = 3

    # The documented contract: the funnel may cost at most 3% of a bar.
    BUDGET = 0.03
    # Early warning. Measured cost when this pin was written was ~0.06%,
    # so this trips at roughly 9x the current cost while still leaving 6x
    # of margin beneath the budget itself.
    CANARY = 0.005

    @staticmethod
    def _bar_work(n: int) -> float:
        """Stand-in for a bar's real signal-generation cost.

        Args:
            n: Inner loop size.

        Returns:
            An accumulated float (kept so the loop is not optimised out).
        """
        total = 0.0
        for i in range(n):
            total += (i * 1.000001) ** 0.5
        return total

    @classmethod
    def _time_funnel_calls(cls, funnel) -> float:
        """Time FUNNEL_REPS repeats of one bar's funnel call sequence.

        Args:
            funnel: SignalFunnel or NullFunnel.

        Returns:
            Elapsed wall-clock seconds for the whole block.
        """
        reason = REASON_VALIDITY["rrr_meets_minimum"]
        start = time.perf_counter()
        for _ in range(cls.FUNNEL_REPS):
            funnel.count(STAGE_BARS_EVALUATED)
            funnel.record_regime("trending_strong")
            funnel.count_strategy("momentum_scalping", STAGE_STRATEGY_INVOKED)
            funnel.count_strategy("momentum_scalping", STAGE_RAW_SIGNALS)
            funnel.count_strategy("momentum_scalping", STAGE_VALIDITY_DROPPED)
            funnel.reject(reason, strategy="momentum_scalping")
        return time.perf_counter() - start

    @classmethod
    def _time_bar_work(cls) -> float:
        """Time BAR_REPS bars' worth of stand-in signal-generation work.

        Returns:
            Elapsed wall-clock seconds for the whole block.
        """
        start = time.perf_counter()
        for _ in range(cls.BAR_REPS):
            cls._bar_work(cls.BAR_INNER)
        return time.perf_counter() - start

    @classmethod
    def _best(cls, fn, *args) -> float:
        """Return the fastest of REPEATS runs.

        Noise only ever inflates a wall-clock sample, so the minimum is
        the stable estimator.

        Args:
            fn: Callable returning elapsed seconds.
            *args: Arguments forwarded to fn.

        Returns:
            The smallest observed elapsed time.
        """
        return min(fn(*args) for _ in range(cls.REPEATS))

    @classmethod
    def _funnel_cost_per_bar(cls, funnel) -> float:
        """Seconds of funnel work attributable to a single bar."""
        return cls._best(cls._time_funnel_calls, funnel) / cls.FUNNEL_REPS

    @classmethod
    def _bar_cost(cls) -> float:
        """Seconds of signal-generation work in a single bar."""
        return cls._best(cls._time_bar_work) / cls.BAR_REPS

    def test_overhead_within_budget(self):
        """The funnel's incremental cost stays inside the 3% budget."""
        overhead = (
            self._funnel_cost_per_bar(SignalFunnel())
            - self._funnel_cost_per_bar(NULL_FUNNEL)
        ) / self._bar_cost()

        assert overhead < self.BUDGET, (
            f"signal funnel costs {overhead:.3%} of a bar's work, over the "
            f"{self.BUDGET:.0%} hot-path budget"
        )
        assert overhead < self.CANARY, (
            f"signal funnel costs {overhead:.3%} of a bar's work. That is "
            f"still inside the {self.BUDGET:.0%} budget, but far above the "
            f"~0.06% measured when this pin was written, so something got "
            f"materially slower. If the extra cost is deliberate, raise "
            f"CANARY and say why."
        )

    def test_measurement_resolves_its_own_budget(self):
        """Control: NULL against NULL, whose true overhead is exactly 0.

        Guards the pin above. If this ever reports a number anywhere near
        the budget, the estimator has stopped being able to tell a real
        regression from machine noise and the assertion above is
        worthless -- which is precisely how the previous version of this
        test came to cry wolf.
        """
        noise = (
            abs(
                self._funnel_cost_per_bar(NULL_FUNNEL)
                - self._funnel_cost_per_bar(NULL_FUNNEL)
            )
            / self._bar_cost()
        )

        assert noise < self.CANARY, (
            f"measurement noise floor is {noise:.3%} of a bar's work, which "
            f"is too close to the {self.BUDGET:.0%} budget for the overhead "
            f"assertion to mean anything on this machine"
        )

    def test_null_funnel_allocates_no_state(self):
        assert not hasattr(NULL_FUNNEL, "__dict__")
