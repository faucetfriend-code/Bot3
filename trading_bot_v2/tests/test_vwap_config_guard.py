"""
Regression tests for the VWAP scalping entry-threshold configuration guard.

Background
----------
`VWAP_SD_ENTRY_THRESHOLD` shipped as 4.037, a value tuned in the standalone
BTV2 harness against a DAILY SESSION-ANCHORED VWAP. This strategy uses a
rolling cumulative VWAP whose deviation distribution is much tighter (mean
~1.0 SD, empirical max ~4.0 SD over six months of BTC/ETH/SUI 5m data), so
the entry gate in `generate_signals()` could never fire and the strategy
emitted zero signals for its entire life.

The existing end-to-end tests did not catch it because they construct the
strategy with a hardcoded `sd_entry_threshold=1.5` and never exercise the
value that is actually shipped in `.env`.

These tests pin the two invariants that would have caught it:
  1. The threshold the strategy ends up running with is always inside the
     range the optimizer is allowed to search.
  2. That range is genuinely attainable -- a signal really is produced at
     the default threshold on reference data.
"""

import random

import pytest

from trading_bot_v2.market_regime import MarketRegime
from trading_bot_v2.models import OrderSide
from trading_bot_v2.optimization.search_spaces import get_search_space
from trading_bot_v2.strategies.vwap_scalping import (
    DEFAULT_ENTRY_CONFIRMATION,
    DEFAULT_SD_ENTRY_THRESHOLD,
    DEFAULT_STOP_SOURCE,
    ENTRY_CONFIRMATION_ADVERSE,
    ENTRY_CONFIRMATION_NONE,
    ENTRY_CONFIRMATION_TURNING,
    SD_ENTRY_THRESHOLD_MAX,
    SD_ENTRY_THRESHOLD_MIN,
    STOP_SOURCE_ATR_EXECUTION,
    STOP_SOURCE_ATR_STRUCTURE,
    STOP_SOURCE_SD_BAND,
    UNSUPPORTED_ENV_VARS,
    VALID_ENTRY_CONFIRMATIONS,
    VALID_STOP_SOURCES,
    VWAPScalpingStrategy,
    validate_entry_confirmation,
    validate_sd_entry_threshold,
    validate_stop_source,
    warn_unsupported_env_vars,
)
from trading_bot_v2.strategy_manager import (
    DEFAULT_VWAP_ACTIVE_REGIMES,
    StrategyManager,
    resolve_vwap_active_regimes,
)

# The historical bad value from .env, kept as a literal so this test keeps
# failing if anyone reintroduces it.
UNREACHABLE_LEGACY_THRESHOLD = 4.037


# ---------------------------------------------------------------------------
# Deterministic reference data (mirrors DataGenerator in test_strategy_e2e)
# ---------------------------------------------------------------------------


def _make_ohlcv(closes, spread_pct=0.005, base_volume=1000.0):
    """Build high/low/open/volume lists from closes."""
    highs, lows, opens, volumes = [], [], [], []
    for c in closes:
        highs.append(c * (1 + spread_pct))
        lows.append(c * (1 - spread_pct))
        opens.append(c * (1 + random.uniform(-spread_pct * 0.5, spread_pct * 0.5)))
        volumes.append(base_volume * random.uniform(0.8, 1.2))
    return {
        "high": highs,
        "low": lows,
        "close": closes,
        "open": opens,
        "volume": volumes,
    }


def _below_vwap_data():
    """Range then sustained decline: price ends well below the rolling VWAP."""
    random.seed(42)
    closes = [100 + random.uniform(-0.5, 0.5) for _ in range(200)]
    for _ in range(50):
        closes.append(closes[-1] - 0.08)
    # Accelerate into the close so the MACD histogram stays negative, which
    # is what the BUY branch requires.
    closes[-1] -= 0.3
    closes[-2] -= 0.15
    return _make_ohlcv(closes)


