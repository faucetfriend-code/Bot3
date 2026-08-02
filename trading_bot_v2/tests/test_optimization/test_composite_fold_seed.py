"""Tests for identity-derived per-fold Optuna seeding.

Until 2026-08-02 the composite tuner seeded each fold with
``--seed + fold_no``, i.e. the fold's ORDINAL POSITION. The same calendar
window therefore drew a different trial sequence in a 5-fold run than in
a 10-fold run, so tuned-arm scores were not comparable between runs -
the exact comparison the monthly re-tune cadence exists to make.

These tests pin the property that replaced it: a fold's seed is a pure
function of the fold's IDENTITY (run seed, strategy, symbol, window), so
position cannot influence it, and the derivation is stable across
processes with different PYTHONHASHSEED values.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import trading_bot_v2.optimization.run_composite_tuning
from trading_bot_v2.optimization.run_composite_tuning import (
    _FOLD_SEED_NAMESPACE,
    fold_seed,
)

WINDOW = ("2021-07-01", "2022-07-01", "2022-07-01", "2023-01-01")
OTHER_WINDOW = ("2021-01-01", "2022-01-01", "2022-01-01", "2022-07-01")


def _seed(base=0, strategy="mean_reversion", symbol="BTC-USDC", window=WINDOW):
    return fold_seed(base, strategy, symbol, *window)


class TestDeterminism:
    """Same inputs -> same seed, always."""

    def test_repeated_calls_agree(self):
        assert _seed() == _seed() == _seed()

    def test_seed_is_a_valid_optuna_seed(self):
        # Non-negative and inside 32 bits, which is what the samplers take.
        for base in (0, 1, 7, 12345, -3):
            value = _seed(base=base)
            assert isinstance(value, int)
            assert 0 <= value <= 0xFFFFFFFF

    def test_derivation_is_the_documented_one(self):
        """A reader must be able to recompute a reported seed by hand."""
        canonical = "|".join(
            [
                _FOLD_SEED_NAMESPACE,
                "0",
                "mean_reversion",
                "BTC-USDC",
                *WINDOW,
            ]
        )
        expected = (
            int.from_bytes(
                hashlib.sha256(canonical.encode("utf-8")).digest()[:4], "big"
            )
            & 0xFFFFFFFF
        )
        assert _seed() == expected


class TestIndependentOfPosition:
    """The defect this replaced: seeds must not depend on fold ordinal."""

    def test_no_fold_number_argument_exists(self):
        """The signature offers nowhere to leak an ordinal in."""
        import inspect

        params = list(inspect.signature(fold_seed).parameters)
        assert params == [
            "base_seed",
            "strategy",
            "symbol",
            "train_start",
            "train_end",
            "test_start",
            "test_end",
        ]

    def test_same_window_at_any_position_in_any_sequence(self):
        """Simulate two runs whose fold sequences overlap but are offset.

        Run A starts six months earlier, so the shared window is its
        fold 2 and run B's fold 1. Under the old ``seed + fold_no``
        scheme those were seeds 2 and 1; now they must be equal.
        """
        run_a = [OTHER_WINDOW, WINDOW]
        run_b = [WINDOW]
        seeds_a = [_seed(window=w) for w in run_a]
        seeds_b = [_seed(window=w) for w in run_b]
        assert seeds_a[1] == seeds_b[0]

    def test_distinct_windows_still_get_distinct_seeds(self):
        assert _seed(window=WINDOW) != _seed(window=OTHER_WINDOW)

    def test_every_window_component_moves_the_seed(self):
        base = _seed()
        shifted = [
            ("2021-07-02", "2022-07-01", "2022-07-01", "2023-01-01"),
            ("2021-07-01", "2022-07-02", "2022-07-01", "2023-01-01"),
            ("2021-07-01", "2022-07-01", "2022-07-02", "2023-01-01"),
            ("2021-07-01", "2022-07-01", "2022-07-01", "2023-01-02"),
        ]
        for window in shifted:
            assert _seed(window=window) != base


class TestGlobalKnobStillWorks:
    """``--seed`` must still move every fold together."""

    def test_base_seed_changes_the_seed(self):
        assert _seed(base=0) != _seed(base=1)

    def test_base_seed_moves_all_folds(self):
        windows = [WINDOW, OTHER_WINDOW]
        at_zero = [_seed(base=0, window=w) for w in windows]
        at_one = [_seed(base=1, window=w) for w in windows]
        assert all(a != b for a, b in zip(at_zero, at_one))


class TestReplicationIndependence:
    """Symbols and strategies are independent replications, not paired arms.

    Optuna's startup trials come from the seed alone, so keying only on
    the window would hand every symbol the identical opening parameter
    vectors and make cross-symbol agreement partly an artifact of the
    shared draw.
    """

    def test_symbol_changes_the_seed(self):
        assert _seed(symbol="BTC-USDC") != _seed(symbol="ETH-USDC")

    def test_strategy_changes_the_seed(self):
        assert _seed(strategy="mean_reversion") != _seed(strategy="grid_trading")


class TestStableAcrossProcesses:
    """Builtin ``hash()`` is salted per process; this must not be."""

    def _subprocess_seed(self, hashseed):
        repo_root = Path(
            trading_bot_v2.optimization.run_composite_tuning.__file__
        ).resolve().parents[2]
        code = (
            "import json;"
            "from trading_bot_v2.optimization.run_composite_tuning "
            "import fold_seed;"
            "print(json.dumps(fold_seed("
            f"0, 'mean_reversion', 'BTC-USDC', {WINDOW[0]!r}, {WINDOW[1]!r}, "
            f"{WINDOW[2]!r}, {WINDOW[3]!r})))"
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
