"""
Tests for the trading_bot_v2.history module (Jul 2026 consolidation).

Covers:
- metrics functions match the legacy StrategyMonitor helper semantics
  (thin wrappers remain and must agree on the same series)
- TradeStore round-trip on a temp DB: auto/explicit exchange tagging,
  COALESCE default for legacy NULL rows, and every query filter
- aggregate() spot-checked against hand-computed values
- Kelly + adaptive weights produce identical numbers on a seeded temp DB
  through the legacy db path and the TradeStore path
- exchange column migration is idempotent
"""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trading_bot_v2.history import (
    DEFAULT_EXCHANGE,
    TradeStore,
    get_active_exchange_name,
    metrics,
)

NOW = datetime(2026, 7, 19, 12, 0, 0)


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


def _closed_trade(
    symbol="SUI",
    strategy="MEAN_REVERSION",
    regime="ranging_calm",
    pnl=2.0,
    entry_price=100.0,
    quantity=1.0,
    exit_time=None,
    exchange=None,
):
    """Build a closed-trade dict for save_trade/record_trade."""
    ts = exit_time or NOW.isoformat()
    trade = {
        "symbol": symbol,
        "side": "LONG",
        "quantity": quantity,
        "entry_price": entry_price,
        "entry_time": ts,
        "exit_time": ts,
        "pnl": pnl,
        "strategy": strategy,
        "status": "closed",
        "regime": regime,
    }
    if exchange is not None:
        trade["exchange"] = exchange
    return trade


# ============================================================
# 1. Metrics functions vs legacy StrategyMonitor helpers
# ============================================================


SERIES = [
    [],
    [1.0, 2.0, 3.0],
    [-1.0, -2.0],
    [2.0, -1.0, 4.0, -1.0],
    [10.0, -5.0, -5.0],
    [0.0, 0.0],
    [1.5, -0.5, 0.0, 2.25, -3.0],
]


class TestMetricsMatchLegacyHelpers:
    @pytest.fixture
    def monitor(self, temp_db):
        from trading_bot_v2.strategy_monitor import StrategyMonitor

        with patch("trading_bot_v2.strategy_monitor.get_event_bus") as bus:
            bus.return_value = MagicMock()
            return StrategyMonitor()

    @pytest.mark.parametrize("series", SERIES)
    def test_profit_factor_identical(self, monitor, series):
        assert monitor._compute_profit_factor(series) == metrics.profit_factor(series)

    @pytest.mark.parametrize("series", SERIES)
    def test_max_drawdown_identical(self, monitor, series):
        assert monitor._compute_max_drawdown_pct(series) == metrics.max_drawdown_pct(
            series
        )

    def test_profit_factor_known_values(self):
        assert metrics.profit_factor([2.0, -1.0, 4.0, -1.0]) == pytest.approx(3.0)
        assert metrics.profit_factor([1.0, 2.0]) is None  # no losses
        assert metrics.profit_factor([-1.0, -2.0]) == 0.0  # no wins
        assert metrics.profit_factor([]) is None

    def test_max_drawdown_known_value(self):
        # 100 -> 110 -> 104.5 -> 99.275; dd = (110 - 99.275) / 110
        dd = metrics.max_drawdown_pct([10.0, -5.0, -5.0])
        assert dd == pytest.approx(9.75, abs=0.01)
        assert metrics.max_drawdown_pct([]) == 0.0
        assert metrics.max_drawdown_pct([1.0, 2.0]) == 0.0

    def test_win_rate_and_expectancy(self):
        assert metrics.win_rate([]) == 0.0
        assert metrics.win_rate([1.0, -1.0, 2.0, 0.0]) == 0.5
        assert metrics.expectancy([]) == 0.0
        assert metrics.expectancy([2.0, -1.0]) == pytest.approx(0.5)

    def test_recency_weighted_expectancy(self):
        # Equal ages: reduces to the plain mean.
        assert metrics.recency_weighted_expectancy(
            [(2.0, 5.0), (4.0, 5.0)], half_life_days=14.0
        ) == pytest.approx(3.0)
        # One trade now, one exactly one half-life old (half weight):
        # (4*1 + 1*0.5) / 1.5 = 3.0
        assert metrics.recency_weighted_expectancy(
            [(4.0, 0.0), (1.0, 14.0)], half_life_days=14.0
        ) == pytest.approx(3.0)
        assert metrics.recency_weighted_expectancy([], 14.0) == 0.0

    def test_trade_pnl_pct(self):
        assert metrics.trade_pnl_pct(2.0, 100.0, 1.0) == pytest.approx(2.0)
        assert metrics.trade_pnl_pct(None, 100.0, 1.0) == pytest.approx(0.0)
        assert metrics.trade_pnl_pct(2.0, 0.0, 1.0) is None  # no notional
        assert metrics.trade_pnl_pct("bad", 100.0, 1.0) is None


