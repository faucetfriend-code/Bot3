"""
Session Range Breakout Strategy (ORB-style)

Opening Range Breakout on the 5m timeframe, anchored to configurable
session opens (UTC daily open 00:00 and US equity open 13:30 UTC).

Logic:
- After a session opens, the first ORB_RANGE_MINUTES of 5m candles define
  the opening range (high/low).
- Once the range is formed, a 5m candle CLOSING above the range high
  (long) or below the range low (short) with volume >= ORB_VOLUME_MULT x
  the 20-bar 5m average triggers an entry, valid for
  ORB_ENTRY_WINDOW_HOURS after the range completes.
- Stop sits on the opposite side of the range (mid-range when the range
  is wider than 2x ATR(14) on 5m). Target is the breakout level +/-
  ORB_TP_RANGE_MULT x range height.
- Max ORB_MAX_TRADES_PER_SESSION entries per symbol per session.
- Positions carry indicators["time_exit_hours"] so position managers /
  the backtest engine can force-close stale breakouts.

Requires per-candle timestamps in the 5m data ("timestamp" key emitted by
MultiTimeframeFetcher._parse_candles and BacktestDataLoader). Timestamps
may be epoch seconds, epoch milliseconds, or ISO-8601 strings; naive
values are treated as UTC.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from ..models import Signal, OrderSide
from ..config import StrategyType, TradeQuality, MarketState, AssetClass
from ..indicators import calculate_atr


class SessionRangeBreakoutStrategy:
    """
    Session-anchored opening range breakout on 5m candles.

    Trades the break of the first ORB_RANGE_MINUTES range after each
    enabled session open, with volume confirmation and a bounded entry
    window. Runs as an overlay in all regimes (time-gated by design).
    """

    def __init__(
        self,
        enable_utc_open: bool = True,
        enable_us_open: bool = True,
        range_minutes: int = 30,
        entry_window_hours: float = 4.0,
        volume_mult: float = 1.5,
        min_range_pct: float = 0.15,
        max_range_pct: float = 3.0,
        tp_range_mult: float = 1.5,
        time_exit_hours: float = 4.0,
        max_trades_per_session: int = 1,
        atr_period: int = 14,
        min_confidence: float = 0.55,
        min_rrr: float = 1.2,
    ):
        """
        Initialize SessionRangeBreakoutStrategy.

        Args:
            enable_utc_open: Trade the 00:00 UTC daily open session.
            enable_us_open: Trade the 13:30 UTC US equity open session.
            range_minutes: Opening range duration in minutes (multiple of 5).
            entry_window_hours: Hours after range completion during which
                breakout entries are allowed.
            volume_mult: Breakout candle volume must be >= this multiple of
                the 20-bar 5m average volume.
            min_range_pct: Skip ranges narrower than this % of price.
            max_range_pct: Skip ranges wider than this % of price.
            tp_range_mult: Take profit at breakout level +/- this multiple
                of the range height.
            time_exit_hours: Advisory max hold time carried on signals via
                indicators["time_exit_hours"].
            max_trades_per_session: Max entries per symbol per session.
            atr_period: ATR period for the wide-range stop fallback.
            min_confidence: Base signal confidence.
            min_rrr: Minimum reward/risk for rrr_meets_minimum. With a
                full-range stop the structural RRR ceiling is tp_range_mult,
                so this defaults below the 1.5 used by trend strategies
                (VWAP scalping uses 0.8 for the same reason).
        """
        self.strategy_type = StrategyType.SESSION_RANGE_BREAKOUT

        # Sessions: (name, hour_utc, minute_utc)
        self.sessions: List[Tuple[str, int, int]] = []
        if enable_utc_open:
            self.sessions.append(("utc_open", 0, 0))
        if enable_us_open:
            self.sessions.append(("us_open", 13, 30))

        self.range_minutes = range_minutes
        self.entry_window_hours = entry_window_hours
        self.volume_mult = volume_mult
        self.min_range_pct = min_range_pct
        self.max_range_pct = max_range_pct
        self.tp_range_mult = tp_range_mult
        self.time_exit_hours = time_exit_hours
        self.max_trades_per_session = max_trades_per_session
        self.atr_period = atr_period
        self.min_confidence = min_confidence
        self.min_rrr = min_rrr

        # Per-session trade counter: "{symbol}:{session_date}:{session_name}" -> count
        self._session_trades: Dict[str, int] = {}

        # Symbols we already warned about missing timestamps (warn once each)
        self._warned_no_timestamp: set[str] = set()

        # Simulated time injected by backtest engine (None = use wall-clock)
        self._sim_time: Optional[datetime] = None

        logger.info(
            f"SessionRangeBreakoutStrategy initialized: "
            f"sessions={[s[0] for s in self.sessions]}, range={range_minutes}min, "
            f"window={entry_window_hours}h, vol_mult={volume_mult}x, "
            f"tp={tp_range_mult}x range, time_exit={time_exit_hours}h"
        )

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """Return current UTC time - simulated candle time in backtesting."""
        now = (
            self._sim_time if self._sim_time is not None else datetime.now(timezone.utc)
        )
        if now.tzinfo is None:
            # Backtest sim times are naive candle timestamps; treat as UTC.
            now = now.replace(tzinfo=timezone.utc)
        return now

    @staticmethod
    def _parse_ts(value: Any) -> Optional[datetime]:
        """
        Parse a candle timestamp into an aware UTC datetime.

        Accepts epoch seconds, epoch milliseconds (int/float or numeric
        string), or ISO-8601 strings. Naive datetimes are treated as UTC.

        Args:
            value: Raw timestamp value from a candle dict.

        Returns:
            Aware UTC datetime, or None if the value cannot be parsed.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                return None
            if seconds > 1e11:  # epoch milliseconds
                seconds /= 1000.0
            try:
                dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        elif isinstance(value, str):
            text = value.strip()
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                try:
                    return SessionRangeBreakoutStrategy._parse_ts(float(text))
                except (TypeError, ValueError):
                    return None
        else:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _most_recent_session(self, now: datetime) -> Optional[Tuple[str, datetime]]:
        """
        Find the most recent enabled session open at or before now.

        Args:
            now: Current aware UTC datetime.

        Returns:
            (session_name, session_open_utc) or None if no sessions enabled.
        """
        best: Optional[Tuple[str, datetime]] = None
        for name, hour, minute in self.sessions:
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                candidate -= timedelta(days=1)
            if best is None or candidate > best[1]:
                best = (name, candidate)
        return best

    def _prune_session_counters(self, now: datetime) -> None:
        """Drop per-session trade counters older than two days."""
        if len(self._session_trades) <= 16:
            return
        cutoff = (now - timedelta(days=2)).date()
        for key in list(self._session_trades):
            parts = key.split(":")
            try:
                key_date = datetime.fromisoformat(parts[1]).date()
                if key_date < cutoff:
                    del self._session_trades[key]
            except (IndexError, ValueError):
                del self._session_trades[key]

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Any],
        current_price: float,
        **kwargs: Any,
    ) -> List[Signal]:
        """
        Generate session range breakout signals.

        Args:
            symbol: Trading symbol.
            multi_tf_data: Regime/structure timeframe data (may include 5m).
            current_price: Current market price.
            **kwargs: Optionally execution_tf_data with a "5m" bundle
                (preferred source for 5m candles).

        Returns:
            List with 0 or 1 Signal objects.
        """
        signals: List[Signal] = []

        if not self.sessions or current_price <= 0:
            return signals

        # Prefer execution timeframe data (live + backtest both supply it)
        execution_tf_data = kwargs.get("execution_tf_data") or {}
        df_5m = execution_tf_data.get("5m") or multi_tf_data.get("5m")
        if not df_5m:
            logger.debug(f"{symbol}: No 5m data for session range breakout")
            return signals

        raw_timestamps = df_5m.get("timestamp")
        if not raw_timestamps:
            if symbol not in self._warned_no_timestamp:
                self._warned_no_timestamp.add(symbol)
                logger.warning(
                    f"{symbol}: 5m data has no 'timestamp' key - "
                    f"SessionRangeBreakout disabled for this symbol"
                )
            return signals

        highs = list(df_5m.get("high", []))
        lows = list(df_5m.get("low", []))
        closes = list(df_5m.get("close", []))
        volumes = list(df_5m.get("volume", []))

        n = min(len(highs), len(lows), len(closes), len(volumes), len(raw_timestamps))
        if n < 2:
            return signals
        highs, lows, closes = highs[:n], lows[:n], closes[:n]
        volumes, raw_timestamps = volumes[:n], raw_timestamps[:n]

        now = self._now()

        # Locate the active session and its windows
        session = self._most_recent_session(now)
        if session is None:
            return signals
        session_name, session_open = session
        range_end = session_open + timedelta(minutes=self.range_minutes)
        window_end = range_end + timedelta(hours=self.entry_window_hours)

        if now < range_end:
            logger.debug(
                f"{symbol}: {session_name} range still forming "
                f"(until {range_end.isoformat()})"
            )
            return signals
        if now > window_end:
            logger.debug(
                f"{symbol}: outside {session_name} entry window "
                f"(ended {window_end.isoformat()})"
            )
            return signals

        # Per-session trade limit
        self._prune_session_counters(now)
        session_key = f"{symbol}:{session_open.date().isoformat()}:{session_name}"
        if self._session_trades.get(session_key, 0) >= self.max_trades_per_session:
            logger.debug(f"{symbol}: session trade limit reached for {session_key}")
            return signals

        # Build the opening range from candles inside [session_open, range_end)
        timestamps = [self._parse_ts(v) for v in raw_timestamps]
        range_idx = [
            i
            for i, ts in enumerate(timestamps)
            if ts is not None and session_open <= ts < range_end
        ]

        expected_candles = self.range_minutes // 5
        if len(range_idx) < expected_candles - 1:
            logger.debug(
                f"{symbol}: only {len(range_idx)}/{expected_candles} range candles "
                f"for {session_name} - skipping"
            )
            return signals
        if len(range_idx) < expected_candles:
            logger.warning(
                f"{symbol}: {session_name} range missing one 5m candle "
                f"({len(range_idx)}/{expected_candles}) - proceeding"
            )

        range_high = max(highs[i] for i in range_idx)
        range_low = min(lows[i] for i in range_idx)
        range_height = range_high - range_low
        if range_height <= 0:
            return signals

        # Range sanity: skip degenerate or blown-out ranges
        range_pct = (range_height / current_price) * 100.0
        if range_pct < self.min_range_pct or range_pct > self.max_range_pct:
            logger.debug(
                f"{symbol}: {session_name} range {range_pct:.2f}% outside "
                f"[{self.min_range_pct}, {self.max_range_pct}]% - skipping"
            )
            return signals

        # Latest CLOSED 5m candle must be after the range window
        last_i = n - 1
        last_ts = timestamps[last_i]
        if last_ts is None or last_ts < range_end:
            return signals

        last_close = closes[last_i]
        last_volume = volumes[last_i]

        if last_close > range_high:
            direction = "long"
        elif last_close < range_low:
            direction = "short"
        else:
            return signals

        # Volume confirmation vs 20-bar 5m average (excluding breakout bar)
        if n < 21:
            logger.debug(
                f"{symbol}: insufficient 5m history for volume baseline ({n} < 21)"
            )
            return signals
        avg_volume = sum(volumes[-21:-1]) / 20.0
        volume_ratio = last_volume / avg_volume if avg_volume > 0 else 0.0
        if volume_ratio < self.volume_mult:
            logger.debug(
                f"{symbol}: {session_name} {direction} breakout without volume "
                f"({volume_ratio:.2f}x < {self.volume_mult}x) - no signal"
            )
            return signals

        # ATR(14) on 5m for the wide-range stop fallback
        atr_value = 0.0
        if n >= self.atr_period + 1:
            try:
                atr_value = calculate_atr(highs, lows, closes, self.atr_period)
            except ValueError:
                atr_value = 0.0

        mid_range = (range_high + range_low) / 2.0
        use_mid_stop = atr_value > 0 and range_height > 2.0 * atr_value

        if direction == "long":
            side = OrderSide.BUY
            stop_loss = mid_range if use_mid_stop else range_low
            take_profit = range_high + self.tp_range_mult * range_height
        else:
            side = OrderSide.SELL
            stop_loss = mid_range if use_mid_stop else range_high
            take_profit = range_low - self.tp_range_mult * range_height

        entry_price = current_price

        # Geometry sanity: entry must sit between stop and target
        if side == OrderSide.BUY and not (stop_loss < entry_price < take_profit):
            return signals
        if side == OrderSide.SELL and not (take_profit < entry_price < stop_loss):
            return signals

        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        rrr = reward / risk if risk > 0 else 0.0

        # Confidence from volume strength and range quality
        confidence = self.min_confidence
        if volume_ratio >= self.volume_mult * 1.5:
            confidence += 0.05
        if volume_ratio >= self.volume_mult * 2.0:
            confidence += 0.05
        if 0.3 <= range_pct <= 1.5:
            confidence += 0.05  # clean, tradeable range
        confidence = min(0.85, confidence)

        signal = Signal(
            strategy=self.strategy_type,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=(
                TradeQuality.HIGH_CONVICTION
                if confidence > 0.75
                else TradeQuality.STANDARD
            ),
            timeframe="5m",
            market_state=MarketState.TREND,
            notes=(
                f"ORB {direction}: {session_name} range "
                f"{range_low:.4f}-{range_high:.4f} ({range_pct:.2f}%), "
                f"close {last_close:.4f}, vol {volume_ratio:.2f}x"
                + (", mid-range stop (wide range)" if use_mid_stop else "")
            ),
            indicators={
                "range_high": range_high,
                "range_low": range_low,
                "range_height": range_height,
                "range_pct": range_pct,
                "session": session_name,
                "session_open": session_open.isoformat(),
                "volume_ratio": volume_ratio,
                "atr": atr_value,
                "mid_range_stop": use_mid_stop,
                "time_exit_hours": self.time_exit_hours,
                "direction": direction,
            },
            # Validation flags (RiskManager re-validates downstream)
            volume_confirmation=True,
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=rrr >= self.min_rrr,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )

        signals.append(signal)
        self._session_trades[session_key] = self._session_trades.get(session_key, 0) + 1

        logger.info(
            f"{symbol}: ORB signal - {direction.upper()} @ ${entry_price:.4f}, "
            f"SL=${stop_loss:.4f}, TP=${take_profit:.4f}, conf={confidence:.0%}, "
            f"RRR={rrr:.2f}, session={session_name}"
        )

        return signals
