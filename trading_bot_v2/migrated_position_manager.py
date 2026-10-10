"""
MigratedPositionManager
=======================

Manages positions migrated from grid trading to trend-following.

When a grid is partially unwound due to regime change:
- Against-trend positions are closed
- With-trend positions are KEPT and migrated here
- This manager provides trailing stops and trend-following exits

Responsibilities:
- Set and update trailing stops for migrated positions
- Monitor trend direction for exit signals
- Close positions when trend reverses
- Partial take profit at targets

This module works in conjunction with:
- GridLifecycleManager: Source of migrated positions
- RiskManager: Tracks migrated position exposure
- MarketRegimeDetector: Provides trend direction
"""

import os
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, cast
from loguru import logger

from .exit_sizing import plan_close_quantity, remaining_exchange_quantity, ClosePlan
from .order_result import OrderResult
from .price_lookup import PriceLookup, fetch_current_price
from .venue_stops import (
    PROTECTED_STATES,
    STOP_STATE_ATTACHED,
    STOP_STATE_MISSING,
    STOP_STATE_STANDALONE,
    stop_move_pct,
    venue_stops_supported,
)

if TYPE_CHECKING:
    from .market_regime import MarketRegimeDetector
    from .multi_timeframe_fetcher import MultiTimeframeFetcher
    from .risk_manager import RiskManager


def _config_value(name: str, default: Any) -> Any:
    """Read ``config.<name>`` without importing config at module import."""
    try:
        from .config import config

        return getattr(config, name, default)
    except Exception:  # noqa: BLE001 - config unavailable -> default
        return default


def _env_or_config_int(env_name: str, config_name: str, default: int) -> int:
    """Env var first (legacy in-module behaviour), else config, else default."""
    raw = os.getenv(env_name)
    if raw is not None:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    try:
        return max(1, int(_config_value(config_name, default)))
    except (TypeError, ValueError):
        return default


def _env_or_config_float(env_name: str, config_name: str, default: float) -> float:
    """Env var first, else config, else default (non-negative)."""
    raw = os.getenv(env_name)
    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    try:
        return max(0.0, float(_config_value(config_name, default)))
    except (TypeError, ValueError):
        return default


def _max_close_attempts() -> int:
    """MIGRATED_CLOSE_MAX_ATTEMPTS: env var first, else config (default 5)."""
    return _env_or_config_int(
        "MIGRATED_CLOSE_MAX_ATTEMPTS", "migrated_close_max_attempts", 5
    )


def _send_telegram_error_alert(error_type: str, message: str, context: str) -> bool:
    """Send a Telegram error alert synchronously; False when disabled.

    Imported lazily so the manager stays importable without httpx and so
    tests can monkeypatch this hook.  The TelegramAlerts helpers are
    async; ``asyncio.run`` mirrors ``TelegramAlerts._handle_event_sync``.
    """
    from .telegram_alerts import telegram_alerts

    if not telegram_alerts.enabled:
        return False
    import asyncio

    return bool(
        asyncio.run(telegram_alerts.send_error_alert(error_type, message, context))
    )


