"""
VWAP Regime-Entry Parameter Tuning
================================
Based on prior project parameter sweeps, create tuned parameter config.

Combos with documented success:
1. RANGING + mean_reversion (documented winner)
2. BEAR_WEAK + bull_pullback
3. BULL_WEAK + bull_pullback
4. BULL_STRONG + cross
5. BEAR_STRONG + cross
"""

import pandas as pd
from data_manager import get_candles
from regime_detector import RegimeDetector, MarketRegime
from strategies import run_vwap_scalping


SYMBOL = "BTCUSDT"
INTERVAL = "5m"
START_DATE = "2020-01-01"
END_DATE = "2025-12-31"


WINNING_COMBOS = [
    ("RANGING", "mean_reversion"),
    ("BEAR_WEAK", "bull_pullback"),
    ("BULL_WEAK", "bull_pullback"),
    ("BULL_STRONG", "cross"),
    ("BEAR_STRONG", "cross"),
]


# Parameter grids per combo (derived from prior sweeps)
PARAM_GRIDS = {
    ("RANGING", "mean_reversion"): {
        "sd_threshold": [2.5, 3.0, 3.5, 4.0],
        "atr_stop": [1.0, 1.5, 2.0, 2.5],
        "trailing_atr": [1.0, 1.5, 2.0],
    },
    ("BEAR_WEAK", "bull_pullback"): {
        "sd_threshold": [2.5, 3.0, 3.5],
        "atr_stop": [1.0, 1.5, 2.0],
        "trailing_atr": [1.0, 1.5, 2.0],
    },
    ("BULL_WEAK", "bull_pullback"): {
        "sd_threshold": [2.5, 3.0, 3.5],
        "atr_stop": [1.0, 1.5, 2.0],
        "trailing_atr": [1.0, 1.5, 2.0],
    },
    ("BULL_STRONG", "cross"): {
        "sd_threshold": [2.5, 3.0, 3.5],
        "atr_stop": [1.0, 1.5, 2.0],
        "trailing_atr": [1.0, 1.5, 2.0],
    },
    ("BEAR_STRONG", "cross"): {
        "sd_threshold": [2.5, 3.0, 3.5],
        "atr_stop": [1.0, 1.5, 2.0],
        "trailing_atr": [1.0, 1.5, 2.0],
    },
}


