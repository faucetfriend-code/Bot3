"""Flag-gated Kelly size cap and signal-pipeline gate on standard entries.

Covers ``entry_gates`` and its two call sites in ``TradingBot``
(``_coordinate_signal_execution`` for sizing,
``_execute_standard_signal_coordinated`` for the pipeline gate), plus the
strict ``KellyPositionSizer.evaluate`` /
``IntegratedPositionSizer.calculate_kelly_bounded_size`` they rely on and
the Phase 1 regime lookup in ``signal_phases``.

Every "today's size" below is produced by running the same signal through
a bot with both flags off (the untouched path) or by calling the real
``RiskManager`` directly; none is a hardcoded guess.  Signals are real
``models.Signal`` objects and the Kelly sizer is the real one, reading
closed trades from a temporary SQLite database through the real
``TradeStore``.  The exchange, signal logger and event bus are the in-memory
fakes from ``order_risk_fakes``; the bot is the duck-typed stand-in from
``make_bot`` with the production methods bound to it.
"""

import os
import sqlite3
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import create_autospec, patch

import pytest
from loguru import logger

from order_risk_fakes import FakeExchange, make_bot, make_signal, position, raiser
from trading_bot_v2.config import Config, StrategyType
from trading_bot_v2.confidence_sizer import ConfidenceSizer, IntegratedPositionSizer
from trading_bot_v2.entry_gates import (
    SIZING_MODE_KELLY_CAPPED,
    SIZING_MODE_RISK_MANAGER,
    kelly_capped_quantity,
    kelly_sizer_from_config,
    pipeline_block_reason,
    signal_pipeline_from_config,
    sizing_mode,
)
from trading_bot_v2.history import TradeStore
from trading_bot_v2.kelly_position_sizer import (
    KELLY_INSUFFICIENT_HISTORY,
    KELLY_INVALID_INPUT,
    KELLY_INVALID_STOP,
    KELLY_NON_POSITIVE,
    KELLY_OK,
    KellyPositionSizer,
)
from trading_bot_v2.market_regime import MarketRegime, MarketRegimeDetector
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.risk_manager import RiskManager
from trading_bot_v2.signal_phases import (
    Phase1RegimePermission,
    Phase3ExecutionFilter,
    PhaseResult,
    SignalPipeline,
)

BALANCE = 10_000.0

# 40 wins of 100 and 10 losses of 50: raw Kelly 0.70, half-Kelly 0.35,
# capped at 10% of balance at risk.
HEALTHY = [100.0] * 40 + [-50.0] * 10
# 10 wins of 50 and 40 losses of 100: raw Kelly (0.2*50 - 0.8*100)/50 = -1.4
NEGATIVE_EDGE = [50.0] * 10 + [-100.0] * 40
# 25 wins of 101 and 25 losses of 100: raw Kelly 0.5/101 = 0.00495
THIN_EDGE = [101.0] * 25 + [-100.0] * 25

KELLY_ON = SimpleNamespace(enable_kelly_sizing=True)
PIPELINE_ON = SimpleNamespace(enable_signal_pipeline=True)


# ======================================================================
# Fixtures and helpers
# ======================================================================


