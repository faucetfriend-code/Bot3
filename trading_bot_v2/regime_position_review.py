"""
Regime-Flip Position Review
===========================

Reviews open non-grid positions when a symbol's confirmed market regime
changes (EventType.REGIME_CHANGED). Open positions are read from the
``positions`` table together with the ``strategy`` tag stamped at entry.
For each position whose owning strategy is NOT active in the new regime
(overlay strategies count as always-active):

- In profit  -> tighten/arm the trailing stop via the existing
  MigratedPositionManager trailing mechanism (do not close).
- Not in profit and against the 4h SMA 50/200 trend -> close at market
  with reason "regime_exit", using the same close path as existing
  trailing/trend exits.
- Otherwise -> log only.

Never reviewed (left to their stops):

- positions with no owner to judge them by: tag ``unknown``, ``migrated``,
  missing, or not a real strategy;
- grid positions and every position on a symbol a grid is tracked on;
- overlay strategies and strategies still active in the new regime.

No action is taken on missing data: an unavailable price, an unreadable
venue or an unresolvable active-strategy set leaves the position alone.
The review runs only on a REGIME_CHANGED event that names two different
regimes; nothing is reviewed at startup.

The handler runs inline on the synchronous EventBus (the regime detection
path), so it is kept exception-safe: any per-position failure is caught
and logged without breaking detection, and the whole handler never raises.

Enabled via ENABLE_REGIME_POSITION_REVIEW (env, default true); wired in
trading_bot.py next to the telegram REGIME_CHANGED subscription.
"""

import os
import threading
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
)

from loguru import logger

from .exit_sizing import remaining_exchange_quantity
from .history.closed_trades import canonical_strategy_tag, is_strategy_tag
from .market_regime import MarketRegime
from .price_lookup import PriceLookup, fetch_current_price

if TYPE_CHECKING:
    from .database import DatabaseManager
    from .event_system import Event
    from .market_regime import MarketRegimeDetector
    from .migrated_position_manager import MigratedPositionManager
    from .multi_timeframe_fetcher import MultiTimeframeFetcher

