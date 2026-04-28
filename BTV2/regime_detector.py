"""
regime_detector.py — Universal Market Regime Detection for BTV2
===============================================================

A reusable, pluggable regime detection system that can be used across ALL
strategies in the BTV2 backtesting engine.

Regime Types
------------
- BULL_STRONG  : Strong uptrend  (ADX > 30, EMA bullish alignment, positive momentum)
- BULL_WEAK    : Weak uptrend    (ADX 20-30, EMA bullish, mild positive momentum)
- RANGING      : Sideways/choppy (ADX < 20, mixed EMAs, near-zero momentum)
- BEAR_WEAK    : Weak downtrend  (ADX 20-30, EMA bearish, mild negative momentum)
- BEAR_STRONG  : Strong downtrend (ADX > 30, EMA bearish alignment, negative momentum)

Detection Methods
-----------------
1. ADX-Based     : Trend strength via ADX + directional bias via EMA
2. EMA-Based     : EMA(9) > EMA(21) > EMA(50) hierarchy
3. Volatility    : ATR relative to its rolling average
4. Momentum      : Rate of Change + MACD histogram (NEW — fixes bear market detection)
5. Combined      : Weighted score from all four methods

Trend Direction
---------------
- UP    : EMA alignment bullish + price in upper range
- DOWN  : EMA alignment bearish + price in lower range
- NEUTRAL : Mixed signals

Usage
-----
    from regime_detector import RegimeDetector, MarketRegime

    detector = RegimeDetector(df, method="combined")
    regimes  = detector.get_regimes()          # pd.Series[MarketRegime]
    current  = detector.get_current_regime()   # MarketRegime
    ok       = detector.is_suitable_for("mean_reversion")  # bool
    trend    = detector.get_trend_direction()  # TrendDirection
    size     = detector.get_position_size_multiplier("momentum")  # 0-1

    # Per-bar query inside a strategy loop:
    regime = detector.get_regime_at(i)
    if regime not in allowed_regimes:
        continue
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# REGIME ENUM
# ─────────────────────────────────────────────────────────────────────────────

class MarketRegime(str, Enum):
    """Canonical market regimes detected by the universal regime detector."""
    BULL_STRONG = "bull_strong"
    BULL_WEAK   = "bull_weak"
    RANGING     = "ranging"
    BEAR_WEAK   = "bear_weak"
    BEAR_STRONG = "bear_strong"


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY COMPATIBILITY MATRIX
# ─────────────────────────────────────────────────────────────────────────────

STRATEGY_REGIME_COMPATIBILITY: dict[str, list[MarketRegime]] = {
    "Mean Reversion":      [MarketRegime.RANGING,     MarketRegime.BULL_WEAK],
    "VWAP Scalping":       [MarketRegime.RANGING,     MarketRegime.BULL_WEAK],
    "Momentum Scalping":   [MarketRegime.BULL_STRONG, MarketRegime.BEAR_STRONG],
    "Liquidation Capture": [MarketRegime.BULL_WEAK,   MarketRegime.RANGING],
    "Grid Trading":        [MarketRegime.RANGING],
    "MA Crossover":        [MarketRegime.BULL_STRONG, MarketRegime.BEAR_STRONG],
}


# ─────────────────────────────────────────────────────────────────────────────
# TIMEFRAME-SPECIFIC PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

# FIXED: Added 5m params, increased thresholds for less noise
TIMEFRAME_PARAMS: dict[str, dict[str, float]] = {
    "1d": {
        "adx_strong": 30.0,
        "adx_weak": 20.0,
        "momentum_strong": 0.30,
        "momentum_weak": 0.10,
    },
    "4h": {
        "adx_strong": 25.0,
        "adx_weak": 15.0,
        "momentum_strong": 0.25,
        "momentum_weak": 0.08,
    },
    "1h": {
        "adx_strong": 20.0,
        "adx_weak": 12.0,
        "momentum_strong": 0.20,
        "momentum_weak": 0.05,
    },
    "5m": {
        "adx_strong": 28.0,   # FIXED: Higher = less oscillation
        "adx_weak": 18.0,   # FIXED: Higher = less noise  
        "momentum_strong": 0.30,  # FIXED: Higher threshold
        "momentum_weak": 0.10,     # FIXED: Higher threshold
    },
    "15m": {
        "adx_strong": 25.0,   # FIXED: Raised from 18
        "adx_weak": 15.0,     # FIXED: Raised from 10
        "momentum_strong": 0.25,  # FIXED: Raised from 0.15
        "momentum_weak": 0.08,    # FIXED: Raised from 0.03
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# TREND DIRECTION
# ─────────────────────────────────────────────────────────────────────────────

class TrendDirection(str, Enum):
    """Trend direction for pullback trading context."""
    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# SHARED INDICATORS (mirrors strategies.py to avoid circular imports)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def _compute_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Wilder ADX (same implementation as strategies.compute_adx)."""
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    plus_dm  = np.where((high - prev_high) > (prev_low - low),
                        np.maximum(high - prev_high, 0), 0.0)
    minus_dm = np.where((prev_low - low) > (high - prev_high),
                        np.maximum(prev_low - low, 0), 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=high.index)
    minus_dm_s = pd.Series(minus_dm, index=low.index)

    atr_s    = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_di  = 100 * plus_dm_s.ewm(alpha=1.0 / period, min_periods=period,
                                    adjust=False).mean() / (atr_s + 1e-10)
    minus_di = 100 * minus_dm_s.ewm(alpha=1.0 / period, min_periods=period,
                                     adjust=False).mean() / (atr_s + 1e-10)

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return adx


