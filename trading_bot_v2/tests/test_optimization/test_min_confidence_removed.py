"""min_confidence is not a tunable dimension for mean_reversion.

Removed from the search space and from ADOPTED_PARAMS on 2026-08-02.
The reason is not that a better value was found - it is that the
parameter provably cannot change an outcome for this strategy, so
Optuna was sampling noise on that axis and spending trial budget the
other four dimensions could have used.

MeanReversion assigns ``self.min_confidence`` in ``__init__`` and then
references it only inside debug log f-strings; nothing compares against
it, unlike ma_crossover which does enforce its own. The one
gate that could act on confidence,
``StrategyManager._apply_regime_confidence_gate``, uses the global
MIN_SIGNAL_CONFIDENCE_FLOOR (0.0) plus a per-regime adjustment that is
non-zero only for INDECISIVE and RANGING_VOLATILE - and MeanReversion is
admitted only in RANGING_CALM, so it returns early every time.

Confidence still matters LIVE, where ConfidenceSizer scales position
size by it. That is a continuous effect with no threshold to tune, which
is why the fix was to drop the dimension rather than to start enforcing
it: enforcing would reverse the deliberate "confidence affects SIZE, not
permission" decision recorded in mean_reversion.py.

These tests fail if someone re-adds the dimension without first making
it gate something.
"""

import pytest

from trading_bot_v2.optimization.monthly_retune import ADOPTED_PARAMS
from trading_bot_v2.optimization.search_spaces import (
    PARAMETER_TYPES,
    get_search_space,
)
from trading_bot_v2.strategies.mean_reversion import MeanReversionStrategy

#: The four dimensions that actually steer mean_reversion.
EFFECTIVE_PARAMS = {
    "rsi_oversold",
    "rsi_overbought",
    "bb_std_dev",
    "atr_stop_multiplier",
}


class TestSearchSpace:
    def test_mean_reversion_does_not_offer_min_confidence(self):
        assert "min_confidence" not in get_search_space("mean_reversion")

    def test_range_and_type_dicts_agree(self):
        """A key in one dict and not the other is silently untunable."""
        assert set(PARAMETER_TYPES["mean_reversion"]) == set(
            get_search_space("mean_reversion")
        )

    def test_the_four_effective_dimensions_remain(self):
        assert set(get_search_space("mean_reversion")) == EFFECTIVE_PARAMS

    def test_other_strategies_keep_their_own_min_confidence(self):
        """Only mean_reversion was inert - do not strip the enforcers."""
        for strategy in ("ma_crossover", "momentum_scalping"):
            assert "min_confidence" in PARAMETER_TYPES[strategy]


class TestAdoptedParams:
    def test_adopted_states_carry_only_effective_params(self):
        for state, params in ADOPTED_PARAMS["mean_reversion"].items():
            assert set(params) == EFFECTIVE_PARAMS, state

    def test_frozen_medians_are_unchanged(self):
        """Dropping min_confidence must not disturb the other four."""
        assert ADOPTED_PARAMS["mean_reversion"]["vol_low:trend"] == {
            "rsi_oversold": 32.7504,
            "rsi_overbought": 62.3545,
            "bb_std_dev": 2.7145,
            "atr_stop_multiplier": 2.3388,
        }
        assert ADOPTED_PARAMS["mean_reversion"]["vol_mid:trend"] == {
            "rsi_oversold": 32.7239,
            "rsi_overbought": 63.8175,
            "bb_std_dev": 2.4479,
            "atr_stop_multiplier": 1.8305,
        }


class TestStrategyIgnoresIt:
    @pytest.mark.parametrize("value", [0.05, 0.45, 0.95])
    def test_min_confidence_never_gates_a_signal(self, value):
        """The attribute exists but no code path compares against it.

        A backtest over BTC 2024-01..2024-07 returns byte-identical
        results at 0.05 and 0.95; this is the cheap unit-level proof of
        the same fact.
        """
        strategy = MeanReversionStrategy(min_confidence=value)
        assert strategy.min_confidence == value

        source = MeanReversionStrategy.generate_signals.__doc__ or ""
        assert "min_confidence" not in source

    def test_attribute_is_never_compared_against(self):
        """Guard against someone wiring it in without updating this file.

        Walks the AST rather than grepping text: the attribute may be
        assigned and interpolated into logs freely, but the moment it
        appears in a comparison it has become a gate, and the search
        space and ADOPTED_PARAMS need revisiting.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(MeanReversionStrategy)))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for operand in [node.left, *node.comparators]:
                if (
                    isinstance(operand, ast.Attribute)
                    and operand.attr == "min_confidence"
                ):
                    offenders.append(f"line {node.lineno}")
        assert not offenders, (
            "min_confidence is now compared against at "
            f"{offenders} - it has become a real gate. Re-add it to the "
            "mean_reversion search space and re-tune, and update this test."
        )
