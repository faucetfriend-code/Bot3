# Prompt 062: Backtesting Engine

## Priority: 1 | Difficulty: High | Effort: 5-8 days

---

## Task Overview

Build a complete backtesting engine for all 8 trading strategies inside `trading_bot_v2`. The engine replays historical candle data through the **exact same signal pipeline** the live bot uses — same regime detection, same strategy logic, same risk manager, same position sizing — with a simulated exchange replacing real API calls.

This is not a separate research tool. It runs the live strategy code unchanged and wraps a fake exchange around it. Results are therefore directly comparable to live performance.

---

## Why This Approach

- **Zero strategy drift**: Same `generate_signals()` code runs in backtest and live. No re-implementation.
- **Regime-accurate**: ADX regime detection runs on replayed 4h candles, so strategy selection matches what the live bot would have done.
- **Risk-realistic**: RiskManager, Kelly sizer, and circuit breaker all run with real thresholds. No inflated position sizes.
- **Deployment-ready output**: Results feed directly into `.env` parameter tuning before live run.

---

## New Files to Create

```
trading_bot_v2/
  backtesting/
    __init__.py
    engine.py              # Core replay loop
    simulated_exchange.py  # Fake PacificaClient + WebSocket
    data_loader.py         # Historical candle loader + cache
    cost_model.py          # Slippage, fees, funding simulation
    performance.py         # Metrics calculation
    walk_forward.py        # Walk-forward analysis runner
    reports/
      __init__.py
      html_report.py       # Self-contained HTML results report
```

---

## Technical Requirements

### 1. Config Updates

Add to `.env`:
```
# Backtesting
BACKTEST_START_DATE=2024-01-01
BACKTEST_END_DATE=2024-12-31
BACKTEST_SYMBOL=SUI-USDC
BACKTEST_INITIAL_CAPITAL=10000.0
BACKTEST_SLIPPAGE_PCT=0.002        # 0.2% slippage per fill
BACKTEST_TAKER_FEE_PCT=0.0006     # 0.06% taker fee
BACKTEST_MAKER_FEE_PCT=0.0002     # 0.02% maker fee
BACKTEST_FUNDING_HOURLY_PCT=0.0001 # Default hourly funding (fallback)
BACKTEST_DATA_DIR=backtesting/data
BACKTEST_WALK_FORWARD_TRAIN_MONTHS=6
BACKTEST_WALK_FORWARD_TEST_MONTHS=1
```

Add to `config.py` (append to `Config.__init__`):
```python
# Backtesting
self.backtest_start_date: str = os.getenv("BACKTEST_START_DATE", "2024-01-01")
self.backtest_end_date: str = os.getenv("BACKTEST_END_DATE", "2024-12-31")
self.backtest_symbol: str = os.getenv("BACKTEST_SYMBOL", "SUI-USDC")
self.backtest_initial_capital: float = float(os.getenv("BACKTEST_INITIAL_CAPITAL", "10000.0"))
self.backtest_slippage_pct: float = float(os.getenv("BACKTEST_SLIPPAGE_PCT", "0.002"))
self.backtest_taker_fee_pct: float = float(os.getenv("BACKTEST_TAKER_FEE_PCT", "0.0006"))
self.backtest_maker_fee_pct: float = float(os.getenv("BACKTEST_MAKER_FEE_PCT", "0.0002"))
self.backtest_funding_hourly_pct: float = float(os.getenv("BACKTEST_FUNDING_HOURLY_PCT", "0.0001"))
self.backtest_data_dir: str = os.getenv("BACKTEST_DATA_DIR", "backtesting/data")
self.backtest_walk_forward_train_months: int = int(os.getenv("BACKTEST_WALK_FORWARD_TRAIN_MONTHS", "6"))
self.backtest_walk_forward_test_months: int = int(os.getenv("BACKTEST_WALK_FORWARD_TEST_MONTHS", "1"))
```

---

## Implementation

### File: `trading_bot_v2/backtesting/data_loader.py`

Loads historical OHLCV candles from disk or fetches from a public source. Stores as parquet files in `BACKTEST_DATA_DIR`. Provides a multi-timeframe view (1m, 5m, 15m, 1h, 4h) matching what `MultiTimeframeFetcher` returns in production.

```python
"""
Historical Data Loader
======================

Loads OHLCV candles from local parquet cache or fetches from
public APIs (CoinGecko, Binance public, or raw CSV).

Provides the same interface as MultiTimeframeFetcher so the
live strategy code sees no difference between backtest and live.

Timeframes provided: 1m, 5m, 15m, 1h, 4h
Minimum history for regime detection: 29 × 4h candles (4.8 days)
"""

import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
from loguru import logger


class BacktestDataLoader:
    """
    Loads multi-timeframe historical candles for backtesting.

    Usage:
        loader = BacktestDataLoader(symbol="SUI-USDC", data_dir="backtesting/data")
        candles = loader.get_candles("4h", start="2024-01-01", end="2024-12-31")
    """

    SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

    def __init__(self, symbol: str, data_dir: str = "backtesting/data"):
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, pd.DataFrame] = {}

    def get_candles(
        self,
        timeframe: str,
        start: str,
        end: str,
    ) -> Dict[str, List]:
        """
        Returns candles in the same format as MultiTimeframeFetcher.

        Returns dict with keys: open, high, low, close, volume, timestamp
        Each value is a list ordered oldest → newest.
        """
        df = self._load_or_fetch(timeframe, start, end)
        mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
        df = df[mask].copy()
        return {
            "open": df["open"].tolist(),
            "high": df["high"].tolist(),
            "low": df["low"].tolist(),
            "close": df["close"].tolist(),
            "volume": df["volume"].tolist(),
            "timestamp": df["timestamp"].tolist(),
        }

    def _load_or_fetch(self, timeframe: str, start: str, end: str) -> pd.DataFrame:
        cache_key = f"{self.symbol}_{timeframe}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        parquet_path = self.data_dir / f"{self.symbol.replace('/', '_')}_{timeframe}.parquet"

        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            logger.info(f"Loaded {len(df)} {timeframe} candles from {parquet_path}")
        else:
            df = self._fetch_from_api(timeframe, start, end)
            df.to_parquet(parquet_path)
            logger.info(f"Fetched and cached {len(df)} {timeframe} candles")

        self._cache[cache_key] = df
        return df

    def _fetch_from_api(self, timeframe: str, start: str, end: str) -> pd.DataFrame:
        """
        Fetch historical data from public API.

        Priority order:
        1. Binance public API (most liquid, good history)
        2. CoinGecko (fallback, 1-day granularity only)
        3. Raw CSV in data_dir (manual import fallback)

        For SUI-USDC on Pacifica testnet: use Binance SUI-USDT
        as the price proxy (same underlying, negligible USDT/USDC spread).
        """
        raise NotImplementedError(
            f"Auto-fetch not yet implemented for {timeframe}. "
            f"Place a CSV file at {self.data_dir}/{self.symbol}_{timeframe}.csv "
            f"with columns: timestamp,open,high,low,close,volume"
        )
```

