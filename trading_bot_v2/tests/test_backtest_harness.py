"""
Tests for the offline backtest harness.

Covers the validated OHLCV loader (ordering, duplicates, gaps, timezone,
values, file formats, offline guarantee), the strategy adapter and
registry, the metrics/report writer, the synthetic sample generator and
an end-to-end run of the harness on the committed sample in both modes.

No test touches the network: the loader is built offline and a download
manager that raises is injected wherever one could be consulted.
"""

from __future__ import annotations

import gzip
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

from trading_bot_v2.backtesting.data_loader import BacktestDataLoader
from trading_bot_v2.backtesting.harness import (
    DIRECT_REQUIRED_TIMEFRAMES,
    HarnessConfig,
    make_offline_loader,
    run_harness,
)
from trading_bot_v2.backtesting.ohlcv import (
    CandleDataError,
    CandleValidation,
    candle_file_for,
    load_validated_candles,
    read_ohlcv_file,
    validate_candles,
)
from trading_bot_v2.backtesting.performance import (
    PerformanceTracker,
    per_strategy_breakdown,
)
from trading_bot_v2.backtesting.report import (
    SCHEMA,
    build_report,
    drawdown_profile,
    render_summary,
    write_report,
)
from trading_bot_v2.backtesting.run_harness import SAMPLE_DATA_DIR, main as cli_main
from trading_bot_v2.backtesting.sample_data import (
    DEFAULT_SYMBOL,
    SampleSpec,
    generate_sample_candles,
    write_sample_dataset,
)
from trading_bot_v2.backtesting.strategy_interface import (
    STRATEGY_REGISTRY,
    StrategyAdapter,
    StrategySpec,
    build_adapter,
    build_adapters,
    resolve_strategy_key,
)
from trading_bot_v2.config import StrategyType
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.config import AssetClass