# ============================================================
# 2. TradeStore round-trip and filters
# ============================================================


class TestTradeStoreRoundTrip:
    def test_record_auto_tags_default_exchange(self, temp_db, monkeypatch):
        monkeypatch.delenv("EXCHANGE", raising=False)
        store = TradeStore(db=temp_db.DatabaseManager())
        store.record_trade(_closed_trade())
        trades = store.get_closed_trades()
        assert len(trades) == 1
        assert trades[0]["exchange"] == DEFAULT_EXCHANGE

    def test_record_auto_tags_env_exchange(self, temp_db, monkeypatch):
        monkeypatch.setenv("EXCHANGE", "blofin")
        assert get_active_exchange_name() == "blofin"
        store = TradeStore(db=temp_db.DatabaseManager())
        store.record_trade(_closed_trade())
        assert store.get_closed_trades()[0]["exchange"] == "blofin"

    def test_record_explicit_exchange_wins(self, temp_db, monkeypatch):
        monkeypatch.setenv("EXCHANGE", "blofin")
        store = TradeStore(db=temp_db.DatabaseManager())
        store.record_trade(_closed_trade(exchange="mockswap"))
        assert store.get_closed_trades()[0]["exchange"] == "mockswap"

    def test_legacy_null_rows_coalesce_to_pacifica(self, temp_db):
        dbm = temp_db.DatabaseManager()
        # Simulate a pre-migration row: saved without exchange tag.
        dbm.save_trade(_closed_trade())
        store = TradeStore(db=dbm)
        rows = store.get_closed_trades()
        assert rows[0]["exchange"] == "pacifica"
        # And the pacifica filter matches the untagged legacy row.
        assert len(store.get_closed_trades(exchange="pacifica")) == 1
        assert store.get_closed_trades(exchange="blofin") == []

    @pytest.mark.parametrize("active_exchange", ["pacifica", "blofin"])
    def test_filters(self, temp_db, monkeypatch, active_exchange):
        # Both exchanges are live, so the filters must behave identically
        # whichever one is active.  EXCHANGE is pinned rather than read
        # from the ambient environment (the operator's .env sets it): a
        # test that depends on whichever default happens to be configured
        # flips every time the default moves and covers only one exchange.
        monkeypatch.setenv("EXCHANGE", active_exchange)
        other_exchange = "blofin" if active_exchange == "pacifica" else "pacifica"
        store = TradeStore(db=temp_db.DatabaseManager())
        old_ts = (NOW - timedelta(days=10)).isoformat()
        store.record_trade(
            _closed_trade(
                symbol="SUI",
                strategy="MEAN_REVERSION",
                regime="ranging_calm",
                pnl=2.0,
                exchange="pacifica",
            )
        )
        store.record_trade(
            _closed_trade(
                symbol="BTC",
                strategy="GRID_TRADING",
                regime="ranging_volatile",
                pnl=-1.0,
                exit_time=old_ts,
                exchange="blofin",
            )
        )
        # Open trade must never appear in closed queries.  Left untagged
        # on purpose: it must pick up the active exchange, whichever that is.
        store.record_trade(
            {
                "symbol": "ETH",
                "side": "LONG",
                "quantity": 1.0,
                "entry_price": 10.0,
                "entry_time": NOW.isoformat(),
                "strategy": "MEAN_REVERSION",
                "status": "open",
                "regime": "ranging_calm",
            }
        )

        assert len(store.get_closed_trades()) == 2
        assert [
            t["strategy"] for t in store.get_closed_trades(strategy="MEAN_REVERSION")
        ] == ["MEAN_REVERSION"]
        assert [
            t["regime"] for t in store.get_closed_trades(regime="ranging_volatile")
        ] == ["ranging_volatile"]
        # Explicit per-exchange filtering: each tagged trade is reachable
        # by its own exchange and invisible under the other one.
        assert [t["symbol"] for t in store.get_closed_trades(exchange="blofin")] == [
            "BTC"
        ]
        assert [t["symbol"] for t in store.get_closed_trades(exchange="pacifica")] == [
            "SUI"
        ]
        assert store.get_closed_trades(exchange="mockswap") == []
        assert [t["symbol"] for t in store.get_closed_trades(symbol="SUI")] == ["SUI"]
        # since: only the recent trade has exit_time >= NOW - 1 day
        recent = store.get_closed_trades(since=NOW - timedelta(days=1))
        assert [t["symbol"] for t in recent] == ["SUI"]
        # limit: newest (by exit_time) first
        limited = store.get_closed_trades(limit=1)
        assert len(limited) == 1
        assert limited[0]["symbol"] == "SUI"
        # open trades
        open_trades = store.get_open_trades()
        assert [t["symbol"] for t in open_trades] == ["ETH"]
        # Untagged writes take the active exchange, and the open-trade
        # exchange filter agrees with the tag it was given.
        assert open_trades[0]["exchange"] == active_exchange
        assert [
            t["symbol"] for t in store.get_open_trades(exchange=active_exchange)
        ] == ["ETH"]
        assert store.get_open_trades(exchange=other_exchange) == []

    def test_get_recent_trades_shape_and_filter(self, temp_db):
        store = TradeStore(db=temp_db.DatabaseManager())
        store.record_trade(_closed_trade(symbol="SUI", exchange="pacifica"))
        store.record_trade(_closed_trade(symbol="BTC", exchange="blofin"))
        rows = store.get_recent_trades(limit=10)
        assert len(rows) == 2
        assert rows[0]["type"] == "trade"
        assert {"id", "symbol", "status", "exchange"} <= set(rows[0].keys())
        only_blofin = store.get_recent_trades(limit=10, exchange="blofin")
        assert [r["symbol"] for r in only_blofin] == ["BTC"]

    def test_database_get_trades_exchange_passthrough(self, temp_db):
        dbm = temp_db.DatabaseManager()
        TradeStore(db=dbm).record_trade(_closed_trade(symbol="BTC", exchange="blofin"))
        rows = dbm.get_trades(limit=100, exchange="blofin")
        assert [r["symbol"] for r in rows] == ["BTC"]
        assert rows[0]["exchange"] == "blofin"