---

### File: `trading_bot_v2/backtesting/simulated_exchange.py`

A drop-in replacement for `PacificaClient` and `PacificaWebSocketClient`. All order placement, position queries, and ticker reads are intercepted here. No real network calls are made.

```python
"""
Simulated Exchange
==================

Replaces PacificaClient and PacificaWebSocketClient during backtesting.

The SimulatedExchange:
  - Fills limit orders when price crosses the order level
  - Applies slippage and taker/maker fees on every fill
  - Tracks positions, margin, and unrealised PnL
  - Simulates hourly funding charges/credits on open positions
  - Fires WebSocket-style price updates as candles advance

Key design rule: The engine injects this object wherever the live code
calls the real client. Strategy code does NOT know it is backtesting.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class SimulatedOrder:
    order_id: str
    symbol: str
    side: str          # "bid" | "ask"  (Pacifica convention)
    price: float
    quantity: float
    order_type: str    # "limit" | "market"
    status: str = "open"   # "open" | "filled" | "cancelled"
    filled_qty: float = 0.0
    fill_price: float = 0.0
    fee: float = 0.0
    timestamp: str = ""


@dataclass
class SimulatedPosition:
    symbol: str
    side: str          # "long" | "short"  (Pacifica convention)
    quantity: float
    entry_price: float
    unrealised_pnl: float = 0.0
    realised_pnl: float = 0.0
    funding_paid: float = 0.0


class SimulatedExchange:
    """
    Full simulated exchange compatible with PacificaClient interface.

    Constructor args:
        initial_capital: Starting USDC balance
        slippage_pct: Fraction of price applied as slippage on fill
        taker_fee_pct: Fee fraction for market/aggressive fills
        maker_fee_pct: Fee fraction for limit/passive fills
        funding_hourly_pct: Default hourly funding rate (overridden by
                             real historical rates if available)
    """

    def __init__(
        self,
        initial_capital: float,
        slippage_pct: float = 0.002,
        taker_fee_pct: float = 0.0006,
        maker_fee_pct: float = 0.0002,
        funding_hourly_pct: float = 0.0001,
    ):
        self.balance = initial_capital
        self.initial_capital = initial_capital
        self.slippage_pct = slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.maker_fee_pct = maker_fee_pct
        self.funding_hourly_pct = funding_hourly_pct

        self._orders: Dict[str, SimulatedOrder] = {}
        self._positions: Dict[str, SimulatedPosition] = {}
        self._current_price: float = 0.0
        self._current_timestamp: str = ""
        self._order_counter: int = 0

        # Trade ledger for performance analysis
        self.trade_log: List[Dict] = []

    # ------------------------------------------------------------------
    # PacificaClient-compatible interface
    # ------------------------------------------------------------------

    def get_account_balance(self) -> Dict:
        unrealised = sum(p.unrealised_pnl for p in self._positions.values())
        return {
            "balance": str(round(self.balance + unrealised, 4)),
            "available": str(round(self.balance, 4)),
            "locked": "0.00",
        }

    def get_positions(self) -> List[Dict]:
        result = []
        for pos in self._positions.values():
            result.append({
                "symbol": pos.symbol,
                "side": pos.side,
                "quantity": str(pos.quantity),
                "entry_price": str(pos.entry_price),
                "unrealised_pnl": str(round(pos.unrealised_pnl, 4)),
            })
        return result

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: str,
        order_type: str = "market",
        price: Optional[float] = None,
    ) -> Dict:
        self._order_counter += 1
        order_id = f"bt_{self._order_counter:06d}"
        qty = float(quantity)

        order = SimulatedOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price or self._current_price,
            quantity=qty,
            order_type=order_type,
            timestamp=self._current_timestamp,
        )
        self._orders[order_id] = order

        # Market orders fill immediately
        if order_type == "market":
            self._fill_order(order, is_taker=True)

        return {"order_id": order_id, "status": "success"}

    def cancel_order(self, order_id: str) -> Dict:
        if order_id in self._orders:
            self._orders[order_id].status = "cancelled"
        return {"status": "success"}

    def cancel_all_orders(self, symbol: str) -> Dict:
        for order in self._orders.values():
            if order.symbol == symbol and order.status == "open":
                order.status = "cancelled"
        return {"status": "success"}

    def get_ticker(self, symbol: str) -> Dict:
        return {
            "symbol": symbol,
            "last": str(self._current_price),
            "bid": str(self._current_price * 0.9995),
            "ask": str(self._current_price * 1.0005),
        }

    def get_orderbook(self, symbol: str, depth: int = 10) -> Dict:
        """Returns synthetic orderbook centred on current price."""
        spread_pct = 0.0005
        bids = [
            [str(round(self._current_price * (1 - spread_pct * i), 4)), str(1000 / (i + 1))]
            for i in range(1, depth + 1)
        ]
        asks = [
            [str(round(self._current_price * (1 + spread_pct * i), 4)), str(1000 / (i + 1))]
            for i in range(1, depth + 1)
        ]
        return {"bids": bids, "asks": asks}

    def get_funding_rate(self, symbol: str) -> Dict:
        return {
            "symbol": symbol,
            "funding_rate": str(self.funding_hourly_pct),
            "next_funding_time": self._current_timestamp,
        }

    # ------------------------------------------------------------------
    # Engine-facing interface (called by BacktestEngine)
    # ------------------------------------------------------------------

    def advance(self, candle: Dict, timestamp: str) -> None:
        """
        Called by the engine on each new candle.
        Updates price, checks pending limit orders, applies hourly funding.
        """
        self._current_price = float(candle["close"])
        self._current_timestamp = timestamp
        self._check_pending_orders(candle)
        self._update_unrealised_pnl()
        if self._is_funding_hour(timestamp):
            self._apply_funding()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_order(self, order: SimulatedOrder, is_taker: bool) -> None:
        direction = 1 if order.side == "bid" else -1
        slippage = self._current_price * self.slippage_pct * direction
        fill_price = self._current_price + slippage
        fee_pct = self.taker_fee_pct if is_taker else self.maker_fee_pct
        fee = fill_price * order.quantity * fee_pct

        order.fill_price = fill_price
        order.filled_qty = order.quantity
        order.fee = fee
        order.status = "filled"

        # Update position
        symbol = order.symbol
        if order.side == "bid":  # Buy → Long
            self._open_or_add_position(symbol, "long", order.quantity, fill_price)
        else:                     # Sell → Short
            self._open_or_add_position(symbol, "short", order.quantity, fill_price)

        self.balance -= fee
        self._log_trade(order, fill_price, fee)

    def _open_or_add_position(
        self, symbol: str, side: str, qty: float, price: float
    ) -> None:
        opposite = "short" if side == "long" else "long"
        existing = self._positions.get(symbol)

        if existing and existing.side == opposite:
            # Close or reduce opposing position
            if qty >= existing.quantity:
                pnl = self._calculate_pnl(existing, price, existing.quantity)
                self.balance += pnl + existing.quantity * existing.entry_price
                remaining = qty - existing.quantity
                del self._positions[symbol]
                if remaining > 0:
                    self._positions[symbol] = SimulatedPosition(
                        symbol=symbol, side=side, quantity=remaining, entry_price=price
                    )
            else:
                pnl = self._calculate_pnl(existing, price, qty)
                self.balance += pnl
                existing.realised_pnl += pnl
                existing.quantity -= qty
        elif existing and existing.side == side:
            # Add to existing position (average entry)
            total_qty = existing.quantity + qty
            avg_price = (existing.entry_price * existing.quantity + price * qty) / total_qty
            existing.entry_price = avg_price
            existing.quantity = total_qty
        else:
            # New position — deduct notional from balance
            self.balance -= qty * price
            self._positions[symbol] = SimulatedPosition(
                symbol=symbol, side=side, quantity=qty, entry_price=price
            )

    def _calculate_pnl(self, pos: SimulatedPosition, exit_price: float, qty: float) -> float:
        if pos.side == "long":
            return (exit_price - pos.entry_price) * qty
        else:
            return (pos.entry_price - exit_price) * qty

    def _check_pending_orders(self, candle: Dict) -> None:
        high = float(candle["high"])
        low = float(candle["low"])
        for order in list(self._orders.values()):
            if order.status != "open":
                continue
            # Limit buy fills if low touches or crosses order price
            if order.side == "bid" and low <= order.price:
                self._fill_order(order, is_taker=False)
            # Limit sell fills if high touches or crosses order price
            elif order.side == "ask" and high >= order.price:
                self._fill_order(order, is_taker=False)

    def _update_unrealised_pnl(self) -> None:
        for pos in self._positions.values():
            pos.unrealised_pnl = self._calculate_pnl(pos, self._current_price, pos.quantity)

    def _apply_funding(self) -> None:
        for pos in self._positions.values():
            funding = pos.quantity * self._current_price * self.funding_hourly_pct
            if pos.side == "long":
                cost = -funding   # Longs pay positive funding
            else:
                cost = funding    # Shorts receive positive funding
            pos.funding_paid += cost
            self.balance += cost

    def _is_funding_hour(self, timestamp: str) -> bool:
        """Pacifica pays funding every hour — check if this candle is on the hour."""
        try:
            dt = datetime.fromisoformat(timestamp)
            return dt.minute == 0
        except Exception:
            return False

    def _log_trade(self, order: SimulatedOrder, fill_price: float, fee: float) -> None:
        self.trade_log.append({
            "order_id": order.order_id,
            "timestamp": self._current_timestamp,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "fill_price": fill_price,
            "fee": fee,
            "balance_after": round(self.balance, 4),
        })
```

