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
    DEFAULT_SD_ENTRY_THRESHOLD,
    SD_ENTRY_THRESHOLD_MAX,
    SD_ENTRY_THRESHOLD_MIN,
    UNSUPPORTED_ENV_VARS,
    VWAPScalpingStrategy,
    validate_sd_entry_threshold,
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
