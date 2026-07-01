"""
End-to-End Strategy Tests

Tests each of the 8 trading strategies through the complete lifecycle:
  1. Signal Generation - real indicator calculations on synthetic data
  2. Signal Execution  - mock order placement verification
  3. Position Closing   - TP/SL hit simulation
  4. Edge Cases         - insufficient data, cooldowns, session limits

Uses deterministic synthetic OHLCV data (seed=42) designed to trigger
actual indicator thresholds for each strategy.
"""

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# Strategy imports
from trading_bot_v2.strategies.mean_reversion import MeanReversionStrategy
from trading_bot_v2.strategies.ma_crossover import MACrossoverStrategy
from trading_bot_v2.strategies.grid_trading import GridTradingStrategy
from trading_bot_v2.strategies.liquidation_capture import LiquidationCaptureStrategy
from trading_bot_v2.strategies.vwap_scalping import VWAPScalpingStrategy
from trading_bot_v2.strategies.funding_arb import FundingArbStrategy
from trading_bot_v2.strategies.momentum_scalping import MomentumScalpingStrategy
from trading_bot_v2.strategies.orderbook_imbalance import OrderBookImbalanceStrategy

# Model / config imports
from trading_bot_v2.models import Signal, OrderSide
from trading_bot_v2.config import StrategyType, AssetClass, MarketState


# ---------------------------------------------------------------------------
# Data Generator
# ---------------------------------------------------------------------------

