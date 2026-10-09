"""
Regime-Flip Position Review
===========================

Reviews open non-grid positions when a symbol's confirmed market regime
changes (EventType.REGIME_CHANGED). For each open position whose owning
strategy is NOT active in the new regime (overlay strategies count as
always-active):

- In profit  -> tighten/arm the trailing stop via the existing
  MigratedPositionManager trailing mechanism (do not close).
- Against the 4h SMA 50/200 trend -> close at market with reason
  "regime_exit", using the same close path as existing trailing/trend
  exits.
- Otherwise -> log only.

The handler runs inline on the synchronous EventBus (the regime detection
path), so it is kept exception-safe: any per-position failure is caught
and logged without breaking detection, and the whole handler never raises.

Enabled via ENABLE_REGIME_POSITION_REVIEW (env, default true); wired in
trading_bot.py next to the telegram REGIME_CHANGED subscription.
"""

import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger

from .market_regime import MarketRegime

if TYPE_CHECKING:
    from .database import DatabaseManager
    from .event_system import Event
    from .market_regime import MarketRegimeDetector
    from .migrated_position_manager import MigratedPositionManager
    from .multi_timeframe_fetcher import MultiTimeframeFetcher


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment (true/1/yes/on, false/0/no/off)."""
    value = os.getenv(name, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


def _normalize_strategy(name: Any) -> str:
    """Normalize a strategy identifier to a canonical lookup key."""
    if name is None:
        return ""
    raw = getattr(name, "value", name)
    return str(raw).lower().replace("_", "").replace(" ", "")


class RegimePositionReviewer:
    """
    Reviews open non-grid positions on confirmed regime flips.

    Args:
        db: DatabaseManager (needs get_open_trades).
        client: Exchange client (PacificaClient) for current prices.
        regime_detector: MarketRegimeDetector (get_active_strategies,
            get_trend_direction).
        migrated_position_manager: MigratedPositionManager whose trailing
            stop and close mechanisms are reused.
        multi_tf_fetcher: Optional MultiTimeframeFetcher for 4h trend data.
        enabled: Override for ENABLE_REGIME_POSITION_REVIEW (default
            env/true).
    """

    # Overlay / time-gated strategies run in ALL regimes and are never
    # reviewed out by a regime flip.
    OVERLAY_STRATEGIES = {
        "liquidationcapture",
        "fundingarb",
        "fundingarbitrage",
        "orderbookimbalance",
        "sessionrangebreakout",
    }

    # Grid positions are handled by GridLifecycleManager.on_regime_disallowed
    GRID_STRATEGIES = {"gridtrading"}

    def __init__(
        self,
        db: "DatabaseManager",
        client: Any,
        regime_detector: "MarketRegimeDetector",
        migrated_position_manager: "MigratedPositionManager",
        multi_tf_fetcher: Optional["MultiTimeframeFetcher"] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.db = db
        self.client = client
        self.regime_detector = regime_detector
        self.migrated_position_manager = migrated_position_manager
        self.multi_tf_fetcher = multi_tf_fetcher
        self.enabled = (
            enabled
            if enabled is not None
            else _env_bool("ENABLE_REGIME_POSITION_REVIEW", True)
        )

        logger.info(f"RegimePositionReviewer initialized (enabled={self.enabled})")

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    def handle_regime_changed(self, event: "Event") -> None:
        """
        Handle a REGIME_CHANGED event (exception-safe, runs inline).

        Args:
            event: Event with data containing symbol, old_regime,
                new_regime (regime value strings).
        """
        if not self.enabled:
            return

        try:
            data = event.data if hasattr(event, "data") else {}
            symbol = data.get("symbol")
            new_regime_value = data.get("new_regime")
            if not symbol or not new_regime_value:
                logger.warning(
                    "Regime review: event missing symbol/new_regime - skipping"
                )
                return
            new_regime = MarketRegime(str(new_regime_value).lower())
        except Exception as e:
            logger.error(f"Regime review: could not parse event: {e}")
            return

        try:
            self._review_symbol(symbol, new_regime)
        except Exception as e:
            logger.error(f"Regime review failed for {symbol}: {e}")

    # ------------------------------------------------------------------
    # Review logic
    # ------------------------------------------------------------------

    def _review_symbol(self, symbol: str, new_regime: MarketRegime) -> None:
        """Review all open non-grid positions on a symbol after a flip."""
        positions = self._get_open_positions(symbol)
        if not positions:
            logger.debug(f"Regime review: no open positions for {symbol}")
            return

        active = {
            _normalize_strategy(name)
            for name in self.regime_detector.get_active_strategies(new_regime)
        }

        market_data = self._get_market_data(symbol)
        trend = "none"
        if market_data:
            try:
                trend = self.regime_detector.get_trend_direction(market_data)
            except Exception as e:
                logger.warning(
                    f"Regime review: trend detection failed for {symbol}: {e}"
                )

        current_price = self._get_current_price(symbol)

        logger.info(
            f"Regime review for {symbol} -> {new_regime.value}: "
            f"{len(positions)} open position(s), trend={trend}, "
            f"active_strategies={sorted(active)}"
        )

        for pos in positions:
            try:
                self._review_position(
                    symbol, pos, active, trend, current_price, market_data
                )
            except Exception as e:
                logger.error(
                    f"Regime review: error reviewing {symbol} position "
                    f"{pos.get('id')}: {e}"
                )

    def _review_position(
        self,
        symbol: str,
        pos: Dict[str, Any],
        active_strategies: set[str],
        trend: str,
        current_price: Optional[float],
        market_data: Optional[Dict[str, List[float]]],
    ) -> None:
        """Review a single open position after a regime flip."""
        strategy_key = _normalize_strategy(pos.get("strategy"))

        if not strategy_key:
            logger.info(
                f"Regime review: {symbol} position {pos.get('id')} has no "
                f"strategy tag - log only"
            )
            return

        if strategy_key in self.GRID_STRATEGIES:
            return  # Grid transitions handled by GridLifecycleManager

        if strategy_key in self.OVERLAY_STRATEGIES:
            return  # Overlays are always-active

        if strategy_key in active_strategies:
            return  # Owning strategy still active in the new regime

        side = self._normalize_side(pos.get("side"))
        if side is None:
            logger.info(
                f"Regime review: {symbol} position {pos.get('id')} has "
                f"unrecognized side {pos.get('side')!r} - log only"
            )
            return

        try:
            qty = float(pos.get("quantity") or 0.0)
            entry_price = float(pos.get("entry_price") or 0.0)
        except (TypeError, ValueError):
            qty, entry_price = 0.0, 0.0
        if qty <= 0 or entry_price <= 0:
            logger.info(
                f"Regime review: {symbol} position {pos.get('id')} has "
                f"invalid qty/entry - log only"
            )
            return

        mp_pos = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry_price": entry_price,
            "strategy": strategy_key,
        }

        in_profit = False
        if current_price is not None and current_price > 0:
            if side == "long":
                in_profit = current_price > entry_price
            else:
                in_profit = current_price < entry_price

        against_trend = (side == "long" and trend == "down") or (
            side == "short" and trend == "up"
        )

        if in_profit and current_price is not None:
            # Profitable but misaligned: arm/tighten trailing stop, keep open
            self._ensure_registered(symbol, mp_pos, trend)
            self.migrated_position_manager.arm_trailing_stop(
                symbol, mp_pos, current_price, market_data
            )
            logger.info(
                f"Regime review: {symbol} {side} ({strategy_key}) misaligned "
                f"but in profit - trailing stop armed, position kept"
            )
        elif against_trend:
            # Against the 4h SMA trend: close at market
            logger.warning(
                f"Regime review: {symbol} {side} ({strategy_key}) misaligned "
                f"and against {trend} trend - closing (regime_exit)"
            )
            self.migrated_position_manager.close_position(symbol, mp_pos, "regime_exit")
        else:
            logger.info(
                f"Regime review: {symbol} {side} ({strategy_key}) misaligned "
                f"but not in profit and not against trend ({trend}) - log only"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_registered(
        self, symbol: str, mp_pos: Dict[str, Any], trend: str
    ) -> None:
        """Register the position with RiskManager migrated tracking if new."""
        try:
            risk_manager = self.migrated_position_manager.risk_manager
            existing = risk_manager.get_migrated_positions(symbol)
            for tracked in existing:
                if tracked.get("side") == mp_pos["side"]:
                    return  # Already tracked - trailing mechanism will manage
            risk_manager.register_migrated_position(
                symbol,
                {
                    "side": mp_pos["side"],
                    "qty": mp_pos["qty"],
                    "entry_price": mp_pos["entry_price"],
                    "has_stop": False,  # Stop armed right after registration
                    "trend_direction": trend if trend != "none" else None,
                },
            )
        except Exception as e:
            logger.warning(
                f"Regime review: could not register {symbol} position for "
                f"trailing management: {e}"
            )

    def _get_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch open non-grid trades for the symbol from the database."""
        try:
            trades = self.db.get_open_trades(symbol=symbol)
        except Exception as e:
            logger.error(f"Regime review: could not read open trades: {e}")
            return []
        # Exclude grid rows (side 'GRID' marker or grid strategy tag)
        return [
            t
            for t in trades
            if str(t.get("side", "")).upper() != "GRID"
            and _normalize_strategy(t.get("strategy")) not in self.GRID_STRATEGIES
        ]

    def _get_market_data(self, symbol: str) -> Optional[Dict[str, List[float]]]:
        """Fetch 4h market data for trend detection (None on failure)."""
        if not self.multi_tf_fetcher:
            return None
        try:
            data = self.multi_tf_fetcher.get_candles_multi_tf(
                symbol=symbol, timeframes=["4h"], lookback_candles=200
            )
            return data.get("4h")
        except Exception as e:
            logger.warning(f"Regime review: could not fetch 4h data for {symbol}: {e}")
            return None

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Fetch the current price for a symbol (None on failure)."""
        try:
            ticker = self.client.get_ticker(symbol)
            if ticker:
                price = float(
                    ticker.get("last")
                    or ticker.get("price")
                    or ticker.get("mark_price", 0)
                )
                return price if price > 0 else None
        except Exception as e:
            logger.warning(f"Regime review: could not get price for {symbol}: {e}")
        return None

    @staticmethod
    def _normalize_side(raw_side: Any) -> Optional[str]:
        """Normalize a trade/position side to 'long' or 'short'."""
        side = str(raw_side or "").lower()
        if side in ("long", "buy", "bid"):
            return "long"
        if side in ("short", "sell", "ask"):
            return "short"
        return None
