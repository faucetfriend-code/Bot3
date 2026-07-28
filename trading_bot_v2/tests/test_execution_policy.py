"""
Execution-policy tests for BacktestEngine._execute_signal.

_execute_signal is the last gate in the signal pipeline and the only one that
discards signals AFTER all eight validity flags have passed. Measured on the
canonical candle store it is the largest attrition stage for several
strategies, so each early return is pinned here:

  * exec:same_direction_skip - POLICY. Anti-pyramiding, and the position state
    it reads is accurate (the exchange deletes closed positions), so it is not
    silently dropping re-entries. Tests below pin both the block AND the
    accuracy of the position state at block time, so nobody "fixes" it later.
  * exec:hedge_mode_block    - POLICY, now configurable under an honest name.
  * exec:min_hold_block      - POLICY, and deliberately unreachable while
    signal-driven closes are off.
  * exec:qty_non_positive    - guard, unchanged.

Plus the bookkeeping fix: a position opened by a resting limit order is now
aged from its FILL rather than from the bar its order was placed on.

No parquet data is required - the engine's execution path is driven directly
against a SimulatedExchange.
"""

import pytest

from trading_bot_v2.backtesting.engine import (
    DEFAULT_MAX_PYRAMID_ENTRIES,
    DEFAULT_MIN_HOLD_CANDLES,
    MAX_PYRAMID_ENTRIES_CEILING,
    BacktestEngine,
    validate_max_pyramid_entries,
    validate_non_negative_candles,
)
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.config import AssetClass, StrategyType
from trading_bot_v2.diagnostics.funnel import (
    REASON_EXEC_HEDGE_MODE,
    REASON_EXEC_MIN_HOLD,
    REASON_EXEC_PYRAMID_SPACING,
    REASON_EXEC_QTY_NON_POSITIVE,
    REASON_EXEC_SAME_DIRECTION,
    STAGE_EXECUTION_BLOCKED,
    SignalFunnel,
)
from trading_bot_v2.models import OrderSide, Signal

SYMBOL = "SUI-USDC"


def make_signal(side=OrderSide.BUY, price=100.0, strategy=None):
    """Build a minimal Signal that _execute_signal will accept."""
    long = side == OrderSide.BUY
    return Signal(
        strategy=strategy or StrategyType.MEAN_REVERSION,
        asset=SYMBOL,
        asset_class=AssetClass.CRYPTO,
        side=side,
        entry_price=price,
        stop_loss=price * (0.98 if long else 1.02),
        take_profit=price * (1.04 if long else 0.96),
        quantity=1.0,
        confidence=0.9,
    )


def build_engine(monkeypatch, **env):
    """Construct an engine with a resolved policy from env overrides."""
    from trading_bot_v2.config import config

    # config.py always defines these two, and cfg wins over env, so clear
    # them to let the env values under test through.
    monkeypatch.delattr(config, "backtest_hedge_mode", raising=False)
    monkeypatch.delattr(config, "backtest_min_hold_candles", raising=False)
    for name in (
        "BACKTEST_HEDGE_MODE",
        "BACKTEST_OPPOSING_CLOSES_POSITION",
        "BACKTEST_MIN_HOLD_CANDLES",
        "BACKTEST_MAX_PYRAMID_ENTRIES",
        "BACKTEST_PYRAMID_MIN_SPACING_CANDLES",
    ):
        monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    engine = BacktestEngine()
    engine._resolve_execution_policy()
    engine._funnel = SignalFunnel(label="test")
    engine._position_open_candle = {}
    engine._position_entry_count = {}
    engine._position_last_entry_candle = {}
    engine._position_time_exit = {}
    return engine


def build_exchange(price=100.0, capital=100000.0):
    exchange = SimulatedExchange(initial_capital=capital)
    exchange._current_price = price
    exchange._current_timestamp = "2024-06-01T00:00:00"
    return exchange


@pytest.fixture
def warning_log():
    """Capture loguru WARNING+ records (caplog does not intercept loguru)."""
    from loguru import logger as loguru_logger

    messages = []
    sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level="WARNING")
    yield messages
    loguru_logger.remove(sink_id)


