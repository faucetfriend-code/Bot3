"""
Offline training pipeline for the ML regime detectors (GMM + HMM).

Loads 4h candles per symbol via BacktestDataLoader (same loader as the
backtesting engine), builds the 6 statistical features PER SYMBOL (log
returns must never span symbol boundaries), concatenates the feature
matrices into one global training set (matching the shared detector used
at runtime), trains the requested detector(s), and persists artifacts via
ModelManager with LATEST markers.

Usage:
    python -m trading_bot_v2.ml.train_regime_model \
        --symbols BTC-USDC,ETH-USDC,SUI-USDC \
        --start 2024-01-01 --end 2025-12-31 --model both

Notes:
    - Training refuses to run below the detector minimum candle count.
    - When --model both, the GMM is trained first and the HMM last, so the
      LATEST_MODEL marker (which records the most recently trained type)
      points at the HMM artifact.
"""

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

from trading_bot_v2.backtesting.data_loader import BacktestDataLoader
from trading_bot_v2.ml.feature_engineering import FeatureExtractor
from trading_bot_v2.ml.gmm_regime import GMMConfig, GMMRegimeDetector
from trading_bot_v2.ml.hmm_regime import HMMConfig, HMMRegimeDetector

DEFAULT_DATA_DIR = "trading_bot_v2/backtesting/data"

FEATURE_NAMES = [
    "volatility",
    "returns",
    "skewness",
    "atr_ratio",
    "volume_ratio",
    "bb_width",
]


def load_symbol_features(
    symbols: List[str], start: str, end: str, data_dir: str
) -> Tuple[np.ndarray, List[int], Dict[str, Dict[str, int]]]:
    """Load 4h candles and build per-symbol feature matrices.

    Args:
        symbols: Trading symbols, e.g. ["BTC-USDC", "ETH-USDC"].
        start: Inclusive start date (YYYY-MM-DD).
        end: Inclusive end date (YYYY-MM-DD).
        data_dir: Directory containing <symbol>_4h.csv or .parquet.

    Returns:
        Tuple of (concatenated feature matrix, per-sequence lengths,
        per-symbol counts dict with candle and feature counts).
    """
    extractor = FeatureExtractor()
    matrices: List[np.ndarray] = []
    lengths: List[int] = []
    counts: Dict[str, Dict[str, int]] = {}

    for symbol in symbols:
        loader = BacktestDataLoader(symbol=symbol, data_dir=data_dir)
        candles = loader.get_candles("4h", start=start, end=end)
        n_candles = len(candles["close"])

        features = extractor.extract_batch(
            candles["close"],
            candles["high"],
            candles["low"],
            candles["volume"],
        )
        X = np.array([f.to_array() for f in features], dtype=np.float64)
        counts[symbol] = {"candles": n_candles, "features": len(X)}

        if len(X) > 0:
            matrices.append(X)
            lengths.append(len(X))

    if not matrices:
        return np.empty((0, len(FEATURE_NAMES))), [], counts

    return np.vstack(matrices), lengths, counts


def _print_cluster_table(
    title: str,
    means_raw: np.ndarray,
    label_map: Dict[int, str],
    sizes: Dict[int, int],
) -> None:
    """Print a summary table of cluster/state means with assigned labels."""
    from trading_bot_v2.ml.gmm_regime import _DEFAULT_REGIME_MAP, _LatentRegime

    print(f"\n{title}")
    header = f"  {'idx':>3} {'label':<10} {'system_regime':<18} {'samples':>8}"
    for name in FEATURE_NAMES:
        header += f" {name:>12}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for idx in range(means_raw.shape[0]):
        label = label_map.get(idx, "?")
        try:
            system = _DEFAULT_REGIME_MAP[_LatentRegime(label)].value
        except (ValueError, KeyError):
            system = "?"
        row = f"  {idx:>3} {label:<10} {system:<18} {sizes.get(idx, 0):>8}"
        for j in range(means_raw.shape[1]):
            row += f" {means_raw[idx, j]:>12.5f}"
        print(row)


def train_gmm(X: np.ndarray, save: bool = True) -> Optional[Dict[str, object]]:
    """Train and optionally persist the GMM detector on a feature matrix.

    Args:
        X: Concatenated feature matrix (raw units).
        save: Persist the artifact via ModelManager when ``True``.

    Returns:
        Training summary dict, or ``None`` on failure.
    """
    detector = GMMRegimeDetector(GMMConfig())
    summary = detector.train_on_features(X, auto_save=save)
    if summary is None:
        return None

    _print_cluster_table(
        "GMM cluster means (raw feature units):",
        summary["cluster_means_raw"],
        summary["cluster_to_latent"],
        summary["cluster_sizes"],
    )
    print(
        f"\n  converged={summary['converged']}  "
        f"avg_log_likelihood={summary['log_likelihood']:.4f}  "
        f"version={summary['version']}"
    )
    return summary


