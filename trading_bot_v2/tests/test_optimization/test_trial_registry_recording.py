"""The composite path must count toward the DSR's multiple-testing N.

``run_composite_tuning`` drives Optuna directly rather than through
``OptunaRunner``, and until 2026-08-02 that meant it recorded nothing in
the ``trial_registry`` - the table ``validation/gate.py`` and
``validation/runner.py`` read the Deflated Sharpe Ratio's N from. The
whole campaign from 2026-07-28 on was invisible to the deflation, which
therefore penalised too little.

Two halves are pinned here: the live recorder that keeps future runs
counted, and the backfill that reconstructs the runs already made. The
backfill's de-duplication rule is the subtle part - identical output
does NOT mean a single search, and collapsing on it would re-create the
under-count.
"""

import json

import optuna
import pytest

from trading_bot_v2.database import DatabaseManager
from trading_bot_v2.optimization import backfill_trial_registry as bf
from trading_bot_v2.optimization import run_composite_tuning as rct


class _FakeTrial:
    def __init__(self, state, value):
        self.state = state
        self.value = value


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials


COMPLETE = optuna.trial.TrialState.COMPLETE
PRUNED = optuna.trial.TrialState.PRUNED
FAIL = optuna.trial.TrialState.FAIL


def _record(study, objective="sharpe_ratio"):
    return rct.record_fold_trials(
        study,
        "mean_reversion",
        "BTC-USDC",
        objective,
        ["2024-01-01", "2025-01-01"],
        ["2025-01-01", "2025-07-01"],
    )


def _rows():
    return DatabaseManager().get_trial_registry(strategy="mean_reversion")


class TestRecordFoldTrials:
    def test_counts_completed_and_pruned(self):
        study = _FakeStudy(
            [
                _FakeTrial(COMPLETE, 1.0),
                _FakeTrial(COMPLETE, 2.0),
                _FakeTrial(PRUNED, 0.5),
                _FakeTrial(FAIL, None),
            ]
        )
        before = len(_rows())

        row_id = _record(study)

        assert row_id is not None
        rows = _rows()
        assert len(rows) == before + 1
        assert rows[0]["n_trials"] == 3

    def test_regime_is_null_because_the_pool_is_shared(self):
        """One trial pool serves all six states; attributing it to one
        would be false, and NULL counts toward every regime query."""
        _record(_FakeStudy([_FakeTrial(COMPLETE, 1.0)]))

        assert _rows()[0]["regime"] is None

    def test_source_identifies_the_fold_window(self):
        _record(_FakeStudy([_FakeTrial(COMPLETE, 1.0)]))

        source = _rows()[0]["source"]
        assert "mean_reversion" in source
        assert "BTC-USDC" in source
        assert "2025-01-01_2025-07-01" in source

    def test_variance_only_for_sharpe_objective(self):
        trials = [_FakeTrial(COMPLETE, v) for v in (1.0, 2.0, 3.0)]

        _record(_FakeStudy(trials), objective="profit_factor")
        assert _rows()[0]["sr_variance"] is None

        _record(_FakeStudy(trials), objective="sharpe_ratio")
        assert _rows()[0]["sr_variance"] == pytest.approx(1.0)

    def test_sentinel_trials_are_excluded_from_variance(self):
        """-1e9 means "no state scored", not "Sharpe of -1e9"."""
        clean = _FakeStudy([_FakeTrial(COMPLETE, v) for v in (1.0, 2.0, 3.0)])
        _record(clean)
        without = _rows()[0]["sr_variance"]

        polluted = _FakeStudy(
            [_FakeTrial(COMPLETE, v) for v in (1.0, 2.0, 3.0)]
            + [_FakeTrial(COMPLETE, -1e9)]
        )
        _record(polluted)
        with_sentinel = _rows()[0]["sr_variance"]

        assert with_sentinel == pytest.approx(without)

    def test_no_row_when_nothing_scored(self):
        before = len(_rows())

        assert _record(_FakeStudy([_FakeTrial(FAIL, None)])) is None
        assert len(_rows()) == before

    def test_database_failure_does_not_kill_the_run(self, monkeypatch):
        """A registry write must never destroy a multi-hour tuning run."""
        import trading_bot_v2.database as db_mod

        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("database is gone")

        monkeypatch.setattr(db_mod, "DatabaseManager", _Boom)

        assert _record(_FakeStudy([_FakeTrial(COMPLETE, 1.0)])) is None


