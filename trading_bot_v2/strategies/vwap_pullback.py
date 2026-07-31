"""
VWAP Pullback-Continuation Strategy

Trend-side continuation entries at the session VWAP, the mechanical
opposite of VWAPScalping's counter-trend fade. Motivated by the
2026-07-29 signal study (docs/VWAP-SIGNAL-STUDY.md): fading deviations
has no edge net of costs, and heavy-volume deviations CONTINUE rather
than revert - which is exactly the population a continuation entry
wants. This strategy is an UNVALIDATED hypothesis; it ships disabled
and the promotion gate decides.

Logic (long; short is the mirror under a bearish stack):
- 4h EMA stack must agree: close > EMA(fast) > EMA(slow). This is the
  strategy's own hard gate, independent of DIRECTIONAL_GATE.
- Session-anchored VWAP (00:00 UTC) over the visible 15m bars of the
  current session, HLC3-weighted, with volume-weighted sigma.
- The session must have EXTENDED away from VWAP first (session high
  >= VWAP + extension_min_sd * sigma) - otherwise there is nothing to
  pull back from.
- The last closed 15m bar is the resumption bar: its low touches the
  VWAP band (low <= VWAP + band_sd * sigma) and it CLOSES back above
  VWAP - the pullback held.
- Optional relative-volume floor on the resumption bar (rvol_min; the
  study measured heavy volume favouring continuation, so unlike the
  fade this lever points WITH volume). Default off.
- Stop below the pullback low minus an ATR(15m) buffer; target at
  tp_rr times the risk; positions carry indicators["time_exit_hours"]
  so stale entries are force-closed.

Requires per-candle timestamps in the 15m data ("timestamp" key). With
the backtest's default 60-candle history slice a late-session VWAP is
computed over the visible tail of the session only; set
BACKTEST_HISTORY_LOOKBACK >= 100 for full-session fidelity.
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ..models import Signal, OrderSide
from ..config import StrategyType, TradeQuality, MarketState, AssetClass
from ..indicators import calculate_atr, calculate_ema, calculate_relative_volume

# Timestamp parsing is identical to SessionRangeBreakout's - reuse it
# rather than growing a third slightly-different parser in the package.
from .session_range_breakout import SessionRangeBreakoutStrategy

_parse_ts = SessionRangeBreakoutStrategy._parse_ts


class VWAPPullbackStrategy:
    """
    Session-VWAP pullback continuation on 15m bars, gated by the 4h
    EMA stack. Runs as an overlay in all regimes (self-gated by trend).
    """

    def __init__(
        self,
        ema_fast: int = 20,
        ema_slow: int = 50,
        band_sd: float = 0.25,
        extension_min_sd: float = 1.0,
        min_session_bars: int = 8,
        rvol_min: float = 0.0,
        rvol_window: int = 96,
        atr_period: int = 14,
        atr_stop_buffer: float = 0.5,
        tp_rr: float = 2.0,
        time_exit_hours: float = 24.0,
        cooldown_hours: float = 4.0,
        min_confidence: float = 0.6,
        min_rrr: float = 1.5,
        enable_short: bool = True,
        entry_mode: str = "market",
        maker_offset_bp: float = 15.0,
        entry_ttl_candles: int = 12,
    ):
        """
        Initialize VWAPPullbackStrategy.

        Args:
            ema_fast: Fast EMA period for the 4h trend stack.
            ema_slow: Slow EMA period for the 4h trend stack.
            band_sd: Pullback band half-width around VWAP in sigma.
            extension_min_sd: Session must have moved at least this many
                sigma away from VWAP before a pullback counts.
            min_session_bars: Minimum visible bars in the current session
                before the session VWAP is trusted (early-session sigma
                is unstable - same guard as the signal study).
            rvol_min: Resumption-bar relative volume floor (0 = off).
            rvol_window: Trailing bars for the rvol median (96 = 1 day
                of 15m bars).
            atr_period: ATR period for the stop buffer.
            atr_stop_buffer: Stop sits this many ATR(15m) beyond the
                pullback extreme.
            tp_rr: Take profit at this multiple of the entry risk.
            time_exit_hours: Max hold carried via
                indicators["time_exit_hours"].
            cooldown_hours: Minimum spacing between entries per symbol.
            min_confidence: Base signal confidence.
            min_rrr: Minimum RRR for rrr_meets_minimum (structurally
                tp_rr, so the flag only fails on misconfiguration).
            enable_short: Whether to mirror the setup under a bearish
                stack.
            entry_mode: "market" (shipped: enter at current price,
                taker fees) or "maker" (rest a limit maker_offset_bp
                inside the move: below market for longs, above for
                shorts - maker fees and better fills, at the cost of
                some entries never filling).
            maker_offset_bp: Limit offset from current price in basis
                points (maker mode only). Must exceed the engine's 0.1%
                market-order threshold to actually rest; 5 bp does not,
                so the engine treats <=10 bp as market - use >10 to rest.
                Kept configurable because the offset IS the tradeoff:
                bigger = better price + more maker fills, fewer trades.
            entry_ttl_candles: 5m replay candles an unfilled maker entry
                may rest before the engine cancels it AND its exit
                orders (carried via indicators["entry_ttl_candles"]).
                Without this a never-filled entry leaves naked exit
                orders that can fill as an inverted position.
        """
        self.strategy_type = StrategyType.VWAP_PULLBACK

        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.band_sd = band_sd
        self.extension_min_sd = extension_min_sd
        self.min_session_bars = min_session_bars
        self.rvol_min = rvol_min
        self.rvol_window = rvol_window
        self.atr_period = atr_period
        self.atr_stop_buffer = atr_stop_buffer
        self.tp_rr = tp_rr
        self.time_exit_hours = time_exit_hours
        self.cooldown_hours = cooldown_hours
        self.min_confidence = min_confidence
        self.min_rrr = min_rrr
        self.enable_short = enable_short
        self.entry_mode = (
            entry_mode if entry_mode in ("market", "maker") else "market"
        )
        self.maker_offset_bp = maker_offset_bp
        self.entry_ttl_candles = entry_ttl_candles

        # symbol -> last signal time (sim time in backtest)
        self._last_signal_time: Dict[str, datetime] = {}
        # Simulated time injected by StrategyManager.set_sim_time()
        self._sim_time: Optional[datetime] = None
        # Symbols already warned about missing timestamps (warn once)
        self._warned_no_timestamp: set = set()

        logger.info(
            f"VWAPPullbackStrategy initialized: 4h EMA {ema_fast}/{ema_slow}, "
            f"band={band_sd} SD, extension>={extension_min_sd} SD, "
            f"stop=low-{atr_stop_buffer}xATR, tp={tp_rr}R, "
            f"time_exit={time_exit_hours}h, rvol_min={rvol_min}, "
            f"short={enable_short}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """Current UTC time - simulated candle time in backtesting."""
        now = self._sim_time if self._sim_time is not None else datetime.now(
            timezone.utc
        )
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now

    def _trend_bias(
        self, multi_tf_data: Dict[str, Any]
    ) -> Tuple[str, Dict[str, float]]:
        """4h EMA-stack direction: 'long', 'short' or 'neutral'."""
        closes = (multi_tf_data.get("4h") or {}).get("close") or []
        if len(closes) < self.ema_slow:
            return "neutral", {}
        closes = [float(c) for c in closes]
        price = closes[-1]
        fast = calculate_ema(closes, self.ema_fast)
        slow = calculate_ema(closes, self.ema_slow)
        if price > fast > slow:
            return "long", {"ema_fast": fast, "ema_slow": slow}
        if price < fast < slow:
            return "short", {"ema_fast": fast, "ema_slow": slow}
        return "neutral", {"ema_fast": fast, "ema_slow": slow}

    @staticmethod
    def _session_vwap(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        idx: List[int],
    ) -> Tuple[float, float]:
        """HLC3 session VWAP and volume-weighted sigma over bars ``idx``.

        Returns:
            (vwap, sigma); sigma is 0.0 when undefined (no volume).
        """
        sum_pv = 0.0
        sum_p2v = 0.0
        sum_v = 0.0
        for i in idx:
            typical = (highs[i] + lows[i] + closes[i]) / 3.0
            sum_pv += typical * volumes[i]
            sum_p2v += typical * typical * volumes[i]
            sum_v += volumes[i]
        if sum_v <= 0:
            return 0.0, 0.0
        vwap = sum_pv / sum_v
        variance = max(sum_p2v / sum_v - vwap * vwap, 0.0)
        return vwap, math.sqrt(variance)

    def _in_cooldown(self, symbol: str, now: datetime) -> bool:
        last = self._last_signal_time.get(symbol)
        if last is None:
            return False
        return (now - last).total_seconds() < self.cooldown_hours * 3600.0

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
        Generate VWAP pullback-continuation signals.

        Args:
            symbol: Trading symbol.
            multi_tf_data: Regime/structure timeframe data (needs 15m
                with timestamps and 4h for the trend stack).
            current_price: Current market price.
            **kwargs: Ignored (accepted for dispatch compatibility).

        Returns:
            List with 0 or 1 Signal objects.
        """
        signals: List[Signal] = []
        if current_price <= 0:
            return signals

        df_15m = multi_tf_data.get("15m")
        if not df_15m:
            logger.debug(f"{symbol}: No 15m data for VWAP pullback")
            return signals

        raw_timestamps = df_15m.get("timestamp")
        if not raw_timestamps:
            if symbol not in self._warned_no_timestamp:
                self._warned_no_timestamp.add(symbol)
                logger.warning(
                    f"{symbol}: 15m data has no 'timestamp' key - "
                    f"VWAPPullback disabled for this symbol"
                )
            return signals

        now = self._now()
        if self._in_cooldown(symbol, now):
            logger.debug(f"{symbol}: VWAP pullback in cooldown")
            return signals

        direction, trend_detail = self._trend_bias(multi_tf_data)
        if direction == "neutral":
            logger.debug(f"{symbol}: 4h EMA stack neutral - no pullback setup")
            return signals
        if direction == "short" and not self.enable_short:
            return signals

        highs = [float(v) for v in df_15m.get("high", [])]
        lows = [float(v) for v in df_15m.get("low", [])]
        closes = [float(v) for v in df_15m.get("close", [])]
        opens = [float(v) for v in df_15m.get("open", [])]
        volumes = [float(v) for v in df_15m.get("volume", [])]
        n = min(
            len(highs), len(lows), len(closes), len(opens),
            len(volumes), len(raw_timestamps),
        )
        if n < self.min_session_bars:
            return signals

        timestamps = [_parse_ts(v) for v in raw_timestamps[:n]]
        last_ts = timestamps[n - 1]
        if last_ts is None:
            return signals

        # Bars belonging to the resumption bar's own session (00:00 UTC)
        session_open = last_ts.replace(hour=0, minute=0, second=0, microsecond=0)
        session_idx = [
            i
            for i, ts in enumerate(timestamps)
            if ts is not None and ts >= session_open
        ]
        if len(session_idx) < self.min_session_bars:
            logger.debug(
                f"{symbol}: only {len(session_idx)} session bars "
                f"(need {self.min_session_bars}) - VWAP not trusted yet"
            )
            return signals

        vwap, sigma = self._session_vwap(highs, lows, closes, volumes, session_idx)
        if vwap <= 0 or sigma <= 0:
            return signals

        last_i = n - 1
        last_close = closes[last_i]
        last_open = opens[last_i]

        if direction == "long":
            # Session must have extended above VWAP before the pullback
            extension = (max(highs[i] for i in session_idx) - vwap) / sigma
            touched = lows[last_i] <= vwap + self.band_sd * sigma
            resumed = last_close > vwap and last_close > last_open
        else:
            extension = (vwap - min(lows[i] for i in session_idx)) / sigma
            touched = highs[last_i] >= vwap - self.band_sd * sigma
            resumed = last_close < vwap and last_close < last_open

        if extension < self.extension_min_sd:
            logger.debug(
                f"{symbol}: session extension {extension:.2f} SD < "
                f"{self.extension_min_sd} - nothing to pull back from"
            )
            return signals
        if not (touched and resumed):
            return signals

        # Optional relative-volume floor on the resumption bar
        rvol = None
        if self.rvol_min > 0:
            if last_i + 1 < self.rvol_window + 1:
                logger.debug(
                    f"{symbol}: insufficient history for rvol "
                    f"({last_i + 1} < {self.rvol_window + 1}) - skipping"
                )
                return signals
            rvol = calculate_relative_volume(
                volumes[: last_i + 1], window=self.rvol_window
            )
            if rvol < self.rvol_min:
                logger.debug(
                    f"{symbol}: resumption rvol {rvol:.2f} < {self.rvol_min}"
                )
                return signals

        # ATR(15m) buffer for the stop
        atr_value = 0.0
        if n >= self.atr_period + 1:
            try:
                atr_value = calculate_atr(highs, lows, closes, self.atr_period)
            except ValueError:
                atr_value = 0.0
        if atr_value <= 0:
            return signals

        # Maker mode rests a limit inside the move; market mode (shipped)
        # enters at the current price. The engine turns any entry more
        # than 0.1% from market into a resting limit, which fills as a
        # maker order in the sim and live alike.
        if self.entry_mode == "maker":
            offset = self.maker_offset_bp / 10_000.0
            if direction == "long":
                entry_price = current_price * (1.0 - offset)
            else:
                entry_price = current_price * (1.0 + offset)
        else:
            entry_price = current_price

        if direction == "long":
            side = OrderSide.BUY
            stop_loss = lows[last_i] - self.atr_stop_buffer * atr_value
            risk = entry_price - stop_loss
            take_profit = entry_price + self.tp_rr * risk
        else:
            side = OrderSide.SELL
            stop_loss = highs[last_i] + self.atr_stop_buffer * atr_value
            risk = stop_loss - entry_price
            take_profit = entry_price - self.tp_rr * risk

        if risk <= 0:
            return signals
        # Geometry sanity: entry must sit between stop and target
        if side == OrderSide.BUY and not (stop_loss < entry_price < take_profit):
            return signals
        if side == OrderSide.SELL and not (take_profit < entry_price < stop_loss):
            return signals

        rrr = self.tp_rr  # by construction
        confidence = self.min_confidence
        if extension >= 1.5 * self.extension_min_sd:
            confidence += 0.05
        if rvol is not None and rvol >= 1.5:
            confidence += 0.05
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
            timeframe="15m",
            market_state=MarketState.TREND,
            notes=(
                f"VWAP pullback {direction}: vwap {vwap:.4f} "
                f"(sigma {sigma:.4f}), extension {extension:.2f} SD, "
                f"resumption close {last_close:.4f}"
                + (f", rvol {rvol:.2f}" if rvol is not None else "")
            ),
            indicators={
                "vwap": vwap,
                "vwap_sigma": sigma,
                "extension_sd": extension,
                "pullback_low": lows[last_i],
                "pullback_high": highs[last_i],
                "atr": atr_value,
                "session_open": session_open.isoformat(),
                "session_bars": len(session_idx),
                "trend_ema_fast": trend_detail.get("ema_fast"),
                "trend_ema_slow": trend_detail.get("ema_slow"),
                "rvol": rvol,
                "time_exit_hours": self.time_exit_hours,
                "direction": direction,
                "entry_mode": self.entry_mode,
                # Engine cancels the unfilled entry + its exits after
                # this many replay candles (maker mode only)
                "entry_ttl_candles": (
                    self.entry_ttl_candles
                    if self.entry_mode == "maker"
                    else None
                ),
            },
            # Validation flags (RiskManager re-validates downstream).
            # multi_timeframe_alignment is TRUE BY MEASUREMENT here: the
            # 4h stack gate is a hard entry condition, not a hardcode.
            volume_confirmation=(self.rvol_min <= 0 or rvol is not None),
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=rrr >= self.min_rrr,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )

        signals.append(signal)
        self._last_signal_time[symbol] = now

        logger.info(
            f"{symbol}: VWAP pullback signal - {direction.upper()} @ "
            f"${entry_price:.4f}, SL=${stop_loss:.4f}, TP=${take_profit:.4f}, "
            f"conf={confidence:.0%}, ext={extension:.2f} SD"
        )
        return signals
