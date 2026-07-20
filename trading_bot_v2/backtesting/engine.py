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
from ..regime_param_overlay import (
    DISPLAY_TO_STRATEGY_KEY,
    apply_params_to_strategy,
    resolve_strategy_display_name,
)
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
        # Per-run state (reset in run())
        self._hedge_mode: bool = False
        self._min_hold_candles: int = 6
        self._position_open_candle: Dict[str, int] = {}
        # Time-exit tracking for signals carrying indicators["time_exit_hours"]:
        # {symbol: {"open_ts": datetime, "hours": float, "strategy": str}}
        self._position_time_exit: Dict[str, Dict] = {}
        self._sim_dt: Optional[datetime] = None

    def run(
        self,
        start: str,
        end: str,
        symbol: Optional[str] = None,
        initial_capital: Optional[float] = None,
        strategy_filter: Optional[str] = None,
    ) -> BacktestResult:
        symbol = symbol or self.cfg.backtest_symbol
        initial_capital = initial_capital or self.cfg.backtest_initial_capital
        strategy_filter = strategy_filter or getattr(self.cfg, "backtest_strategy", "") or None

        # Initialise per-run state
        self._hedge_mode = getattr(self.cfg, "backtest_hedge_mode", False)
        self._min_hold_candles = getattr(self.cfg, "backtest_min_hold_candles", 6)
        self._position_open_candle = {}
        self._position_time_exit = {}
        self._sim_dt = None

        logger.info(
            f"Starting backtest: {symbol} | {start} -> {end} | capital={initial_capital} | "
            f"hedge_mode={self._hedge_mode} | min_hold_candles={self._min_hold_candles}"
            + (f" | strategy_filter={strategy_filter}" if strategy_filter else "")
        )

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

        # Build strategy enable kwargs for single-strategy mode
        strategy_kwargs: Dict = {}
        if strategy_filter:
            # Accept both display ("MeanReversion") and optimization
            # snake_case ("mean_reversion") strategy identifiers.
            resolved_filter = resolve_strategy_display_name(strategy_filter)
            if resolved_filter is not None:
                strategy_filter = resolved_filter
            else:
                logger.warning(
                    f"Unknown strategy_filter '{strategy_filter}' - "
                    f"no strategy will match"
                )
            _all_strategy_flags = {
                "MeanReversion": "enable_mean_reversion",
                "MACrossover": "enable_ma_crossover",
                "GridTrading": "enable_grid_trading",
                "LiquidationCapture": "enable_liquidation_capture",
                "VWAPScalping": "enable_vwap_scalping",
                "MomentumScalping": "enable_momentum_scalping",
                "FundingArb": "enable_funding_arb",
                "OrderBookImbalance": "enable_orderbook_imbalance",
                "SessionRangeBreakout": "enable_session_range_breakout",
            }
            for name, flag in _all_strategy_flags.items():
                strategy_kwargs[flag] = (name == strategy_filter)
            logger.info(f"Single-strategy mode: only {strategy_filter} enabled")

        strategy_manager = StrategyManager(
            risk_manager=risk_manager,
            client=exchange,
            **strategy_kwargs,
        )

        # Apply per-strategy optimization parameter overrides carried on
        # the config proxy (set by OptimizationAdapter as
        # ``_optimization_params_<strategy>``). Only whitelisted params
        # (search-space keys) are ever set on the strategy instances.
        for _display_name, _strategy_obj in strategy_manager.strategies.items():
            _strategy_key = DISPLAY_TO_STRATEGY_KEY.get(_display_name)
            if not _strategy_key:
                continue
            _params = getattr(
                self.cfg, f"_optimization_params_{_strategy_key}", None
            )
            if _params:
                _applied = apply_params_to_strategy(
                    _strategy_obj, _strategy_key, _params
                )
                if _applied:
                    logger.info(
                        f"Backtest param overrides applied to "
                        f"{_display_name}: {_applied}"
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
                "4h":  self._history(candles["4h"],  i_4h,  60),
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
            self._sim_dt = sim_dt
            strategy_manager.set_sim_time(sim_dt)

            # Drive the regime detector's injectable clock with simulated
            # time so its cache TTL / dwell / confirmation logic follows
            # candle time instead of wall-clock (otherwise the regime
            # would be computed once and served from cache for the whole
            # replay). Uses the same injection point as
            # analysis/regime_stability.py.
            if sim_dt is not None:
                strategy_manager.regime_detector._clock = lambda dt=sim_dt: dt

            # --- Time-based exits (signals carrying time_exit_hours) ---
            if sim_dt is not None and self._position_time_exit:
                self._apply_time_exits(exchange, sim_dt)

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

            # Expose the confirmed regime to the exchange so every fill
            # is regime-tagged (reuses the cache populated during signal
            # generation - no recomputation).
            regime_obj = strategy_manager.regime_detector.get_current_regime(
                symbol
            )
            exchange._current_regime = getattr(regime_obj, "value", "") or ""

            # --- Execute signals ---
            for signal in signals:
                try:
                    cost_model.apply(signal, exchange._current_price)
                    exchange._current_strategy = signal.strategy.value
                    executed = self._execute_signal(signal, exchange, i)
                    if executed:
                        strategy_manager.register_trade_execution(
                            signal, {"quantity": 0, "price": exchange._current_price}
                        )
                        # Wire LiquidationCapture session tracking
                        if signal.strategy == StrategyType.LIQUIDATION_CAPTURE:
                            lc = strategy_manager.strategies.get("LiquidationCapture")
                            if lc:
                                lc.record_trade()
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

    def _execute_signal(self, signal, exchange: SimulatedExchange, candle_idx: int) -> bool:
        """
        Translate a Signal object into a SimulatedExchange order.

        Returns True if an order was placed, False if the signal was skipped.

        Hedge-mode enforcement (Fix 2D):
            When hedge_mode=False (Pacifica default), any signal that opposes an
            open position is dropped. Positions are closed only when their SL or
            TP order fills — never by a competing strategy signal.

        Min-hold enforcement (Fix Layer 1):
            When hedge_mode=True, opposing signals are additionally blocked until
            the position has been open for at least min_hold_candles candles,
            preventing premature cross-strategy exits that crush R/R.
        """
        price = exchange._current_price
        if price <= 0:
            return False

        side = "bid" if signal.side == OrderSide.BUY else "ask"
        exit_side = "ask" if signal.side == OrderSide.BUY else "bid"

        # --- Existing position check ---
        existing_pos = exchange._positions.get(signal.asset)
        if existing_pos:
            signal_side_str = "long" if signal.side == OrderSide.BUY else "short"
            if existing_pos.side == signal_side_str:
                return False  # Same direction — skip duplicate entry

            # Opposing direction — apply hedge_mode and hold_time guards
            if not self._hedge_mode:
                # Hedge mode disabled (Pacifica): skip opposing signals entirely.
                # Positions are only closed by their SL/TP orders.
                logger.debug(
                    f"Hedge mode off: blocking opposing {signal.side.value} signal "
                    f"for {signal.asset}"
                )
                return False

            # Hedge mode enabled: enforce minimum hold time
            open_candle = self._position_open_candle.get(signal.asset, candle_idx)
            candles_held = candle_idx - open_candle
            if candles_held < self._min_hold_candles:
                logger.debug(
                    f"Min hold not met for {signal.asset}: "
                    f"{candles_held}/{self._min_hold_candles} candles — skipping close"
                )
                return False

            # Allow close: size to exactly the existing position quantity
            close_qty = existing_pos.quantity
            price_diff_pct = abs(signal.entry_price - price) / price
            if price_diff_pct > 0.001:
                exchange.place_order(
                    symbol=signal.asset,
                    side=side,
                    quantity=str(close_qty),
                    order_type="limit",
                    price=signal.entry_price,
                )
            else:
                exchange.place_order(
                    symbol=signal.asset,
                    side=side,
                    quantity=str(close_qty),
                    order_type="market",
                )
            # Closing trades need no SL/TP — the position is being exited
            self._position_open_candle.pop(signal.asset, None)
            self._position_time_exit.pop(signal.asset, None)
            return True

        # --- Opening a new position ---
        qty = signal.quantity
        if qty <= 0:
            # Fixed fractional sizing: 2% of available balance per trade
            available = exchange.balance
            risk_pct = getattr(self.cfg, "max_risk_per_trade", 0.02)
            qty = round((available * risk_pct) / price, 6)

        if qty <= 0:
            return False

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

        # Track when this position was opened (for min_hold_candles)
        self._position_open_candle[signal.asset] = candle_idx

        # Track time-based exit if the signal requests one (e.g. SessionRangeBreakout)
        time_exit_hours = (signal.indicators or {}).get("time_exit_hours")
        if time_exit_hours and self._sim_dt is not None:
            self._position_time_exit[signal.asset] = {
                "open_ts": self._sim_dt,
                "hours": float(time_exit_hours),
                "strategy": signal.strategy.value,
            }

        # Place exit orders. Grid signals use stop-only (the opposing grid limit
        # order acts as TP when price reaches it). All other strategies get both.
        is_grid = signal.strategy == StrategyType.GRID_TRADING
        if is_grid:
            if signal.stop_loss and signal.stop_loss > 0:
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(qty),
                    order_type="stop",
                    price=signal.stop_loss,
                )
        else:
            if signal.stop_loss and signal.stop_loss > 0:
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(qty),
                    order_type="stop",
                    price=signal.stop_loss,
                )
            if signal.take_profit and signal.take_profit > 0:
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(qty),
                    order_type="limit",
                    price=signal.take_profit,
                )

        return True

    # ------------------------------------------------------------------
    # Time-based exits
    # ------------------------------------------------------------------

    def _apply_time_exits(self, exchange: SimulatedExchange, sim_dt: datetime) -> None:
        """
        Close open positions whose originating signal set a max hold time.

        Signals that carry indicators["time_exit_hours"] (e.g.
        SessionRangeBreakout) are tracked in _position_time_exit at open.
        Once the position's age exceeds its limit it is closed at the
        current bar close via a market order (reason: time_exit). SL/TP
        orders are cancelled automatically by the exchange's OCO cleanup
        when the position fully closes.
        """
        for symbol in list(self._position_time_exit.keys()):
            info = self._position_time_exit[symbol]
            pos = exchange._positions.get(symbol)
            if pos is None:
                # Already closed by SL/TP - drop stale tracking
                del self._position_time_exit[symbol]
                continue

            age_hours = (sim_dt - info["open_ts"]).total_seconds() / 3600.0
            if age_hours < info["hours"]:
                continue

            close_side = "ask" if pos.side == "long" else "bid"
            exchange._current_strategy = info.get("strategy", "")
            exchange.place_order(
                symbol=symbol,
                side=close_side,
                quantity=str(pos.quantity),
                order_type="market",
            )
            del self._position_time_exit[symbol]
            self._position_open_candle.pop(symbol, None)
            logger.debug(
                f"time_exit: closed {symbol} {pos.side} after {age_hours:.1f}h "
                f"(limit {info['hours']}h)"
            )

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _candle_at(candles: Dict, idx: int) -> Dict:
        return {k: candles[k][idx] for k in ("open", "high", "low", "close", "volume")}

    @staticmethod
    def _history(candles: Dict, up_to: int, lookback: int) -> Dict:
        """Return a slice of candles up to and including up_to index.

        Includes the "timestamp" list (ISO-8601 strings from the data loader)
        so time-aware strategies (e.g. SessionRangeBreakout) can locate
        session windows within the slice.
        """
        start = max(0, up_to - lookback + 1)
        return {k: candles[k][start: up_to + 1] for k in candles}

    @staticmethod
    def _nearest_idx(idx_map: Dict, ts: str, fallback_5m_idx: int, ratio: int) -> int:
        """Return the most recent higher-TF index at or before ts."""
        if ts in idx_map:
            return idx_map[ts]
        # Approximate: 5m_idx / ratio (e.g., 5m->1h is /12)
        return max(0, fallback_5m_idx // ratio)
