"""Tests for the monthly re-tune driver's pure logic (no subprocesses).

The tuning subprocess itself is always mocked: a real run is ~55 minutes.
"""

import json
from pathlib import Path

import pytest

from trading_bot_v2.optimization import monthly_retune
from trading_bot_v2.optimization.monthly_retune import (
    ADOPTED_PARAMS,
    RETUNE_STRATEGY,
    _fresh_medians,
    _month_window,
    _render_markdown,
    _retune_env,
    _run_step,
    _shift_months,
    compare_retune,
    step_retune,
)


class TestMonthWindow:
    def test_explicit_month(self):
        assert _month_window("2026-06") == ("2026-06-01", "2026-07-01", "2026-06")

    def test_december_rolls_year(self):
        assert _month_window("2025-12") == ("2025-12-01", "2026-01-01", "2025-12")

    def test_default_is_a_valid_window(self):
        start, end, label = _month_window(None)
        assert start < end
        assert label == start[:7]


class TestShiftMonths:
    def test_back_36(self):
        assert _shift_months("2026-08-01", -36) == "2023-08-01"

    def test_back_across_year(self):
        assert _shift_months("2026-01-01", -2) == "2025-11-01"


class TestCompare:
    def _report(self, tmp_path, params):
        report = {
            "summary": {
                "vol_low:trend": {"folds": 2, "tuned": 1.2, "default": 0.7, "edge": 0.5}
            },
            "folds": [
                {"states": {"vol_low:trend": {"params": params}}},
                {"states": {"vol_low:trend": {"params": params}}},
            ],
        }
        path = tmp_path / "r.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_fresh_medians(self):
        report = {
            "folds": [
                {"states": {"s": {"params": {"a": 1.0}}}},
                {"states": {"s": {"params": {"a": 3.0}}}},
            ]
        }
        assert _fresh_medians(report) == {"s": {"a": 2.0}}

    def test_drift_vs_adopted(self, tmp_path):
        adopted = ADOPTED_PARAMS["mean_reversion"]["vol_low:trend"]
        path = self._report(tmp_path, dict(adopted))
        cmp = compare_retune(path)
        entry = cmp["vol_low:trend"]
        assert entry["adopted_params"] == adopted
        assert all(v == 0.0 for v in entry["median_drift_vs_adopted"].values())

    def test_missing_report(self, tmp_path):
        assert "error" in compare_retune(tmp_path / "nope.json")


class _FakeProc:
    returncode = 0