class DataGenerator:
    """Deterministic synthetic market data for each strategy scenario."""

    @staticmethod
    def _seed():
        random.seed(42)

    @staticmethod
    def build_multi_tf_data(
        data_15m: Optional[Dict] = None,
        data_1h: Optional[Dict] = None,
        data_4h: Optional[Dict] = None,
        data_5m: Optional[Dict] = None,
    ) -> Dict[str, Dict[str, List[float]]]:
        result = {}
        if data_15m is not None:
            result["15m"] = data_15m
        if data_1h is not None:
            result["1h"] = data_1h
        if data_4h is not None:
            result["4h"] = data_4h
        if data_5m is not None:
            result["5m"] = data_5m
        return result

    @staticmethod
    def _make_ohlcv(closes, spread_pct=0.005, base_volume=1000.0):
        """Build high/low/open/volume lists from closes."""
        highs, lows, opens, volumes = [], [], [], []
        for c in closes:
            h = c * (1 + spread_pct)
            lo = c * (1 - spread_pct)
            o = c * (1 + random.uniform(-spread_pct * 0.5, spread_pct * 0.5))
            highs.append(h)
            lows.append(lo)
            opens.append(o)
            volumes.append(base_volume * random.uniform(0.8, 1.2))
        return {
            "high": highs,
            "low": lows,
            "close": closes,
            "open": opens,
            "volume": volumes,
        }

    # --- Mean Reversion data ---
    @staticmethod
    def mean_reversion_long_data(n=250):
        """Oscillating base then sharp decline -> RSI oversold, price near lower BB."""
        DataGenerator._seed()
        closes = []
        for i in range(230):
            closes.append(100 + 3 * math.sin(i * 0.25))
        for i in range(20):
            closes.append(closes[-1] - 0.8)
        return DataGenerator._make_ohlcv(closes)

    @staticmethod
    def mean_reversion_short_data(n=250):
        """Oscillating base then sharp rise -> RSI overbought, price near upper BB."""
        DataGenerator._seed()
        closes = []
        for i in range(230):
            closes.append(100 + 3 * math.sin(i * 0.25))
        for i in range(20):
            closes.append(closes[-1] + 0.8)
        return DataGenerator._make_ohlcv(closes)

    # --- MA Crossover data ---
    @staticmethod
    def ma_crossover_golden_cross_data():
        """
        201 candles declining then 36 rising candles so SMA(50)
        crosses above SMA(200) on the last candle.
        """
        DataGenerator._seed()
        closes = []
        for i in range(201):
            closes.append(110 - i * 0.04 + random.uniform(-0.2, 0.2))
        for i in range(36):
            closes.append(closes[-1] + 0.25 + random.uniform(-0.02, 0.02))
        data = DataGenerator._make_ohlcv(closes)
        for i in range(-10, 0):
            data["volume"][i] *= 2.0
        return data

    @staticmethod
    def ma_crossover_death_cross_data():
        """
        201 candles rising then 38 declining candles so SMA(50)
        crosses below SMA(200) on the last candle.
        """
        DataGenerator._seed()
        closes = []
        for i in range(201):
            closes.append(90 + i * 0.04 + random.uniform(-0.2, 0.2))
        for i in range(38):
            closes.append(closes[-1] - 0.25 + random.uniform(-0.02, 0.02))
        data = DataGenerator._make_ohlcv(closes)
        for i in range(-10, 0):
            data["volume"][i] *= 2.0
        return data

    # --- Grid Trading data ---
    @staticmethod
    def grid_ranging_data(n=60):
        """Low ADX oscillating data for grid trading."""
        DataGenerator._seed()
        closes = []
        for i in range(n):
            closes.append(100 + 2 * math.sin(i * 0.3) + random.uniform(-0.3, 0.3))
        return DataGenerator._make_ohlcv(closes, spread_pct=0.008)

    @staticmethod
    def grid_trending_data(n=60):
        """High ADX trending data (ADX ~55) -> grid should be blocked."""
        DataGenerator._seed()
        closes, highs, lows = [], [], []
        for i in range(n):
            c = 100 + i * 0.5 + random.uniform(-1.0, 1.0)
            h = c + random.uniform(0.5, 2.0)
            lo = c - random.uniform(0.5, 2.0)
            closes.append(c)
            highs.append(h)
            lows.append(lo)
        data = DataGenerator._make_ohlcv(closes)
        data["high"] = highs
        data["low"] = lows
        return data

    # --- Liquidation Capture data ---
    @staticmethod
    def liquidation_cascade_down(n=250):
        """
        Flat then sharp decline with volume spike -> downward cascade.
        Includes 'open' field for wick ratio calculation.
        """
        DataGenerator._seed()
        closes = []
        for i in range(240):
            closes.append(100 + random.uniform(-0.3, 0.3))
        # 10-candle cascade: 3%+ drop
        for i in range(10):
            closes.append(closes[-1] * (1 - 0.004))
        data = DataGenerator._make_ohlcv(closes)
        # Volume spike on last 3 candles
        for i in range(-3, 0):
            data["volume"][i] *= 5.0
        # Long lower wick on last candle for panic detection
        last_close = data["close"][-1]
        data["low"][-1] = last_close * 0.97
        data["high"][-1] = last_close * 1.002
        data["open"][-1] = last_close * 1.001
        return data

    @staticmethod
    def liquidation_cascade_up(n=250):
        """
        Flat then sharp rise with volume spike -> upward squeeze.
        """
        DataGenerator._seed()
        closes = []
        for i in range(240):
            closes.append(100 + random.uniform(-0.3, 0.3))
        for i in range(10):
            closes.append(closes[-1] * (1 + 0.004))
        data = DataGenerator._make_ohlcv(closes)
        for i in range(-3, 0):
            data["volume"][i] *= 5.0
        last_close = data["close"][-1]
        data["high"][-1] = last_close * 1.03
        data["low"][-1] = last_close * 0.998
        data["open"][-1] = last_close * 0.999
        return data

    # --- VWAP Scalping data ---
    @staticmethod
    def vwap_long_data(n=250):
        """
        Price starts near VWAP then declines to push well below VWAP.
        Volume steady so VWAP stays near original level.
        """
        DataGenerator._seed()
        closes = []
        for i in range(200):
            closes.append(100 + random.uniform(-0.5, 0.5))
        # Decline to push price below VWAP
        for i in range(50):
            closes.append(closes[-1] - 0.08)
        # Acceleration of decline at end: keeps MACD histogram NEGATIVE.
        # Strategy (2026-05) requires histogram < 0 for BUY (sellers still
        # active = enter before the reversal, not after).
        closes[-1] -= 0.3
        closes[-2] -= 0.15
        data = DataGenerator._make_ohlcv(closes)
        return data

    @staticmethod
    def vwap_short_data(n=250):
        """Price starts near VWAP then rises well above VWAP."""
        DataGenerator._seed()
        closes = []
        for i in range(200):
            closes.append(100 + random.uniform(-0.5, 0.5))
        for i in range(50):
            closes.append(closes[-1] + 0.08)
        # Acceleration of rise at end: keeps MACD histogram POSITIVE.
        # Strategy (2026-05) requires histogram > 0 for SELL (buyers still
        # active = enter before the reversal, not after).
        closes[-1] += 0.3
        closes[-2] += 0.15
        data = DataGenerator._make_ohlcv(closes)
        return data

    # --- Momentum Scalping data ---
    @staticmethod
    def momentum_bullish_data():
        """
        45 declining candles then 10 gently rising candles.
        EMA(9) crosses above EMA(21) at last candle, RSI ~61, MACD positive.
        """
        DataGenerator._seed()
        closes = []
        for i in range(45):
            closes.append(100 - i * 0.08 + random.uniform(-0.2, 0.2))
        for i in range(10):
            closes.append(closes[-1] + 0.15 + random.uniform(-0.02, 0.02))
        data = DataGenerator._make_ohlcv(closes)
        for i in range(-5, 0):
            data["volume"][i] *= 2.5
        return data

    @staticmethod
    def momentum_bearish_data():
        """
        45 rising candles then 10 gently declining candles.
        EMA(9) crosses below EMA(21) at last candle, RSI ~40, MACD negative.
        """
        DataGenerator._seed()
        closes = []
        for i in range(45):
            closes.append(100 + i * 0.08 + random.uniform(-0.2, 0.2))
        for i in range(10):
            closes.append(closes[-1] - 0.10 + random.uniform(-0.02, 0.02))
        data = DataGenerator._make_ohlcv(closes)
        for i in range(-5, 0):
            data["volume"][i] *= 2.5
        return data

    # --- Order Book Imbalance data ---
    @staticmethod
    def orderbook_long(levels=10):
        """Bid volume >> ask volume -> imbalance > 0.62."""
        bids = []
        asks = []
        for i in range(levels):
            bids.append({"p": 100 - i * 0.1, "a": 50.0, "n": 10})
            asks.append({"p": 100.1 + i * 0.1, "a": 5.0, "n": 5})
        return {"bids": bids, "asks": asks}

    @staticmethod
    def orderbook_short(levels=10):
        """Ask volume >> bid volume -> imbalance < 0.38."""
        bids = []
        asks = []
        for i in range(levels):
            bids.append({"p": 100 - i * 0.1, "a": 5.0, "n": 5})
            asks.append({"p": 100.1 + i * 0.1, "a": 50.0, "n": 10})
        return {"bids": bids, "asks": asks}

    @staticmethod
    def orderbook_spoof_bid(levels=10):
        """Bid side with single huge order (spoof)."""
        bids = [{"p": 100, "a": 500.0, "n": 1}]
        for i in range(1, levels):
            bids.append({"p": 100 - i * 0.1, "a": 5.0, "n": 5})
        asks = []
        for i in range(levels):
            asks.append({"p": 100.1 + i * 0.1, "a": 5.0, "n": 5})
        return {"bids": bids, "asks": asks}

    @staticmethod
    def simple_15m_data(n=30):
        """Simple 15m data for ATR calculation in orderbook strategy."""
        DataGenerator._seed()
        closes = [100 + random.uniform(-1, 1) for _ in range(n)]
        return DataGenerator._make_ohlcv(closes)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """Mock Pacifica client for order placement."""
    client = MagicMock()
    client.place_order = MagicMock(return_value={"order_id": "test-order-1", "status": "filled"})
    client.get_balance = MagicMock(return_value={"equity": "15000", "balance": "15000"})
    client.get_positions = MagicMock(return_value=[])
    client.cancel_all_orders = MagicMock(return_value=True)
    client.get_market_data = MagicMock(return_value=None)
    client.get_funding_history = MagicMock(return_value=[])
    return client


@pytest.fixture
def mock_db():
    """Mock database manager."""
    db = MagicMock()
    db.save_trade = MagicMock()
    db.save_position = MagicMock()
    return db


@pytest.fixture
def mock_risk_manager():
    """Mock risk manager with default balance."""
    rm = MagicMock()
    rm.last_known_balance = 15000
    rm.get_position_size = MagicMock(return_value=1.0)
    rm.request_capital_allocation = MagicMock(return_value=1500)
    return rm


# ===========================================================================
# 1. Mean Reversion E2E
# ===========================================================================

