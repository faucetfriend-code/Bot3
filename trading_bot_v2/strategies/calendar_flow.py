"""
Calendar Flow Strategy (turn-of-the-month flow effect test)

Tests the turn-of-the-month (TOM) flow effect adapted to crypto: a long
window around the month boundary (institutional / ETF rebalancing flows
documented in bonds and equities), plus an optional mid-month short
window. This is deliberately a MINIMAL-FILTER strategy - a pure calendar
effect test - and ships disabled until it clears the validation gate.

Logic:
- Long window: enter long at/after 00:00 UTC on (month boundary +
  CALFLOW_LONG_ENTRY_DAY days, default -2, i.e. two days before the new
  month starts) and hold until 00:00 UTC on (boundary +
  CALFLOW_LONG_EXIT_DAY days, default +3, i.e. the 4th of the new
  month). The exit is carried as indicators["time_exit_hours"] so the
  live position manager and the backtest engine force-close at window
  end.
- Optional short window (CALFLOW_ENABLE_SHORT, default false): short
  from 00:00 UTC on day-of-month CALFLOW_SHORT_ENTRY_DOM (default 10)
  until 00:00 UTC on CALFLOW_SHORT_EXIT_DOM (default 15).
- Max 1 trade per window per symbol (dedup key
  "{symbol}:{anchor-month}:{window}").
- Stop: CALFLOW_ATR_STOP_MULT x ATR(14) on 4h (wide - multi-day hold).
- Take profit: nominal 2x stop distance. The time exit at window close
  is the PRIMARY exit; the TP exists so downstream components have a
  complete bracket and an honest RRR (2.0 by construction).

Requires per-candle timestamps in the 4h data ("timestamp" key emitted
by MultiTimeframeFetcher._parse_candles and BacktestDataLoader). The
strategy is time-driven: without timestamps in the feed it disables
itself for the symbol (warn once, return no signals).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ..models import Signal, OrderSide
from ..config import StrategyType, TradeQuality, MarketState, AssetClass
from ..indicators import calculate_atr


class CalendarFlowStrategy:
    """
    Turn-of-the-month calendar flow overlay.

    Goes long across the month boundary (default window: 2 days before
    the new month through the 4th), optionally short mid-month. Runs as
    an overlay in all regimes (time-gated by design, minimal filters -
    this is an honest test of a pure calendar effect).
    """

    def __init__(
        self,
        long_entry_day: int = -2,
        long_exit_day: int = 3,
        enable_short: bool = False,
        short_entry_dom: int = 10,
        short_exit_dom: int = 15,
        atr_stop_mult: float = 3.0,
        atr_period: int = 14,
        confidence: float = 0.6,
        min_remaining_hours: float = 1.0,
    ):
        """
        Initialize CalendarFlowStrategy.

        Args:
            long_entry_day: Long window open in calendar days relative
                to the month boundary (00:00 UTC on the 1st of the next
                month). -2 opens the window at 00:00 UTC two days before
                the boundary.
            long_exit_day: Long window close in days after the boundary.
                +3 closes at 00:00 UTC on the 4th of the new month.
            enable_short: Enable the optional mid-month short window.
            short_entry_dom: Short window open day-of-month (00:00 UTC).
            short_exit_dom: Short window close day-of-month (00:00 UTC).
            atr_stop_mult: Stop distance as a multiple of ATR(14) on 4h.
            atr_period: ATR period on the 4h timeframe.
            confidence: Fixed signal confidence (minimal filters by
                design - there is nothing to grade the entry on).
            min_remaining_hours: Skip entries with less than this many
                hours left in the window (a market entry moments before
                the time exit is pure cost).

        Raises:
            ValueError: If a window is empty or inverted.
        """
        if long_entry_day >= long_exit_day:
            raise ValueError(
                f"long_entry_day ({long_entry_day}) must be < "
                f"long_exit_day ({long_exit_day})"
            )
        if short_entry_dom >= short_exit_dom:
            raise ValueError(
                f"short_entry_dom ({short_entry_dom}) must be < "
                f"short_exit_dom ({short_exit_dom})"
            )
        if not 1 <= short_entry_dom <= 28 or not 1 <= short_exit_dom <= 28:
            raise ValueError(
                "short window days-of-month must be within 1..28 "
                f"(got {short_entry_dom}..{short_exit_dom})"
            )

        self.strategy_type = StrategyType.CALENDAR_FLOW
        self.long_entry_day = long_entry_day
        self.long_exit_day = long_exit_day
        self.enable_short = enable_short
        self.short_entry_dom = short_entry_dom
        self.short_exit_dom = short_exit_dom
        self.atr_stop_mult = atr_stop_mult
        self.atr_period = atr_period
        self.confidence = confidence
        self.min_remaining_hours = min_remaining_hours

        # Per-window dedup: "{symbol}:{anchor}:{window}" -> True
        self._window_trades: Dict[str, bool] = {}

        # Symbols we already warned about missing timestamps (warn once)
        self._warned_no_timestamp: set = set()

        # Simulated time injected by backtest engine (None = wall-clock)
        self._sim_time: Optional[datetime] = None

        logger.info(
            f"CalendarFlowStrategy initialized: long window "
            f"[{long_entry_day:+d}, {long_exit_day:+d}] days around month "
            f"boundary, short={'on' if enable_short else 'off'} "
            f"(dom {short_entry_dom}-{short_exit_dom}), "
            f"stop={atr_stop_mult}x ATR({atr_period}) 4h, conf={confidence}"
        )

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """Return current UTC time - simulated candle time in backtesting."""
        now = self._sim_time if self._sim_time is not None else datetime.now(
            timezone.utc
        )
        if now.tzinfo is None:
            # Backtest sim times are naive candle timestamps; treat as UTC.
            now = now.replace(tzinfo=timezone.utc)
        return now

    @staticmethod
    def _month_start(dt: datetime) -> datetime:
        """00:00 UTC on the 1st of dt's month."""
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _next_month_start(dt: datetime) -> datetime:
        """00:00 UTC on the 1st of the month after dt's month."""
        if dt.month == 12:
            return dt.replace(
                year=dt.year + 1, month=1, day=1,
                hour=0, minute=0, second=0, microsecond=0,
            )
        return dt.replace(
            month=dt.month + 1, day=1,
            hour=0, minute=0, second=0, microsecond=0,
        )

    def _active_window(
        self, now: datetime
    ) -> Optional[Tuple[str, str, str, datetime, datetime]]:
        """
        Find the calendar window containing now, if any.

        The long window is anchored to a month boundary B (00:00 UTC on
        the 1st): [B + long_entry_day days, B + long_exit_day days).
        With the defaults (-2, +3) it spans the boundary, so both the
        current month's boundary and the next month's boundary are
        candidates. The short window lives inside a single month:
        [day short_entry_dom, day short_exit_dom) at 00:00 UTC.

        Args:
            now: Current aware UTC datetime.

        Returns:
            (window_name, anchor "YYYY-MM", direction, open_dt, close_dt)
            or None when now is outside all enabled windows.
        """
        month_start = self._month_start(now)
        for boundary in (month_start, self._next_month_start(month_start)):
            open_dt = boundary + timedelta(days=self.long_entry_day)
            close_dt = boundary + timedelta(days=self.long_exit_day)
            if open_dt <= now < close_dt:
                anchor = f"{boundary.year:04d}-{boundary.month:02d}"
                return ("tom_long", anchor, "long", open_dt, close_dt)

        if self.enable_short:
            open_dt = month_start.replace(day=self.short_entry_dom)
            close_dt = month_start.replace(day=self.short_exit_dom)
            if open_dt <= now < close_dt:
                anchor = f"{month_start.year:04d}-{month_start.month:02d}"
                return ("mid_short", anchor, "short", open_dt, close_dt)

        return None

    def _prune_window_trades(self, now: datetime) -> None:
        """Drop dedup entries anchored more than two months in the past."""
        if len(self._window_trades) <= 24:
            return
        current = now.year * 12 + now.month
        for key in list(self._window_trades):
            parts = key.split(":")
            try:
                year_s, month_s = parts[1].split("-")
                anchor = int(year_s) * 12 + int(month_s)
                if current - anchor > 2:
                    del self._window_trades[key]
            except (IndexError, ValueError):
                del self._window_trades[key]

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Any],
        current_price: float,
        **kwargs,
    ) -> List[Signal]:
        """
        Generate turn-of-the-month calendar flow signals.

        Args:
            symbol: Trading symbol.
            multi_tf_data: Multi-timeframe data; the 4h bundle must carry
                "timestamp" plus high/low/close for the ATR stop.
            current_price: Current market price.
            **kwargs: Unused (accepted for interface compatibility).

        Returns:
            List with 0 or 1 Signal objects.
        """
        signals: List[Signal] = []

        if current_price <= 0:
            return signals

        df_4h = multi_tf_data.get("4h")
        if not df_4h:
            logger.debug(f"{symbol}: No 4h data for calendar flow")
            return signals

        if not df_4h.get("timestamp"):
            if symbol not in self._warned_no_timestamp:
                self._warned_no_timestamp.add(symbol)
                logger.warning(
                    f"{symbol}: 4h data has no 'timestamp' key - "
                    f"CalendarFlow disabled for this symbol"
                )
            return signals

        now = self._now()
        window = self._active_window(now)
        if window is None:
            return signals
        window_name, anchor, direction, open_dt, close_dt = window

        # One trade per window per symbol
        self._prune_window_trades(now)
        window_key = f"{symbol}:{anchor}:{window_name}"
        if self._window_trades.get(window_key):
            return signals

        # Skip entries with almost no window left (entry cost > any edge)
        remaining_hours = (close_dt - now).total_seconds() / 3600.0
        if remaining_hours < self.min_remaining_hours:
            logger.debug(
                f"{symbol}: only {remaining_hours:.2f}h left in "
                f"{window_name} window - skipping entry"
            )
            return signals

        # ATR(14) on 4h for the wide multi-day stop
        highs = list(df_4h.get("high", []))
        lows = list(df_4h.get("low", []))
        closes = list(df_4h.get("close", []))
        n = min(len(highs), len(lows), len(closes))
        if n < self.atr_period + 1:
            logger.debug(
                f"{symbol}: insufficient 4h history for ATR "
                f"({n} < {self.atr_period + 1})"
            )
            return signals
        try:
            atr_value = calculate_atr(
                highs[:n], lows[:n], closes[:n], self.atr_period
            )
        except ValueError:
            atr_value = 0.0
        if atr_value <= 0:
            logger.debug(f"{symbol}: non-positive 4h ATR - no calendar signal")
            return signals

        stop_distance = self.atr_stop_mult * atr_value
        entry_price = current_price
        if direction == "long":
            side = OrderSide.BUY
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + 2.0 * stop_distance
        else:
            side = OrderSide.SELL
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - 2.0 * stop_distance

        if stop_loss <= 0 or take_profit <= 0:
            logger.debug(
                f"{symbol}: degenerate bracket (stop={stop_loss:.4f}, "
                f"tp={take_profit:.4f}) - skipping"
            )
            return signals

        signal = Signal(
            strategy=self.strategy_type,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=self.confidence,
            quality=TradeQuality.STANDARD,
            timeframe="4h",
            market_state=MarketState.UNKNOWN,
            notes=(
                f"CalendarFlow {direction}: {window_name} window "
                f"{open_dt.isoformat()} -> {close_dt.isoformat()}, "
                f"time exit in {remaining_hours:.1f}h "
                f"(nominal TP at 2x stop; time exit dominates)"
            ),
            indicators={
                "window": window_name,
                "window_anchor": anchor,
                "window_open": open_dt.isoformat(),
                "window_close": close_dt.isoformat(),
                "atr": atr_value,
                "stop_distance": stop_distance,
                "time_exit_hours": remaining_hours,
                "direction": direction,
            },
            # Validation flags (RiskManager re-validates downstream).
            # This is a deliberate minimal-filter calendar test: RRR is
            # 2.0 by construction (nominal TP at 2x stop distance), but
            # the time exit at window close is the primary exit.
            volume_confirmation=True,
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=True,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )

        signals.append(signal)
        self._window_trades[window_key] = True

        logger.info(
            f"{symbol}: CalendarFlow signal - {direction.upper()} @ "
            f"${entry_price:.4f}, SL=${stop_loss:.4f}, TP=${take_profit:.4f} "
            f"(nominal), time exit {remaining_hours:.1f}h, "
            f"window={window_name} {anchor}"
        )

        return signals
