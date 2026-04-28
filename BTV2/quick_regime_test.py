"""
quick_regime_test.py - Fast regime-specific parameter testing
=============================================================
Quick focused test to find best params per regime type.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    INTERVAL_BARS_PER_YEAR,
)


DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
TEST_START = "2023-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10


def load_data():
    path = DATA_DIR / "BTCUSDT_5m.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    mask = (df.index >= TEST_START) & (df.index < TEST_END)
    return df.loc[mask].copy()


def compute_metrics(equity, trades):
    if len(equity) < 2 or len(trades) == 0:
        return {"win_rate": 0.0, "net_pct": 0.0, "sharpe": 0.0, "trades": 0}
    
    import math
    bars = INTERVAL_BARS_PER_YEAR.get("5m", 105120)
    total_ret = (float(equity.iloc[-1]) / float(equity.iloc[0]) - 1) * 100
    
    wins = [t for t in trades if t > 0]
    wr = len(wins) / max(len(trades), 1) * 100
    
    return {
        "win_rate": round(wr, 1),
        "net_pct": round(total_ret, 1),
        "sharpe": round(total_ret / max(len(trades), 1) * 10, 2),
        "trades": len(trades),
    }


def run_test(df, regime_series, entry_mode, sd, atr, tp, trailing, regimes):
    df_c = df.copy()
    df_c.index = df_c.index.tz_localize(None)
    reg = regime_series.copy()
    reg.index = reg.index.tz_localize(None)
    
    levels = compute_reference_levels(df_c)
    
    params = {
        "atr_target": 2.0,
        "ema_fast": 9, "ema_slow": 20,
        "use_trend_filter": True, "use_volume_filter": True,
        "volume_mult": 1.2, "use_trailing_stop": True,
        "adx_max": 25.0, "rsi_max": 50.0,
        "pullback_bars": 3,
        "use_htf_vwap": False, "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "stoch_oversold": 45, "stoch_overbought": 55,
        "use_anchored_vwap": True, "use_session_filter": True,
        "require_reversal_candle": False, "require_mss": False,
        "use_fvg_filter": False, "use_ob_filter": False,
        "use_eqhl_filter": False, "use_cvd_filter": False,
        "use_dynamic_mode": False,
        "sd_threshold": sd, "atr_stop": atr,
        "tp_mode": tp, "trailing_atr": trailing,
        "entry_mode": entry_mode, "levels": levels,
        "regime_series": reg, "allowed_regimes": regimes,
    }
    
    equity, trades = run_vwap_scalping(df_c, CUTOFF, **params)
    return compute_metrics(equity, trades)


def main():
    print("Loading data...")
    df = load_data()
    print(f"Loaded {len(df):,} bars")
    
    print("Computing regimes...")
    detector = RegimeDetector(df, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    
    # Regime definitions
    REGIMES = {
        "RANGING": [MarketRegime.RANGING],
        "BEAR": [MarketRegime.BEAR_WEAK, MarketRegime.BEAR_STRONG],
        "BULL": [MarketRegime.BULL_WEAK, MarketRegime.BULL_STRONG],
        "ALL": list(MarketRegime),
    }
    
    # Test configs: (entry_mode, sd_range, atr_range, tp_range, trailing_range)
    # Based on theory: mean_reversion best for RANGING, cross for TRENDING
    CONFIGS = [
        # Config: (description, entry_mode, sd_values, atr_values, tp_values, trailing_values)
        ("MR_Loose", "mean_reversion", [2.5, 3.0], [1.5, 2.0, 2.5], ["atr", "pdh"], [1.2, 1.5]),
        ("MR_Tight", "mean_reversion", [3.5, 4.0], [1.0, 1.5], ["atr", "pdh"], [1.0, 1.2]),
        ("Cross_Loose", "cross", [2.5, 3.0], [1.5, 2.0, 2.5], ["atr", "pdh"], [1.2, 1.5]),
        ("Cross_Tight", "cross", [3.5, 4.0], [1.0, 1.5], ["atr", "pdh"], [1.0, 1.2]),
    ]
    
    results = []
    
    for config_name, mode, sds, atrs, tps, trailings in CONFIGS:
        print(f"\n{'='*60}")
        print(f"Testing: {config_name} ({mode})")
        print(f"{'='*60}")
        
        for regime_name, regimes in REGIMES.items():
            print(f"\n  [{regime_name}]")
            
            best = None
            best_wr = 0
            
            for sd in sds:
                for atr in atrs:
                    for tp in tps:
                        for trl in trailings:
                            m = run_test(df, regime_series, mode, sd, atr, tp, trl, regimes)
                            
                            result = {
                                "config": config_name,
                                "entry_mode": mode,
                                "regime": regime_name,
                                "sd": sd,
                                "atr": atr,
                                "tp": tp,
                                "trailing": trl,
                                "wr": m["win_rate"],
                                "net": m["net_pct"],
                                "trades": m["trades"],
                                "sharpe": m["sharpe"],
                            }
                            results.append(result)
                            
                            if m["win_rate"] > best_wr:
                                best_wr = m["win_rate"]
                                best = result
            
            if best:
                print(f"    Best: SD={best['sd']}, ATR={best['atr']}, TP={best['tp']}, Trl={best['trailing']}")
                print(f"    WR={best['wr']:.1f}%, Net={best['net']:+.1f}%, Trades={best['trades']}")
    
    # Save and analyze
    df_results = pd.DataFrame(results)
    df_results.to_csv(RESULTS_DIR / "quick_regime_test.csv", index=False)
    
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    
    # Best by regime
    for regime_name in REGIMES.keys():
        reg_df = df_results[df_results["regime"] == regime_name]
        if len(reg_df) == 0:
            continue
        
        # Qualified (WR >= 45)
        qual = reg_df[reg_df["wr"] >= 45]
        
        print(f"\n[{regime_name}]")
        if len(qual) > 0:
            best = qual.sort_values("wr", ascending=False).iloc[0]
            print(f"  Qualified (WR >= 45%): {len(qual)} combos")
            print(f"  Best: {best['config']} SD={best['sd']}, ATR={best['atr']}, TP={best['tp']}, Trl={best['trailing']}")
            print(f"  WR={best['wr']:.1f}%, Net={best['net']:+.1f}%, Trades={best['trades']}")
        else:
            best = reg_df.sort_values("wr", ascending=False).iloc[0]
            print(f"  No WR >= 45%")
            print(f"  Best: {best['config']} SD={best['sd']}, ATR={best['atr']}")
            print(f"  WR={best['wr']:.1f}%, Net={best['net']:+.1f}%, Trades={best['trades']}")
    
    # Best by entry mode
    print("\n" + "=" * 80)
    print("BEST BY ENTRY MODE (All Regimes)")
    print("=" * 80)
    
    for mode in ["mean_reversion", "cross"]:
        mode_df = df_results[df_results["entry_mode"] == mode]
        if len(mode_df) == 0:
            continue
        
        qual = mode_df[mode_df["wr"] >= 45]
        
        print(f"\n[{mode.upper()}]")
        if len(qual) > 0:
            best = qual.sort_values("wr", ascending=False).iloc[0]
            print(f"  Qualified: {len(qual)} combos")
            print(f"  Best: Regime={best['regime']}, SD={best['sd']}, ATR={best['atr']}, TP={best['tp']}, Trl={best['trailing']}")
            print(f"  WR={best['wr']:.1f}%, Net={best['net']:+.1f}%")
        else:
            best = mode_df.sort_values("wr", ascending=False).iloc[0]
            print(f"  No WR >= 45%")
            print(f"  Best: Regime={best['regime']}, SD={best['sd']}, ATR={best['atr']}")
            print(f"  WR={best['wr']:.1f}%, Net={best['net']:+.1f}%")
    
    print(f"\nResults saved to: {RESULTS_DIR / 'quick_regime_test.csv'}")


if __name__ == "__main__":
    main()