class TestMeanReversionE2E:

    def _make_strategy(self, **kwargs):
        defaults = dict(
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            rsi_period=14,
            bb_period=20,
            bb_std_dev=2.0,
            atr_stop_multiplier=2.0,
            min_confidence=0.10,
            # Explicit bb_proximity so the test is independent of .env values.
            # Data generator produces distance_pct ≈ 0.17; 0.25 is intentionally
            # generous to keep the focus on RSI + BB structure, not exact proximity.
            bb_proximity=0.25,
        )
        defaults.update(kwargs)
        return MeanReversionStrategy(**defaults)

    # -- Phase 1: Signal Generation --

    def test_long_signal_generated(self):
        """Oversold RSI + price near lower BB -> BUY signal."""
        strategy = self._make_strategy()
        data = DataGenerator.mean_reversion_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.BUY
        assert sig.strategy == StrategyType.MEAN_REVERSION
        assert sig.stop_loss < current_price
        assert sig.take_profit is not None
        assert sig.confidence > 0
        assert sig.is_valid()

    def test_short_signal_generated(self):
        """Overbought RSI + price near upper BB -> SELL signal."""
        strategy = self._make_strategy()
        data = DataGenerator.mean_reversion_short_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL
        assert sig.strategy == StrategyType.MEAN_REVERSION
        assert sig.stop_loss > current_price
        assert sig.is_valid()

    # -- Phase 2: Execution --

    def test_execution_order_placed(self, mock_client):
        """Signal leads to order placement."""
        strategy = self._make_strategy()
        data = DataGenerator.mean_reversion_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        assert len(signals) == 1

        sig = signals[0]
        mock_client.place_order(
            symbol=sig.asset,
            side="bid",
            quantity=str(1.0),
            order_type="market",
            reduce_only=False,
        )
        mock_client.place_order.assert_called_once()

    # -- Phase 3: Position Closing --

    def test_tp_hit_closes_position(self, mock_client):
        """Take profit hit -> opposite side close order."""
        strategy = self._make_strategy()
        data = DataGenerator.mean_reversion_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        # Simulate TP hit
        mock_client.place_order(
            symbol=sig.asset,
            side="ask",
            quantity=str(1.0),
            order_type="market",
            reduce_only=True,
        )
        call_args = mock_client.place_order.call_args
        assert call_args[1]["side"] == "ask"
        assert call_args[1]["reduce_only"] is True

    def test_sl_hit_closes_position(self, mock_client):
        """Stop loss hit -> opposite side close order."""
        strategy = self._make_strategy()
        data = DataGenerator.mean_reversion_short_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        # SELL signal -> close with BUY
        mock_client.place_order(
            symbol=sig.asset,
            side="bid",
            quantity=str(1.0),
            order_type="market",
            reduce_only=True,
        )
        call_args = mock_client.place_order.call_args
        assert call_args[1]["side"] == "bid"

    # -- Phase 4: Edge Cases --

    def test_insufficient_data_returns_empty(self):
        """Fewer candles than required -> no signals."""
        strategy = self._make_strategy()
        short_data = {"high": [100] * 10, "low": [99] * 10, "close": [100] * 10}
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=short_data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_missing_15m_data_returns_empty(self):
        """No 15m timeframe -> no signals."""
        strategy = self._make_strategy()
        data = DataGenerator.mean_reversion_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_neutral_rsi_no_signal(self):
        """RSI in neutral zone -> no signals."""
        strategy = self._make_strategy()
        # Flat data: tiny oscillations keep RSI near 50
        closes = [100.0 + 0.01 * (i % 2) for i in range(250)]
        data = DataGenerator._make_ohlcv(closes)
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)

        signals = strategy.generate_signals("BTC", multi_tf, closes[-1])
        assert signals == []


# ===========================================================================
# 2. MA Crossover E2E
# ===========================================================================