@pytest.fixture
def clean_vwap_env(monkeypatch):
    """Remove every VWAP_* override so tests do not inherit a developer .env."""
    for name in UNSUPPORTED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in (
        "VWAP_SD_ENTRY_THRESHOLD",
        "VWAP_STOP_SOURCE",
        "VWAP_ENTRY_CONFIRMATION",
        "VWAP_ATR_PERIOD",
        "VWAP_ATR_STOP_MULTIPLIER",
        "VWAP_MACD_FAST",
        "VWAP_MACD_SLOW",
        "VWAP_MACD_SIGNAL",
        "VWAP_MIN_CONFIDENCE",
        "VWAP_COOLDOWN_MINUTES",
        "VWAP_SD_MULTIPLIERS",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# 1. The guard range must track the optimizer search space
# ---------------------------------------------------------------------------


class TestSupportedRange:

    def test_bounds_match_optimizer_search_space(self):
        """Guard bounds must not drift from _vwap_scalping_space()."""
        low, high = get_search_space("vwap_scalping")["sd_entry_threshold"]
        assert (SD_ENTRY_THRESHOLD_MIN, SD_ENTRY_THRESHOLD_MAX) == (low, high)

    def test_default_is_inside_supported_range(self):
        assert SD_ENTRY_THRESHOLD_MIN <= DEFAULT_SD_ENTRY_THRESHOLD
        assert DEFAULT_SD_ENTRY_THRESHOLD <= SD_ENTRY_THRESHOLD_MAX

    def test_legacy_value_is_out_of_range(self):
        """Sanity check that the value this bug was about is still rejected."""
        assert UNREACHABLE_LEGACY_THRESHOLD > SD_ENTRY_THRESHOLD_MAX


# ---------------------------------------------------------------------------
# 2. validate_sd_entry_threshold behaviour
# ---------------------------------------------------------------------------


class TestValidateSdEntryThreshold:

    @pytest.mark.parametrize(
        "value",
        [SD_ENTRY_THRESHOLD_MIN, 1.5, 2.0, 2.5, SD_ENTRY_THRESHOLD_MAX],
    )
    def test_in_range_values_pass_through(self, value):
        assert validate_sd_entry_threshold(value) == value

    @pytest.mark.parametrize(
        "value", [UNREACHABLE_LEGACY_THRESHOLD, 3.5, 10.0, 0.5, 0.0, -1.0]
    )
    def test_out_of_range_values_fall_back_to_default(self, value):
        assert validate_sd_entry_threshold(value) == DEFAULT_SD_ENTRY_THRESHOLD

    def test_out_of_range_value_logs_a_warning(self):
        """The failure mode was silence; a warning is the whole point."""
        from loguru import logger

        records = []
        sink_id = logger.add(records.append, level="WARNING")
        try:
            validate_sd_entry_threshold(UNREACHABLE_LEGACY_THRESHOLD)
        finally:
            logger.remove(sink_id)

        assert any("VWAP_SD_ENTRY_THRESHOLD" in str(r) for r in records)

    def test_in_range_value_logs_nothing(self):
        from loguru import logger

        records = []
        sink_id = logger.add(records.append, level="WARNING")
        try:
            validate_sd_entry_threshold(2.0)
        finally:
            logger.remove(sink_id)

        assert records == []


# ---------------------------------------------------------------------------
# 3. The guard is applied on every construction path
# ---------------------------------------------------------------------------


class TestConstructionPaths:

    def test_explicit_kwarg_is_validated(self):
        strategy = VWAPScalpingStrategy(
            sd_entry_threshold=UNREACHABLE_LEGACY_THRESHOLD
        )
        assert strategy.sd_entry_threshold == DEFAULT_SD_ENTRY_THRESHOLD

    def test_env_value_is_validated(self, clean_vwap_env):
        clean_vwap_env.setenv(
            "VWAP_SD_ENTRY_THRESHOLD", str(UNREACHABLE_LEGACY_THRESHOLD)
        )
        strategy = VWAPScalpingStrategy()
        assert strategy.sd_entry_threshold == DEFAULT_SD_ENTRY_THRESHOLD

    def test_env_default_is_used_when_unset(self, clean_vwap_env):
        strategy = VWAPScalpingStrategy()
        assert strategy.sd_entry_threshold == DEFAULT_SD_ENTRY_THRESHOLD

    def test_valid_env_value_is_respected(self, clean_vwap_env):
        clean_vwap_env.setenv("VWAP_SD_ENTRY_THRESHOLD", "2.4")
        strategy = VWAPScalpingStrategy()
        assert strategy.sd_entry_threshold == pytest.approx(2.4)

    def test_strategy_manager_never_builds_an_unreachable_strategy(
        self, clean_vwap_env
    ):
        """
        THE regression test.

        StrategyManager reads VWAP_SD_ENTRY_THRESHOLD from .env and passes it
        to the constructor explicitly, which is why the code default never
        applied. Before the fix this produced a strategy pinned at 4.037.
        """
        clean_vwap_env.setenv(
            "VWAP_SD_ENTRY_THRESHOLD", str(UNREACHABLE_LEGACY_THRESHOLD)
        )

        manager = StrategyManager(
            enable_mean_reversion=False,
            enable_ma_crossover=False,
            enable_trend_following=False,
            enable_grid_trading=False,
            enable_liquidation_capture=False,
            enable_vwap_scalping=True,
            enable_funding_arb=False,
            enable_momentum_scalping=False,
            enable_orderbook_imbalance=False,
            enable_session_range_breakout=False,
            enable_calendar_flow=False,
        )

        threshold = manager.strategies["VWAPScalping"].sd_entry_threshold
        assert SD_ENTRY_THRESHOLD_MIN <= threshold <= SD_ENTRY_THRESHOLD_MAX


# ---------------------------------------------------------------------------
# 4. The supported range is actually attainable
# ---------------------------------------------------------------------------


class TestThresholdIsAttainable:

    def test_reference_data_reaches_the_default_threshold(self):
        """
        A threshold is only meaningful if real deviations can reach it.

        This is the property the old 4.037 violated: over six months of 5m
        data the observed maximum deviation was ~3.95 SD, so the gate never
        opened. Here the configured default must be reachable.
        """
        strategy = VWAPScalpingStrategy(sd_entry_threshold=DEFAULT_SD_ENTRY_THRESHOLD)
        data = _below_vwap_data()

        bands = strategy._calculate_vwap_and_bands(
            data["high"], data["low"], data["close"], data["volume"]
        )
        assert bands is not None

        current_price = data["close"][-1]
        deviation_sd = abs(current_price - bands["vwap"]) / bands["stddev"]

        assert deviation_sd >= strategy.sd_entry_threshold, (
            f"configured threshold {strategy.sd_entry_threshold} is unreachable: "
            f"max observed deviation on reference data is {deviation_sd:.3f} SD"
        )

    def test_signal_is_emitted_at_the_default_threshold(self):
        """End-to-end proof that the shipped default is not inert."""
        strategy = VWAPScalpingStrategy(
            sd_entry_threshold=DEFAULT_SD_ENTRY_THRESHOLD,
            min_confidence=0.30,
            cooldown_minutes=0,
        )
        data = _below_vwap_data()

        signals = strategy.generate_signals(
            "BTC", {"15m": data}, data["close"][-1]
        )

        assert len(signals) == 1
        assert signals[0].side == OrderSide.BUY

    def test_legacy_threshold_would_have_produced_no_signal(self):
        """
        Demonstrates the original bug on the same data, bypassing the guard.

        Setting the attribute directly is deliberate: it reproduces the
        pre-fix state that the guard now prevents.
        """
        strategy = VWAPScalpingStrategy(
            min_confidence=0.30,
            cooldown_minutes=0,
        )
        strategy.sd_entry_threshold = UNREACHABLE_LEGACY_THRESHOLD
        data = _below_vwap_data()

        signals = strategy.generate_signals(
            "BTC", {"15m": data}, data["close"][-1]
        )

        assert signals == []


# ---------------------------------------------------------------------------
# 5. Dead .env knobs announce themselves
# ---------------------------------------------------------------------------


class TestUnsupportedEnvVars:

    def test_unsupported_vars_are_not_read_by_the_strategy(self):
        """None of these names may become live config without updating the list."""
        import inspect

        from trading_bot_v2.strategies import vwap_scalping

        source = inspect.getsource(vwap_scalping.VWAPScalpingStrategy)
        for name in UNSUPPORTED_ENV_VARS:
            assert name not in source, (
                f"{name} is now read by the strategy; remove it from "
                "UNSUPPORTED_ENV_VARS"
            )

    def test_set_but_unread_vars_are_reported(self, clean_vwap_env):
        clean_vwap_env.setenv("VWAP_ENTRY_MODE", "bull_pullback")
        clean_vwap_env.setenv("VWAP_USE_HTF_EMA", "true")

        ignored = warn_unsupported_env_vars()

        assert set(ignored) == {"VWAP_ENTRY_MODE", "VWAP_USE_HTF_EMA"}

    def test_nothing_reported_when_env_is_clean(self, clean_vwap_env):
        assert warn_unsupported_env_vars() == []


# ---------------------------------------------------------------------------
# 6. The regime mapping is configurable, and its bounds hold
# ---------------------------------------------------------------------------


class TestActiveRegimeMapping:
    """``VWAP_ACTIVE_REGIMES`` decides which regimes VWAP is admitted to.

    ``docs/REGIME-CENSUS.md`` measured the three shipped cells separately
    and they disagree by a factor of 1.6 in profit factor, so which cells
    are mapped is a decision that has to be falsifiable rather than a
    literal buried in ``generate_signals_for_market``.
    """

    def test_default_is_the_shipped_mapping(self, monkeypatch):
        monkeypatch.delenv("VWAP_ACTIVE_REGIMES", raising=False)
        assert resolve_vwap_active_regimes() == list(DEFAULT_VWAP_ACTIVE_REGIMES)

    def test_default_excludes_every_trending_regime(self):
        assert MarketRegime.TRENDING_STRONG not in DEFAULT_VWAP_ACTIVE_REGIMES
        assert MarketRegime.TRENDING_MODERATE not in DEFAULT_VWAP_ACTIVE_REGIMES

    def test_explicit_subset_is_honoured(self):
        assert resolve_vwap_active_regimes("INDECISIVE") == [MarketRegime.INDECISIVE]

    def test_parsing_is_case_and_space_insensitive(self):
        assert resolve_vwap_active_regimes(" indecisive , Ranging_Volatile ") == [
            MarketRegime.INDECISIVE,
            MarketRegime.RANGING_VOLATILE,
        ]

    def test_duplicates_collapse(self):
        assert resolve_vwap_active_regimes("INDECISIVE,INDECISIVE") == [
            MarketRegime.INDECISIVE
        ]

    def test_unknown_names_are_dropped_not_fatal(self):
        assert resolve_vwap_active_regimes("INDECISIVE,NOT_A_REGIME") == [
            MarketRegime.INDECISIVE
        ]

    def test_trending_regimes_are_refused(self):
        """VWAP is counter-trend; the env may not re-admit it to a trend."""
        assert resolve_vwap_active_regimes(
            "TRENDING_STRONG,TRENDING_MODERATE,INDECISIVE"
        ) == [MarketRegime.INDECISIVE]

    def test_empty_resolution_falls_back_to_the_shipped_mapping(self):
        assert resolve_vwap_active_regimes("TRENDING_STRONG") == list(
            DEFAULT_VWAP_ACTIVE_REGIMES
        )
        assert resolve_vwap_active_regimes("") == list(DEFAULT_VWAP_ACTIVE_REGIMES)

    def test_manager_reads_the_env(self, monkeypatch):
        monkeypatch.setenv("VWAP_ACTIVE_REGIMES", "INDECISIVE")
        manager = StrategyManager(
            enable_mean_reversion=False,
            enable_ma_crossover=False,
            enable_trend_following=False,
            enable_grid_trading=False,
            enable_liquidation_capture=False,
            enable_vwap_scalping=True,
            enable_funding_arb=False,
            enable_momentum_scalping=False,
            enable_orderbook_imbalance=False,
            enable_session_range_breakout=False,
            enable_calendar_flow=False,
        )
        assert manager.vwap_active_regimes == [MarketRegime.INDECISIVE]


# ---------------------------------------------------------------------------
# 6b. Stop placement and entry confirmation are switchable and measurable
# ---------------------------------------------------------------------------


class TestStopSourceAndEntryConfirmation:
    """Both switches default to the shipped behaviour.

    docs/VWAP-LEVERS.md measured the shipped configuration at an 18.1% win
    rate with 82% of exits via the stop. The two defects that produce it are
    a stop taken from the 1m ATR (~5x tighter than the 15m setup implies)
    and a MACD gate that requires momentum to still run against the trade.
    These tests pin both as *choices* so an A/B can replace them.
    """

    # -- validators ----------------------------------------------------

    @pytest.mark.parametrize("value", VALID_STOP_SOURCES)
    def test_valid_stop_sources_pass_through(self, value):
        assert validate_stop_source(value) == value

    @pytest.mark.parametrize("value", VALID_ENTRY_CONFIRMATIONS)
    def test_valid_confirmations_pass_through(self, value):
        assert validate_entry_confirmation(value) == value

    def test_parsing_is_case_and_space_insensitive(self):
        assert validate_stop_source("  SD_Band ") == STOP_SOURCE_SD_BAND
        assert validate_entry_confirmation(" Turning ") == ENTRY_CONFIRMATION_TURNING

    @pytest.mark.parametrize("value", ["", None, "atr", "1m", "nonsense"])
    def test_unknown_stop_source_falls_back(self, value):
        assert validate_stop_source(value) == DEFAULT_STOP_SOURCE

    @pytest.mark.parametrize("value", ["", None, "macd", "exhausted"])
    def test_unknown_confirmation_falls_back(self, value):
        assert validate_entry_confirmation(value) == DEFAULT_ENTRY_CONFIRMATION

    def test_defaults_reproduce_shipped_behaviour(self):
        """The A/B is only honest if the control is genuinely the control."""
        assert DEFAULT_STOP_SOURCE == STOP_SOURCE_ATR_EXECUTION
        assert DEFAULT_ENTRY_CONFIRMATION == ENTRY_CONFIRMATION_ADVERSE

    def test_env_is_read(self, clean_vwap_env):
        clean_vwap_env.setenv("VWAP_STOP_SOURCE", "atr_structure")
        clean_vwap_env.setenv("VWAP_ENTRY_CONFIRMATION", "turning")
        strategy = VWAPScalpingStrategy()
        assert strategy.stop_source == STOP_SOURCE_ATR_STRUCTURE
        assert strategy.entry_confirmation == ENTRY_CONFIRMATION_TURNING

    def test_strategy_manager_honours_the_env(self, clean_vwap_env):
        """The construction path that broke `sd_entry_threshold` before.

        StrategyManager passes most VWAP parameters as explicit kwargs, which
        is why the constructor's own env fallback never applied to them. These
        two are deliberately NOT in that kwarg list, so a shell export reaches
        them - which is what the A/B sweep depends on.
        """
        clean_vwap_env.setenv("VWAP_STOP_SOURCE", "sd_band")
        clean_vwap_env.setenv("VWAP_ENTRY_CONFIRMATION", "turning")

        manager = StrategyManager(
            enable_mean_reversion=False,
            enable_ma_crossover=False,
            enable_trend_following=False,
            enable_grid_trading=False,
            enable_liquidation_capture=False,
            enable_vwap_scalping=True,
            enable_funding_arb=False,
            enable_momentum_scalping=False,
            enable_orderbook_imbalance=False,
            enable_session_range_breakout=False,
            enable_calendar_flow=False,
        )

        strategy = manager.strategies["VWAPScalping"]
        assert strategy.stop_source == STOP_SOURCE_SD_BAND
        assert strategy.entry_confirmation == ENTRY_CONFIRMATION_TURNING

    def test_kwarg_beats_env(self, clean_vwap_env):
        clean_vwap_env.setenv("VWAP_STOP_SOURCE", "atr_structure")
        strategy = VWAPScalpingStrategy(stop_source="sd_band")
        assert strategy.stop_source == STOP_SOURCE_SD_BAND

    # -- entry confirmation truth table --------------------------------

    def test_adverse_requires_momentum_against_the_trade(self):
        """The shipped gate, stated plainly so its oddity is visible."""
        s = VWAPScalpingStrategy(entry_confirmation=ENTRY_CONFIRMATION_ADVERSE)
        assert s._entry_confirmed(OrderSide.BUY, -0.5, None) is True
        assert s._entry_confirmed(OrderSide.BUY, 0.5, None) is False
        assert s._entry_confirmed(OrderSide.SELL, 0.5, None) is True
        assert s._entry_confirmed(OrderSide.SELL, -0.5, None) is False

    def test_turning_requires_momentum_inflecting_toward_the_trade(self):
        s = VWAPScalpingStrategy(entry_confirmation=ENTRY_CONFIRMATION_TURNING)
        # Still negative, but rising: the case `adverse` calls a late entry.
        assert s._entry_confirmed(OrderSide.BUY, -0.3, -0.5) is True
        assert s._entry_confirmed(OrderSide.BUY, -0.7, -0.5) is False
        assert s._entry_confirmed(OrderSide.SELL, 0.3, 0.5) is True
        assert s._entry_confirmed(OrderSide.SELL, 0.7, 0.5) is False

    def test_turning_refuses_without_a_previous_histogram(self):
        s = VWAPScalpingStrategy(entry_confirmation=ENTRY_CONFIRMATION_TURNING)
        assert s._entry_confirmed(OrderSide.BUY, -0.3, None) is False

    def test_none_admits_everything(self):
        s = VWAPScalpingStrategy(entry_confirmation=ENTRY_CONFIRMATION_NONE)
        for side in (OrderSide.BUY, OrderSide.SELL):
            for hist in (-1.0, 0.0, 1.0):
                assert s._entry_confirmed(side, hist, None) is True

    def test_adverse_and_turning_disagree_on_the_rejected_case(self):
        """`turning` exists precisely to take the trade `adverse` skips."""
        adverse = VWAPScalpingStrategy(entry_confirmation=ENTRY_CONFIRMATION_ADVERSE)
        turning = VWAPScalpingStrategy(entry_confirmation=ENTRY_CONFIRMATION_TURNING)
        # Histogram has crossed up: adverse calls this "late", turning takes it.
        assert adverse._entry_confirmed(OrderSide.BUY, 0.1, -0.2) is False
        assert turning._entry_confirmed(OrderSide.BUY, 0.1, -0.2) is True

    # -- stop placement ------------------------------------------------

    def test_atr_modes_use_the_atr_they_are_given(self):
        for source in (STOP_SOURCE_ATR_EXECUTION, STOP_SOURCE_ATR_STRUCTURE):
            s = VWAPScalpingStrategy(stop_source=source, atr_stop_multiplier=1.5)
            buy = s._resolve_stop_loss(
                OrderSide.BUY, 100.0, atr=2.0, vwap=105.0, stddev=2.0, deviation_sd=2.5
            )
            assert buy == pytest.approx(97.0)
            sell = s._resolve_stop_loss(
                OrderSide.SELL, 100.0, atr=2.0, vwap=95.0, stddev=2.0, deviation_sd=2.5
            )
            assert sell == pytest.approx(103.0)

    def test_sd_band_stop_sits_beyond_the_next_whole_band(self):
        s = VWAPScalpingStrategy(stop_source=STOP_SOURCE_SD_BAND)
        # Price 2.5 SD below a VWAP of 105 with sigma 2 -> next band out is 3SD.
        stop = s._resolve_stop_loss(
            OrderSide.BUY, 100.0, atr=0.1, vwap=105.0, stddev=2.0, deviation_sd=2.5
        )
        assert stop == pytest.approx(105.0 - 3 * 2.0)
        assert stop < 100.0, "a long's stop must sit below the entry"

    def test_sd_band_stop_ignores_the_atr_entirely(self):
        s = VWAPScalpingStrategy(stop_source=STOP_SOURCE_SD_BAND)
        args = dict(vwap=105.0, stddev=2.0, deviation_sd=2.5)
        wide = s._resolve_stop_loss(OrderSide.BUY, 100.0, atr=99.0, **args)
        narrow = s._resolve_stop_loss(OrderSide.BUY, 100.0, atr=0.01, **args)
        assert wide == narrow

    def test_execution_atr_produces_a_materially_tighter_stop(self):
        """THE regression test for the geometry defect.

        Measured on BTC 2022-07..2024-07, ATR(15m) is 5.08x ATR(1m), so the
        shipped `atr_execution` stop is about a fifth of the distance the 15m
        setup implies - and smaller than the entry bar's own high-low range on
        99.3% of signals. This asserts the two modes really do differ, so the
        defect cannot silently return by the two paths converging.
        """
        atr_1m, atr_15m = 0.055, 0.278  # median % of price, from the parquets

        execution = VWAPScalpingStrategy(stop_source=STOP_SOURCE_ATR_EXECUTION)
        structure = VWAPScalpingStrategy(stop_source=STOP_SOURCE_ATR_STRUCTURE)
        args = dict(vwap=101.0, stddev=0.5, deviation_sd=2.0)

        tight = 100.0 - execution._resolve_stop_loss(
            OrderSide.BUY, 100.0, atr=atr_1m, **args
        )
        wide = 100.0 - structure._resolve_stop_loss(
            OrderSide.BUY, 100.0, atr=atr_15m, **args
        )
        assert wide > 4 * tight

    # -- end to end ----------------------------------------------------

    def test_switches_are_reported_on_the_signal(self):
        """The A/B is only readable if each fill records its configuration."""
        strategy = VWAPScalpingStrategy(
            sd_entry_threshold=DEFAULT_SD_ENTRY_THRESHOLD,
            min_confidence=0.30,
            cooldown_minutes=0,
            stop_source=STOP_SOURCE_SD_BAND,
            entry_confirmation=ENTRY_CONFIRMATION_NONE,
        )
        data = _below_vwap_data()
        signals = strategy.generate_signals("BTC", {"15m": data}, data["close"][-1])

        assert len(signals) == 1
        assert signals[0].indicators["stop_source"] == STOP_SOURCE_SD_BAND
        assert signals[0].indicators["entry_confirmation"] == ENTRY_CONFIRMATION_NONE

    def test_none_confirmation_admits_a_bar_adverse_rejects(self):
        """End-to-end proof the gate is really removed, not merely renamed."""
        data = _below_vwap_data()
        # Flip the tape so the MACD histogram is positive at a price *below*
        # VWAP, which is exactly what the shipped BUY branch refuses.
        closes = list(data["close"])
        closes[-1] += 0.55
        closes[-2] += 0.35
        flipped = _make_ohlcv(closes)

        def run(mode):
            return VWAPScalpingStrategy(
                sd_entry_threshold=DEFAULT_SD_ENTRY_THRESHOLD,
                min_confidence=0.30,
                cooldown_minutes=0,
                entry_confirmation=mode,
            ).generate_signals("BTC", {"15m": flipped}, flipped["close"][-1])

        assert run(ENTRY_CONFIRMATION_NONE), "none must admit the bar"
        assert not run(ENTRY_CONFIRMATION_ADVERSE), "adverse must still refuse it"


# ---------------------------------------------------------------------------
# 7. The search space carries no parameter the strategy cannot read
# ---------------------------------------------------------------------------


class TestSearchSpaceHasNoDeadDimensions:
    """Every searched key must reach a branch, not merely an attribute.

    ``sd_exit_threshold`` was removed on 2026-07-28 for describing a
    mechanism VWAP does not have. ``rsi_oversold`` / ``rsi_overbought``
    followed on 2026-07-29: the constructor stores them, but
    ``generate_signals`` only ever formats the RSI *value* into a note
    string, so no sampled value could change a single decision.
    """

    INERT_KEYS = ("sd_exit_threshold", "rsi_oversold", "rsi_overbought")

    def test_inert_parameters_are_not_searched(self):
        space = get_search_space("vwap_scalping")
        for key in self.INERT_KEYS:
            assert key not in space, (
                f"{key} is back in the VWAP search space; it is stored but "
                f"never read, so every trial spent on it is charged to the "
                f"deflated Sharpe for nothing"
            )

    def test_rsi_thresholds_are_still_inert(self):
        """Fail loudly if RSI ever becomes a real gate.

        That would make the two dimensions legitimate again, and this
        test is what tells the next reader to put them back.
        """
        import inspect

        from trading_bot_v2.strategies import vwap_scalping

        source = inspect.getsource(vwap_scalping.VWAPScalpingStrategy.generate_signals)
        assert "self.rsi_oversold" not in source
        assert "self.rsi_overbought" not in source

    def test_every_searched_key_is_a_strategy_attribute(self):
        space = get_search_space("vwap_scalping")
        strategy = VWAPScalpingStrategy()
        for key in space:
            assert hasattr(strategy, key), (
                f"{key} is searched but VWAPScalpingStrategy has no such "
                f"attribute, so the optimizer's setattr is a silent no-op"
            )