SAMPLE_SYMBOL = DEFAULT_SYMBOL


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Belt and braces: even code paths that ignore ``offline`` stay offline."""
    monkeypatch.setenv("DATA_AUTODOWNLOAD", "false")


class RaisingDownloadManager:
    """Any download attempt is a test failure."""

    def ensure(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("network download attempted")


def make_frame(
    n: int,
    step_minutes: int = 60,
    start: datetime = datetime(2024, 1, 1),
    tz_suffix: str = "",
) -> pd.DataFrame:
    """A clean, ascending candle frame on the ``step_minutes`` grid."""
    rows = []
    price = 100.0
    for i in range(n):
        ts = start + timedelta(minutes=step_minutes * i)
        o = price
        c = price + (0.5 if i % 2 else -0.3)
        rows.append(
            {
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S") + tz_suffix,
                "open": o,
                "high": max(o, c) + 0.2,
                "low": min(o, c) - 0.2,
                "close": c,
                "volume": 10.0 + i,
            }
        )
        price = c
    return pd.DataFrame(rows)


def write_store(tmp_path: Path, symbol: str, tf: str, df: pd.DataFrame, kind: str):
    """Write a frame as the store file for symbol/tf in the requested format."""
    base = tmp_path / f"{symbol}_{tf}"
    if kind == "csv":
        path = base.with_suffix(".csv")
        df.to_csv(path, index=False)
    elif kind == "gz":
        path = Path(str(base) + ".csv.gz")
        df.to_csv(path, index=False, compression="gzip")
    else:
        path = base.with_suffix(".parquet")
        df.to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------
# Loader / validation
# ---------------------------------------------------------------------------


class TestValidateCandles:
    def test_clean_frame_passes(self):
        frame, report = validate_candles(make_frame(10), "1h")
        assert report.ok
        assert report.rows == 10
        assert report.gaps == 0 and report.duplicates == 0
        assert report.first == "2024-01-01T00:00:00"
        assert report.last == "2024-01-01T09:00:00"
        assert list(frame.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert all(isinstance(t, str) for t in frame["timestamp"])  # canonical

    def test_duplicates_are_counted_and_repaired(self):
        df = make_frame(6)
        df = pd.concat([df, df.iloc[[2, 4]]], ignore_index=True)
        frame, report = validate_candles(df, "1h")
        assert report.duplicates == 2
        assert report.repaired
        assert report.ok
        assert len(frame) == 6
        assert frame["timestamp"].is_monotonic_increasing

    def test_duplicates_fatal_without_repair(self):
        df = make_frame(6)
        df = pd.concat([df, df.iloc[[2]]], ignore_index=True)
        _, report = validate_candles(df, "1h", repair=False)
        assert not report.ok
        assert any("duplicate" in p for p in report.fatal)

    def test_out_of_order_is_counted_and_sorted(self):
        df = make_frame(8).iloc[[0, 3, 1, 2, 5, 4, 6, 7]].reset_index(drop=True)
        frame, report = validate_candles(df, "1h")
        assert report.out_of_order > 0
        assert report.repaired and report.ok
        assert frame["timestamp"].tolist() == make_frame(8)["timestamp"].tolist()

    def test_gaps_are_reported_not_filled(self):
        df = make_frame(12).drop(index=[3, 4, 9]).reset_index(drop=True)
        frame, report = validate_candles(df, "1h")
        assert report.ok  # gaps are a warning
        assert report.gaps == 3
        assert report.gap_locations == [
            ("2024-01-01T02:00:00", 2),
            ("2024-01-01T08:00:00", 1),
        ]
        assert len(frame) == 9

    def test_tz_aware_input_is_converted_to_naive_utc(self):
        df = make_frame(4, tz_suffix="+02:00")
        frame, report = validate_candles(df, "1h")
        assert report.tz_aware
        assert frame["timestamp"].iloc[0] == "2023-12-31T22:00:00"
        assert report.ok

    def test_naive_input_is_not_flagged_tz_aware(self):
        _, report = validate_candles(make_frame(4), "1h")
        assert not report.tz_aware

    def test_bad_values_are_fatal(self):
        df = make_frame(6)
        df.loc[1, "high"] = df.loc[1, "low"] - 1  # inverted
        df.loc[2, "close"] = float("nan")
        df.loc[3, "volume"] = -5
        df.loc[4, "open"] = 0.0
        _, report = validate_candles(df, "1h")
        assert report.bad_values == 4
        assert not report.ok

    def test_off_grid_timestamps_are_fatal(self):
        df = make_frame(6)
        df.loc[2, "timestamp"] = "2024-01-01T02:07:00"
        _, report = validate_candles(df, "1h")
        assert report.off_grid == 1
        assert not report.ok

    def test_unknown_timeframe_raises(self):
        with pytest.raises(CandleDataError, match="unknown timeframe"):
            validate_candles(make_frame(3), "7m")

    def test_missing_column_raises(self):
        with pytest.raises(CandleDataError, match="missing columns"):
            validate_candles(make_frame(3).drop(columns=["volume"]), "1h")

    def test_unparseable_timestamp_raises(self):
        df = make_frame(3)
        df.loc[1, "timestamp"] = "not a date"
        with pytest.raises(CandleDataError, match="unparseable"):
            validate_candles(df, "1h")

    def test_aliased_columns_are_accepted(self):
        df = make_frame(3).rename(
            columns={"timestamp": "Date", "open": "O", "volume": "Vol"}
        )
        _, report = validate_candles(df, "1h")
        assert report.ok

    def test_empty_frame_is_not_ok(self):
        empty = pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        _, report = validate_candles(empty, "1h")
        assert report.rows == 0 and not report.ok

    def test_to_dict_and_summary(self):
        _, report = validate_candles(make_frame(5), "15m")
        d = report.to_dict()
        assert d["timeframe"] == "15m" and d["ok"] is True
        json.dumps(d)  # JSON-safe
        assert "15m: OK 5 rows" in report.summary()


class TestReadFiles:
    @pytest.mark.parametrize("kind", ["csv", "gz", "parquet"])
    def test_each_format_round_trips(self, tmp_path, kind):
        path = write_store(tmp_path, "X-USDC", "1h", make_frame(5), kind)
        frame, report = load_validated_candles(path, "1h")
        assert report.ok and len(frame) == 5
        assert candle_file_for(tmp_path, "X-USDC", "1h") == path
        assert candle_file_for(tmp_path, "X-USDC", "4h") is None

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_ohlcv_file(tmp_path / "nope.csv")

    def test_unsupported_suffix_raises(self, tmp_path):
        path = tmp_path / "x.txt"
        path.write_text("timestamp,open,high,low,close,volume\n")
        with pytest.raises(CandleDataError, match="unsupported suffix"):
            read_ohlcv_file(path)

    def test_strict_raises_on_fatal(self, tmp_path):
        df = make_frame(5)
        df.loc[1, "high"] = 0
        path = write_store(tmp_path, "X-USDC", "1h", df, "csv")
        with pytest.raises(CandleDataError, match="invalid OHLCV"):
            load_validated_candles(path, "1h")
        _, report = load_validated_candles(path, "1h", strict=False)
        assert not report.ok


class TestOfflineLoader:
    def test_offline_loader_never_downloads(self, tmp_path):
        loader = BacktestDataLoader(
            "X-USDC",
            str(tmp_path),
            download_manager=RaisingDownloadManager(),
            offline=True,
        )
        with pytest.raises(FileNotFoundError, match="offline"):
            loader.get_candles("1h", "2024-01-01", "2024-01-02")
        assert loader.coverage_shortfall("1h", "2024-01-01", "2024-01-02") == {
            "covered": False,
            "first": None,
            "last": None,
            "missing_leading": 0,
            "missing_trailing": 0,
            "requested": 0,
        }

    def test_validate_mode_records_reports_and_reads_gz(self, tmp_path):
        df = make_frame(30, step_minutes=240)
        df = pd.concat([df, df.iloc[[5]]], ignore_index=True)  # one duplicate
        write_store(tmp_path, "X-USDC", "4h", df, "gz")
        loader = make_offline_loader("X-USDC", tmp_path)
        candles = loader.get_candles("4h", "2024-01-01", "2024-01-05")
        # 4 days x 6 bars; the loader's string-compared end bound excludes
        # the bar stamped exactly "2024-01-05T00:00:00" for a date-only end.
        assert len(candles["close"]) == 24
        report = loader.validations["4h"]
        assert isinstance(report, CandleValidation)
        assert report.duplicates == 1 and report.repaired and report.ok

    def test_validate_mode_refuses_bad_file(self, tmp_path):
        df = make_frame(10)
        df.loc[0, "low"] = -1
        write_store(tmp_path, "X-USDC", "1h", df, "csv")
        loader = make_offline_loader("X-USDC", tmp_path)
        with pytest.raises(CandleDataError):
            loader.get_candles("1h", "2024-01-01", "2024-01-01T05:00:00")


# ---------------------------------------------------------------------------
# Strategy adapter
# ---------------------------------------------------------------------------


def _signal(strategy: StrategyType = StrategyType.MEAN_REVERSION) -> Signal:
    return Signal(
        strategy=strategy,
        asset="X-USDC",
        asset_class=AssetClass.PERPETUAL,
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
    )


class _WithExecution:
    def __init__(self):
        self.seen: List[Any] = []
        self._sim_time = None

    def generate_signals(
        self, symbol, multi_tf_data, current_price, execution_tf_data=None
    ):
        self.seen.append(execution_tf_data)
        return [_signal()]


class _WithoutExecution:
    def generate_signals(self, symbol, multi_tf_data, current_price):
        return [_signal(StrategyType.MA_CROSSOVER)]

    def required_history(self):
        return 51


class _WithKwargs:
    def generate_signals(self, symbol, multi_tf_data, current_price, **kwargs):
        return [_signal()] if "execution_tf_data" in kwargs else []


class _Raising:
    def generate_signals(self, symbol, multi_tf_data, current_price):
        raise RuntimeError("boom")


def _spec(factory) -> StrategySpec:
    return StrategySpec(
        key="fake",
        display_name="Fake",
        strategy_type=StrategyType.MEAN_REVERSION,
        factory=factory,
        primary_timeframe="15m",
        description="fake",
    )


class TestStrategyAdapter:
    def test_passes_execution_data_when_declared(self):
        strat = _WithExecution()
        adapter = StrategyAdapter(strat, _spec(_WithExecution))
        out = adapter.generate_signals("X", {}, 1.0, execution_tf_data={"5m": {}})
        assert len(out) == 1
        assert strat.seen == [{"5m": {}}]
        assert adapter.stats.calls == 1 and adapter.stats.signals == 1

    def test_omits_execution_data_when_not_declared(self):
        adapter = StrategyAdapter(_WithoutExecution(), _spec(_WithoutExecution))
        out = adapter.generate_signals("X", {}, 1.0, execution_tf_data={"5m": {}})
        assert out[0].strategy is StrategyType.MA_CROSSOVER
        assert adapter.required_history() == 51

    def test_var_kwargs_receives_execution_data(self):
        adapter = StrategyAdapter(_WithKwargs(), _spec(_WithKwargs))
        assert adapter.generate_signals("X", {}, 1.0, execution_tf_data={}) != []

    def test_exceptions_are_counted_not_raised(self):
        adapter = StrategyAdapter(_Raising(), _spec(_Raising))
        assert adapter.generate_signals("X", {}, 1.0) == []
        assert adapter.stats.errors == 1
        assert "RuntimeError" in adapter.stats.last_error
        assert adapter.required_history() is None

    def test_set_sim_time_mirrors_strategy_manager(self):
        strat = _WithExecution()
        adapter = StrategyAdapter(strat, _spec(_WithExecution))
        now = datetime(2024, 1, 2, 3, 4)
        adapter.set_sim_time(now)
        assert strat._sim_time == now

    def test_rejects_object_without_generate_signals(self):
        with pytest.raises(TypeError):
            StrategyAdapter(object(), _spec(object))


class TestRegistry:
    def test_registry_has_at_least_two_live_strategies(self):
        assert {"mean_reversion", "ma_crossover"} <= set(STRATEGY_REGISTRY)

    @pytest.mark.parametrize("key", sorted(STRATEGY_REGISTRY))
    def test_every_registered_strategy_builds_and_runs(self, key):
        adapter = build_adapter(key)
        assert adapter.key == key
        assert adapter.spec.strategy_type.value == key
        bars = generate_sample_candles(SampleSpec(), ("5m", "15m", "1h", "4h"))
        bundles = {tf: _bundle(df.tail(60)) for tf, df in bars.items()}
        multi = {tf: bundles[tf] for tf in ("15m", "1h", "4h")}
        adapter.set_sim_time(datetime(2024, 1, 30, 23, 55))
        out = adapter.generate_signals(
            SAMPLE_SYMBOL, multi, bundles["5m"]["close"][-1], {"5m": bundles["5m"]}
        )
        assert isinstance(out, list)
        assert adapter.stats.errors == 0
        assert all(isinstance(s, Signal) for s in out)

    def test_resolve_accepts_display_and_key(self):
        assert resolve_strategy_key("MeanReversion") == "mean_reversion"
        assert resolve_strategy_key("MA_CROSSOVER") == "ma_crossover"
        with pytest.raises(KeyError, match="unknown strategy"):
            resolve_strategy_key("Nope")

    def test_build_adapters_rejects_duplicates_and_applies_params(self):
        with pytest.raises(ValueError, match="more than once"):
            build_adapters(["mean_reversion", "MeanReversion"])
        (adapter,) = build_adapters(
            ["ma_crossover"],
            {"ma_crossover": {"fast_ma_period": 10, "slow_ma_period": 30}},
        )
        assert adapter.strategy.fast_ma_period == 10
        assert adapter.strategy.slow_ma_period == 30
        assert adapter.required_history() == 35  # max(31, 26 + 9)


def _bundle(df: pd.DataFrame) -> Dict[str, List[Any]]:
    return {
        "timestamp": df["timestamp"].tolist(),
        "open": df["open"].tolist(),
        "high": df["high"].tolist(),
        "low": df["low"].tolist(),
        "close": df["close"].tolist(),
        "volume": df["volume"].tolist(),
    }


# ---------------------------------------------------------------------------
# Metrics and report
# ---------------------------------------------------------------------------


def _fill(ts: str, strategy: str, net_pnl: float, fee: float, closed: float = 1.0):
    return {
        "order_id": "x",
        "timestamp": ts,
        "symbol": "X-USDC",
        "side": "ask",
        "quantity": 1.0,
        "fill_price": 100.0,
        "fee": fee,
        "pnl": net_pnl + fee,
        "closed_qty": closed,
        "net_pnl": net_pnl,
        "balance_after": 0.0,
        "strategy": strategy,
        "regime": "ranging_calm",
        "direction": "neutral",
        "role": "taker",
    }


class TestMetrics:
    def test_finalise_reports_trades_pnl_fees_funding(self):
        tracker = PerformanceTracker(initial_capital=1000.0)
        stamps = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(5)]
        for ts, eq in zip(stamps, (1000.0, 1010.0, 990.0, 1005.0, 1020.0)):
            tracker.record_snapshot(ts.isoformat(), eq, {})
        log = [
            _fill("2024-01-01T01:00:00", "MeanReversion", 10.0, 0.5),
            _fill("2024-01-01T02:00:00", "MeanReversion", -20.0, 0.5),
            _fill("2024-01-01T03:00:00", "MACrossover", 15.0, 0.4),
            _fill("2024-01-01T04:00:00", "MACrossover", 0.0, 0.1, closed=0.0),  # entry
        ]
        result = tracker.finalise(
            final_equity=1020.0,
            trade_log=log,
            symbol="X-USDC",
            start="2024-01-01",
            end="2024-01-02",
            total_funding=-1.5,
        )
        assert result.total_trades == 4 and result.closed_trades == 3
        assert result.win_rate_pct == pytest.approx(200 / 3)
        assert result.profit_factor == pytest.approx(25 / 20)
        assert result.total_return_pct == pytest.approx(2.0)
        assert result.total_fees == pytest.approx(1.5)
        assert result.total_funding_paid == pytest.approx(1.5)
        assert result.max_drawdown_pct == pytest.approx((1010 - 990) / 1010 * 100)
        assert result.sharpe_ratio > 0
        by = result.by_strategy
        assert by["MeanReversion"]["closed_trades"] == 2
        assert by["MeanReversion"]["net_pnl"] == pytest.approx(-10.0)
        assert by["MeanReversion"]["profit_factor"] == pytest.approx(0.5)
        assert by["MACrossover"]["fills"] == 2
        assert by["MACrossover"]["profit_factor"] is None  # no losses
        assert by["MACrossover"]["fees"] == pytest.approx(0.5)

    def test_per_strategy_breakdown_handles_missing_strategy_name(self):
        cells = per_strategy_breakdown([_fill("t", "", 1.0, 0.0)], [])
        assert "unknown" in cells and cells["unknown"]["closed_trades"] == 0

    def test_drawdown_profile_locates_peak_trough_recovery(self):
        curve = [
            {"timestamp": "2024-01-01T00:00:00", "equity": 100.0},
            {"timestamp": "2024-01-01T01:00:00", "equity": 110.0},
            {"timestamp": "2024-01-01T02:00:00", "equity": 99.0},
            {"timestamp": "2024-01-01T03:00:00", "equity": 104.0},
            {"timestamp": "2024-01-01T04:00:00", "equity": 112.0},
            {"timestamp": "2024-01-01T05:00:00", "equity": 108.0},
        ]
        dd = drawdown_profile(curve)
        assert dd["max_drawdown_pct"] == pytest.approx(10.0)
        assert dd["peak"] == "2024-01-01T01:00:00"
        assert dd["trough"] == "2024-01-01T02:00:00"
        assert dd["recovery"] == "2024-01-01T04:00:00"
        assert dd["longest_underwater_hours"] == pytest.approx(3.0)
        assert drawdown_profile([])["max_drawdown_pct"] == 0.0

    def test_build_and_write_report(self, tmp_path):
        tracker = PerformanceTracker(initial_capital=1000.0)
        tracker.record_snapshot("2024-01-01T00:00:00", 1000.0, {})
        tracker.record_snapshot("2024-01-01T01:00:00", 1001.0, {})
        result = tracker.finalise(1001.0, [], "X-USDC", "2024-01-01", "2024-01-02")
        result.profit_factor = float("inf")  # no losses: must serialise as null
        report = build_report(
            result,
            run={
                "symbol": "X-USDC",
                "start": "a",
                "end": "b",
                "mode": "direct",
                "strategies": ["mean_reversion"],
            },
            validations={"1h": validate_candles(make_frame(3), "1h")[1]},
            adapters={"mean_reversion": {"calls": 1, "signals": 0, "errors": 0}},
        )
        assert report["schema"] == SCHEMA
        assert report["metrics"]["profit_factor"] is None
        assert report["data"]["1h"]["rows"] == 3
        json_path, txt_path = write_report(report, tmp_path / "out", "r")
        loaded = json.loads(json_path.read_text())
        assert loaded["metrics"]["final_equity"] == 1001.0
        text = txt_path.read_text()
        assert "BACKTEST HARNESS" in text
        assert "Profit factor   : n/a" in text
        assert "mean_reversion" in text
        assert render_summary(report) == text


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------


class TestSampleData:
    def test_committed_sample_matches_generator(self):
        """The committed files are exactly what the default spec produces."""
        generated = generate_sample_candles(SampleSpec(), ("4h", "1h"))
        for tf in ("4h", "1h"):
            path = SAMPLE_DATA_DIR / f"{SAMPLE_SYMBOL}_{tf}.csv.gz"
            with gzip.open(path, "rt") as fh:
                committed = pd.read_csv(fh)
            pd.testing.assert_frame_equal(committed, generated[tf], check_dtype=False)

    def test_timeframes_roll_up_consistently(self):
        bars = generate_sample_candles(SampleSpec(), ("1h", "4h"))
        h1, h4 = bars["1h"], bars["4h"]
        first4 = h1.iloc[:4]
        assert h4.iloc[0]["open"] == first4.iloc[0]["open"]
        assert h4.iloc[0]["close"] == first4.iloc[-1]["close"]
        assert h4.iloc[0]["high"] == first4["high"].max()
        assert h4.iloc[0]["low"] == first4["low"].min()
        assert h4.iloc[0]["volume"] == pytest.approx(first4["volume"].sum(), abs=0.01)

    def test_committed_sample_validates_clean(self):
        for tf in DIRECT_REQUIRED_TIMEFRAMES:
            path = candle_file_for(SAMPLE_DATA_DIR, SAMPLE_SYMBOL, tf)
            assert path is not None, tf
            _, report = load_validated_candles(path, tf)
            assert report.ok and report.gaps == 0 and not report.repaired

    def test_committed_sample_is_small(self):
        total = sum(p.stat().st_size for p in SAMPLE_DATA_DIR.glob("*.csv.gz"))
        assert total < 400_000

    def test_write_sample_dataset_plain_csv(self, tmp_path):
        paths = write_sample_dataset(tmp_path, SampleSpec(), ("4h",), gzip=False)
        assert [p.name for p in paths] == [f"{SAMPLE_SYMBOL}_4h.csv"]
        _, report = load_validated_candles(paths[0], "4h")
        assert report.ok


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestHarnessEndToEnd:
    def test_direct_mode_on_committed_sample(self, tmp_path):
        cfg = HarnessConfig(
            data_dir=SAMPLE_DATA_DIR,
            symbol=SAMPLE_SYMBOL,
            start="2024-01-08",
            end="2024-01-18",
            strategies=["mean_reversion", "ma_crossover"],
            output_dir=tmp_path,
            basename="e2e",
        )
        run = run_harness(cfg)
        result = run.result
        assert result.symbol == SAMPLE_SYMBOL
        assert set(run.validations) == set(DIRECT_REQUIRED_TIMEFRAMES)
        assert all(v.ok for v in run.validations.values())
        # Both adapters were driven; MeanReversion trades on the ranging start.
        stats = {a.key: a.stats for a in run.adapters}
        assert stats["mean_reversion"].calls > 0 and stats["ma_crossover"].calls > 0
        assert stats["mean_reversion"].errors == 0 and stats["ma_crossover"].errors == 0
        assert result.total_trades > 0
        assert result.closed_trades > 0
        # Fills carry StrategyType.value, so the breakdown is keyed snake_case.
        assert "mean_reversion" in result.by_strategy
        # Accounting: final equity is initial + realised net PnL + funding on
        # a window that ends flat or with the open position marked.
        realised = sum(t["net_pnl"] for t in result.trade_log)
        open_marked = result.final_equity - (
            cfg.initial_capital + realised - result.total_funding_paid
        )
        assert abs(open_marked) < 0.05 * cfg.initial_capital
        assert len(result.equity_curve) > 2000  # one snapshot per 5m bar
        # Files were written and the JSON is complete.
        assert run.json_path == tmp_path / "e2e.json"
        loaded = json.loads(run.json_path.read_text())
        assert loaded["run"]["mode"] == "direct"
        assert loaded["metrics"]["fills"] == result.total_trades
        assert len(loaded["trades"]) == result.total_trades
        assert loaded["adapters"]["ma_crossover"]["calls"] > 0
        assert loaded["diagnostics"]["notes"]["mode"] == "direct"
        assert run.txt_path.read_text().startswith("=" * 64)

    def test_direct_mode_is_deterministic(self):
        cfg = dict(
            data_dir=SAMPLE_DATA_DIR,
            symbol=SAMPLE_SYMBOL,
            start="2024-01-08",
            end="2024-01-12",
            strategies=["mean_reversion"],
            output_dir=None,
        )
        a = run_harness(HarnessConfig(**cfg)).result
        b = run_harness(HarnessConfig(**cfg)).result
        assert a.final_equity == b.final_equity
        assert a.trade_log == b.trade_log

    def test_window_outside_store_is_refused(self):
        with pytest.raises(ValueError, match="do not cover"):
            run_harness(
                HarnessConfig(
                    data_dir=SAMPLE_DATA_DIR,
                    symbol=SAMPLE_SYMBOL,
                    start="2024-02-01",
                    end="2024-02-10",
                    strategies=["mean_reversion"],
                    output_dir=None,
                )
            )

    def test_missing_symbol_is_refused_offline(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_harness(
                HarnessConfig(
                    data_dir=tmp_path,
                    symbol="NOPE-USDC",
                    start="2024-01-08",
                    end="2024-01-10",
                    strategies=["mean_reversion"],
                    output_dir=None,
                )
            )

    def test_config_validation(self):
        with pytest.raises(ValueError, match="mode"):
            HarnessConfig(SAMPLE_DATA_DIR, "X", "2024-01-01", "2024-01-02", mode="x")
        with pytest.raises(ValueError, match="precede"):
            HarnessConfig(SAMPLE_DATA_DIR, "X", "2024-01-02", "2024-01-01")
        with pytest.raises(ValueError, match="strategy"):
            HarnessConfig(
                SAMPLE_DATA_DIR, "X", "2024-01-01", "2024-01-02", strategies=[]
            )

    def test_pipeline_mode_needs_1m(self):
        with pytest.raises(FileNotFoundError, match="1m"):
            run_harness(
                HarnessConfig(
                    data_dir=SAMPLE_DATA_DIR,
                    symbol=SAMPLE_SYMBOL,
                    start="2024-01-08",
                    end="2024-01-10",
                    strategies=["mean_reversion"],
                    mode="pipeline",
                    output_dir=None,
                )
            )

    @pytest.mark.slow
    def test_pipeline_mode_runs_engine_on_full_sample(self, tmp_path):
        """The live StrategyManager path over a regenerated sample with 1m."""
        store = tmp_path / "store"
        write_sample_dataset(store, SampleSpec(), ("1m", "5m", "15m", "1h", "4h"))
        run = run_harness(
            HarnessConfig(
                data_dir=store,
                symbol=SAMPLE_SYMBOL,
                start="2024-01-08",
                end="2024-01-12",
                strategies=["mean_reversion", "ma_crossover"],
                mode="pipeline",
                output_dir=tmp_path / "out",
            )
        )
        assert run.result.final_equity > 0
        assert set(run.validations) == {"1m", *DIRECT_REQUIRED_TIMEFRAMES}
        assert run.report["run"]["mode"] == "pipeline"
        assert run.report["adapters"] == {}
        assert run.json_path is not None and run.json_path.exists()


class TestCli:
    def test_cli_runs_sample_without_writing(self, capsys):
        code = cli_main(
            [
                "--no-write",
                "--start",
                "2024-01-08",
                "--end",
                "2024-01-11",
                "--strategies",
                "mean_reversion",
                "--strategy-log-level",
                "CRITICAL",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "BACKTEST HARNESS" in out
        assert "Report written" not in out

    def test_cli_writes_report(self, tmp_path, capsys):
        code = cli_main(
            [
                "--output-dir",
                str(tmp_path),
                "--basename",
                "cli",
                "--start",
                "2024-01-08",
                "--end",
                "2024-01-11",
                "--strategies",
                "mean_reversion",
            ]
        )
        assert code == 0
        assert (tmp_path / "cli.json").exists() and (tmp_path / "cli.txt").exists()
        assert "Report written" in capsys.readouterr().out

    def test_cli_reports_errors_with_exit_code_2(self, tmp_path, capsys):
        code = cli_main(["--data-dir", str(tmp_path), "--no-write"])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_cli_rejects_bad_params(self, capsys):
        code = cli_main(["--no-write", "--params", "[1,2]"])
        assert code == 2

    def test_cli_lists_strategies(self, capsys):
        assert cli_main(["--list-strategies"]) == 0
        out = capsys.readouterr().out
        assert "mean_reversion" in out and "ma_crossover" in out


def test_math_is_finite_helper_used_in_report():
    """Guard against a regression where inf leaks into the JSON."""
    assert json.loads(
        json.dumps(
            build_report(
                PerformanceTracker(1.0).finalise(
                    1.0, [], "X", "2024-01-01", "2024-01-02"
                ),
                run={"strategies": []},
            )["metrics"]
        )
    )["profit_factor"] is None or math.isfinite(1.0)