class TestDefaultsAreUnchanged:
    """The shipped behaviour must survive the knobs being introduced."""

    def test_default_policy_matches_pre_change_engine(self, monkeypatch):
        engine = build_engine(monkeypatch)
        assert engine.execution_policy() == {
            "opposing_closes_position": False,
            "min_hold_candles": DEFAULT_MIN_HOLD_CANDLES,
            "max_pyramid_entries": 1,
            "pyramid_min_spacing_candles": 0,
        }

    def test_hedge_mode_alias_still_enables_signal_closes(self, monkeypatch):
        engine = build_engine(monkeypatch, BACKTEST_HEDGE_MODE="true")
        assert engine._opposing_closes_position is True

    def test_new_name_enables_signal_closes(self, monkeypatch):
        engine = build_engine(
            monkeypatch, BACKTEST_OPPOSING_CLOSES_POSITION="true"
        )
        assert engine._opposing_closes_position is True

    def test_policy_is_attached_to_the_funnel_as_a_note(self, monkeypatch):
        engine = build_engine(monkeypatch)
        engine._funnel.note("execution_policy", engine.execution_policy())
        payload = engine._funnel.to_dict()
        assert payload["notes"]["execution_policy"]["max_pyramid_entries"] == 1


class TestSameDirectionSkipIsCorrectPolicy:
    """POLICY, not a bug - pin it so it does not get "fixed".

    Measured across seven strategy/symbol replays on real candles, 1042 of
    1042 same-direction skips had a genuinely open same-side position. The
    tests below pin the mechanism that makes that true.
    """

    def test_same_direction_signal_is_blocked_by_default(self, monkeypatch):
        engine = build_engine(monkeypatch)
        exchange = build_exchange()

        assert engine._execute_signal(make_signal(), exchange, 0) is True
        engine._sync_position_tracking(exchange, 1)
        assert exchange._positions[SYMBOL].side == "long"

        assert engine._execute_signal(make_signal(), exchange, 1) is False
        assert engine._funnel.reasons[REASON_EXEC_SAME_DIRECTION] == 1
        assert engine._funnel.get(STAGE_EXECUTION_BLOCKED) == 1

    def test_position_state_is_live_not_stale_at_skip_time(self, monkeypatch):
        """The skip reads exchange state, and a closed position is gone.

        This is the whole reason same_direction_skip is policy rather than a
        bug: after a close, the next same-direction signal is NOT skipped.
        """
        engine = build_engine(monkeypatch)
        exchange = build_exchange()
        engine._execute_signal(make_signal(), exchange, 0)
        engine._sync_position_tracking(exchange, 1)
        assert SYMBOL in exchange._positions

        # Close the position the way SL/TP does (opposing fill for full size).
        exchange.place_order(
            symbol=SYMBOL, side="ask", quantity="1.0", order_type="market"
        )
        assert SYMBOL not in exchange._positions
        engine._sync_position_tracking(exchange, 2)
        assert SYMBOL not in engine._position_open_candle

        # A genuine re-entry now goes through - nothing was silently dropped.
        assert engine._execute_signal(make_signal(), exchange, 2) is True
        assert REASON_EXEC_SAME_DIRECTION not in engine._funnel.reasons

    def test_pyramiding_allows_configured_number_of_adds(self, monkeypatch):
        engine = build_engine(monkeypatch, BACKTEST_MAX_PYRAMID_ENTRIES=3)
        exchange = build_exchange()

        for idx in range(3):
            assert engine._execute_signal(make_signal(), exchange, idx) is True
            engine._sync_position_tracking(exchange, idx + 1)
        assert engine._position_entry_count[SYMBOL] == 3
        assert exchange._positions[SYMBOL].quantity == pytest.approx(3.0)

        # The fourth is blocked by the cap.
        assert engine._execute_signal(make_signal(), exchange, 3) is False
        assert engine._funnel.reasons[REASON_EXEC_SAME_DIRECTION] == 1

    def test_pyramid_add_replaces_exits_instead_of_stacking(self, monkeypatch):
        """Stacked exits total more than the position and flip it on trigger."""
        engine = build_engine(monkeypatch, BACKTEST_MAX_PYRAMID_ENTRIES=2)
        exchange = build_exchange()
        engine._execute_signal(make_signal(), exchange, 0)
        engine._sync_position_tracking(exchange, 1)
        engine._execute_signal(make_signal(), exchange, 1)

        open_exits = [
            o
            for o in exchange._orders.values()
            if o.status == "open" and o.side == "ask"
        ]
        assert open_exits, "expected the position to still carry exit orders"
        total_exit_qty = sum(o.quantity for o in open_exits)
        # One SL + one TP, each sized to the whole position - never 2x stacked
        # sets that would over-close.
        assert total_exit_qty == pytest.approx(
            2 * exchange._positions[SYMBOL].quantity
        )

    def test_pyramid_spacing_blocks_back_to_back_adds(self, monkeypatch):
        engine = build_engine(
            monkeypatch,
            BACKTEST_MAX_PYRAMID_ENTRIES=3,
            BACKTEST_PYRAMID_MIN_SPACING_CANDLES=10,
        )
        exchange = build_exchange()
        assert engine._execute_signal(make_signal(), exchange, 0) is True
        engine._sync_position_tracking(exchange, 1)

        assert engine._execute_signal(make_signal(), exchange, 1) is False
        assert engine._funnel.reasons[REASON_EXEC_PYRAMID_SPACING] == 1

        assert engine._execute_signal(make_signal(), exchange, 10) is True