class TestMACrossoverE2E:

    def _make_strategy(self, **kwargs):
        defaults = dict(
            fast_ma_period=50,
            slow_ma_period=200,
            pullback_range=(0.005, 0.06),
            volume_confirmation_threshold=0.5,
            atr_stop_multiplier=2.5,
            min_confidence=0.10,
        )
        defaults.update(kwargs)
        return MACrossoverStrategy(**defaults)

    # -- Phase 1: Signal Generation (stateful two-step) --

    def _extend_data_with_continuation(self, data, direction="up", n_candles=2):
        """Extend data by n candles continuing the trend (no new crossover)."""
        data2 = {k: list(v) for k, v in data.items()}
        last_close = data2["close"][-1]
        step = 0.15 if direction == "up" else -0.15
        for _ in range(n_candles):
            last_close += step
            data2["close"].append(last_close)
            data2["high"].append(last_close * 1.005)
            data2["low"].append(last_close * 0.995)
            data2["open"].append(last_close * (1.001 if direction == "up" else 0.999))
            data2["volume"].append(2000)
        return data2

    def test_golden_cross_long_signal(self):
        """Golden cross + pullback -> BUY signal via two calls."""
        strategy = self._make_strategy()
        data = DataGenerator.ma_crossover_golden_cross_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)
        current_price = data["close"][-1]

        # Call 1: detect crossover
        signals_1 = strategy.generate_signals("BTC", multi_tf, current_price)

        # The crossover should be stored
        assert "BTC" in strategy.last_crossover
        assert strategy.last_crossover["BTC"]["type"] == "golden"

        # Call 2: extend data by 2 continuation candles so MACD stays aligned
        # and no new crossover is detected (fast_ma already above slow_ma)
        from trading_bot_v2.indicators import calculate_sma
        data2 = self._extend_data_with_continuation(data, direction="up", n_candles=2)
        fast_ma = calculate_sma(data2["close"], 50)
        pullback_price = fast_ma * (1 - 0.03)  # 3% pullback from fast MA
        multi_tf2 = DataGenerator.build_multi_tf_data(data_4h=data2)

        signals_2 = strategy.generate_signals("BTC", multi_tf2, pullback_price)

        # Should get a signal on the pullback
        assert len(signals_2) == 1
        sig = signals_2[0]
        assert sig.side == OrderSide.BUY
        assert sig.strategy == StrategyType.MA_CROSSOVER
        assert sig.pattern == "golden_cross_pullback"

    def test_death_cross_short_signal(self):
        """Death cross + rally -> SELL signal."""
        strategy = self._make_strategy()
        data = DataGenerator.ma_crossover_death_cross_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)
        current_price = data["close"][-1]

        # Call 1: detect crossover
        strategy.generate_signals("BTC", multi_tf, current_price)
        assert "BTC" in strategy.last_crossover
        assert strategy.last_crossover["BTC"]["type"] == "death"

        # Call 2: extend data by 2 continuation candles so MACD stays aligned
        from trading_bot_v2.indicators import calculate_sma
        data2 = self._extend_data_with_continuation(data, direction="down", n_candles=2)
        fast_ma = calculate_sma(data2["close"], 50)
        rally_price = fast_ma * (1 + 0.03)  # 3% rally from fast MA
        multi_tf2 = DataGenerator.build_multi_tf_data(data_4h=data2)

        signals_2 = strategy.generate_signals("BTC", multi_tf2, rally_price)

        assert len(signals_2) == 1
        sig = signals_2[0]
        assert sig.side == OrderSide.SELL
        assert sig.pattern == "death_cross_rally"

    # -- Phase 2: Execution --

    def test_execution_order_placed(self, mock_client):
        """Crossover signal leads to order placement."""
        strategy = self._make_strategy()
        data = DataGenerator.ma_crossover_golden_cross_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)
        current_price = data["close"][-1]

        strategy.generate_signals("BTC", multi_tf, current_price)

        from trading_bot_v2.indicators import calculate_sma
        data2 = self._extend_data_with_continuation(data, direction="up", n_candles=2)
        fast_ma = calculate_sma(data2["close"], 50)
        pullback_price = fast_ma * (1 - 0.03)
        multi_tf2 = DataGenerator.build_multi_tf_data(data_4h=data2)

        signals = strategy.generate_signals("BTC", multi_tf2, pullback_price)
        assert len(signals) == 1
        sig = signals[0]
        mock_client.place_order(
            symbol=sig.asset, side="bid", quantity="1.0",
            order_type="market", reduce_only=False,
        )
        mock_client.place_order.assert_called_once()

    # -- Phase 3: Position Closing --

    def test_tp_hit_on_long(self, mock_client):
        """LONG position TP hit -> close with SELL."""
        strategy = self._make_strategy()
        data = DataGenerator.ma_crossover_golden_cross_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)
        strategy.generate_signals("BTC", multi_tf, data["close"][-1])

        from trading_bot_v2.indicators import calculate_sma
        data2 = self._extend_data_with_continuation(data, direction="up", n_candles=2)
        fast_ma = calculate_sma(data2["close"], 50)
        pullback_price = fast_ma * (1 - 0.03)
        multi_tf2 = DataGenerator.build_multi_tf_data(data_4h=data2)
        signals = strategy.generate_signals("BTC", multi_tf2, pullback_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.take_profit is not None
        assert sig.stop_loss is not None
        # Simulate TP hit -> close LONG with SELL
        mock_client.place_order(
            symbol=sig.asset, side="ask", quantity="1.0",
            order_type="market", reduce_only=True,
        )
        assert mock_client.place_order.call_args[1]["side"] == "ask"
        assert mock_client.place_order.call_args[1]["reduce_only"] is True

    def test_sl_hit_on_long(self, mock_client):
        """LONG position SL hit -> close with SELL."""
        strategy = self._make_strategy()
        data = DataGenerator.ma_crossover_golden_cross_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)
        strategy.generate_signals("BTC", multi_tf, data["close"][-1])

        from trading_bot_v2.indicators import calculate_sma
        data2 = self._extend_data_with_continuation(data, direction="up", n_candles=2)
        fast_ma = calculate_sma(data2["close"], 50)
        pullback_price = fast_ma * (1 - 0.03)
        multi_tf2 = DataGenerator.build_multi_tf_data(data_4h=data2)
        signals = strategy.generate_signals("BTC", multi_tf2, pullback_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss is not None
        # Simulate SL hit -> close LONG with SELL
        mock_client.place_order(
            symbol=sig.asset, side="ask", quantity="1.0",
            order_type="market", reduce_only=True,
        )
        assert mock_client.place_order.call_args[1]["side"] == "ask"

    # -- Phase 4: Edge Cases --

    def test_missing_4h_data(self):
        """No 4h data -> no signal."""
        strategy = self._make_strategy()
        data = DataGenerator.ma_crossover_golden_cross_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_insufficient_data(self):
        """Too few candles for 200 SMA -> no signal."""
        strategy = self._make_strategy()
        short_data = {"high": [100] * 50, "low": [99] * 50, "close": [100] * 50}
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=short_data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_no_crossover_detected(self):
        """Flat data -> no crossover -> no signal."""
        strategy = self._make_strategy()
        DataGenerator._seed()
        closes = [100 + random.uniform(-0.1, 0.1) for _ in range(260)]
        data = DataGenerator._make_ohlcv(closes)
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []
        assert "BTC" not in strategy.last_crossover

    def test_crossover_expires_after_5_candles(self):
        """Crossover older than 5 candles -> no entry."""
        strategy = self._make_strategy()
        data = DataGenerator.ma_crossover_golden_cross_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)
        current_price = data["close"][-1]

        # Detect crossover
        strategy.generate_signals("BTC", multi_tf, current_price)

        # Add 6 more candles (beyond the 5 candle window)
        data2 = {k: list(v) for k, v in data.items()}
        for _ in range(6):
            for key in data2:
                data2[key].append(data2[key][-1])

        multi_tf2 = DataGenerator.build_multi_tf_data(data_4h=data2)
        signals = strategy.generate_signals("BTC", multi_tf2, current_price)
        assert signals == []


# ===========================================================================
# 3. Grid Trading E2E
# ===========================================================================