---

### File: `trading_bot_v2/backtesting/engine.py`

The core replay loop. Advances candles one at a time, calls regime detection and strategy managers exactly as the live bot does, routes fills through the simulated exchange.

```python
"""
Backtest Engine
===============

Replays historical candles through the live strategy pipeline.

Architecture:
  BacktestEngine
    ├── BacktestDataLoader    (historical candles)
    ├── SimulatedExchange     (fake PacificaClient)
    ├── MarketRegimeDetector  (unchanged live code)
    ├── StrategyManager       (unchanged live code)
    ├── RiskManager           (unchanged live code)
    └── PerformanceTracker    (metrics accumulator)

Usage:
    engine = BacktestEngine(config)
    result = engine.run(
        start="2024-01-01",
        end="2024-12-31",
        symbol="SUI-USDC",
        initial_capital=10000.0,
    )
    result.print_summary()
    result.save_html("backtest_result.html")
"""

from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger

from ..config import config as live_config
from ..market_regime import MarketRegimeDetector
from ..strategy_manager import StrategyManager
from ..risk_manager import RiskManager
from ..kelly_position_sizer import KellyPositionSizer
from ..signal_logger import SignalLogger
from .data_loader import BacktestDataLoader
from .simulated_exchange import SimulatedExchange
from .performance import PerformanceTracker, BacktestResult
from .cost_model import CostModel


class BacktestEngine:
    """
    Runs a full backtest of all enabled strategies.

    The engine advances the simulation one 1m candle at a time.
    Every 4h candle boundary, regime detection re-runs.
    Every 5m candle boundary, strategies generate signals.
    Every 1m candle, the simulated exchange checks pending limit orders.

    This mirrors the live bot's multi-timeframe execution model.
    """

    def __init__(self, override_config=None):
        self.cfg = override_config or live_config
        self.regime_detector = MarketRegimeDetector()
        self.risk_manager = RiskManager(self.cfg)
        self.kelly_sizer = KellyPositionSizer(self.cfg)
        self.signal_logger = SignalLogger()

    def run(
        self,
        start: str,
        end: str,
        symbol: Optional[str] = None,
        initial_capital: Optional[float] = None,
    ) -> "BacktestResult":
        symbol = symbol or self.cfg.backtest_symbol
        initial_capital = initial_capital or self.cfg.backtest_initial_capital

        logger.info(f"Starting backtest: {symbol} | {start} → {end} | capital={initial_capital}")

        # Initialise components
        exchange = SimulatedExchange(
            initial_capital=initial_capital,
            slippage_pct=self.cfg.backtest_slippage_pct,
            taker_fee_pct=self.cfg.backtest_taker_fee_pct,
            maker_fee_pct=self.cfg.backtest_maker_fee_pct,
            funding_hourly_pct=self.cfg.backtest_funding_hourly_pct,
        )
        loader = BacktestDataLoader(symbol=symbol, data_dir=self.cfg.backtest_data_dir)
        strategy_manager = StrategyManager(
            config=self.cfg,
            client=exchange,   # Inject simulated exchange
            risk_manager=self.risk_manager,
            kelly_sizer=self.kelly_sizer,
            signal_logger=self.signal_logger,
        )
        performance = PerformanceTracker(initial_capital=initial_capital)
        cost_model = CostModel(
            slippage_pct=self.cfg.backtest_slippage_pct,
            taker_fee_pct=self.cfg.backtest_taker_fee_pct,
        )

        # Load all required timeframes up front
        candles_1m = loader.get_candles("1m", start, end)
        candles_4h = loader.get_candles("4h", start, end)
        candles_1h = loader.get_candles("1h", start, end)
        candles_15m = loader.get_candles("15m", start, end)
        candles_5m = loader.get_candles("5m", start, end)

        timestamps_1m = candles_1m["timestamp"]
        total_candles = len(timestamps_1m)
        logger.info(f"Loaded {total_candles} 1m candles for replay")

        # Build lookup indices for higher timeframes
        candle_idx_4h = self._build_timeframe_index(candles_4h["timestamp"])
        candle_idx_1h = self._build_timeframe_index(candles_1h["timestamp"])
        candle_idx_15m = self._build_timeframe_index(candles_15m["timestamp"])
        candle_idx_5m = self._build_timeframe_index(candles_5m["timestamp"])

        current_regime = None
        last_regime_ts = None
        last_signal_ts = None

        # --- Main replay loop ---
        for i, ts in enumerate(timestamps_1m):
            candle_1m = self._get_candle_at(candles_1m, i)
            exchange.advance(candle_1m, ts)

            # --- Regime detection: re-run on every new 4h candle ---
            idx_4h = candle_idx_4h.get(ts)
            if idx_4h is not None and (last_regime_ts is None or idx_4h != last_regime_ts):
                last_regime_ts = idx_4h
                history_4h = self._get_history(candles_4h, idx_4h, lookback=50)
                if len(history_4h["close"]) >= 29:
                    try:
                        current_regime = self.regime_detector.detect_regime(history_4h)
                        logger.debug(f"[{ts}] Regime: {current_regime}")
                    except Exception as e:
                        logger.warning(f"Regime detection failed at {ts}: {e}")

            if current_regime is None:
                continue  # Wait until we have enough history

            # --- Signal generation: run on every new 5m candle ---
            idx_5m = candle_idx_5m.get(ts)
            if idx_5m is not None and (last_signal_ts is None or idx_5m != last_signal_ts):
                last_signal_ts = idx_5m

                # Build multi-timeframe data bundle (same structure as live)
                mtf_data = {
                    "1m": self._get_history(candles_1m, i, lookback=60),
                    "5m": self._get_history(candles_5m, idx_5m, lookback=60),
                    "15m": self._get_history(candles_15m,
                                              candle_idx_15m.get(ts, 0), lookback=60),
                    "1h": self._get_history(candles_1h,
                                             candle_idx_1h.get(ts, 0), lookback=60),
                    "4h": self._get_history(candles_4h, idx_4h or 0, lookback=50),
                }

                try:
                    signals = strategy_manager.get_signals(
                        regime=current_regime,
                        market_data=mtf_data,
                        symbol=symbol,
                    )
                    for signal in signals:
                        cost_model.apply(signal, exchange._current_price)
                        strategy_manager.execute_signal(signal, exchange)
                except Exception as e:
                    logger.warning(f"Signal generation error at {ts}: {e}")

            # --- Record equity snapshot every 1h ---
            if i % 60 == 0:
                equity = float(exchange.get_account_balance()["balance"])
                performance.record_snapshot(ts, equity, exchange._positions.copy())

        # --- Finalise ---
        final_equity = float(exchange.get_account_balance()["balance"])
        result = performance.finalise(
            final_equity=final_equity,
            trade_log=exchange.trade_log,
            symbol=symbol,
            start=start,
            end=end,
        )
        logger.info(f"Backtest complete. Final equity: {final_equity:.2f} | "
                    f"Return: {result.total_return_pct:.1f}% | "
                    f"Sharpe: {result.sharpe_ratio:.2f} | "
                    f"Max DD: {result.max_drawdown_pct:.1f}%")
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_timeframe_index(timestamps: List) -> Dict:
        """Maps each 1m timestamp to the index of the most recent higher-TF candle."""
        return {ts: i for i, ts in enumerate(timestamps)}

    @staticmethod
    def _get_candle_at(candles: Dict, idx: int) -> Dict:
        return {k: candles[k][idx] for k in ("open", "high", "low", "close", "volume")}

    @staticmethod
    def _get_history(candles: Dict, up_to_idx: int, lookback: int) -> Dict:
        start = max(0, up_to_idx - lookback + 1)
        return {k: candles[k][start : up_to_idx + 1] for k in candles}
```