# (regime, symbol) -> names of the strategies active in that regime.
ActiveStrategyResolver = Callable[[MarketRegime, str], Iterable[Any]]
# symbol -> True when a grid is tracked on the symbol.
GridSymbolChecker = Callable[[str], bool]


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment (true/1/yes/on, false/0/no/off)."""
    value = os.getenv(name, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


class RegimePositionReviewer:
    """
    Reviews open non-grid positions on confirmed regime flips.

    Args:
        db: DatabaseManager (needs get_position).
        client: Exchange client (PacificaClient) for current prices and
            the venue's open quantity.
        regime_detector: MarketRegimeDetector (get_trend_direction, and
            get_active_strategies when no resolver is injected).
        migrated_position_manager: MigratedPositionManager whose trailing
            stop and close mechanisms are reused.
        multi_tf_fetcher: Optional MultiTimeframeFetcher for 4h trend data.
        enabled: Override for ENABLE_REGIME_POSITION_REVIEW (default
            env/true).
        price_lookup: Optional ticker source (symbol -> ticker dict).
            TradingBot injects its WebSocket-with-REST-fallback lookup;
            without it the client itself is asked (see price_lookup.py).
        active_strategy_resolver: Optional (regime, symbol) -> strategy
            names. TradingBot injects
            StrategyManager.get_active_strategy_names, the same function
            signal generation uses; without it the detector's regime map
            is used.
        grid_symbol_checker: Optional symbol -> bool; True means a grid is
            tracked on the symbol and nothing on it is reviewed.
    """

    # Overlay / time-gated strategies run in ALL regimes and are never
    # reviewed out by a regime flip.
    OVERLAY_STRATEGIES = {
        "liquidationcapture",
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
        price_lookup: Optional[PriceLookup] = None,
        active_strategy_resolver: Optional[ActiveStrategyResolver] = None,
        grid_symbol_checker: Optional[GridSymbolChecker] = None,
    ) -> None:
        self.db = db
        self.client = client
        self._price_lookup = price_lookup
        self.regime_detector = regime_detector
        self.migrated_position_manager = migrated_position_manager
        self.multi_tf_fetcher = multi_tf_fetcher
        self._active_strategy_resolver = active_strategy_resolver
        self._grid_symbol_checker = grid_symbol_checker
        self.enabled = (
            enabled
            if enabled is not None
            else _env_bool("ENABLE_REGIME_POSITION_REVIEW", True)
        )
        self._overlay_tags = {
            canonical_strategy_tag(name) for name in self.OVERLAY_STRATEGIES
        }
        self._grid_tags = {
            canonical_strategy_tag(name) for name in self.GRID_STRATEGIES
        }
        # Used only when the manager exposes no operation_lock.
        self._review_lock = threading.RLock()
        # Position episodes already reported as "no owner" (log once).
        self._exempt_logged: Set[Tuple[str, str, str]] = set()

        logger.info(f"RegimePositionReviewer initialized (enabled={self.enabled})")

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    def handle_regime_changed(self, event: "Event") -> None:
        """
        Handle a REGIME_CHANGED event (exception-safe, runs inline).

        Only an event naming two different regimes is a change; anything
        else (no old regime, old equal to new) is ignored, so a startup
        state can never be reviewed.

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
            old_regime_value = data.get("old_regime")
            if not symbol or not new_regime_value:
                logger.warning(
                    "Regime review: event missing symbol/new_regime - skipping"
                )
                return
            new_regime = MarketRegime(str(new_regime_value).lower())
            old_regime = (
                MarketRegime(str(old_regime_value).lower())
                if old_regime_value
                else None
            )
        except Exception as e:
            logger.error(f"Regime review: could not parse event: {e}")
            return

        if old_regime is None or old_regime == new_regime:
            logger.info(
                f"Regime review: {symbol} event is not a regime change "
                f"(old={old_regime_value!r}, new={new_regime.value}) - no review"
            )
            return

        # The event is delivered on the publisher's thread (the trading
        # loop, or an API request that ran regime detection).  Holding the
        # manager's lock keeps the review and manage_positions from
        # deciding on the same position at the same time.
        lock = getattr(self.migrated_position_manager, "operation_lock", None)
        try:
            with lock if lock is not None else self._review_lock:
                self._review_symbol(symbol, old_regime, new_regime)
        except Exception as e:
            logger.error(f"Regime review failed for {symbol}: {e}")

    # ------------------------------------------------------------------
    # Review logic
    # ------------------------------------------------------------------

    def _review_symbol(
        self, symbol: str, old_regime: MarketRegime, new_regime: MarketRegime
    ) -> None:
        """Review all open non-grid positions on a symbol after a flip."""
        if self._grid_owns_symbol(symbol):
            return

        positions = self._get_open_positions(symbol)
        if not positions:
            logger.debug(f"Regime review: no open positions for {symbol}")
            return

        active = self._active_tags(new_regime, symbol)
        if active is None:
            return

        candidates = [
            pos for pos in positions if self._is_reviewable(symbol, pos, active)
        ]
        if not candidates:
            return

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
            f"Regime review for {symbol} {old_regime.value} -> "
            f"{new_regime.value}: {len(candidates)} position(s) to review, "
            f"trend={trend}, active_strategies={sorted(active)}"
        )

        regimes = f"old_regime={old_regime.value} new_regime={new_regime.value}"
        for pos in candidates:
            try:
                self._review_position(
                    symbol, pos, regimes, trend, current_price, market_data
                )
            except Exception as e:
                logger.error(
                    f"Regime review: error reviewing {symbol} position "
                    f"{pos.get('id')}: {e}"
                )

    def _is_reviewable(
        self, symbol: str, pos: Dict[str, Any], active_tags: Set[str]
    ) -> bool:
        """True when the position's owner is known and no longer active."""
        tag = canonical_strategy_tag(pos.get("strategy"))
        if not is_strategy_tag(tag):
            self._log_exempt_once(symbol, pos, tag)
            return False
        if tag in self._grid_tags:
            return False  # Grid transitions handled by GridLifecycleManager
        if tag in self._overlay_tags:
            return False  # Overlays are always-active
        if tag in active_tags:
            return False  # Owning strategy still active in the new regime
        return True

    def _review_position(
        self,
        symbol: str,
        pos: Dict[str, Any],
        regimes: str,
        trend: str,
        current_price: Optional[float],
        market_data: Optional[Dict[str, List[float]]],
    ) -> None:
        """Review a single open position whose owner is no longer active."""
        strategy = canonical_strategy_tag(pos.get("strategy"))
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

        facts = f"symbol={symbol} side={side} strategy={strategy} {regimes}"
        blocked = self._missing_data_reason(symbol, side, current_price)
        if blocked is not None or current_price is None:
            logger.warning(
                f"Regime review decision: {facts} action=none reason={blocked}"
            )
            return

        if side == "long":
            in_profit = current_price > entry_price
        else:
            in_profit = current_price < entry_price
        against_trend = (side == "long" and trend == "down") or (
            side == "short" and trend == "up"
        )
        facts = (
            f"{facts} in_profit={in_profit} trend={trend} "
            f"against_trend={against_trend} entry={entry_price} "
            f"price={current_price}"
        )

        mp_pos = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry_price": entry_price,
            "strategy": strategy,
        }

        if in_profit:
            # Profitable but misaligned: arm/tighten trailing stop, keep open
            self._arm_trailing(
                symbol, mp_pos, pos, trend, current_price, market_data, facts
            )
        elif against_trend:
            # Not in profit and against the 4h SMA trend: close at market
            logger.warning(
                f"Regime review decision: {facts} action=close reason=regime_exit"
            )
            self.migrated_position_manager.close_position(symbol, mp_pos, "regime_exit")
        else:
            logger.info(
                f"Regime review decision: {facts} action=none "
                f"reason=not_in_profit_not_against_trend"
            )

    def _missing_data_reason(
        self, symbol: str, side: str, current_price: Optional[float]
    ) -> Optional[str]:
        """Why no decision can be made on this position now (None = can)."""
        if current_price is None or current_price <= 0:
            return "price_unavailable"
        venue_quantity = remaining_exchange_quantity(self.client, symbol, side)
        if venue_quantity is None:
            return "venue_unreadable"
        if venue_quantity <= 0:
            return "venue_flat_left_to_position_sync"
        pending = getattr(self.migrated_position_manager, "get_pending_closes", None)
        closes = pending() if callable(pending) else None
        if isinstance(closes, dict) and (symbol, side) in closes:
            return "close_already_pending"
        return None

    def _arm_trailing(
        self,
        symbol: str,
        mp_pos: Dict[str, Any],
        row: Dict[str, Any],
        trend: str,
        current_price: float,
        market_data: Optional[Dict[str, List[float]]],
        facts: str,
    ) -> None:
        """Put an in-profit position under the trailing stop; keep it open.

        The position's stored stop is handed to the manager as a floor, so
        the trailing stop is never less protective than the stop it had.
        """
        manager = self.migrated_position_manager
        side = str(mp_pos["side"])
        newly_registered = self._ensure_registered(symbol, mp_pos, trend)
        if newly_registered is None:
            logger.warning(
                f"Regime review decision: {facts} action=none "
                f"reason=could_not_register_for_trailing"
            )
            return
        try:
            manager.arm_trailing_stop(
                symbol,
                mp_pos,
                current_price,
                market_data,
                floor_stop=row.get("venue_stop_price"),
            )
        except Exception:
            # Never leave a position registered (local stop enforcement
            # skips it) without a trailing stop.
            if newly_registered:
                manager.risk_manager.unregister_migrated_position(symbol, side)
            raise
        stop = manager.get_trailing_stop(symbol, side)
        if stop is None:
            # No stop could be armed: hand a position registered just now
            # back, so its own stop keeps being enforced.
            if newly_registered:
                manager.risk_manager.unregister_migrated_position(symbol, side)
            logger.error(
                f"Regime review decision: {facts} action=none "
                f"reason=trailing_stop_not_armed"
            )
            return
        logger.info(
            f"Regime review decision: {facts} action=arm_trailing_stop "
            f"reason=inactive_strategy_in_profit trailing_stop={stop} "
            f"position_kept=True"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_registered(
        self, symbol: str, mp_pos: Dict[str, Any], trend: str
    ) -> Optional[bool]:
        """Register the position with RiskManager migrated tracking if new.

        The trend is stored only when the position is aligned with it, so
        the manager's trend-reversal exit can fire only on a flip AGAINST
        the position (a long kept against a down trend is not closed when
        the trend turns up).

        Returns:
            True when registered now, False when it was already tracked,
            None when it could not be registered.
        """
        try:
            risk_manager = self.migrated_position_manager.risk_manager
            existing = risk_manager.get_migrated_positions(symbol)
            for tracked in existing:
                if tracked.get("side") == mp_pos["side"]:
                    return False  # Already tracked - trailing mechanism manages
            aligned = (mp_pos["side"] == "long" and trend == "up") or (
                mp_pos["side"] == "short" and trend == "down"
            )
            registered = risk_manager.register_migrated_position(
                symbol,
                {
                    "side": mp_pos["side"],
                    "qty": mp_pos["qty"],
                    "entry_price": mp_pos["entry_price"],
                    "has_stop": False,  # Stop armed right after registration
                    "trend_direction": trend if aligned else None,
                },
            )
            return True if registered else None
        except Exception as e:
            logger.warning(
                f"Regime review: could not register {symbol} position for "
                f"trailing management: {e}"
            )
            return None

    def _grid_owns_symbol(self, symbol: str) -> bool:
        """True when a grid is tracked on the symbol (or that is unknown)."""
        if self._grid_symbol_checker is None:
            return False
        try:
            owned = bool(self._grid_symbol_checker(symbol))
        except Exception as e:
            logger.warning(
                f"Regime review: grid state unreadable for {symbol} ({e}) - no review"
            )
            return True
        if owned:
            logger.info(
                f"Regime review: {symbol} has a tracked grid - positions left "
                f"to the grid lifecycle, no review"
            )
        return owned

    def _active_tags(self, new_regime: MarketRegime, symbol: str) -> Optional[Set[str]]:
        """Canonical tags of the strategies active in the new regime.

        Returns:
            The tag set, or None when it cannot be resolved (no review).
        """
        try:
            if self._active_strategy_resolver is not None:
                names = self._active_strategy_resolver(new_regime, symbol)
            else:
                names = self.regime_detector.get_active_strategies(new_regime)
            return {canonical_strategy_tag(name) for name in names}
        except Exception as e:
            logger.error(
                f"Regime review: active strategies unresolved for {symbol} "
                f"({e}) - no review"
            )
            return None

    def _log_exempt_once(self, symbol: str, pos: Dict[str, Any], tag: str) -> None:
        """Log (once per position episode) that a position has no owner."""
        key = (symbol, str(pos.get("side")), str(pos.get("opened_at")))
        if key in self._exempt_logged:
            return
        self._exempt_logged.add(key)
        logger.info(
            f"Regime review: {symbol} {pos.get('side')} position "
            f"{pos.get('id')} has no owning strategy (tag={tag!r}) - not "
            f"subject to regime exit, left to its stops"
        )

    def _get_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        """Read the open positions rows (with strategy tag) for the symbol."""
        rows: List[Dict[str, Any]] = []
        try:
            for side in ("LONG", "SHORT"):
                for spelling in (side, side.lower()):
                    row = self.db.get_position(symbol, spelling)
                    if isinstance(row, dict):
                        rows.append(row)
                        break
        except Exception as e:
            logger.error(f"Regime review: could not read open positions: {e}")
            return []
        return rows

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
            return fetch_current_price(self.client, symbol, self._price_lookup)
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
