"""
Backtest Engine
===============

Replays historical candles through the live strategy pipeline.

Architecture:
  BacktestEngine
    +-- BacktestDataLoader       (historical candles)
    +-- SimulatedExchange        (fake PacificaClient)
    +-- StrategyManager          (unchanged live code, handles regime internally)
    +-- RiskManager              (unchanged live code)
    +-- PerformanceTracker       (metrics accumulator)

Usage:
    engine = BacktestEngine()
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

from ..models import OrderSide, StrategyType
from ..strategy_manager import StrategyManager
from ..risk_manager import RiskManager
from .data_loader import BacktestDataLoader
from .simulated_exchange import SimulatedExchange
from .performance import PerformanceTracker, BacktestResult
from .cost_model import CostModel


class BacktestEngine:
    """
    Runs a full backtest of all enabled strategies.

    The engine advances the simulation one 5m candle at a time (matching the
    live bot's primary signal-generation cadence). Regime detection is handled
    internally by StrategyManager using the 4h/1h slice of each bundle.

    Every 60 candles (5h) an equity snapshot is recorded.
    """

    def __init__(self, override_config=None):
        # Import here to avoid circular imports and to allow override_config
        from ..config import config as live_config
        self.cfg = override_config or live_config

    def run(
        self,
        start: str,
        end: str,
        symbol: Optional[str] = None,
        initial_capital: Optional[float] = None,
    ) -> BacktestResult:
        symbol = symbol or self.cfg.backtest_symbol
        initial_capital = initial_capital or self.cfg.backtest_initial_capital

        logger.info(f"Starting backtest: {symbol} | {start} -> {end} | capital={initial_capital}")

        # --- Build components ---
        exchange = SimulatedExchange(
            initial_capital=initial_capital,
            slippage_pct=self.cfg.backtest_slippage_pct,
            taker_fee_pct=self.cfg.backtest_taker_fee_pct,
            maker_fee_pct=self.cfg.backtest_maker_fee_pct,
            funding_hourly_pct=self.cfg.backtest_funding_hourly_pct,
        )
        loader = BacktestDataLoader(symbol=symbol, data_dir=self.cfg.backtest_data_dir)
        risk_manager = RiskManager(client=exchange)
        strategy_manager = StrategyManager(
            risk_manager=risk_manager,
            client=exchange,
        )
        performance = PerformanceTracker(initial_capital=initial_capital)
        cost_model = CostModel(
            slippage_pct=self.cfg.backtest_slippage_pct,
            taker_fee_pct=self.cfg.backtest_taker_fee_pct,
        )

        # --- Load candles ---
        candles = {
            tf: loader.get_candles(tf, start, end)
            for tf in ("1m", "5m", "15m", "1h", "4h")
        }

        timestamps_5m = candles["5m"]["timestamp"]
        total = len(timestamps_5m)
        logger.info(f"Loaded {total} 5m candles for replay")

        # Build reverse-lookup: 5m timestamp -> index in each higher timeframe
        idx_map = {
            tf: {ts: i for i, ts in enumerate(candles[tf]["timestamp"])}
            for tf in ("1m", "15m", "1h", "4h")
        }

        # --- Replay loop (one 5m candle at a time) ---
        last_4h_idx = None

        for i, ts in enumerate(timestamps_5m):
            # Advance the simulated exchange price to this candle's close
            candle_5m = self._candle_at(candles["5m"], i)
            exchange.advance(candle_5m, ts)

            # --- Build multi-timeframe bundles ---
            i_15m = self._nearest_idx(idx_map["15m"], ts, i, 3)
            i_1h  = self._nearest_idx(idx_map["1h"],  ts, i, 12)
            i_4h  = self._nearest_idx(idx_map["4h"],  ts, i, 48)
            i_1m  = self._nearest_idx(idx_map["1m"],  ts, i, 1)

            # Regime / structure timeframes (required by StrategyManager)
            multi_tf_data = {
                "15m": self._history(candles["15m"], i_15m, 60),
                "1h":  self._history(candles["1h"],  i_1h,  60),
                "4h":  self._history(candles["4h"],  i_4h,  50),
            }

            # Execution timeframes (optional, for precise entry)
            execution_tf_data = {
                "5m": self._history(candles["5m"], i, 60),
                "1m": self._history(candles["1m"], i_1m, 60),
            }

            # Skip until we have enough 4h history for regime detection (29 candles)
            if i_4h < 28:
                continue

            # Advance simulated time so strategy cooldowns use candle timestamps
            try:
                sim_dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                sim_dt = None
            strategy_manager.set_sim_time(sim_dt)

            # --- Generate signals ---
            try:
                signals = strategy_manager.generate_signals_for_market(
                    symbol=symbol,
                    multi_tf_data=multi_tf_data,
                    current_price=exchange._current_price,
                    execution_tf_data=execution_tf_data,
                )
            except Exception as e:
                logger.debug(f"Signal generation skipped at {ts}: {e}")
                signals = []

            # --- Execute signals ---
            for signal in signals:
                try:
                    cost_model.apply(signal, exchange._current_price)
                    self._execute_signal(signal, exchange)
                except Exception as e:
                    logger.debug(f"Signal execution skipped: {e}")

            # --- Equity snapshot every 60 candles (~5h) ---
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
        logger.info(
            f"Backtest complete | Final: ${final_equity:,.2f} | "
            f"Return: {result.total_return_pct:+.1f}% | "
            f"Sharpe: {result.sharpe_ratio:.2f} | "
            f"Max DD: {result.max_drawdown_pct:.1f}% | "
            f"Trades: {result.total_trades}"
        )
        return result

    # ------------------------------------------------------------------
    # Signal execution
    # ------------------------------------------------------------------

    def _execute_signal(self, signal, exchange: SimulatedExchange) -> None:
        """Translate a Signal object into a SimulatedExchange order."""
        price = exchange._current_price
        if price <= 0:
            return

        # Fix 3 — position deduplication: skip new entries in the same direction
        # as an existing open position, but allow opposite-direction signals
        # (closes/reversals) through.
        existing_pos = exchange._positions.get(signal.asset)
        if existing_pos:
            signal_side_str = "long" if signal.side == OrderSide.BUY else "short"
            if existing_pos.side == signal_side_str:
                return  # already long/short in this direction, skip duplicate entry

        # Determine position size
        qty = signal.quantity
        if qty <= 0:
            # Fixed fractional sizing: 2% of available balance per trade
            available = exchange.balance
            risk_pct = getattr(self.cfg, "max_risk_per_trade", 0.02)
            qty = round((available * risk_pct) / price, 6)

        if qty <= 0:
            return

        side = "bid" if signal.side == OrderSide.BUY else "ask"
        exit_side = "ask" if signal.side == OrderSide.BUY else "bid"

        # Use limit order at entry_price if it differs from current price
        # by more than 0.1%, otherwise use market order for immediate fill
        price_diff_pct = abs(signal.entry_price - price) / price
        if price_diff_pct > 0.001:
            exchange.place_order(
                symbol=signal.asset,
                side=side,
                quantity=str(qty),
                order_type="limit",
                price=signal.entry_price,
            )
        else:
            exchange.place_order(
                symbol=signal.asset,
                side=side,
                quantity=str(qty),
                order_type="market",
            )

        # Place stop-loss and take-profit exit orders for single-entry strategies.
        # Grid trading manages its own exit levels via its grid nodes — adding
        # engine-level SL/TP on top creates destructive order churn.
        is_grid = signal.strategy == StrategyType.GRID_TRADING
        if not is_grid:
            if signal.stop_loss and signal.stop_loss > 0:
                # Stop orders: fill when price moves adversely through the stop level
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(qty),
                    order_type="stop",
                    price=signal.stop_loss,
                )
            if signal.take_profit and signal.take_profit > 0:
                # Take-profit: limit order that fills when price reaches the target
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(qty),
                    order_type="limit",
                    price=signal.take_profit,
                )

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _candle_at(candles: Dict, idx: int) -> Dict:
        return {k: candles[k][idx] for k in ("open", "high", "low", "close", "volume")}

    @staticmethod
    def _history(candles: Dict, up_to: int, lookback: int) -> Dict:
        """Return a slice of candles up to and including up_to index."""
        start = max(0, up_to - lookback + 1)
        return {k: candles[k][start: up_to + 1] for k in candles if k != "timestamp"}

    @staticmethod
    def _nearest_idx(idx_map: Dict, ts: str, fallback_5m_idx: int, ratio: int) -> int:
        """Return the most recent higher-TF index at or before ts."""
        if ts in idx_map:
            return idx_map[ts]
        # Approximate: 5m_idx / ratio (e.g., 5m->1h is /12)
        return max(0, fallback_5m_idx // ratio)