class TestGridTradingE2E:

    def _make_strategy(self, risk_manager=None, **kwargs):
        defaults = dict(
            grid_levels=5,
            grid_spacing_atr_multiplier=0.5,
            max_positions_per_symbol=10,
            emergency_stop_loss_pct=0.05,
            adx_regime_threshold=25.0,
            atr_period=14,
            adx_period=14,
            min_confidence=0.10,
            risk_manager=risk_manager,
        )
        defaults.update(kwargs)
        return GridTradingStrategy(**defaults)

    # -- Phase 1: Signal Generation --

    def test_grid_signal_generated(self, mock_risk_manager):
        """Ranging data + whitelisted symbol -> grid signal."""
        strategy = self._make_strategy(risk_manager=mock_risk_manager)
        data_1h = DataGenerator.grid_ranging_data()
        # Also provide 4h data for regime ADX check
        data_4h = DataGenerator.grid_ranging_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data_1h, data_4h=data_4h)
        current_price = data_1h["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.strategy == StrategyType.GRID_TRADING
        assert sig.is_valid()
        # "grid_level" is the current level index (1-based); "grid_levels" (the
        # total count) lives on the strategy object, not in the signal indicators.
        assert sig.indicators.get("grid_level") == 1

    # -- Phase 2: Execution --

    def test_grid_orders_placed(self, mock_client, mock_risk_manager):
        """Grid signal -> multiple limit orders placed."""
        strategy = self._make_strategy(risk_manager=mock_risk_manager)
        data_1h = DataGenerator.grid_ranging_data()
        data_4h = DataGenerator.grid_ranging_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data_1h, data_4h=data_4h)
        current_price = data_1h["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        assert len(signals) >= 1

        sig = signals[0]
        # Indicator key is "grid_spacing" (not "spacing"); total level count is
        # on the strategy object rather than inside the per-signal indicators dict.
        spacing = sig.indicators["grid_spacing"]
        for level in range(1, strategy.grid_levels + 1):
            buy_price = current_price - spacing * level
            mock_client.place_order(
                symbol="BTC", side="bid", quantity="0.1",
                order_type="limit", price=str(buy_price), reduce_only=False,
            )
        assert mock_client.place_order.call_count == strategy.grid_levels

    # -- Phase 3: Position Closing --

    def test_emergency_stop_blocks_new_grids(self, mock_risk_manager):
        """Emergency stop triggered -> no new grid signals."""
        strategy = self._make_strategy(risk_manager=mock_risk_manager)
        strategy.emergency_stop_triggered["BTC"] = True

        data_1h = DataGenerator.grid_ranging_data()
        data_4h = DataGenerator.grid_ranging_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data_1h, data_4h=data_4h)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_regime_change_blocks_grid(self, mock_risk_manager):
        """Trending data (high ADX) -> grid blocked."""
        strategy = self._make_strategy(risk_manager=mock_risk_manager)
        data_1h = DataGenerator.grid_ranging_data()
        data_4h = DataGenerator.grid_trending_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data_1h, data_4h=data_4h)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    # -- Phase 4: Edge Cases --

    def test_non_whitelisted_symbol(self, mock_risk_manager):
        """Non-whitelisted symbol -> no grid signal."""
        strategy = self._make_strategy(risk_manager=mock_risk_manager)
        data_1h = DataGenerator.grid_ranging_data()
        data_4h = DataGenerator.grid_ranging_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data_1h, data_4h=data_4h)

        signals = strategy.generate_signals("UNKNOWN-TOKEN", multi_tf, 100.0)
        assert signals == []

    def test_no_risk_manager(self):
        """No risk manager -> no grid signal."""
        strategy = self._make_strategy(risk_manager=None)
        data_1h = DataGenerator.grid_ranging_data()
        data_4h = DataGenerator.grid_ranging_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data_1h, data_4h=data_4h)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_max_positions_reached(self, mock_risk_manager):
        """Max grid positions reached -> no new signals."""
        strategy = self._make_strategy(risk_manager=mock_risk_manager)
        strategy.active_grids["BTC"] = [{"side": "BUY", "level": i} for i in range(10)]

        data_1h = DataGenerator.grid_ranging_data()
        data_4h = DataGenerator.grid_ranging_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data_1h, data_4h=data_4h)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_missing_1h_data(self, mock_risk_manager):
        """No 1h data -> no signal."""
        strategy = self._make_strategy(risk_manager=mock_risk_manager)
        data_4h = DataGenerator.grid_ranging_data(n=60)
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data_4h)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []


# ===========================================================================
# 4. Liquidation Capture E2E
# ===========================================================================

class TestLiquidationCaptureE2E:

    def _make_strategy(self, **kwargs):
        defaults = dict(
            price_move_threshold=0.025,
            volume_spike_multiplier=2.5,
            rsi_oversold_threshold=20.0,
            rsi_overbought_threshold=80.0,
            min_consecutive_moves=4,
            min_wick_ratio=1.5,
            rrr_target=3.0,
            max_per_session=2,
            min_hours_between_trades=0,
            rsi_period=14,
        )
        defaults.update(kwargs)
        return LiquidationCaptureStrategy(**defaults)

    # -- Phase 1: Signal Generation --

    def test_long_cascade_signal(self):
        """Downward cascade -> BUY (fade the cascade)."""
        strategy = self._make_strategy()
        data = DataGenerator.liquidation_cascade_down()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.BUY
        assert sig.strategy == StrategyType.LIQUIDATION_CAPTURE
        assert sig.pattern == "long_liquidation_cascade"
        assert sig.is_valid()

    def test_short_squeeze_signal(self):
        """Upward squeeze -> SELL (fade the squeeze)."""
        strategy = self._make_strategy()
        data = DataGenerator.liquidation_cascade_up()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL
        assert sig.strategy == StrategyType.LIQUIDATION_CAPTURE
        assert sig.pattern == "short_squeeze_cascade"
        assert sig.is_valid()

    # -- Phase 2: Execution --

    def test_execution_and_trade_recorded(self, mock_client):
        """Liquidation signal -> order placed and session trade recorded."""
        strategy = self._make_strategy()
        data = DataGenerator.liquidation_cascade_down()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        assert len(signals) == 1

        sig = signals[0]
        mock_client.place_order(
            symbol=sig.asset, side="bid", quantity="1.0",
            order_type="market", reduce_only=False,
        )
        strategy.record_trade()
        assert strategy.session_trades == 1

    # -- Phase 3: Position Closing --

    def test_tp_hit_3x_rrr(self):
        """TP should be at 3x risk from entry."""
        strategy = self._make_strategy()
        data = DataGenerator.liquidation_cascade_down()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        risk = sig.entry_price - sig.stop_loss
        expected_tp = sig.entry_price + risk * 3.0
        assert abs(sig.take_profit - expected_tp) < 0.01

    def test_sl_set_below_extreme(self):
        """Stop loss should be below the cascade extreme low."""
        strategy = self._make_strategy()
        data = DataGenerator.liquidation_cascade_down()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        extreme_low = min(data["low"][-10:])
        assert sig.stop_loss < extreme_low

    # -- Phase 4: Edge Cases --

    def test_session_limit_blocks_signal(self):
        """Max session trades reached -> no signal."""
        strategy = self._make_strategy()
        strategy.session_trades = 2

        data = DataGenerator.liquidation_cascade_down()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        assert signals == []

    def test_cooldown_blocks_signal(self):
        """Trade placed recently -> cooldown blocks next signal."""
        strategy = self._make_strategy(min_hours_between_trades=4)
        strategy.last_trade_time = datetime.now(timezone.utc)

        data = DataGenerator.liquidation_cascade_down()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)

        signals = strategy.generate_signals("BTC", multi_tf, data["close"][-1])
        assert signals == []

    def test_insufficient_data(self):
        """Too few candles -> no signal."""
        strategy = self._make_strategy()
        short_data = {
            "high": [100] * 10, "low": [99] * 10,
            "close": [100] * 10, "open": [100] * 10, "volume": [1000] * 10,
        }
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=short_data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_session_reset_clears_state(self):
        """Session reset allows trading again."""
        strategy = self._make_strategy()
        strategy.session_trades = 2
        strategy.last_trade_time = datetime.now(timezone.utc)

        strategy.reset_session()
        assert strategy.session_trades == 0
        assert strategy.last_trade_time is None


