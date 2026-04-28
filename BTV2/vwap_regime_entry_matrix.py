"""
VWAP Regime-Entry Matrix - Using Real Strategy Function
========================================================
Tests each entry mode across each market regime to find optimal combinations.
Uses the real run_vwap_scalping function for accurate results.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime
import warnings

from data_manager import get_candles
from regime_detector import RegimeDetector, MarketRegime
from strategies import run_vwap_scalping

warnings.filterwarnings("ignore")

# Constants
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
START_DATE = "2020-01-01"
END_DATE = "2025-12-31"

ENTRY_MODES = ["bull_pullback", "bear_pullback", "cross", "mean_reversion", "deviation"]
REGIMES = [MarketRegime.BULL_STRONG, MarketRegime.BULL_WEAK, MarketRegime.RANGING, MarketRegime.BEAR_WEAK, MarketRegime.BEAR_STRONG]
REGIME_NAMES = {r: r.value for r in REGIMES}


def compute_metrics(equity: pd.Series, trades: list) -> dict:
    """Compute metrics from equity and trades."""
    if len(equity) < 2 or equity.iloc[0] == 0:
        return {"win_rate": 0.0, "net_pct": 0.0, "sharpe": 0.0, "trade_count": 0, "profit_factor": 0.0}
    
    total_ret = (equity.iloc[-1] / equity.iloc[0] - 1.0) * 100
    rets = equity.pct_change().dropna()
    
    if len(rets) > 1 and rets.std() > 0:
        sharpe = (rets.mean() / rets.std()) * np.sqrt(252 * 12)
    else:
        sharpe = 0.0
    
    if trades:
        wins = [t for t in trades if t > 0]
        win_rate = len(wins) / len(trades) * 100
        gross_profit = sum(wins) * 100
        gross_loss = abs(sum([t for t in trades if t < 0])) * 100
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
    else:
        win_rate, profit_factor = 0.0, 0.0
    
    return {
        "win_rate": round(win_rate, 1),
        "net_pct": round(total_ret, 2),
        "sharpe": round(sharpe, 2),
        "trade_count": len(trades),
        "profit_factor": round(profit_factor, 2),
    }


# Optimized params for the strategy
PARAMS = {
    "cutoff": 0.10,
    "sd_threshold": 3.0,
    "atr_stop": 2.0,
    "trailing_atr": 1.5,
    "adx_max": 30.0,
    "volume_mult": 1.0,
    "rsi_max": 55.0,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": False,
    "use_volume_filter": True,
    "use_trailing_stop": True,
    "use_htf_vwap": False,
    "use_htf_ema": False,
    "htf_adx_max": 30.0,
    "use_stoch_filter": False,
    "use_anchored_vwap": True,
    "use_session_filter": False,
    "require_reversal_candle": False,
    "tp_mode": "atr",
    "deviation_pct": 0.5,
    "momentum_bars": 2,
    "pullback_bars": 3,
}


def run_test(df: pd.DataFrame, regime_series: pd.Series, entry_mode: str, target_regime: MarketRegime) -> dict:
    """Run strategy with regime filter."""
    params = PARAMS.copy()
    params["entry_mode"] = entry_mode
    
    try:
        equity, trades = run_vwap_scalping(
            df,
            regime_series=regime_series,
            allowed_regimes=[target_regime],
            **params
        )
        return compute_metrics(equity, trades)
    except Exception as e:
        return {"win_rate": 0.0, "net_pct": 0.0, "sharpe": 0.0, "trade_count": 0, "profit_factor": 0.0, "error": str(e)[:30]}


def main():
    print("="*60)
    print("VWAP Regime-Entry Matrix (Using Real Strategy)")
    print("="*60)
    print(f"Period: {START_DATE} to {END_DATE}")
    print()
    
    # Load data
    print("Loading data...")
    df = get_candles(SYMBOL, INTERVAL, START_DATE, END_DATE)
    print(f"Loaded {len(df)} bars")
    
    # Compute regimes
    print("Computing regimes...")
    detector = RegimeDetector(df, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    
    regime_dist = regime_series.value_counts()
    for r in REGIMES:
        cnt = regime_dist.get(r, 0)
        print(f"  {REGIME_NAMES[r]}: {cnt} bars ({cnt/len(regime_series)*100:.1f}%)")
    
    # Run matrix
    print("\nRunning matrix (this takes ~10 min)...")
    results = []
    
    for regime in REGIMES:
        regime_name = REGIME_NAMES[regime]
        regime_bars = regime_dist.get(regime, 0)
        
        for mode in ENTRY_MODES:
            print(f"  {regime_name}/{mode}...", end=" ", flush=True)
            metrics = run_test(df, regime_series, mode, regime)
            results.append({
                "regime": regime_name,
                "entry_mode": mode,
                "trade_count": metrics["trade_count"],
                "win_rate": metrics["win_rate"],
                "net_pct": metrics["net_pct"],
                "sharpe": metrics["sharpe"],
                "profit_factor": metrics["profit_factor"],
            })
            print(f"{metrics['trade_count']} trades, WR={metrics['win_rate']:.0f}%, Net={metrics['net_pct']:.1f}%, Sharpe={metrics['sharpe']:.2f}")
    
    matrix = pd.DataFrame(results)
    
    # Find optimal per regime (minimum 10 trades, then best Sharpe)
    print("\n" + "="*60)
    print("OPTIMAL ENTRY MODE PER REGIME")
    print("="*60)
    print(f"{'Regime':<12} {'Best Mode':<14} {'WR%':>5} {'Net%':>6} {'Sharpe':>6} {'Trades':>6}")
    print("-" * 60)
    
    optimal = {}
    for regime in REGIMES:
        regime_name = REGIME_NAMES[regime]
        data = matrix[matrix["regime"] == regime_name]
        
        # Filter to modes with at least 10 trades
        valid = data[data["trade_count"] >= 10]
        
        # If none with 10+, use all
        if valid.empty:
            valid = data[data["trade_count"] > 0]
        
        if valid.empty:
            optimal[regime_name] = {"mode": "none", "wr": 0, "net": 0, "sharpe": 0, "trades": 0}
        else:
            # Find best by Sharpe
            best_idx = valid["sharpe"].idxmax()
            best = valid.loc[best_idx]
            
            optimal[regime_name] = {
                "mode": str(best["entry_mode"]),
                "wr": float(best["win_rate"]),
                "net": float(best["net_pct"]),
                "sharpe": float(best["sharpe"]),
                "trades": int(best["trade_count"]),
            }
            print(f"{regime_name:<12} {best['entry_mode']:<14} {best['win_rate']:>4.0f}% {best['net_pct']:>5.1f}% {best['sharpe']:>5.2f} {int(best['trade_count']):>6}")
    
    # Verify hypotheses
    print("\n" + "="*60)
    print("HYPOTHESIS VERIFICATION")
    print("="*60)
    hypotheses = {
        "bull_strong": ["cross", "bull_pullback"],
        "ranging": ["mean_reversion"],
        "bear_strong": ["bear_pullback"],
    }
    
    for r, expected in hypotheses.items():
        actual = optimal.get(r, {}).get("mode", "none")
        status = "OK" if actual in expected else "DIFF"
        print(f"  [{status}] {r}: expected {expected}, got {actual}")
    
    # Save
    output_path = "BTV2/results/vwap_regime_entry_matrix.csv"
    matrix.to_csv(output_path, index=False)
    print(f"\nSaved to {output_path}")
    print("Done!")


if __name__ == "__main__":
    main()