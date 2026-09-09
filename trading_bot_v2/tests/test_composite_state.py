"""
Tests for composite-state (vol tercile x direction) plumbing.

Covers: direction tagging on fills and its entry-attribution rule in
SimulatedExchange, composite-key normalization in regime_param_overlay,
and composite filtering in OptimizationAdapter.get_regime_trades.
"""

import pytest

from trading_bot_v2.backtesting.cost_model import CostTable
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.regime_param_overlay import normalize_regime_value

FLAT_BAR = {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000}


def _exchange(capital=10000.0):
    costs = CostTable(
        profile="legacy",
        base_maker_fee_pct=0.0,
        base_taker_fee_pct=0.0,
        base_slippage_pct=0.0,
        read_env_overrides=False,
    )
    return SimulatedExchange(
        initial_capital=capital, cost_table=costs, funding_hourly_pct=0.0
    )


class TestDirectionTagging:
    def test_opening_fill_carries_current_direction(self):
        ex = _exchange()
        ex._current_regime = "vol_low"
        ex._current_direction = "bull"
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")
        ex.place_order(symbol="BTC-USDC", side="bid", quantity="1", order_type="market")
        assert ex.trade_log[-1]["direction"] == "bull"
        assert ex.trade_log[-1]["regime"] == "vol_low"
        assert ex._positions["BTC-USDC"].entry_direction == "bull"

    def test_closing_fill_attributes_entry_direction_not_current(self):
        # Open under bull, close under bear: the closing trade must be
        # attributed to the ENTRY state, mirroring the regime rule
        ex = _exchange()
        ex._current_regime = "vol_low"
        ex._current_direction = "bull"
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")
        ex.place_order(symbol="BTC-USDC", side="bid", quantity="1", order_type="market")
        ex._current_direction = "bear"
        ex._current_regime = "vol_high"
        ex.advance(FLAT_BAR, "2024-01-01T01:00:00")
        ex.place_order(symbol="BTC-USDC", side="ask", quantity="1", order_type="market")
        closing = ex.trade_log[-1]
        assert closing["direction"] == "bull"
        assert closing["regime"] == "vol_low"

    def test_untagged_run_defaults_to_empty_string(self):
        ex = _exchange()
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")
        ex.place_order(symbol="BTC-USDC", side="bid", quantity="1", order_type="market")
        assert ex.trade_log[-1]["direction"] == ""


class TestCompositeNormalization:
    def test_plain_regimes_unchanged(self):
        assert normalize_regime_value("RANGING_CALM") == "ranging_calm"
        assert normalize_regime_value("vol_low") == "vol_low"

    def test_composite_key_normalizes_both_parts(self):
        assert normalize_regime_value("VOL_LOW:TREND") == "vol_low:trend"
        assert normalize_regime_value("Vol_High:Neutral") == "vol_high:neutral"
        assert normalize_regime_value("vol_mid:bull") == "vol_mid:bull"

    def test_unknown_direction_raises(self):
        with pytest.raises(ValueError, match="direction"):
            normalize_regime_value("VOL_LOW:SIDEWAYS")

    def test_unknown_regime_raises(self):
        with pytest.raises(ValueError, match="Unknown regime"):
            normalize_regime_value("VOL_EXTREME:TREND")


class TestCompositeTradeFilter:
    @staticmethod
    def _result_with(trades):
        from trading_bot_v2.backtesting.optimization_adapter import (
            OptimizationAdapter,
        )

        class _Result:
            trade_log = trades

        return OptimizationAdapter(), _Result()

    TRADES = [
        {"pnl": 1.0, "regime": "vol_low", "direction": "bull"},
        {"pnl": -1.0, "regime": "vol_low", "direction": "bear"},
        {"pnl": 2.0, "regime": "vol_low", "direction": "neutral"},
        {"pnl": 1.5, "regime": "vol_high", "direction": "bull"},
        {"pnl": 0.0, "regime": "vol_low", "direction": "bull"},  # open fill
    ]

    def test_plain_regime_ignores_direction(self):
        adapter, result = self._result_with(self.TRADES)
        assert len(adapter.get_regime_trades(result, "vol_low")) == 3

    def test_trend_matches_bull_and_bear(self):
        adapter, result = self._result_with(self.TRADES)
        trades = adapter.get_regime_trades(result, "VOL_LOW:TREND")
        assert len(trades) == 2
        assert {t["direction"] for t in trades} == {"bull", "bear"}

    def test_neutral_matches_only_neutral(self):
        adapter, result = self._result_with(self.TRADES)
        trades = adapter.get_regime_trades(result, "vol_low:neutral")
        assert len(trades) == 1
        assert trades[0]["pnl"] == 2.0

    def test_bull_matches_exactly(self):
        adapter, result = self._result_with(self.TRADES)
        trades = adapter.get_regime_trades(result, "vol_low:bull")
        assert len(trades) == 1
        assert trades[0]["pnl"] == 1.0

    def test_open_fills_excluded(self):
        adapter, result = self._result_with(self.TRADES)
        for key in ("vol_low", "vol_low:bull", "vol_low:trend"):
            assert all(t["pnl"] != 0 for t in adapter.get_regime_trades(result, key))
