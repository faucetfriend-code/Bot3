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

from typing import Dict, Any, List, Optional
from datetime import datetime
from loguru import logger


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

    def __init__(self, client, risk_manager, regime_detector, multi_tf_fetcher=None):
        """
        Initialize MigratedPositionManager.

        Args:
            client: Exchange client (PacificaClient)
            risk_manager: Central RiskManager instance (has migrated positions tracking)
            regime_detector: MarketRegimeDetector for trend direction
            multi_tf_fetcher: Optional MultiTimeframeFetcher for ATR calculation
        """
        self.client = client
        self.risk_manager = risk_manager
        self.regime_detector = regime_detector
        self.multi_tf_fetcher = multi_tf_fetcher

        # Trailing stop tracking: {symbol: {side: stop_price}}
        self._trailing_stops: Dict[str, Dict[str, float]] = {}

        # Take profit tracking: {symbol: {side: {'target': price, 'partial_taken': bool}}}
        self._take_profits: Dict[str, Dict[str, Dict]] = {}

        # Last known ATR per symbol (cached)
        self._atr_cache: Dict[str, Dict[str, Any]] = {}
        self._atr_cache_ttl_seconds = 300  # 5 minute cache

        logger.info("MigratedPositionManager initialized")

    def has_positions(self) -> bool:
        """Check if there are any migrated positions to manage."""
        return self.risk_manager.has_migrated_positions()

    def manage_positions(
        self, current_prices: Dict[str, float] = None
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
        results = {
            "positions_managed": 0,
            "stops_updated": 0,
            "positions_closed": 0,
            "partial_profits": 0,
            "errors": [],
        }

        # Get all migrated positions from RiskManager
        positions = self.risk_manager.get_migrated_positions()

        if not positions:
            return results

        for pos in positions:
            symbol = pos.get("symbol")
            if not symbol:
                continue

            results["positions_managed"] += 1

            try:
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

                # 3. Update trailing stop
                if self._update_trailing_stop(symbol, pos, current_price, market_data):
                    results["stops_updated"] += 1

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
            ticker = self.client.get_ticker(symbol)
            if ticker:
                return float(
                    ticker.get("last")
                    or ticker.get("price")
                    or ticker.get("mark_price", 0)
                )
        except Exception as e:
            logger.warning(f"Could not get price for {symbol}: {e}")
        return None

    def _get_market_data(self, symbol: str) -> Optional[Dict[str, List]]:
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

    def _get_atr(self, symbol: str, market_data: Dict[str, List] = None) -> float:
        """
        Get ATR for symbol (with caching).

        Args:
            symbol: Trading symbol
            market_data: Optional pre-fetched market data

        Returns:
            ATR value, or 0 if calculation fails
        """
        from datetime import datetime
        import time

        # Check cache
        if symbol in self._atr_cache:
            cache = self._atr_cache[symbol]
            if time.time() - cache["timestamp"] < self._atr_cache_ttl_seconds:
                return cache["atr"]

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

    def _check_trend_reversal(
        self, symbol: str, pos: Dict, market_data: Dict = None
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

    def _check_stop_hit(self, symbol: str, pos: Dict, current_price: float) -> bool:
        """
        Check if trailing stop has been hit.

        Args:
            symbol: Trading symbol
            pos: Position dict
            current_price: Current market price

        Returns:
            True if stop was hit
        """
        side = pos.get("side")
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
        self, symbol: str, pos: Dict, current_price: float, market_data: Dict = None
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
        side = pos.get("side")
        entry_price = pos.get("entry_price", 0)

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

    def _check_take_profit(
        self, symbol: str, pos: Dict, current_price: float, market_data: Dict = None
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
        side = pos.get("side")
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
            # Take partial profit (50%)
            partial_qty = qty * self.PARTIAL_PROFIT_PCT

            try:
                close_side = "sell" if side == "long" else "buy"
                self.client.place_order(symbol, close_side, partial_qty, "market")

                tp_info["partial_taken"] = True

                logger.info(
                    f"📊 Partial take profit: {symbol} {side} "
                    f"closed {partial_qty:.6f} @ ${current_price:.2f} "
                    f"(target ${tp_info['target']:.2f})"
                )

                return True

            except Exception as e:
                logger.error(f"Failed to take partial profit for {symbol}: {e}")

        return False

    def _close_position(self, symbol: str, pos: Dict, reason: str):
        """
        Close a migrated position.

        Args:
            symbol: Trading symbol
            pos: Position dict
            reason: Exit reason ('trailing_stop', 'trend_reversal', 'manual')
        """
        side = pos.get("side")
        qty = pos.get("qty", 0)

        if qty <= 0:
            return

        try:
            # Place market close order
            close_side = "sell" if side == "long" else "buy"
            self.client.place_order(symbol, close_side, qty, "market")

            # Unregister from RiskManager
            self.risk_manager.unregister_migrated_position(symbol, side, qty)

            # Clean up local tracking
            if symbol in self._trailing_stops and side in self._trailing_stops[symbol]:
                del self._trailing_stops[symbol][side]
            if symbol in self._take_profits and side in self._take_profits[symbol]:
                del self._take_profits[symbol][side]

            logger.critical(
                f"📊 Migrated position CLOSED: {symbol} {side} {qty:.6f} "
                f"(reason: {reason})"
            )

            # Update database
            self._update_db_position(symbol, side, qty, reason)

        except Exception as e:
            logger.error(f"Failed to close migrated position {symbol} {side}: {e}")

    def _update_db_position(self, symbol: str, side: str, qty: float, exit_reason: str):
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

    def force_close_all(self, reason: str = "manual"):
        """Force close all migrated positions."""
        positions = self.risk_manager.get_migrated_positions()

        for pos in positions:
            symbol = pos.get("symbol")
            if symbol:
                self._close_position(symbol, pos, reason)

        logger.critical(f"All migrated positions force-closed (reason: {reason})")