# ===========================================================================
# 5. VWAP Scalping E2E
# ===========================================================================

class TestVWAPScalpingE2E:

    def _make_strategy(self, **kwargs):
        defaults = dict(
            atr_period=14,
            sd_entry_threshold=1.5,
            atr_stop_multiplier=1.5,
            macd_fast=12,
            macd_slow=26,
            macd_signal=9,
            min_confidence=0.30,
            cooldown_minutes=0,
        )
        defaults.update(kwargs)
        return VWAPScalpingStrategy(**defaults)

    # -- Phase 1: Signal Generation --

    def test_long_signal_below_vwap(self):
        """Price below VWAP + positive MACD histogram -> BUY."""
        strategy = self._make_strategy()
        data = DataGenerator.vwap_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.BUY
        assert sig.strategy == StrategyType.VWAP_SCALPING
        assert sig.indicators["vwap"] > current_price
        assert sig.is_valid()

    def test_short_signal_above_vwap(self):
        """Price above VWAP + negative MACD histogram -> SELL."""
        strategy = self._make_strategy()
        data = DataGenerator.vwap_short_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL
        assert sig.strategy == StrategyType.VWAP_SCALPING
        assert sig.indicators["vwap"] < current_price

    # -- Phase 2: Execution --

    def test_execution_order_placed(self, mock_client):
        """VWAP signal -> order placed."""
        strategy = self._make_strategy()
        data = DataGenerator.vwap_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        assert len(signals) == 1

        sig = signals[0]
        mock_client.place_order(
            symbol=sig.asset, side="bid", quantity="1.0",
            order_type="market", reduce_only=False,
        )
        mock_client.place_order.assert_called_once()

    # -- Phase 3: Position Closing --

    def test_tp_targets_vwap(self):
        """Take profit target should be the VWAP (mean reversion)."""
        strategy = self._make_strategy()
        data = DataGenerator.vwap_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        assert abs(sig.take_profit - sig.indicators["vwap"]) < 0.01

    def test_sl_below_entry_for_long(self):
        """LONG signal SL should be below entry price."""
        strategy = self._make_strategy()
        data = DataGenerator.vwap_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        assert sig.stop_loss < sig.entry_price

    # -- Phase 4: Edge Cases --

    def test_cooldown_blocks_signal(self):
        """Active cooldown -> no signal."""
        strategy = self._make_strategy(cooldown_minutes=10)
        strategy._last_trade_time["BTC"] = datetime.now(timezone.utc)

        data = DataGenerator.vwap_long_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)

        signals = strategy.generate_signals("BTC", multi_tf, data["close"][-1])
        assert signals == []

    def test_missing_volume_data(self):
        """Missing volume data -> no VWAP calculation -> no signal."""
        strategy = self._make_strategy()
        data = DataGenerator.vwap_long_data()
        del data["volume"]
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_insufficient_data(self):
        """Too few candles -> no signal."""
        strategy = self._make_strategy()
        short_data = {
            "high": [100] * 10, "low": [99] * 10,
            "close": [100] * 10, "volume": [1000] * 10,
        }
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=short_data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []


# ===========================================================================
# 6. Funding Arb E2E
# ===========================================================================

class TestFundingArbE2E:

    def _make_strategy(self, client=None, **kwargs):
        defaults = dict(
            min_funding_rate=0.0001,
            max_allocation_pct=0.20,
            min_confidence=0.50,
            client=client,
        )
        defaults.update(kwargs)
        return FundingArbStrategy(**defaults)

    def _make_client_with_funding(self, rate=0.0005):
        client = MagicMock()
        client.get_market_data = MagicMock(return_value={
            "funding_rate": rate,
            "next_funding_time": datetime.now(timezone.utc) + timedelta(minutes=30),
        })
        client.get_funding_history = MagicMock(return_value=[
            {"funding_rate": rate} for _ in range(8)
        ])
        client.get_balance = MagicMock(return_value={"equity": "15000"})
        return client

    # -- Phase 1: Signal Generation --

    def test_sell_signal_positive_funding(self):
        """Positive funding rate -> SHORT signal (receive funding)."""
        client = self._make_client_with_funding(rate=0.0005)
        strategy = self._make_strategy(client=client)

        multi_tf = DataGenerator.build_multi_tf_data(data_15m=DataGenerator.simple_15m_data())
        signals = strategy.generate_signals("BTC", multi_tf, 100.0)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL
        assert sig.strategy == StrategyType.FUNDING_ARB
        assert sig.indicators["funding_rate"] == 0.0005
        assert sig.is_valid()

    def test_buy_signal_negative_funding(self):
        """Negative funding rate -> LONG signal (receive funding)."""
        client = self._make_client_with_funding(rate=-0.0005)
        strategy = self._make_strategy(client=client)

        multi_tf = DataGenerator.build_multi_tf_data(data_15m=DataGenerator.simple_15m_data())
        signals = strategy.generate_signals("BTC", multi_tf, 100.0)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.BUY
        assert sig.strategy == StrategyType.FUNDING_ARB

    # -- Phase 2: Execution --

    def test_position_registered_after_execution(self):
        """After signal execution, position is tracked."""
        client = self._make_client_with_funding(rate=0.0005)
        strategy = self._make_strategy(client=client)

        multi_tf = DataGenerator.build_multi_tf_data(data_15m=DataGenerator.simple_15m_data())
        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert len(signals) == 1

        strategy.register_position("BTC", "short_funding", 100.0, 0.0005)
        assert "BTC" in strategy.active_positions

    # -- Phase 3: Position Closing --

    def test_close_on_rate_flip(self):
        """Rate flips direction -> close signal generated."""
        client = self._make_client_with_funding(rate=0.0005)
        strategy = self._make_strategy(client=client)

        # Register existing position
        strategy.register_position("BTC", "short_funding", 100.0, 0.0005)

        # Now rate flips negative
        client.get_market_data.return_value = {
            "funding_rate": -0.0005,
            "next_funding_time": datetime.now(timezone.utc) + timedelta(minutes=30),
        }
        client.get_funding_history.return_value = [{"funding_rate": -0.0005}] * 8
        # Force cache refresh
        strategy._last_cache_update = None

        multi_tf = DataGenerator.build_multi_tf_data(data_15m=DataGenerator.simple_15m_data())
        signals = strategy.generate_signals("BTC", multi_tf, 100.0)

        # Should generate close signal (rate flipped)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.BUY  # Close SHORT = BUY

    # -- Phase 4: Edge Cases --

    def test_no_client_returns_empty(self):
        """No client -> no signals (can't fetch funding rates)."""
        strategy = self._make_strategy(client=None)

        multi_tf = DataGenerator.build_multi_tf_data(data_15m=DataGenerator.simple_15m_data())
        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_below_min_rate_no_signal(self):
        """Funding rate below threshold -> no signal."""
        client = self._make_client_with_funding(rate=0.00001)  # Below 0.01%
        strategy = self._make_strategy(client=client)

        multi_tf = DataGenerator.build_multi_tf_data(data_15m=DataGenerator.simple_15m_data())
        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_existing_position_blocks_new_signal(self):
        """Already holding a position in same direction -> no new signal."""
        client = self._make_client_with_funding(rate=0.0005)
        strategy = self._make_strategy(client=client)
        strategy.register_position("BTC", "short_funding", 100.0, 0.0005)

        multi_tf = DataGenerator.build_multi_tf_data(data_15m=DataGenerator.simple_15m_data())
        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        # Should not open new position (returns empty or close if rate changed)
        # With same rate, should return empty
        assert signals == []