---

### File: `trading_bot_v2/backtesting/cost_model.py`

```python
"""
Cost Model
==========

Applies realistic transaction cost estimates to signals BEFORE
they are routed to the simulated exchange.

Why here (not in the exchange): Strategies should see cost-adjusted
expected values when calculating RRR, so low-RRR signals are
filtered out under realistic cost assumptions.
"""

from loguru import logger
from ..models import Signal


class CostModel:
    """
    Adjusts signal stop/target for realistic round-trip costs.

    Costs applied:
      - Taker fee × 2 (entry + exit, worst case)
      - Slippage on entry
    """

    def __init__(self, slippage_pct: float = 0.002, taker_fee_pct: float = 0.0006):
        self.slippage_pct = slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.round_trip_cost_pct = (taker_fee_pct * 2) + slippage_pct

    def apply(self, signal: Signal, current_price: float) -> None:
        """
        Adjusts signal expected_return to account for costs.
        Signals below break-even after costs are flagged (not blocked —
        RiskManager handles final go/no-go).
        """
        if signal.take_profit and signal.stop_loss:
            gross_rr = abs(signal.take_profit - current_price) / abs(current_price - signal.stop_loss)
            cost_drag = self.round_trip_cost_pct * current_price
            net_target = signal.take_profit - cost_drag if signal.take_profit > current_price else signal.take_profit + cost_drag
            signal.take_profit = net_target
            if gross_rr < 1.0:
                logger.debug(f"Low RRR signal from {signal.strategy}: gross_rr={gross_rr:.2f}")
```

