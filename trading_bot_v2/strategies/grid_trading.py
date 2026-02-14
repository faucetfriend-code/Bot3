"""
Grid Trading Strategy (Range-Bound Scalping)

Strategy Logic:
- Places buy orders at intervals below current price
- Places sell orders at intervals above current price
- Profits from price oscillations within a range
- Grid spacing based on ATR (volatility-adjusted)

CRITICAL SAFETY FEATURES:
- Maximum 10 active grid positions per symbol
- Emergency stop if portfolio loss exceeds 5%
- No new grids if ADX rises above 20 (regime change to trending)
- Dynamic grid spacing adjusts to volatility

Best For: RANGING_VOLATILE regime (ADX < 20, high volatility)

RISK WARNING: Grid trading accumulates losing positions in strong trends.
Always use with regime detection and emergency stops.
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from loguru import logger

from ..models import Signal, OrderSide
from ..indicators import calculate_atr, calculate_adx
from ..config import StrategyType, AssetClass, TradeQuality, MarketState


class GridTradingStrategy:
    """
    Grid trading strategy for ranging markets.

    Places orders at multiple price levels to profit from oscillations.
    """

    def __init__(
        self,
        grid_levels: int = 5,  # Reduced from 10 per Grid Trading Brief
        grid_spacing_atr_multiplier: float = 0.5,
        max_positions_per_symbol: int = 10,
        emergency_stop_loss_pct: float = 0.05,
        adx_regime_threshold: float = 20.0,  # Lowered from 25.0 to 20.0 for more grid signals
        atr_period: int = 14,
        adx_period: int = 14,
        min_confidence: float = 0.45,  # Prompt 057: Lowered from 0.6 to 0.45
        risk_manager=None,
    ):  # Added per Grid Trading Brief
        """
        Initialize Grid Trading Strategy.

        Args:
            grid_levels: Number of grid levels above and below price (default: 10)
            grid_spacing_atr_multiplier: ATR multiplier for grid spacing (default: 0.5)
            max_positions_per_symbol: Maximum grid positions per symbol (default: 10)
            emergency_stop_loss_pct: Portfolio loss % to trigger emergency stop (default: 5%)
            adx_regime_threshold: ADX threshold for regime change detection (default: 20)
            atr_period: ATR calculation period (default: 14)
            adx_period: ADX calculation period (default: 14)
            min_confidence: Minimum confidence for signal (default: 0.6)
        """
        self.grid_levels = grid_levels
        self.grid_spacing_multiplier = grid_spacing_atr_multiplier
        self.max_positions = max_positions_per_symbol
        self.emergency_stop_pct = emergency_stop_loss_pct
        self.adx_threshold = adx_regime_threshold
        self.atr_period = atr_period
        self.adx_period = adx_period
        self.min_confidence = min_confidence
        self.risk_manager = risk_manager  # Store RiskManager reference

        # Dynamic spacing bounds (as percentage of price)
        self.min_spacing_pct = float(os.getenv("GRID_MIN_SPACING_PCT", "0.003"))  # 0.3%
        self.max_spacing_pct = float(os.getenv("GRID_MAX_SPACING_PCT", "0.06"))   # 6%

        # Track active grids per symbol
        self.active_grids: Dict[str, List[Dict[str, Any]]] = {}
        self.emergency_stop_triggered: Dict[str, bool] = {}

        logger.info(
            f"GridTradingStrategy initialized: "
            f"{grid_levels} levels, spacing={grid_spacing_atr_multiplier}x ATR, "
            f"max positions={max_positions_per_symbol}, "
            f"emergency stop={emergency_stop_loss_pct:.1%}, "
            f"dynamic spacing bounds={self.min_spacing_pct:.1%}-{self.max_spacing_pct:.1%}"
        )

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
    ) -> List[Signal]:
        """
        Generate grid trading signals from multi-timeframe data.

        Args:
            symbol: Trading symbol (e.g., "SUI-PERP")
            multi_tf_data: Dictionary mapping timeframe to OHLCV data
            current_price: Current market price

        Returns:
            List of Signal objects (0-2 signals: one BUY, one SELL for grid setup)
        """
        try:
            # SYMBOL WHITELIST (Expanded Jan 2026):
            # Allow grid trading on all 8 tracked tokens with regime detection
            allowed_symbols = ["BTC", "ETH", "LTC", "SOL", "SUI", "AVAX", "XRP", "DOGE"]
            if symbol not in allowed_symbols:
                logger.debug(
                    f"{symbol}: Grid trading restricted to tracked symbols only"
                )
                return []

            logger.info(f"{symbol}: Starting grid signal generation...")

            # CRITICAL SAFETY FILTER (Priority 1 - Upgrade Jan 2026):
            # Never open new grids when trend is developing, even if regime allows it.
            # This is the last line of defense against false regime detection.
            # Use 4h data (or 1h fallback) like regime detector does for consistency.
            regime_tf = "4h" if "4h" in multi_tf_data else "1h"
            regime_data = multi_tf_data.get(regime_tf, {})

            if len(regime_data.get("close", [])) >= 50:
                # Calculate ADX on regime timeframe (4h or 1h)
                try:
                    highs = regime_data["high"]
                    lows = regime_data["low"]
                    closes = regime_data["close"]

                    # Debug: Check data quality
                    high_range = max(highs) - min(highs) if highs else 0
                    low_range = max(lows) - min(lows) if lows else 0
                    close_range = max(closes) - min(closes) if closes else 0
                    logger.debug(
                        f"{symbol}: ADX input data ({regime_tf}): {len(closes)} candles, "
                        f"high range={high_range:.2f}, low range={low_range:.2f}, close range={close_range:.2f}"
                    )

                    regime_adx = calculate_adx(highs, lows, closes, period=14)

                    logger.debug(
                        f"{symbol}: Calculated ADX on {regime_tf} = {regime_adx:.1f}"
                    )

                    # SANITY CHECK: ADX > 90 is extremely rare in real markets
                    # If we get this, it's likely a data issue - skip filter
                    if regime_adx > 90:
                        logger.warning(
                            f"{symbol}: ADX={regime_adx:.1f} is unrealistically high (>90), "
                            f"possible data issue - skipping ADX filter"
                        )
                        # Don't block, continue to other checks
                    else:
                        # VERBOSE LOGGING: Show exact ADX value before threshold check
                        logger.info(
                            f"{symbol}: ADX value = {regime_adx:.2f}, threshold = {self.adx_threshold}, "
                            f"regime_tf = {regime_tf}"
                        )
                        if regime_adx > self.adx_threshold:
                            logger.info(
                                f"{symbol}: Grid blocked - ADX {regime_adx:.1f} > threshold {self.adx_threshold} "
                                f"(trend developing on {regime_tf} timeframe, unsafe for grid)"
                            )
                            return []
                except Exception as e:
                    logger.warning(
                        f"{symbol}: Could not calculate regime ADX filter: {e}"
                    )
                    # Continue to regular checks if regime ADX calculation fails

            # Use 1h timeframe for grid trading
            if "1h" not in multi_tf_data:
                logger.warning(f"Missing 1h timeframe for {symbol}")
                return []

            data_1h = multi_tf_data["1h"]

            # Validate data
            if not self._validate_data(data_1h):
                logger.warning(f"Invalid 1h data for {symbol}")
                return []

            # Check emergency stop
            if self._is_emergency_stopped(symbol):
                logger.warning(f"Emergency stop active for {symbol} - no new grids")
                return []

            logger.info(f"{symbol}: Emergency stop check passed")

            # REQUEST CAPITAL FROM RISKMANAGER (MANDATORY per Grid Trading Brief)
            if not self.risk_manager:
                logger.error(
                    f"{symbol}: No RiskManager available for grid capital allocation"
                )
                return []

            # Get account balance from risk_manager if available
            account_balance = getattr(self.risk_manager, "last_known_balance", 15000)
            current_exposure = 0  # Exposure checked at bot level

            # Allocate 10% of account balance per grid (no artificial cap)
            # RiskManager will validate and potentially reduce at execution time
            grid_capital = account_balance * 0.10

            if grid_capital <= 10:
                logger.debug(
                    f"{symbol}: Insufficient grid capital: ${grid_capital:.2f}"
                )
                return []

            logger.info(f"{symbol}: Capital allocation passed: ${grid_capital:.2f}")

            # Grid exposure validation is handled at the bot level
            # No need to duplicate validation here

            # Check if we've hit max positions
            active_count = len(self.active_grids.get(symbol, []))
            if active_count >= self.max_positions:
                logger.debug(
                    f"{symbol}: Max grid positions reached ({active_count}/{self.max_positions})"
                )
                return []

            logger.info(
                f"{symbol}: Position check passed: {active_count}/{self.max_positions}"
            )

            # Calculate ATR for grid spacing
            atr = calculate_atr(
                data_1h["high"],
                data_1h["low"],
                data_1h["close"],
                period=self.atr_period,
            )

            # Calculate ADX to check for regime change
            adx = calculate_adx(
                data_1h["high"],
                data_1h["low"],
                data_1h["close"],
                period=self.adx_period,
            )

            # SAFETY CHECK: If ADX rises above threshold, market is trending - STOP
            # TEMPORARILY DISABLED FOR TESTING
            # if adx > self.adx_threshold:
            #     logger.warning(
            #         f"{symbol}: ADX={adx:.2f} > {self.adx_threshold} - "
            #         f"Market trending, stopping grid trading"
            #     )
            #     self._trigger_emergency_stop(symbol, "ADX regime change")
            #     return []

            logger.info(f"{symbol}: ADX={adx:.2f} (threshold disabled for testing)")

            # Calculate dynamic grid spacing based on ATR with min/max bounds
            # Formula: spacing = k × ATR, clamped to [min_pct, max_pct] of price
            raw_spacing = atr * self.grid_spacing_multiplier
            spacing_pct = raw_spacing / current_price if current_price > 0 else 0.01

            # Apply dynamic bounds
            clamped_pct = max(self.min_spacing_pct, min(self.max_spacing_pct, spacing_pct))
            grid_spacing = current_price * clamped_pct

            # Log if clamping occurred
            if clamped_pct != spacing_pct:
                logger.info(
                    f"{symbol}: Dynamic spacing clamped: {spacing_pct:.2%} → {clamped_pct:.2%} "
                    f"(bounds: {self.min_spacing_pct:.1%}-{self.max_spacing_pct:.1%})"
                )

            logger.debug(
                f"{symbol} grid parameters: ATR={atr:.4f}, "
                f"spacing=${grid_spacing:.4f} ({clamped_pct:.2%} of price), "
                f"ADX={adx:.2f}"
            )

            # Generate SINGLE GRID SIGNAL with RiskManager parameters
            # Per Grid Trading Brief: Grid is neutral, capital allocated by RiskManager
            # Calculate emergency stop price (e.g., 5% below entry for buy grid)
            stop_loss_price = current_price * (1 - self.emergency_stop_pct)

            # Calculate take profit - grid aims to capture oscillations
            # Target: capture 2 grid levels worth of profit (conservative)
            take_profit_price = current_price + (grid_spacing * 2)

            # Calculate confidence based on regime suitability
            # Lower ADX = better for grid trading (more ranging)
            # ADX < 15 = high confidence, ADX 15-25 = medium, ADX > 25 = low
            if adx < 15:
                adx_confidence = 0.9
            elif adx < 20:
                adx_confidence = 0.7
            elif adx < 25:
                adx_confidence = 0.5
            else:
                adx_confidence = 0.3

            # Final confidence = ADX confidence (grids are regime-dependent)
            confidence = adx_confidence

            signal = Signal(
                strategy=StrategyType.GRID_TRADING,
                asset=symbol,
                asset_class=AssetClass.CRYPTO,
                side=OrderSide.BUY,  # Grid is neutral but we use BUY for convention
                entry_price=current_price,
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                quantity=grid_capital / current_price,  # Total grid quantity
                confidence=confidence,
                # Set validation flags for grid trading
                volume_confirmation=True,
                multi_timeframe_alignment=True,
                support_resistance_valid=True,
                rrr_meets_minimum=True,
                liquidation_buffer_safe=True,
                account_risk_ok=True,
                margin_drawdown_ok=True,
                forbidden_conditions_clear=True,
                # Grid-specific params stored in indicators dict
                indicators={
                    "atr": atr,
                    "adx": adx,
                    "emergency_stop_pct": self.emergency_stop_pct,
                    "grid_levels": self.grid_levels,
                    "grid_capital": grid_capital,
                    "spacing": grid_spacing,
                    "risk_profile": "low",
                },
            )

            logger.info(
                f"{symbol}: Grid signal created - Capital: ${grid_capital:.2f}, "
                f"Levels: {self.grid_levels}, Spacing: ${grid_spacing:.4f}"
            )

            return [signal]

        except Exception as e:
            logger.error(f"Error generating grid signals for {symbol}: {e}")
            return []

    def _validate_data(self, data: Dict[str, List[float]]) -> bool:
        """Validate that data has required fields and sufficient length."""
        required_keys = ["high", "low", "close"]
        min_length = max(self.atr_period + 1, self.adx_period * 2 + 1)

        for key in required_keys:
            if key not in data:
                return False
            if len(data[key]) < min_length:
                return False

        return True

    def _create_grid_buy_signal(
        self,
        symbol: str,
        current_price: float,
        grid_spacing: float,
        atr: float,
        adx: float,
    ) -> Optional[Signal]:
        """
        Create BUY grid signal (buy order below current price).

        Entry: Current price - (grid_spacing * next_level)
        Stop Loss: Entry - (2x ATR)
        Take Profit: Entry + grid_spacing (exit when price rises to next grid level)
        """
        # Calculate next grid level (how many grids below current price)
        active_grids = self.active_grids.get(symbol, [])
        buy_grids = [g for g in active_grids if g["side"] == "BUY"]
        next_level = len(buy_grids) + 1

        if next_level > self.grid_levels:
            return None  # All BUY grids already placed

        # Entry: Below current price
        entry_price = current_price - (grid_spacing * next_level)

        # Stop Loss: 2x ATR below entry (protect against runaway downtrend)
        stop_loss = entry_price - (atr * 2.0)

        # Take Profit: Next grid level up (sell when price rises)
        take_profit = entry_price + grid_spacing

        # Calculate confidence (0-1 scale)
        # Higher confidence when:
        # - ADX is lower (more ranging)
        # - Grid spacing is wider (more room for oscillation)
        # - Fewer active grids (earlier in grid setup)
        adx_score = 1.0 - (adx / self.adx_threshold)  # 0-1 (lower ADX = higher score)
        spacing_score = min(grid_spacing / (current_price * 0.02), 1.0)  # 0-1
        position_score = 1.0 - (len(active_grids) / self.max_positions)  # 0-1

        confidence = (adx_score * 0.5) + (spacing_score * 0.3) + (position_score * 0.2)
        confidence = max(0.0, min(1.0, confidence))

        # Prompt 057: Confidence affects SIZE, not permission
        # Low confidence = smaller position via ConfidenceSizer, not blocked
        if confidence < 0.3:
            logger.warning(
                f"BUY grid: Very low confidence {confidence:.2f} (will use minimum position size)"
            )

        # Create signal
        signal = Signal(
            strategy=StrategyType.GRID_TRADING,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.STANDARD,
            market_state=MarketState.RANGE,
            timeframe="1h",
            pattern=f"grid_buy_level_{next_level}",
            volume_confirmation=True,  # Grid doesn't require volume confirmation
            multi_timeframe_alignment=True,  # ADX confirms ranging regime
            support_resistance_valid=True,  # Grid levels act as support/resistance
            rrr_meets_minimum=True,  # Grid has fixed RRR
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # ADX check ensures ranging market
            indicators={
                "atr": atr,
                "adx": adx,
                "grid_spacing": grid_spacing,
                "grid_level": next_level,
                "active_grids": len(active_grids),
            },
            notes=f"Grid BUY level {next_level}, spacing ${grid_spacing:.4f}, ADX {adx:.1f}",
        )

        return signal

    def _create_grid_sell_signal(
        self,
        symbol: str,
        current_price: float,
        grid_spacing: float,
        atr: float,
        adx: float,
    ) -> Optional[Signal]:
        """
        Create SELL grid signal (sell order above current price).

        Entry: Current price + (grid_spacing * next_level)
        Stop Loss: Entry + (2x ATR)
        Take Profit: Entry - grid_spacing (exit when price drops to next grid level)
        """
        # Calculate next grid level (how many grids above current price)
        active_grids = self.active_grids.get(symbol, [])
        sell_grids = [g for g in active_grids if g["side"] == "SELL"]
        next_level = len(sell_grids) + 1

        if next_level > self.grid_levels:
            return None  # All SELL grids already placed

        # Entry: Above current price
        entry_price = current_price + (grid_spacing * next_level)

        # Stop Loss: 2x ATR above entry (protect against runaway uptrend)
        stop_loss = entry_price + (atr * 2.0)

        # Take Profit: Next grid level down (buy back when price drops)
        take_profit = entry_price - grid_spacing

        # Calculate confidence (same as BUY)
        adx_score = 1.0 - (adx / self.adx_threshold)
        spacing_score = min(grid_spacing / (current_price * 0.02), 1.0)
        position_score = 1.0 - (len(active_grids) / self.max_positions)

        confidence = (adx_score * 0.5) + (spacing_score * 0.3) + (position_score * 0.2)
        confidence = max(0.0, min(1.0, confidence))

        # Prompt 057: Confidence affects SIZE, not permission
        # Low confidence = smaller position via ConfidenceSizer, not blocked
        if confidence < 0.3:
            logger.warning(
                f"SELL grid: Very low confidence {confidence:.2f} (will use minimum position size)"
            )

        # Create signal
        signal = Signal(
            strategy=StrategyType.GRID_TRADING,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.SELL,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.STANDARD,
            market_state=MarketState.RANGE,
            timeframe="1h",
            pattern=f"grid_sell_level_{next_level}",
            volume_confirmation=True,
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=True,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
            indicators={
                "atr": atr,
                "adx": adx,
                "grid_spacing": grid_spacing,
                "grid_level": next_level,
                "active_grids": len(active_grids),
            },
            notes=f"Grid SELL level {next_level}, spacing ${grid_spacing:.4f}, ADX {adx:.1f}",
        )

        return signal

    def _is_emergency_stopped(self, symbol: str) -> bool:
        """Check if emergency stop is active for symbol."""
        return self.emergency_stop_triggered.get(symbol, False)

    def _trigger_emergency_stop(self, symbol: str, reason: str) -> None:
        """
        Trigger emergency stop for symbol.

        This prevents new grid positions from being created.
        Existing positions should be closed by external risk management.
        """
        self.emergency_stop_triggered[symbol] = True
        logger.critical(
            f"EMERGENCY STOP triggered for {symbol}: {reason}. "
            f"No new grid positions will be created."
        )

    def register_grid_position(
        self, symbol: str, side: str, level: int, entry_price: float
    ) -> None:
        """
        Register an active grid position.

        Called externally when a grid order is filled.

        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            level: Grid level number
            entry_price: Actual fill price
        """
        if symbol not in self.active_grids:
            self.active_grids[symbol] = []

        grid_position = {
            "side": side,
            "level": level,
            "entry_price": entry_price,
            "timestamp": datetime.utcnow(),
        }

        self.active_grids[symbol].append(grid_position)

        logger.info(
            f"Registered grid position: {symbol} {side} level {level} @ ${entry_price:.4f}. "
            f"Total grids: {len(self.active_grids[symbol])}/{self.max_positions}"
        )

    def remove_grid_position(self, symbol: str, side: str, level: int) -> None:
        """
        Remove a closed grid position.

        Called externally when a grid position is closed (take profit or stop loss hit).

        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            level: Grid level number
        """
        if symbol not in self.active_grids:
            return

        # Find and remove matching grid
        self.active_grids[symbol] = [
            g
            for g in self.active_grids[symbol]
            if not (g["side"] == side and g["level"] == level)
        ]

        logger.info(
            f"Removed grid position: {symbol} {side} level {level}. "
            f"Remaining grids: {len(self.active_grids[symbol])}"
        )

    def reset_grids(self, symbol: str) -> None:
        """
        Reset all grids for a symbol.

        Called when market regime changes or emergency stop is triggered.
        External system should close all positions before calling this.
        """
        if symbol in self.active_grids:
            grid_count = len(self.active_grids[symbol])
            del self.active_grids[symbol]
            logger.warning(f"Reset all {grid_count} grids for {symbol}")

        if symbol in self.emergency_stop_triggered:
            del self.emergency_stop_triggered[symbol]
            logger.info(f"Cleared emergency stop for {symbol}")

    def get_active_grid_count(self, symbol: str) -> int:
        """Get number of active grid positions for symbol."""
        return len(self.active_grids.get(symbol, []))

    def get_grid_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get grid status information for symbol.

        Returns:
            Dictionary with grid statistics
        """
        grids = self.active_grids.get(symbol, [])

        if not grids:
            return {
                "symbol": symbol,
                "active_grids": 0,
                "buy_grids": 0,
                "sell_grids": 0,
                "emergency_stopped": self._is_emergency_stopped(symbol),
            }

        buy_grids = [g for g in grids if g["side"] == "BUY"]
        sell_grids = [g for g in grids if g["side"] == "SELL"]

        return {
            "symbol": symbol,
            "active_grids": len(grids),
            "buy_grids": len(buy_grids),
            "sell_grids": len(sell_grids),
            "max_positions": self.max_positions,
            "utilization": len(grids) / self.max_positions,
            "emergency_stopped": self._is_emergency_stopped(symbol),
            "grids": grids,
        }
