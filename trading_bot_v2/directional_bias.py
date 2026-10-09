"""Directional bias: HTF trend stack + funding-extremes contrarian tilt.

WHY THIS EXISTS
===============
Two measured facts motivated this module (2026-07-30):

1. The regime taxonomy carries almost no forward DIRECTIONAL information
   (docs/REGIME-DISCRIMINATION.md) - it gates strategies on trend
   *strength* while the thing that actually splits winners from losers
   across 2018/2022 vs 2020/2021 is trend *direction*.
2. Every strategy hardcodes ``multi_timeframe_alignment=True``, so the
   one validation flag designed to encode higher-timeframe agreement has
   never gated anything.

This module computes a per-symbol directional bias from two independent
observations and (optionally) enforces it centrally in StrategyManager:

* **Trend bias** - a 4h EMA stack (close vs EMA-fast vs EMA-slow). Long
  only when close > fast > slow, short only when close < fast < slow,
  neutral otherwise. Deliberately dumb and slow-moving.
* **Funding bias** - contrarian read of perpetual funding extremes. When
  the last settled rate sits in the top decile of its trailing window
  AND is positive, longs are crowded -> short bias; bottom decile and
  negative -> long bias. Uses the same causality-guarded surface in
  live (PacificaClient) and backtest (SimulatedExchange /
  FundingSchedule.venue_history): only rates already SETTLED at the
  current bar are visible. Percentile-based, so it is invariant to the
  cross-venue conversion factor (BACKTEST_FUNDING_CONVERSION).

GATE MODES (env ``DIRECTIONAL_GATE``)
=====================================
* ``off`` (default) - bias is not even computed. Shipped behaviour, the
  A/B control arm.
* ``log`` - bias is computed and stamped into signal.indicators
  ["directional_bias"] but nothing is dropped. For measuring how the
  gate WOULD have voted without changing a single trade.
* ``enforce`` - as ``log``, plus signals whose side fights the combined
  bias get ``multi_timeframe_alignment=False`` and are dropped by the
  existing 8-flag validation, attributed by the existing discard
  accounting.

Strategies in ``DIRECTIONAL_GATE_EXEMPT`` are never gated. The default
exempts the strategies whose designs are direction-neutral or
deliberately contrarian at a faster timescale: GridTrading (quotes both
sides by construction), FundingArb (carry, not direction),
LiquidationCapture (fades forced flow inside minutes).

The gate is a HYPOTHESIS, not a finding. It ships default-off; the
fresh campaign measures it (off vs enforce) before anything is claimed.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .indicators import calculate_ema
from .models import OrderSide

# --- Bias values ---
BIAS_LONG = "long"
BIAS_SHORT = "short"
BIAS_NEUTRAL = "neutral"

# --- Gate modes ---
GATE_OFF = "off"
GATE_LOG = "log"
GATE_ENFORCE = "enforce"
GATE_MODES = (GATE_OFF, GATE_LOG, GATE_ENFORCE)
#: Default is "off" so shipped behaviour is unchanged and the campaign
#: has a real control arm - the same convention as VWAP_STOP_SOURCE.
DEFAULT_GATE_MODE = GATE_OFF

# --- Trend defaults ---
#: 4h fits the regime-timeframe convention and the backtest's default
#: 60-candle history slice (EMA-50 needs 50 candles; EMA-200 would not).
DEFAULT_TREND_TIMEFRAME = "4h"
DEFAULT_TREND_EMA_FAST = 20
DEFAULT_TREND_EMA_SLOW = 50

# --- Funding defaults ---
#: Venue settlements in the trailing percentile window. On Pacifica's
#: hourly grid 240 = 10 days.
DEFAULT_FUNDING_LOOKBACK = 240
#: Below this many observed settlements the funding leg reports neutral
#: rather than computing a percentile over noise.
DEFAULT_FUNDING_MIN_OBS = 100
DEFAULT_FUNDING_HIGH_PCT = 0.90
DEFAULT_FUNDING_LOW_PCT = 0.10

DEFAULT_GATE_EXEMPT = "GridTrading,FundingArb,LiquidationCapture"


def validate_gate_mode(value: Any) -> str:
    """Validate the DIRECTIONAL_GATE mode with warn-and-fall-back.

    Args:
        value: Configured value (string or None).

    Returns:
        One of GATE_MODES.
    """
    mode = str(value or "").strip().lower()
    if mode in GATE_MODES:
        return mode
    if mode:
        logger.warning(
            f"DIRECTIONAL_GATE={value!r} is not one of {'/'.join(GATE_MODES)}. "
            f"Falling back to '{DEFAULT_GATE_MODE}'."
        )
    return DEFAULT_GATE_MODE


@dataclass
class DirectionalBias:
    """A symbol's directional bias at one moment.

    Attributes:
        trend: BIAS_* from the HTF EMA stack.
        funding: BIAS_* from the funding-extreme contrarian read.
        detail: Diagnostic values (EMAs, funding percentile, reasons).
    """

    trend: str = BIAS_NEUTRAL
    funding: str = BIAS_NEUTRAL
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def combined(self) -> str:
        """Combine the two legs conservatively.

        Agreement or one-sided evidence gives that side; an outright
        conflict (trend long, funding short or vice versa) gives
        neutral - the gate stands aside rather than picking a winner.
        """
        if self.trend == self.funding:
            return self.trend
        if self.trend == BIAS_NEUTRAL:
            return self.funding
        if self.funding == BIAS_NEUTRAL:
            return self.trend
        return BIAS_NEUTRAL

    def allows(self, side: OrderSide) -> bool:
        """Whether a signal side is compatible with the combined bias."""
        combined = self.combined
        if combined == BIAS_NEUTRAL:
            return True
        return (side == OrderSide.BUY) == (combined == BIAS_LONG)

    def to_indicator(self) -> Dict[str, Any]:
        """Compact dict stamped into signal.indicators for the census."""
        return {
            "trend": self.trend,
            "funding": self.funding,
            "combined": self.combined,
            **self.detail,
        }


class DirectionalBiasEngine:
    """Computes DirectionalBias per symbol from candles + funding history.

    One instance lives on StrategyManager. The funding leg is cached per
    (symbol, hour) because funding only moves on the venue's settlement
    grid - without the cache an 8-year backtest would rebuild a
    240-record history view on every one of ~840k 5m bars.
    """

    def __init__(self, client: Any = None):
        """
        Args:
            client: Object exposing ``get_funding_history(symbol, limit)``
                (PacificaClient live, SimulatedExchange in backtest).
                None disables the funding leg (reports neutral).
        """
        self.client = client
        self.trend_timeframe = os.getenv(
            "DIRECTIONAL_TREND_TIMEFRAME", DEFAULT_TREND_TIMEFRAME
        )
        self.ema_fast = int(
            os.getenv("DIRECTIONAL_TREND_EMA_FAST", str(DEFAULT_TREND_EMA_FAST))
        )
        self.ema_slow = int(
            os.getenv("DIRECTIONAL_TREND_EMA_SLOW", str(DEFAULT_TREND_EMA_SLOW))
        )
        self.funding_lookback = int(
            os.getenv("DIRECTIONAL_FUNDING_LOOKBACK", str(DEFAULT_FUNDING_LOOKBACK))
        )
        self.funding_min_obs = int(
            os.getenv("DIRECTIONAL_FUNDING_MIN_OBS", str(DEFAULT_FUNDING_MIN_OBS))
        )
        self.funding_high_pct = float(
            os.getenv("DIRECTIONAL_FUNDING_HIGH_PCT", str(DEFAULT_FUNDING_HIGH_PCT))
        )
        self.funding_low_pct = float(
            os.getenv("DIRECTIONAL_FUNDING_LOW_PCT", str(DEFAULT_FUNDING_LOW_PCT))
        )
        if self.ema_fast >= self.ema_slow:
            logger.warning(
                f"DIRECTIONAL_TREND_EMA_FAST ({self.ema_fast}) >= "
                f"EMA_SLOW ({self.ema_slow}); trend leg will report neutral"
            )
        # (symbol) -> (hour_key, bias_str, detail)
        self._funding_cache: Dict[str, Tuple[str, str, Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Legs
    # ------------------------------------------------------------------

    def trend_bias(
        self, multi_tf_data: Dict[str, Dict[str, List[float]]]
    ) -> Tuple[str, Dict[str, Any]]:
        """HTF EMA-stack trend bias.

        Long requires close > EMA-fast > EMA-slow, short the mirror.
        Anything else - including insufficient history - is neutral.

        Args:
            multi_tf_data: Per-timeframe OHLCV bundles.

        Returns:
            (BIAS_*, detail dict)
        """
        if self.ema_fast >= self.ema_slow:
            return BIAS_NEUTRAL, {"trend_reason": "bad_ema_config"}

        closes = (multi_tf_data.get(self.trend_timeframe) or {}).get("close") or []
        if len(closes) < self.ema_slow:
            return BIAS_NEUTRAL, {
                "trend_reason": f"insufficient_{self.trend_timeframe}_history",
                "trend_bars": len(closes),
            }

        closes = [float(c) for c in closes]
        price = closes[-1]
        fast = calculate_ema(closes, self.ema_fast)
        slow = calculate_ema(closes, self.ema_slow)

        if price > fast > slow:
            bias = BIAS_LONG
        elif price < fast < slow:
            bias = BIAS_SHORT
        else:
            bias = BIAS_NEUTRAL

        return bias, {
            "trend_tf": self.trend_timeframe,
            "trend_ema_fast": round(fast, 6),
            "trend_ema_slow": round(slow, 6),
        }

    def funding_bias(
        self, symbol: str, now: Optional[datetime] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """Contrarian funding-extreme bias.

        The last settled rate is ranked inside its trailing window. Top
        decile AND positive -> crowded longs -> BIAS_SHORT; bottom
        decile AND negative -> BIAS_LONG. The sign requirement stops an
        "extreme" inside an all-negative (or all-positive) window from
        firing against the actual crowd.

        Args:
            symbol: Bot symbol.
            now: Current time (sim time in backtest) - used only as the
                cache bucket; the client's own causality guard decides
                what is visible.

        Returns:
            (BIAS_*, detail dict)
        """
        if self.client is None:
            return BIAS_NEUTRAL, {"funding_reason": "no_client"}

        hour_key = (now or datetime.now()).strftime("%Y-%m-%dT%H")
        cached = self._funding_cache.get(symbol)
        if cached is not None and cached[0] == hour_key:
            return cached[1], cached[2]

        try:
            history = self.client.get_funding_history(
                symbol, limit=self.funding_lookback
            )
        except Exception as e:
            logger.debug(f"{symbol}: funding history unavailable: {e}")
            history = []

        rates: List[float] = []
        for record in history or []:
            raw = record.get("funding_rate", record.get("rate"))
            try:
                rates.append(float(raw))
            except (TypeError, ValueError):
                continue

        if len(rates) < self.funding_min_obs:
            bias, detail = (
                BIAS_NEUTRAL,
                {
                    "funding_reason": "insufficient_history",
                    "funding_obs": len(rates),
                },
            )
        else:
            current = rates[-1]
            rank = sum(1 for r in rates if r <= current) / len(rates)
            if rank >= self.funding_high_pct and current > 0:
                bias = BIAS_SHORT
            elif rank <= self.funding_low_pct and current < 0:
                bias = BIAS_LONG
            else:
                bias = BIAS_NEUTRAL
            detail = {
                "funding_rate": current,
                "funding_pct": round(rank, 4),
                "funding_obs": len(rates),
            }

        self._funding_cache[symbol] = (hour_key, bias, detail)
        return bias, detail

    # ------------------------------------------------------------------
    # Combined
    # ------------------------------------------------------------------

    def compute(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        now: Optional[datetime] = None,
    ) -> DirectionalBias:
        """Compute the full bias for a symbol at the current moment.

        Args:
            symbol: Bot symbol.
            multi_tf_data: Per-timeframe OHLCV bundles.
            now: Current time (sim time in backtest).

        Returns:
            DirectionalBias with both legs and diagnostics.
        """
        trend, trend_detail = self.trend_bias(multi_tf_data)
        funding, funding_detail = self.funding_bias(symbol, now=now)
        detail: Dict[str, Any] = {}
        detail.update(trend_detail)
        detail.update(funding_detail)
        return DirectionalBias(trend=trend, funding=funding, detail=detail)


def resolve_gate_exempt() -> set[str]:
    """Strategy display names never gated (env DIRECTIONAL_GATE_EXEMPT)."""
    raw = os.getenv("DIRECTIONAL_GATE_EXEMPT", DEFAULT_GATE_EXEMPT)
    return {name.strip() for name in raw.split(",") if name.strip()}