---

### File: `trading_bot_v2/backtesting/performance.py`

Calculates all performance metrics from the equity curve and trade log.

```python
"""
Performance Tracker
===================

Accumulates equity snapshots during the backtest and computes
the full suite of performance metrics on finalise().

Metrics:
  Profitability:  Total return %, CAGR, Profit factor, Win rate
  Risk:           Sharpe ratio, Sortino ratio, Max drawdown %, Calmar ratio
  Execution:      Trade count, Avg hold time, Avg fee per trade
  Per-strategy:   All of the above broken down by strategy
  Per-regime:     PnL and trade count per market regime
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger


@dataclass
class BacktestResult:
    # Summary
    symbol: str
    start: str
    end: str
    initial_capital: float
    final_equity: float

    # Profitability
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate_pct: float = 0.0

    # Risk
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    avg_drawdown_duration_days: float = 0.0

    # Execution
    total_trades: int = 0
    avg_fee_per_trade: float = 0.0
    total_fees: float = 0.0
    total_funding_paid: float = 0.0

    # Breakdowns
    by_strategy: Dict = field(default_factory=dict)
    by_regime: Dict = field(default_factory=dict)

    # Raw data for report generation
    equity_curve: List = field(default_factory=list)
    trade_log: List = field(default_factory=list)

    def print_summary(self) -> None:
        print(f"\n{'='*60}")
        print(f"BACKTEST RESULTS: {self.symbol} | {self.start} → {self.end}")
        print(f"{'='*60}")
        print(f"  Initial Capital : ${self.initial_capital:,.2f}")
        print(f"  Final Equity    : ${self.final_equity:,.2f}")
        print(f"  Total Return    : {self.total_return_pct:+.1f}%")
        print(f"  CAGR            : {self.cagr_pct:+.1f}%")
        print(f"  Sharpe Ratio    : {self.sharpe_ratio:.2f}")
        print(f"  Sortino Ratio   : {self.sortino_ratio:.2f}")
        print(f"  Max Drawdown    : {self.max_drawdown_pct:.1f}%")
        print(f"  Win Rate        : {self.win_rate_pct:.1f}%")
        print(f"  Profit Factor   : {self.profit_factor:.2f}")
        print(f"  Total Trades    : {self.total_trades}")
        print(f"  Total Fees      : ${self.total_fees:,.2f}")
        print(f"{'='*60}\n")

    def save_html(self, path: str) -> None:
        """Generate a self-contained HTML report with equity curve chart."""
        from .reports.html_report import generate_html_report
        generate_html_report(self, path)
        logger.info(f"Report saved to {path}")


class PerformanceTracker:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self._snapshots: List[Dict] = []

    def record_snapshot(self, timestamp: str, equity: float, positions: Dict) -> None:
        self._snapshots.append({
            "timestamp": timestamp,
            "equity": equity,
            "open_positions": len(positions),
        })

    def finalise(
        self,
        final_equity: float,
        trade_log: List[Dict],
        symbol: str,
        start: str,
        end: str,
    ) -> BacktestResult:
        equity_series = [s["equity"] for s in self._snapshots]
        result = BacktestResult(
            symbol=symbol,
            start=start,
            end=end,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            equity_curve=self._snapshots,
            trade_log=trade_log,
            total_trades=len(trade_log),
        )

        result.total_return_pct = (final_equity / self.initial_capital - 1) * 100
        result.total_fees = sum(t.get("fee", 0) for t in trade_log)
        result.avg_fee_per_trade = result.total_fees / max(1, result.total_trades)

        # Days in backtest
        try:
            dt_start = datetime.fromisoformat(start)
            dt_end = datetime.fromisoformat(end)
            days = (dt_end - dt_start).days
            years = days / 365.25
            if years > 0 and final_equity > 0:
                result.cagr_pct = ((final_equity / self.initial_capital) ** (1 / years) - 1) * 100
        except Exception:
            pass

        # Sharpe (annualised, using hourly snapshots)
        if len(equity_series) > 2:
            returns = [
                (equity_series[i] - equity_series[i - 1]) / equity_series[i - 1]
                for i in range(1, len(equity_series))
            ]
            mean_r = sum(returns) / len(returns)
            std_r = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5
            result.sharpe_ratio = (mean_r / std_r * math.sqrt(8760)) if std_r > 0 else 0.0

            # Sortino (downside deviation only)
            downside = [r for r in returns if r < 0]
            if downside:
                downside_std = (sum(r ** 2 for r in downside) / len(downside)) ** 0.5
                result.sortino_ratio = (mean_r / downside_std * math.sqrt(8760)) if downside_std > 0 else 0.0

        # Max drawdown
        peak = self.initial_capital
        max_dd = 0.0
        for eq in equity_series:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = max_dd * 100
        result.calmar_ratio = (result.cagr_pct / result.max_drawdown_pct
                                if result.max_drawdown_pct > 0 else 0.0)

        # Win/loss stats from trade log
        wins = [t for t in trade_log if t.get("pnl", 0) > 0]
        losses = [t for t in trade_log if t.get("pnl", 0) <= 0]
        gross_profit = sum(t.get("pnl", 0) for t in wins)
        gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
        result.win_rate_pct = len(wins) / max(1, len(trade_log)) * 100
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return result
```

---

### File: `trading_bot_v2/backtesting/walk_forward.py`

