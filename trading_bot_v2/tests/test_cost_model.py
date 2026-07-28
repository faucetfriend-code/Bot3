"""
Tests for the per-symbol, per-liquidity-role cost model.

Covers the three properties the model exists to guarantee:
  1. Maker and taker fills are charged differently (a resting grid limit
     must never pay a taker fee).
  2. Per-symbol overrides apply, in both profiles.
  3. The ``legacy`` profile reproduces the historic flat model exactly,
     so existing results stay comparable until the operator opts in.
"""

import pytest

from trading_bot_v2.backtesting.cost_model import (
    DEFAULT_HALF_SPREAD_PCT,
    DEFAULT_PROFILE,
    FALLBACK_HALF_SPREAD_PCT,
    LEGACY_MAKER_FEE_PCT,
    LEGACY_SLIPPAGE_PCT,
    LEGACY_TAKER_FEE_PCT,
    LIMIT_ENTRY_PRICE_GAP_PCT,
    PACIFICA_MAKER_FEE_PCT,
    PACIFICA_TAKER_FEE_PCT,
    PROFILE_LEGACY,
    PROFILE_PACIFICA,
    CostModel,
    CostTable,
    LiquidityRole,
    resolve_cost_profile,
    symbol_root,
)
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.config import AssetClass, StrategyType


CANDLE = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
          "volume": 10000.0}


def _clear_cost_env(monkeypatch):
    """Remove every cost env var so a test sees pristine defaults."""
    for name in (
        "BACKTEST_COST_PROFILE",
        "BACKTEST_COST_TP_HAIRCUT",
        "BACKTEST_SLIPPAGE_VOL_COEF",
        "BACKTEST_SLIPPAGE_IMPACT_COEF",
        "BACKTEST_MAX_SLIPPAGE_PCT",
    ):
        monkeypatch.delenv(name, raising=False)
    for root in ("BTC", "ETH", "SOL", "SUI", "DOGE"):
        for prefix in (
            "BACKTEST_TAKER_FEE_PCT",
            "BACKTEST_MAKER_FEE_PCT",
            "BACKTEST_HALF_SPREAD_PCT",
            "BACKTEST_SLIPPAGE_PCT",
        ):
            monkeypatch.delenv(f"{prefix}_{root}", raising=False)


def _signal(entry_price=100.0, take_profit=105.0, stop_loss=98.0,
            asset="BTC-USDC", strategy=StrategyType.GRID_TRADING):
    return Signal(
        strategy=strategy,
        asset=asset,
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------

class TestSymbolRoot:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("BTC-USDC", "BTC"),
            ("btc-usdc", "BTC"),
            ("SUI", "SUI"),
            ("ETH/USDT", "ETH"),
            ("SOL_USDC", "SOL"),
            ("", ""),
        ],
    )
    def test_root_extraction(self, symbol, expected):
        assert symbol_root(symbol) == expected


# ---------------------------------------------------------------------------
# Profile resolution (warn-and-fall-back house pattern)
# ---------------------------------------------------------------------------

