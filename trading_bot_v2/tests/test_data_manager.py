"""Tests for the historical candle download manager and loader hookup.

Covers gap detection, merge/dedup idempotency, timestamp
normalization, 5m->1h/4h resampling correctness, gap-driven ensure()
downloads, the loader auto-download trigger and Binance paging logic.
All network access is mocked.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_bot_v2.backtesting.data_loader import BacktestDataLoader
from trading_bot_v2.data_manager import (
    BinanceSource,
    BitstampSource,
    CandleDownloadManager,
    CandleSource,
    Coverage,
    merge_candles,
    normalize_candles,
)


def make_candles(start: str, count: int, tf_minutes: int, tz_suffix: str = ""):
    """Build a synthetic canonical-ish candle frame.

    Args:
        start: ISO start timestamp (naive).
        count: Number of candles.
        tf_minutes: Candle spacing in minutes.
        tz_suffix: Optional suffix (e.g. "+00:00") appended to each
            timestamp string to simulate tz-aware legacy CSVs.

    Returns:
        DataFrame with timestamp/open/high/low/close/volume.
    """
    start_dt = datetime.fromisoformat(start)
    rows = []
    for i in range(count):
        ts = start_dt + timedelta(minutes=tf_minutes * i)
        price = 100.0 + i
        sep = " " if tz_suffix else "T"
        rows.append(
            {
                "timestamp": ts.strftime(f"%Y-%m-%d{sep}%H:%M:%S") + tz_suffix,
                "open": price,
                "high": price + 2.0,
                "low": price - 1.0,
                "close": price + 1.0,
                "volume": 10.0 + i,
            }
        )
    return pd.DataFrame(rows)


class RecordingSource(CandleSource):
    """Fake source that records requested windows and serves a range."""

    name = "recording"
    supported_timeframes = {"1m", "5m", "15m", "1h", "4h"}
    pair_map = {"BTC-USDC": "FAKE"}

    def __init__(self, available_start: str, available_end: str, tf_minutes: int):
        super().__init__(throttle_s=0)
        self.calls = []
        self.available_start = datetime.fromisoformat(available_start)
        self.available_end = datetime.fromisoformat(available_end)
        self.tf_minutes = tf_minutes

    def fetch(self, symbol, tf, start_dt, end_dt):
        self.calls.append((start_dt, end_dt))
        lo = max(start_dt, self.available_start)
        hi = min(end_dt, self.available_end)
        if lo > hi:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        count = int((hi - lo).total_seconds() // 60 // self.tf_minutes) + 1
        return make_candles(lo.isoformat(), count, self.tf_minutes)


class TestGapDetection:
    """missing_ranges() finds leading, trailing and internal gaps."""

    def test_leading_trailing_internal_gaps(self):
        df = merge_candles(
            make_candles("2024-01-01T02:00:00", 4, 60),  # 02..05
            make_candles("2024-01-01T08:00:00", 3, 60),  # 08..10
        )
        gaps = CandleDownloadManager.missing_ranges(
            df,
            "1h",
            datetime(2024, 1, 1, 0, 0),
            datetime(2024, 1, 1, 12, 0),
        )
        assert gaps == [
            (datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 1, 0)),
            (datetime(2024, 1, 1, 6, 0), datetime(2024, 1, 1, 7, 0)),
            (datetime(2024, 1, 1, 11, 0), datetime(2024, 1, 1, 12, 0)),
        ]

    def test_no_gaps_when_fully_covered(self):
        df = make_candles("2024-01-01T00:00:00", 13, 60)
        gaps = CandleDownloadManager.missing_ranges(
            df, "1h", datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 12, 0)
        )
        assert gaps == []

    def test_empty_store_is_one_full_gap(self):
        df = pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        gaps = CandleDownloadManager.missing_ranges(
            df, "4h", datetime(2024, 1, 1), datetime(2024, 1, 2)
        )
        assert gaps == [(datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 2, 0, 0))]

    def test_coverage_reports_bounds_count_and_gaps(self, tmp_path):
        mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[])
        df = merge_candles(
            make_candles("2024-01-01T00:00:00", 3, 60),
            make_candles("2024-01-01T05:00:00", 2, 60),
        )
        mgr.save_store("BTC-USDC", "1h", df)
        cov = mgr.coverage("BTC-USDC", "1h")
        assert isinstance(cov, Coverage)
        assert cov.start == "2024-01-01T00:00:00"
        assert cov.end == "2024-01-01T06:00:00"
        assert cov.candle_count == 5
        assert cov.gaps == [("2024-01-01T03:00:00", "2024-01-01T04:00:00")]


class TestMergeAndNormalize:
    """merge_candles / normalize_candles behaviour."""

    def test_merge_dedup_idempotent(self):
        a = make_candles("2024-01-01T00:00:00", 5, 60)
        merged_once = merge_candles(a, a)
        merged_twice = merge_candles(merged_once, a)
        assert len(merged_once) == 5
        pd.testing.assert_frame_equal(merged_once, merged_twice)

    def test_first_frame_wins_on_duplicates(self):
        a = make_candles("2024-01-01T00:00:00", 2, 60)
        b = make_candles("2024-01-01T00:00:00", 2, 60)
        b["close"] = 999.0
        merged = merge_candles(a, b)
        assert merged["close"].tolist() == a["close"].tolist()

    def test_tz_aware_input_normalized_to_naive_canonical(self):
        legacy = make_candles("2018-01-01T00:00:00", 3, 5, tz_suffix="+00:00")
        assert "+00:00" in legacy["timestamp"].iloc[0]
        out = normalize_candles(legacy)
        assert out["timestamp"].tolist() == [
            "2018-01-01T00:00:00",
            "2018-01-01T00:05:00",
            "2018-01-01T00:10:00",
        ]

    def test_mixed_tz_and_naive_merge_sorted(self):
        naive = make_candles("2024-01-01T00:00:00", 2, 5)
        aware = make_candles("2023-12-31T23:50:00", 2, 5, tz_suffix="+00:00")
        merged = merge_candles(naive, aware)
        assert merged["timestamp"].tolist() == [
            "2023-12-31T23:50:00",
            "2023-12-31T23:55:00",
            "2024-01-01T00:00:00",
            "2024-01-01T00:05:00",
        ]


class TestResampling:
    """5m -> 1h/4h resampling OHLCV correctness."""

    def _mgr(self, tmp_path):
        return CandleDownloadManager(data_dir=str(tmp_path), sources=[])

    def test_5m_to_1h_ohlcv_aggregation(self, tmp_path):
        mgr = self._mgr(tmp_path)
        df = make_candles("2024-01-01T00:00:00", 12, 5)
        mgr.save_store("BTC-USDC", "5m", df)
        added = mgr.resample_fill("BTC-USDC", "5m", "1h")
        assert added == 1
        out = mgr.load_store("BTC-USDC", "1h")
        assert out["timestamp"].tolist() == ["2024-01-01T00:00:00"]
        row = out.iloc[0]
        # open of first 5m, high=max(high), low=min(low), close of last
        assert row["open"] == 100.0
        assert row["high"] == 111.0 + 2.0
        assert row["low"] == 100.0 - 1.0
        assert row["close"] == 111.0 + 1.0
        assert row["volume"] == pytest.approx(sum(10.0 + i for i in range(12)))

    def test_5m_to_4h_and_incomplete_bucket_dropped(self, tmp_path):
        mgr = self._mgr(tmp_path)
        # 48 candles = one full 4h bucket, plus 3 stragglers
        df = make_candles("2024-01-01T00:00:00", 51, 5)
        mgr.save_store("BTC-USDC", "5m", df)
        added = mgr.resample_fill("BTC-USDC", "5m", "4h", min_fraction=1.0)
        assert added == 1
        out = mgr.load_store("BTC-USDC", "4h")
        assert out["timestamp"].tolist() == ["2024-01-01T00:00:00"]
        assert out.iloc[0]["close"] == 147.0 + 1.0

    def test_lenient_fraction_keeps_partial_bucket(self, tmp_path):
        mgr = self._mgr(tmp_path)
        df = make_candles("2024-01-01T00:00:00", 30, 5)  # 2.5h of a 4h bucket
        mgr.save_store("BTC-USDC", "5m", df)
        assert mgr.resample_fill("BTC-USDC", "5m", "4h", min_fraction=1.0) == 0
        assert mgr.resample_fill("BTC-USDC", "5m", "4h", min_fraction=0.5) == 1

    def test_existing_target_candles_not_overwritten(self, tmp_path):
        mgr = self._mgr(tmp_path)
        mgr.save_store("BTC-USDC", "5m", make_candles("2024-01-01T00:00:00", 12, 5))
        native = make_candles("2024-01-01T00:00:00", 1, 60)
        native["close"] = 555.0
        mgr.save_store("BTC-USDC", "1h", native)
        assert mgr.resample_fill("BTC-USDC", "5m", "1h") == 0
        assert mgr.load_store("BTC-USDC", "1h").iloc[0]["close"] == 555.0


class TestEnsure:
    """ensure() downloads only what is missing."""

    def test_downloads_only_missing_leading_and_trailing(self, tmp_path):
        source = RecordingSource("2023-01-01T00:00:00", "2027-01-01T00:00:00", 60)
        mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[source])
        # Store already covers 2024-01-02 .. 2024-01-03
        mgr.save_store("BTC-USDC", "1h", make_candles("2024-01-02T00:00:00", 25, 60))
        mgr.ensure("BTC-USDC", "1h", "2024-01-01", "2024-01-04T00:00:00")
        assert source.calls == [
            (datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 23, 0)),
            (datetime(2024, 1, 3, 1, 0), datetime(2024, 1, 4, 0, 0)),
        ]
        cov = mgr.coverage("BTC-USDC", "1h")
        assert cov.start == "2024-01-01T00:00:00"
        assert cov.end == "2024-01-04T00:00:00"
        assert cov.gaps == []

    def test_internal_gap_download_and_skip_flag(self, tmp_path):
        source = RecordingSource("2020-01-01T00:00:00", "2027-01-01T00:00:00", 60)
        mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[source])
        with_gap = merge_candles(
            make_candles("2024-01-01T00:00:00", 5, 60),
            make_candles("2024-01-01T10:00:00", 5, 60),
        )
        mgr.save_store("BTC-USDC", "1h", with_gap)
        # include_internal_gaps=False -> nothing to fetch (bounds covered)
        mgr.ensure(
            "BTC-USDC",
            "1h",
            "2024-01-01T00:00:00",
            "2024-01-01T14:00:00",
            include_internal_gaps=False,
        )
        assert source.calls == []
        # Default fills the internal hole
        mgr.ensure("BTC-USDC", "1h", "2024-01-01T00:00:00", "2024-01-01T14:00:00")
        assert source.calls == [
            (datetime(2024, 1, 1, 5, 0), datetime(2024, 1, 1, 9, 0))
        ]
        assert mgr.coverage("BTC-USDC", "1h").gaps == []

    def test_no_download_when_fully_covered(self, tmp_path):
        source = RecordingSource("2020-01-01T00:00:00", "2027-01-01T00:00:00", 60)
        mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[source])
        mgr.save_store("BTC-USDC", "1h", make_candles("2024-01-01T00:00:00", 25, 60))
        summary = mgr.ensure(
            "BTC-USDC", "1h", "2024-01-01T00:00:00", "2024-01-02T00:00:00"
        )
        assert source.calls == []
        assert summary["added"] == 0

    def test_unfilled_range_reported_when_source_has_no_data(self, tmp_path):
        source = RecordingSource("2020-01-01T00:00:00", "2027-01-01T00:00:00", 60)
        mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[source])
        summary = mgr.ensure(
            "BTC-USDC", "1h", "2015-01-01T00:00:00", "2015-01-02T00:00:00"
        )
        assert summary["added"] == 0
        assert summary["unfilled_ranges"] == [
            (datetime(2015, 1, 1, 0, 0), datetime(2015, 1, 2, 0, 0))
        ]

    def test_ingest_external_btv2_layout(self, tmp_path):
        mgr = CandleDownloadManager(data_dir=str(tmp_path / "store"), sources=[])
        # BTV2 layout: tz-aware UTC DatetimeIndex, capitalized columns
        idx = pd.DatetimeIndex(
            pd.date_range("2018-01-01", periods=4, freq="1h", tz="UTC"),
            name="Datetime",
        )
        ext = pd.DataFrame(
            {
                "Open": [1.0, 2.0, 3.0, 4.0],
                "High": [1.5, 2.5, 3.5, 4.5],
                "Low": [0.5, 1.5, 2.5, 3.5],
                "Close": [1.2, 2.2, 3.2, 4.2],
                "Volume": [10.0, 20.0, 30.0, 40.0],
            },
            index=idx,
        )
        ext_path = tmp_path / "BTCUSDT_1h.parquet"
        ext.to_parquet(ext_path)
        # Pre-existing store candle wins on overlap
        native = make_candles("2018-01-01T03:00:00", 1, 60)
        native["close"] = 777.0
        mgr.save_store("BTC-USDC", "1h", native)
        added = mgr.ingest_external_parquet(ext_path, "BTC-USDC", "1h")
        assert added == 3
        out = mgr.load_store("BTC-USDC", "1h")
        assert out["timestamp"].tolist() == [
            "2018-01-01T00:00:00",
            "2018-01-01T01:00:00",
            "2018-01-01T02:00:00",
            "2018-01-01T03:00:00",
        ]
        assert out.iloc[3]["close"] == 777.0
        assert out.iloc[0]["open"] == 1.0

    def test_consolidate_csvs_merges_yearly_and_all_files(self, tmp_path):
        mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[])
        make_candles("2024-01-01T00:00:00", 4, 5).to_csv(
            tmp_path / "BTC-USDC_5m.csv", index=False
        )
        make_candles("2018-01-01T00:00:00", 4, 5, tz_suffix="+00:00").to_csv(
            tmp_path / "BTC-USDC_5m_2018.csv", index=False
        )
        make_candles("2018-01-01T00:00:00", 6, 5, tz_suffix="+00:00").to_csv(
            tmp_path / "BTC-USDC_5m_all.csv", index=False
        )
        added = mgr.consolidate_csvs("BTC-USDC", "5m")
        assert added == 10  # 6 from 2018 (deduped) + 4 from 2024
        cov = mgr.coverage("BTC-USDC", "5m")
        assert cov.start == "2018-01-01T00:00:00"
        assert cov.end == "2024-01-01T00:15:00"
        # Originals untouched
        assert (tmp_path / "BTC-USDC_5m_all.csv").exists()


class FakeManager:
    """Stand-in for CandleDownloadManager recording ensure() calls."""

    def __init__(self, data_dir=None, added: int = 0):
        self.calls = []
        self.added = added

    def ensure(self, symbol, tf, start, end, include_internal_gaps=True):
        self.calls.append((symbol, tf, start, end, include_internal_gaps))
        return {"added": self.added, "requested_ranges": [], "unfilled_ranges": []}


class TestLoaderAutoDownload:
    """BacktestDataLoader triggers ensure() only when coverage misses."""

    def _write_store(self, tmp_path, symbol, tf, df):
        mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[])
        mgr.save_store(symbol, tf, df)

    def test_no_trigger_when_range_covered(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        self._write_store(
            tmp_path, "BTC-USDC", "1h", make_candles("2024-01-01T00:00:00", 49, 60)
        )
        fake = FakeManager()
        loader = BacktestDataLoader(
            symbol="BTC-USDC", data_dir=str(tmp_path), download_manager=fake
        )
        candles = loader.get_candles("1h", "2024-01-01", "2024-01-02")
        assert fake.calls == []
        # String mask excludes the 2024-01-02T00:00:00 candle ("...T..."
        # sorts after the bare date) - 24 candles for Jan 1.
        assert len(candles["close"]) == 24

    def test_trigger_when_leading_coverage_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        self._write_store(
            tmp_path, "BTC-USDC", "1h", make_candles("2024-01-02T00:00:00", 25, 60)
        )
        fake = FakeManager()
        loader = BacktestDataLoader(
            symbol="BTC-USDC", data_dir=str(tmp_path), download_manager=fake
        )
        loader.get_candles("1h", "2024-01-01", "2024-01-02")
        assert fake.calls == [("BTC-USDC", "1h", "2024-01-01", "2024-01-02", False)]
        # Second identical request: attempted once only
        loader.get_candles("1h", "2024-01-01", "2024-01-02")
        assert len(fake.calls) == 1

    def test_disabled_via_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "false")
        self._write_store(
            tmp_path, "BTC-USDC", "1h", make_candles("2024-01-02T00:00:00", 25, 60)
        )
        fake = FakeManager()
        loader = BacktestDataLoader(
            symbol="BTC-USDC", data_dir=str(tmp_path), download_manager=fake
        )
        loader.get_candles("1h", "2024-01-01", "2024-01-02")
        assert fake.calls == []

    def test_1m_not_autodownloaded_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        monkeypatch.delenv("DATA_AUTODOWNLOAD_TIMEFRAMES", raising=False)
        self._write_store(
            tmp_path, "BTC-USDC", "1m", make_candles("2024-01-02T00:00:00", 10, 1)
        )
        fake = FakeManager()
        loader = BacktestDataLoader(
            symbol="BTC-USDC", data_dir=str(tmp_path), download_manager=fake
        )
        loader.get_candles("1m", "2024-01-01", "2024-01-02")
        assert fake.calls == []

    def test_reload_serves_new_candles_after_download(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_AUTODOWNLOAD", "true")
        store_mgr = CandleDownloadManager(data_dir=str(tmp_path), sources=[])
        store_mgr.save_store(
            "BTC-USDC", "1h", make_candles("2024-01-02T00:00:00", 25, 60)
        )

        class WritingManager(FakeManager):
            def ensure(self, symbol, tf, start, end, include_internal_gaps=True):
                merged = merge_candles(
                    store_mgr.load_store(symbol, tf),
                    make_candles("2024-01-01T00:00:00", 24, 60),
                )
                store_mgr.save_store(symbol, tf, merged)
                self.calls.append((symbol, tf, start, end))
                return {"added": 24, "requested_ranges": [], "unfilled_ranges": []}

        fake = WritingManager()
        loader = BacktestDataLoader(
            symbol="BTC-USDC", data_dir=str(tmp_path), download_manager=fake
        )
        candles = loader.get_candles("1h", "2024-01-01", "2024-01-02")
        assert len(fake.calls) == 1
        assert candles["timestamp"][0] == "2024-01-01T00:00:00"
        assert len(candles["close"]) == 24


class TestBinancePaging:
    """Binance source paging: 1000-candle pages, partial last page."""

    def _make_pages(self, start_ms, total, tf_ms, page_size=1000):
        pages = []
        produced = 0
        while produced < total:
            n = min(page_size, total - produced)
            page = []
            for i in range(n):
                t = start_ms + (produced + i) * tf_ms
                page.append(
                    [
                        t,
                        "100.0",
                        "102.0",
                        "99.0",
                        "101.0",
                        "5.0",
                        0,
                        "0",
                        0,
                        "0",
                        "0",
                        "0",
                    ]
                )
            pages.append(page)
            produced += n
        return pages

    def test_pages_until_partial_page(self, monkeypatch):
        source = BinanceSource(throttle_s=0)
        tf_ms = 3_600_000
        start_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        pages = self._make_pages(start_ms, 2500, tf_ms)
        all_rows = [r for page in pages for r in page]
        requested = []

        def fake_get_page(params):
            requested.append(dict(params))
            rows = [
                r for r in all_rows if params["startTime"] <= r[0] <= params["endTime"]
            ]
            return rows[: params["limit"]]

        monkeypatch.setattr(source, "_get_page", fake_get_page)
        end_dt = datetime(2024, 1, 1) + timedelta(hours=2600)
        df = source.fetch("BTC-USDC", "1h", datetime(2024, 1, 1), end_dt)
        # 3 requests: 1000 + 1000 + 500 (partial last page stops the loop)
        assert len(requested) == 3
        assert len(df) == 2500
        assert requested[0]["startTime"] == start_ms
        # Paging advances to (last open time + 1) after each full page
        assert requested[1]["startTime"] == start_ms + 999 * tf_ms + 1
        assert requested[2]["startTime"] == start_ms + 1999 * tf_ms + 1
        assert df["timestamp"].iloc[0] == "2024-01-01T00:00:00"
        assert df["timestamp"].is_monotonic_increasing

    def test_empty_first_page_returns_empty_frame(self, monkeypatch):
        source = BinanceSource(throttle_s=0)
        monkeypatch.setattr(source, "_get_page", lambda params: [])
        df = source.fetch("BTC-USDC", "1h", datetime(2015, 1, 1), datetime(2015, 2, 1))
        assert df.empty


class TestBitstampPaging:
    """Bitstamp paging: start-only requests, client-side end filter.

    Regression: with both start and end supplied, Bitstamp anchors to
    end and returns the 1000 candles ENDING there. The source must
    never send "end" and must filter the requested window itself.
    """

    def test_start_only_paging_and_window_filter(self, monkeypatch):
        source = BitstampSource(throttle_s=0)
        calls = []
        t0 = 1_600_000_000
        t_max = t0 + 5000 * 3600

        def fake_get_json(url, params):
            calls.append(dict(params))
            assert "end" not in params
            step = params["step"]
            rows = []
            t = max(params["start"], t0)
            while len(rows) < params["limit"] and t <= t_max:
                rows.append(
                    {
                        "timestamp": str(t),
                        "open": "1",
                        "high": "2",
                        "low": "0.5",
                        "close": "1.5",
                        "volume": "3",
                    }
                )
                t += step
            return {"data": {"ohlc": rows}}

        monkeypatch.setattr(source, "_get_json", fake_get_json)
        start_dt = datetime.fromtimestamp(t0, tz=timezone.utc).replace(tzinfo=None)
        end_dt = start_dt + timedelta(hours=1499)
        df = source.fetch("BTC-USDC", "1h", start_dt, end_dt)
        # Two pages (1000 + partial), everything past end_dt filtered out
        assert len(calls) == 2
        assert len(df) == 1500
        assert df["timestamp"].iloc[0] == start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        assert df["timestamp"].iloc[-1] == end_dt.strftime("%Y-%m-%dT%H:%M:%S")
