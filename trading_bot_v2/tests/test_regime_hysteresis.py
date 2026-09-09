"""
Tests for regime hysteresis hardening and regime observability (Jul 2026).

Covers:
- Asymmetric exit bands (ADX exit band, vol score enter/exit bands)
- Minimum dwell-time suppression layered on the 2-count confirmation
- INDECISIVE classification on falling ADX slope in the 20-25 band
- REGIME_CHANGED emission + regime_history persistence on confirmed switches
- Idempotent trades.regime column migration
- Per-(strategy, regime) attribution math including the UNTAGGED bucket
"""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trading_bot_v2.market_regime import MarketRegime, MarketRegimeDetector


def make_detector(**overrides):
    """Create a detector with explicit default thresholds and bands."""
    params = dict(
        adx_trending_threshold=25.0,
        adx_ranging_threshold=20.0,
        adx_moderate_threshold=20.0,
        volatility_high_percentile=65.0,
        adx_exit_trending=22.0,
        vol_score_enter=68.0,
        vol_score_exit=60.0,
        min_dwell_hours=4.0,
    )
    params.update(overrides)
    return MarketRegimeDetector(**params)


@pytest.fixture
def market_data():
    """40 synthetic candles (enough for ADX slope lookback of 3)."""
    base = 1.0
    highs, lows, closes = [], [], []
    for i in range(40):
        highs.append(base + 0.05 + i * 0.01)
        lows.append(base + i * 0.01)
        closes.append(base + 0.03 + i * 0.01)
    return {
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0 + i for i in range(40)],
    }


class TestAdxExitBand:
    """TRENDING_STRONG asymmetric exit band (enter >25, exit <22)."""

    def test_adx_26_enters_trending_strong(self, market_data):
        det = make_detector()
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=26.0):
            assert det.detect_regime(market_data) == MarketRegime.TRENDING_STRONG

    def test_adx_23_holds_trending_strong(self, market_data):
        det = make_detector()
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=23.0):
            regime = det.detect_regime(
                market_data, previous_regime=MarketRegime.TRENDING_STRONG
            )
            assert regime == MarketRegime.TRENDING_STRONG

    def test_adx_23_without_previous_is_not_strong(self, market_data):
        det = make_detector()
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=23.0):
            regime = det.detect_regime(market_data)
            assert regime != MarketRegime.TRENDING_STRONG

    def test_adx_21_exits_trending_strong(self, market_data):
        det = make_detector()
        with (
            patch("trading_bot_v2.market_regime.calculate_adx", return_value=21.0),
            patch.object(det, "_adx_slope_falling", return_value=False),
        ):
            regime = det.detect_regime(
                market_data, previous_regime=MarketRegime.TRENDING_STRONG
            )
            assert regime == MarketRegime.TRENDING_MODERATE

    def test_adx_exactly_22_holds(self, market_data):
        det = make_detector()
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=22.0):
            regime = det.detect_regime(
                market_data, previous_regime=MarketRegime.TRENDING_STRONG
            )
            assert regime == MarketRegime.TRENDING_STRONG


class TestVolScoreBands:
    """RANGING_VOLATILE enter (68) / exit (60) bands at ADX <= 20."""

    def _detect(self, det, market_data, vol_score, previous):
        with (
            patch("trading_bot_v2.market_regime.calculate_adx", return_value=18.0),
            patch.object(det, "_calculate_volatility_score", return_value=vol_score),
        ):
            return det.detect_regime(market_data, previous_regime=previous)

    def test_no_previous_uses_raw_65_threshold(self, market_data):
        det = make_detector()
        assert (
            self._detect(det, market_data, 66.0, None) == MarketRegime.RANGING_VOLATILE
        )
        assert self._detect(det, market_data, 64.0, None) == MarketRegime.RANGING_CALM

    def test_entering_from_calm_requires_above_68(self, market_data):
        det = make_detector()
        assert (
            self._detect(det, market_data, 66.0, MarketRegime.RANGING_CALM)
            == MarketRegime.RANGING_CALM
        )
        assert (
            self._detect(det, market_data, 69.0, MarketRegime.RANGING_CALM)
            == MarketRegime.RANGING_VOLATILE
        )

    def test_holding_volatile_until_below_60(self, market_data):
        det = make_detector()
        assert (
            self._detect(det, market_data, 61.0, MarketRegime.RANGING_VOLATILE)
            == MarketRegime.RANGING_VOLATILE
        )
        assert (
            self._detect(det, market_data, 59.0, MarketRegime.RANGING_VOLATILE)
            == MarketRegime.RANGING_CALM
        )


