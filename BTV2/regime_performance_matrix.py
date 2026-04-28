"""
regime_performance_matrix.py — Regime-Based Strategy Performance Analysis
===========================================================================

Uses the actual strategy functions with regime filtering to test each strategy
in each market regime.

Regimes: BULL_STRONG, BULL_WEAK, RANGING, BEAR_WEAK, BEAR_STRONG

Strategies:
1. Mean Reversion (daily bars) - optimized config
2. VWAP Scalping (5m bars) - optimized config  
3. Liquidation Capture (daily bars) - optimized config

Period: 2018-01-01 to 2025-12-31
Output: BTV2/results/regime_performance_matrix.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd
from regime_detector import RegimeDetector, MarketRegime
from strategies import (
    run_mean_reversion,
    run_vwap_scalping,
    run_liquidation_capture,
    compute_reference_levels,
)

# ─────────────────────────────────────────────────────────────────────────────
# DATA PATHS
# ─────────────────────────────────────────────────────────────────────────────

STORAGE_ROOT = Path("G:/Candle Data")


def load_daily_data(start: str, end: str) -> pd.DataFrame:
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1d.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= start) & (df.index < end)].copy()
    return df


def load_5m_data(start: str, end: str) -> pd.DataFrame:
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= start) & (df.index < end)].copy()
    return df


def load_1m_data(start: str, end: str) -> pd.DataFrame:
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= start) & (df.index < end)].copy()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

MR_PARAMS = {
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "bb_proximity": 0.10,
    "atr_stop": 3.0,
    "use_sfp_entry": False,
    "use_poc_tp": False,
    "use_regime_filter": True,
    "adx_max": 25.0,
    "use_trailing_stop": False,
    "trailing_atr_mult": 1.5,
}

VWAP_PARAMS = {
    "sd_threshold": 2.0,
    "atr_stop": 0.7,
    "atr_target": 2.0,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "volume_mult": 1.2,
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    "adx_max": 30,
    "rsi_max": 50,
    "entry_mode": "mean_reversion",
    "use_anchored_vwap": True,
    "use_session_filter": True,
    "tp_mode": "atr",
}

LC_PARAMS = {
    "price_threshold": 0.030,
    "volume_mult": 3.0,
    "rsi_threshold": 18.0,
    "min_consecutive_moves": 4,
    "min_wick_ratio": 1.5,
    "rrr_target": 3.0,
    "use_nearest_tp": False,
}


def compute_metrics(trades: list) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0, "net_pct": 0, "sharpe": 0}
    
    wins = sum(1 for t in trades if t > 0)
    net = sum(trades) * 100
    
    if len(trades) > 1:
        std = np.std(trades, ddof=1) if np.std(trades, ddof=1) > 0 else 1e-10
        sharpe = np.mean(trades) / std * np.sqrt(252)
    else:
        sharpe = 0
    
    return {
        "trades": len(trades),
        "win_rate": round(wins / len(trades) * 100, 1),
        "net_pct": round(net, 2),
        "sharpe": round(sharpe, 2),
    }


def main():
    print("=" * 80)
    print("REGIME PERFORMANCE MATRIX")
    print("=" * 80)
    
    # Load data
    print("\n[1] Loading data...")
    df_daily = load_daily_data("2018-01-01", "2025-12-31")
    df_5m = load_5m_data("2024-01-01", "2025-12-31")  # Use 2 years for speed
    df_1m = load_1m_data("2024-01-01", "2025-12-31")
    print(f"  Daily: {len(df_daily)}, 5m: {len(df_5m)}")
    
    # Classify regimes
    print("\n[2] Classifying regimes...")
    detector = RegimeDetector(df_daily, method="combined", timeframe="1d")
    regimes = detector.get_regimes()
    print("  Distribution:", detector.get_regime_distribution())
    
    all_regimes = [
        MarketRegime.BULL_STRONG,
        MarketRegime.BULL_WEAK,
        MarketRegime.RANGING,
        MarketRegime.BEAR_WEAK,
        MarketRegime.BEAR_STRONG,
    ]
    
    results = {
        "Mean Reversion": {r.value: {"trades": 0, "win_rate": 0, "net_pct": 0, "sharpe": 0} for r in all_regimes},
        "VWAP Scalping": {r.value: {"trades": 0, "win_rate": 0, "net_pct": 0, "sharpe": 0} for r in all_regimes},
        "Liquidation Capture": {r.value: {"trades": 0, "win_rate": 0, "net_pct": 0, "sharpe": 0} for r in all_regimes},
    }
    
    # Run each strategy in each regime
    print("\n[3] Testing strategies...")
    
    # Pre-compute for VWAP
    levels = compute_reference_levels(df_5m)
    
    for regime in all_regimes:
        reg_name = regime.value
        print(f"  {reg_name}...", end=" ", flush=True)
        
        # Mean Reversion
        _, mr_trades = run_mean_reversion(df_daily, cutoff=0.10, regime_series=regimes, allowed_regimes=[regime], **MR_PARAMS)
        results["Mean Reversion"][reg_name] = compute_metrics(mr_trades)
        
        # VWAP (only for 2024-2025 to save time)
        _, vwap_trades = run_vwap_scalping(df_5m, cutoff=0.10, regime_series=regimes, allowed_regimes=[regime], df_exit=df_1m, levels=levels, **VWAP_PARAMS)
        results["VWAP Scalping"][reg_name] = compute_metrics(vwap_trades)
        
        # Liquidation
        _, lc_trades = run_liquidation_capture(df_daily, cutoff=0.10, regime_series=regimes, allowed_regimes=[regime], **LC_PARAMS)
        results["Liquidation Capture"][reg_name] = compute_metrics(lc_trades)
        
        print(f"MR:{len(mr_trades)} VWAP:{len(vwap_trades)} LC:{len(lc_trades)}")
    
    # Build matrix
    print("\n[4] Building matrix...")
    
    regime_notes = {
        "bull_strong": "Strong uptrend",
        "bull_weak": "Weak uptrend",
        "ranging": "Range-bound",
        "bear_weak": "Weak downtrend",
        "bear_strong": "Strong downtrend",
    }
    
    rows = []
    for reg in ["bull_strong", "bull_weak", "ranging", "bear_weak", "bear_strong"]:
        mr = results["Mean Reversion"][reg]
        vs = results["VWAP Scalping"][reg]
        lc = results["Liquidation Capture"][reg]
        
        # Find best by net%
        best = None
        best_m = {"net_pct": -999}
        for name, m in [("Mean Reversion", mr), ("VWAP Scalping", vs), ("Liquidation C.", lc)]:
            if m["trades"] > 0 and m["net_pct"] > best_m["net_pct"]:
                best = name
                best_m = m
        
        rows.append({
            "Regime": reg.upper(),
            "Best Strategy": best or "N/A",
            "WR%": f"{best_m['win_rate']:.1f}%" if best_m["trades"] > 0 else "N/A",
            "Net%": f"{best_m['net_pct']:+.1f}%" if best_m["trades"] > 0 else "N/A",
            "Sharpe": f"{best_m['sharpe']:.2f}" if best_m["trades"] > 0 else "N/A",
            "Trades": best_m["trades"],
            "Notes": regime_notes[reg],
            "Trade Counts": f"MR:{mr['trades']} VWAP:{vs['trades']} LC:{lc['trades']}",
        })
    
    df_out = pd.DataFrame(rows)
    
    # Save
    print("\n[5] Saving...")
    out_path = Path("C:/Users/z_shi/Desktop/N8NPROJECTS/Bot3/BTV2/results/regime_performance_matrix.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(out_path, index=False)
    print(f"  Saved: {out_path}")
    
    # Print
    print("\n" + "=" * 80)
    print("REGIME PERFORMANCE MATRIX")
    print("=" * 80)
    print(df_out.to_string(index=False))
    
    print("\n--- DETAILED ---")
    for strat in ["Mean Reversion", "VWAP Scalping", "Liquidation Capture"]:
        print(f"\n{strat}:")
        for reg in ["bull_strong", "bull_weak", "ranging", "bear_weak", "bear_strong"]:
            m = results[strat][reg]
            print(f"  {reg:15}: {m['trades']:3} trades, WR:{m['win_rate']:5.1f}%, Net:{m['net_pct']:+7.2f}%, Sharpe:{m['sharpe']:5.2f}")
    
    print("\nDone!")


if __name__ == "__main__":
    main()