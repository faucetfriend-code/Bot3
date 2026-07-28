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

from bisect import bisect_left, bisect_right
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger

from ..diagnostics.funnel import (
    NULL_FUNNEL,
    REASON_EXEC_EXCEPTION,
    REASON_EXEC_HEDGE_MODE,
    REASON_EXEC_MIN_HOLD,
    REASON_EXEC_NO_PRICE,
    REASON_EXEC_QTY_NON_POSITIVE,
    REASON_EXEC_SAME_DIRECTION,
    STAGE_BARS_SKIPPED_WARMUP,
    STAGE_CLOSED_TRADES,
    STAGE_EXECUTION_BLOCKED,
    STAGE_FILLS,
    STAGE_ORDERS_PLACED,
    SignalFunnel,
)
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

# These overlays depend on live-only surfaces (real L2 orderbook depth,
# funding-history API) that SimulatedExchange cannot provide, so they can
# never produce meaningful signals in a backtest. Keys are the snake_case
# optimization identifiers (see regime_param_overlay.STRATEGY_KEY_TO_DISPLAY).
NON_BACKTESTABLE_STRATEGIES = frozenset({"orderbook_imbalance", "funding_arb"})

# StrategyManager display name -> constructor enable-flag kwarg.
STRATEGY_ENABLE_FLAGS: Dict[str, str] = {
    "MeanReversion": "enable_mean_reversion",
    "MACrossover": "enable_ma_crossover",
    "GridTrading": "enable_grid_trading",
    "LiquidationCapture": "enable_liquidation_capture",
    "VWAPScalping": "enable_vwap_scalping",
    "MomentumScalping": "enable_momentum_scalping",
    "FundingArb": "enable_funding_arb",
    "OrderBookImbalance": "enable_orderbook_imbalance",
    "SessionRangeBreakout": "enable_session_range_breakout",
    "CalendarFlow": "enable_calendar_flow",
}


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
        # Signal funnel for this run (replaced in run(); NullFunnel until
        # then so _execute_signal is safe to call standalone in tests).
        self._funnel = NULL_FUNNEL
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
        # Signal funnel: a backtest always wants diagnostics (the cost is
        # a handful of dict increments per bar - see the <3% benchmark in
        # tests/test_diagnostics.py). The live bot keeps NullFunnel.
        funnel = SignalFunnel(
            label=f"{strategy_filter or 'all'}/{symbol} {start}..{end}"
        )
        self._funnel = funnel

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
            for name, flag in STRATEGY_ENABLE_FLAGS.items():
                strategy_kwargs[flag] = (name == strategy_filter)
            logger.info(f"Single-strategy mode: only {strategy_filter} enabled")

        # Force-disable strategies that can never work against the
        # simulated exchange (see NON_BACKTESTABLE_STRATEGIES).
        for display_name, flag in STRATEGY_ENABLE_FLAGS.items():
            strategy_key = DISPLAY_TO_STRATEGY_KEY.get(display_name)
            if strategy_key not in NON_BACKTESTABLE_STRATEGIES:
                continue
            if strategy_kwargs.get(flag, True):
                logger.warning(
                    f"SKIPPING {display_name} in backtest mode: not "
                    f"backtestable (depends on live-only data surfaces - "
                    f"real L2 orderbook depth / funding-history API - that "
                    f"SimulatedExchange cannot provide)"
                )
            strategy_kwargs[flag] = False

        strategy_manager = StrategyManager(
            risk_manager=risk_manager,
            client=exchange,
            **strategy_kwargs,
        )
        strategy_manager.set_funnel(funnel)

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

        # --- 1m execution-coverage guard ---
        # The replay loop always feeds a 1m execution slice to the
        # strategy pipeline, and _nearest_idx falls back to a positional
        # guess when a timestamp is missing from the 1m map - which
        # silently serves wrong-date candles when 1m data does not cover
        # the window. Refuse to run instead.
        if not loader.covers("1m", start, end):
            bounds = loader.coverage_bounds("1m")
            available = (
                f"{bounds[0]} .. {bounds[1]}" if bounds else "no 1m data on disk"
            )
            raise ValueError(
                f"1m candle data for {symbol} does not cover the requested "
                f"backtest window {start} .. {end} "
                f"(available 1m coverage: {available}). "
                f"Backfill it with: python -m trading_bot_v2.data_manager "
                f"--symbols {symbol} --timeframes 1m"
            )

        # --- Load candles (with a pre-window warmup prefix) ---
        # Every timeframe is loaded from `start - warmup` so the first
        # replayed bar already sees a full `lookback`-candle history slice,
        # instead of ramping up from 1 candle inside the requested window.
        lookback = max(1, int(getattr(self.cfg, "backtest_history_lookback", 60) or 60))
        warmup = int(getattr(self.cfg, "backtest_warmup_candles", 0) or 0) or lookback
        logger.info(
            f"History lookback: {lookback} candles/timeframe | "
            f"warmup prefix: {warmup} candles/timeframe"
        )

        candles = {
            tf: loader.get_candles(tf, start, end, warmup_candles=warmup)
            for tf in ("1m", "5m", "15m", "1h", "4h")
        }

        timestamps_5m = candles["5m"]["timestamp"]
        # Sorted string views of each timeframe's timestamps, for the
        # as-of index lookup in _nearest_idx.
        sorted_ts = {
            tf: [str(t) for t in candles[tf]["timestamp"]]
            for tf in ("1m", "15m", "1h", "4h")
        }

        # First 5m index inside the requested window - everything before it
        # is warmup and is only ever read through _history().
        replay_start = self._first_index_at_or_after(timestamps_5m, start)
        total = len(timestamps_5m) - replay_start
        logger.info(
            f"Loaded {len(timestamps_5m)} 5m candles "
            f"({replay_start} warmup + {total} replayed)"
        )

        # Build reverse-lookup: 5m timestamp -> index in each higher timeframe
        idx_map = {
            tf: {ts: i for i, ts in enumerate(candles[tf]["timestamp"])}
            for tf in ("1m", "15m", "1h", "4h")
        }

        # --- Replay loop (one 5m candle at a time) ---
        for i in range(replay_start, len(timestamps_5m)):
            ts = timestamps_5m[i]
            # Advance the simulated exchange price to this candle's close
            candle_5m = self._candle_at(candles["5m"], i)
            exchange.advance(candle_5m, ts)

            # --- Build multi-timeframe bundles ---
            i_15m = self._nearest_idx(idx_map["15m"], sorted_ts["15m"], ts)
            i_1h  = self._nearest_idx(idx_map["1h"],  sorted_ts["1h"],  ts)
            i_4h  = self._nearest_idx(idx_map["4h"],  sorted_ts["4h"],  ts)
            i_1m  = self._nearest_idx(idx_map["1m"],  sorted_ts["1m"],  ts)

            # Regime / structure timeframes (required by StrategyManager)
            multi_tf_data = {
                "15m": self._history(candles["15m"], i_15m, lookback),
                "1h":  self._history(candles["1h"],  i_1h,  lookback),
                "4h":  self._history(candles["4h"],  i_4h,  lookback),
            }

            # Execution timeframes (optional, for precise entry)
            execution_tf_data = {
                "5m": self._history(candles["5m"], i, lookback),
                "1m": self._history(candles["1m"], i_1m, lookback),
            }

            # Skip until we have enough 4h history for regime detection (29 candles)
            if i_4h < 28:
                funnel.count(STAGE_BARS_SKIPPED_WARMUP)
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
                        funnel.count(STAGE_ORDERS_PLACED)
                        strategy_manager.register_trade_execution(
                            signal, {"quantity": 0, "price": exchange._current_price}
                        )
                        # Wire LiquidationCapture session tracking
                        if signal.strategy == StrategyType.LIQUIDATION_CAPTURE:
                            lc = strategy_manager.strategies.get("LiquidationCapture")
                            if lc:
                                lc.record_trade()
                except Exception as e:
                    funnel.count(STAGE_EXECUTION_BLOCKED)
                    funnel.reject(REASON_EXEC_EXCEPTION)
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
        # Terminal funnel stages are only known once the exchange has
        # been finalised.
        funnel.set_stage(STAGE_FILLS, result.total_trades)
        funnel.set_stage(STAGE_CLOSED_TRADES, result.closed_trades)
        result.diagnostics = funnel.to_dict()
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
            TP order fills - never by a competing strategy signal.

        Min-hold enforcement (Fix Layer 1):
            When hedge_mode=True, opposing signals are additionally blocked until
            the position has been open for at least min_hold_candles candles,
            preventing premature cross-strategy exits that crush R/R.
        """
        funnel = self._funnel
        price = exchange._current_price
        if price <= 0:
            funnel.count(STAGE_EXECUTION_BLOCKED)
            funnel.reject(REASON_EXEC_NO_PRICE)
            return False

        side = "bid" if signal.side == OrderSide.BUY else "ask"
        exit_side = "ask" if signal.side == OrderSide.BUY else "bid"

        # --- Existing position check ---
        existing_pos = exchange._positions.get(signal.asset)
        if existing_pos:
            signal_side_str = "long" if signal.side == OrderSide.BUY else "short"
            if existing_pos.side == signal_side_str:
                funnel.count(STAGE_EXECUTION_BLOCKED)
                funnel.reject(REASON_EXEC_SAME_DIRECTION)
                return False  # Same direction - skip duplicate entry

            # Opposing direction - apply hedge_mode and hold_time guards
            if not self._hedge_mode:
                # Hedge mode disabled (Pacifica): skip opposing signals entirely.
                # Positions are only closed by their SL/TP orders.
                funnel.count(STAGE_EXECUTION_BLOCKED)
                funnel.reject(REASON_EXEC_HEDGE_MODE)
                logger.debug(
                    f"Hedge mode off: blocking opposing {signal.side.value} signal "
                    f"for {signal.asset}"
                )
                return False

            # Hedge mode enabled: enforce minimum hold time
            open_candle = self._position_open_candle.get(signal.asset, candle_idx)
            candles_held = candle_idx - open_candle
            if candles_held < self._min_hold_candles:
                funnel.count(STAGE_EXECUTION_BLOCKED)
                funnel.reject(REASON_EXEC_MIN_HOLD)
                logger.debug(
                    f"Min hold not met for {signal.asset}: "
                    f"{candles_held}/{self._min_hold_candles} candles - skipping close"
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
            # Closing trades need no SL/TP - the position is being exited
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
            funnel.count(STAGE_EXECUTION_BLOCKED)
            funnel.reject(REASON_EXEC_QTY_NON_POSITIVE)
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
    def _nearest_idx(idx_map: Dict, sorted_ts: List[str], ts) -> int:
        """Return the most recent higher-TF index at or before ts.

        Exact hit first (the common case, since higher-TF bars land on 5m
        boundaries), otherwise an as-of lookup over the timeframe's sorted
        timestamps. Timestamps are canonical "%Y-%m-%dT%H:%M:%S" strings, so
        lexicographic order matches chronological order.

        A positional estimate (5m_index // ratio) is deliberately not used:
        it breaks as soon as the series carry a warmup prefix or contain
        gaps, and silently serves candles from the wrong date.
        """
        if ts in idx_map:
            return idx_map[ts]
        return max(0, bisect_right(sorted_ts, str(ts)) - 1)

    @staticmethod
    def _first_index_at_or_after(timestamps: List, boundary: str) -> int:
        """Index of the first timestamp at or after boundary (len if none)."""
        return bisect_left([str(t) for t in timestamps], str(boundary))
