"""Tests for identity-derived per-study Optuna seeding.

Until 2026-08-02 ``OptunaRunner._build_sampler`` hard-coded ``seed=42``
for every study it ever built - every strategy, symbol, regime,
objective and window. Measured: all 13 studies stored in
``optimization_studies.db`` open on the identical trial 0, and study 2
(which pruned every trial, so TPE never acquired an observation and kept
drawing from the seeded path) repeats 11 of study 1's 25 trials despite
being a different regime. ``run_regime_optimization`` prints those
studies side by side as per-regime findings.

The same constant also meant every window of a walk-forward run and
every fold of a chunked walk-forward searched identically, so the
windows were not the independent trials the aggregate out-of-sample
statistics assume.

These tests pin the property that replaced it: a study's seed is a pure
function of the study's IDENTITY (base seed, strategy, symbols, regime,
objective, window series), so nothing positional can influence it, the
derivation is stable across processes with different PYTHONHASHSEED
values, and distinct experiments get distinct seeds.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import trading_bot_v2.optimization.optuna_runner as optuna_runner_module
from trading_bot_v2.optimization.optuna_runner import (
    STUDY_SEED_NAMESPACE,
    OptunaRunner,
    canonical_windows,
    study_seed,
)

WINDOWS = [("2024-01-01", "2024-03-01")]
OTHER_WINDOWS = [("2024-03-01", "2024-05-01")]


def _seed(
    base=0,
    strategy="mean_reversion",
    symbols=("BTC-USDC",),
    regime="RANGING_CALM",
    objective="sharpe_ratio",
    windows=WINDOWS,
):
    return study_seed(base, strategy, list(symbols), regime, objective, windows)


class TestDeterminism:
    """Same identity -> same seed, always."""

    def test_repeated_calls_agree(self):
        assert _seed() == _seed() == _seed()

    def test_seed_is_a_valid_optuna_seed(self):
        for base in (0, 1, 7, 12345, -3):
            value = _seed(base=base)
            assert isinstance(value, int)
            assert 0 <= value <= 0xFFFFFFFF

    def test_derivation_is_the_documented_one(self):
        """A reader must be able to recompute a reported seed by hand."""
        canonical = "|".join(
            [
                STUDY_SEED_NAMESPACE,
                "0",
                "mean_reversion",
                "BTC-USDC",
                "RANGING_CALM",
                "sharpe_ratio",
                "2024-01-01..2024-03-01",
            ]
        )
        expected = (
            int.from_bytes(
                hashlib.sha256(canonical.encode("utf-8")).digest()[:4], "big"
            )
            & 0xFFFFFFFF
        )
        assert _seed() == expected

    def test_pooled_study_has_a_stable_empty_regime_component(self):
        assert _seed(regime=None) == _seed(regime="")

    def test_regime_case_does_not_change_the_seed(self):
        """The runner normalizes regimes; casing must not fork the seed."""
        assert _seed(regime="ranging_calm") == _seed(regime="RANGING_CALM")


class TestIndependentOfPosition:
    """The defect class: a seed must not depend on when a study is built."""

    def test_no_constant_seed_default_exists(self):
        """_build_sampler must not be able to silently reuse a default."""
        import inspect

        params = inspect.signature(OptunaRunner._build_sampler).parameters
        assert list(params) == ["self", "sampler", "n_trials", "seed"]
        assert params["seed"].default is inspect.Parameter.empty

    def test_same_identity_at_any_position_in_any_sequence(self):
        """Two runs whose study sequences overlap but are offset."""
        run_a = [OTHER_WINDOWS, WINDOWS]
        run_b = [WINDOWS]
        seeds_a = [_seed(windows=w) for w in run_a]
        seeds_b = [_seed(windows=w) for w in run_b]
        assert seeds_a[1] == seeds_b[0]

    def test_trial_budget_does_not_change_the_seed(self, tmp_path):
        """A 50-trial pilot must be a prefix of the 100-trial run."""
        runner = _bare_runner(tmp_path)
        first = _draws(runner._build_sampler("tpe", 50, 123), n=3)
        second = _draws(runner._build_sampler("tpe", 100, 123), n=3)
        assert first == second


class TestDistinctStudiesDistinctSeeds:
    """Everything that makes two studies different experiments."""

    def test_regime_changes_the_seed(self):
        assert _seed(regime="RANGING_CALM") != _seed(regime="RANGING_VOLATILE")

    def test_strategy_changes_the_seed(self):
        assert _seed(strategy="mean_reversion") != _seed(strategy="grid_trading")

    def test_symbol_changes_the_seed(self):
        assert _seed(symbols=("BTC-USDC",)) != _seed(symbols=("ETH-USDC",))

    def test_objective_changes_the_seed(self):
        assert _seed(objective="sharpe_ratio") != _seed(objective="profit_factor")

    def test_window_changes_the_seed(self):
        assert _seed(windows=WINDOWS) != _seed(windows=OTHER_WINDOWS)

    def test_every_window_bound_moves_the_seed(self):
        base = _seed()
        for windows in (
            [("2024-01-02", "2024-03-01")],
            [("2024-01-01", "2024-03-02")],
        ):
            assert _seed(windows=windows) != base

    def test_window_count_moves_the_seed(self):
        assert _seed(windows=WINDOWS) != _seed(windows=WINDOWS + OTHER_WINDOWS)

    def test_the_thirteen_stored_studies_would_now_differ(self):
        """The measured before-state: distinct regimes, one shared draw."""
        seeds = {
            _seed(regime=r)
            for r in (
                "RANGING_CALM",
                "RANGING_VOLATILE",
                "TRENDING_STRONG",
                "TRENDING_MODERATE",
                "INDECISIVE",
            )
        }
        assert len(seeds) == 5


class TestOrderingCannotChangeTheSeed:
    """optimize_chunked scores a LIST of symbols inside one objective."""

    def test_symbol_list_order_is_irrelevant(self):
        forward = _seed(symbols=("BTC-USDC", "ETH-USDC", "SUI-USDC"))
        reverse = _seed(symbols=("SUI-USDC", "ETH-USDC", "BTC-USDC"))
        assert forward == reverse

    def test_symbol_set_still_matters(self):
        assert _seed(symbols=("BTC-USDC", "ETH-USDC")) != _seed(
            symbols=("BTC-USDC", "SUI-USDC")
        )

    def test_window_series_order_is_irrelevant(self):
        series = WINDOWS + OTHER_WINDOWS
        assert _seed(windows=series) == _seed(windows=list(reversed(series)))

    def test_canonical_windows_is_sorted_and_total(self):
        assert canonical_windows(None) == []
        assert canonical_windows([]) == []
        assert canonical_windows(
            [("2024-03-01", "2024-05-01"), ("2024-01-01", "2024-03-01")]
        ) == ["2024-01-01..2024-03-01", "2024-03-01..2024-05-01"]

    def test_tuples_and_lists_are_equivalent(self):
        assert _seed(windows=[["2024-01-01", "2024-03-01"]]) == _seed(
            windows=[("2024-01-01", "2024-03-01")]
        )


class TestPinnedBaseSeedPropagates:
    """``--seed`` must still be the deliberate-reproduction knob."""

    def test_base_seed_changes_the_seed(self):
        assert _seed(base=0) != _seed(base=1)

    def test_base_seed_moves_every_study_together(self):
        identities = [
            {"regime": "RANGING_CALM"},
            {"regime": "RANGING_VOLATILE"},
            {"strategy": "grid_trading"},
        ]
        at_zero = [_seed(base=0, **i) for i in identities]
        at_one = [_seed(base=1, **i) for i in identities]
        assert all(a != b for a, b in zip(at_zero, at_one))

    def test_runner_stores_the_base_seed(self, tmp_path):
        runner = _bare_runner(tmp_path, seed=7)
        assert runner.seed == 7

    def test_runner_defaults_to_the_documented_base(self, tmp_path):
        assert _bare_runner(tmp_path).seed == optuna_runner_module.DEFAULT_BASE_SEED

    def test_sampler_actually_receives_the_seed(self, tmp_path):
        """Not just derived - it must reach the sampler's draws."""
        runner = _bare_runner(tmp_path)
        same = _draws(runner._build_sampler("tpe", 10, 999))
        again = _draws(runner._build_sampler("tpe", 10, 999))
        other = _draws(runner._build_sampler("tpe", 10, 1000))
        assert same == again
        assert same != other

    def test_random_sampler_is_seeded_too(self, tmp_path):
        runner = _bare_runner(tmp_path)
        same = _draws(runner._build_sampler("random", 10, 999))
        again = _draws(runner._build_sampler("random", 10, 999))
        other = _draws(runner._build_sampler("random", 10, 1000))
        assert same == again
        assert same != other

    def test_unknown_sampler_still_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            _bare_runner(tmp_path)._build_sampler("cmaes", 10, 1)