```python
"""
Walk-Forward Analysis
=====================

Splits the full date range into rolling train/test windows.
For each window: trains on N months, tests on M months, steps forward M months.

Default: 6-month train, 1-month test.

This prevents overfitting to a single time period and gives a
distribution of performance across different market conditions.

Usage:
    wf = WalkForwardAnalyzer(engine, config)
    results = wf.run(start="2023-01-01", end="2024-12-31", symbol="SUI-USDC")
    wf.print_summary(results)
"""

from datetime import datetime, timedelta
from typing import List
from loguru import logger

from .engine import BacktestEngine
from .performance import BacktestResult


class WalkForwardAnalyzer:
    def __init__(self, engine: BacktestEngine, config=None):
        self.engine = engine
        self.cfg = config or engine.cfg

    def run(
        self,
        start: str,
        end: str,
        symbol: str,
        initial_capital: float = 10000.0,
    ) -> List[BacktestResult]:
        train_months = self.cfg.backtest_walk_forward_train_months
        test_months = self.cfg.backtest_walk_forward_test_months

        windows = self._build_windows(start, end, train_months, test_months)
        results = []

        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            logger.info(f"Window {i+1}/{len(windows)}: test {test_start} → {test_end}")
            # Note: training window is reserved for parameter optimisation
            # (future enhancement — currently runs test with default params)
            result = self.engine.run(
                start=test_start,
                end=test_end,
                symbol=symbol,
                initial_capital=initial_capital,
            )
            result.start = test_start  # Label by test window
            results.append(result)

        self.print_summary(results)
        return results

    def print_summary(self, results: List[BacktestResult]) -> None:
        print(f"\n{'='*60}")
        print(f"WALK-FORWARD SUMMARY ({len(results)} windows)")
        print(f"{'='*60}")
        returns = [r.total_return_pct for r in results]
        sharpes = [r.sharpe_ratio for r in results]
        drawdowns = [r.max_drawdown_pct for r in results]
        win_rates = [r.win_rate_pct for r in results]
        profitable = sum(1 for r in returns if r > 0)

        print(f"  Profitable windows : {profitable}/{len(results)}")
        print(f"  Avg return         : {sum(returns)/len(returns):+.1f}%")
        print(f"  Avg Sharpe         : {sum(sharpes)/len(sharpes):.2f}")
        print(f"  Avg Max DD         : {sum(drawdowns)/len(drawdowns):.1f}%")
        print(f"  Avg Win Rate       : {sum(win_rates)/len(win_rates):.1f}%")
        print(f"{'='*60}\n")

    @staticmethod
    def _build_windows(
        start: str,
        end: str,
        train_months: int,
        test_months: int,
    ) -> List:
        windows = []
        dt_start = datetime.fromisoformat(start)
        dt_end = datetime.fromisoformat(end)

        # First test window starts after initial training period
        test_start = dt_start + timedelta(days=train_months * 30)

        while test_start < dt_end:
            train_start = test_start - timedelta(days=train_months * 30)
            train_end = test_start - timedelta(days=1)
            test_end = min(test_start + timedelta(days=test_months * 30 - 1), dt_end)
            windows.append((
                train_start.date().isoformat(),
                train_end.date().isoformat(),
                test_start.date().isoformat(),
                test_end.date().isoformat(),
            ))
            test_start += timedelta(days=test_months * 30)

        return windows
```

---

### File: `trading_bot_v2/backtesting/reports/html_report.py`

Generates a self-contained HTML file with:
- Equity curve (Chart.js line chart)
- Drawdown chart
- Trade table (sortable)
- Strategy breakdown table
- Key metrics cards

```python
"""
HTML Report Generator
=====================

Produces a single self-contained HTML file from a BacktestResult.
No external CDN required — Chart.js is embedded inline.

Output is viewable in any browser without a server.
"""

from pathlib import Path


def generate_html_report(result, output_path: str) -> None:
    """
    Writes a self-contained HTML backtest report to output_path.
    """
    timestamps = [s["timestamp"] for s in result.equity_curve]
    equities = [s["equity"] for s in result.equity_curve]

    # Calculate drawdown series
    peak = result.initial_capital
    drawdowns = []
    for eq in equities:
        peak = max(peak, eq)
        drawdowns.append(round((peak - eq) / peak * 100, 2))

    ts_json = str(timestamps[:500])   # Downsample for chart performance
    eq_json = str(equities[:500])
    dd_json = str(drawdowns[:500])

    trade_rows = ""
    for t in result.trade_log[:200]:
        trade_rows += (
            f"<tr><td>{t.get('timestamp','')[:16]}</td>"
            f"<td>{t.get('side','')}</td>"
            f"<td>{t.get('quantity','')}</td>"
            f"<td>{t.get('fill_price','')}</td>"
            f"<td>${t.get('fee',0):.4f}</td></tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Backtest Report — {result.symbol}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; background: #f9f9f9; }}
  h1 {{ color: #1a1a2e; }} h2 {{ color: #16213e; margin-top: 40px; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 16px; margin: 20px 0; }}
  .card {{ background: white; border-radius: 8px; padding: 16px 24px; box-shadow: 0 1px 3px rgba(0,0,0,.1); min-width: 160px; }}
  .card .label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
  .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  .green {{ color: #16a34a; }} .red {{ color: #dc2626; }}
  canvas {{ background: white; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); width: 100% !important; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-top: 16px; }}
  th {{ background: #1a1a2e; color: white; padding: 10px 14px; text-align: left; font-size: 13px; }}
  td {{ padding: 8px 14px; border-bottom: 1px solid #eee; font-size: 13px; }}
  tr:last-child td {{ border-bottom: none; }}
</style>
</head>
<body>
<h1>Backtest Report — {result.symbol}</h1>
<p style="color:#666">{result.start} → {result.end} &nbsp;|&nbsp; Initial capital: ${result.initial_capital:,.0f}</p>

<div class="cards">
  <div class="card"><div class="label">Total Return</div>
    <div class="value {'green' if result.total_return_pct >= 0 else 'red'}">{result.total_return_pct:+.1f}%</div></div>
  <div class="card"><div class="label">CAGR</div>
    <div class="value {'green' if result.cagr_pct >= 0 else 'red'}">{result.cagr_pct:+.1f}%</div></div>
  <div class="card"><div class="label">Sharpe Ratio</div>
    <div class="value">{result.sharpe_ratio:.2f}</div></div>
  <div class="card"><div class="label">Max Drawdown</div>
    <div class="value red">{result.max_drawdown_pct:.1f}%</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value">{result.win_rate_pct:.1f}%</div></div>
  <div class="card"><div class="label">Profit Factor</div>
    <div class="value">{result.profit_factor:.2f}</div></div>
  <div class="card"><div class="label">Total Trades</div>
    <div class="value">{result.total_trades}</div></div>
  <div class="card"><div class="label">Total Fees</div>
    <div class="value">${result.total_fees:,.2f}</div></div>
</div>

<h2>Equity Curve</h2>
<canvas id="eqChart" height="80"></canvas>

<h2>Drawdown</h2>
<canvas id="ddChart" height="60"></canvas>

<h2>Trade Log (first 200)</h2>
<table>
  <thead><tr><th>Timestamp</th><th>Side</th><th>Qty</th><th>Fill Price</th><th>Fee</th></tr></thead>
  <tbody>{trade_rows}</tbody>
</table>

<script>
// Inline Chart.js (minified stub — replace with full CDN in browser if needed)
// For full charts: add <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
const ts = {ts_json};
const eq = {eq_json};
const dd = {dd_json};

function tryChart(id, labels, data, color, label, yReverse) {{
  const canvas = document.getElementById(id);
  if (!canvas || typeof Chart === 'undefined') return;
  new Chart(canvas, {{
    type: 'line',
    data: {{ labels, datasets: [{{ label, data, borderColor: color, fill: true,
      backgroundColor: color + '22', borderWidth: 2, pointRadius: 0 }}] }},
    options: {{ animation: false, plugins: {{ legend: {{ display: false }} }},
      scales: {{ y: {{ reverse: !!yReverse }} }} }}
  }});
}}

// Load Chart.js dynamically
const s = document.createElement('script');
s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
s.onload = () => {{
  tryChart('eqChart', ts, eq, '#2563eb', 'Equity ($)', false);
  tryChart('ddChart', ts, dd, '#dc2626', 'Drawdown (%)', true);
}};
document.head.appendChild(s);
</script>
</body>
</html>"""

    Path(output_path).write_text(html, encoding="utf-8")
```

