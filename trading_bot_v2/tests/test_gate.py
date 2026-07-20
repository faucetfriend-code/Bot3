"""
Tests for the P5 strategy validation gate.
"""

import pytest

from trading_bot_v2.validation.gate import (
    GateVerdict,
    evaluate_strategy_gate,
    load_gate_policy,
    print_verdict,
)

# A strongly positive series: 40 wins of +1%, 5 losses of -0.2%
GOOD = [0.01] * 40 + [-0.002] * 5
# A clearly losing series
BAD = [-0.01] * 25 + [0.002] * 10


def _check(verdict: GateVerdict, name: str):
    matches = [c for c in verdict.checks if c.name == name]
    assert len(matches) == 1, f"check {name} missing"
    return matches[0]


class TestGatePolicy:
    def test_defaults(self, monkeypatch):
        for var in ("GATE_MIN_TRADES", "GATE_MIN_PF", "GATE_MIN_PSR"):
            monkeypatch.delenv(var, raising=False)
        policy = load_gate_policy()
        assert policy["min_closed_trades"] == 30
        assert policy["min_profit_factor"] == 1.3
        assert policy["min_psr"] == 0.95
        assert policy["min_consistent_symbols"] == 2

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("GATE_MIN_TRADES", "50")
        monkeypatch.setenv("GATE_MIN_PF", "2.0")
        monkeypatch.setenv("GATE_MIN_PSR", "0.99")
        policy = load_gate_policy()
        assert policy["min_closed_trades"] == 50
        assert policy["min_profit_factor"] == 2.0
        assert policy["min_psr"] == 0.99


class TestGateChecks:
    def test_all_pass(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD), "BTC-USDC": list(GOOD)},
        )
        assert verdict.passed is True
        for check in verdict.checks:
            assert check.passed, f"{check.name} unexpectedly failed"

    def test_min_trades_fail(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={
                "SUI-USDC": list(GOOD),
                "BTC-USDC": list(GOOD)[:10],  # only 10 closed trades
            },
        )
        check = _check(verdict, "min_closed_trades")
        assert check.passed is False
        assert verdict.passed is False
        assert "BTC-USDC: 10" in check.detail

    def test_profit_factor_fail(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(BAD), "BTC-USDC": list(BAD)},
        )
        assert _check(verdict, "profit_factor").passed is False
        assert verdict.passed is False

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

    def test_single_symbol_cannot_pass_consistency(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={"SUI-USDC": list(GOOD)},
        )
        check = _check(verdict, "cross_symbol_consistency")
        assert check.passed is False
        assert "only 1 symbol" in check.detail

    def test_threshold_overrides(self):
        verdict = evaluate_strategy_gate(
            strategy="mean_reversion",
            symbol_returns={
                "SUI-USDC": list(GOOD)[:20],
                "BTC-USDC": list(GOOD)[:20],
            },
            min_closed_trades=10,
        )
        assert _check(verdict, "min_closed_trades").passed is True

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
            "min_closed_trades",
            "profit_factor",
            "psr_or_dsr",
            "cross_symbol_consistency",
        ):
            assert name in out