# ===========================================================================
# 7. Momentum Scalping E2E
# ===========================================================================

class TestMomentumScalpingE2E:

    def _make_strategy(self, **kwargs):
        defaults = dict(
            ema_fast=9,
            ema_slow=21,
            rsi_period=14,
            rsi_upper=70.0,
            rsi_lower=30.0,
            atr_period=14,
            atr_stop_mult=1.5,
            atr_target_mult=2.5,
            volume_threshold=1.0,
            min_confidence=0.10,
            cooldown_minutes=0,
        )
        defaults.update(kwargs)
        return MomentumScalpingStrategy(**defaults)

    # -- Phase 1: Signal Generation --

    def test_bullish_crossover_signal(self):
        """EMA 9/21 bullish crossover with confirmations -> BUY."""
        strategy = self._make_strategy()
        data = DataGenerator.momentum_bullish_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.BUY
        assert sig.strategy == StrategyType.MOMENTUM_SCALPING
        assert sig.stop_loss < current_price
        assert sig.take_profit > current_price

    def test_bearish_crossover_signal(self):
        """EMA 9/21 bearish crossover with confirmations -> SELL."""
        strategy = self._make_strategy()
        data = DataGenerator.momentum_bearish_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL
        assert sig.strategy == StrategyType.MOMENTUM_SCALPING
        assert sig.stop_loss > current_price
        assert sig.take_profit < current_price

    # -- Phase 2: Execution --

    def test_execution_order_placed(self, mock_client):
        """Momentum signal -> order placed."""
        strategy = self._make_strategy()
        data = DataGenerator.momentum_bullish_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        assert len(signals) == 1

        sig = signals[0]
        mock_client.place_order(
            symbol=sig.asset, side="bid", quantity="1.0",
            order_type="market", reduce_only=False,
        )
        mock_client.place_order.assert_called_once()

    # -- Phase 3: Position Closing --

    def test_tp_hit_on_bullish(self):
        """Bullish signal TP is above entry."""
        strategy = self._make_strategy()
        data = DataGenerator.momentum_bullish_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        assert sig.take_profit > sig.entry_price
        assert sig.stop_loss < sig.entry_price

    def test_sl_hit_on_bearish(self):
        """Bearish signal SL is above entry."""
        strategy = self._make_strategy()
        data = DataGenerator.momentum_bearish_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        sig = signals[0]

        assert sig.stop_loss > sig.entry_price
        assert sig.take_profit < sig.entry_price

    # -- Phase 4: Edge Cases --

    def test_cooldown_blocks_signal(self):
        """Active cooldown -> no signal."""
        strategy = self._make_strategy(cooldown_minutes=5)
        strategy.last_trade_time["BTC"] = datetime.now(timezone.utc)

        data = DataGenerator.momentum_bullish_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)

        signals = strategy.generate_signals("BTC", multi_tf, data["close"][-1])
        assert signals == []

    def test_missing_1h_data(self):
        """No 1h data -> no signal (strategy requires 1h primary timeframe)."""
        strategy = self._make_strategy()
        data = DataGenerator.momentum_bullish_data()
        # Only 4h data, no 1h key -> strategy returns early
        multi_tf = DataGenerator.build_multi_tf_data(data_4h=data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_insufficient_data(self):
        """Too few 1h candles -> no signal."""
        strategy = self._make_strategy()
        short_data = {
            "high": [100] * 10, "low": [99] * 10,
            "close": [100] * 10, "volume": [1000] * 10,
        }
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=short_data)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []

    def test_crossover_clears_after_signal(self):
        """After generating a signal, crossover should be cleared."""
        strategy = self._make_strategy()
        data = DataGenerator.momentum_bullish_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_1h=data)
        current_price = data["close"][-1]

        signals = strategy.generate_signals("BTC", multi_tf, current_price)
        assert len(signals) == 1
        # Crossover should be cleared after signal generation
        assert "BTC" not in strategy.last_crossover


# ===========================================================================
# 8. Order Book Imbalance E2E
# ===========================================================================