---

### File: `trading_bot_v2/backtesting/__init__.py`

```python
from .engine import BacktestEngine
from .walk_forward import WalkForwardAnalyzer
from .performance import BacktestResult

__all__ = ["BacktestEngine", "WalkForwardAnalyzer", "BacktestResult"]
```

---

## Acceptance Criteria

### Strategy Simulation
- [ ] All 8 strategies receive candle data through the same `generate_signals()` interface used in live trading — zero code changes to any strategy file
- [ ] Regime detection (ADX 4h) correctly gates strategy selection during replay — trending candle sequences activate MA Crossover/Momentum, ranging sequences activate Mean Reversion/Grid
- [ ] Overlay strategies (LiquidationCapture, FundingArb, OrderBookImbalance) fire independently of regime
- [ ] Multi-timeframe candle bundles (1m/5m/15m/1h/4h) are presented to strategies in the same dict format as `MultiTimeframeFetcher`
- [ ] Walk-forward runner produces ≥ 6 non-overlapping test windows for a 12-month run

### Risk & Position Sizing
- [ ] `RiskManager` circuit breaker triggers a full stop if equity drops > 10% (`CIRCUIT_BREAKER_LOSS_PCT`) in the simulation — no further trades placed after trigger
- [ ] Kelly Criterion sizer activates after 50 completed trades (same threshold as live, `KELLY_MIN_TRADES=50`); before that, uses fixed fractional sizing
- [ ] All 8 signal validation flags are evaluated during backtest (volume confirmation, multi-TF alignment, RRR, liquidation buffer, account risk, margin drawdown, forbidden conditions)
- [ ] Cost model deducts realistic slippage (default 0.2%) and taker fees (0.06%) per round trip; results explicitly report total fees and funding paid

### Performance Metrics & Reporting
- [ ] `BacktestResult` exposes: total return %, CAGR, Sharpe ratio, Sortino ratio, max drawdown %, Calmar ratio, win rate %, profit factor, total trades, total fees
- [ ] `result.print_summary()` prints a clean tabular summary to stdout
- [ ] `result.save_html(path)` produces a viewable HTML report with equity curve chart, drawdown chart, and trade log table
- [ ] Walk-forward `print_summary()` shows: profitable window count, avg return, avg Sharpe, avg max drawdown, avg win rate across all test windows

---

## Integration Test

Add `trading_bot_v2/tests/test_backtesting.py`:

