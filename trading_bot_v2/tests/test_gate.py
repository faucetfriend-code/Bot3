"""
Tests for the P5 strategy validation gate.
"""

import pytest

from trading_bot_v2.validation.gate import (
    GateOutcome,
    GateVerdict,
    evaluate_strategy_gate,
    load_gate_policy,
    print_verdict,
    required_window_months,
    verdict_label,
)

GATE_ENV_VARS = (
    "GATE_MIN_TRADES",
    "GATE_MIN_PF",
    "GATE_MIN_PSR",
    "GATE_REFERENCE_SR",
    "GATE_MIN_TRADES_PER_SYMBOL",
    "GATE_PF_CONFIDENCE",
    "GATE_MAX_WINDOW_MONTHS",
)

# A strongly positive series: 40 wins of +1%, 5 losses of -0.2%
GOOD = [0.01] * 40 + [-0.002] * 5
# A clearly losing series
BAD = [-0.01] * 25 + [0.002] * 10
# A slow (4h-bar) strategy's record: few trades, but a real edge.
# 18 trades is what ma_crossover-like frequency yields over ~a year.
SLOW_STRONG = [0.02] * 11 + [-0.012] * 7
# The same strategy over a 2-month window: 3 trades, nothing decidable.
SLOW_SHORT = [0.02, 0.02, -0.012]
# A fast strategy grinding out thousands of near-break-even trades.
FAST_MEDIOCRE = [0.004] * 1520 + [-0.0038] * 1480


@pytest.fixture(autouse=True)
def clean_gate_env(monkeypatch):
    """Judge every test against the shipped defaults, not the local .env."""
    for var in GATE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _check(verdict: GateVerdict, name: str):
    matches = [c for c in verdict.checks if c.name == name]
    assert len(matches) == 1, f"check {name} missing"
    return matches[0]


class TestGatePolicy:
    def test_defaults(self):
        policy = load_gate_policy()
        # 33 is DERIVED: the observations the PSR needs to resolve a
        # Sharpe-per-trade of 0.30 at 95% confidence.
        assert policy["min_closed_trades"] == 33
        assert policy["reference_sr"] == 0.30
        assert policy["min_trades_per_symbol"] == 5
        assert policy["min_profit_factor"] == 1.3
        assert policy["min_psr"] == 0.95
        assert policy["min_consistent_symbols"] == 2
        assert policy["pf_confidence"] is False

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("GATE_MIN_PF", "2.0")
        monkeypatch.setenv("GATE_MIN_TRADES_PER_SYMBOL", "12")
        policy = load_gate_policy()
        assert policy["min_profit_factor"] == 2.0
        assert policy["min_trades_per_symbol"] == 12

    def test_sample_requirement_tracks_confidence(self, monkeypatch):
        """Demanding more confidence must demand more evidence."""
        base = load_gate_policy()["min_closed_trades"]
        monkeypatch.setenv("GATE_MIN_PSR", "0.99")
        assert load_gate_policy()["min_closed_trades"] > base

    def test_sample_requirement_tracks_reference_edge(self, monkeypatch):
        """A bigger edge is provable on a smaller sample."""
        base = load_gate_policy()["min_closed_trades"]
        monkeypatch.setenv("GATE_REFERENCE_SR", "0.50")
        assert load_gate_policy()["min_closed_trades"] < base

    def test_legacy_min_trades_pins_pooled_requirement(self, monkeypatch):
        monkeypatch.setenv("GATE_MIN_TRADES", "50")
        assert load_gate_policy()["min_closed_trades"] == 50


class TestGateChecks:
    def test_all_pass(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
        )
        assert verdict.passed is True
        assert verdict.outcome is GateOutcome.PASS
        for check in verdict.checks:
            assert check.passed, f"{check.name} unexpectedly failed"
            assert check.advisory is False

    def test_profit_factor_fail(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(BAD), "BTC-USDC": list(BAD)},
        )
        assert _check(verdict, "profit_factor").passed is False
        assert verdict.passed is False
        assert verdict.outcome is GateOutcome.FAIL

    def test_psr_pass_without_registry(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
            n_trials=None,
        )
        check = _check(verdict, "psr_or_dsr")
        assert check.passed is True
        assert "unknown" in check.detail

    def test_dsr_leg_used_when_n_known(self):
        # Massive N with high variance deflates the benchmark far above
        # the observed SR, so the DSR leg fails; PSR alone still passes.
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
            n_trials=100000,
            sr_variance=25.0,
        )
        check = _check(verdict, "psr_or_dsr")
        assert check.passed is True  # PSR leg saves it
        assert "DSR" in check.value

    def test_psr_or_dsr_fail_on_weak_series(self):
        weak = [0.001, -0.001] * 20  # mean ~ 0
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(weak), "BTC-USDC": list(weak)},
        )
        assert _check(verdict, "psr_or_dsr").passed is False
        assert verdict.passed is False

    def test_cross_symbol_fail_on_one_negative(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(BAD)},
        )
        check = _check(verdict, "cross_symbol_consistency")
        assert check.passed is False
        assert "SUI-USDC" in check.detail
        assert verdict.outcome is GateOutcome.FAIL

    def test_single_symbol_cannot_pass_consistency(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD)},
        )
        check = _check(verdict, "cross_symbol_consistency")
        assert check.passed is False
        assert "only 1 symbol" in check.detail
        # Generalization is untestable with one symbol - that is missing
        # evidence, not a demonstrated failure.
        assert verdict.outcome is GateOutcome.INSUFFICIENT_DATA

    def test_threshold_overrides(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={
                "SUI-USDC": list(GOOD)[:20],
                "BTC-USDC": list(GOOD)[:20],
            },
            min_closed_trades=10,
        )
        assert _check(verdict, "sample_adequacy").passed is True

    def test_print_verdict_smoke(self, capsys):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
        )
        print_verdict(verdict)
        out = capsys.readouterr().out
        assert "VALIDATION GATE" in out
        assert "OVERALL: PASS" in out
        for name in (
            "sample_adequacy",
            "profit_factor",
            "psr_or_dsr",
            "cross_symbol_consistency",
        ):
            assert name in out


