"""
Offline ADX-vs-ML regime shadow comparison CLI.

Replays historical 4h candles bar-by-bar through BOTH the authoritative
ADX detector (with hysteresis, confirmation, and dwell, exactly as live)
and a trained ML detector (HMM forward-filtered by default, or GMM), then
prints:

- overall agreement pct
- per-regime confusion summary (ADX regime x ML regime counts)
- dwell statistics (median/mean segment hours, flips per week) for the
  ADX and ML series side by side

This is the promotion-decision tool for the USE_ML_REGIME flag: promotion
requires ML dwell stability at least matching ADX and a sensible
agreement pattern.

Pure analysis - no database writes and no event emission.

Usage:
    python -m trading_bot_v2.analysis.regime_shadow_report \
        --symbol SUI-USDC --start 2024-01-01 --end 2024-12-31 --model hmm
"""

import argparse
import os
import statistics
from typing import Any, Dict, List, Optional

import numpy as np

from trading_bot_v2.analysis.regime_stability import (
    MIN_BARS,
    WINDOW_BARS,
    _parse_timestamp,
)
from trading_bot_v2.backtesting.data_loader import BacktestDataLoader
from trading_bot_v2.market_regime import MarketRegime, MarketRegimeDetector
from trading_bot_v2.ml.feature_engineering import MIN_CANDLES, FeatureExtractor
from trading_bot_v2.ml.gmm_regime import GMMRegimeDetector, _LatentRegime
from trading_bot_v2.ml.hmm_regime import HMMRegimeDetector
from trading_bot_v2.ml.model_manager import read_latest_model_type


def _load_ml_detector(model: Optional[str]):
    """Load the requested (or latest) trained ML detector.

    Args:
        model: "hmm", "gmm", or ``None`` to use the LATEST_MODEL marker
            (falling back to hmm).

    Returns:
        Tuple of (detector, model_type).

    Raises:
        SystemExit: When no trained artifact is available.
    """
    if model is None:
        model = read_latest_model_type() or "hmm"

    detector = HMMRegimeDetector() if model == "hmm" else GMMRegimeDetector()
    if not detector.ensure_model_loaded():
        raise SystemExit(
            f"No trained {model} artifact found. Train first: "
            f"python -m trading_bot_v2.ml.train_regime_model"
        )
    return detector, model


def _dwell_stats(regimes: List[str], hours_per_bar: float = 4.0) -> Dict[str, Any]:
    """Compute dwell segments, medians, and flips for a regime series.

    Args:
        regimes: Per-bar regime values.
        hours_per_bar: Bar duration in hours (4h candles by default).

    Returns:
        Dict with segment count, median/mean segment hours, flip count,
        and flips per week.
    """
    if not regimes:
        return {
            "segments": 0,
            "median_h": 0.0,
            "mean_h": 0.0,
            "flips": 0,
            "flips_per_week": 0.0,
        }

    segment_bars: List[int] = []
    run = 1
    for i in range(1, len(regimes)):
        if regimes[i] == regimes[i - 1]:
            run += 1
        else:
            segment_bars.append(run)
            run = 1
    segment_bars.append(run)

    segment_hours = [bars * hours_per_bar for bars in segment_bars]
    total_weeks = len(regimes) * hours_per_bar / (24.0 * 7.0)
    flips = len(segment_bars) - 1

    return {
        "segments": len(segment_bars),
        "median_h": statistics.median(segment_hours),
        "mean_h": statistics.fmean(segment_hours),
        "flips": flips,
        "flips_per_week": flips / total_weeks if total_weeks > 0 else 0.0,
    }