# ============================================================
# 3. aggregate()
# ============================================================


class TestAggregate:
    def test_aggregate_hand_computed(self, temp_db):
        store = TradeStore(db=temp_db.DatabaseManager())
        # MEAN_REVERSION / ranging_calm: pnl +10, -5
        store.record_trade(_closed_trade(pnl=10.0))
        store.record_trade(_closed_trade(pnl=-5.0))
        # GRID_TRADING / untagged regime: pnl +2
        store.record_trade(_closed_trade(strategy="GRID_TRADING", regime=None, pnl=2.0))

        agg = store.aggregate(group_by=("strategy", "regime"))

        calm = agg["MEAN_REVERSION"]["ranging_calm"]
        assert calm["trade_count"] == 2
        assert calm["win_rate"] == 0.5
        assert calm["profit_factor"] == pytest.approx(2.0)  # 10 / 5
        assert calm["expectancy"] == pytest.approx(2.5)  # (10 - 5) / 2
        assert calm["total_pnl"] == pytest.approx(5.0)

        untagged = agg["GRID_TRADING"]["UNTAGGED"]
        assert untagged["trade_count"] == 1
        assert untagged["profit_factor"] is None  # no losses
        assert untagged["total_pnl"] == pytest.approx(2.0)

    def test_aggregate_by_exchange(self, temp_db):
        store = TradeStore(db=temp_db.DatabaseManager())
        store.record_trade(_closed_trade(pnl=3.0, exchange="pacifica"))
        store.record_trade(_closed_trade(pnl=-1.0, exchange="blofin"))
        agg = store.aggregate(group_by=("exchange",))
        assert agg["pacifica"]["total_pnl"] == pytest.approx(3.0)
        assert agg["blofin"]["total_pnl"] == pytest.approx(-1.0)

    def test_aggregate_rejects_bad_key(self, temp_db):
        store = TradeStore(db=temp_db.DatabaseManager())
        with pytest.raises(ValueError):
            store.aggregate(group_by=("side",))


