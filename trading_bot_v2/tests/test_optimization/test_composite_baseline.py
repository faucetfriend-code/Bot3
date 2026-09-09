"""Tests for the composite tuner's adopted-params (baseline) third arm.

The arm exists so leg 2 of the pre-registered adoption rule ("fresh
tuned must also beat the currently adopted params out-of-sample") is
measurable. Nothing here runs a real backtest: the adapter is a stub and
scores are dictated per (params, state).
"""

import argparse
import json

import pytest

from trading_bot_v2.optimization.run_composite_tuning import (
    _baseline_state_scores,
    _cached_backtest,
    _load_baseline_params,
    _summarize,
)

STATES = ["vol_low:trend", "vol_mid:trend", "vol_high:neutral"]

ADOPTED = {
    "vol_low:trend": {"rsi_oversold": 32.75, "bb_std_dev": 2.71},
    "vol_mid:trend": {"rsi_oversold": 32.72, "bb_std_dev": 2.45},
}


class _StubAdapter:
    """Records every backtest call and hands back canned trades."""

    def __init__(self, score_by_param=None):
        self.calls = []
        self.score_by_param = score_by_param or {}

    def run_backtest(
        self, strategy, params, start, end, symbol=None, initial_capital=None
    ):
        self.calls.append({"params": dict(params), "start": start, "end": end})
        return {"params": dict(params)}

    def get_regime_trades(self, result, state):
        return [{"pnl": 1.0}, {"pnl": 2.0}]

    def calculate_objective_from_trades(self, trades, objective, capital, start, end):
        return self.score_by_param.get(
            json.dumps(self._key(trades), sort_keys=True), 0.0
        )

    @staticmethod
    def _key(trades):
        return len(trades)


def _args(**kw):
    base = {
        "strategy": "mean_reversion",
        "symbol": "BTC-USDC",
        "objective": "sharpe_ratio",
        "capital": 10000.0,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestLoadBaselineParams:
    def test_none_path_is_no_arm(self):
        assert _load_baseline_params(None, STATES) == {}

    def test_loads_and_lowercases_states(self, tmp_path):
        path = tmp_path / "adopted.json"
        path.write_text(json.dumps({"VOL_LOW:Trend": {"a": 1.0}}), encoding="utf-8")
        assert _load_baseline_params(str(path), STATES) == {"vol_low:trend": {"a": 1.0}}

    def test_states_outside_the_run_are_dropped(self, tmp_path):
        path = tmp_path / "adopted.json"
        path.write_text(
            json.dumps({"vol_low:trend": {"a": 1.0}, "vol_nope:trend": {"a": 2.0}}),
            encoding="utf-8",
        )
        assert set(_load_baseline_params(str(path), STATES)) == {"vol_low:trend"}

    def test_malformed_file_raises_rather_than_silently_dropping(self, tmp_path):
        path = tmp_path / "adopted.json"
        path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        with pytest.raises(ValueError):
            _load_baseline_params(str(path), STATES)

    def test_non_object_entry_raises(self, tmp_path):
        path = tmp_path / "adopted.json"
        path.write_text(json.dumps({"vol_low:trend": 3}), encoding="utf-8")
        with pytest.raises(ValueError):
            _load_baseline_params(str(path), STATES)


class TestBaselineScoredOnTestWindow:
    def test_uses_the_unseen_test_window_not_the_train_window(self):
        adapter = _StubAdapter()
        cache = {}
        _baseline_state_scores(
            adapter, cache, _args(), ADOPTED, "2025-01-01", "2025-07-01"
        )
        assert len(adapter.calls) == 2
        for call in adapter.calls:
            assert call["start"] == "2025-01-01"
            assert call["end"] == "2025-07-01"
        assert {json.dumps(c["params"], sort_keys=True) for c in adapter.calls} == {
            json.dumps(p, sort_keys=True) for p in ADOPTED.values()
        }

    def test_returns_a_score_cell_per_baseline_state(self):
        adapter = _StubAdapter()
        out = _baseline_state_scores(
            adapter, {}, _args(), ADOPTED, "2025-01-01", "2025-07-01"
        )
        assert set(out) == set(ADOPTED)
        assert out["vol_low:trend"]["n"] == 2
        assert out["vol_low:trend"]["pnl"] == 3.0

    def test_no_baseline_means_no_extra_backtests(self):
        adapter = _StubAdapter()
        assert (
            _baseline_state_scores(adapter, {}, _args(), {}, "2025-01-01", "2025-07-01")
            == {}
        )
        assert adapter.calls == []

    def test_shared_cache_reuses_an_identical_tuned_run(self):
        adapter = _StubAdapter()
        cache = {}
        params = ADOPTED["vol_low:trend"]
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
        _baseline_state_scores(
            adapter,
            cache,
            _args(),
            {"vol_low:trend": params},
            "2025-01-01",
            "2025-07-01",
        )
        assert len(adapter.calls) == 1


class TestSummarizeThreeArms:
    def _fold(self, tuned, default, baseline=None):
        cell = {"test_tuned": {"score": tuned}, "test_default": {"score": default}}
        if baseline is not None:
            cell["test_baseline"] = {"score": baseline}
        return {"states": {"vol_low:trend": cell}}

    def test_states_without_a_baseline_keep_the_old_shape(self):
        summary = _summarize(
            [self._fold(1.0, 0.5), self._fold(2.0, 0.5)], ["vol_low:trend"]
        )
        assert summary["vol_low:trend"] == {
            "folds": 2,
            "tuned": 1.5,
            "default": 0.5,
            "edge": 1.0,
        }

    def test_baseline_arm_is_aggregated_and_paired(self):
        summary = _summarize(
            [self._fold(1.0, 0.5, 0.8), self._fold(2.0, 0.5, 1.0)], ["vol_low:trend"]
        )["vol_low:trend"]
        assert summary["baseline_folds"] == 2
        assert summary["baseline"] == pytest.approx(0.9)
        assert summary["baseline_tuned"] == pytest.approx(1.5)
        assert summary["edge_vs_baseline"] == pytest.approx(0.6)

    def test_baseline_edge_is_paired_over_its_own_folds(self):
        # Fold 2 has no baseline score: it must not drag the paired
        # tuned mean that edge_vs_baseline is built from.
        summary = _summarize(
            [self._fold(1.0, 0.5, 0.8), self._fold(3.0, 0.5)], ["vol_low:trend"]
        )["vol_low:trend"]
        assert summary["folds"] == 2
        assert summary["tuned"] == pytest.approx(2.0)
        assert summary["baseline_folds"] == 1
        assert summary["baseline_tuned"] == pytest.approx(1.0)
        assert summary["edge_vs_baseline"] == pytest.approx(0.2)

    def test_unmeasured_state_still_reports_zero_folds(self):
        summary = _summarize([{"states": {}}], ["vol_low:trend"])
        assert summary["vol_low:trend"] == {"folds": 0}

    def test_baseline_survives_an_unmeasured_tuned_arm(self):
        # Tuned has no score, so there is nothing to pair with: the
        # baseline keys are absent rather than a bare number that would
        # read as an edge.
        fold = {
            "states": {
                "vol_low:trend": {
                    "verdict": "insufficient_data",
                    "test_baseline": {"score": 0.8},
                }
            }
        }
        summary = _summarize([fold], ["vol_low:trend"])["vol_low:trend"]
        assert summary["folds"] == 0
        assert "edge_vs_baseline" not in summary
