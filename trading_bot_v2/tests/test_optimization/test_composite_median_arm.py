"""Tests for the composite tuner's prequential-median (fourth) arm.

The arm scores the object the monthly cadence actually DEPLOYS - the
coordinate-wise median of the fold winners - instead of the fold winners
themselves. The property that matters most is leak-freeness: fold k's
median must be built from folds 1..k-1 and from nothing later. Nothing
here runs a real backtest; the adapter is a stub whose score is a pure
function of the parameter vector.
"""

import argparse
import json

import pytest

from trading_bot_v2.optimization import run_composite_tuning as rct
from trading_bot_v2.optimization.run_composite_tuning import (
    _arm_state_scores,
    _cached_backtest,
    _summarize,
    coordinate_median,
    prequential_medians,
    run,
)

STATE = "vol_low:trend"


def _args(**kw):
    base = {
        "strategy": "mean_reversion",
        "symbol": "BTC-USDC",
        "objective": "sharpe_ratio",
        "capital": 10000.0,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class _ParamScoreAdapter:
    """Stub adapter whose score is dictated by one parameter.

    ``rsi_oversold`` is carried through the fake trades so the objective
    can recover it, which makes the winning trial of every fold exactly
    "the trial with the largest rsi_oversold" - deterministic without
    pinning Optuna's sampler.
    """

    def __init__(self, trades_per_state: int = 12):
        self.calls = []
        self.trades_per_state = trades_per_state

    def run_backtest(
        self, strategy, params, start, end, symbol=None, initial_capital=None
    ):
        self.calls.append({"params": dict(params), "start": start, "end": end})
        return {"params": dict(params)}

    def get_regime_trades(self, result, state):
        pnl = float(result["params"].get("rsi_oversold", 0.0))
        return [{"pnl": pnl}] * self.trades_per_state

    def calculate_objective_from_trades(self, trades, objective, capital, start, end):
        return trades[0]["pnl"] / 100.0


class TestCoordinateMedian:
    def test_empty_history_has_no_median(self):
        assert coordinate_median([]) is None

    def test_single_vector_is_its_own_median(self):
        assert coordinate_median([{"a": 1.23456}]) == {"a": 1.2346}

    def test_each_coordinate_is_taken_independently(self):
        # a's median comes from the middle vector, b's from a different
        # one: the result is a vector nobody proposed.
        got = coordinate_median(
            [{"a": 1.0, "b": 9.0}, {"a": 2.0, "b": 1.0}, {"a": 3.0, "b": 5.0}]
        )
        assert got == {"a": 2.0, "b": 5.0}

    def test_rounding_matches_the_deployed_median(self):
        from trading_bot_v2.optimization.monthly_retune import _fresh_medians

        winners = [{"a": 1.111111}, {"a": 2.222222}, {"a": 3.333333}]
        report = {"folds": [{"states": {STATE: {"params": w}}} for w in winners]}
        assert _fresh_medians(report)[STATE] == coordinate_median(winners)


class TestPrequentialMedians:
    def test_state_with_no_prior_winner_is_absent(self):
        assert prequential_medians({}, [STATE]) == {}

    def test_only_the_listed_states_are_built(self):
        prior = {STATE: [{"a": 1.0}], "vol_mid:trend": [{"a": 2.0}]}
        assert set(prequential_medians(prior, [STATE])) == {STATE}


class TestArmCacheReuse:
    def test_median_equal_to_an_existing_vector_costs_no_backtest(self):
        adapter = _ParamScoreAdapter()
        cache = {}
        params = {"rsi_oversold": 30.0}
        _cached_backtest(
            adapter,
            cache,
            "mean_reversion",
            params,
            "2025-01-01",
            "2025-07-01",
            "BTC-USDC",
            10000.0,
        )
        _arm_state_scores(
            adapter, cache, _args(), {STATE: dict(params)}, "2025-01-01", "2025-07-01"
        )
        assert len(adapter.calls) == 1

    def test_a_new_vector_does_cost_a_backtest(self):
        adapter = _ParamScoreAdapter()
        cache = {}
        _cached_backtest(
            adapter,
            cache,
            "mean_reversion",
            {"rsi_oversold": 30.0},
            "2025-01-01",
            "2025-07-01",
            "BTC-USDC",
            10000.0,
        )
        _arm_state_scores(
            adapter,
            cache,
            _args(),
            {STATE: {"rsi_oversold": 31.0}},
            "2025-01-01",
            "2025-07-01",
        )
        assert len(adapter.calls) == 2


class TestSummarizeMedianArm:
    def _fold(self, tuned=None, default=None, median=None, baseline=None):
        cell = {}
        for key, value in (
            ("test_tuned", tuned),
            ("test_default", default),
            ("test_median", median),
            ("test_baseline", baseline),
        ):
            if value is not None:
                cell[key] = {"score": value}
        return {"states": {STATE: cell}}

    def test_states_without_a_median_arm_keep_the_old_shape(self):
        summary = _summarize([self._fold(1.0, 0.5), self._fold(2.0, 0.5)], [STATE])
        assert summary[STATE] == {"folds": 2, "tuned": 1.5, "default": 0.5, "edge": 1.0}

    def test_median_vs_default_is_paired_over_median_folds_only(self):
        # Fold 1 has no median arm (as in a real run): its default score
        # must not enter the median-vs-default comparison.
        summary = _summarize(
            [
                self._fold(1.0, 5.0),
                self._fold(2.0, 0.5, median=0.9),
                self._fold(3.0, 0.5, median=1.1),
            ],
            [STATE],
        )[STATE]
        assert summary["folds"] == 3
        assert summary["median_folds"] == 2
        assert summary["median"] == pytest.approx(1.0)
        assert summary["median_default"] == pytest.approx(0.5)
        assert summary["edge_median_vs_default"] == pytest.approx(0.5)

    def test_median_vs_tuned_gap_is_paired(self):
        summary = _summarize(
            [
                self._fold(1.0, 0.5),
                self._fold(2.0, 0.5, median=1.0),
                self._fold(4.0, 0.5, median=2.0),
            ],
            [STATE],
        )[STATE]
        assert summary["tuned"] == pytest.approx(7.0 / 3)
        assert summary["median_tuned"] == pytest.approx(3.0)
        assert summary["median_on_tuned_folds"] == pytest.approx(1.5)
        assert summary["edge_median_vs_tuned"] == pytest.approx(-1.5)

    def test_median_vs_adopted_baseline_is_reported(self):
        summary = _summarize(
            [
                self._fold(1.0, 0.5, baseline=0.6),
                self._fold(2.0, 0.5, median=1.4, baseline=0.4),
            ],
            [STATE],
        )[STATE]
        assert summary["median_baseline_folds"] == 1
        assert summary["median_baseline"] == pytest.approx(0.4)
        assert summary["edge_median_vs_baseline"] == pytest.approx(1.0)

    def test_median_survives_an_unmeasured_tuned_arm(self):
        summary = _summarize(
            [
                {
                    "states": {
                        STATE: {
                            "verdict": "insufficient_data",
                            "test_default": {"score": 0.5},
                            "test_median": {"score": 1.0},
                        }
                    }
                }
            ],
            [STATE],
        )[STATE]
        assert summary["folds"] == 0
        assert summary["median_folds"] == 1
        assert "edge_median_vs_tuned" not in summary


class TestRunUsesOnlyPriorFolds:
    """End-to-end over four folds with a stubbed adapter.

    This is the correctness property the arm exists for: if fold 3's
    median were built from all four folds it would encode the outcome of
    windows it is about to be scored on.
    """

    @pytest.fixture
    def report(self, tmp_path, monkeypatch):
        adapter = _ParamScoreAdapter()
        monkeypatch.setattr(rct, "OptimizationAdapter", lambda: adapter)
        path = tmp_path / "composite.json"
        code = run(
            [
                "--strategy",
                "mean_reversion",
                "--symbol",
                "BTC-USDC",
                "--start",
                "2020-01-01",
                "--end",
                "2025-01-01",
                "--train-months",
                "12",
                "--test-months",
                "12",
                "--trials",
                "5",
                "--states",
                STATE,
                "--report",
                str(path),
            ]
        )
        assert code == 0
        return json.loads(path.read_text(encoding="utf-8"))

    def _winner(self, report, fold_no):
        return report["folds"][fold_no - 1]["states"][STATE]["params"]

    def _cell(self, report, fold_no):
        return report["folds"][fold_no - 1]["states"][STATE]

    def test_four_folds_with_distinct_winners(self, report):
        assert len(report["folds"]) == 4
        winners = [self._winner(report, k) for k in (1, 2, 3, 4)]
        assert len({json.dumps(w, sort_keys=True) for w in winners}) == 4

    def test_fold_one_has_no_median_arm(self, report):
        cell = self._cell(report, 1)
        assert "median_params" not in cell
        assert "test_median" not in cell

    def test_fold_two_median_is_exactly_fold_ones_winner(self, report):
        cell = self._cell(report, 2)
        assert cell["median_from_folds"] == 1
        assert cell["median_params"] == coordinate_median([self._winner(report, 1)])

    def test_fold_three_median_comes_from_folds_one_and_two(self, report):
        cell = self._cell(report, 3)
        assert cell["median_from_folds"] == 2
        assert cell["median_params"] == coordinate_median(
            [self._winner(report, k) for k in (1, 2)]
        )

    def test_fold_three_median_excludes_folds_three_and_four(self, report):
        got = self._cell(report, 3)["median_params"]
        for leaked in ((1, 2, 3), (1, 2, 3, 4), (1, 2, 4)):
            assert got != coordinate_median([self._winner(report, k) for k in leaked])

    def test_fold_four_median_comes_from_the_first_three(self, report):
        cell = self._cell(report, 4)
        assert cell["median_from_folds"] == 3
        assert cell["median_params"] == coordinate_median(
            [self._winner(report, k) for k in (1, 2, 3)]
        )

    def test_median_is_scored_on_the_folds_own_test_window(self, report):
        cell = self._cell(report, 3)
        assert cell["test_median"]["score"] is not None
        assert cell["test_median"]["n"] == 12

    def test_summary_reports_three_median_folds(self, report):
        summary = report["summary"][STATE]
        assert summary["folds"] == 4
        assert summary["median_folds"] == 3
        assert "edge_median_vs_default" in summary
        assert "edge_median_vs_tuned" in summary
