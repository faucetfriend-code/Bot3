"""Tests for perpetual-funding ingest, venue mapping and simulation.

Three layers:

* ``data_manager`` - the funding store (normalization, grid snapping,
  paging, gap-driven ensure, coverage). All network access is mocked.
* ``backtesting/funding`` - the 8h-source to 1h-venue mapping, which is
  the one modelling assumption in the whole path.
* ``SimulatedExchange`` - that funding is actually charged, that the
  flat model's shipped behaviour is unchanged, and that a real negative
  rate CREDITS a long (the flat model can never express that).
"""

import random
from datetime import datetime, timedelta

import pandas as pd
import pytest

from trading_bot_v2.backtesting.funding import (
    CONVERSION_IDENTITY,
    CONVERSION_PRORATA,
    DEFAULT_CONVERSION,
    DEFAULT_FUNDING_MODEL,
    FundingSchedule,
    conversion_factor,
    load_funding_schedule,
    validate_conversion,
    validate_funding_model,
)
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.data_manager import (
    FUNDING_COLUMNS,
    FUNDING_KEY,
    BinanceFundingSource,
    CandleDownloadManager,
    FundingSource,
    merge_funding,
    normalize_funding,
)
from trading_bot_v2.models import AssetClass, OrderSide, Signal, StrategyType


# ---------------------------------------------------------------------
# Store: normalization, merge, coverage
# ---------------------------------------------------------------------


def make_funding(start: str, count: int, rate: float = 0.0001):
    """Build a synthetic 8h-grid funding frame."""
    start_dt = datetime.fromisoformat(start)
    rows = []
    for i in range(count):
        ts = start_dt + timedelta(hours=8 * i)
        rows.append(
            {
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                "funding_rate": rate,
                "mark_price": 30000.0,
                "rate_type": "Regular",
            }
        )
    return pd.DataFrame(rows)


class TestFundingNormalization:
    def test_canonical_columns_and_order(self):
        df = make_funding("2024-01-01T00:00:00", 3)
        out = normalize_funding(df.iloc[::-1])
        assert list(out.columns) == FUNDING_COLUMNS
        assert out["timestamp"].tolist() == [
            "2024-01-01T00:00:00",
            "2024-01-01T08:00:00",
            "2024-01-01T16:00:00",
        ]

    def test_millisecond_late_settlement_snaps_to_grid(self):
        """Binance stamps some settlements at HH:00:00.001."""
        df = pd.DataFrame(
            [{"timestamp": "2024-01-01T16:00:00.001", "funding_rate": 0.0002}]
        )
        out = normalize_funding(df)
        assert out["timestamp"].iloc[0] == "2024-01-01T16:00:00"

    def test_genuinely_off_grid_settlement_is_preserved(self):
        """A special settlement far from the grid keeps its own time."""
        df = pd.DataFrame(
            [{"timestamp": "2024-01-01T12:34:00", "funding_rate": 0.0002}]
        )
        out = normalize_funding(df)
        assert out["timestamp"].iloc[0] == "2024-01-01T12:34:00"

    def test_missing_optional_columns_are_filled(self):
        df = pd.DataFrame(
            [{"timestamp": "2024-01-01T00:00:00", "funding_rate": 0.0003}]
        )
        out = normalize_funding(df)
        assert out["rate_type"].iloc[0] == "Regular"
        assert pd.isna(out["mark_price"].iloc[0])

    def test_empty_frame_gives_canonical_empty(self):
        out = normalize_funding(pd.DataFrame())
        assert out.empty
        assert list(out.columns) == FUNDING_COLUMNS

    def test_merge_dedups_first_wins(self):
        a = make_funding("2024-01-01T00:00:00", 2, rate=0.0001)
        b = make_funding("2024-01-01T00:00:00", 3, rate=0.0009)
        merged = merge_funding(a, b)
        assert len(merged) == 3
        assert merged["funding_rate"].iloc[0] == pytest.approx(0.0001)
        assert merged["funding_rate"].iloc[2] == pytest.approx(0.0009)

    def test_merge_is_idempotent(self):
        a = make_funding("2024-01-01T00:00:00", 5)
        assert len(merge_funding(a, a)) == 5