class TestOpposingSignalPolicy:
    def test_opposing_signal_is_dropped_by_default(self, monkeypatch):
        engine = build_engine(monkeypatch)
        exchange = build_exchange()
        engine._execute_signal(make_signal(OrderSide.BUY), exchange, 0)
        engine._sync_position_tracking(exchange, 1)

        assert (
            engine._execute_signal(make_signal(OrderSide.SELL), exchange, 50)
            is False
        )
        assert engine._funnel.reasons[REASON_EXEC_HEDGE_MODE] == 1
        assert exchange._positions[SYMBOL].side == "long"

    def test_opposing_signal_closes_position_when_enabled(self, monkeypatch):
        engine = build_engine(
            monkeypatch,
            BACKTEST_OPPOSING_CLOSES_POSITION="true",
            BACKTEST_MIN_HOLD_CANDLES=6,
        )
        exchange = build_exchange()
        engine._execute_signal(make_signal(OrderSide.BUY), exchange, 0)
        engine._sync_position_tracking(exchange, 1)

        assert (
            engine._execute_signal(make_signal(OrderSide.SELL), exchange, 50)
            is True
        )
        assert SYMBOL not in exchange._positions

    def test_min_hold_blocks_an_early_signal_driven_close(self, monkeypatch):
        engine = build_engine(
            monkeypatch,
            BACKTEST_OPPOSING_CLOSES_POSITION="true",
            BACKTEST_MIN_HOLD_CANDLES=6,
        )
        exchange = build_exchange()
        engine._execute_signal(make_signal(OrderSide.BUY), exchange, 0)
        engine._sync_position_tracking(exchange, 1)

        assert (
            engine._execute_signal(make_signal(OrderSide.SELL), exchange, 3)
            is False
        )
        assert engine._funnel.reasons[REASON_EXEC_MIN_HOLD] == 1
        assert SYMBOL in exchange._positions

    def test_min_hold_is_silent_while_closes_are_disabled(self, monkeypatch):
        """Not dead code - unreachable because its parent policy is off.

        Pinned so the reachability of exec:min_hold_block always follows
        opposing_closes_position and never drifts.
        """
        engine = build_engine(monkeypatch, BACKTEST_MIN_HOLD_CANDLES=6)
        exchange = build_exchange()
        engine._execute_signal(make_signal(OrderSide.BUY), exchange, 0)
        engine._sync_position_tracking(exchange, 1)

        engine._execute_signal(make_signal(OrderSide.SELL), exchange, 1)
        assert REASON_EXEC_MIN_HOLD not in engine._funnel.reasons
        assert engine._funnel.reasons[REASON_EXEC_HEDGE_MODE] == 1