class TestAdxSlopeIndecisive:
    """20 < ADX <= 25 splits into INDECISIVE (falling) / MODERATE (rising)."""

    def test_falling_slope_gives_indecisive(self, market_data):
        det = make_detector()
        # First call = current ADX (23), second = ADX 3 bars prior (24)
        with patch(
            "trading_bot_v2.market_regime.calculate_adx",
            side_effect=[23.0, 24.0],
        ):
            assert det.detect_regime(market_data) == MarketRegime.INDECISIVE

    def test_rising_slope_gives_trending_moderate(self, market_data):
        det = make_detector()
        with patch(
            "trading_bot_v2.market_regime.calculate_adx",
            side_effect=[23.0, 21.0],
        ):
            assert det.detect_regime(market_data) == MarketRegime.TRENDING_MODERATE

    def test_flat_slope_gives_trending_moderate(self, market_data):
        det = make_detector()
        with patch(
            "trading_bot_v2.market_regime.calculate_adx",
            side_effect=[23.0, 23.0],
        ):
            assert det.detect_regime(market_data) == MarketRegime.TRENDING_MODERATE

    def test_insufficient_data_treated_as_not_falling(self):
        det = make_detector()
        short_data = {
            "high": [1.0 + i * 0.01 for i in range(30)],
            "low": [0.95 + i * 0.01 for i in range(30)],
            "close": [0.98 + i * 0.01 for i in range(30)],
            "volume": [1000.0] * 30,
        }
        # 30 - 3 = 27 < 29 required, slope check returns False
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=23.0):
            assert det.detect_regime(short_data) == MarketRegime.TRENDING_MODERATE


class TestMinDwell:
    """Minimum dwell time suppresses flips after a confirmed switch."""

    def _drive(self, det, symbol, market_data, regime, at):
        """Run one cached detection at simulated time ``at``."""
        det._clock = lambda: at
        with patch.object(det, "detect_regime", return_value=regime):
            return det.detect_regime_cached(symbol, market_data)

    def test_dwell_suppresses_then_allows(self, market_data):
        det = make_detector(min_dwell_hours=4.0)
        t0 = datetime(2026, 1, 1, 0, 0, 0)
        sym = "SUI"

        # Initial regime
        assert (
            self._drive(det, sym, market_data, MarketRegime.TRENDING_STRONG, t0)
            == MarketRegime.TRENDING_STRONG
        )

        # Switch to RANGING_CALM via 2-count confirmation (no prior switch,
        # so no dwell applies yet)
        t1 = t0 + timedelta(hours=1.5)
        assert (
            self._drive(det, sym, market_data, MarketRegime.RANGING_CALM, t1)
            == MarketRegime.TRENDING_STRONG
        )  # pending
        t2 = t0 + timedelta(hours=3)
        assert (
            self._drive(det, sym, market_data, MarketRegime.RANGING_CALM, t2)
            == MarketRegime.RANGING_CALM
        )  # confirmed switch at t2

        # Within 4h of the confirmed switch: flips suppressed
        t3 = t2 + timedelta(hours=1.5)
        assert (
            self._drive(det, sym, market_data, MarketRegime.TRENDING_STRONG, t3)
            == MarketRegime.RANGING_CALM
        )
        t4 = t2 + timedelta(hours=3)
        assert (
            self._drive(det, sym, market_data, MarketRegime.TRENDING_STRONG, t4)
            == MarketRegime.RANGING_CALM
        )

        # After the dwell period: normal 2-count confirmation resumes
        t5 = t2 + timedelta(hours=4.5)
        assert (
            self._drive(det, sym, market_data, MarketRegime.TRENDING_STRONG, t5)
            == MarketRegime.RANGING_CALM
        )  # pending again
        t6 = t2 + timedelta(hours=6)
        assert (
            self._drive(det, sym, market_data, MarketRegime.TRENDING_STRONG, t6)
            == MarketRegime.TRENDING_STRONG
        )  # confirmed

    def test_two_count_confirmation_still_required(self, market_data):
        det = make_detector()
        t0 = datetime(2026, 1, 1)
        sym = "BTC"
        self._drive(det, sym, market_data, MarketRegime.RANGING_CALM, t0)
        # Single divergent detection does not switch
        t1 = t0 + timedelta(hours=2)
        assert (
            self._drive(det, sym, market_data, MarketRegime.TRENDING_STRONG, t1)
            == MarketRegime.RANGING_CALM
        )


