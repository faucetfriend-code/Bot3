"""
Tests for portfolio-level validation (validation/portfolio.py).

Every test here builds its own equity curves, trade logs and funnel
payloads, so nothing depends on the gitignored candle store.
"""

import math
import os

import pytest

from trading_bot_v2.validation.portfolio import (
    CORRELATED_THRESHOLD,
    ConflictProbe,
    PortfolioValidation,
    RunResult,
    additive_equity,
    align_curves,
    annualised_sharpe,
    average_offdiagonal,
    backtestable_strategies,
    blend_equity,
    build_comparison,
    conflict_stats,
    correlation_matrix,
    crowding_ratios,
    daily_pnl_by_strategy,
    dense_daily_series,
    estimate_campaign_seconds,
    exec_block_stats,
    format_correlation_matrix,
    format_report,
    largest_fill_notional,
    max_drawdown_pct,
    most_correlated_pair,
    occupancy_stats,
    pearson,
    period_returns,
    raw_signals_by_strategy,
    strategy_enables,
    trade_attribution,
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def make_run(
    label,
    equity,
    trades=None,
    diagnostics=None,
    capital=1000.0,
    timestamps=None,
    conflict_events=None,
):
    """Build a RunResult from a hand-written equity curve."""
    stamps = timestamps or [f"2025-01-{i + 1:02d}T00:00:00" for i in range(len(equity))]
    points = list(zip(stamps, [float(e) for e in equity]))
    snapshots = [
        {"timestamp": ts, "equity": eq, "open_positions": 1 if i % 2 else 0}
        for i, (ts, eq) in enumerate(points)
    ]
    returns = period_returns(equity)
    return RunResult(
        label=label,
        start="2025-01-01",
        end="2025-01-31",
        initial_capital=capital,
        final_equity=float(equity[-1]) if equity else capital,
        total_return_pct=(float(equity[-1]) / capital - 1.0) * 100.0 if equity else 0.0,
        max_drawdown_pct=max_drawdown_pct(equity, peak=capital),
        sharpe_ratio=annualised_sharpe(returns),
        profit_factor=1.0,
        closed_trades=len([t for t in (trades or []) if t.get("pnl")]),
        equity_points=points,
        snapshots=snapshots,
        trade_log=list(trades or []),
        diagnostics=dict(diagnostics or {}),
        conflict_events=list(conflict_events or []),
    )


# ---------------------------------------------------------------------------
# Equity analytics
# ---------------------------------------------------------------------------


class TestEquityAnalytics:
    def test_period_returns_basic(self):
        assert period_returns([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])

    def test_period_returns_short_curve(self):
        assert period_returns([100.0]) == []
        assert period_returns([]) == []

    def test_period_returns_survives_zero_equity(self):
        # A wiped account must not raise; the step contributes nothing.
        assert period_returns([100.0, 0.0, 0.0]) == pytest.approx([-1.0, 0.0])

    def test_max_drawdown_from_first_point(self):
        assert max_drawdown_pct([100.0, 120.0, 90.0, 110.0]) == pytest.approx(25.0)

    def test_max_drawdown_seeded_with_capital(self):
        # Seeding the peak with initial capital counts an immediate loss,
        # which is what the engine does.
        assert max_drawdown_pct([90.0, 95.0], peak=100.0) == pytest.approx(10.0)
        assert max_drawdown_pct([90.0, 95.0]) == pytest.approx(0.0)

    def test_max_drawdown_empty(self):
        assert max_drawdown_pct([]) == 0.0

    def test_sharpe_of_flat_curve_is_zero(self):
        assert annualised_sharpe([0.0, 0.0, 0.0]) == 0.0

    def test_sharpe_sign_follows_mean(self):
        assert annualised_sharpe([0.01, 0.02, 0.01, 0.03]) > 0
        assert annualised_sharpe([-0.01, -0.02, -0.01, -0.03]) < 0

    def test_sharpe_too_few_points(self):
        assert annualised_sharpe([0.05]) == 0.0


class TestAlignCurves:
    def test_intersects_timestamps(self):
        curves = {
            "a": [("t1", 100.0), ("t2", 110.0), ("t3", 120.0)],
            "b": [("t2", 100.0), ("t3", 90.0)],
        }
        stamps, aligned = align_curves(curves)
        assert stamps == ["t2", "t3"]
        assert aligned["a"] == [110.0, 120.0]
        assert aligned["b"] == [100.0, 90.0]

    def test_disjoint_curves_yield_nothing(self):
        stamps, aligned = align_curves({"a": [("t1", 1.0)], "b": [("t2", 1.0)]})
        assert stamps == []
        assert aligned == {"a": [], "b": []}

    def test_empty_input(self):
        assert align_curves({}) == ([], {})


class TestBlending:
    def test_equal_weight_blend_averages_returns(self):
        # +20% and -10% blend to +5% on the same capital.
        curves = {"a": [1000.0, 1200.0], "b": [1000.0, 900.0]}
        blend = blend_equity(curves, 1000.0)
        assert blend[0] == pytest.approx(1000.0)
        assert blend[-1] == pytest.approx(1050.0)

    def test_weighted_blend_respects_weights(self):
        curves = {"a": [1000.0, 1200.0], "b": [1000.0, 900.0]}
        blend = blend_equity(curves, 1000.0, weights={"a": 3.0, "b": 1.0})
        # 0.75 * +0.20 + 0.25 * -0.10 = +0.125
        assert blend[-1] == pytest.approx(1125.0)

    def test_blend_ignores_empty_curves(self):
        curves = {"a": [1000.0, 1100.0], "b": []}
        assert blend_equity(curves, 1000.0)[-1] == pytest.approx(1100.0)

    def test_blend_of_nothing_is_empty(self):
        assert blend_equity({}, 1000.0) == []
        assert blend_equity({"a": [1000.0]}, 0.0) == []

    def test_additive_stack_sums_pnl(self):
        curves = {"a": [1000.0, 1200.0], "b": [1000.0, 900.0]}
        stack = additive_equity(curves, 1000.0)
        assert stack[-1] == pytest.approx(1100.0)

    def test_blend_drawdown_is_never_worse_than_the_worst_part(self):
        # The mathematical point of the whole exercise: an equal-weight
        # blend cannot drawdown more than the deepest constituent.
        up = [1000.0, 1100.0, 900.0, 1200.0]
        down = [1000.0, 800.0, 1000.0, 950.0]
        blend = blend_equity({"a": up, "b": down}, 1000.0)
        worst_part = max(
            max_drawdown_pct(up, peak=1000.0), max_drawdown_pct(down, peak=1000.0)
        )
        assert max_drawdown_pct(blend, peak=1000.0) <= worst_part + 1e-9


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


class TestCorrelation:
    def test_identical_series_correlate_perfectly(self):
        series = [0.01, -0.02, 0.03, -0.01]
        assert pearson(series, series) == pytest.approx(1.0)

    def test_mirrored_series_correlate_negatively(self):
        series = [0.01, -0.02, 0.03, -0.01]
        assert pearson(series, [-x for x in series]) == pytest.approx(-1.0)

    def test_constant_series_is_zero_not_nan(self):
        value = pearson([0.0, 0.0, 0.0], [0.01, 0.02, 0.03])
        assert value == 0.0
        assert not math.isnan(value)

    def test_short_overlap_is_zero(self):
        assert pearson([0.1], [0.2]) == 0.0

    def test_matrix_diagonal_and_symmetry(self):
        matrix = correlation_matrix(
            {"a": [0.01, -0.02, 0.03], "b": [-0.01, 0.02, -0.03]}
        )
        assert matrix["a"]["a"] == pytest.approx(1.0)
        assert matrix["a"]["b"] == pytest.approx(matrix["b"]["a"])
        assert matrix["a"]["b"] == pytest.approx(-1.0)

    def test_average_offdiagonal_excludes_diagonal(self):
        matrix = correlation_matrix(
            {"a": [0.01, -0.02, 0.03], "b": [0.01, -0.02, 0.03]}
        )
        assert average_offdiagonal(matrix) == pytest.approx(1.0)

    def test_average_offdiagonal_single_name(self):
        assert average_offdiagonal({"a": {"a": 1.0}}) == 0.0

    def test_most_correlated_pair(self):
        matrix = correlation_matrix(
            {
                "a": [0.01, -0.02, 0.03],
                "b": [0.01, -0.02, 0.03],
                "c": [-0.01, 0.02, -0.03],
            }
        )
        pair = most_correlated_pair(matrix)
        assert pair is not None
        assert set(pair[:2]) == {"a", "b"}
        assert pair[2] == pytest.approx(1.0)

    def test_most_correlated_pair_none_when_single(self):
        assert most_correlated_pair({"a": {"a": 1.0}}) is None


# ---------------------------------------------------------------------------
# Trade-log analytics
# ---------------------------------------------------------------------------


TRADES = [
    {
        "strategy": "grid_trading",
        "pnl": 0.0,
        "fee": 0.1,
        "quantity": 2.0,
        "fill_price": 100.0,
        "timestamp": "2025-01-01T00:00:00",
    },
    {
        "strategy": "grid_trading",
        "pnl": 12.0,
        "fee": 0.1,
        "quantity": 2.0,
        "fill_price": 106.0,
        "timestamp": "2025-01-01T05:00:00",
    },
    {
        "strategy": "mean_reversion",
        "pnl": -4.0,
        "fee": 0.2,
        "quantity": 1.0,
        "fill_price": 500.0,
        "timestamp": "2025-01-02T00:00:00",
    },
    {
        "pnl": 3.0,
        "fee": 0.0,
        "quantity": 1.0,
        "fill_price": 10.0,
        "timestamp": "2025-01-03T00:00:00",
    },
]


class TestTradeAnalytics:
    def test_attribution_counts_fills_and_closes(self):
        attribution = trade_attribution(TRADES)
        assert attribution["grid_trading"]["fills"] == 2
        assert attribution["grid_trading"]["closed_trades"] == 1
        assert attribution["grid_trading"]["pnl"] == pytest.approx(12.0)
        assert attribution["grid_trading"]["wins"] == 1
        assert attribution["mean_reversion"]["wins"] == 0
        assert "unknown" in attribution

    def test_attribution_of_empty_log(self):
        assert trade_attribution([]) == {}

    def test_daily_pnl_groups_by_day_and_strategy(self):
        daily = daily_pnl_by_strategy(TRADES)
        assert daily["grid_trading"] == {"2025-01-01": pytest.approx(12.0)}
        assert daily["mean_reversion"] == {"2025-01-02": pytest.approx(-4.0)}

    def test_daily_pnl_skips_opening_fills(self):
        opening_only = [
            {"strategy": "x", "pnl": 0.0, "timestamp": "2025-01-01T00:00:00"}
        ]
        assert daily_pnl_by_strategy(opening_only) == {}

    def test_dense_daily_series_pads_missing_days(self):
        dense = dense_daily_series(daily_pnl_by_strategy(TRADES))
        lengths = {len(series) for series in dense.values()}
        assert lengths == {3}
        assert dense["grid_trading"] == pytest.approx([12.0, 0.0, 0.0])
        assert dense["mean_reversion"] == pytest.approx([0.0, -4.0, 0.0])

    def test_largest_fill_notional(self):
        assert largest_fill_notional(TRADES) == pytest.approx(500.0)

    def test_largest_fill_notional_ignores_garbage(self):
        assert largest_fill_notional([{"quantity": "n/a", "fill_price": 10.0}]) == 0.0

    def test_occupancy_stats(self):
        snapshots = [
            {"open_positions": 0},
            {"open_positions": 1},
            {"open_positions": 2},
            {"open_positions": 0},
        ]
        stats = occupancy_stats(snapshots)
        assert stats["snapshots"] == 4
        assert stats["occupied_snapshots"] == 2
        assert stats["occupancy_pct"] == pytest.approx(50.0)
        assert stats["max_concurrent_positions"] == 2

    def test_occupancy_stats_empty(self):
        stats = occupancy_stats([])
        assert stats["occupancy_pct"] == 0.0
        assert stats["max_concurrent_positions"] == 0

    def test_crowding_ratios_detect_lost_trades(self):
        crowding = crowding_ratios(
            portfolio_attr={"a": {"closed_trades": 2, "pnl": 1.0}},
            isolated_attr={
                "a": {"closed_trades": 10, "pnl": 5.0},
                "b": {"closed_trades": 4, "pnl": -1.0},
            },
        )
        assert crowding["a"]["retention"] == pytest.approx(0.2)
        # A strategy absent from the portfolio run was fully crowded out.
        assert crowding["b"]["portfolio"] == 0
        assert crowding["b"]["retention"] == 0.0

    def test_crowding_ratio_zero_isolated_trades(self):
        crowding = crowding_ratios({}, {"a": {"closed_trades": 0}})
        assert crowding["a"]["retention"] == 0.0


# ---------------------------------------------------------------------------
# Funnel consumption
# ---------------------------------------------------------------------------


DIAGNOSTICS = {
    "stages": {"raw_signals": 200, "conflict_dropped": 40},
    "reasons": {
        "conflict:lost_resolution": 40,
        "exec:hedge_mode_block": 118,
        "exec:same_direction_skip": 95,
        "validity:rrr_meets_minimum": 7,
    },
    "by_strategy": {
        "mean_reversion": {"raw_signals": 80, "conflict_dropped": 22},
        "vwap_scalping": {"raw_signals": 90, "conflict_dropped": 18},
        "grid_trading": {"raw_signals": 30},
    },
}


class TestFunnelConsumption:
    def test_conflict_stats(self):
        stats = conflict_stats(DIAGNOSTICS)
        assert stats["total_dropped"] == 40
        assert stats["raw_signals"] == 200
        assert stats["drop_share_pct"] == pytest.approx(20.0)
        assert stats["by_strategy"] == {
            "mean_reversion": 22,
            "vwap_scalping": 18,
        }

    def test_conflict_stats_on_empty_payload(self):
        stats = conflict_stats({})
        assert stats["total_dropped"] == 0
        assert stats["drop_share_pct"] == 0.0

    def test_exec_block_stats_filters_and_sorts(self):
        blocks = exec_block_stats(DIAGNOSTICS)
        assert list(blocks) == ["exec:hedge_mode_block", "exec:same_direction_skip"]
        assert blocks["exec:hedge_mode_block"] == 118

    def test_raw_signals_by_strategy_drops_zeroes(self):
        payload = {"by_strategy": {"a": {"raw_signals": 5}, "b": {"raw_signals": 0}}}
        assert raw_signals_by_strategy(payload) == {"a": 5}


# ---------------------------------------------------------------------------
# Conflict probe
# ---------------------------------------------------------------------------


class _FakeStrategy:
    def __init__(self, value):
        self.value = value


class _FakeSignal:
    def __init__(self, strategy):
        self.strategy = _FakeStrategy(strategy)


class _FakeManager:
    """Stand-in for StrategyManager with the one method the probe wraps."""

    def _resolve_signal_conflicts(self, signals, regime):
        # Keep the first candidate, mimicking "highest confidence wins".
        return signals[:1]


class TestConflictProbe:
    def test_records_invocation_and_drops(self):
        probe = ConflictProbe(target=_FakeManager)
        signals = [_FakeSignal("grid_trading"), _FakeSignal("mean_reversion")]
        with probe:
            survivors = _FakeManager()._resolve_signal_conflicts(
                signals, "ranging_calm"
            )
        assert survivors == signals[:1]
        assert len(probe.events) == 1
        event = probe.events[0]
        assert event["candidates"] == ["grid_trading", "mean_reversion"]
        assert event["kept"] == ["grid_trading"]
        assert event["dropped"] == ["mean_reversion"]
        assert event["regime"] == "ranging_calm"

    def test_single_candidate_is_not_an_event(self):
        probe = ConflictProbe(target=_FakeManager)
        with probe:
            _FakeManager()._resolve_signal_conflicts(
                [_FakeSignal("grid_trading")], "ranging_calm"
            )
        assert probe.events == []

    def test_patch_is_reverted_on_exit(self):
        original = _FakeManager._resolve_signal_conflicts
        with ConflictProbe(target=_FakeManager):
            assert _FakeManager._resolve_signal_conflicts is not original
        assert _FakeManager._resolve_signal_conflicts is original

    def test_patch_is_reverted_when_the_run_raises(self):
        original = _FakeManager._resolve_signal_conflicts
        with pytest.raises(RuntimeError):
            with ConflictProbe(target=_FakeManager):
                raise RuntimeError("engine blew up")
        assert _FakeManager._resolve_signal_conflicts is original

    def test_counts_synthesized_signals(self):
        class Combiner:
            def _resolve_signal_conflicts(self, signals, regime):
                return [_FakeSignal("combined")]

        probe = ConflictProbe(target=Combiner)
        with probe:
            Combiner()._resolve_signal_conflicts(
                [_FakeSignal("a"), _FakeSignal("b")], "trending_strong"
            )
        assert probe.events[0]["synthesized"] == 1
        assert probe.events[0]["kept"] == []

    def test_summary_aggregates_events(self):
        probe = ConflictProbe(target=_FakeManager)
        signals = [_FakeSignal("grid_trading"), _FakeSignal("mean_reversion")]
        with probe:
            for _ in range(3):
                _FakeManager()._resolve_signal_conflicts(signals, "ranging_calm")
        summary = probe.summary()
        assert summary["invocations"] == 3
        assert summary["candidates"] == 6
        assert summary["dropped"] == 3
        assert summary["dropped_by_strategy"] == {"mean_reversion": 3}
        assert summary["won_by_strategy"] == {"grid_trading": 3}
        assert summary["by_regime"] == {"ranging_calm": 3}

    def test_summary_of_empty_probe(self):
        summary = ConflictProbe(target=_FakeManager).summary()
        assert summary["invocations"] == 0
        assert summary["dropped_by_strategy"] == {}


# ---------------------------------------------------------------------------
# Strategy set selection
# ---------------------------------------------------------------------------


class TestStrategySelection:
    def test_non_backtestable_are_filtered_out(self):
        keys = ["mean_reversion", "funding_arb", "orderbook_imbalance", "grid_trading"]
        assert backtestable_strategies(keys) == ["mean_reversion", "grid_trading"]

    def test_strategy_enables_sets_and_restores(self):
        os.environ["ENABLE_MEAN_REVERSION"] = "false"
        os.environ.pop("ENABLE_GRID_TRADING", None)
        with strategy_enables(["grid_trading"]):
            assert os.environ["ENABLE_GRID_TRADING"] == "true"
            assert os.environ["ENABLE_MEAN_REVERSION"] == "false"
        assert os.environ["ENABLE_MEAN_REVERSION"] == "false"
        assert "ENABLE_GRID_TRADING" not in os.environ
        os.environ.pop("ENABLE_MEAN_REVERSION", None)

    def test_strategy_enables_restores_after_exception(self):
        os.environ["ENABLE_GRID_TRADING"] = "false"
        with pytest.raises(RuntimeError):
            with strategy_enables(["grid_trading"]):
                raise RuntimeError("boom")
        assert os.environ["ENABLE_GRID_TRADING"] == "false"
        os.environ.pop("ENABLE_GRID_TRADING", None)


# ---------------------------------------------------------------------------
# End-to-end comparison assembly (no candle data)
# ---------------------------------------------------------------------------


def _build_campaign():
    """Build a two-strategy comparison from hand-written runs."""
    winner = make_run("a", [1000.0, 1100.0, 1050.0, 1200.0])
    loser = make_run("b", [1000.0, 950.0, 900.0, 940.0])
    portfolio = make_run(
        "PORTFOLIO",
        [1000.0, 1010.0, 990.0, 1005.0],
        trades=TRADES,
        diagnostics=DIAGNOSTICS,
        conflict_events=[
            {
                "regime": "ranging_calm",
                "candidates": ["a", "b"],
                "kept": ["a"],
                "dropped": ["b"],
                "synthesized": 0,
            }
        ],
    )
    comparison = build_comparison(
        symbol="SUI-USDC",
        start="2025-01-01",
        end="2025-01-31",
        capital=1000.0,
        portfolio=portfolio,
        isolated={"a": winner, "b": loser},
    )
    return PortfolioValidation(
        symbol="SUI-USDC",
        strategies=["a", "b"],
        capital=1000.0,
        windows=[comparison],
    )


class TestBuildComparison:
    def test_blend_and_edges(self):
        campaign = _build_campaign()
        window = campaign.windows[0]
        # +20% and -6% blend to +7%; the portfolio only made +0.5%.
        assert window.blend_return_pct == pytest.approx(7.0)
        assert window.portfolio.total_return_pct == pytest.approx(0.5)
        assert window.return_edge_pct == pytest.approx(-6.5)
        assert window.additive_return_pct == pytest.approx(14.0)

    def test_drawdown_edge_sign(self):
        window = _build_campaign().windows[0]
        # Positive edge means the portfolio drew down LESS than the blend.
        expected = window.blend_max_drawdown_pct - window.portfolio.max_drawdown_pct
        assert window.drawdown_edge_pct == pytest.approx(expected)

    def test_mean_isolated_drawdown_recorded(self):
        window = _build_campaign().windows[0]
        assert window.mean_isolated_drawdown_pct > 0

    def test_conflict_and_exec_carried_through(self):
        window = _build_campaign().windows[0]
        assert window.conflict["total_dropped"] == 40
        assert window.conflict_probe["invocations"] == 1
        assert window.conflict_probe["dropped_by_strategy"] == {"b": 1}
        assert window.exec_blocks["exec:hedge_mode_block"] == 118

    def test_exposure_block(self):
        window = _build_campaign().windows[0]
        assert window.exposure["max_concurrent_positions"] == 1
        assert window.exposure["largest_fill_notional"] == pytest.approx(500.0)
        # The whole point: the cap is never consulted on this path.
        assert window.exposure["risk_cap_consulted"] is False

    def test_flat_strategies_excluded_from_correlation(self):
        flat = make_run("flat", [1000.0, 1000.0, 1000.0, 1000.0])
        moving = make_run("moving", [1000.0, 1100.0, 1050.0, 1200.0])
        comparison = build_comparison(
            "SUI-USDC",
            "2025-01-01",
            "2025-01-31",
            1000.0,
            make_run("PORTFOLIO", [1000.0, 1010.0, 990.0, 1005.0]),
            {"flat": flat, "moving": moving},
        )
        assert "flat" not in comparison.correlation
        assert "moving" in comparison.correlation

    def test_failed_isolated_run_is_skipped_not_fatal(self):
        broken = RunResult(
            label="broken",
            start="2025-01-01",
            end="2025-01-31",
            initial_capital=1000.0,
            error="1m candle data does not cover the window",
        )
        good = make_run("good", [1000.0, 1100.0])
        comparison = build_comparison(
            "SUI-USDC",
            "2025-01-01",
            "2025-01-31",
            1000.0,
            make_run("PORTFOLIO", [1000.0, 1050.0]),
            {"broken": broken, "good": good},
        )
        assert comparison.blend_return_pct == pytest.approx(10.0)
        assert "broken" not in comparison.crowding

    def test_no_isolated_runs_at_all(self):
        comparison = build_comparison(
            "SUI-USDC",
            "2025-01-01",
            "2025-01-31",
            1000.0,
            make_run("PORTFOLIO", [1000.0, 1050.0]),
            {},
        )
        assert comparison.blend_return_pct == 0.0
        assert comparison.correlation == {}


class TestVerdict:
    def test_safer_but_lower_return_is_the_fixture_case(self):
        # The fixture portfolio gives up return (+0.5% vs a +7% blend) but
        # rides a shallower drawdown, so the verdict must not flatten that
        # into a single "worse" answer.
        verdict = _build_campaign().verdict()
        assert "SAFER THAN ITS PARTS" in verdict["headline"]
        assert verdict["return_edge_pct"] < 0
        assert verdict["drawdown_edge_pct"] > 0

    def test_worse_on_both_axes(self):
        campaign = _build_campaign()
        window = campaign.windows[0]
        window.blend_return_pct = 20.0
        window.blend_max_drawdown_pct = 0.5
        verdict = campaign.verdict()
        assert "WORSE THAN ITS PARTS" in verdict["headline"]
        assert verdict["return_edge_pct"] < 0
        assert verdict["drawdown_edge_pct"] < 0

    def test_names_conflict_resolution_as_a_factor(self):
        verdict = _build_campaign().verdict()
        assert any("conflict resolution: fired" in f for f in verdict["factors"])

    def test_reports_when_conflict_never_fired(self):
        campaign = _build_campaign()
        campaign.windows[0].conflict_probe = {"invocations": 0, "dropped": 0}
        verdict = campaign.verdict()
        assert any("never fired" in f for f in verdict["factors"])

    def test_flags_correlated_bets(self):
        same = [1000.0, 1100.0, 1050.0, 1200.0]
        campaign = PortfolioValidation(
            symbol="SUI-USDC",
            strategies=["a", "b"],
            capital=1000.0,
            windows=[
                build_comparison(
                    "SUI-USDC",
                    "2025-01-01",
                    "2025-01-31",
                    1000.0,
                    make_run("PORTFOLIO", [1000.0, 1010.0, 990.0, 1005.0]),
                    {"a": make_run("a", same), "b": make_run("b", list(same))},
                )
            ],
        )
        verdict = campaign.verdict()
        assert verdict["avg_correlation"] >= CORRELATED_THRESHOLD
        assert any("correlated bets" in f for f in verdict["factors"])

    def test_names_crowded_out_strategies(self):
        campaign = _build_campaign()
        campaign.windows[0].crowding = {
            "a": {
                "isolated": 40,
                "portfolio": 2,
                "pnl_isolated": 1.0,
                "pnl_portfolio": 0.0,
            }
        }
        verdict = campaign.verdict()
        assert any("capacity crowding" in f for f in verdict["factors"])

    def test_better_on_both_axes(self):
        campaign = _build_campaign()
        window = campaign.windows[0]
        window.blend_return_pct = -5.0
        window.blend_max_drawdown_pct = 40.0
        assert "BEATS ITS PARTS on both" in campaign.verdict()["headline"]

    def test_safer_but_lower_return(self):
        campaign = _build_campaign()
        window = campaign.windows[0]
        window.blend_return_pct = 20.0
        window.blend_max_drawdown_pct = 40.0
        assert "SAFER THAN ITS PARTS" in campaign.verdict()["headline"]


class TestAggregation:
    def test_mean_across_windows(self):
        campaign = _build_campaign()
        campaign.windows.append(campaign.windows[0])
        assert campaign.mean("blend_return_pct") == pytest.approx(7.0)

    def test_mean_of_empty_campaign(self):
        empty = PortfolioValidation(symbol="X", strategies=[], capital=1000.0)
        assert empty.mean("blend_return_pct") == 0.0

    def test_pooled_correlation_averages_windows(self):
        campaign = _build_campaign()
        pooled = campaign.pooled_correlation()
        assert set(pooled) == {"a", "b"}
        campaign.windows.append(campaign.windows[0])
        assert campaign.pooled_correlation()["a"]["b"] == pytest.approx(
            pooled["a"]["b"]
        )

    def test_to_dict_is_json_safe(self):
        import json

        payload = _build_campaign().to_dict()
        assert json.loads(json.dumps(payload))["symbol"] == "SUI-USDC"
        assert payload["windows"][0]["portfolio"]["label"] == "PORTFOLIO"


class TestReporting:
    def test_report_contains_every_section(self):
        text = format_report(_build_campaign())
        for heading in (
            "portfolio vs sum of parts",
            "isolated vs inside the portfolio",
            "Correlation of per-strategy returns",
            "Conflict resolution",
            "Exposure: does anything bind?",
            "Verdict",
        ):
            assert heading in text

    def test_report_is_ascii(self):
        assert all(ord(c) < 128 for c in format_report(_build_campaign()))

    def test_report_on_empty_campaign(self):
        empty = PortfolioValidation(symbol="X", strategies=[], capital=1000.0)
        assert "Verdict" in format_report(empty)

    def test_correlation_matrix_rendering(self):
        lines = format_correlation_matrix(
            correlation_matrix({"aaa": [0.1, -0.2, 0.3], "bbb": [0.1, -0.2, 0.3]})
        )
        assert len(lines) == 3
        assert "+1.00" in lines[1]

    def test_correlation_matrix_rendering_single_name(self):
        lines = format_correlation_matrix({"only": {"only": 1.0}})
        assert "fewer than two" in lines[0]


class TestRuntimeEstimate:
    def test_scales_with_runs_and_months(self):
        # 6 strategies + 1 portfolio run = 7 runs per window.
        assert estimate_campaign_seconds(6, 3, 2, 3.5) == pytest.approx(147.0)

    def test_zero_windows_costs_nothing(self):
        assert estimate_campaign_seconds(6, 0, 2, 3.5) == 0.0