class TestProfileResolution:
    def test_unknown_profile_falls_back(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        assert resolve_cost_profile("hyperliquid") == DEFAULT_PROFILE

    def test_default_is_legacy(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        assert resolve_cost_profile(None) == PROFILE_LEGACY

    def test_env_selects_profile(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_COST_PROFILE", "PACIFICA")
        assert resolve_cost_profile(None) == PROFILE_PACIFICA

    def test_junk_numeric_env_falls_back(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_SLIPPAGE_IMPACT_COEF", "not-a-number")
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.impact_coef == pytest.approx(0.02)

    def test_out_of_range_env_falls_back(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_MAX_SLIPPAGE_PCT", "-1")
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.max_slippage_pct == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# Maker vs taker  (the pin the follow-up asked for)
# ---------------------------------------------------------------------------

class TestMakerTakerSeparation:
    def test_table_charges_maker_less_than_taker(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        maker = table.fee_pct("BTC-USDC", LiquidityRole.MAKER)
        taker = table.fee_pct("BTC-USDC", LiquidityRole.TAKER)
        assert maker == pytest.approx(PACIFICA_MAKER_FEE_PCT)
        assert taker == pytest.approx(PACIFICA_TAKER_FEE_PCT)
        assert maker < taker

    def test_resting_limit_fill_pays_maker_fee(self, monkeypatch):
        """A grid's resting limit must not be billed as a taker fill."""
        _clear_cost_env(monkeypatch)
        ex = SimulatedExchange(initial_capital=10000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("BTC-USDC", "bid", "1.0", order_type="limit", price=99.0)
        ex.advance({"open": 100, "high": 100, "low": 98, "close": 99,
                    "volume": 1000}, "2024-01-01T01:00:00")

        fill = ex.trade_log[-1]
        assert fill["role"] == "maker"
        assert fill["fee"] == pytest.approx(99.0 * 1.0 * ex.maker_fee_pct)
        # A resting limit takes no adverse slippage either.
        assert fill["fill_price"] == pytest.approx(99.0)

    def test_market_fill_pays_taker_fee_and_slippage(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        ex = SimulatedExchange(initial_capital=10000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("BTC-USDC", "bid", "1.0", order_type="market")

        fill = ex.trade_log[-1]
        assert fill["role"] == "taker"
        assert fill["fill_price"] > 100.0  # adverse slippage on a buy
        assert fill["fee"] > 0

    def test_maker_and_taker_fills_are_charged_differently(self, monkeypatch):
        """Same symbol, same size, same price - different bill."""
        _clear_cost_env(monkeypatch)

        taker_ex = SimulatedExchange(initial_capital=10000.0)
        taker_ex._current_price = 100.0
        taker_ex._current_timestamp = "2024-01-01T00:00:00"
        taker_ex.place_order("BTC-USDC", "bid", "1.0", order_type="market")

        maker_ex = SimulatedExchange(initial_capital=10000.0)
        maker_ex._current_price = 100.0
        maker_ex._current_timestamp = "2024-01-01T00:00:00"
        maker_ex.place_order(
            "BTC-USDC", "bid", "1.0", order_type="limit", price=100.0
        )
        maker_ex.advance(CANDLE, "2024-01-01T01:00:00")

        assert maker_ex.trade_log[-1]["fee"] < taker_ex.trade_log[-1]["fee"]

    def test_stop_fill_is_taker(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        ex = SimulatedExchange(initial_capital=10000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("BTC-USDC", "ask", "1.0", order_type="stop", price=98.0)
        ex.advance({"open": 100, "high": 100, "low": 97, "close": 98,
                    "volume": 1000}, "2024-01-01T01:00:00")
        assert ex.trade_log[-1]["role"] == "taker"


# ---------------------------------------------------------------------------
# Per-symbol overrides
# ---------------------------------------------------------------------------

class TestPerSymbolOverrides:
    def test_per_symbol_fee_override_applies(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_TAKER_FEE_PCT_SUI", "0.0011")
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.fee_pct("SUI-USDC", LiquidityRole.TAKER) == pytest.approx(
            0.0011
        )
        # Other symbols untouched.
        assert table.fee_pct("BTC-USDC", LiquidityRole.TAKER) == pytest.approx(
            PACIFICA_TAKER_FEE_PCT
        )

    def test_per_symbol_override_applies_in_legacy_profile_too(
        self, monkeypatch
    ):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_MAKER_FEE_PCT_BTC", "0.0")
        table = CostTable.from_env(profile=PROFILE_LEGACY)
        assert table.fee_pct("BTC-USDC", LiquidityRole.MAKER) == 0.0
        assert table.fee_pct("SUI-USDC", LiquidityRole.MAKER) == pytest.approx(
            LEGACY_MAKER_FEE_PCT
        )

    def test_per_symbol_flat_slippage_override_wins(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_SLIPPAGE_PCT_SUI", "0.0035")
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.slippage_pct(
            "SUI-USDC", notional=1e6, bar_range_pct=0.05, bar_notional=1e6
        ) == pytest.approx(0.0035)

    def test_per_symbol_half_spread_override(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_HALF_SPREAD_PCT_BTC", "0.001")
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.slippage_pct("BTC-USDC") == pytest.approx(0.001)

    def test_exchange_honours_per_symbol_override(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_COST_PROFILE", "pacifica")
        monkeypatch.setenv("BACKTEST_TAKER_FEE_PCT_SUI", "0.002")
        ex = SimulatedExchange(initial_capital=10000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("SUI-USDC", "bid", "1.0", order_type="market")
        ex.place_order("BTC-USDC", "bid", "1.0", order_type="market")
        sui_fee = ex.trade_log[0]["fee"]
        btc_fee = ex.trade_log[1]["fee"]
        assert sui_fee > btc_fee

    def test_unknown_symbol_uses_conservative_fallback_spread(
        self, monkeypatch
    ):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.slippage_pct("PEPE-USDC") == pytest.approx(
            FALLBACK_HALF_SPREAD_PCT
        )
        assert FALLBACK_HALF_SPREAD_PCT > DEFAULT_HALF_SPREAD_PCT["BTC"]


# ---------------------------------------------------------------------------
# Dynamic slippage
# ---------------------------------------------------------------------------

class TestDynamicSlippage:
    def test_thinner_symbol_costs_more(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        btc = table.slippage_pct("BTC-USDC", 200, 0.0015, 5e6)
        sui = table.slippage_pct("SUI-USDC", 200, 0.0015, 5e6)
        assert sui > btc

    def test_volatile_bar_costs_more(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        calm = table.slippage_pct("BTC-USDC", 200, 0.001, 5e6)
        wild = table.slippage_pct("BTC-USDC", 200, 0.05, 5e6)
        assert wild > calm

    def test_larger_order_costs_more(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        small = table.slippage_pct("BTC-USDC", 200, 0.0015, 5e6)
        large = table.slippage_pct("BTC-USDC", 200000, 0.0015, 5e6)
        assert large > small

    def test_slippage_is_capped(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.slippage_pct(
            "SUI-USDC", notional=1e9, bar_range_pct=0.9, bar_notional=1.0
        ) == pytest.approx(table.max_slippage_pct)

    def test_missing_volume_drops_impact_term_without_error(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(profile=PROFILE_PACIFICA)
        assert table.slippage_pct(
            "BTC-USDC", notional=200, bar_range_pct=0.0, bar_notional=0.0
        ) == pytest.approx(DEFAULT_HALF_SPREAD_PCT["BTC"])

    def test_exchange_captures_bar_context(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        ex = SimulatedExchange(initial_capital=10000.0)
        ex.advance(
            {"open": 100, "high": 110, "low": 90, "close": 100, "volume": 50},
            "2024-01-01T00:00:00",
        )
        assert ex._bar_range_pct == pytest.approx(0.2)
        assert ex._bar_notional == pytest.approx(5000.0)

    def test_malformed_bar_degrades_to_zero(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        ex = SimulatedExchange(initial_capital=10000.0)
        ex._capture_bar_context({"high": "nope"})
        assert ex._bar_range_pct == 0.0
        assert ex._bar_notional == 0.0


# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------

class TestLegacyProfileUnchanged:
    def test_legacy_slippage_is_flat_and_symbol_blind(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        table = CostTable.from_env(
            slippage_pct=LEGACY_SLIPPAGE_PCT,
            taker_fee_pct=LEGACY_TAKER_FEE_PCT,
            maker_fee_pct=LEGACY_MAKER_FEE_PCT,
        )
        for symbol in ("BTC-USDC", "SUI-USDC", "PEPE-USDC"):
            assert table.slippage_pct(
                symbol, 999999, 0.9, 1.0
            ) == pytest.approx(LEGACY_SLIPPAGE_PCT)

    def test_legacy_round_trip_cost_matches_historic_formula(
        self, monkeypatch
    ):
        _clear_cost_env(monkeypatch)
        model = CostModel(slippage_pct=0.002, taker_fee_pct=0.0006)
        assert model.round_trip_cost_pct == pytest.approx(0.0032)
        signal = _signal()
        assert model.round_trip_cost_for(signal, 100.0) == pytest.approx(0.0032)

    def test_legacy_haircut_is_bit_for_bit(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        model = CostModel(slippage_pct=0.002, taker_fee_pct=0.0006)
        signal = _signal(take_profit=105.0)
        model.apply(signal, current_price=100.0)
        assert signal.take_profit == pytest.approx(105.0 - 0.0032 * 100.0)

    def test_legacy_exchange_fill_prices_unchanged(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        ex = SimulatedExchange(initial_capital=10000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("SUI-USDC", "bid", "1.0", order_type="market")
        # 0.20% adverse slippage on a market buy, as before.
        assert ex.trade_log[-1]["fill_price"] == pytest.approx(100.2)
        assert ex.trade_log[-1]["fee"] == pytest.approx(100.2 * 0.0006)


# ---------------------------------------------------------------------------
# Order-type aware take-profit haircut
# ---------------------------------------------------------------------------

class TestRoleAwareHaircut:
    def test_entry_role_mirrors_engine_limit_rule(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        model = CostModel(profile=PROFILE_PACIFICA)
        near = _signal(entry_price=100.0 * (1 + LIMIT_ENTRY_PRICE_GAP_PCT / 2))
        far = _signal(entry_price=100.0 * (1 + LIMIT_ENTRY_PRICE_GAP_PCT * 2))
        assert model.entry_role(near, 100.0) == LiquidityRole.TAKER
        assert model.entry_role(far, 100.0) == LiquidityRole.MAKER

    def test_maker_entry_is_charged_less_than_taker_entry(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        model = CostModel(profile=PROFILE_PACIFICA)
        near = _signal(entry_price=100.0)
        far = _signal(entry_price=102.0)
        assert model.round_trip_cost_for(far, 100.0) < model.round_trip_cost_for(
            near, 100.0
        )

    def test_pacifica_haircut_far_smaller_than_legacy(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        legacy = CostModel(profile=PROFILE_LEGACY)
        pacifica = CostModel(profile=PROFILE_PACIFICA)
        grid_signal = _signal(entry_price=102.0, asset="BTC-USDC")
        assert pacifica.round_trip_cost_for(
            grid_signal, 100.0
        ) < legacy.round_trip_cost_for(grid_signal, 100.0) / 5

    def test_haircut_can_be_disabled(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        monkeypatch.setenv("BACKTEST_COST_TP_HAIRCUT", "false")
        model = CostModel(slippage_pct=0.002, taker_fee_pct=0.0006)
        signal = _signal(take_profit=105.0)
        model.apply(signal, current_price=100.0)
        assert signal.take_profit == pytest.approx(105.0)

    def test_haircut_direction_for_short(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        model = CostModel(slippage_pct=0.002, taker_fee_pct=0.0006)
        signal = _signal(take_profit=95.0, stop_loss=102.0)
        model.apply(signal, current_price=100.0)
        # Short target moves UP (closer to entry) by the same drag.
        assert signal.take_profit == pytest.approx(95.0 + 0.32)

    def test_thinner_symbol_gets_a_bigger_haircut(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        model = CostModel(profile=PROFILE_PACIFICA)
        btc = _signal(asset="BTC-USDC", entry_price=100.0)
        sui = _signal(asset="SUI-USDC", entry_price=100.0)
        assert model.round_trip_cost_for(
            sui, 100.0
        ) > model.round_trip_cost_for(btc, 100.0)

    def test_missing_levels_are_left_alone(self, monkeypatch):
        _clear_cost_env(monkeypatch)
        model = CostModel(profile=PROFILE_PACIFICA)
        signal = _signal(take_profit=None)
        model.apply(signal, current_price=100.0)
        assert signal.take_profit is None