@pytest.fixture
def temp_db():
    """Point trading_bot_v2.database at a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    original_env = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = tmp_path
    os.environ["DATABASE_BACKEND"] = "sqlite"

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = tmp_path
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool
    if original_env is not None:
        os.environ["DATABASE_PATH"] = original_env
    elif "DATABASE_PATH" in os.environ:
        del os.environ["DATABASE_PATH"]
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


class TestTransitionObservability:
    """Confirmed transitions emit REGIME_CHANGED and persist to the DB."""

    def test_confirmed_transition_emits_and_persists(self, market_data, temp_db):
        fake_bus = MagicMock()
        fake_db = MagicMock()
        det = make_detector()
        det._event_bus = fake_bus
        det._db = fake_db

        t0 = datetime(2026, 1, 1)
        sym = "SUI"

        def drive(regime, at):
            det._clock = lambda: at
            with patch.object(det, "detect_regime", return_value=regime):
                return det.detect_regime_cached(sym, market_data)

        drive(MarketRegime.RANGING_CALM, t0)
        drive(MarketRegime.TRENDING_STRONG, t0 + timedelta(hours=2))
        result = drive(MarketRegime.TRENDING_STRONG, t0 + timedelta(hours=4))
        assert result == MarketRegime.TRENDING_STRONG

        # Event emitted once with the expected payload
        assert fake_bus.publish_event.call_count == 1
        call = fake_bus.publish_event.call_args
        event_type, payload = call[0]
        source = call[1]["source"]
        from trading_bot_v2.event_system import EventType

        assert event_type == EventType.REGIME_CHANGED
        assert payload["symbol"] == sym
        assert payload["old_regime"] == "ranging_calm"
        assert payload["new_regime"] == "trending_strong"
        assert "adx" in payload
        assert "volatility_score" in payload
        assert "timestamp" in payload
        assert source == "MarketRegimeDetector"

        # DB persistence called with matching values
        assert fake_db.save_regime_transition.call_count == 1
        kwargs = fake_db.save_regime_transition.call_args[1]
        assert kwargs["symbol"] == sym
        assert kwargs["old_regime"] == "ranging_calm"
        assert kwargs["new_regime"] == "trending_strong"

    def test_transition_persists_to_regime_history_table(self, market_data, temp_db):
        db_manager = temp_db.DatabaseManager()
        det = make_detector()
        det._db = db_manager

        t0 = datetime(2026, 1, 1)
        sym = "ETH"

        def drive(regime, at):
            det._clock = lambda: at
            with patch.object(det, "detect_regime", return_value=regime):
                return det.detect_regime_cached(sym, market_data)

        drive(MarketRegime.RANGING_CALM, t0)
        drive(MarketRegime.RANGING_VOLATILE, t0 + timedelta(hours=2))
        drive(MarketRegime.RANGING_VOLATILE, t0 + timedelta(hours=4))

        rows = db_manager.get_regime_history(symbol=sym)
        assert len(rows) == 1
        assert rows[0]["old_regime"] == "ranging_calm"
        assert rows[0]["new_regime"] == "ranging_volatile"

    def test_db_failure_does_not_break_detection(self, market_data):
        failing_db = MagicMock()
        failing_db.save_regime_transition.side_effect = RuntimeError("db down")
        det = make_detector()
        det._db = failing_db

        t0 = datetime(2026, 1, 1)

        def drive(regime, at):
            det._clock = lambda: at
            with patch.object(det, "detect_regime", return_value=regime):
                return det.detect_regime_cached("SOL", market_data)

        drive(MarketRegime.RANGING_CALM, t0)
        drive(MarketRegime.TRENDING_STRONG, t0 + timedelta(hours=2))
        result = drive(MarketRegime.TRENDING_STRONG, t0 + timedelta(hours=4))
        # Transition still confirmed despite the DB error
        assert result == MarketRegime.TRENDING_STRONG


class TestTradesRegimeMigration:
    """trades.regime column migration is idempotent."""

    def test_migration_idempotent(self, temp_db):
        # init_database already ran once in the fixture; run twice more
        temp_db.init_database()
        temp_db.init_database()

        with temp_db.get_db_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(trades)")
            columns = [row[1] for row in cursor.fetchall()]
        assert columns.count("regime") == 1

    def test_save_trade_persists_regime(self, temp_db):
        db_manager = temp_db.DatabaseManager()
        trade_id = db_manager.save_trade(
            {
                "symbol": "SUI",
                "side": "LONG",
                "quantity": 10.0,
                "entry_price": 2.0,
                "entry_time": "2026-01-01T00:00:00",
                "strategy": "MeanReversion",
                "status": "open",
                "regime": "ranging_calm",
            }
        )
        assert trade_id is not None
        with temp_db.get_db_connection() as conn:
            cursor = conn.execute("SELECT regime FROM trades WHERE id = ?", (trade_id,))
            assert cursor.fetchone()[0] == "ranging_calm"


class TestRegimeAttribution:
    """Per-(strategy, regime) attribution math including UNTAGGED bucket."""

    def test_attribution_math(self, temp_db):
        from trading_bot_v2.strategy_monitor import StrategyMonitor

        db_manager = temp_db.DatabaseManager()

        def insert(strategy, regime, pnl, entry_price=10.0, quantity=10.0):
            db_manager.save_trade(
                {
                    "symbol": "SUI",
                    "side": "LONG",
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "entry_time": "2026-01-01T00:00:00",
                    "pnl": pnl,
                    "strategy": strategy,
                    "status": "closed",
                    "regime": regime,
                }
            )

        # Notional = 100 -> pnl of 10 == 10 pct
        insert("MeanReversion", "ranging_calm", 10.0)
        insert("MeanReversion", "ranging_calm", -5.0)
        insert("MeanReversion", "trending_strong", 4.0)
        insert("MACrossover", None, 2.0)  # untagged
        # Open trade must be excluded
        db_manager.save_trade(
            {
                "symbol": "SUI",
                "side": "LONG",
                "quantity": 1.0,
                "entry_price": 10.0,
                "entry_time": "2026-01-01T00:00:00",
                "pnl": 999.0,
                "strategy": "MeanReversion",
                "status": "open",
                "regime": "ranging_calm",
            }
        )

        with patch("trading_bot_v2.strategy_monitor.get_event_bus") as mock_bus:
            mock_bus.return_value = MagicMock()
            monitor = StrategyMonitor()

        attribution = monitor.get_regime_attribution()

        calm = attribution["MeanReversion"]["ranging_calm"]
        assert calm["trade_count"] == 2
        assert calm["win_rate"] == 0.5
        assert calm["profit_factor"] == 2.0  # 10 / 5
        assert calm["avg_pnl_pct"] == 2.5  # (10 - 5) / 2
        assert calm["total_pnl_pct"] == 5.0

        strong = attribution["MeanReversion"]["trending_strong"]
        assert strong["trade_count"] == 1
        assert strong["win_rate"] == 1.0
        assert strong["profit_factor"] is None  # no losses

        untagged = attribution["MACrossover"]["UNTAGGED"]
        assert untagged["trade_count"] == 1
        assert untagged["total_pnl_pct"] == 2.0