class StubFundingSource(FundingSource):
    """Deterministic offline funding source."""

    name = "stub-funding"
    pair_map = {"BTC-USDC": "BTCUSDT"}

    def __init__(self, frame):
        super().__init__(throttle_s=0.0)
        self._frame = frame
        self.calls = []

    def fetch(self, symbol, start_dt, end_dt):
        self.calls.append((start_dt, end_dt))
        df = normalize_funding(self._frame)
        keep = (pd.to_datetime(df["timestamp"]) >= start_dt) & (
            pd.to_datetime(df["timestamp"]) <= end_dt
        )
        return df[keep].reset_index(drop=True)


class TestFundingStore:
    def test_ensure_downloads_only_missing_ranges(self, tmp_path):
        full = make_funding("2024-01-01T00:00:00", 9)
        source = StubFundingSource(full)
        mgr = CandleDownloadManager(data_dir=str(tmp_path), funding_sources=[source])
        mgr.save_funding_store("BTC-USDC", full.iloc[:3])

        mgr.ensure_funding("BTC-USDC", "2024-01-01", "2024-01-04")
        cov = mgr.funding_coverage("BTC-USDC")
        assert cov.candle_count == 9
        assert cov.gaps == []
        # The already-stored leading block was never requested.
        assert all(start >= datetime(2024, 1, 2) for start, _ in source.calls)

    def test_coverage_reports_internal_gaps(self, tmp_path):
        full = make_funding("2024-01-01T00:00:00", 9)
        holed = pd.concat([full.iloc[:3], full.iloc[6:]], ignore_index=True)
        mgr = CandleDownloadManager(data_dir=str(tmp_path), funding_sources=[])
        mgr.save_funding_store("BTC-USDC", holed)
        cov = mgr.funding_coverage("BTC-USDC")
        assert cov.timeframe == FUNDING_KEY
        assert cov.candle_count == 6
        assert cov.gaps == [("2024-01-02T00:00:00", "2024-01-02T16:00:00")]

    def test_missing_store_reports_empty_coverage(self, tmp_path):
        mgr = CandleDownloadManager(data_dir=str(tmp_path), funding_sources=[])
        cov = mgr.funding_coverage("BTC-USDC")
        assert cov.candle_count == 0
        assert cov.start is None

    def test_ensure_is_idempotent(self, tmp_path):
        full = make_funding("2024-01-01T00:00:00", 6)
        source = StubFundingSource(full)
        mgr = CandleDownloadManager(data_dir=str(tmp_path), funding_sources=[source])
        mgr.ensure_funding("BTC-USDC", "2024-01-01", "2024-01-02T16:00:00")
        first = mgr.funding_coverage("BTC-USDC").candle_count
        second = mgr.ensure_funding("BTC-USDC", "2024-01-01", "2024-01-02T16:00:00")
        assert second["added"] == 0
        assert mgr.funding_coverage("BTC-USDC").candle_count == first