@pytest.fixture
def temp_db() -> Iterator[Any]:
    """Point trading_bot_v2.database at a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    original_env = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = tmp_path
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
    else:
        os.environ.pop("DATABASE_PATH", None)
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


@pytest.fixture
def error_logs() -> Iterator[List[str]]:
    """Collect loguru records at ERROR and above."""
    messages: List[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="ERROR")
    yield messages
    logger.remove(sink_id)


def seed_trades(
    db_mod: Any,
    pnls: List[float],
    strategy: StrategyType = StrategyType.MEAN_REVERSION,
) -> TradeStore:
    """Insert closed trades tagged the way KellyPositionSizer queries them."""
    dbm = db_mod.DatabaseManager()
    store = TradeStore(db=dbm)
    for index, pnl in enumerate(pnls):
        stamp = f"2026-07-01T00:{index:02d}:00"
        store.record_trade(
            {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 1.0,
                "entry_price": 100.0,
                "entry_time": stamp,
                "exit_time": stamp,
                "pnl": pnl,
                "strategy": strategy.value,
                "status": "closed",
            }
        )
    return store


def real_sizer(db_mod: Any, pnls: List[float]) -> IntegratedPositionSizer:
    """The sizer the bot builds when ENABLE_KELLY_SIZING is true."""
    store = seed_trades(db_mod, pnls)
    sizer = kelly_sizer_from_config(KELLY_ON, store.db, store)
    assert isinstance(sizer, IntegratedPositionSizer)
    assert isinstance(sizer.kelly, KellyPositionSizer)
    return sizer


def exchange_with(exposure_notional: float = 0.0) -> FakeExchange:
    """Exchange with BALANCE equity and one position of the given notional."""
    positions = []
    if exposure_notional:
        positions.append(
            position(quantity=exposure_notional / 100.0, entry_price=100.0)
        )
    return FakeExchange(balance=BALANCE, positions=positions)


def run_entry(
    signal: Signal,
    exposure_notional: float = 0.0,
    kelly_sizer: Any = None,
    pipeline: Any = None,
    risk_manager: Optional[RiskManager] = None,
    regime: Optional[MarketRegime] = None,
) -> Any:
    """Send one signal through the coordinator and return the bot."""
    bot = make_bot(
        exchange_with(exposure_notional), risk_manager=risk_manager or RiskManager()
    )
    if regime is not None:
        bot.market_regime = SimpleNamespace(get_current_regime=lambda symbol: regime)
    if kelly_sizer is not None:
        bot._kelly_sizer = kelly_sizer
    if pipeline is not None:
        bot._signal_pipeline = pipeline
    bot._coordinate_signal_execution(signal)
    return bot


def todays_quantity(
    signal_kwargs: Dict[str, Any],
    exposure: float = 0.0,
    regime: Optional[MarketRegime] = None,
) -> float:
    """Order quantity the untouched (flags off) path sends for a signal."""
    bot = run_entry(make_signal(**signal_kwargs), exposure, regime=regime)
    assert len(bot.exchange.orders) == 1
    return float(bot.exchange.orders[0]["quantity"])


def sent_quantity(bot: Any) -> float:
    """Quantity of the single order the bot sent."""
    assert len(bot.exchange.orders) == 1
    return float(bot.exchange.orders[0]["quantity"])


# ======================================================================
# Flags and construction
# ======================================================================


class TestFlags:
    @pytest.mark.parametrize("name", ["ENABLE_KELLY_SIZING", "ENABLE_SIGNAL_PIPELINE"])
    def test_default_is_off(self, name: str, monkeypatch: Any) -> None:
        monkeypatch.delenv(name, raising=False)
        assert getattr(Config(), name.lower()) is False

    @pytest.mark.parametrize("name", ["ENABLE_KELLY_SIZING", "ENABLE_SIGNAL_PIPELINE"])
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), ("1", True), ("YES", True), ("false", False), ("", False)],
    )
    def test_parsed_like_the_other_enable_flags(
        self, name: str, raw: str, expected: bool, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv(name, raw)
        assert getattr(Config(), name.lower()) is expected

    def test_off_builds_nothing(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("ENABLE_KELLY_SIZING", raising=False)
        monkeypatch.delenv("ENABLE_SIGNAL_PIPELINE", raising=False)
        cfg = Config()
        with (
            patch("trading_bot_v2.kelly_position_sizer.KellyPositionSizer") as kelly,
            patch("trading_bot_v2.confidence_sizer.IntegratedPositionSizer") as sizer,
            patch("trading_bot_v2.signal_phases.SignalPipeline") as pipeline,
        ):
            assert kelly_sizer_from_config(cfg, None, None) is None
            assert signal_pipeline_from_config(cfg, RiskManager(), None) is None
        assert not kelly.called and not sizer.called and not pipeline.called

    def test_a_truthy_non_bool_does_not_enable(self) -> None:
        cfg = SimpleNamespace(enable_kelly_sizing="false", enable_signal_pipeline=1)
        assert kelly_sizer_from_config(cfg, None, None) is None
        assert signal_pipeline_from_config(cfg, RiskManager(), None) is None

    def test_kelly_build_failure_keeps_risk_manager_sizing(
        self, error_logs: List[str]
    ) -> None:
        with patch(
            "trading_bot_v2.kelly_position_sizer.KellyPositionSizer",
            side_effect=RuntimeError("no db"),
        ):
            assert kelly_sizer_from_config(KELLY_ON, None, None) is None
        assert any("could not be built" in m for m in error_logs)

    def test_pipeline_build_failure_is_not_swallowed(self) -> None:
        with patch(
            "trading_bot_v2.signal_phases.Phase3ExecutionFilter",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                signal_pipeline_from_config(PIPELINE_ON, RiskManager(), None)

    def test_sizing_mode_reports_the_active_path(self, temp_db: Any) -> None:
        bot = make_bot(exchange_with())
        assert sizing_mode(bot) == SIZING_MODE_RISK_MANAGER
        bot._kelly_sizer = None
        assert sizing_mode(bot) == SIZING_MODE_RISK_MANAGER
        bot._kelly_sizer = real_sizer(temp_db, [])
        assert sizing_mode(bot) == SIZING_MODE_KELLY_CAPPED


# ======================================================================
# Flags off: identical to the untouched path
# ======================================================================


def traced_run(signal: Signal, exposure: float, **flags: Any) -> Dict[str, Any]:
    """Run an entry recording the order of every sizing / gating call."""
    calls: List[str] = []
    rm = RiskManager()
    bot = make_bot(exchange_with(exposure), risk_manager=rm)
    for key, value in flags.items():
        setattr(bot, key, value)

    def spy(owner: Any, name: str) -> None:
        original = getattr(owner, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return original(*args, **kwargs)

        setattr(owner, name, wrapper)

    spy(rm, "get_position_size")
    spy(rm, "request_capital_allocation")
    spy(bot.execution_layer, "refine_entry")
    spy(bot.exchange, "place_order")
    bot._coordinate_signal_execution(signal)
    order = dict(bot.exchange.orders[0]) if bot.exchange.orders else None
    if order is not None:
        order.pop("client_order_id")
    return {"calls": calls, "order": order, "bot": bot}


class TestFlagsOffIsUnchanged:
    SEQUENCE = [
        "get_position_size",
        "request_capital_allocation",
        "refine_entry",
        "place_order",
    ]

    @pytest.mark.parametrize("exposure", [0.0, 1_000.0])
    @pytest.mark.parametrize(
        "strategy", [StrategyType.MEAN_REVERSION, StrategyType.MA_CROSSOVER]
    )
    def test_quantity_and_check_sequence_match_the_risk_manager_path(
        self, exposure: float, strategy: StrategyType
    ) -> None:
        # Expected value from the untouched RiskManager functions themselves.
        rm = RiskManager()
        reference = make_signal(strategy=strategy)
        size = rm.get_position_size(
            signal=reference,
            account_balance=BALANCE,
            current_exposure=exposure,
            regime=None,
        )
        allocation = rm.request_capital_allocation(
            symbol="BTC",
            requested_amount=size * reference.entry_price,
            strategy=strategy.name,
            account_balance=BALANCE,
            current_exposure=exposure,
        )
        expected = allocation["allocated_amount"] / reference.entry_price

        with (
            patch.object(IntegratedPositionSizer, "calculate_position_size") as legacy,
            patch.object(
                IntegratedPositionSizer, "calculate_kelly_bounded_size"
            ) as strict,
            patch.object(KellyPositionSizer, "evaluate") as evaluate,
            patch.object(SignalPipeline, "review_entry") as review,
            patch.object(Phase1RegimePermission, "check") as phase1,
            patch.object(Phase3ExecutionFilter, "check") as phase3,
        ):
            absent = traced_run(make_signal(strategy=strategy), exposure)
            explicit_none = traced_run(
                make_signal(strategy=strategy),
                exposure,
                _kelly_sizer=None,
                _signal_pipeline=None,
            )
        for spy in (legacy, strict, evaluate, review, phase1, phase3):
            assert not spy.called

        for run in (absent, explicit_none):
            assert run["calls"] == self.SEQUENCE
            assert run["order"]["quantity"] == pytest.approx(expected)
            assert run["bot"].signal_logger.rejected == []
        assert absent["order"] == explicit_none["order"]

    def test_grid_signals_never_consult_the_kelly_sizer(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        seen: List[Dict[str, Any]] = []
        results = []
        for kelly_sizer in (None, sizer):
            bot = make_bot(exchange_with(), risk_manager=RiskManager())
            bot._kelly_sizer = kelly_sizer
            bot._execute_grid_signal_coordinated = (
                lambda s, a, log_entry=None: seen.append(a)
            )
            with patch.object(
                IntegratedPositionSizer, "calculate_kelly_bounded_size"
            ) as strict:
                bot._coordinate_signal_execution(
                    make_signal(strategy=StrategyType.GRID_TRADING)
                )
            assert not strict.called
            results.append(seen[-1]["allocated_amount"])
        assert results[0] == results[1]


# ======================================================================
# Kelly on
# ======================================================================


class TestKellyOn:
    def test_fewer_than_fifty_trades_keeps_todays_size(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, HEALTHY[:49])
        bot = run_entry(make_signal(), kelly_sizer=sizer)
        assert sent_quantity(bot) == todays_quantity({})

    def test_empty_history_keeps_todays_size(self, temp_db: Any) -> None:
        bot = run_entry(make_signal(), kelly_sizer=real_sizer(temp_db, []))
        assert sent_quantity(bot) == todays_quantity({})

    def test_history_of_another_strategy_does_not_count(self, temp_db: Any) -> None:
        store = seed_trades(temp_db, HEALTHY, strategy=StrategyType.MA_CROSSOVER)
        sizer = kelly_sizer_from_config(KELLY_ON, store.db, store)
        bot = run_entry(make_signal(), kelly_sizer=sizer)  # MEAN_REVERSION
        assert sent_quantity(bot) == todays_quantity({})

    def test_negative_edge_keeps_todays_size(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, NEGATIVE_EDGE)
        bot = run_entry(make_signal(), kelly_sizer=sizer)
        # Not Kelly's own 1% substitute and not a fixed 2%.
        assert sent_quantity(bot) == todays_quantity({})

    def test_healthy_history_is_bounded_by_the_ten_percent_cap(
        self, temp_db: Any
    ) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        today = todays_quantity({})
        bot = run_entry(make_signal(), kelly_sizer=sizer)
        # Kelly 200 contracts * confidence 0.7 = 140 -> 10% cap = 10 contracts
        assert sent_quantity(bot) == pytest.approx(10.0)
        assert sent_quantity(bot) <= today
        assert sent_quantity(bot) * 100.0 <= BALANCE * 0.10 + 1e-9

    def test_todays_size_wins_when_it_is_smaller(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        today = todays_quantity({}, exposure=1_000.0)  # 500 of room left
        bot = run_entry(make_signal(), exposure_notional=1_000.0, kelly_sizer=sizer)
        assert today < 10.0
        assert sent_quantity(bot) == today

    def test_todays_size_wins_when_the_regime_multiplier_shrinks_it(
        self, temp_db: Any
    ) -> None:
        # INDECISIVE scales today's 15 contracts by 0.4 -> 6, with 1_500 of
        # exposure room still free, so no later cap hides a Kelly size of 10:
        # only the min rule keeps the order at 6.
        sizer = real_sizer(temp_db, HEALTHY)
        regime = MarketRegime.INDECISIVE
        today = todays_quantity({}, regime=regime)
        assert today == pytest.approx(6.0)
        bot = run_entry(make_signal(), kelly_sizer=sizer, regime=regime)
        assert sent_quantity(bot) == today

    @pytest.mark.parametrize(("baseline", "expected"), [(6.0, 6.0), (15.0, 10.0)])
    def test_min_rule_in_isolation(
        self, temp_db: Any, baseline: float, expected: float
    ) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        out = kelly_capped_quantity(sizer, make_signal(), baseline, BALANCE)
        assert out == pytest.approx(expected)

    def test_thin_edge_sizes_below_both_caps(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, THIN_EDGE)
        today = todays_quantity({})
        bot = run_entry(make_signal(), kelly_sizer=sizer)
        # 0.5/101 raw * 0.5 = 0.002475 of 10_000 = 24.75 at risk / 5% stop
        # = 495 notional = 4.95 contracts * confidence 0.7
        assert sent_quantity(bot) == pytest.approx(4.95049505 * 0.7)
        assert sent_quantity(bot) < today
        assert sent_quantity(bot) * 100.0 < BALANCE * 0.10

    @pytest.mark.parametrize("pnls", [HEALTHY, THIN_EDGE, NEGATIVE_EDGE, HEALTHY[:10]])
    @pytest.mark.parametrize("exposure", [0.0, 1_400.0])
    @pytest.mark.parametrize("regime", [None, MarketRegime.INDECISIVE])
    @pytest.mark.parametrize("confidence", [0.0, 0.3, 0.7, 1.0])
    @pytest.mark.parametrize("stop", [99.5, 95.0, 60.0])
    def test_min_rule_never_exceeds_today_or_the_cap(
        self,
        pnls: List[float],
        exposure: float,
        regime: Optional[MarketRegime],
        confidence: float,
        stop: float,
    ) -> None:
        # Real Kelly sizer over canned PnLs (the database-backed cases are
        # above; this matrix only varies the arithmetic).
        sizer = IntegratedPositionSizer(strict_kelly(pnls), ConfidenceSizer())
        kwargs = {"confidence": confidence, "stop_loss": stop}
        today = todays_quantity(kwargs, exposure, regime)
        bot = run_entry(
            make_signal(**kwargs), exposure, kelly_sizer=sizer, regime=regime
        )
        sent = sent_quantity(bot)
        assert 0 < sent <= today
        assert sent * 100.0 <= BALANCE * 0.10 + 1e-9 or sent == today

    def test_missing_stop_keeps_todays_size(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        bot = run_entry(make_signal(stop_loss=None), kelly_sizer=sizer)
        assert sent_quantity(bot) == todays_quantity({"stop_loss": None})

    def test_history_read_failure_keeps_todays_size_and_logs_an_error(
        self, error_logs: List[str]
    ) -> None:
        store = SimpleNamespace(
            get_closed_trades=raiser(sqlite3.OperationalError("database is locked"))
        )
        sizer = kelly_sizer_from_config(KELLY_ON, SimpleNamespace(), store)
        bot = run_entry(make_signal(), kelly_sizer=sizer)
        assert sent_quantity(bot) == todays_quantity({})
        assert any("database is locked" in m for m in error_logs)

    def test_error_inside_the_sizer_keeps_todays_size_and_logs_an_error(
        self, temp_db: Any, error_logs: List[str]
    ) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        with patch.object(
            KellyPositionSizer, "evaluate", side_effect=TypeError("bad call")
        ):
            bot = run_entry(make_signal(), kelly_sizer=sizer)
        assert sent_quantity(bot) == todays_quantity({})
        assert bot.signal_logger.failed == []
        matching = [m for m in error_logs if "sizer raised TypeError: bad call" in m]
        assert len(matching) == 1

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -3.0])
    def test_unusable_kelly_size_keeps_todays_size(
        self, temp_db: Any, bad: float
    ) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        sizer.max_position_pct = float("inf")
        with patch.object(ConfidenceSizer, "apply_to_size") as apply_to_size:
            apply_to_size.return_value = SimpleNamespace(
                effective_size=bad, multiplier=1.0, kelly_status="ok", reason=""
            )
            assert kelly_capped_quantity(sizer, make_signal(), 15.0, BALANCE) == 15.0

    def test_zero_baseline_is_not_sized_up(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        assert kelly_capped_quantity(sizer, make_signal(), 0.0, BALANCE) == 0.0
        # at the exposure cap today's path sends nothing; neither does Kelly
        bot = run_entry(make_signal(), exposure_notional=1_500.0, kelly_sizer=sizer)
        assert bot.exchange.orders == []
        reason = bot.signal_logger.rejected[0]["reason"]
        assert reason == "Capital allocation denied: invalid_request_amount"

    def test_signal_is_not_modified_by_kelly(self, temp_db: Any) -> None:
        sizer = real_sizer(temp_db, HEALTHY)
        off = traced_run(make_signal(), 0.0)
        on = traced_run(make_signal(), 0.0, _kelly_sizer=sizer)
        assert on["calls"] == off["calls"]
        differing = {k for k in off["order"] if off["order"][k] != on["order"][k]}
        assert differing == {"quantity"}

    def test_circuit_breaker_still_blocks(self, temp_db: Any) -> None:
        bot = make_bot(exchange_with(), risk_manager=RiskManager())
        bot._kelly_sizer = real_sizer(temp_db, HEALTHY)
        bot._circuit_breaker_triggered = True
        bot._coordinate_signal_execution(make_signal())
        assert bot.exchange.orders == []
        assert "Circuit breaker" in bot.signal_logger.rejected[0]["reason"]


# ======================================================================
# Strict Kelly evaluation
# ======================================================================


class _Store:
    """TradeStore stand-in returning canned PnLs."""

    def __init__(self, pnls: List[float]) -> None:
        self.pnls = pnls

    def get_closed_trades(self, **kw: Any) -> List[Dict[str, float]]:
        return [{"pnl": p} for p in self.pnls]


def strict_kelly(pnls: List[float]) -> KellyPositionSizer:
    """KellyPositionSizer over canned PnLs."""
    return KellyPositionSizer(db=SimpleNamespace(), trade_store=_Store(pnls))


class TestKellyEvaluate:
    def test_ok_has_no_one_contract_floor(self) -> None:
        # 10% of 100 = 10 at risk / 5% stop = 200 notional / 50_000 = 0.004
        out = strict_kelly(HEALTHY).evaluate(
            make_signal(entry_price=50_000.0, stop_loss=47_500.0), 100.0
        )
        assert out.status == KELLY_OK
        assert out.quantity == pytest.approx(0.004)
        assert out.raw_kelly_pct == pytest.approx(0.70)
        assert out.applied_pct == pytest.approx(0.10)

    def test_insufficient_history(self) -> None:
        out = strict_kelly(HEALTHY[:49]).evaluate(make_signal(), BALANCE)
        assert (out.status, out.quantity, out.total_trades) == (
            KELLY_INSUFFICIENT_HISTORY,
            0.0,
            49,
        )

    @pytest.mark.parametrize(
        "pnls",
        [NEGATIVE_EDGE, [-10.0] * 50, [0.0] * 50, [10.0] * 25 + [-10.0] * 25],
    )
    def test_non_positive_kelly(self, pnls: List[float]) -> None:
        out = strict_kelly(pnls).evaluate(make_signal(), BALANCE)
        assert out.status == KELLY_NON_POSITIVE and out.quantity == 0.0

    def test_all_winners_is_capped_at_ten_percent_risk(self) -> None:
        out = strict_kelly([5.0] * 50).evaluate(make_signal(), BALANCE)
        assert out.status == KELLY_OK and out.applied_pct == pytest.approx(0.10)

    @pytest.mark.parametrize("stop", [None, 100.0])
    def test_missing_or_zero_distance_stop(self, stop: Optional[float]) -> None:
        out = strict_kelly(HEALTHY).evaluate(make_signal(stop_loss=stop), BALANCE)
        assert out.status == KELLY_INVALID_STOP and out.quantity == 0.0

    @pytest.mark.parametrize(("entry", "balance"), [(0.0, BALANCE), (100.0, 0.0)])
    def test_invalid_input(self, entry: float, balance: float) -> None:
        out = strict_kelly(HEALTHY).evaluate(make_signal(entry_price=entry), balance)
        assert out.status == KELLY_INVALID_INPUT and out.quantity == 0.0

    def test_bounded_size_reports_the_kelly_status(self) -> None:
        sizer = IntegratedPositionSizer(strict_kelly([]), ConfidenceSizer())
        out = sizer.calculate_kelly_bounded_size(make_signal(), BALANCE)
        assert out.kelly_status == KELLY_INSUFFICIENT_HISTORY
        assert out.effective_size == 0.0  # no 0.001 floor on this path


# ======================================================================
# Pipeline on
# ======================================================================


def live_pipeline(rm: RiskManager, **config: Any) -> SignalPipeline:
    """The pipeline the bot builds when ENABLE_SIGNAL_PIPELINE is true."""
    cfg = SimpleNamespace(enable_signal_pipeline=True, **config)
    pipeline = signal_pipeline_from_config(cfg, rm, None)
    assert isinstance(pipeline, SignalPipeline)
    return pipeline


class TestPipelineOn:
    def test_permitted_signal_proceeds_unchanged(self) -> None:
        rm = RiskManager()
        off = traced_run(make_signal(), 0.0)
        signal = make_signal()
        before = (signal.side, signal.entry_price, signal.stop_loss, signal.asset)
        bot = make_bot(exchange_with(), risk_manager=rm)
        bot._signal_pipeline = live_pipeline(rm)
        bot._coordinate_signal_execution(signal)
        order = dict(bot.exchange.orders[0])
        order.pop("client_order_id")
        assert order == off["order"]
        assert (signal.side, signal.entry_price, signal.stop_loss, signal.asset) == (
            before
        )
        assert bot.signal_logger.rejected == []

    def test_phase3_symbol_exposure_rejection_sends_no_order(self) -> None:
        rm = RiskManager()
        # Grid exposure is not part of the bot's position-based exposure, so
        # today's path would send 15 contracts; Phase 3 counts it:
        # (2_000 + 1_500) / 10_000 = 35% > 25% per symbol.
        rm.grid_exposure["BTC"] = 2_000.0
        assert todays_quantity({}) == pytest.approx(15.0)
        bot = run_entry(make_signal(), pipeline=live_pipeline(rm), risk_manager=rm)
        assert bot.exchange.orders == []
        reason = bot.signal_logger.rejected[0]["reason"]
        assert reason.startswith("Signal pipeline: Exposure 35.0%")

    def test_phase3_minimum_size_rejection_sends_no_order(self) -> None:
        rm = RiskManager()
        pipeline = live_pipeline(rm, min_position_size=20.0)
        bot = run_entry(make_signal(), pipeline=pipeline, risk_manager=rm)
        assert bot.exchange.orders == []
        assert "below minimum" in bot.signal_logger.rejected[0]["reason"]

    def test_phase1_rejection_sends_no_order(self) -> None:
        rm = RiskManager()
        pipeline = live_pipeline(rm, enable_mean_reversion=False)
        bot = run_entry(make_signal(), pipeline=pipeline, risk_manager=rm)
        assert bot.exchange.orders == []
        reason = bot.signal_logger.rejected[0]["reason"]
        assert reason == "Signal pipeline: Strategy mean_reversion disabled in config"

    def test_live_wiring_does_not_enforce_the_phase1_regime_map(self) -> None:
        rm = RiskManager()
        pipeline = live_pipeline(rm)
        assert pipeline.phase1.enforce_regime is False
        # MOMENTUM_SCALPING is in no Phase 1 regime list; the live strategy
        # manager is what decides whether it may trade.
        signal = make_signal(strategy=StrategyType.MOMENTUM_SCALPING)
        assert pipeline_block_reason(pipeline, signal, 5.0, BALANCE) is None

    def test_unknown_balance_rejects(self) -> None:
        rm = RiskManager()
        pipeline = live_pipeline(rm)
        reason = pipeline_block_reason(pipeline, make_signal(), 5.0, 0.0)
        assert reason is not None and "Exposure" in reason

    def test_circuit_breaker_still_blocks_when_the_pipeline_would_pass(self) -> None:
        rm = RiskManager()
        pipeline = live_pipeline(rm)
        assert pipeline_block_reason(pipeline, make_signal(), 15.0, BALANCE) is None
        bot = make_bot(exchange_with(), risk_manager=rm)
        bot._signal_pipeline = pipeline
        bot._circuit_breaker_triggered = True
        with patch.object(SignalPipeline, "review_entry") as review:
            bot._coordinate_signal_execution(make_signal())
        assert bot.exchange.orders == []
        assert "Circuit breaker" in bot.signal_logger.rejected[0]["reason"]
        assert not review.called

    def test_exposure_cap_still_blocks_when_the_pipeline_would_pass(self) -> None:
        rm = RiskManager()
        bot = run_entry(
            make_signal(),
            exposure_notional=1_500.0,  # at the 15% portfolio cap
            pipeline=live_pipeline(rm),
            risk_manager=rm,
        )
        assert bot.exchange.orders == []
        reason = bot.signal_logger.rejected[0]["reason"]
        assert reason == "Capital allocation denied: invalid_request_amount"

    def test_pipeline_error_sends_no_order(self) -> None:
        rm = RiskManager()
        pipeline = live_pipeline(rm)
        with patch.object(
            SignalPipeline, "review_entry", side_effect=TypeError("bad call")
        ):
            bot = run_entry(make_signal(), pipeline=pipeline, risk_manager=rm)
        assert bot.exchange.orders == []
        assert bot.signal_logger.failed[0]["error"] == "TypeError: bad call"

    def test_both_flags_together(self, temp_db: Any) -> None:
        rm = RiskManager()
        bot = run_entry(
            make_signal(),
            kelly_sizer=real_sizer(temp_db, HEALTHY),
            pipeline=live_pipeline(rm),
            risk_manager=rm,
        )
        assert sent_quantity(bot) == pytest.approx(10.0)


# ======================================================================
# signal_phases against real collaborators
# ======================================================================


def detector(regime: Optional[MarketRegime]) -> Any:
    """Autospecced real detector: only methods that exist can be called."""
    spec = create_autospec(MarketRegimeDetector, instance=True)
    spec.get_current_regime.return_value = regime
    return spec


class TestPhase1RegimeLookup:
    def _phase1(self, regime: Optional[MarketRegime]) -> Phase1RegimePermission:
        return Phase1RegimePermission(detector(regime), RiskManager(), Config())

    def test_reads_the_confirmed_regime_for_the_symbol(self) -> None:
        phase1 = self._phase1(MarketRegime.RANGING_CALM)
        out = phase1.check("BTC", StrategyType.MEAN_REVERSION, {})
        assert out.result == PhaseResult.PASS
        phase1.regime_detector.get_current_regime.assert_called_once_with("BTC")
        blocked = phase1.check("BTC", StrategyType.MA_CROSSOVER, {})
        assert blocked.result == PhaseResult.BLOCK
        assert "RANGING_CALM" in blocked.reason

    def test_no_confirmed_regime_is_indecisive(self) -> None:
        out = self._phase1(None).check("BTC", StrategyType.MEAN_REVERSION, {})
        assert out.result == PhaseResult.BLOCK and "INDECISIVE" in out.reason

    def test_a_detector_without_the_accessor_is_not_swallowed(self) -> None:
        phase1 = Phase1RegimePermission(SimpleNamespace(), RiskManager(), Config())
        with pytest.raises(AttributeError):
            phase1.check("BTC", StrategyType.MEAN_REVERSION, {})


class TestPipelineBaseSize:
    def _pipeline(self, sizer: Any) -> SignalPipeline:
        rm = RiskManager()
        phase1 = Phase1RegimePermission(
            None, rm, SimpleNamespace(), enforce_regime=False
        )
        phase3 = Phase3ExecutionFilter(rm, None, SimpleNamespace())
        return SignalPipeline(phase1, {}, phase3, sizer)

    def test_real_kelly_sizer_is_called_with_its_real_signature(self) -> None:
        size = self._pipeline(strict_kelly(HEALTHY))._calculate_base_size(
            make_signal(), BALANCE
        )
        assert size == pytest.approx(200.0)

    def test_sizer_error_propagates(self) -> None:
        sizer = SimpleNamespace(calculate_position_size=raiser(TypeError("bad call")))
        with pytest.raises(TypeError):
            self._pipeline(sizer)._calculate_base_size(make_signal(), BALANCE)

    def test_no_sizer_uses_two_percent(self) -> None:
        size = self._pipeline(None)._calculate_base_size(make_signal(), BALANCE)
        assert size == pytest.approx(2.0)

    def test_review_entry_leaves_the_signal_alone(self) -> None:
        signal = make_signal(side=OrderSide.SELL, entry_price=100.0, stop_loss=105.0)
        snapshot = dict(vars(signal))
        out = self._pipeline(None).review_entry(signal, 5.0, BALANCE)
        assert out.result == PhaseResult.PASS
        assert vars(signal) == snapshot