class TestPositionAgeing:
    def test_limit_entry_is_aged_from_its_fill_not_its_order(self, monkeypatch):
        """A resting limit entry used to start its min-hold clock at order
        placement, so a grid position could satisfy min-hold before it even
        existed. It is now stamped on the bar the fill is observed."""
        engine = build_engine(
            monkeypatch,
            BACKTEST_OPPOSING_CLOSES_POSITION="true",
            BACKTEST_MIN_HOLD_CANDLES=6,
        )
        exchange = build_exchange(price=100.0)
        # entry_price 2% below market -> a resting bid limit, not a market fill
        signal = make_signal(OrderSide.BUY, price=98.0)
        assert engine._execute_signal(signal, exchange, 0) is True
        assert SYMBOL not in exchange._positions
        assert SYMBOL not in engine._position_open_candle

        # Price trades down to the limit 20 bars later; the position opens now.
        exchange.advance(
            {"open": 99.0, "high": 99.0, "low": 97.0, "close": 98.0, "volume": 1.0},
            "2024-06-01T01:40:00",
        )
        engine._sync_position_tracking(exchange, 20)
        assert exchange._positions[SYMBOL].side == "long"
        assert engine._position_open_candle[SYMBOL] == 20

        # Bar 23 is 3 candles after the FILL, so min-hold still blocks.
        assert (
            engine._execute_signal(make_signal(OrderSide.SELL), exchange, 23)
            is False
        )
        assert engine._funnel.reasons[REASON_EXEC_MIN_HOLD] == 1

    def test_sync_clears_tracking_when_sl_tp_closes_the_position(
        self, monkeypatch
    ):
        """Only the signal-driven close path used to pop these, so a
        SL/TP-closed position left stale bookkeeping behind forever."""
        engine = build_engine(monkeypatch)
        exchange = build_exchange()
        engine._execute_signal(make_signal(), exchange, 0)
        engine._sync_position_tracking(exchange, 1)
        assert engine._position_open_candle[SYMBOL] == 0

        exchange.place_order(
            symbol=SYMBOL, side="ask", quantity="1.0", order_type="market"
        )
        engine._sync_position_tracking(exchange, 2)
        assert engine._position_open_candle == {}
        assert engine._position_entry_count == {}
        assert engine._position_last_entry_candle == {}


class TestQuantityGuard:
    def test_non_positive_quantity_is_blocked(self, monkeypatch):
        engine = build_engine(monkeypatch)
        exchange = build_exchange(capital=0.0)
        signal = make_signal()
        signal.quantity = 0.0
        assert engine._execute_signal(signal, exchange, 0) is False
        assert engine._funnel.reasons[REASON_EXEC_QTY_NON_POSITIVE] == 1


class TestPolicyValidation:
    """Nonsense config warns and falls back - never raises, never silently
    disables the engine (house style, see vwap_scalping.validate_*)."""

    @pytest.mark.parametrize("bad", [0, -1, MAX_PYRAMID_ENTRIES_CEILING + 1])
    def test_out_of_range_pyramid_entries_fall_back(self, bad, warning_log):
        assert validate_max_pyramid_entries(bad) == DEFAULT_MAX_PYRAMID_ENTRIES
        assert any("BACKTEST_MAX_PYRAMID_ENTRIES" in m for m in warning_log)

    def test_non_numeric_pyramid_entries_falls_back(self, warning_log):
        assert (
            validate_max_pyramid_entries("lots") == DEFAULT_MAX_PYRAMID_ENTRIES
        )
        assert any("not an integer" in m for m in warning_log)

    def test_in_range_pyramid_entries_pass_through(self):
        assert validate_max_pyramid_entries("4") == 4
        assert (
            validate_max_pyramid_entries(MAX_PYRAMID_ENTRIES_CEILING)
            == MAX_PYRAMID_ENTRIES_CEILING
        )

    def test_negative_candle_counts_fall_back(self, warning_log):
        assert validate_non_negative_candles(-3, "BACKTEST_MIN_HOLD_CANDLES", 6) == 6
        assert any("negative" in m for m in warning_log)

    def test_zero_candles_is_allowed(self):
        assert validate_non_negative_candles(0, "BACKTEST_MIN_HOLD_CANDLES", 6) == 0

    def test_bad_env_value_does_not_disable_the_engine(self, monkeypatch):
        engine = build_engine(monkeypatch, BACKTEST_MAX_PYRAMID_ENTRIES="banana")
        assert engine._max_pyramid_entries == DEFAULT_MAX_PYRAMID_ENTRIES


class TestExecutionLayerIsLiveOnly:
    def test_backtest_engine_does_not_use_execution_layer(self):
        """The exec:* funnel reasons come only from the backtest engine."""
        import inspect

        from trading_bot_v2.backtesting import engine as engine_mod

        source = inspect.getsource(engine_mod)
        assert "ExecutionLayer" not in source
        assert "execution_layer" not in source

    def test_execution_layer_no_hard_block(self):
        """refine_entry never returns None.

        trading_bot.py treats None as "skip this entry", and the backtest has
        no equivalent gate, so a None return would silently diverge live from
        every backtest. If this test is changed, the engine needs a matching
        gate first.
        """
        import inspect

        from trading_bot_v2.execution_layer import ExecutionLayer

        source = inspect.getsource(ExecutionLayer.refine_entry)
        assert "return None" not in source
