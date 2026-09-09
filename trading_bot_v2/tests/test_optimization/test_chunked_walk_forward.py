"""
Tests for the chunked rolling-origin walk-forward sweep.

The regression pin of the whole feature is
``TestOverfitDetection::test_overfit_params_score_well_in_sample_and_badly_oos``:
a parameter set that is excellent on the training windows and terrible
on the held-out ones must show up as a large positive in-sample /
out-of-sample gap, with the OUT-OF-SAMPLE number as the headline. If
train/test separation ever regresses, that test fails.

Everything here runs off stub adapters - no candle data is required.
"""

import os
import tempfile

import pytest

from trading_bot_v2.backtesting.performance import BacktestResult
from trading_bot_v2.diagnostics.outcomes import TRADED_SCORE_FLOOR

optuna = pytest.importorskip("optuna")

from trading_bot_v2.backtesting.walk_forward import (  # noqa: E402
    ChunkedWalkForwardReport,
    build_chunk_folds,
    print_chunked_walk_forward_report,
    run_chunked_walk_forward,
)
from trading_bot_v2.optimization.optuna_runner import (  # noqa: E402
    OptunaRunner,
    traded_trial_values,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


WINDOWS = [
    ("2024-01-01", "2024-03-01"),
    ("2024-03-01", "2024-05-01"),
    ("2024-05-01", "2024-07-01"),
]

# The overfit pin uses a TWO-window series, i.e. exactly one fold. That
# is not a shortcut: under rolling origin, window i is fold i-1's TEST
# window and fold i's TRAIN window, so no window-keyed stub can be
# "good in sample and bad out of sample" for more than one fold at a
# time. One fold states the property without ambiguity; the multi-fold
# tests below cover the structural separation instead.
WINDOWS_ONE_FOLD = WINDOWS[:2]


def _result(symbol, start, end, trades, sharpe, ret):
    """Build a BacktestResult with a trade log matching `trades`."""
    pnl_per_trade = ret * 100.0 / trades if trades else 0.0
    trade_log = []
    for i in range(trades):
        # Alternate magnitudes so the return series has variance and the
        # PSR/DSR path is exercised rather than short-circuiting.
        trade_log.append({"pnl": pnl_per_trade * (1.4 if i % 2 else 0.6)})
    return BacktestResult(
        symbol=symbol,
        start=start,
        end=end,
        initial_capital=10000.0,
        final_equity=10000.0 + ret * 100,
        sharpe_ratio=sharpe,
        total_return_pct=ret,
        max_drawdown_pct=5.0,
        closed_trades=trades,
        trade_log=trade_log,
    )


class OverfitAdapter:
    """Adapter where every parameter set is a mirage.

    On the listed windows the objective is strongly positive; on every
    other window the exact same parameters lose money. Any code path
    that grades the winner on data it was fitted to will report a good
    number - which is precisely what these tests forbid.
    """

    def __init__(self, good_windows):
        self.good_windows = set(good_windows)
        self.calls = []

    def run_backtest(self, **kwargs):
        start, end = kwargs["start"], kwargs["end"]
        self.calls.append((kwargs["symbol"], start, end))
        if (start, end) in self.good_windows:
            return _result(kwargs["symbol"], start, end, 20, 1.8, 9.0)
        return _result(kwargs["symbol"], start, end, 20, -1.2, -6.0)

    def calculate_objective(self, result, objective):
        return result.sharpe_ratio


class GenuineEdgeAdapter:
    """Adapter where the edge is real: train and test both pay.

    The Sharpe varies slightly with a sampled parameter so trial values
    have non-zero dispersion - otherwise the DSR variance estimate is
    identically zero and the deflation path is never exercised.
    """

    def run_backtest(self, **kwargs):
        params = kwargs.get("params") or {}
        confidence = float(params.get("min_confidence", 0.5))
        sharpe = 0.9 + 0.4 * confidence
        return _result(
            kwargs["symbol"],
            kwargs["start"],
            kwargs["end"],
            20,
            sharpe,
            5.0,
        )

    def calculate_objective(self, result, objective):
        return result.sharpe_ratio


class SilentOutOfSampleAdapter:
    """Trades in sample, produces nothing out of sample."""

    def __init__(self, good_windows):
        self.good_windows = set(good_windows)

    def run_backtest(self, **kwargs):
        start, end = kwargs["start"], kwargs["end"]
        if (start, end) in self.good_windows:
            return _result(kwargs["symbol"], start, end, 20, 1.5, 8.0)
        return _result(kwargs["symbol"], start, end, 0, 0.0, 0.0)

    def calculate_objective(self, result, objective):
        return result.sharpe_ratio


def _run(
    adapter,
    symbols=("BTC-USDC", "SUI-USDC"),
    n_trials=2,
    windows=None,
    **kwargs,
):
    """Run a chunked walk-forward against a stub adapter."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        runner = OptunaRunner(db_path=db_path)
        runner.adapter = adapter
        report = run_chunked_walk_forward(
            strategy="ma_crossover",
            symbols=list(symbols),
            n_trials=n_trials,
            sampler="random",
            objective="sharpe",
            windows=list(windows if windows is not None else WINDOWS),
            runner=runner,
            **kwargs,
        )
        return report
    finally:
        import gc

        gc.collect()
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def _overfit_run(n_trials=3):
    """One fold: train on the first window, graded on the second."""
    return _run(
        OverfitAdapter([WINDOWS_ONE_FOLD[0]]),
        n_trials=n_trials,
        windows=WINDOWS_ONE_FOLD,
    )


# ---------------------------------------------------------------------------
# Fold construction
# ---------------------------------------------------------------------------


class TestBuildChunkFolds:
    def test_rolling_origin_folds_never_train_on_the_test_window(self):
        folds = build_chunk_folds(WINDOWS, train_windows=1)
        assert len(folds) == 2
        for train, test in folds:
            assert test not in train
            # Training data always ENDS before the test window starts.
            assert max(w[1] for w in train) <= test[0]

    def test_anchored_folds_expand_the_training_set(self):
        folds = build_chunk_folds(WINDOWS, train_windows=1, anchored=True)
        assert [len(train) for train, _ in folds] == [1, 2]

    def test_too_few_windows_yields_no_folds(self):
        assert build_chunk_folds(WINDOWS[:1], train_windows=1) == []

    def test_invalid_train_windows_rejected(self):
        with pytest.raises(ValueError, match="train_windows"):
            build_chunk_folds(WINDOWS, train_windows=0)

    def test_run_refuses_a_series_too_short_for_a_fold(self):
        with pytest.raises(ValueError, match="train/test fold"):
            _run(GenuineEdgeAdapter(), n_trials=1, train_windows=5)


# ---------------------------------------------------------------------------
# The regression pin: overfit in sample, exposed out of sample
# ---------------------------------------------------------------------------


class TestOverfitDetection:
    def test_overfit_params_score_well_in_sample_and_badly_oos(self):
        """Deliberately overfit parameters must be caught by the split.

        The adapter pays 1.8 Sharpe on the training window and -1.2 on
        the held-out one. Whatever the search picks, the in-sample
        number is good, the out-of-sample number is bad, and the gap
        between them is the overfitting signal. This is the regression
        pin for the whole feature: if training and grading ever share a
        window again, the OOS number goes positive and this fails.
        """
        report = _overfit_run(n_trials=3)

        assert len(report.folds) == 1
        assert report.in_sample_objective > 1.0
        assert report.out_of_sample_objective < 0
        assert report.overfit_gap > 2.0
        fold = report.folds[0]
        assert fold.in_sample > 0
        assert fold.out_of_sample < 0
        assert fold.gap > 0
        # The pooled OOS trade series must lose money too.
        assert report.oos_total_return_pct < 0
        assert report.oos_trades > 0

    def test_a_genuine_edge_survives_the_split(self):
        """The control: a real edge must NOT be flagged as overfit."""
        report = _run(GenuineEdgeAdapter(), n_trials=3)
        assert report.out_of_sample_objective > 0
        assert report.oos_total_return_pct > 0
        assert abs(report.overfit_gap) < 0.5

    def test_a_regime_that_stops_working_shows_up_in_the_last_fold(self):
        """Worked historically, fails on recent data - the common case."""
        report = _run(OverfitAdapter(WINDOWS[:2]), n_trials=2, windows=WINDOWS)
        assert len(report.folds) == 2
        # Fold 1 trains and tests inside the period that worked.
        assert report.folds[0].out_of_sample > 0
        # Fold 2 is graded on the period that stopped working.
        assert report.folds[1].out_of_sample < 0
        assert report.folds[1].gap > 2.0

    def test_the_test_window_is_never_backtested_during_training(self):
        """Separation is structural, not a post-hoc filter."""
        adapter = OverfitAdapter(WINDOWS[:2])
        report = _run(adapter, n_trials=2)

        assert adapter.calls  # sanity: backtests happened
        for fold in report.folds:
            assert fold.test_window not in fold.train_windows
            # Training data always ends before the test window opens.
            assert max(w[1] for w in fold.train_windows) <= fold.test_window[0]


# ---------------------------------------------------------------------------
# Diagnostics survive the train/test path
# ---------------------------------------------------------------------------


class TestDiagnosticsThroughTheSplit:
    def _silent_run(self, n_trials=2):
        return _run(
            SilentOutOfSampleAdapter([WINDOWS_ONE_FOLD[0]]),
            n_trials=n_trials,
            windows=WINDOWS_ONE_FOLD,
        )

    def test_zero_trade_oos_is_banded_and_diagnosed(self):
        """A winner that goes silent OOS must say so, not score 0."""
        report = self._silent_run()

        assert report.oos_trades == 0
        for fold in report.folds:
            assert fold.oos_trades == 0
            # Banded, never 0, never -inf.
            assert fold.out_of_sample < TRADED_SCORE_FLOOR
            assert fold.oos_outcome not in ("", "traded")
        # The gap is enormous by construction, and the verdict must say
        # the in-sample number proves nothing.
        assert report.overfit_gap > 50

    def test_in_sample_trials_keep_their_funnel_attrs(self):
        """Banded scoring and funnel attrs still work inside a fold."""
        report = self._silent_run()
        assert report.folds[0].study_name
        assert report.folds[0].in_sample >= TRADED_SCORE_FLOOR


# ---------------------------------------------------------------------------
# Deflation
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """DatabaseManager wired to a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    db_file = str(tmp_path / "chunked_wf.db")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = db_file
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod.DatabaseManager()

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool


class TestDeflationWiring:
    def test_trial_counts_reach_the_registry_and_the_dsr(self, tmp_db):
        """Search cost must be charged against the reported result.

        Every fold records its trial count in trial_registry, the report
        reads that back, and the DSR is deflated for it - the same N
        validation/gate.py will use.
        """
        n_trials = 4
        report = _run(GenuineEdgeAdapter(), n_trials=n_trials)

        rows = tmp_db.get_trial_registry(strategy="ma_crossover")
        assert len(rows) == len(report.folds)
        assert sum(r["n_trials"] for r in rows) == n_trials * len(report.folds)

        # The registry is what the gate reads.
        assert tmp_db.get_total_trials("ma_crossover") == n_trials * len(report.folds)
        assert report.n_trials_total == n_trials * len(report.folds)
        assert report.n_trials_registry == report.n_trials_total
        assert report.n_trials_deflated == report.n_trials_total

        # And the DSR actually used it.
        assert report.dsr is not None
        assert report.dsr.n_trials == report.n_trials_total
        assert report.dsr.value is not None

    def test_more_trials_deflate_harder(self, tmp_db):
        """N is not decoration: a bigger search must cost more."""
        from trading_bot_v2.validation.statistics import deflated_sharpe_ratio

        report = _run(GenuineEdgeAdapter(), n_trials=2)
        returns = report.oos_returns
        var = report.dsr.var_sharpe

        small = deflated_sharpe_ratio(returns, 4, var_sharpe_across_trials=var)
        large = deflated_sharpe_ratio(returns, 400, var_sharpe_across_trials=var)
        assert large.benchmark_sr > small.benchmark_sr
        assert large.value <= small.value

    def test_gate_runs_on_the_out_of_sample_series(self, tmp_db):
        report = _run(GenuineEdgeAdapter(), n_trials=2)
        assert report.gate is not None
        assert report.gate.n_trials == report.n_trials_deflated
        names = {c.name for c in report.gate.checks}
        assert "psr_or_dsr" in names
        assert "cross_symbol_consistency" in names

    def test_banded_scores_are_excluded_from_the_sr_variance(self):
        """Band scores are ranks, not Sharpes - they must not be averaged.

        Mixing them in inflated the variance by orders of magnitude,
        which inflated the DSR benchmark, which pinned the gate at FAIL
        for reasons unrelated to the search.
        """

        class Stub:
            def __init__(self, value):
                self.value = value
                self.user_attrs = {}

        trials = [
            Stub(1.0),
            Stub(2.0),
            Stub(-99.7),
            Stub(-200.4),
            Stub(float("nan")),
            Stub(None),
        ]
        assert traded_trial_values(trials) == [1.0, 2.0]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReporting:
    def test_report_prints_both_sides_and_the_gap(self, capsys):
        report = _overfit_run(n_trials=2)
        print_chunked_walk_forward_report(report)
        out = capsys.readouterr().out

        assert "IN-SAMPLE vs OUT-OF-SAMPLE" in out
        assert "OUT-OF-SAMPLE  (HEADLINE)" in out
        assert "Overfit gap (IS - OOS)" in out
        assert "VERDICT: OVERFIT" in out
        assert "AGGREGATE OUT-OF-SAMPLE" in out
        assert "DSR" in out

    def test_zero_trade_oos_report_names_the_binding_stage(self, capsys):
        report = _run(
            SilentOutOfSampleAdapter([WINDOWS_ONE_FOLD[0]]),
            n_trials=2,
            windows=WINDOWS_ONE_FOLD,
        )
        print_chunked_walk_forward_report(report)
        out = capsys.readouterr().out
        assert "OOS outcome:" in out
        assert "never traded out of sample" in out

    def test_empty_report_prints_without_crashing(self, capsys):
        report = ChunkedWalkForwardReport(
            strategy="ma_crossover",
            symbols=["BTC-USDC"],
            window_spec="3x2mo",
            windows=list(WINDOWS),
        )
        print_chunked_walk_forward_report(report)
        out = capsys.readouterr().out
        assert "nothing to believe yet" in out


# ---------------------------------------------------------------------------
# Regime-conditional walk-forward
# ---------------------------------------------------------------------------


class RegimeMixAdapter:
    """Every window trades in two regimes with opposite sign.

    trending_strong trades win, ranging_calm trades lose, so a
    regime-conditional run must produce a different verdict from the
    pooled one - and must count only the matching subset.
    """

    N_TRENDING = 40
    N_RANGING = 40

    def run_backtest(self, **kwargs):
        trade_log = [
            {"pnl": 12.0 if i % 2 else 8.0, "regime": "trending_strong"}
            for i in range(self.N_TRENDING)
        ]
        trade_log += [
            {"pnl": -12.0 if i % 2 else -8.0, "regime": "ranging_calm"}
            for i in range(self.N_RANGING)
        ]
        result = _result(kwargs["symbol"], kwargs["start"], kwargs["end"], 1, 0.1, 0.0)
        result.trade_log = trade_log
        result.closed_trades = len(trade_log)
        return result

    def calculate_objective(self, result, objective):
        return result.sharpe_ratio

    def get_regime_trades(self, result, regime):
        from trading_bot_v2.backtesting.optimization_adapter import (
            OptimizationAdapter,
        )

        return OptimizationAdapter.get_regime_trades(self, result, regime)

    def calculate_objective_from_trades(self, **kwargs):
        from trading_bot_v2.backtesting.optimization_adapter import (
            OptimizationAdapter,
        )

        return OptimizationAdapter.calculate_objective_from_trades(self, **kwargs)


class TestRegimeConditionalWalkForward:
    """A regime-conditional run stays out-of-sample and counts only the
    matching subset.

    Regime tuning used to be reachable ONLY through the legacy
    single-window ``optimize()``, which fits and scores on the same data.
    Wiring it into the chunked walk-forward is what makes a per-regime
    variant falsifiable.
    """

    def test_only_matching_trades_are_counted(self):
        report = _run(
            RegimeMixAdapter(),
            n_trials=2,
            windows=WINDOWS_ONE_FOLD,
            regime="TRENDING_STRONG",
        )
        assert report.regime == "trending_strong"
        # 2 symbols x 1 test window x 40 trending trades
        assert report.oos_trades == 2 * RegimeMixAdapter.N_TRENDING
        assert report.oos_returns
        assert all(r > 0 for r in report.oos_returns)

    def test_the_losing_regime_grades_negative(self):
        report = _run(
            RegimeMixAdapter(),
            n_trials=2,
            windows=WINDOWS_ONE_FOLD,
            regime="RANGING_CALM",
        )
        assert report.oos_trades == 2 * RegimeMixAdapter.N_RANGING
        assert report.oos_returns
        assert all(r < 0 for r in report.oos_returns)

    def test_report_header_names_the_regime(self, capsys):
        report = _run(
            RegimeMixAdapter(),
            n_trials=2,
            windows=WINDOWS_ONE_FOLD,
            regime="TRENDING_STRONG",
        )
        print_chunked_walk_forward_report(report)
        out = capsys.readouterr().out
        assert "REGIME: trending_strong" in out
        assert "counts ONLY trades entered in trending_strong" in out