class TestRecordingIsOptIn:
    def test_recording_is_on_by_default(self):
        args = rct._parse_args(["--strategy", "mean_reversion"])

        assert args.no_trial_registry is False

    def test_the_flag_is_recorded_in_run_config(self):
        args = rct._parse_args(["--strategy", "mean_reversion", "--no-trial-registry"])

        block = rct.build_run_config(args, ["vol_low:trend"], 6, [])

        assert block["args"]["no_trial_registry"] is True


def _write_report(path, strategy="mean_reversion", folds=2, trials=25, marker=0.0):
    payload = {
        "strategy": strategy,
        "symbol": "BTC-USDC",
        "trials_per_fold": trials,
        "folds": [
            {
                "train": [f"202{i}-01-01", f"202{i}-07-01"],
                "test": [f"202{i}-07-01", f"202{i + 1}-01-01"],
                "states": {"vol_low:trend": {"score": marker + i}},
            }
            for i in range(folds)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestBackfillPlan:
    def test_one_row_per_fold(self, tmp_path):
        _write_report(tmp_path / "a.json", folds=3, trials=25)

        rows = bf.plan([str(tmp_path / "a.json")])

        assert len(rows) == 3
        assert all(r["n_trials"] == 25 for r in rows)

    def test_identical_results_still_count_as_two_searches(self, tmp_path):
        """Two runs spend two budgets even when their output coincides.

        The real case: composite_grid_pf_gateoff and
        composite_grid_pf_gateenforce are byte-identical, because
        GridTrading is in DEFAULT_GATE_EXEMPT and the two directional
        gate arms cannot differ - but both burned 125 trials. Collapsing
        them would under-count N, and under-counting deflates too
        little, which is the error this backfill exists to fix.
        """
        _write_report(tmp_path / "gateoff.json", folds=2)
        _write_report(tmp_path / "gateenforce.json", folds=2)

        rows = bf.plan(
            [
                str(tmp_path / "gateoff.json"),
                str(tmp_path / "gateenforce.json"),
            ]
        )

        assert len(rows) == 4
        assert len({r["source"] for r in rows}) == 4

    def test_driver_scratch_copies_are_skipped(self, tmp_path):
        """monthly_retune writes _tune/ then copies it; one search."""
        _write_report(tmp_path / "monthly" / "_tune" / "r.json", folds=2)
        _write_report(tmp_path / "monthly" / "r.json", folds=2)

        rows = bf.plan(
            [
                str(tmp_path / "monthly" / "_tune" / "r.json"),
                str(tmp_path / "monthly" / "r.json"),
            ]
        )

        assert len(rows) == 2
        assert all("_tune" not in r["source"] for r in rows)

    def test_intermediate_can_be_forced_in(self, tmp_path):
        _write_report(tmp_path / "_tune" / "r.json", folds=2)

        rows = bf.plan([str(tmp_path / "_tune" / "r.json")], include_intermediate=True)

        assert len(rows) == 2

    def test_sources_are_stable_across_calls(self, tmp_path):
        """Idempotency depends on this."""
        path = str(_write_report(tmp_path / "a.json", folds=2))

        assert [r["source"] for r in bf.plan([path])] == [
            r["source"] for r in bf.plan([path])
        ]

    def test_edited_results_produce_new_sources(self, tmp_path):
        """A changed artifact must not silently reuse the old id."""
        path = tmp_path / "a.json"
        _write_report(path, folds=2, marker=0.0)
        first = [r["source"] for r in bf.plan([str(path)])]

        _write_report(path, folds=2, marker=9.0)
        second = [r["source"] for r in bf.plan([str(path)])]

        assert first != second

    def test_non_composite_json_is_ignored(self, tmp_path):
        (tmp_path / "other.json").write_text('{"hello": 1}', encoding="utf-8")
        (tmp_path / "broken.json").write_text("not json", encoding="utf-8")

        assert (
            bf.plan([str(tmp_path / "other.json"), str(tmp_path / "broken.json")]) == []
        )

    def test_is_intermediate_matches_directories_not_substrings(self):
        assert bf.is_intermediate("out/monthly/_tune/r.json")
        assert not bf.is_intermediate("out/my_tuned_run/r.json")