class TestBinanceFundingPaging:
    def test_pages_until_short_page(self, monkeypatch):
        source = BinanceFundingSource(throttle_s=0.0)
        source.page_limit = 2
        base = 1704067200000  # 2024-01-01T00:00:00Z

        def fake_get_json(url, params):
            start = params["startTime"]
            idx = max(0, (start - base) // (8 * 3600 * 1000))
            if idx >= 3:
                return []
            out = []
            for k in range(min(2, 3 - idx)):
                out.append(
                    {
                        "symbol": "BTCUSDT",
                        "fundingTime": base + (idx + k) * 8 * 3600 * 1000,
                        "fundingRate": "0.00010000",
                        "markPrice": "42000.0",
                        "rateType": "Regular",
                    }
                )
            return out

        monkeypatch.setattr(source, "_get_json", fake_get_json)
        df = source.fetch("BTC-USDC", datetime(2024, 1, 1), datetime(2024, 1, 3))
        assert len(df) == 3
        assert df["timestamp"].iloc[0] == "2024-01-01T00:00:00"
        assert df["timestamp"].iloc[-1] == "2024-01-01T16:00:00"

    def test_empty_first_page_returns_canonical_empty(self, monkeypatch):
        source = BinanceFundingSource(throttle_s=0.0)
        monkeypatch.setattr(source, "_get_json", lambda url, params: [])
        df = source.fetch("BTC-USDC", datetime(2024, 1, 1), datetime(2024, 1, 3))
        assert df.empty
        assert list(df.columns) == FUNDING_COLUMNS


# ---------------------------------------------------------------------
# Venue mapping: the 8h/1h assumption
# ---------------------------------------------------------------------


class TestConversion:
    def test_prorata_is_one_eighth_for_pacifica(self):
        assert conversion_factor(CONVERSION_PRORATA, 1, 8) == pytest.approx(0.125)

    def test_identity_keeps_the_number(self):
        assert conversion_factor(CONVERSION_IDENTITY, 1, 8) == 1.0

    def test_same_interval_venue_is_a_no_op(self):
        assert conversion_factor(CONVERSION_PRORATA, 8, 8) == 1.0

    def test_unknown_mode_falls_back_without_raising(self):
        assert validate_conversion("nonsense") == DEFAULT_CONVERSION
        assert validate_conversion(None) == DEFAULT_CONVERSION

    def test_unknown_model_falls_back_without_raising(self):
        assert validate_funding_model("nonsense") == DEFAULT_FUNDING_MODEL
        assert validate_funding_model("historical") == "historical"


def build_schedule(**kwargs):
    """8h series: 0.0008, then -0.0016, at 00:00 and 08:00."""
    defaults = dict(
        symbol="BTC-USDC",
        times=[datetime(2024, 1, 1, 0), datetime(2024, 1, 1, 8)],
        rates=[0.0008, -0.0016],
        venue_interval_hours=1,
    )
    defaults.update(kwargs)
    return FundingSchedule(**defaults)


class TestFundingSchedule:
    def test_venue_rate_is_prorated(self):
        sched = build_schedule()
        assert sched.venue_rate_at(datetime(2024, 1, 1, 3)) == pytest.approx(0.0001)

    def test_scale_multiplies_the_basis(self):
        sched = build_schedule(scale=2.0)
        assert sched.venue_rate_at(datetime(2024, 1, 1, 3)) == pytest.approx(0.0002)

    def test_lookup_never_looks_ahead(self):
        """07:59 must still see the 00:00 settlement, not the 08:00 one."""
        sched = build_schedule()
        assert sched.observed_rate_at(datetime(2024, 1, 1, 7, 59)) == pytest.approx(
            0.0008
        )
        assert sched.observed_rate_at(datetime(2024, 1, 1, 8)) == pytest.approx(-0.0016)

    def test_before_series_start_is_none(self):
        sched = build_schedule()
        assert sched.venue_rate_at(datetime(2023, 12, 31)) is None

    def test_sign_is_preserved(self):
        sched = build_schedule()
        assert sched.venue_rate_at(datetime(2024, 1, 1, 12)) < 0

    def test_venue_history_is_on_the_venue_grid(self):
        sched = build_schedule()
        hist = sched.venue_history(datetime(2024, 1, 1, 10, 30), limit=4)
        assert [h["funding_time"] for h in hist] == [
            "2024-01-01T07:00:00",
            "2024-01-01T08:00:00",
            "2024-01-01T09:00:00",
            "2024-01-01T10:00:00",
        ]
        # The 08:00 settlement flips the sign mid-window.
        assert hist[0]["funding_rate"] > 0
        assert hist[-1]["funding_rate"] < 0

    def test_venue_history_skips_pre_series_slots(self):
        sched = build_schedule()
        hist = sched.venue_history(datetime(2024, 1, 1, 1), limit=8)
        assert len(hist) == 2  # only 00:00 and 01:00 exist

    def test_settlement_times_follow_the_venue_interval(self):
        hourly = build_schedule(venue_interval_hours=1)
        eight = build_schedule(venue_interval_hours=8)
        assert hourly.is_settlement_time(datetime(2024, 1, 1, 3, 0))
        assert not eight.is_settlement_time(datetime(2024, 1, 1, 3, 0))
        assert eight.is_settlement_time(datetime(2024, 1, 1, 8, 0))
        assert not hourly.is_settlement_time(datetime(2024, 1, 1, 3, 5))

    def test_charge_for_is_none_off_settlement(self):
        sched = build_schedule()
        assert sched.charge_for(datetime(2024, 1, 1, 3, 5)) is None
        assert sched.charge_for(datetime(2024, 1, 1, 3, 0)) is not None

    def test_eight_hour_venue_needs_no_rescaling(self):
        sched = build_schedule(venue_interval_hours=8)
        assert sched.venue_rate_at(datetime(2024, 1, 1, 4)) == pytest.approx(0.0008)

    def test_load_returns_none_when_store_absent(self, tmp_path):
        assert load_funding_schedule("BTC-USDC", str(tmp_path)) is None

    def test_load_roundtrips_a_written_store(self, tmp_path):
        mgr = CandleDownloadManager(data_dir=str(tmp_path), funding_sources=[])
        mgr.save_funding_store(
            "BTC-USDC", make_funding("2024-01-01T00:00:00", 3, rate=0.0008)
        )
        sched = load_funding_schedule("BTC-USDC", str(tmp_path), venue_interval_hours=1)
        assert sched is not None
        assert len(sched) == 3
        assert sched.venue_rate_at(datetime(2024, 1, 1, 5)) == pytest.approx(0.0001)


# ---------------------------------------------------------------------
# SimulatedExchange
# ---------------------------------------------------------------------


def open_position(exchange, side="bid", qty="10", price=100.0, ts=None):
    """Open a market position at ``price`` on the given side."""
    exchange._current_price = price
    exchange._current_timestamp = ts or "2024-01-01T00:00:00"
    exchange.place_order("BTC-USDC", side, qty, order_type="market")


def advance_hours(exchange, hours, start=datetime(2024, 1, 1), price=100.0):
    """Advance the exchange one 5m bar at a time for ``hours`` hours."""
    t = start
    for _ in range(hours * 12):
        t += timedelta(minutes=5)
        exchange.advance(
            {"open": price, "high": price, "low": price, "close": price, "volume": 1.0},
            t.isoformat(),
        )


class TestFlatFundingModelUnchanged:
    """The shipped flat model must behave exactly as before."""

    def test_long_pays_every_hour(self):
        ex = SimulatedExchange(initial_capital=10000.0)
        open_position(ex)
        advance_hours(ex, 24)
        pos = ex._positions["BTC-USDC"]
        # 24 charges x 1000 notional x 0.0001
        assert pos.funding_paid == pytest.approx(-2.4, abs=1e-6)
        assert ex.funding_events == 24

    def test_short_receives_every_hour(self):
        ex = SimulatedExchange(initial_capital=10000.0)
        open_position(ex, side="ask")
        advance_hours(ex, 24)
        assert ex._positions["BTC-USDC"].funding_paid == pytest.approx(2.4, abs=1e-6)

    def test_eight_hour_venue_charges_three_times_a_day(self):
        ex = SimulatedExchange(initial_capital=10000.0, funding_interval_hours=8)
        open_position(ex)
        advance_hours(ex, 24)
        assert ex.funding_events == 3


class TestHistoricalFundingModel:
    def test_negative_rate_credits_a_long(self):
        """The whole point: real funding has a sign, the flat model does not."""
        sched = FundingSchedule(
            symbol="BTC-USDC",
            times=[datetime(2024, 1, 1)],
            rates=[-0.0008],
            venue_interval_hours=1,
        )
        ex = SimulatedExchange(initial_capital=10000.0, funding_schedule=sched)
        open_position(ex)
        advance_hours(ex, 8)
        # -0.0001/hr on 1000 notional, long -> +0.10/hr, +0.80 over 8h
        assert ex._positions["BTC-USDC"].funding_paid == pytest.approx(0.8, abs=1e-6)
        assert ex.total_funding > 0

    def test_rate_is_one_eighth_of_the_flat_default(self):
        sched = FundingSchedule(
            symbol="BTC-USDC",
            times=[datetime(2024, 1, 1)],
            rates=[0.0001],
            venue_interval_hours=1,
        )
        ex = SimulatedExchange(initial_capital=10000.0, funding_schedule=sched)
        flat = SimulatedExchange(initial_capital=10000.0)
        open_position(ex)
        open_position(flat)
        advance_hours(ex, 24)
        advance_hours(flat, 24)
        assert ex._positions["BTC-USDC"].funding_paid == pytest.approx(
            flat._positions["BTC-USDC"].funding_paid / 8, abs=1e-6
        )

    def test_nothing_charged_before_the_series_starts(self):
        sched = FundingSchedule(
            symbol="BTC-USDC",
            times=[datetime(2025, 1, 1)],
            rates=[0.0008],
            venue_interval_hours=1,
        )
        ex = SimulatedExchange(initial_capital=10000.0, funding_schedule=sched)
        open_position(ex)
        advance_hours(ex, 24)
        assert ex.total_funding == 0.0
        assert ex.funding_events == 0


class TestFillOrderDeterminism:
    """The SL/TP tie-break must not draw from the global RNG.

    It used to, so two runs of the same window over the same data could
    disagree - measured at PF 0.9485 vs 1.0287 on vwap_scalping/BTC-USDC
    2022-06..08 across consecutive runs in one process.
    """

    def _run(self, seed):
        ex = SimulatedExchange(initial_capital=10000.0, seed=seed)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("BTC-USDC", "bid", "10", order_type="market")
        # A stop below and a take-profit above, both inside one bar.
        ex.place_order("BTC-USDC", "ask", "10", order_type="stop", price=95.0)
        ex.place_order("BTC-USDC", "ask", "10", order_type="limit", price=110.0)
        ex.advance(
            {"open": 100, "high": 115, "low": 90, "close": 100, "volume": 1},
            "2024-01-01T00:05:00",
        )
        return [t["order_id"] for t in ex.trade_log]

    def test_same_seed_gives_the_same_fill_order(self):
        assert self._run(7) == self._run(7)

    def test_global_random_state_is_not_consumed(self):
        random.seed(1234)
        before = random.random()
        random.seed(1234)
        self._run(0)
        assert random.random() == before

    def test_seed_actually_changes_the_tie_break(self):
        """Some seed must order the two exits differently, or the knob lies."""
        baseline = self._run(0)
        assert any(self._run(s) != baseline for s in range(1, 25))


class TestStrategyFacingSurfaces:
    def _exchange(self):
        sched = build_schedule()
        ex = SimulatedExchange(initial_capital=10000.0, funding_schedule=sched)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T03:00:00"
        return ex

    def test_get_market_data_shape(self):
        data = self._exchange().get_market_data("BTC-USDC")
        assert data["funding_rate"] == pytest.approx(0.0001)
        assert data["funding_interval_hours"] == 1
        assert data["next_funding_time"].startswith("2024-01-01T04:00")

    def test_get_funding_history_is_venue_grid(self):
        hist = self._exchange().get_funding_history("BTC-USDC", limit=3)
        assert len(hist) == 3
        assert hist[-1]["funding_time"] == "2024-01-01T03:00:00"

    def test_get_funding_history_empty_without_real_data(self):
        """A strategy must not average an invented constant."""
        ex = SimulatedExchange(initial_capital=10000.0)
        ex._current_timestamp = "2024-01-01T03:00:00"
        assert ex.get_funding_history("BTC-USDC") == []

    def test_get_balance_exposes_equity(self):
        ex = self._exchange()
        bal = ex.get_balance()
        assert bal["equity"] == pytest.approx(10000.0)
        assert bal["available"] == pytest.approx(10000.0)


# ---------------------------------------------------------------------
# Engine contract
# ---------------------------------------------------------------------


class TestEngineContract:
    def test_funding_arb_is_backtestable(self):
        from trading_bot_v2.backtesting.engine import NON_BACKTESTABLE_STRATEGIES

        assert "funding_arb" not in NON_BACKTESTABLE_STRATEGIES
        assert "orderbook_imbalance" in NON_BACKTESTABLE_STRATEGIES

    def test_stopless_signal_has_zero_rrr_instead_of_raising(self):
        sig = Signal(
            strategy=StrategyType.FUNDING_ARB,
            asset="BTC-USDC",
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.SELL,
            entry_price=100.0,
            stop_loss=None,
            take_profit=None,
            confidence=0.8,
        )
        assert sig.rrr == 0.0
        assert sig.stop_distance_pct == 0.0

    def test_explicit_close_bypasses_the_hedge_mode_block(self):
        from trading_bot_v2.backtesting.engine import BacktestEngine

        engine = BacktestEngine()
        engine._resolve_execution_policy()
        assert engine._opposing_closes_position is False

        ex = SimulatedExchange(initial_capital=10000.0)
        open_position(ex, side="ask")  # short
        assert "BTC-USDC" in ex._positions

        close = Signal(
            strategy=StrategyType.FUNDING_ARB,
            asset="BTC-USDC",
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=100.0,
            stop_loss=None,
            take_profit=None,
            confidence=0.9,
            indicators={"close_position": True},
        )
        assert engine._execute_signal(close, ex, candle_idx=1) is True
        assert "BTC-USDC" not in ex._positions

    def test_plain_opposing_signal_is_still_blocked(self):
        from trading_bot_v2.backtesting.engine import BacktestEngine

        engine = BacktestEngine()
        engine._resolve_execution_policy()
        ex = SimulatedExchange(initial_capital=10000.0)
        open_position(ex, side="ask")

        entry = Signal(
            strategy=StrategyType.MEAN_REVERSION,
            asset="BTC-USDC",
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            confidence=0.9,
        )
        assert engine._execute_signal(entry, ex, candle_idx=1) is False
        assert "BTC-USDC" in ex._positions