class TestSlowStrategyIsJudgedFairly:
    """A 4h strategy must be able to pass, and must never be FAILed for
    the crime of trading on 4h bars."""

    def test_short_window_is_insufficient_not_fail(self):
        verdict = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": list(SLOW_SHORT),
                "ETH-USDC": list(SLOW_SHORT) + [0.02],
                "SUI-USDC": list(SLOW_SHORT),
            },
            window_months=2,
            n_windows=3,
        )
        assert verdict.outcome is GateOutcome.INSUFFICIENT_DATA
        assert verdict.outcome is not GateOutcome.FAIL
        assert verdict.passed is False
        assert verdict_label(verdict) == "INSUFFICIENT_DATA"
        assert verdict.n_pooled == 10
        assert verdict.data_multiple_needed and verdict.data_multiple_needed > 1

    def test_short_window_recommends_a_longer_one(self):
        verdict = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": list(SLOW_SHORT),
                "ETH-USDC": list(SLOW_SHORT) + [0.02],
                "SUI-USDC": list(SLOW_SHORT),
            },
            window_months=2,
            n_windows=3,
        )
        # The remedy must be more data, not a lower bar.
        assert "calendar months" in verdict.outcome_reason
        assert "lengthen the window" in verdict.outcome_reason

    def test_quality_checks_are_advisory_on_a_thin_sample(self):
        verdict = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": list(SLOW_SHORT),
                "ETH-USDC": list(SLOW_SHORT),
            },
        )
        assert _check(verdict, "sample_adequacy").advisory is False
        for name in ("profit_factor", "psr_or_dsr", "cross_symbol_consistency"):
            assert _check(verdict, name).advisory is True

    def test_advisory_checks_can_never_promote(self):
        """Even if every quality check happens to pass, a thin sample
        must not produce a PASS."""
        verdict = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": [0.05, 0.06] * 3,
                "ETH-USDC": [0.05, 0.06] * 3,
            },
        )
        quality = [c for c in verdict.checks if c.name != "sample_adequacy"]
        assert all(c.passed for c in quality)
        assert verdict.passed is False
        assert verdict.outcome is GateOutcome.INSUFFICIENT_DATA

    def test_slow_strategy_passes_on_a_long_enough_window(self):
        """The whole point: a genuinely good slow strategy CAN pass -
        it just needs the window that its bar frequency implies."""
        verdict = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": list(SLOW_STRONG),
                "ETH-USDC": list(SLOW_STRONG),
                "SUI-USDC": list(SLOW_STRONG),
            },
        )
        assert verdict.outcome is GateOutcome.PASS
        assert verdict.passed is True
        assert verdict.n_pooled == 54

    def test_thin_symbol_no_longer_blocks_a_pooled_pass(self):
        """The old per-symbol count check failed the whole strategy on
        its least active symbol (liquidation_capture: BTC 10 vs 30)."""
        verdict = evaluate_strategy_gate(
            strategy="liquidation_capture",
            symbol_returns={
                "SUI-USDC": list(GOOD),
                "BTC-USDC": list(GOOD)[:10],
            },
        )
        assert _check(verdict, "sample_adequacy").passed is True
        assert verdict.outcome is GateOutcome.PASS

    def test_symbol_under_the_floor_cannot_vote_on_consistency(self):
        """A symbol with 2 trades has no measurable expectancy."""
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={
                "SUI-USDC": list(GOOD),
                "BTC-USDC": list(GOOD),
                "ETH-USDC": [0.01, 0.01],
            },
        )
        check = _check(verdict, "cross_symbol_consistency")
        assert check.value == "2"
        assert "ETH-USDC" in check.detail