def _capture_subprocess(monkeypatch):
    """Replace subprocess.run with a recorder; return the capture dict."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(monthly_retune.subprocess, "run", fake_run)
    return seen


class TestRetuneEnv:
    def test_declares_volatility_mode_and_lookback(self):
        env = _retune_env(RETUNE_STRATEGY)
        assert env["REGIME_MODE"] == "volatility"
        assert env["BACKTEST_HISTORY_LOOKBACK"] == "100"

    def test_strategy_admitted_in_every_tercile(self):
        env = _retune_env("mean_reversion")
        for suffix in ("LOW", "MID", "HIGH"):
            assert env[f"REGIME_VOL_STRATEGIES_{suffix}"] == "MeanReversion"
            assert env[f"REGIME_VOL_WEIGHTS_{suffix}"] == "MeanReversion:1.0"

    def test_display_name_is_derived_not_hardcoded(self):
        assert _retune_env("grid_trading")["REGIME_VOL_STRATEGIES_LOW"] == "GridTrading"
        assert (
            _retune_env("MomentumScalping")["REGIME_VOL_WEIGHTS_HIGH"]
            == "MomentumScalping:1.0"
        )

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError):
            _retune_env("no_such_strategy")

    def test_no_dotenv_backed_keys_injected(self):
        # LOG_LEVEL / DIRECTIONAL_GATE live in .env, where
        # load_dotenv(override=True) beats any subprocess env we set;
        # they must stay CLI flags instead.
        env = _retune_env(RETUNE_STRATEGY)
        assert "LOG_LEVEL" not in env
        assert "DIRECTIONAL_GATE" not in env


class TestRunStepEnv:
    def test_no_overrides_inherits_environment(self, tmp_path, monkeypatch):
        seen = _capture_subprocess(monkeypatch)
        record = _run_step(["python", "-c", "pass"], tmp_path / "l.log", 5)
        assert seen["env"] is None
        assert record["ok"] is True
        assert "env_overrides" not in record

    def test_overrides_merge_over_os_environ(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONTHLY_RETUNE_MARKER", "inherited")
        seen = _capture_subprocess(monkeypatch)
        record = _run_step(
            ["python", "-c", "pass"],
            tmp_path / "l.log",
            5,
            env_overrides={"REGIME_MODE": "volatility"},
        )
        assert seen["env"]["MONTHLY_RETUNE_MARKER"] == "inherited"
        assert seen["env"]["REGIME_MODE"] == "volatility"
        assert record["env_overrides"] == {"REGIME_MODE": "volatility"}


class TestStepRetuneEnv:
    def test_subprocess_carries_regime_env(self, tmp_path, monkeypatch):
        seen = _capture_subprocess(monkeypatch)
        result = step_retune(
            "2026-08-01", 36, 25, tmp_path / "report.json", tmp_path, "2026-07"
        )
        env = seen["env"]
        assert env["REGIME_MODE"] == "volatility"
        assert env["BACKTEST_HISTORY_LOOKBACK"] == "100"
        for suffix in ("LOW", "MID", "HIGH"):
            assert env[f"REGIME_VOL_STRATEGIES_{suffix}"] == "MeanReversion"
            assert env[f"REGIME_VOL_WEIGHTS_{suffix}"] == "MeanReversion:1.0"
        # Recorded on the step so the run is reproducible from the report.
        assert result["env_overrides"]["REGIME_MODE"] == "volatility"
        assert result["window"] == "2023-08-01..2026-08-01"

    def test_dotenv_backed_settings_stay_cli_flags(self, tmp_path, monkeypatch):
        # DIRECTIONAL_GATE / LOG_LEVEL are in .env, so the child's
        # load_dotenv(override=True) would clobber anything we put in
        # its environment. They must travel as CLI flags, which
        # run_composite_tuning applies after the config import.
        seen = _capture_subprocess(monkeypatch)
        result = step_retune(
            "2026-08-01", 36, 25, tmp_path / "report.json", tmp_path, "2026-07"
        )
        cmd = seen["cmd"]
        assert cmd[cmd.index("--directional-gate") + 1] == "enforce"
        assert cmd[cmd.index("--log-level") + 1] == "WARNING"
        assert "DIRECTIONAL_GATE" not in result["env_overrides"]
        assert "LOG_LEVEL" not in result["env_overrides"]


class TestInsufficientDataIsAFailure:
    def _write(self, tmp_path, summary, folds):
        path = tmp_path / "r.json"
        path.write_text(
            json.dumps({"summary": summary, "folds": folds}), encoding="utf-8"
        )
        return path

    def test_all_states_zero_folds_is_an_error(self, tmp_path):
        path = self._write(
            tmp_path,
            {s: {"folds": 0} for s in ("vol_low:trend", "vol_mid:neutral")},
            [
                {
                    "states": {
                        "vol_low:trend": {"verdict": "insufficient_data"},
                        "vol_mid:neutral": {"verdict": "insufficient_data"},
                    }
                }
            ],
        )
        cmp = compare_retune(path)
        assert "error" in cmp
        assert "REGIME_MODE=volatility" in cmp["error"]
        assert set(cmp["states"]) == {"vol_low:trend", "vol_mid:neutral"}

    def test_empty_summary_is_an_error(self, tmp_path):
        cmp = compare_retune(self._write(tmp_path, {}, []))
        assert "error" in cmp

    def test_one_measured_state_is_not_an_error(self, tmp_path):
        path = self._write(
            tmp_path,
            {
                "vol_low:trend": {
                    "folds": 2,
                    "tuned": 1.1,
                    "default": 0.7,
                    "edge": 0.4,
                },
                "vol_high:neutral": {"folds": 0},
            },
            [{"states": {"vol_low:trend": {"params": {"a": 1.0}}}}],
        )
        cmp = compare_retune(path)
        assert "error" not in cmp
        assert cmp["vol_low:trend"]["oos"]["folds"] == 2

    def test_driver_marks_step_failed_and_exits_nonzero(self, tmp_path, monkeypatch):
        report = {"summary": {"vol_low:trend": {"folds": 0}}, "folds": []}

        def fake_step_retune(end, tune_months, trials, report_path, log_dir, label):
            report_path.write_text(json.dumps(report), encoding="utf-8")
            return {
                "ok": True,
                "returncode": 0,
                "seconds": 1.0,
                "window": "2023-08-01..2026-08-01",
                "env_overrides": {"REGIME_MODE": "volatility"},
            }

        monkeypatch.setattr(monthly_retune, "step_retune", fake_step_retune)
        out_dir = tmp_path / "out"
        code = monthly_retune.main(
            [
                "--month",
                "2026-07",
                "--skip-refresh",
                "--skip-scorecard",
                "--out-dir",
                str(out_dir),
                "--log-dir",
                str(tmp_path / "logs"),
            ]
        )
        assert code == 1
        payload = json.loads(
            (out_dir / "retune-2026-07.json").read_text(encoding="utf-8")
        )
        assert payload["retune"]["ok"] is False
        assert "error" in payload["retune"]
        assert payload["env_overrides"]["REGIME_MODE"] == "volatility"
        md = (out_dir / "retune-2026-07.md").read_text(encoding="utf-8")
        assert "RE-TUNE PRODUCED NO MEASUREMENT" in md
        assert "REGIME_MODE=volatility" in md


class TestMarkdown:
    def test_renders_scorecard_and_comparison(self):
        payload = {
            "scorecard": [
                {
                    "strategy": "vwap_pullback",
                    "symbol": "BTC-USDC",
                    "closed_trades": 61,
                    "profit_factor": 2.374,
                    "net_pnl": 19.54,
                    "return_pct": 0.2,
                    "win_rate_pct": 60.0,
                    "max_drawdown_pct": 1.0,
                },
                {
                    "strategy": "grid_trading",
                    "symbol": "SUI-USDC",
                    "error": "ValueError: no data",
                },
            ],
            "comparison": {
                "vol_low:trend": {
                    "oos": {"folds": 4, "tuned": 1.1, "default": 0.7, "edge": 0.4},
                    "adopted_params": {"a": 1.0},
                    "adopted_oos": {
                        "score": 0.9,
                        "folds": 4,
                        "tuned_paired": 1.1,
                        "edge_vs_adopted": 0.2,
                    },
                    "beats_default": True,
                    "beats_adopted": True,
                    "verdict": monthly_retune.VERDICT_REPLACE,
                },
            },
        }
        md = _render_markdown("2026-07", payload)
        assert "| vwap_pullback | BTC-USDC | 61 | 2.374 |" in md
        assert "ERROR: ValueError: no data" in md
        assert (
            "| vol_low:trend | 4 | 1.1 | 0.7 | 0.9 | 0.4 | 0.2 | "
            "yes | yes | replace_adopted |"
        ) in md
        assert "Adoption rule" in md

    def test_no_adopted_baseline_is_stated_plainly(self):
        payload = {
            "comparison": {
                "vol_high:neutral": {
                    "oos": {"folds": 3, "tuned": 0.4, "default": 0.1, "edge": 0.3},
                    "adopted_params": None,
                    "adopted_oos": None,
                    "beats_default": True,
                    "beats_adopted": None,
                    "verdict": monthly_retune.VERDICT_ADOPT_FIRST,
                },
            },
        }
        md = _render_markdown("2026-07", payload)
        assert "no adopted baseline" in md
        assert "vol_high:neutral" in md.split("States with NO adopted baseline")[1]

    def test_missing_arm_reads_as_unmeasured_not_as_a_loss(self):
        payload = {
            "comparison": {
                "vol_low:trend": {
                    "oos": {"folds": 4, "tuned": 1.1, "default": 0.7, "edge": 0.4},
                    "adopted_params": {"a": 1.0},
                    "adopted_oos": None,
                    "beats_default": True,
                    "beats_adopted": None,
                    "verdict": monthly_retune.VERDICT_UNMEASURED_VS_ADOPTED,
                },
            },
        }
        md = _render_markdown("2026-07", payload)
        assert "| no adopted baseline |" not in md.split("\n")[0]
        row = [ln for ln in md.split("\n") if ln.startswith("| vol_low:trend")]
        assert row and "not measured" in row[0]
        assert "unmeasured_vs_adopted" in md


class TestRuleLegs:
    """The two legs of the pre-registered adoption rule."""

    def _summary(self, **kw):
        base = {"folds": 4, "tuned": 1.1, "default": 0.7, "edge": 0.4}
        base.update(kw)
        return base

    def test_both_legs_pass_is_replace(self):
        legs = monthly_retune._rule_legs(
            self._summary(edge_vs_baseline=0.2), has_adopted=True
        )
        assert legs == {
            "beats_default": True,
            "beats_adopted": True,
            "verdict": monthly_retune.VERDICT_REPLACE,
        }

    def test_beats_defaults_but_loses_to_adopted_is_no_change(self):
        legs = monthly_retune._rule_legs(
            self._summary(edge_vs_baseline=-0.15), has_adopted=True
        )
        assert legs["beats_default"] is True
        assert legs["beats_adopted"] is False
        assert legs["verdict"] == monthly_retune.VERDICT_NO_CHANGE

    def test_ties_are_not_beats(self):
        legs = monthly_retune._rule_legs(
            self._summary(edge_vs_baseline=0.0), has_adopted=True
        )
        assert legs["beats_adopted"] is False
        assert monthly_retune._rule_legs(self._summary(edge=0.0), has_adopted=False)[
            "verdict"
        ] == (monthly_retune.VERDICT_NO_CHANGE)

    def test_leg1_failure_short_circuits(self):
        legs = monthly_retune._rule_legs(
            self._summary(edge=-0.2, edge_vs_baseline=0.9), has_adopted=True
        )
        assert legs["verdict"] == monthly_retune.VERDICT_NO_CHANGE

    def test_no_adopted_set_makes_leg2_vacuous(self):
        legs = monthly_retune._rule_legs(self._summary(), has_adopted=False)
        assert legs["beats_adopted"] is None
        assert legs["verdict"] == monthly_retune.VERDICT_ADOPT_FIRST

    def test_missing_arm_is_unmeasured_not_a_loss(self):
        legs = monthly_retune._rule_legs(self._summary(), has_adopted=True)
        assert legs["beats_adopted"] is None
        assert legs["verdict"] == (monthly_retune.VERDICT_UNMEASURED_VS_ADOPTED)

    def test_unmeasured_state(self):
        legs = monthly_retune._rule_legs({"folds": 0}, has_adopted=True)
        assert legs["verdict"] == monthly_retune.VERDICT_NOT_MEASURED
        assert legs["beats_default"] is None


class TestCompareThreeWay:
    """compare_retune over a synthetic report that HAS the third arm."""

    def _write(self, tmp_path, summary_extra, state="vol_low:trend"):
        adopted = ADOPTED_PARAMS[RETUNE_STRATEGY].get(state, {"rsi_oversold": 30.0})
        summary = {"folds": 2, "tuned": 1.2, "default": 0.7, "edge": 0.5}
        summary.update(summary_extra)
        report = {
            "summary": {state: summary},
            "folds": [{"states": {state: {"params": dict(adopted)}}}],
        }
        path = tmp_path / "r.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_adopted_arm_surfaces_and_both_legs_pass(self, tmp_path):
        path = self._write(
            tmp_path,
            {
                "baseline": 0.9,
                "baseline_folds": 2,
                "baseline_tuned": 1.2,
                "edge_vs_baseline": 0.3,
            },
        )
        entry = compare_retune(path)["vol_low:trend"]
        assert entry["adopted_oos"] == {
            "score": 0.9,
            "folds": 2,
            "tuned_paired": 1.2,
            "edge_vs_adopted": 0.3,
        }
        assert entry["beats_default"] is True
        assert entry["beats_adopted"] is True
        assert entry["verdict"] == monthly_retune.VERDICT_REPLACE
        # Drift is kept as context, not as a stand-in for a score.
        assert all(v == 0.0 for v in entry["median_drift_vs_adopted"].values())

    def test_tuned_beats_defaults_but_loses_to_adopted(self, tmp_path):
        path = self._write(
            tmp_path,
            {
                "baseline": 1.5,
                "baseline_folds": 2,
                "baseline_tuned": 1.2,
                "edge_vs_baseline": -0.3,
            },
        )
        entry = compare_retune(path)["vol_low:trend"]
        assert entry["beats_default"] is True
        assert entry["beats_adopted"] is False
        assert entry["verdict"] == monthly_retune.VERDICT_NO_CHANGE

    def test_report_without_the_arm_is_unmeasured(self, tmp_path):
        # Exactly the shape of the 2026-07 live run: two arms only.
        entry = compare_retune(self._write(tmp_path, {}))["vol_low:trend"]
        assert entry["adopted_oos"] is None
        assert entry["beats_adopted"] is None
        assert entry["verdict"] == (monthly_retune.VERDICT_UNMEASURED_VS_ADOPTED)

    def test_state_without_adopted_params_is_unaffected(self, tmp_path):
        path = self._write(tmp_path, {}, state="vol_high:neutral")
        entry = compare_retune(path)["vol_high:neutral"]
        assert entry["adopted_params"] is None
        assert entry["verdict"] == monthly_retune.VERDICT_ADOPT_FIRST


class TestStepRetuneBaselineArm:
    def _capture_baseline(self, monkeypatch):
        """Record the baseline JSON handed to the subprocess."""
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            if "--baseline-params" in cmd:
                path = cmd[cmd.index("--baseline-params") + 1]
                seen["path"] = path
                with open(path, "r", encoding="utf-8") as fh:
                    seen["baseline"] = json.load(fh)
            return _FakeProc()

        monkeypatch.setattr(monthly_retune.subprocess, "run", fake_run)
        return seen

    def test_adopted_params_are_passed_to_the_tuner(self, tmp_path, monkeypatch):
        seen = self._capture_baseline(monkeypatch)
        result = step_retune(
            "2026-08-01", 36, 25, tmp_path / "report.json", tmp_path, "2026-07"
        )
        assert seen["baseline"] == ADOPTED_PARAMS[RETUNE_STRATEGY]
        assert result["baseline_params"] == ADOPTED_PARAMS[RETUNE_STRATEGY]
        # Temp file is cleaned up; the params survive inline.
        assert not Path(seen["path"]).exists()

    def test_no_adopted_params_skips_the_flag(self, tmp_path, monkeypatch):
        seen = self._capture_baseline(monkeypatch)
        step_retune(
            "2026-08-01",
            36,
            25,
            tmp_path / "report.json",
            tmp_path,
            "2026-07",
            baseline={},
        )
        assert "--baseline-params" not in seen["cmd"]

    def test_adopted_params_are_not_mutated(self, tmp_path, monkeypatch):
        before = json.dumps(ADOPTED_PARAMS, sort_keys=True)
        self._capture_baseline(monkeypatch)
        step_retune("2026-08-01", 36, 25, tmp_path / "report.json", tmp_path, "2026-07")
        assert json.dumps(ADOPTED_PARAMS, sort_keys=True) == before


class TestPrequentialMedianSurfaced:
    """The monthly report has to SHOW the arm that scores what it ships.

    The adoption rule stays pre-registered on the fold-winner arm; these
    columns are reported context, not a new gate.
    """

    def _write(self, tmp_path, summary):
        path = tmp_path / "composite.json"
        path.write_text(
            json.dumps(
                {
                    "folds": [{"states": {"vol_low:trend": {"params": {"a": 1.0}}}}],
                    "summary": {"vol_low:trend": summary},
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_median_arm_reaches_the_comparison(self, tmp_path):
        path = self._write(
            tmp_path,
            {
                "folds": 4,
                "tuned": 1.1,
                "default": 0.7,
                "edge": 0.4,
                "median_folds": 3,
                "median": 0.8,
                "edge_median_vs_default": 0.1,
                "edge_median_vs_tuned": -0.3,
                "edge_median_vs_baseline": -0.2,
            },
        )
        arm = compare_retune(path)["vol_low:trend"]["prequential_median_oos"]
        assert arm == {
            "score": 0.8,
            "folds": 3,
            "edge_vs_default": 0.1,
            "edge_vs_tuned": -0.3,
            "edge_vs_adopted": -0.2,
        }

    def test_report_without_the_arm_reads_as_not_measured(self, tmp_path):
        path = self._write(
            tmp_path, {"folds": 4, "tuned": 1.1, "default": 0.7, "edge": 0.4}
        )
        entry = compare_retune(path)["vol_low:trend"]
        assert entry["prequential_median_oos"] is None
        md = _render_markdown("2026-07", {"comparison": {"x": entry}})
        assert (
            "not measured" in [ln for ln in md.split("\n") if ln.startswith("| x |")][0]
        )

    def test_markdown_shows_the_median_columns(self):
        payload = {
            "comparison": {
                "vol_low:trend": {
                    "oos": {"folds": 4, "tuned": 1.1, "default": 0.7, "edge": 0.4},
                    "adopted_params": {"a": 1.0},
                    "adopted_oos": None,
                    "prequential_median_oos": {
                        "score": 0.8,
                        "folds": 3,
                        "edge_vs_default": 0.1,
                        "edge_vs_tuned": -0.3,
                        "edge_vs_adopted": None,
                    },
                    "beats_default": True,
                    "beats_adopted": None,
                    "verdict": monthly_retune.VERDICT_UNMEASURED_VS_ADOPTED,
                }
            }
        }
        md = _render_markdown("2026-07", payload)
        row = [ln for ln in md.split("\n") if ln.startswith("| vol_low:trend |")][0]
        assert row.endswith("| 0.8 | 0.1 | -0.3 |")
        assert "median OOS" in md
        assert "docs/MEDIAN-ARM.md" in md