# ============================================================
# 4. Consumers produce identical numbers pre/post migration
# ============================================================


class TestConsumerParity:
    def test_kelly_stats_match_hand_computed(self, temp_db):
        from trading_bot_v2.config import StrategyType
        from trading_bot_v2.kelly_position_sizer import KellyPositionSizer

        dbm = temp_db.DatabaseManager()
        strategy = StrategyType.MEAN_REVERSION
        # 10 oldest trades (outside the 50-trade window): pnl +5
        for i in range(10):
            ts = (NOW - timedelta(days=60, minutes=i)).isoformat()
            dbm.save_trade(
                _closed_trade(strategy=strategy.value, pnl=5.0, exit_time=ts)
            )
        # Latest 50 trades: 30 wins of +100, 20 losses of -50
        for i, pnl in enumerate([100.0] * 30 + [-50.0] * 20):
            ts = (NOW - timedelta(minutes=50 - i)).isoformat()
            dbm.save_trade(
                _closed_trade(strategy=strategy.value, pnl=pnl, exit_time=ts)
            )

        sizer = KellyPositionSizer(db=dbm, kelly_fraction=0.5, min_trades=50)
        stats = sizer.get_strategy_stats(strategy)

        # Same numbers the legacy last-50 SQL produced.
        assert stats["total_trades"] == 50
        assert stats["win_rate"] == pytest.approx(0.6)
        assert stats["avg_win"] == pytest.approx(100.0)
        assert stats["avg_loss"] == pytest.approx(50.0)

    def test_adaptive_weights_identical_via_db_and_store(self, temp_db):
        from trading_bot_v2.adaptive_weights import AdaptiveWeightManager
        from trading_bot_v2.market_regime import MarketRegime

        dbm = temp_db.DatabaseManager()
        for _ in range(3):
            dbm.save_trade(_closed_trade(pnl=2.0))  # +2% of 100 notional

        kwargs = dict(
            enabled=True,
            halflife_days=14.0,
            min_trades=1,
            refresh_minutes=60.0,
            scale=2.0,
            min_mult=0.5,
            max_mult=1.5,
        )
        mgr_legacy = AdaptiveWeightManager(db=dbm, **kwargs)
        mgr_store = AdaptiveWeightManager(
            db=dbm, trade_store=TradeStore(db=dbm), **kwargs
        )
        for mgr in (mgr_legacy, mgr_store):
            mgr._now = lambda: NOW
            assert mgr.refresh(force=True) is True

        # +2 pnl-pct expectancy -> score 2.0 -> saturates the 1.5 clamp
        m_legacy = mgr_legacy.get_multiplier(MarketRegime.RANGING_CALM, "MeanReversion")
        m_store = mgr_store.get_multiplier(MarketRegime.RANGING_CALM, "MeanReversion")
        assert m_legacy == pytest.approx(1.5)
        assert m_store == pytest.approx(m_legacy)

    def test_regime_attribution_exchange_filter(self, temp_db):
        from trading_bot_v2.strategy_monitor import StrategyMonitor

        dbm = temp_db.DatabaseManager()
        store = TradeStore(db=dbm)
        # Notional = 100 -> pnl of 10 == 10 pct
        store.record_trade(_closed_trade(pnl=10.0, exchange="pacifica"))
        store.record_trade(_closed_trade(pnl=-5.0, exchange="blofin"))

        with patch("trading_bot_v2.strategy_monitor.get_event_bus") as bus:
            bus.return_value = MagicMock()
            monitor = StrategyMonitor()

        both = monitor.get_regime_attribution()
        assert both["MEAN_REVERSION"]["ranging_calm"]["trade_count"] == 2

        only_pacifica = monitor.get_regime_attribution(exchange="pacifica")
        calm = only_pacifica["MEAN_REVERSION"]["ranging_calm"]
        assert calm["trade_count"] == 1
        assert calm["total_pnl_pct"] == pytest.approx(10.0)


# ============================================================
# 5. Migration idempotency
# ============================================================


class TestExchangeMigration:
    def test_exchange_column_migration_idempotent(self, temp_db):
        # init_database already ran once in the fixture; run twice more
        temp_db.init_database()
        temp_db.init_database()

        with temp_db.get_db_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(trades)")
            columns = [row[1] for row in cursor.fetchall()]
        assert columns.count("exchange") == 1