class MigratedPositionManager:
    """
    Manages positions migrated from grid to trend-following.

    Key behaviors:
    - Trailing stop: 2x ATR below/above price (direction-dependent)
    - Trend reversal exit: Close when trend direction flips
    - Partial take profit: 50% at 3x ATR gain (optional)
    """

    # Configuration constants
    TRAILING_STOP_ATR_MULTIPLIER = 2.0  # Stop trails at 2x ATR
    MIN_STOP_ATR_MULTIPLIER = 0.5  # Minimum stop distance (prevent tiny stops)
    TAKE_PROFIT_ATR_MULTIPLIER = 3.0  # Take partial profit at 3x ATR
    PARTIAL_PROFIT_PCT = 0.5  # Take 50% at TP target
    MAX_POSITIONS_PER_SYMBOL = 10  # Safety cap

    def __init__(
        self,
        client: Any,
        risk_manager: "RiskManager",
        regime_detector: "MarketRegimeDetector",
        multi_tf_fetcher: Optional["MultiTimeframeFetcher"] = None,
        reconcile_callback: Optional[Callable[[], Any]] = None,
        venue_exchange_resolver: Optional[Callable[[], Any]] = None,
        db: Any = None,
        price_lookup: Optional[PriceLookup] = None,
    ) -> None:
        """
        Initialize MigratedPositionManager.

        Args:
            client: Exchange client (PacificaClient)
            risk_manager: Central RiskManager instance (has migrated positions tracking)
            regime_detector: MarketRegimeDetector for trend direction
            multi_tf_fetcher: Optional MultiTimeframeFetcher for ATR calculation
            reconcile_callback: Invoked after an AMBIGUOUS close outcome
                (timeout / exception) so the position reconciler runs
                before any accounting.  Optional.
            venue_exchange_resolver: Returns the exchange ADAPTER used to
                mirror trailing-stop moves onto the venue's stop order.
                Optional; without it (or on venues without server-side
                stops) the trailing stop stays local-only.
            db: Optional DatabaseManager used to write the venue stop
                id/price/state back to the positions table.
            price_lookup: Optional ticker source (symbol -> ticker dict)
                used when manage_positions is not handed a price for a
                symbol.  TradingBot injects its WebSocket-with-REST-
                fallback lookup; without it the client itself is asked
                (see price_lookup.py).
        """
        self.client = client
        self._price_lookup = price_lookup
        self.risk_manager = risk_manager
        self.regime_detector = regime_detector
        self.multi_tf_fetcher = multi_tf_fetcher
        self._venue_exchange_resolver = venue_exchange_resolver
        self._db = db

        # Trailing stop tracking: {symbol: {side: stop_price}}
        self._trailing_stops: Dict[str, Dict[str, float]] = {}

        # Venue stop mirror per position: {(symbol, side): {stop_id,
        # price, entry_order_id, state, failures, fallback_local}}
        self._venue_stops: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._venue_stop_min_move_pct = _env_or_config_float(
            "VENUE_STOP_MIN_MOVE_PCT", "venue_stop_min_move_pct", 0.1
        )
        self._venue_stop_max_amend_failures = _env_or_config_int(
            "VENUE_STOP_MAX_AMEND_FAILURES", "venue_stop_max_amend_failures", 5
        )

        # Take profit tracking: {symbol: {side: {'target': price, 'partial_taken': bool}}}
        self._take_profits: Dict[str, Dict[str, Dict[str, Any]]] = {}

        # Last known ATR per symbol (cached)
        self._atr_cache: Dict[str, Dict[str, Any]] = {}
        self._atr_cache_ttl_seconds = 300  # 5 minute cache

        # Pending-close markers: {(symbol, side): marker}.  A close whose
        # order was rejected, raised, or could not be confirmed stays here
        # with its attempt count until the exchange confirms the position
        # is gone.  RiskManager unregister / DB close / partial_taken
        # happen ONLY on confirmation.  In-memory: the positions table has
        # no pending column and grid_positions is not in the schema.
        self._pending_closes: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._max_close_attempts = _max_close_attempts()
        self._reconcile_callback = reconcile_callback

        # Max hold time (hours) per strategy for time-based exits.
        # Positions tagged with one of these strategies are force-closed
        # once their age exceeds the limit (reason: "time_exit").
        try:
            orb_time_exit_hours = float(os.getenv("ORB_TIME_EXIT_HOURS", "4"))
        except ValueError:
            orb_time_exit_hours = 4.0
        self._max_hold_hours: Dict[str, float] = {
            "session_range_breakout": orb_time_exit_hours,
        }

        logger.info("MigratedPositionManager initialized")

    def has_positions(self) -> bool:
        """Check if there are any migrated positions to manage."""
        return self.risk_manager.has_migrated_positions()

    def manage_positions(
        self, current_prices: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Main management loop - call this periodically from TradingBot.

        For each migrated position:
        1. Get current price
        2. Update trailing stop if price moved favorably
        3. Check if stop hit → close position
        4. Check if trend reversed → close position
        5. Check take profit targets

        Args:
            current_prices: Dict of symbol -> current price

        Returns:
            Dict with management results
        """
        results: Dict[str, Any] = {
            "positions_managed": 0,
            "stops_updated": 0,
            "positions_closed": 0,
            "partial_profits": 0,
            "errors": [],
        }

        # Get all migrated positions from RiskManager
        positions = self.risk_manager.get_migrated_positions()

        # Retry closes that were not confirmed on a previous cycle first,
        # then re-read: a retry may have finalized (unregistered) one.
        results["pending_closes_retried"] = self._retry_pending_closes(positions)
        if self._pending_closes:
            positions = self.risk_manager.get_migrated_positions()

        if not positions:
            return results

        for pos in positions:
            symbol = pos.get("symbol")
            if not symbol:
                continue

            if (symbol, pos.get("side")) in self._pending_closes:
                continue  # a close is already in flight; never stack orders

            results["positions_managed"] += 1

            try:
                # 0. Check time-based exit (strategy max-hold, needs no price)
                if self._check_time_exit(symbol, pos):
                    self._close_position(symbol, pos, "time_exit")
                    results["positions_closed"] += 1
                    continue

                # Get current price
                current_price = (current_prices or {}).get(symbol)
                if not current_price:
                    current_price = self._get_current_price(symbol)

                if not current_price:
                    results["errors"].append(f"Could not get price for {symbol}")
                    continue

                # Get 4h market data for trend direction and ATR
                market_data = self._get_market_data(symbol)

                # 1. Check trend reversal
                if self._check_trend_reversal(symbol, pos, market_data):
                    self._close_position(symbol, pos, "trend_reversal")
                    results["positions_closed"] += 1
                    continue

                # 2. Check if stop hit
                if self._check_stop_hit(symbol, pos, current_price):
                    self._close_position(symbol, pos, "trailing_stop")
                    results["positions_closed"] += 1
                    continue

                # 3. Update trailing stop (and mirror it onto the venue)
                if self._update_trailing_stop(symbol, pos, current_price, market_data):
                    results["stops_updated"] += 1
                    self._sync_venue_stop(symbol, pos)

                # 4. Check take profit
                if self._check_take_profit(symbol, pos, current_price, market_data):
                    results["partial_profits"] += 1

            except Exception as e:
                logger.error(f"Error managing migrated position {symbol}: {e}")
                results["errors"].append(f"{symbol}: {e}")

        return results

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol."""
        try:
            return fetch_current_price(self.client, symbol, self._price_lookup)
        except Exception as e:
            logger.warning(f"Could not get price for {symbol}: {e}")
        return None

    def _get_market_data(self, symbol: str) -> Optional[Dict[str, List[float]]]:
        """Get 4h market data for ATR and trend calculations."""
        if not self.multi_tf_fetcher:
            return None

        try:
            data = self.multi_tf_fetcher.get_candles_multi_tf(
                symbol=symbol, timeframes=["4h"], lookback_candles=200
            )
            return data.get("4h")
        except Exception as e:
            logger.warning(f"Could not get market data for {symbol}: {e}")
            return None

    def _get_atr(
        self, symbol: str, market_data: Optional[Dict[str, List[float]]] = None
    ) -> float:
        """
        Get ATR for symbol (with caching).

        Args:
            symbol: Trading symbol
            market_data: Optional pre-fetched market data

        Returns:
            ATR value, or 0 if calculation fails
        """
        import time

        # Check cache
        if symbol in self._atr_cache:
            cache = self._atr_cache[symbol]
            if time.time() - cache["timestamp"] < self._atr_cache_ttl_seconds:
                return cast(float, cache["atr"])

        # Calculate ATR
        if not market_data:
            market_data = self._get_market_data(symbol)

        if not market_data:
            return 0

        try:
            from .indicators import calculate_atr

            highs = market_data.get("high", [])
            lows = market_data.get("low", [])
            closes = market_data.get("close", [])

            if len(closes) < 15:
                return 0

            atr = calculate_atr(highs, lows, closes, period=14)

            # Cache result
            self._atr_cache[symbol] = {"atr": atr, "timestamp": time.time()}

            return atr

        except Exception as e:
            logger.warning(f"ATR calculation failed for {symbol}: {e}")
            return 0

    def _check_time_exit(self, symbol: str, pos: Dict[str, Any]) -> bool:
        """
        Check if a position has exceeded its strategy's max hold time.

        Only positions tagged with a strategy in self._max_hold_hours
        (currently session_range_breakout) are affected. Position age is
        derived from the epoch-seconds timestamp the position dict carries
        ("entry_time" preferred, "migrated_at" fallback - the key written
        by RiskManager.register_migrated_position). Mirrors the
        Position.hours_since_entry age calculation in models.py.

        Args:
            symbol: Trading symbol.
            pos: Position dict from RiskManager.get_migrated_positions().

        Returns:
            True if the position should be closed with reason "time_exit".
        """
        strategy = pos.get("strategy")
        if not strategy:
            return False

        max_hold = self._max_hold_hours.get(str(strategy))
        if not max_hold:
            return False

        entry_ts = pos.get("entry_time", pos.get("migrated_at"))
        if entry_ts is None:
            return False

        try:
            hours_held = (time.time() - float(entry_ts)) / 3600.0
        except (TypeError, ValueError):
            return False

        if hours_held >= max_hold:
            logger.warning(
                f"TIME EXIT for {symbol} {pos.get('side')}: held "
                f"{hours_held:.1f}h >= {max_hold}h max ({strategy}) - closing"
            )
            return True

        return False

    def _check_trend_reversal(
        self,
        symbol: str,
        pos: Dict[str, Any],
        market_data: Optional[Dict[str, List[float]]] = None,
    ) -> bool:
        """
        Check if trend has reversed against the position.

        Args:
            symbol: Trading symbol
            pos: Position dict with 'side' and 'trend_direction'
            market_data: 4h market data

        Returns:
            True if trend reversed and position should be closed
        """
        if not market_data:
            return False

        original_trend = pos.get("trend_direction")
        if not original_trend:
            return False

        # Get current trend direction
        current_trend = self.regime_detector.get_trend_direction(market_data)

        # Check for reversal
        if original_trend == "up" and current_trend == "down":
            logger.warning(
                f"⚠️ TREND REVERSAL detected for {symbol}: "
                f"was {original_trend}, now {current_trend} - closing {pos['side']}"
            )
            return True

        if original_trend == "down" and current_trend == "up":
            logger.warning(
                f"⚠️ TREND REVERSAL detected for {symbol}: "
                f"was {original_trend}, now {current_trend} - closing {pos['side']}"
            )
            return True

        return False

    def _check_stop_hit(
        self, symbol: str, pos: Dict[str, Any], current_price: float
    ) -> bool:
        """
        Check if trailing stop has been hit.

        Args:
            symbol: Trading symbol
            pos: Position dict
            current_price: Current market price

        Returns:
            True if stop was hit
        """
        side = cast(str, pos.get("side"))
        stop_price = self._trailing_stops.get(symbol, {}).get(side)

        if not stop_price:
            return False

        # Check stop hit based on position side
        if side == "long" and current_price <= stop_price:
            logger.warning(
                f"📊 Trailing stop HIT for {symbol} LONG: "
                f"price ${current_price:.2f} <= stop ${stop_price:.2f}"
            )
            return True

        if side == "short" and current_price >= stop_price:
            logger.warning(
                f"📊 Trailing stop HIT for {symbol} SHORT: "
                f"price ${current_price:.2f} >= stop ${stop_price:.2f}"
            )
            return True

        return False

    def _update_trailing_stop(
        self,
        symbol: str,
        pos: Dict[str, Any],
        current_price: float,
        market_data: Optional[Dict[str, List[float]]] = None,
    ) -> bool:
        """
        Update trailing stop for position.

        Stop trails at 2x ATR distance:
        - LONG: stop trails BELOW price (only moves up)
        - SHORT: stop trails ABOVE price (only moves down)

        Args:
            symbol: Trading symbol
            pos: Position dict
            current_price: Current market price
            market_data: 4h market data for ATR

        Returns:
            True if stop was updated
        """
        side = cast(str, pos.get("side"))

        # Get ATR for stop distance
        atr = self._get_atr(symbol, market_data)
        if atr <= 0:
            # Fallback: use 2% of price as stop distance
            atr = current_price * 0.02
            logger.warning(f"Using fallback ATR for {symbol}: ${atr:.2f}")

        # Calculate stop distance
        stop_distance = max(
            atr * self.TRAILING_STOP_ATR_MULTIPLIER, atr * self.MIN_STOP_ATR_MULTIPLIER
        )

        # Initialize symbol in tracking if needed
        if symbol not in self._trailing_stops:
            self._trailing_stops[symbol] = {}

        current_stop = self._trailing_stops[symbol].get(side)

        if side == "long":
            # Long: stop trails below price
            new_stop = current_price - stop_distance

            # Only update if new stop is HIGHER (more favorable)
            if current_stop is None or new_stop > current_stop:
                self._trailing_stops[symbol][side] = new_stop

                # Update in RiskManager
                self.risk_manager.update_migrated_stop(symbol, side, new_stop)

                logger.info(
                    f"📊 Trailing stop updated: {symbol} LONG "
                    f"stop ${current_stop or 0:.2f} -> ${new_stop:.2f} "
                    f"(price ${current_price:.2f}, ATR ${atr:.2f})"
                )
                return True

        elif side == "short":
            # Short: stop trails above price
            new_stop = current_price + stop_distance

            # Only update if new stop is LOWER (more favorable)
            if current_stop is None or new_stop < current_stop:
                self._trailing_stops[symbol][side] = new_stop

                # Update in RiskManager
                self.risk_manager.update_migrated_stop(symbol, side, new_stop)

                logger.info(
                    f"📊 Trailing stop updated: {symbol} SHORT "
                    f"stop ${current_stop or float('inf'):.2f} -> ${new_stop:.2f} "
                    f"(price ${current_price:.2f}, ATR ${atr:.2f})"
                )
                return True

        return False

    def arm_trailing_stop(
        self,
        symbol: str,
        pos: Dict[str, Any],
        current_price: float,
        market_data: Optional[Dict[str, List[float]]] = None,
    ) -> bool:
        """
        Public wrapper to arm/tighten a trailing stop for a position.

        Used by the regime-flip position review to put a profitable but
        regime-misaligned position under the standard trailing mechanism
        (2x ATR, only-moves-favorably) without closing it.

        Args:
            symbol: Trading symbol.
            pos: Position dict with 'side' ('long'/'short'), 'qty',
                'entry_price'.
            current_price: Current market price.
            market_data: Optional 4h market data for ATR calculation.

        Returns:
            True if the stop was set or tightened.
        """
        return self._update_trailing_stop(symbol, pos, current_price, market_data)

    def close_position(self, symbol: str, pos: Dict[str, Any], reason: str) -> None:
        """
        Public wrapper to close a position through the standard close path.

        Places a market close order, unregisters RiskManager tracking, and
        cleans up trailing/TP state - identical to trailing_stop /
        trend_reversal exits. Used by the regime-flip position review with
        reason "regime_exit".

        Args:
            symbol: Trading symbol.
            pos: Position dict with 'side' and 'qty'.
            reason: Exit reason recorded with the close.
        """
        self._close_position(symbol, pos, reason)

    def _check_take_profit(
        self,
        symbol: str,
        pos: Dict[str, Any],
        current_price: float,
        market_data: Optional[Dict[str, List[float]]] = None,
    ) -> bool:
        """
        Check and execute partial take profit.

        Takes 50% profit at 3x ATR gain.

        Args:
            symbol: Trading symbol
            pos: Position dict
            current_price: Current market price
            market_data: 4h market data for ATR

        Returns:
            True if partial profit was taken
        """
        side = cast(str, pos.get("side"))
        entry_price = pos.get("entry_price", 0)
        qty = pos.get("qty", 0)

        if not entry_price or not qty:
            return False

        # Initialize tracking if needed
        if symbol not in self._take_profits:
            self._take_profits[symbol] = {}

        if side not in self._take_profits[symbol]:
            atr = self._get_atr(symbol, market_data)
            if atr <= 0:
                atr = entry_price * 0.02

            # Set TP target at 3x ATR
            if side == "long":
                target = entry_price + (atr * self.TAKE_PROFIT_ATR_MULTIPLIER)
            else:  # short
                target = entry_price - (atr * self.TAKE_PROFIT_ATR_MULTIPLIER)

            self._take_profits[symbol][side] = {
                "target": target,
                "partial_taken": False,
            }

        tp_info = self._take_profits[symbol][side]

        # Skip if partial already taken
        if tp_info.get("partial_taken"):
            return False

        # Check if target hit
        target_hit = False
        if side == "long" and current_price >= tp_info["target"]:
            target_hit = True
        elif side == "short" and current_price <= tp_info["target"]:
            target_hit = True

        if target_hit:
            # Take partial profit (50%) - reduce-only, sized from the exchange
            partial_qty = qty * self.PARTIAL_PROFIT_PCT
            return self._execute_partial_close(symbol, side, partial_qty, pos)

        return False

    def _execute_partial_close(
        self, symbol: str, side: str, partial_qty: float, pos: Dict[str, Any]
    ) -> bool:
        """Send a reduce-only partial close; mark partial_taken only on fills.

        Args:
            symbol: Trading symbol.
            side: Position side ("long"/"short").
            partial_qty: Quantity the take-profit rule wants to close.
            pos: Local position dict (qty used as the before-hint).

        Returns:
            True only when the exchange confirmed executed quantity > 0.
        """
        key = (symbol, side)
        tp_info = self._take_profits.get(symbol, {}).get(side)
        plan = plan_close_quantity(self.client, symbol, side, partial_qty)
        if plan.already_flat:
            logger.warning(
                f"Partial TP {symbol} {side}: exchange already flat - no order "
                f"sent; the full-close path / reconciler will clean up"
            )
            self._pending_closes.pop(key, None)
            return False
        result = self._send_reduce_only(
            symbol, side, plan.quantity, key, "partial", "take_profit"
        )
        if result is None:
            return False
        executed = self._confirmed_executed_quantity(
            symbol, side, plan, result, float(pos.get("qty", 0) or 0)
        )
        if executed is None:
            self._mark_pending_close(
                key,
                "partial",
                plan.quantity,
                "take_profit",
                "accepted but fill unconfirmed",
                ambiguous=True,
                accepted=True,
            )
            return False
        if executed <= 0:
            self._mark_pending_close(
                key,
                "partial",
                plan.quantity,
                "take_profit",
                "accepted but nothing executed",
                accepted=True,
            )
            return False
        self._pending_closes.pop(key, None)
        if tp_info is not None:
            tp_info["partial_taken"] = True
        logger.info(
            f"Partial take profit: {symbol} {side} executed {executed:.6f} "
            f"of {plan.quantity:.6f} (order {result.order_id})"
        )
        return True

    def _close_position(self, symbol: str, pos: Dict[str, Any], reason: str) -> None:
        """
        Close a migrated position with a reduce-only order sized from the
        exchange's current position.

        Accounting (RiskManager unregister, local cleanup, DB close) runs
        ONLY after the executed quantity is confirmed.  A rejected,
        raised, or unconfirmed close leaves the position registered and
        records a pending-close marker that ``manage_positions`` retries.

        Args:
            symbol: Trading symbol
            pos: Position dict
            reason: Exit reason ('trailing_stop', 'trend_reversal', 'manual')
        """
        side = cast(str, pos.get("side"))
        qty = float(pos.get("qty", 0) or 0)
        if qty <= 0:
            return
        key = (symbol, side)

        plan = plan_close_quantity(self.client, symbol, side, qty)
        if plan.already_flat:
            logger.warning(
                f"Close {symbol} {side} ({reason}): exchange already flat - "
                f"no order sent, reconciling local state"
            )
            self._finalize_close(symbol, side, qty, f"{reason}:already_flat")
            return
        if plan.clamped:
            logger.warning(
                f"Close {symbol} {side}: clamping {qty:.6f} to exchange "
                f"quantity {plan.quantity:.6f}"
            )

        result = self._send_reduce_only(
            symbol, side, plan.quantity, key, "full", reason
        )
        if result is None:
            return
        executed = self._confirmed_executed_quantity(symbol, side, plan, result, qty)
        if executed is None:
            self._mark_pending_close(
                key,
                "full",
                plan.quantity,
                reason,
                "accepted but fill unconfirmed",
                ambiguous=True,
                accepted=True,
            )
            return
        if executed + 1e-9 < plan.quantity:
            self._mark_pending_close(
                key,
                "full",
                plan.quantity - executed,
                reason,
                f"partial execution {executed:.6f}/{plan.quantity:.6f}",
                accepted=True,
            )
            return
        self._finalize_close(symbol, side, qty, reason)

    def _send_reduce_only(
        self,
        symbol: str,
        side: str,
        quantity: float,
        key: Tuple[str, str],
        kind: str,
        reason: str,
    ) -> Optional[OrderResult]:
        """Place a reduce-only market close and normalize the outcome.

        Returns:
            The accepted OrderResult, or None when the order was rejected
            or raised (a pending-close marker has been recorded).
        """
        close_side = "sell" if side == "long" else "buy"
        try:
            ack = self.client.place_order(
                symbol, close_side, quantity, "market", reduce_only=True
            )
        except Exception as exc:  # noqa: BLE001 - outcome is ambiguous
            self._mark_pending_close(
                key,
                kind,
                quantity,
                reason,
                f"{type(exc).__name__}: {exc}",
                ambiguous=True,
            )
            return None
        result = OrderResult.from_ack(ack)
        if not result.accepted:
            self._mark_pending_close(
                key, kind, quantity, reason, result.error or "order rejected"
            )
            return None
        return result

    def _confirmed_executed_quantity(
        self,
        symbol: str,
        side: str,
        plan: ClosePlan,
        result: OrderResult,
        local_before: float,
    ) -> Optional[float]:
        """Executed quantity confirmed by ack fills or the exchange position.

        Returns:
            Executed quantity, or None when it cannot be confirmed.
        """
        if result.has_fills:
            return min(plan.quantity, result.filled_quantity)
        remaining_after = remaining_exchange_quantity(self.client, symbol, side)
        if remaining_after is None:
            return None
        before = (
            plan.exchange_quantity
            if plan.exchange_quantity is not None
            else local_before
        )
        return max(0.0, min(plan.quantity, before - remaining_after))

    def _mark_pending_close(
        self,
        key: Tuple[str, str],
        kind: str,
        quantity: float,
        reason: str,
        error: str,
        ambiguous: bool = False,
        accepted: bool = False,
    ) -> None:
        """Record / bump a pending-close marker; no accounting happens here."""
        marker = self._pending_closes.get(key)
        if marker is None:
            marker = {
                "kind": kind,
                "reason": reason,
                "attempts": 0,
                "first_failed_at": time.time(),
                "escalated": False,
            }
            self._pending_closes[key] = marker
        marker["attempts"] += 1
        marker["qty"] = quantity
        marker["last_error"] = error
        marker["ambiguous"] = ambiguous
        marker["accepted"] = accepted
        marker["last_attempt_at"] = time.time()
        logger.error(
            f"CLOSE NOT CONFIRMED {key[0]} {key[1]} ({kind}, reason={reason}): "
            f"{error} - attempt {marker['attempts']}/{self._max_close_attempts}; "
            f"position kept, no accounting applied"
        )
        if ambiguous:
            self._request_reconcile(key)

    def _request_reconcile(self, key: Tuple[str, str]) -> None:
        """Run the position reconciler after an ambiguous outcome."""
        if self._reconcile_callback is None:
            return
        try:
            self._reconcile_callback()
        except Exception as exc:  # noqa: BLE001 - reconciliation is best effort
            logger.warning(f"Reconcile after ambiguous close {key} failed: {exc}")

    def _retry_pending_closes(self, positions: List[Dict[str, Any]]) -> int:
        """Retry pending closes up to MIGRATED_CLOSE_MAX_ATTEMPTS.

        Args:
            positions: Current RiskManager migrated positions.

        Returns:
            Number of closes retried this cycle.
        """
        if not self._pending_closes:
            return 0
        by_key = {(p.get("symbol"), p.get("side")): p for p in positions}
        retried = 0
        for key in list(self._pending_closes):
            marker = self._pending_closes[key]
            pos = by_key.get(key)
            if pos is None:
                logger.warning(
                    f"Pending close {key} no longer registered - dropping marker"
                )
                self._pending_closes.pop(key, None)
                continue
            if marker["attempts"] >= self._max_close_attempts:
                if not marker.get("escalated"):
                    marker["escalated"] = True
                    marker["escalated_at"] = time.time()
                    self._escalate_pending_close(key, marker)
                continue
            retried += 1
            if marker.get("kind") == "partial":
                self._execute_partial_close(key[0], key[1], float(marker["qty"]), pos)
            else:
                self._close_position(key[0], pos, str(marker.get("reason", "retry")))
        return retried

    def _escalate_pending_close(
        self, key: Tuple[str, str], marker: Dict[str, Any]
    ) -> None:
        """Alert on a close that hit the retry cap: log, EventBus, Telegram.

        Every channel is best effort - a failure is logged and never raised.
        """
        message = (
            f"Pending close {key[0]} {key[1]} reached the retry cap "
            f"({self._max_close_attempts}); position kept open - manual "
            f"action required. Last error: {marker.get('last_error')}"
        )
        logger.error(message)
        payload = {
            "symbol": key[0],
            "side": key[1],
            "source_kind": "migrated_close",
            "kind": marker.get("kind"),
            "reason": marker.get("reason"),
            "qty": marker.get("qty"),
            "attempts": marker.get("attempts"),
            "last_error": marker.get("last_error"),
        }
        try:
            from .event_system import EventType, get_event_bus

            get_event_bus().publish_event(
                EventType.CLOSE_ESCALATED, payload, "MigratedPositionManager"
            )
        except Exception as exc:  # noqa: BLE001 - alerting is best effort
            logger.warning(f"Could not publish CLOSE_ESCALATED for {key}: {exc}")
        try:
            _send_telegram_error_alert(
                "close_escalated", message, f"{key[0]} {key[1]} {marker.get('kind')}"
            )
        except Exception as exc:  # noqa: BLE001 - alerting is best effort
            logger.warning(f"Telegram escalation for {key} failed: {exc}")

    def get_pending_closes(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Return a copy of the pending-close markers (for status/tests)."""
        return {key: dict(marker) for key, marker in self._pending_closes.items()}

    def get_escalated_closes(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Return the pending closes that hit the retry cap (needs a human)."""
        return {
            key: dict(marker)
            for key, marker in self._pending_closes.items()
            if marker.get("escalated")
        }

    def _finalize_close(self, symbol: str, side: str, qty: float, reason: str) -> None:
        """Apply accounting for a CONFIRMED close (exchange flat / fully filled)."""
        self._pending_closes.pop((symbol, side), None)
        try:
            self.risk_manager.unregister_migrated_position(symbol, side, qty)
        except Exception as exc:  # noqa: BLE001 - keep cleaning up local state
            logger.error(f"Unregister failed for {symbol} {side}: {exc}")

        if symbol in self._trailing_stops and side in self._trailing_stops[symbol]:
            del self._trailing_stops[symbol][side]
        if symbol in self._take_profits and side in self._take_profits[symbol]:
            del self._take_profits[symbol][side]
        self._venue_stops.pop((symbol, side), None)

        logger.critical(
            f"Migrated position CLOSED: {symbol} {side} {qty:.6f} (reason: {reason})"
        )
        self._update_db_position(symbol, side, qty, reason)

    # ------------------------------------------------------------------
    # Venue stop coordination (live-readiness audit T4)
    # ------------------------------------------------------------------

    def _venue_exchange(self) -> Optional[Any]:
        """Exchange adapter with venue stops, or None (local-only)."""
        if self._venue_exchange_resolver is None:
            return None
        try:
            exchange = self._venue_exchange_resolver()
        except Exception as exc:  # noqa: BLE001 - resolver failure -> local only
            logger.warning(f"Venue exchange unavailable for stop sync: {exc}")
            return None
        return exchange if venue_stops_supported(exchange) else None

    def _venue_stop_state(
        self, symbol: str, side: str, pos: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Mirror record for a position's venue stop (loaded on first use).

        Sources, in order: the in-memory mirror, keys on the position
        dict (venue_stop_id / venue_stop_price / entry_order_id /
        venue_stop_state), then the positions table.
        """
        key = (symbol, side)
        state = self._venue_stops.get(key)
        if state is not None:
            return state
        state = {
            "stop_id": pos.get("venue_stop_id"),
            "price": pos.get("venue_stop_price"),
            "entry_order_id": pos.get("entry_order_id"),
            "state": pos.get("venue_stop_state"),
            "failures": 0,
            "fallback_local": False,
        }
        if not state["stop_id"] and not state["price"]:
            row = self._load_db_position(symbol, side)
            if row and row.get("venue_stop_state") in PROTECTED_STATES:
                state.update(
                    stop_id=row.get("venue_stop_id"),
                    price=row.get("venue_stop_price"),
                    entry_order_id=row.get("entry_order_id"),
                    state=row.get("venue_stop_state"),
                )
        self._venue_stops[key] = state
        return state

    def _load_db_position(self, symbol: str, side: str) -> Optional[Dict[str, Any]]:
        """Read the positions row (side stored uppercase) or None."""
        getter = getattr(self._db, "get_position", None)
        if not callable(getter):
            return None
        try:
            return cast(Optional[Dict[str, Any]], getter(symbol, str(side).upper()))
        except Exception as exc:  # noqa: BLE001 - DB read failure -> unknown
            logger.debug(f"Could not load position row {symbol} {side}: {exc}")
            return None

    def _write_venue_stop(self, symbol: str, side: str, fields: Dict[str, Any]) -> None:
        """Persist venue stop id/price/state to the positions table."""
        writer = getattr(self._db, "record_position_protection", None)
        if not callable(writer):
            return
        try:
            writer(symbol, str(side).upper(), fields)
        except Exception as exc:  # noqa: BLE001 - persistence must not break exits
            logger.warning(f"Could not write venue stop for {symbol} {side}: {exc}")

    def _sync_venue_stop(self, symbol: str, pos: Dict[str, Any]) -> bool:
        """Mirror the current trailing level onto the venue's stop order.

        Skips moves smaller than VENUE_STOP_MIN_MOVE_PCT (so the venue is
        not hammered), keeps the last known good venue price on an amend
        failure and retries next loop, and after
        VENUE_STOP_MAX_AMEND_FAILURES consecutive failures falls back to
        the local stop for good (ERROR logged, DB state -> missing).

        Args:
            symbol: Trading symbol.
            pos: Migrated position dict (side, qty, ...).

        Returns:
            True when the venue stop was moved this call.
        """
        side = cast(str, pos.get("side"))
        new_stop = self._trailing_stops.get(symbol, {}).get(side)
        if not new_stop:
            return False
        exchange = self._venue_exchange()
        if exchange is None:
            return False
        state = self._venue_stop_state(symbol, side, pos)
        if state.get("fallback_local"):
            return False
        move = stop_move_pct(state.get("price"), new_stop)
        if move < self._venue_stop_min_move_pct:
            logger.debug(
                f"Venue stop {symbol} {side}: move {move:.4f}% below "
                f"{self._venue_stop_min_move_pct}% threshold - not amended"
            )
            return False
        if state.get("stop_id") or state.get("entry_order_id"):
            result = exchange.amend_stop(
                symbol,
                side,
                new_stop,
                entry_order_id=state.get("entry_order_id"),
                stop_id=state.get("stop_id"),
            )
        else:
            result = exchange.install_stop(
                symbol, side, float(pos.get("qty", 0) or 0), new_stop
            )
        if result.accepted:
            self._record_venue_stop_success(symbol, side, state, result, new_stop)
            return True
        self._record_venue_stop_failure(symbol, side, state, result, new_stop)
        return False

    def _record_venue_stop_success(
        self,
        symbol: str,
        side: str,
        state: Dict[str, Any],
        result: OrderResult,
        new_stop: float,
    ) -> None:
        """Update the mirror + DB after the venue accepted the new level."""
        method = str((result.raw or {}).get("data", {}).get("method", "") or "")
        if method == "amend-order":
            venue_state = state.get("state") or STOP_STATE_ATTACHED
        else:
            venue_state = STOP_STATE_STANDALONE
        state.update(
            stop_id=result.order_id or state.get("stop_id"),
            price=new_stop,
            state=venue_state,
            failures=0,
        )
        logger.info(
            f"Venue stop moved: {symbol} {side} -> {new_stop:.6f} "
            f"(id {state['stop_id']}, {method or 'venue'})"
        )
        self._write_venue_stop(
            symbol,
            side,
            {
                "venue_stop_id": state["stop_id"],
                "venue_stop_price": new_stop,
                "venue_stop_state": venue_state,
            },
        )

    def _record_venue_stop_failure(
        self,
        symbol: str,
        side: str,
        state: Dict[str, Any],
        result: OrderResult,
        new_stop: float,
    ) -> None:
        """Count an amend failure; fall back to local after the cap."""
        state["failures"] = int(state.get("failures", 0)) + 1
        cap = self._venue_stop_max_amend_failures
        logger.warning(
            f"Venue stop amend failed for {symbol} {side} -> {new_stop:.6f} "
            f"({state['failures']}/{cap}): {result.error}; keeping venue stop at "
            f"{state.get('price')} and retrying next loop"
        )
        if state["failures"] < cap:
            return
        state["fallback_local"] = True
        logger.error(
            f"Venue stop for {symbol} {side} failed {cap} consecutive amends - "
            f"falling back to LOCAL stop enforcement (venue stop, if any, stays at "
            f"{state.get('price')})"
        )
        self._write_venue_stop(symbol, side, {"venue_stop_state": STOP_STATE_MISSING})

    def _update_db_position(
        self, symbol: str, side: str, qty: float, exit_reason: str
    ) -> None:
        """Update position status in database."""
        try:
            from .database import get_db_connection

            with get_db_connection() as conn:
                conn.execute(
                    """
                    UPDATE grid_positions
                    SET status = 'closed', exit_reason = ?, closed_at = CURRENT_TIMESTAMP
                    WHERE symbol = ? AND side = ? AND status = 'migrated'
                    ORDER BY created_at DESC
                    LIMIT 1
                """,
                    (exit_reason, symbol, side),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not update position in DB: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get status of all managed positions."""
        positions = self.risk_manager.get_migrated_positions()

        return {
            "total_positions": len(positions),
            "positions": positions,
            "trailing_stops": dict(self._trailing_stops),
            "take_profit_targets": {
                sym: {side: info["target"] for side, info in sides.items()}
                for sym, sides in self._take_profits.items()
            },
        }

    def force_close_all(self, reason: str = "manual") -> None:
        """Force close all migrated positions."""
        positions = self.risk_manager.get_migrated_positions()

        for pos in positions:
            symbol = pos.get("symbol")
            if symbol:
                self._close_position(symbol, pos, reason)

        logger.critical(f"All migrated positions force-closed (reason: {reason})")