def train_hmm(
    X: np.ndarray, lengths: List[int], save: bool = True
) -> Optional[Dict[str, object]]:
    """Train and optionally persist the HMM detector on a feature matrix.

    Args:
        X: Concatenated feature matrix (raw units).
        lengths: Per-symbol sequence lengths (prevents EM from learning
            transitions across symbol boundaries).
        save: Persist the artifact via ModelManager when ``True``.

    Returns:
        Training summary dict, or ``None`` on failure.
    """
    detector = HMMRegimeDetector(HMMConfig())
    summary = detector.train_on_features(X, lengths=lengths, auto_save=save)
    if summary is None:
        return None

    _print_cluster_table(
        "HMM state means (raw feature units):",
        summary["state_means_raw"],
        summary["state_to_latent"],
        summary["state_sizes"],
    )

    transmat = np.asarray(summary["transmat"])
    labels = summary["state_to_latent"]
    print("\n  Learned transition matrix (rows = from-state):")
    head = "    " + " " * 12
    for j in range(transmat.shape[1]):
        head += f" {labels.get(j, str(j)):>10}"
    print(head)
    for i in range(transmat.shape[0]):
        row = f"    {labels.get(i, str(i)):>12}"
        for j in range(transmat.shape[1]):
            row += f" {transmat[i, j]:>10.4f}"
        print(row)
    print(
        "    self-transition (dwell) probs: "
        + ", ".join(
            f"{labels.get(i, str(i))}={transmat[i, i]:.4f}"
            for i in range(transmat.shape[0])
        )
    )
    print(
        f"\n  converged={summary['converged']}  "
        f"avg_log_likelihood={summary['log_likelihood']:.4f}  "
        f"version={summary['version']}"
    )
    return summary


def run_training(
    symbols: List[str],
    start: str,
    end: str,
    model: str = "both",
    data_dir: str = DEFAULT_DATA_DIR,
    save: bool = True,
) -> Dict[str, Optional[Dict[str, object]]]:
    """Run the full training pipeline.

    Args:
        symbols: Symbols to train on.
        start: Inclusive start date (YYYY-MM-DD).
        end: Inclusive end date (YYYY-MM-DD).
        model: "gmm", "hmm", or "both". With "both" the HMM is trained
            LAST so the LATEST_MODEL marker points at it.
        data_dir: Candle data directory.
        save: Persist artifacts when ``True``.

    Returns:
        Dict with "gmm" and/or "hmm" training summaries (``None`` for a
        model that failed or was not requested).

    Raises:
        SystemExit: When the total candle count is below the training
            minimum (guardrail against under-trained artifacts).
    """
    X, lengths, counts = load_symbol_features(symbols, start, end, data_dir)

    print("=" * 70)
    print(f"Regime model training: {', '.join(symbols)}  [{start} .. {end}]")
    print("=" * 70)
    print(f"\n  {'symbol':<12} {'4h candles':>12} {'feature rows':>14}")
    for symbol, c in counts.items():
        print(f"  {symbol:<12} {c['candles']:>12} {c['features']:>14}")
    total_candles = sum(c["candles"] for c in counts.values())
    print(f"  {'TOTAL':<12} {total_candles:>12} {len(X):>14}")

    min_candles = GMMConfig().min_train_candles
    if total_candles < min_candles:
        raise SystemExit(
            f"Refusing to train: {total_candles} total candles < minimum "
            f"{min_candles}. Widen the date range or add symbols."
        )

    results: Dict[str, Optional[Dict[str, object]]] = {}
    if model in ("gmm", "both"):
        results["gmm"] = train_gmm(X, save=save)
    if model in ("hmm", "both"):
        results["hmm"] = train_hmm(X, lengths, save=save)

    return results


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Train the ML regime detectors on historical 4h candles"
    )
    parser.add_argument(
        "--symbols",
        default="BTC-USDC,ETH-USDC,SUI-USDC",
        help="Comma-separated symbol list",
    )
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--model", choices=["gmm", "hmm", "both"], default="both")
    parser.add_argument(
        "--data-dir",
        default=os.getenv("BACKTEST_DATA_DIR", DEFAULT_DATA_DIR),
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Train without persisting artifacts (dry run)",
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    results = run_training(
        symbols=symbols,
        start=args.start,
        end=args.end,
        model=args.model,
        data_dir=args.data_dir,
        save=not args.no_save,
    )

    failed = [name for name, summary in results.items() if summary is None]
    if failed:
        print(f"\nTraining FAILED for: {', '.join(failed)}")
        sys.exit(1)
    print("\nTraining complete.")


if __name__ == "__main__":
    main()