def run_shadow_report(
    symbol: str,
    start: str,
    end: str,
    data_dir: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay a window through both detectors and collect comparison stats.

    Features for the ML detector are precomputed once over the full series
    (identical values to per-bar extraction, since each feature only looks
    backwards) and the filter/classifier runs per bar on the prefix.

    Args:
        symbol: Trading symbol, e.g. "SUI-USDC".
        start: Inclusive start date (YYYY-MM-DD).
        end: Inclusive end date (YYYY-MM-DD).
        data_dir: Directory containing <symbol>_4h.csv or .parquet.
        model: "hmm", "gmm", or ``None`` for the latest trained type.

    Returns:
        Dict with agreement, confusion, and dwell statistics.
    """
    ml_detector, model_type = _load_ml_detector(model)

    loader = BacktestDataLoader(symbol=symbol, data_dir=data_dir)
    candles = loader.get_candles("4h", start=start, end=end)
    closes = candles["close"]
    n = len(closes)
    if n < MIN_CANDLES + 1:
        raise SystemExit(
            f"Not enough 4h candles for {symbol} in [{start}, {end}]: "
            f"got {n}, need at least {MIN_CANDLES + 1}"
        )

    timestamps = [_parse_timestamp(t) for t in candles["timestamp"]]

    # Precompute ML features once for the whole series. Feature row k
    # corresponds to candle index k + MIN_CANDLES - 1.
    extractor = FeatureExtractor()
    features = extractor.extract_batch(
        closes, candles["high"], candles["low"], candles["volume"]
    )
    feature_matrix = np.array([f.to_array() for f in features], dtype=np.float64)

    adx_detector = MarketRegimeDetector()  # no event bus / db: pure analysis
    window_frames = getattr(ml_detector.config, "window_frames", 64)

    adx_series: List[str] = []
    ml_series: List[str] = []
    confusion: Dict[str, Dict[str, int]] = {}
    agree_count = 0
    ml_confidences: List[float] = []

    # Start where both detectors have enough history: the ML features
    # need MIN_CANDLES (115) bars, the ADX detector only MIN_BARS (29).
    start_idx = max(MIN_BARS, MIN_CANDLES - 1)

    for i in range(start_idx, n):
        lo = max(0, i + 1 - WINDOW_BARS)
        window = {
            "high": candles["high"][lo : i + 1],
            "low": candles["low"][lo : i + 1],
            "close": closes[lo : i + 1],
            "volume": candles["volume"][lo : i + 1],
        }
        bar_time = timestamps[i]
        adx_detector._clock = lambda t=bar_time: t
        adx_regime = adx_detector.detect_regime_cached(symbol, window)

        # ML classification from the precomputed feature prefix
        feat_idx = i - (MIN_CANDLES - 1)
        frame_lo = max(0, feat_idx + 1 - window_frames)
        ml_result = ml_detector.predict_from_features(
            feature_matrix[frame_lo : feat_idx + 1]
        )
        ml_regime = ml_result.system_regime

        # Same ADX>30 TRENDING_STRONG refinement as the live predict path,
        # reusing the ADX the authoritative detector just computed.
        adx_value = adx_detector._last_calculated_adx
        if (
            adx_value is not None
            and adx_value > 30
            and ml_result.latent_regime == _LatentRegime.TRENDING
        ):
            ml_regime = MarketRegime.TRENDING_STRONG

        adx_series.append(adx_regime.value)
        ml_series.append(ml_regime.value)
        ml_confidences.append(float(ml_result.confidence))

        row = confusion.setdefault(adx_regime.value, {})
        row[ml_regime.value] = row.get(ml_regime.value, 0) + 1
        if adx_regime.value == ml_regime.value:
            agree_count += 1

    bars = len(adx_series)
    return {
        "symbol": symbol,
        "start": start,
        "end": end,
        "model_type": model_type,
        "bars": bars,
        "agreement_pct": 100.0 * agree_count / bars if bars else 0.0,
        "mean_ml_confidence": (
            statistics.fmean(ml_confidences) if ml_confidences else 0.0
        ),
        "confusion": confusion,
        "adx_dwell": _dwell_stats(adx_series),
        "ml_dwell": _dwell_stats(ml_series),
    }


def print_report(result: Dict[str, Any]) -> None:
    """Print a human-readable ADX-vs-ML shadow comparison report."""
    print("=" * 70)
    print(
        f"Regime shadow report: {result['symbol']} "
        f"{result['start']} -> {result['end']} "
        f"({result['bars']} x 4h bars, ML model: {result['model_type']})"
    )
    print("=" * 70)

    print(
        f"\nAgreement: {result['agreement_pct']:.1f}% "
        f"(mean ML confidence {result['mean_ml_confidence']:.3f})"
    )

    # Confusion summary
    ml_labels = sorted({ml for row in result["confusion"].values() for ml in row})
    print("\nConfusion (rows = ADX regime, cols = ML regime):")
    header = f"  {'ADX v ML':<20}"
    for label in ml_labels:
        header += f" {label:>18}"
    print(header)
    for adx_regime in sorted(result["confusion"]):
        row = result["confusion"][adx_regime]
        line = f"  {adx_regime:<20}"
        for label in ml_labels:
            line += f" {row.get(label, 0):>18}"
        print(line)

    # Dwell comparison
    print("\nDwell comparison (ADX vs ML):")
    print(f"  {'metric':<22} {'ADX':>12} {'ML':>12}")
    adx_d, ml_d = result["adx_dwell"], result["ml_dwell"]
    for key, label in [
        ("segments", "segments"),
        ("median_h", "median dwell (h)"),
        ("mean_h", "mean dwell (h)"),
        ("flips", "flips"),
        ("flips_per_week", "flips per week"),
    ]:
        a, m = adx_d[key], ml_d[key]
        if isinstance(a, float):
            print(f"  {label:<22} {a:>12.2f} {m:>12.2f}")
        else:
            print(f"  {label:<22} {a:>12} {m:>12}")
    print()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Offline ADX-vs-ML regime shadow comparison (4h candles)"
    )
    parser.add_argument("--symbol", default="SUI-USDC")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument(
        "--model",
        choices=["gmm", "hmm"],
        default=None,
        help="ML model to compare (default: latest trained)",
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("BACKTEST_DATA_DIR", "trading_bot_v2/backtesting/data"),
    )
    args = parser.parse_args()

    result = run_shadow_report(
        args.symbol, args.start, args.end, args.data_dir, args.model
    )
    print_report(result)


if __name__ == "__main__":
    main()