class TestWalkForwardWindowsSearchIndependently:
    """Defect 2: every WF window used to inherit the same constant."""

    def test_consecutive_walk_forward_windows_get_distinct_seeds(self):
        windows = [
            ("2024-01-01", "2024-04-01"),
            ("2024-04-01", "2024-07-01"),
            ("2024-07-01", "2024-10-01"),
        ]
        seeds = [_seed(windows=[w]) for w in windows]
        assert len(set(seeds)) == len(seeds)

    def test_chunked_folds_get_distinct_seeds(self):
        """Anchored folds train on growing window series."""
        series = [
            ("2024-01-01", "2024-03-01"),
            ("2024-03-01", "2024-05-01"),
            ("2024-05-01", "2024-07-01"),
        ]
        folds = [series[:1], series[:2], series[:3]]
        seeds = [_seed(windows=f) for f in folds]
        assert len(set(seeds)) == len(seeds)


class TestStableAcrossProcesses:
    """Builtin ``hash()`` is salted per process; this must not be."""

    def _subprocess_seed(self, hashseed):
        repo_root = Path(optuna_runner_module.__file__).resolve().parents[2]
        code = (
            "import json;"
            "from trading_bot_v2.optimization.optuna_runner import study_seed;"
            "print(json.dumps(study_seed("
            "0, 'mean_reversion', ['BTC-USDC'], 'RANGING_CALM', "
            "'sharpe_ratio', [('2024-01-01', '2024-03-01')])))"
        )
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(repo_root),
            check=True,
        )
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_identical_under_different_pythonhashseed(self):
        first = self._subprocess_seed("0")
        second = self._subprocess_seed("12345")
        assert first == second == _seed()


class TestSharedDerivationIsNotDuplicated:
    """Both seeding schemes must go through one hashing implementation."""

    def test_composite_and_study_seeds_use_the_shared_helper(self):
        import trading_bot_v2.optimization.run_composite_tuning as composite
        from trading_bot_v2.optimization.seeding import derive_seed

        assert composite.derive_seed is derive_seed
        assert optuna_runner_module.derive_seed is derive_seed

    def test_namespaces_are_distinct(self):
        import trading_bot_v2.optimization.run_composite_tuning as composite

        assert STUDY_SEED_NAMESPACE != composite._FOLD_SEED_NAMESPACE


def _bare_runner(tmp_path, seed=optuna_runner_module.DEFAULT_BASE_SEED):
    """An OptunaRunner pointed at a throwaway study database."""
    return OptunaRunner(db_path=str(tmp_path / "seed_test.db"), seed=seed)


def _draws(sampler, n=2):
    """First ``n`` parameter draws a sampler produces, as a tuple."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(lambda t: t.suggest_float("x", 0.0, 1.0), n_trials=n)
    return tuple(round(t.params["x"], 12) for t in study.trials)
