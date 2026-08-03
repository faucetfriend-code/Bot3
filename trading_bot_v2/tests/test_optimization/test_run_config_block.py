"""Composite tuning reports must describe the run that produced them.

On 2026-08-01 out/composite_mr_gateenforce.json and
out/monthly/composite_mean_reversion_2026-07.json disagreed on a
composite-state result. The cause was a 12-month fold step in the first
run against the default 6-month step in the second - a difference
recorded nowhere in the artifacts, in docs/, or anywhere else in the
repo. It had to be inferred from the fold dates and confirmed by
re-running the window (docs/NEUTRAL-STATE-WINDOW-CHECK.md).

These tests pin the ``run_config`` block that fixes that, with the
emphasis on the field that would actually have caught it: the EFFECTIVE
fold step. Asserting only on the raw ``--step-months`` would be
worthless, because it is None in exactly the case that went wrong - the
run that let it default.
"""

import json

import pytest

from trading_bot_v2.optimization import run_composite_tuning as rct

#: Keys that predate the run_config block. monthly_retune reads "folds"
#: and "summary"; the analysis scripts read the rest.
LEGACY_TOP_LEVEL_KEYS = (
    "strategy",
    "symbol",
    "states",
    "trials_per_fold",
    "objective",
    "folds",
    "summary",
)


class _FakeAdapter:
    """Backtest adapter that returns no trades, as fast as possible.

    Every state comes back "insufficient_data", which is fine: these
    tests are about the report's configuration block, not its results,
    and a real backtest would take minutes per fold.
    """

    def run_backtest(self, strategy, params, start, end, **kwargs):
        return {"strategy": strategy, "params": params}

    def get_regime_trades(self, result, state):
        return []

    def calculate_objective_from_trades(self, *args, **kwargs):
        raise AssertionError("no trades, so no objective should be computed")


def _args(**overrides):
    """Parse a minimal valid CLI, applying overrides as flags."""
    argv = ["--strategy", "mean_reversion"]
    for key, value in overrides.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return rct._parse_args(argv), argv