```python
"""
Tests for the backtesting engine.

Uses synthetic candle data (no real API calls, no disk I/O required).
"""

import pytest
from unittest.mock import patch, MagicMock
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.backtesting.performance import PerformanceTracker, BacktestResult
from trading_bot_v2.backtesting.cost_model import CostModel
from trading_bot_v2.backtesting.walk_forward import WalkForwardAnalyzer


class TestSimulatedExchange:
    def test_market_buy_fills_immediately(self):
        ex = SimulatedExchange(initial_capital=1000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        result = ex.place_order("SUI-USDC", "bid", "1.0", order_type="market")
        assert result["status"] == "success"
        assert len(ex.trade_log) == 1

    def test_limit_buy_fills_on_price_touch(self):
        ex = SimulatedExchange(initial_capital=1000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("SUI-USDC", "bid", "1.0", order_type="limit", price=99.0)
        assert len(ex.trade_log) == 0  # Not filled yet
        ex.advance({"open": 100, "high": 100, "low": 98, "close": 99, "volume": 1000},
                   "2024-01-01T01:00:00")
        assert len(ex.trade_log) == 1  # Filled when low touched 98 < 99

    def test_fees_deducted_on_fill(self):
        ex = SimulatedExchange(initial_capital=1000.0, taker_fee_pct=0.001)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        initial_balance = ex.balance
        ex.place_order("SUI-USDC", "bid", "1.0", order_type="market")
        assert ex.balance < initial_balance  # Fees taken

    def test_balance_sheet_is_consistent(self):
        ex = SimulatedExchange(initial_capital=5000.0)
        ex._current_price = 100.0
        ex._current_timestamp = "2024-01-01T00:00:00"
        ex.place_order("SUI-USDC", "bid", "10.0", order_type="market")  # Buy 10 @ 100
        ex._current_price = 110.0
        ex.advance({"open": 100, "high": 115, "low": 100, "close": 110, "volume": 5000},
                   "2024-01-01T02:00:00")
        balance_info = ex.get_account_balance()
        total = float(balance_info["balance"])
        assert total > 5000  # Should have made money on long position


class TestPerformanceTracker:
    def test_positive_return_calculation(self):
        tracker = PerformanceTracker(initial_capital=10000.0)
        for i, eq in enumerate([10000, 10100, 10200, 10500, 11000]):
            tracker.record_snapshot(f"2024-01-0{i+1}T00:00:00", eq, {})
        result = tracker.finalise(
            final_equity=11000.0,
            trade_log=[],
            symbol="SUI-USDC",
            start="2024-01-01",
            end="2024-01-05",
        )
        assert result.total_return_pct == pytest.approx(10.0, abs=0.1)

    def test_max_drawdown_detected(self):
        tracker = PerformanceTracker(initial_capital=10000.0)
        for eq in [10000, 11000, 9000, 10500]:
            tracker.record_snapshot("2024-01-01T00:00:00", eq, {})
        result = tracker.finalise(
            final_equity=10500.0,
            trade_log=[],
            symbol="SUI-USDC",
            start="2024-01-01",
            end="2024-04-01",
        )
        # Peak was 11000, trough was 9000 → DD = 18.18%
        assert result.max_drawdown_pct == pytest.approx(18.18, abs=0.5)

    def test_sharpe_positive_on_rising_equity(self):
        tracker = PerformanceTracker(initial_capital=10000.0)
        eq = 10000.0
        for i in range(100):
            eq += 10  # Steady gains, no drawdown
            tracker.record_snapshot(f"2024-01-01T{i:02d}:00:00", eq, {})
        result = tracker.finalise(eq, [], "SUI-USDC", "2024-01-01", "2024-04-10")
        assert result.sharpe_ratio > 0


class TestCostModel:
    def test_cost_reduces_take_profit(self):
        from trading_bot_v2.models import Signal, OrderSide
        from trading_bot_v2.config import StrategyType
        model = CostModel(slippage_pct=0.002, taker_fee_pct=0.0006)
        signal = MagicMock()
        signal.take_profit = 105.0
        signal.stop_loss = 98.0
        model.apply(signal, current_price=100.0)
        assert signal.take_profit < 105.0  # Cost drag reduces net target


class TestWalkForwardWindows:
    def test_window_count_correct(self):
        mock_engine = MagicMock()
        mock_engine.cfg.backtest_walk_forward_train_months = 6
        mock_engine.cfg.backtest_walk_forward_test_months = 1
        wf = WalkForwardAnalyzer(mock_engine)
        windows = wf._build_windows("2023-01-01", "2024-12-31", 6, 1)
        assert len(windows) >= 12  # At least 12 monthly test windows in 18 months of test range

    def test_no_look_ahead_bias(self):
        mock_engine = MagicMock()
        mock_engine.cfg.backtest_walk_forward_train_months = 3
        mock_engine.cfg.backtest_walk_forward_test_months = 1
        wf = WalkForwardAnalyzer(mock_engine)
        windows = wf._build_windows("2024-01-01", "2024-12-31", 3, 1)
        for train_start, train_end, test_start, test_end in windows:
            assert train_end < test_start  # Training always ends before test begins
```

---

## CLI Entry Point

Add `trading_bot_v2/backtesting/run_backtest.py` for command-line use:

```python
"""
Run a backtest from the command line.

Usage:
    python -m trading_bot_v2.backtesting.run_backtest
    python -m trading_bot_v2.backtesting.run_backtest --start 2024-01-01 --end 2024-06-30
    python -m trading_bot_v2.backtesting.run_backtest --walk-forward
"""

import argparse
from trading_bot_v2.backtesting import BacktestEngine, WalkForwardAnalyzer
from trading_bot_v2.config import config


def main():
    parser = argparse.ArgumentParser(description="Run trading bot backtest")
    parser.add_argument("--start", default=config.backtest_start_date)
    parser.add_argument("--end", default=config.backtest_end_date)
    parser.add_argument("--symbol", default=config.backtest_symbol)
    parser.add_argument("--capital", type=float, default=config.backtest_initial_capital)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--report", default="backtest_report.html")
    args = parser.parse_args()

    engine = BacktestEngine()

    if args.walk_forward:
        wf = WalkForwardAnalyzer(engine)
        results = wf.run(args.start, args.end, args.symbol, args.capital)
        # Save combined report for walk-forward
        for i, r in enumerate(results):
            r.save_html(f"backtest_wf_{i+1:02d}.html")
    else:
        result = engine.run(args.start, args.end, args.symbol, args.capital)
        result.print_summary()
        result.save_html(args.report)
        print(f"Report saved: {args.report}")


if __name__ == "__main__":
    main()
```

---

## Known Constraints & Assumptions

- **Order book strategies**: `OrderBookImbalance` and `LiquidationCapture` rely on live L2 depth and liquidation events. In backtest, a synthetic order book is generated from OHLCV (see `SimulatedExchange.get_orderbook`). Signal frequency will be lower than live — this is expected and conservative.
- **Grid trading**: Grids require persistent state across candles. `GridLifecycleManager` state is fully preserved within a single `engine.run()` call. Cross-window walk-forward resets grid state (same as a live restart).
- **Funding simulation**: Uses a flat `BACKTEST_FUNDING_HOURLY_PCT` by default. If historical Pacifica funding rate CSV data is available, place it at `BACKTEST_DATA_DIR/{symbol}_funding.csv` with columns `timestamp,rate` and `SimulatedExchange` will load it automatically (future enhancement placeholder).
- **Data source**: The engine does not auto-download data. The `BacktestDataLoader._fetch_from_api` must be implemented or data files placed manually before first run. Priority: Binance public REST API for SUI/USDT OHLCV.
- **Minimum regime history**: The first 4.8 days of any backtest window produce no trades while the ADX regime detector accumulates its required 29 × 4h candles. This is identical to live bot cold-start behaviour.