BASE = {
    "cutoff": 0.10,
    "volume_mult": 1.0,
    "adx_max": 30.0, "rsi_max": 55.0,
    "ema_fast": 9, "ema_slow": 20,
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


def regime_str_to_enum(regime_str: str) -> MarketRegime:
    return {"BULL_STRONG": MarketRegime.BULL_STRONG,
            "BULL_WEAK": MarketRegime.BULL_WEAK,
            "RANGING": MarketRegime.RANGING,
            "BEAR_WEAK": MarketRegime.BEAR_WEAK,
            "BEAR_STRONG": MarketRegime.BEAR_STRONG}[regime_str.upper()]


def run_combo(df, regime_series, regime_name, entry_mode, sd_vals, atr_vals, tr_vals):
    """Run parameter sweep for combo."""
    target = regime_str_to_enum(regime_name)
    results = []
    total = len(sd_vals) * len(atr_vals) * len(tr_vals)
    idx = 0
    
    for sd in sd_vals:
        for atr in atr_vals:
            for tr in tr_vals:
                idx += 1
                params = BASE.copy()
                params.update({
                    "entry_mode": entry_mode,
                    "sd_threshold": sd,
                    "atr_stop": atr,
                    "trailing_atr": tr,
                })
                
                try:
                    equity, trades = run_vwap_scalping(
                        df,
                        regime_series=regime_series,
                        allowed_regimes=[target],
                        **params
                    )
                    
                    if len(trades) > 10:
                        wins = [t for t in trades if t > 0]
                        wr = len(wins) / len(trades) * 100
                        ret = (equity.iloc[-1] / equity.iloc[0] - 1.0) * 100
                    else:
                        wr, ret = 0, 0
                        trades = []
                    
                    results.append({
                        "regime": regime_name,
                        "entry_mode": entry_mode,
                        "sd_threshold": sd,
                        "atr_stop": atr,
                        "trailing_atr": tr,
                        "trade_count": len(trades),
                        "win_rate": wr,
                        "net_pct": ret,
                    })
                    pct = idx / total * 100
                    print(f"  [{pct:5.1f}%] sd={sd}, atr={atr}, tr={tr} => {len(trades)}t, WR={wr:.0f}%, Net={ret:+.1f}%")
                except Exception as e:
                    results.append({
                        "regime": regime_name, "entry_mode": entry_mode,
                        "sd_threshold": sd, "atr_stop": atr, "trailing_atr": tr,
                        "trade_count": 0, "win_rate": 0, "net_pct": 0,
                    })
    return results


def find_best(results):
    """Find best by net profit."""
    valid = [r for r in results if r["trade_count"] >= 20]
    if not valid:
        valid = [r for r in results if r["trade_count"] > 5]
    if not valid:
        return {"sd_threshold": 3.0, "atr_stop": 1.5, "trailing_atr": 1.5,
                "expected_wr": "N/A", "expected_net": "N/A", "expected_sharpe": 0}
    valid.sort(key=lambda x: (x["net_pct"], x["win_rate"]), reverse=True)
    best = valid[0]
    return {"sd_threshold": best["sd_threshold"],
            "atr_stop": best["atr_stop"],
            "trailing_atr": best["trailing_atr"],
            "expected_wr": f"{best['win_rate']:.0f}%",
            "expected_net": f"{best['net_pct']:+.1f}%",
            "expected_sharpe": 0}


def main():
    print("="*60)
    print("VWAP Regime-Entry Parameter Tuning")
    print("="*60)
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"Combos: {len(WINNING_COMBOS)}")
    print()
    
    df = get_candles(SYMBOL, INTERVAL, START_DATE, END_DATE)
    print(f"Loaded {len(df)} bars")
    
    detector = RegimeDetector(df, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    print("Regimes computed.")
    print()
    
    all_results = []
    best_params = {}
    
    for regime, entry in WINNING_COMBOS:
        print(f"\n{'='*50}")
        print(f"Tuning: {regime} + {entry}")
        print(f"{'='*50}")
        
        grid = PARAM_GRIDS[(regime, entry)]
        results = run_combo(
            df, regime_series, regime, entry,
            grid["sd_threshold"], grid["atr_stop"], grid["trailing_atr"]
        )
        all_results.extend(results)
        
        best = find_best(results)
        best_params[f"{regime}_{entry}"] = best
        
        print(f"\nBest for {regime}/{entry}:")
        print(f"  sd_threshold: {best.get('sd_threshold')}")
        print(f"  atr_stop: {best.get('atr_stop')}")
        print(f"  trailing_atr: {best.get('trailing_atr')}")
        print(f"  Expected WR: {best.get('expected_wr')}")
        print(f"  Expected Net: {best.get('expected_net')}")
    
    # Save results
    pd.DataFrame(all_results).to_csv("results/vwap_regime_tuning_full.csv", index=False)
    
    # Save tuned params
    tuned = []
    for key, p in best_params.items():
        tuned.append({"combination": key, **p})
    pd.DataFrame(tuned).to_csv("results/vwap_regime_tuned_params.csv", index=False)
    
    print("\n" + "="*60)
    print("TUNED PARAMETER SUMMARY")
    print("="*60)
    for key, p in best_params.items():
        combo = key.replace("_", " + ")
        print(f"\n{combo}:")
        print(f"  sd_threshold: {p.get('sd_threshold')}")
        print(f"  atr_stop: {p.get('atr_stop')}")
        print(f"  trailing_atr: {p.get('trailing_atr')}")
        print(f"  tp_mode: atr")
        print(f"  volume_mult: 1.0")
        print(f"  expected_wr: {p.get('expected_wr')}")
        print(f"  expected_net: {p.get('expected_net')}")
    
    print("\n\nSaved to: results/vwap_regime_tuned_params.csv")
    return best_params


if __name__ == "__main__":
    main()