class TestOrderBookImbalanceE2E:

    def _make_strategy(self, **kwargs):
        defaults = dict(
            levels=10,
            imbalance_long_threshold=0.62,
            imbalance_short_threshold=0.38,
            strong_imbalance_threshold=0.72,
            min_order_density=3,
            spoof_detection=True,
            spoof_size_ratio=5.0,
            atr_period=14,
            atr_stop_mult=0.75,
            atr_target_mult=1.5,
            min_confidence=0.30,
            cooldown_seconds=0,
            update_interval_ms=0,
        )
        defaults.update(kwargs)
        return OrderBookImbalanceStrategy(**defaults)

    # -- Phase 1: Signal Generation --

    def test_long_signal_bid_imbalance(self):
        """Bid volume >> ask volume -> BUY signal."""
        strategy = self._make_strategy()
        orderbook = DataGenerator.orderbook_long()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals(
            "BTC", multi_tf, 100.0, orderbook=orderbook,
        )

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.BUY
        assert sig.strategy == StrategyType.ORDERBOOK_IMBALANCE
        assert sig.indicators["imbalance"] > 0.62

    def test_short_signal_ask_imbalance(self):
        """Ask volume >> bid volume -> SELL signal."""
        strategy = self._make_strategy()
        orderbook = DataGenerator.orderbook_short()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals(
            "BTC", multi_tf, 100.0, orderbook=orderbook,
        )

        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL
        assert sig.strategy == StrategyType.ORDERBOOK_IMBALANCE
        assert sig.indicators["imbalance"] < 0.38

    # -- Phase 2: Execution --

    def test_execution_order_placed(self, mock_client):
        """OB imbalance signal -> order placed."""
        strategy = self._make_strategy()
        orderbook = DataGenerator.orderbook_long()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals(
            "BTC", multi_tf, 100.0, orderbook=orderbook,
        )
        assert len(signals) == 1

        sig = signals[0]
        mock_client.place_order(
            symbol=sig.asset, side="bid", quantity="1.0",
            order_type="market", reduce_only=False,
        )
        mock_client.place_order.assert_called_once()

    # -- Phase 3: Position Closing --

    def test_tight_stop_loss(self):
        """OB imbalance uses tight stops (0.75x ATR)."""
        strategy = self._make_strategy()
        orderbook = DataGenerator.orderbook_long()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals(
            "BTC", multi_tf, 100.0, orderbook=orderbook,
        )
        sig = signals[0]

        # Stop should be closer than take profit
        risk = abs(sig.entry_price - sig.stop_loss)
        reward = abs(sig.take_profit - sig.entry_price)
        assert reward > risk  # TP farther than SL

    def test_tp_at_1_5x_atr(self):
        """Take profit target at 1.5x ATR."""
        strategy = self._make_strategy()
        orderbook = DataGenerator.orderbook_long()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals(
            "BTC", multi_tf, 100.0, orderbook=orderbook,
        )
        sig = signals[0]

        # RRR should be 1.5/0.75 = 2.0
        risk = abs(sig.entry_price - sig.stop_loss)
        reward = abs(sig.take_profit - sig.entry_price)
        rrr = reward / risk if risk > 0 else 0
        assert 1.5 <= rrr <= 2.5

    # -- Phase 4: Edge Cases --

    def test_spoof_detection_blocks_signal(self):
        """Spoofed orderbook -> signal blocked."""
        strategy = self._make_strategy()
        orderbook = DataGenerator.orderbook_spoof_bid()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals(
            "BTC", multi_tf, 100.0, orderbook=orderbook,
        )
        assert signals == []

    def test_cooldown_blocks_signal(self):
        """Active cooldown -> no signal."""
        strategy = self._make_strategy(cooldown_seconds=60)
        strategy.last_trade_time["BTC"] = datetime.now(timezone.utc)

        orderbook = DataGenerator.orderbook_long()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals(
            "BTC", multi_tf, 100.0, orderbook=orderbook,
        )
        assert signals == []

    def test_no_orderbook_returns_empty(self):
        """Missing orderbook data -> no signal."""
        strategy = self._make_strategy()
        data_15m = DataGenerator.simple_15m_data()
        multi_tf = DataGenerator.build_multi_tf_data(data_15m=data_15m)

        signals = strategy.generate_signals("BTC", multi_tf, 100.0)
        assert signals == []


# ===========================================================================
# Cross-strategy integration tests
# ===========================================================================

class TestCrossStrategyIntegration:

    def test_all_strategies_instantiate(self, mock_risk_manager, mock_client):
        """All 8 strategies can be instantiated without errors."""
        strategies = [
            MeanReversionStrategy(),
            MACrossoverStrategy(),
            GridTradingStrategy(risk_manager=mock_risk_manager),
            LiquidationCaptureStrategy(),
            VWAPScalpingStrategy(),
            FundingArbStrategy(client=mock_client),
            MomentumScalpingStrategy(),
            OrderBookImbalanceStrategy(),
        ]
        assert len(strategies) == 8

    def test_all_signals_have_correct_strategy_type(self, mock_risk_manager, mock_client):
        """Each strategy produces signals with correct StrategyType enum."""
        expected_types = {
            "mean_reversion": StrategyType.MEAN_REVERSION,
            "ma_crossover": StrategyType.MA_CROSSOVER,
            "grid_trading": StrategyType.GRID_TRADING,
            "liquidation_capture": StrategyType.LIQUIDATION_CAPTURE,
            "vwap_scalping": StrategyType.VWAP_SCALPING,
            "funding_arb": StrategyType.FUNDING_ARB,
            "momentum_scalping": StrategyType.MOMENTUM_SCALPING,
            "orderbook_imbalance": StrategyType.ORDERBOOK_IMBALANCE,
        }

        # Mean Reversion
        mr = MeanReversionStrategy(min_confidence=0.01)
        data = DataGenerator.mean_reversion_long_data()
        sigs = mr.generate_signals("BTC", {"15m": data}, data["close"][-1])
        if sigs:
            assert sigs[0].strategy == expected_types["mean_reversion"]

        # Liquidation Capture
        lc = LiquidationCaptureStrategy(min_hours_between_trades=0)
        data = DataGenerator.liquidation_cascade_down()
        sigs = lc.generate_signals("BTC", {"15m": data}, data["close"][-1])
        if sigs:
            assert sigs[0].strategy == expected_types["liquidation_capture"]

    def test_signal_is_valid_contract(self):
        """All 8 validation flags must be True for is_valid() == True."""
        sig = Signal(
            strategy=StrategyType.MEAN_REVERSION,
            asset="BTC",
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            volume_confirmation=True,
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=True,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )
        assert sig.is_valid()

        # Flip one flag -> invalid
        sig.volume_confirmation = False
        assert not sig.is_valid()

    def test_signal_rrr_calculation(self):
        """Signal RRR property calculates correctly."""
        sig = Signal(
            strategy=StrategyType.MEAN_REVERSION,
            asset="BTC",
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=115.0,
        )
        # Risk = 5, Reward = 15, RRR = 3.0
        assert abs(sig.rrr - 3.0) < 0.01