class TestFastStrategyCannotBuyAPass:
    def test_volume_alone_does_not_pass(self):
        """Thousands of near-break-even trades make the PSR confident
        the edge is positive - but it is still not an edge worth
        deploying, and the profit-factor floor is what says so."""
        verdict = evaluate_strategy_gate(
            strategy="grid_trading",
            symbol_returns={
                "BTC-USDC": list(FAST_MEDIOCRE),
                "ETH-USDC": list(FAST_MEDIOCRE),
            },
        )
        assert _check(verdict, "sample_adequacy").passed is True
        assert _check(verdict, "psr_or_dsr").passed is True
        assert _check(verdict, "profit_factor").passed is False
        assert verdict.outcome is GateOutcome.FAIL
        assert verdict.passed is False


class TestOutcomeTaxonomy:
    def test_no_trades_is_its_own_outcome(self):
        verdict = evaluate_strategy_gate(
            strategy="momentum_scalping",
            symbol_returns={"BTC-USDC": [], "ETH-USDC": []},
        )
        assert verdict.outcome is GateOutcome.NO_TRADES
        assert verdict_label(verdict) == "NO_TRADES"
        assert verdict.passed is False
        assert "never traded" in verdict.outcome_reason

    def test_empty_symbol_map_is_no_trades(self):
        verdict = evaluate_strategy_gate(strategy="funding_arb", symbol_returns={})
        assert verdict.outcome is GateOutcome.NO_TRADES
        assert verdict.passed is False

    def test_insufficient_is_distinguishable_from_fail(self):
        thin = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": list(SLOW_SHORT),
                "ETH-USDC": list(SLOW_SHORT),
            },
        )
        rejected = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(BAD), "BTC-USDC": list(BAD)},
        )
        assert thin.passed is rejected.passed is False
        assert thin.outcome is not rejected.outcome
        assert verdict_label(thin) != verdict_label(rejected)

    def test_outcome_survives_check_serialization(self):
        """validation_runs.checks_json only carries name/passed/value/
        threshold/detail, and the runner maps passed -> PASS/FAIL. The
        outcome must still be recoverable from the stored checks."""
        verdict = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": list(SLOW_SHORT),
                "ETH-USDC": list(SLOW_SHORT),
            },
        )
        serialized = [
            {
                "name": c.name,
                "passed": c.passed,
                "value": c.value,
                "threshold": c.threshold,
                "detail": c.detail,
            }
            for c in verdict.checks
        ]
        adequacy = next(c for c in serialized if c["name"] == "sample_adequacy")
        assert "outcome=INSUFFICIENT_DATA" in adequacy["detail"]
        assert "lengthen the window" in adequacy["detail"]
        advisory = next(c for c in serialized if c["name"] == "profit_factor")
        assert advisory["detail"].startswith("[advisory]")

    def test_pass_verdict_labels_its_checks_cleanly(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
        )
        adequacy = _check(verdict, "sample_adequacy")
        assert adequacy.detail.startswith("outcome=PASS")
        assert "[advisory]" not in _check(verdict, "profit_factor").detail

    def test_print_verdict_shows_insufficient(self, capsys):
        verdict = evaluate_strategy_gate(
            strategy="ma_crossover",
            symbol_returns={
                "BTC-USDC": list(SLOW_SHORT),
                "ETH-USDC": list(SLOW_SHORT),
            },
        )
        print_verdict(verdict)
        out = capsys.readouterr().out
        assert "OVERALL: INSUFFICIENT_DATA" in out
        assert "advisory" in out
        assert "REASON:" in out


class TestProfitFactorConfidenceMode:
    def test_disabled_by_default_uses_point_estimate(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
        )
        check = _check(verdict, "profit_factor")
        assert "lower bound" not in check.value

    def test_enabled_mode_reports_a_bound(self, monkeypatch):
        monkeypatch.setenv("GATE_PF_CONFIDENCE", "1")
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
        )
        check = _check(verdict, "profit_factor")
        assert "lower bound" in check.value
        assert check.passed is True

    def test_enabled_mode_rejects_a_marginal_edge(self, monkeypatch):
        """PF 1.5 on a sample where the ratio is not yet pinned down
        clears the point-estimate floor but not the bound."""
        marginal = [0.03, 0.02, 0.01, -0.04] * 40
        monkeypatch.setenv("GATE_PF_CONFIDENCE", "1")
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={
                "SUI-USDC": list(marginal),
                "BTC-USDC": list(marginal),
            },
        )
        assert _check(verdict, "profit_factor").passed is False


class TestRequiredWindowMonths:
    def test_extrapolates_from_the_observed_rate(self):
        # 10 trades over 6 calendar months, 33 needed -> ~20 months.
        assert required_window_months(10, 6.0, required_trades=33) == 20

    def test_already_adequate_returns_the_window_it_has(self):
        assert required_window_months(400, 6.0, required_trades=33) == 6

    def test_capped(self):
        assert required_window_months(1, 6.0, required_trades=33, max_months=12) == 12

    def test_no_trades_gives_no_recommendation(self):
        assert required_window_months(0, 6.0) is None

    def test_unknown_window_gives_no_recommendation(self):
        assert required_window_months(10, 0) is None