class TestBuildRunConfig:
    def test_effective_step_is_recorded_when_flag_defaults(self):
        """The failure mode: --step-months unset, step still knowable."""
        args, argv = _args(test_months=6)
        assert args.step_months is None

        block = rct.build_run_config(args, ["vol_low:trend"], 6, argv)

        assert block["args"]["step_months"] == 6
        assert block["args"]["step_months_arg"] is None

    def test_explicit_step_is_recorded_and_distinguishable(self):
        args, argv = _args(test_months=6, step_months=12)

        block = rct.build_run_config(args, ["vol_low:trend"], 12, argv)

        assert block["args"]["step_months"] == 12
        assert block["args"]["step_months_arg"] == 12

    def test_two_runs_differing_only_in_step_have_different_blocks(self):
        """The 2026-08-01 diagnosis, as a one-line dict comparison."""
        default_args, default_argv = _args(test_months=6)
        stepped_args, stepped_argv = _args(test_months=6, step_months=12)

        default_block = rct.build_run_config(
            default_args, ["vol_low:trend"], 6, default_argv
        )
        stepped_block = rct.build_run_config(
            stepped_args, ["vol_low:trend"], 12, stepped_argv
        )

        assert default_block["args"] != stepped_block["args"]

    def test_window_and_sampling_args_are_recorded(self):
        args, argv = _args(
            start="2021-01-01",
            end="2024-01-01",
            train_months=18,
            test_months=6,
            seed=7,
            capital=25000.0,
            min_state_trades=15,
            directional_gate="enforce",
        )

        block = rct.build_run_config(args, ["vol_mid:trend"], 6, argv)["args"]

        assert block["start"] == "2021-01-01"
        assert block["end"] == "2024-01-01"
        assert block["train_months"] == 18
        assert block["test_months"] == 6
        assert block["seed"] == 7
        assert block["capital"] == 25000.0
        assert block["min_state_trades"] == 15
        assert block["directional_gate"] == "enforce"

    def test_regime_env_is_captured(self, monkeypatch):
        """Miss REGIME_MODE and the run is a silent no-op - record it."""
        monkeypatch.setenv("REGIME_MODE", "volatility")
        monkeypatch.setenv("REGIME_VOL_STRATEGIES_LOW", "MeanReversion")
        monkeypatch.setenv("REGIME_VOL_WEIGHTS_LOW", "MeanReversion:1.0")
        monkeypatch.setenv("BACKTEST_HISTORY_LOOKBACK", "100")
        monkeypatch.setenv("BACKTEST_FUNDING_MODEL", "historical")
        args, argv = _args()

        env = rct.build_run_config(args, ["vol_low:trend"], 6, argv)["env"]

        assert env["REGIME_MODE"] == "volatility"
        assert env["REGIME_VOL_STRATEGIES_LOW"] == "MeanReversion"
        assert env["REGIME_VOL_WEIGHTS_LOW"] == "MeanReversion:1.0"
        assert env["BACKTEST_HISTORY_LOOKBACK"] == "100"
        assert env["BACKTEST_FUNDING_MODEL"] == "historical"

    def test_unset_env_is_null_not_missing(self, monkeypatch):
        """Absent must be readable as absent, not as "we forgot to look"."""
        monkeypatch.delenv("REGIME_VOL_STRATEGIES_WARMUP", raising=False)
        args, argv = _args()

        env = rct.build_run_config(args, ["vol_low:trend"], 6, argv)["env"]

        assert "REGIME_VOL_STRATEGIES_WARMUP" in env
        assert env["REGIME_VOL_STRATEGIES_WARMUP"] is None

    def test_no_credentials_leak_into_the_artifact(self, monkeypatch):
        """The allow-list exists so out/ never carries a secret."""
        monkeypatch.setenv("AGENT_WALLET_PRIVATE_KEY", "not-a-real-key")
        monkeypatch.setenv("ACCOUNT_PUBLIC_KEY", "not-a-real-key")
        args, argv = _args()

        block = rct.build_run_config(args, ["vol_low:trend"], 6, argv)

        assert "not-a-real-key" not in json.dumps(block)

    def test_resolved_says_what_was_used_when_env_is_unset(
        self, monkeypatch
    ):
        """A null in env is only readable next to the resolved value.

        REGIME_MODE unset does not mean "no regime mode" - it means
        "adx", the default at the call site. Recording only the null
        leaves a future reader guessing at what the default was on the
        day, and defaults move.
        """
        monkeypatch.delenv("REGIME_MODE", raising=False)
        args, argv = _args()

        block = rct.build_run_config(args, ["vol_low:trend"], 6, argv)

        assert block["env"]["REGIME_MODE"] is None
        assert block["resolved"]["regime_mode"] == "adx"

    def test_resolved_tracks_an_explicit_setting(self, monkeypatch):
        monkeypatch.setenv("REGIME_MODE", "volatility")
        monkeypatch.setenv("BACKTEST_FUNDING_MODEL", "historical")
        args, argv = _args()

        resolved = rct.build_run_config(
            args, ["vol_low:trend"], 6, argv
        )["resolved"]

        assert resolved["regime_mode"] == "volatility"
        assert resolved["backtest_funding_model"] == "historical"

    def test_resolved_reports_the_default_not_the_typo(self, monkeypatch):
        """The engine falls back on a bad value; the artifact must agree."""
        monkeypatch.setenv("REGIME_MODE", "voltility")
        monkeypatch.setenv("BACKTEST_FUNDING_MODEL", "nonsense")
        args, argv = _args()

        block = rct.build_run_config(args, ["vol_low:trend"], 6, argv)

        assert block["env"]["REGIME_MODE"] == "voltility"
        assert block["resolved"]["regime_mode"] == "adx"
        assert block["resolved"]["backtest_funding_model"] == "flat"

    def test_resolved_never_drops_a_key(self):
        """Missing key must only ever mean "field did not exist yet"."""
        expected = {
            "regime_mode",
            "backtest_history_lookback",
            "backtest_warmup_candles",
            "backtest_funding_model",
            "backtest_funding_conversion",
            "backtest_funding_hourly_pct",
        }
        assert set(rct.resolved_settings()) == expected

    def test_version_is_recorded(self):
        args, argv = _args()

        block = rct.build_run_config(args, ["vol_low:trend"], 6, argv)

        assert block["version"] == rct.RUN_CONFIG_VERSION


class TestWrittenReport:
    """End-to-end: the block reaches the JSON file on disk."""

    @pytest.fixture
    def report(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rct, "OptimizationAdapter", _FakeAdapter)
        monkeypatch.setenv("REGIME_MODE", "volatility")
        path = tmp_path / "composite.json"
        rc = rct.run([
            "--strategy", "mean_reversion",
            "--symbol", "ETH-USDC",
            "--start", "2024-01-01",
            "--end", "2024-10-01",
            "--train-months", "6",
            "--test-months", "3",
            "--trials", "1",
            "--report", str(path),
        ])
        assert rc == 0
        return json.loads(path.read_text(encoding="utf-8"))

    def test_block_is_present(self, report):
        assert "run_config" in report

    def test_effective_step_survives_the_round_trip(self, report):
        """--step-months was never passed; 3 comes from --test-months."""
        assert report["run_config"]["args"]["step_months"] == 3
        assert report["run_config"]["args"]["step_months_arg"] is None

    def test_env_survives_the_round_trip(self, report):
        assert report["run_config"]["env"]["REGIME_MODE"] == "volatility"

    def test_resolved_survives_the_round_trip(self, report):
        assert report["run_config"]["resolved"]["regime_mode"] == "volatility"

    def test_legacy_keys_are_untouched(self, report):
        """monthly_retune and the analysis scripts read these by name."""
        for key in LEGACY_TOP_LEVEL_KEYS:
            assert key in report, key
        assert report["strategy"] == "mean_reversion"
        assert report["symbol"] == "ETH-USDC"