def _compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Wilder ATR from true range."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────────────────
# REGIME DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class RegimeDetector:
    """
    Universal market regime detector.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with columns: Open, High, Low, Close, Volume.
        Must have a DatetimeIndex.
    method : str
        Detection method: "adx", "ema", "volatility", "momentum", or "combined".
        Default "combined" uses a weighted score from all four methods.
    adx_period : int
        Period for ADX calculation. Default 14.
    ema_fast / ema_mid / ema_slow : int
        EMA periods for trend hierarchy. Defaults 9, 21, 50.
    atr_period : int
        Period for ATR calculation. Default 14.
    atr_avg_period : int
        Rolling window for average ATR (volatility baseline). Default 50.
    adx_strong / adx_weak : float
        ADX thresholds for strong/weak trend classification.
        Defaults: adx_strong=30, adx_weak=20.
    vol_high / vol_low : float
        ATR multipliers for high/low volatility classification.
        Defaults: vol_high=2.0, vol_low=0.5.
    weights : dict
        Weights for combined method: {"adx": 0.40, "ema": 0.25, "vol": 0.10, "momentum": 0.25}.
        Must sum to 1.0.
    timeframe : str, optional
        Timeframe identifier for auto-tuning thresholds: "1d", "4h", "1h", "15m".
        If provided, overrides adx_strong/weak and momentum thresholds from
        TIMEFRAME_PARAMS.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        method: str = "combined",
        adx_period: int = 14,
        ema_fast: int = 9,
        ema_mid: int = 21,
        ema_slow: int = 50,
        atr_period: int = 14,
        atr_avg_period: int = 50,
        adx_strong: float = 30.0,
        adx_weak: float = 20.0,
        vol_high: float = 2.0,
        vol_low: float = 0.5,
        weights: Optional[dict[str, float]] = None,
        timeframe: Optional[str] = None,
    ) -> None:
        self.df = df
        self.method = method
        self.adx_period = adx_period
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.atr_avg_period = atr_avg_period
        self.vol_high = vol_high
        self.vol_low = vol_low
        self.timeframe = timeframe

        # Apply timeframe-specific parameters if specified
        if timeframe and timeframe in TIMEFRAME_PARAMS:
            tf_params = TIMEFRAME_PARAMS[timeframe]
            self.adx_strong = tf_params["adx_strong"]
            self.adx_weak = tf_params["adx_weak"]
            self.momentum_strong = tf_params["momentum_strong"]
            self.momentum_weak = tf_params["momentum_weak"]
        else:
            self.adx_strong = adx_strong
            self.adx_weak = adx_weak
            self.momentum_strong = 0.30
            self.momentum_weak = 0.10

        self.weights = weights or {
            "adx": 0.40, "ema": 0.25, "vol": 0.10, "momentum": 0.25,
        }

        # Pre-compute all indicators
        self._close = df["Close"]
        self._high  = df["High"]
        self._low   = df["Low"]

        self._adx   = _compute_adx(self._high, self._low, self._close, adx_period)
        self._ema9  = _compute_ema(self._close, ema_fast)
        self._ema21 = _compute_ema(self._close, ema_mid)
        self._ema50 = _compute_ema(self._close, ema_slow)
        self._atr   = _compute_atr(self._high, self._low, self._close, atr_period)
        self._atr_avg = self._atr.rolling(atr_avg_period).mean()

        # Pre-compute momentum
        self._momentum = self._compute_momentum_score()

        # Cache
        self._regimes: Optional[pd.Series] = None
        self._confidence: Optional[pd.Series] = None
        self._trend_direction: Optional[pd.Series] = None

    # ── Internal detection methods ────────────────────────────────────────────

    def _detect_adx_regime(self) -> pd.Series:
        """
        ADX-based regime detection.

        ADX > 30 + EMA bullish → BULL_STRONG
        ADX > 30 + EMA bearish → BEAR_STRONG
        ADX < 20 → RANGING
        Otherwise → weak trend in EMA direction
        """
        n = len(self._close)
        regimes = np.full(n, MarketRegime.RANGING, dtype=object)

        ema_bullish = (self._ema9 > self._ema21) & (self._ema21 > self._ema50)
        ema_bearish = (self._ema9 < self._ema21) & (self._ema21 < self._ema50)
        adx_vals = self._adx.values

        for i in range(n):
            if np.isnan(adx_vals[i]):
                continue

            adx_v = adx_vals[i]
            bull  = bool(ema_bullish.iloc[i]) if not pd.isna(ema_bullish.iloc[i]) else False
            bear  = bool(ema_bearish.iloc[i]) if not pd.isna(ema_bearish.iloc[i]) else False

            if adx_v >= self.adx_strong:
                regimes[i] = MarketRegime.BULL_STRONG if bull else (
                    MarketRegime.BEAR_STRONG if bear else MarketRegime.RANGING
                )
            elif adx_v >= self.adx_weak:
                regimes[i] = MarketRegime.BULL_WEAK if bull else (
                    MarketRegime.BEAR_WEAK if bear else MarketRegime.RANGING
                )
            else:
                regimes[i] = MarketRegime.RANGING

        return pd.Series(regimes, index=self._close.index, name="adx_regime")

    def _detect_ema_regime(self) -> pd.Series:
        """
        EMA-based regime detection.

        EMA(9) > EMA(21) > EMA(50) → BULL (strength by separation)
        EMA(9) < EMA(21) < EMA(50) → BEAR (strength by separation)
        Mixed → RANGING
        """
        n = len(self._close)
        regimes = np.full(n, MarketRegime.RANGING, dtype=object)

        e9  = self._ema9.values
        e21 = self._ema21.values
        e50 = self._ema50.values

        for i in range(n):
            if np.isnan(e9[i]) or np.isnan(e21[i]) or np.isnan(e50[i]):
                continue

            # Full alignment
            if e9[i] > e21[i] > e50[i]:
                # Strength by separation ratio
                sep = (e9[i] - e50[i]) / (e50[i] + 1e-10)
                regimes[i] = MarketRegime.BULL_STRONG if sep > 0.05 else MarketRegime.BULL_WEAK
            elif e9[i] < e21[i] < e50[i]:
                sep = (e50[i] - e9[i]) / (e50[i] + 1e-10)
                regimes[i] = MarketRegime.BEAR_STRONG if sep > 0.05 else MarketRegime.BEAR_WEAK
            else:
                regimes[i] = MarketRegime.RANGING

        return pd.Series(regimes, index=self._close.index, name="ema_regime")

    def _detect_volatility_regime(self) -> pd.Series:
        """
        Volatility-based regime detection.

        ATR > 2× average → HIGH_VOLATILITY (maps to STRONG regimes)
        ATR < 0.5× average → LOW_VOLATILITY (maps to RANGING)
        Otherwise → use EMA direction for weak/strong classification
        """
        n = len(self._close)
        regimes = np.full(n, MarketRegime.RANGING, dtype=object)

        atr_vals    = self._atr.values
        atr_avg_vals = self._atr_avg.values
        e9  = self._ema9.values
        e21 = self._ema21.values

        for i in range(n):
            if np.isnan(atr_vals[i]) or np.isnan(atr_avg_vals[i]):
                continue

            ratio = atr_vals[i] / (atr_avg_vals[i] + 1e-10)
            bullish = e9[i] > e21[i] if not np.isnan(e9[i]) and not np.isnan(e21[i]) else False
            bearish = e9[i] < e21[i] if not np.isnan(e9[i]) and not np.isnan(e21[i]) else False

            if ratio > self.vol_high:
                regimes[i] = MarketRegime.BULL_STRONG if bullish else (
                    MarketRegime.BEAR_STRONG if bearish else MarketRegime.RANGING
                )
            elif ratio < self.vol_low:
                regimes[i] = MarketRegime.RANGING
            else:
                regimes[i] = MarketRegime.BULL_WEAK if bullish else (
                    MarketRegime.BEAR_WEAK if bearish else MarketRegime.RANGING
                )

        return pd.Series(regimes, index=self._close.index, name="vol_regime")

    def _compute_momentum_score(self) -> pd.Series:
        """
        Calculate price momentum across multiple timeframes.

        Combines:
        - Rate of Change (ROC) at 5, 10, 20 periods
        - MACD histogram for short-term momentum shifts

        Returns a score normalized to approximately [-1, +1].
        """
        # Rate of Change
        roc_5 = self._close.pct_change(5)
        roc_10 = self._close.pct_change(10)
        roc_20 = self._close.pct_change(20)

        # MACD histogram
        ema12 = self._close.ewm(span=12, adjust=False).mean()
        ema26 = self._close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - macd_signal

        # Combine into momentum score
        momentum = (roc_5 + roc_10 + roc_20) / 3.0
        momentum = momentum.clip(-0.1, 0.1) / 0.1  # Normalize ROC component
        momentum += macd_hist / (self._close + 1e-10) * 100  # Add MACD
        momentum = momentum.clip(-1, 1)

        return momentum

    def _detect_momentum_regime(self) -> pd.Series:
        """
        Momentum-based regime detection.

        Uses the pre-computed momentum score to classify regimes:
        momentum < -momentum_strong → BEAR_STRONG
        momentum < -momentum_weak   → BEAR_WEAK
        momentum > momentum_strong  → BULL_STRONG
        momentum > momentum_weak    → BULL_WEAK
        otherwise                   → RANGING
        """
        n = len(self._close)
        regimes = np.full(n, MarketRegime.RANGING, dtype=object)
        mom_vals = self._momentum.values

        for i in range(n):
            if np.isnan(mom_vals[i]):
                continue

            m = mom_vals[i]
            if m < -self.momentum_strong:
                regimes[i] = MarketRegime.BEAR_STRONG
            elif m < -self.momentum_weak:
                regimes[i] = MarketRegime.BEAR_WEAK
            elif m > self.momentum_strong:
                regimes[i] = MarketRegime.BULL_STRONG
            elif m > self.momentum_weak:
                regimes[i] = MarketRegime.BULL_WEAK
            else:
                regimes[i] = MarketRegime.RANGING

        return pd.Series(regimes, index=self._close.index, name="momentum_regime")

    # ── Scoring for combined method ───────────────────────────────────────────

    def _score_regime(self) -> tuple[pd.Series, pd.Series]:
        """
        Compute a regime score for each bar.

        Returns (regime_series, confidence_series).
        Confidence is 0-1 based on agreement between methods.
        Now includes momentum as the 4th detection method.
        """
        adx_regimes  = self._detect_adx_regime()
        ema_regimes  = self._detect_ema_regime()
        vol_regimes  = self._detect_volatility_regime()
        mom_regimes  = self._detect_momentum_regime()

        n = len(self._close)
        regime_arr = np.full(n, MarketRegime.RANGING, dtype=object)
        conf_arr   = np.zeros(n, dtype=float)

        # Map regimes to numeric scores for averaging
        # BULL_STRONG=2, BULL_WEAK=1, RANGING=0, BEAR_WEAK=-1, BEAR_STRONG=-2
        regime_score = {
            MarketRegime.BULL_STRONG:  2.0,
            MarketRegime.BULL_WEAK:    1.0,
            MarketRegime.RANGING:      0.0,
            MarketRegime.BEAR_WEAK:   -1.0,
            MarketRegime.BEAR_STRONG: -2.0,
        }
        reverse_score = {v: k for k, v in regime_score.items()}

        w_adx = self.weights.get("adx", 0.40)
        w_ema = self.weights.get("ema", 0.25)
        w_vol = self.weights.get("vol", 0.10)
        w_mom = self.weights.get("momentum", 0.25)

        for i in range(n):
            s_adx = regime_score.get(adx_regimes.iloc[i], 0.0)
            s_ema = regime_score.get(ema_regimes.iloc[i], 0.0)
            s_vol = regime_score.get(vol_regimes.iloc[i], 0.0)
            s_mom = regime_score.get(mom_regimes.iloc[i], 0.0)

            weighted = w_adx * s_adx + w_ema * s_ema + w_vol * s_vol + w_mom * s_mom

            # Round to nearest integer score, then map back
            rounded = int(round(weighted))
            rounded = max(-2, min(2, rounded))  # clamp
            regime_arr[i] = reverse_score[rounded]

            # Confidence: how much do the methods agree?
            # If all four agree → 1.0; if all disagree → ~0.25
            scores = [s_adx, s_ema, s_vol, s_mom]
            mean_s = np.mean(scores)
            std_s  = np.std(scores)
            # Normalize: std=0 → conf=1.0, std=2 → conf=0.0
            conf_arr[i] = max(0.0, min(1.0, 1.0 - std_s / 2.0))

        return (
            pd.Series(regime_arr, index=self._close.index, name="combined_regime"),
            pd.Series(conf_arr,   index=self._close.index, name="confidence"),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_regimes(self, hysteresis: int = 0) -> pd.Series:
        """
        Return a pd.Series of MarketRegime for every bar in the DataFrame.

        The detection method is determined by the `method` constructor parameter.
        
        Parameters
        ----------
        hysteresis : int
            Minimum consecutive bars before regime can change.
            Default 0 = no hysteresis.
            Recommended: 6-12 bars (30-60 min on 5m)
        """
        if self._regimes is not None and hysteresis == 0:
            return self._regimes

        if self.method == "adx":
            raw_regimes = self._detect_adx_regime()
        elif self.method == "ema":
            raw_regimes = self._detect_ema_regime()
        elif self.method == "volatility":
            raw_regimes = self._detect_volatility_regime()
        else:  # "combined"
            raw_regimes, self._confidence = self._score_regime()

        # Apply hysteresis if requested
        if hysteresis > 0:
            raw_regimes = self._apply_hysteresis(raw_regimes, hysteresis)

        if hysteresis == 0:
            self._regimes = raw_regimes
            
        return raw_regimes

    def _apply_hysteresis(self, regimes: pd.Series, min_bars: int) -> pd.Series:
        """
        Apply hysteresis: require N consecutive bars before regime change.
        
        This prevents rapid flip-flopping between regimes.
        """
        if len(regimes) <= min_bars:
            return regimes
            
        # Create a new series for smoothed regimes
        smoothed = regimes.copy()
        
        current_regime = regimes.iloc[0]
        count = 0
        
        for i in range(len(regimes)):
            if regimes.iloc[i] == current_regime:
                count += 1
            else:
                # Regime changed - check if we've been in current long enough
                if count >= min_bars:
                    current_regime = regimes.iloc[i]
                    count = 1
                else:
                    # Not long enough - keep current regime
                    smoothed.iloc[i] = current_regime
                    count += 1
                    
        return smoothed

    def get_confidence(self) -> pd.Series:
        """
        Return a pd.Series of confidence scores (0-1) for each bar.
        Only meaningful for method="combined"; returns 1.0 for single-method.
        """
        if self._confidence is not None:
            return self._confidence

        # Trigger regime computation
        self.get_regimes()

        if self._confidence is None:
            # Single-method: return 1.0 as confidence
            self._confidence = pd.Series(1.0, index=self._close.index, name="confidence")

        return self._confidence

    def get_current_regime(self) -> MarketRegime:
        """Return the regime of the most recent bar."""
        regimes = self.get_regimes()
        return regimes.iloc[-1]

    def get_regime_at(self, index: int) -> MarketRegime:
        """Return the regime at a specific integer position in the DataFrame."""
        regimes = self.get_regimes()
        return regimes.iloc[index]

    def get_regime_series(self) -> pd.Series:
        """Alias for get_regimes() — convenient for strategy loops."""
        return self.get_regimes()

    def is_suitable_for(self, strategy_name: str, regime: Optional[MarketRegime] = None) -> bool:
        """
        Check if the current (or specified) regime is suitable for a strategy.

        Parameters
        ----------
        strategy_name : str
            Name of the strategy (must be in STRATEGY_REGIME_COMPATIBILITY).
        regime : MarketRegime, optional
            Specific regime to check. If None, uses the current regime.

        Returns
        -------
        bool
            True if the regime is suitable for the strategy.
        """
        if regime is None:
            regime = self.get_current_regime()

        allowed = STRATEGY_REGIME_COMPATIBILITY.get(strategy_name)
        if allowed is None:
            return True  # Unknown strategy → no filter

        return regime in allowed

    def is_suitable_at(self, strategy_name: str, index: int) -> bool:
        """Check if the regime at a specific bar index is suitable for a strategy."""
        regime = self.get_regime_at(index)
        return self.is_suitable_for(strategy_name, regime)

    def get_regime_summary(self) -> pd.DataFrame:
        """
        Return a summary DataFrame with regime and confidence for each bar.

        Columns: regime, confidence
        """
        regimes = self.get_regimes()
        confidence = self.get_confidence()
        return pd.DataFrame({
            "regime": regimes,
            "confidence": confidence,
        }, index=self._close.index)

    def get_regime_distribution(self) -> dict[str, float]:
        """
        Return the percentage distribution of regimes across the dataset.

        Returns dict mapping regime name to percentage (0-100).
        """
        regimes = self.get_regimes()
        counts = regimes.value_counts(normalize=True) * 100
        return {regime.value: round(pct, 1) for regime, pct in counts.items()}

    # ── HTF REGIME DETECTION (NEW) ─────────────────────────────────────────────

    def get_htf_regime_series(self, htf: str = "1h", hysteresis: int = 3) -> pd.Series:
        """
        Compute regime on HIGHER timeframe and map to current (lower) timeframe.
        
        This is the CORRECT way to detect regime:
        - Use HTF (1h, 4h) for clean regime signal
        - Use LTF (5m, 15m) for trade entries
        
        Parameters
        ----------
        htf : str
            Higher timeframe for regime detection ("1h", "4h", "1d")
        hysteresis : int
            Minimum consecutive HTF bars before regime can change.
            Default 3 = 3 hours (for 1h) or 12 hours (for 4h)
            
        Returns
        -------
        pd.Series
            Regime for each bar in the original dataframe, computed from HTF
        """
        import re
        
        # Map to pandas resample rule
        htf_map = {"1h": "1H", "4h": "4H", "1d": "1D", "15m": "15T", "5m": "5T"}
        rule = htf_map.get(htf, "1H")
        
        # Resample to HTF (use last value of each hour)
        df_htf = self.df.resample(rule).agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }).dropna()
        
        # Compute regime on HTF with hysteresis
        htf_detector = RegimeDetector(
            df_htf,
            method="combined",
            timeframe=htf,
        )
        htf_regimes = htf_detector.get_regimes(hysteresis=hysteresis)
        
        # Map HTF regime back to LTF bars (forward fill)
        # Create a mapping from HTF index to regime
        htf_regime_map = htf_regimes.to_dict()
        
        # For each LTF bar, find the corresponding HTF bar
        ltf_regimes = []
        for idx in self.df.index:
            # Find the last HTF bar that started before this LTF bar
            htf_idx = htf_regimes.index[htf_regimes.index <= idx]
            if len(htf_idx) > 0:
                ltf_regimes.append(htf_regimes.loc[htf_idx[-1]])
            else:
                ltf_regimes.append(MarketRegime.RANGING)  # Default
        
        return pd.Series(ltf_regimes, index=self.df.index, name=f"regime_{htf}")

    def get_regime_periods(self) -> list[dict]:
        """
        Identify contiguous periods of the same regime.

        Returns a list of dicts with keys:
            regime, start, end, duration_bars, avg_confidence
        """
        regimes = self.get_regimes()
        confidence = self.get_confidence()

        periods = []
        if len(regimes) == 0:
            return periods

        current_regime = regimes.iloc[0]
        start_idx = 0
        conf_sum = confidence.iloc[0]

        for i in range(1, len(regimes)):
            if regimes.iloc[i] != current_regime:
                periods.append({
                    "regime": current_regime.value,
                    "start": regimes.index[start_idx],
                    "end": regimes.index[i - 1],
                    "duration_bars": i - start_idx,
                    "avg_confidence": round(conf_sum / (i - start_idx), 3),
                })
                current_regime = regimes.iloc[i]
                start_idx = i
                conf_sum = confidence.iloc[i]
            else:
                conf_sum += confidence.iloc[i]

        # Final period
        periods.append({
            "regime": current_regime.value,
            "start": regimes.index[start_idx],
            "end": regimes.index[-1],
            "duration_bars": len(regimes) - start_idx,
            "avg_confidence": round(conf_sum / (len(regimes) - start_idx), 3),
        })

        return periods

    # ── Trend Direction (Task 3) ──────────────────────────────────────────────

    def get_trend_direction(self) -> pd.Series:
        """
        Determine trend direction for each bar using multiple methods.

        Combines:
        - EMA alignment (9 > 21 > 50 = bullish, reverse = bearish)
        - Price position within 20-bar range (higher highs / lower lows)

        Returns pd.Series of TrendDirection for each bar.
        """
        if self._trend_direction is not None:
            return self._trend_direction

        # EMA alignment
        ema_bullish = (self._ema9 > self._ema21) & (self._ema21 > self._ema50)
        ema_bearish = (self._ema9 < self._ema21) & (self._ema21 < self._ema50)

        # Price position within 20-bar range
        hh = self._high.rolling(20).max()
        ll = self._low.rolling(20).min()
        price_range = hh - ll
        price_position = (self._close - ll) / (price_range + 1e-10)

        n = len(self._close)
        trend_arr = np.full(n, TrendDirection.NEUTRAL, dtype=object)

        for i in range(n):
            bull = bool(ema_bullish.iloc[i]) if not pd.isna(ema_bullish.iloc[i]) else False
            bear = bool(ema_bearish.iloc[i]) if not pd.isna(ema_bearish.iloc[i]) else False
            pp = price_position.iloc[i] if not pd.isna(price_position.iloc[i]) else 0.5

            if bull and pp > 0.6:
                trend_arr[i] = TrendDirection.UP
            elif bear and pp < 0.4:
                trend_arr[i] = TrendDirection.DOWN
            else:
                trend_arr[i] = TrendDirection.NEUTRAL

        self._trend_direction = pd.Series(
            trend_arr, index=self._close.index, name="trend_direction",
        )
        return self._trend_direction

    def get_trend_at(self, index: int) -> TrendDirection:
        """Return the trend direction at a specific integer position."""
        trends = self.get_trend_direction()
        return trends.iloc[index]

    def get_current_trend(self) -> TrendDirection:
        """Return the trend direction of the most recent bar."""
        trends = self.get_trend_direction()
        return trends.iloc[-1]

    # ── Position Sizing (Task 4) ──────────────────────────────────────────────

    # Strategy-specific regime suitability for position sizing
    STRATEGY_SUITABILITY: dict[str, dict[MarketRegime, float]] = {
        "mean_reversion": {
            MarketRegime.RANGING:     1.0,
            MarketRegime.BULL_WEAK:   0.7,
            MarketRegime.BEAR_WEAK:   0.5,
            MarketRegime.BULL_STRONG: 0.3,
            MarketRegime.BEAR_STRONG: 0.0,
        },
        "momentum": {
            MarketRegime.BULL_STRONG: 1.0,
            MarketRegime.BEAR_STRONG: 1.0,
            MarketRegime.BULL_WEAK:   0.7,
            MarketRegime.BEAR_WEAK:   0.7,
            MarketRegime.RANGING:     0.2,
        },
        "vwap_scalping": {
            MarketRegime.RANGING:     1.0,
            MarketRegime.BULL_WEAK:   0.8,
            MarketRegime.BEAR_WEAK:   0.5,
            MarketRegime.BULL_STRONG: 0.4,
            MarketRegime.BEAR_STRONG: 0.0,
        },
        "liquidation_capture": {
            MarketRegime.BEAR_STRONG: 1.0,
            MarketRegime.BEAR_WEAK:   0.8,
            MarketRegime.RANGING:     0.6,
            MarketRegime.BULL_WEAK:   0.4,
            MarketRegime.BULL_STRONG: 0.1,
        },
        "grid_trading": {
            MarketRegime.RANGING:     1.0,
            MarketRegime.BULL_WEAK:   0.5,
            MarketRegime.BEAR_WEAK:   0.5,
            MarketRegime.BULL_STRONG: 0.2,
            MarketRegime.BEAR_STRONG: 0.0,
        },
        "ma_crossover": {
            MarketRegime.BULL_STRONG: 1.0,
            MarketRegime.BEAR_STRONG: 1.0,
            MarketRegime.BULL_WEAK:   0.6,
            MarketRegime.BEAR_WEAK:   0.6,
            MarketRegime.RANGING:     0.1,
        },
    }

    def get_position_size_multiplier(
        self,
        strategy_name: str,
        regime: Optional[MarketRegime] = None,
        index: Optional[int] = None,
    ) -> float:
        """
        Return a 0-1 position size multiplier based on regime confidence
        and strategy suitability.

        Parameters
        ----------
        strategy_name : str
            Strategy key (e.g., "mean_reversion", "momentum").
        regime : MarketRegime, optional
            Specific regime to evaluate. If None, uses current or bar at `index`.
        index : int, optional
            Bar index to evaluate. Used when `regime` is None.

        Returns
        -------
        float
            Position size multiplier in [0, 1].
        """
        if regime is None:
            if index is not None:
                regime = self.get_regime_at(index)
            else:
                regime = self.get_current_regime()

        confidence = self.get_confidence()
        if index is not None:
            conf_val = confidence.iloc[index]
        else:
            conf_val = confidence.iloc[-1]

        suitability_map = self.STRATEGY_SUITABILITY.get(strategy_name, {})
        base_suitability = suitability_map.get(regime, 0.5)

        return base_suitability * conf_val

    def get_position_size_series(self, strategy_name: str) -> pd.Series:
        """
        Return a pd.Series of position size multipliers for every bar.

        Useful for equity curve simulation with dynamic position sizing.
        """
        regimes = self.get_regimes()
        confidence = self.get_confidence()
        suitability_map = self.STRATEGY_SUITABILITY.get(strategy_name, {})

        multipliers = []
        for i in range(len(regimes)):
            regime = regimes.iloc[i]
            conf = confidence.iloc[i]
            base = suitability_map.get(regime, 0.5)
            multipliers.append(base * conf)

        return pd.Series(multipliers, index=self._close.index, name="position_size")

        current_regime = regimes.iloc[0]
        start_idx = 0
        conf_sum = confidence.iloc[0]

        for i in range(1, len(regimes)):
            if regimes.iloc[i] != current_regime:
                periods.append({
                    "regime": current_regime.value,
                    "start": regimes.index[start_idx],
                    "end": regimes.index[i - 1],
                    "duration_bars": i - start_idx,
                    "avg_confidence": round(conf_sum / (i - start_idx), 3),
                })
                current_regime = regimes.iloc[i]
                start_idx = i
                conf_sum = confidence.iloc[i]
            else:
                conf_sum += confidence.iloc[i]

        # Final period
        periods.append({
            "regime": current_regime.value,
            "start": regimes.index[start_idx],
            "end": regimes.index[-1],
            "duration_bars": len(regimes) - start_idx,
            "avg_confidence": round(conf_sum / (len(regimes) - start_idx), 3),
        })

        return periods
