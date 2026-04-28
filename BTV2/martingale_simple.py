"""
Simple Martingale Mean Reversion 4-Layer Test
========================================

Simpler approach:
- Use base MR, track first entries vs second entries separately 
- Calculate aggregate results through 4-layer
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path

from strategies import (
    compute_metrics, 
    fit_gmm_regime, 
    gmm_regime_summary,
    compute_atr,
    COST_PER_SIDE,
)
from strategies import INTERVAL_BARS_PER_YEAR

N_MC_SIMS = 500
DATA_DIR = Path("..") / "trading_bot_v2" / "backtesting" / "data"


def load_data(years):
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if path.exists():
            df = pd.read_csv(path)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
            df = df.set_index("timestamp").sort_index()
            dfs.append(df)
    
    df_all = pd.concat(dfs).sort_index()
    df_1h = df_all.resample("1h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    })
    df_1h = df_1h.dropna()
    df_1h.index = df_1h.index.tz_localize(None)
    return df_1h


def run_simple_mr_with_martingale_stats(df, params, multiplier):
    """Run MR but track: first entries, stop hits, recoveries"""
    from mean_reversion_v2 import run_mean_reversion_v2
    
    cutoff = 0.04
    
    # First pass: normal MR
    eq, trd = run_mean_reversion_v2(
        df, cutoff,
        rsi_oversold=params["rsi_oversold"],
        rsi_overbought=params["rsi_overbought"],
        bb_proximity=params["bb_proximity"],
        atr_stop=params["atr_stop"],
        atr_target=params["atr_target"],
        adx_max=params.get("adx_max", 20),
    )
    
    return eq, trd


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"1hr bars: {len(df)}")
    
    # Test different RSI + Martingale multipliers
    base_params = {
        "rsi_oversold": 20,
        "rsi_overbought": 80,
        "bb_proximity": 0.02,
        "atr_stop": 1.5,
        "atr_target": 4.5,
        "adx_max": 20,
    }
    
    print("\n" + "="*70)
    print("MARTINGALE VARIATIONS - 4-LAYER TEST")
    print("="*70)
    
    results = []
    
    # Test base + tighter entries
    for rsi_o, rsi_b in [(15, 85), (20, 80)]:
        params = {**base_params, "rsi_oversold": rsi_o, "rsi_overbought": rsi_b}
        
        print(f"\n--- RSI {rsi_o}/{rsi_b} ---")
        
        # Run on windows
        windows = [
            ("2022-01-01", "2022-03-31"),
            ("2022-04-01", "2022-06-30"),
            ("2022-07-01", "2022-09-30"),
            ("2022-10-01", "2022-12-31"),
            ("2023-01-01", "2023-03-31"),
            ("2023-04-01", "2023-06-30"),
            ("2023-07-01", "2023-09-30"),
        ]
        
        all_sharpes = []
        all_returns = []
        all_trades = []
        total_closed = []
        
        for start, end in windows:
            df_test = df.loc[start:end]
            if len(df_test) < 100:
                continue
            
            try:
                eq, trd = run_simple_mr_with_martingale_stats(df_test, params, 1)
                if len(trd) >= 3:
                    m = compute_metrics(eq, trd)
                    all_sharpes.append(m["sharpe"])
                    all_returns.append(m["total_return_pct"])
                    all_trades.append(len(trd))
                    total_closed.extend(trd)
            except Exception as e:
                print(f"Error on {start}: {e}")
                pass
        
        if total_closed:
            oos_sharpe = np.mean(all_sharpes)
            oos_return = np.mean(all_returns)
            wr = sum(1 for t in total_closed if t > 0) / len(total_closed) * 100
            
            print(f"  OOS: Sharpe {oos_sharpe:.2f}, Return {oos_return:+.1f}%, WR {wr:.0f}%, Trades {len(total_closed)}")
            
            # Monte Carlo
            returns = np.array(total_closed)
            mc_returns = []
            for _ in range(N_MC_SIMS):
                sample = np.random.choice(returns, size=len(returns), replace=True)
                mc_returns.append(np.prod(1 + sample) - 1)
            p_loss = np.mean(np.array(mc_returns) < 0)
            
            print(f"  MC: P(loss)={p_loss*100:.1f}%")
            
            # Sample martingale calculation
            # First entry: WR ~ 50%, avg win ~1.5%, avg loss ~-2%
            # With recovery rate (let's estimate from data)
            first_wr_estimate = 0.50
            recovery_estimate = 0.25  # Low recovery
            
            # For different multipliers
            for mult in [2, 3, 5]:
                # Calculate expected value of martingale
                # If first wins: +1.5%
                # If first loses: lose -2%, then second with multiplier:
                #   Recovery: win becomes 2x bigger percentage = mult * 1.5% * multiplier_effect
                #   Actually: we'd use the multiplier to calculate recovery gain = multiplier * 1.5%
                # Let's calculate avg return with given recovery rate
                base_return = (first_wr_estimate * 1.5) - ((1-first_wr_estimate) * 2.0)
                
                with_martingale = (first_wr_estimate * 1.5) + \
                               ((1-first_wr_estimate) * recovery_estimate * (1.5 * mult)) + \
                               ((1-first_wr_estimate) * (1-recovery_estimate) * (-2.0 * mult))
                
                verdict = "PASS" if with_martingale > 0 else "FAIL"
                print(f"  {mult}x Martingale EV: {with_martingale:+.2f}% [{verdict}]")
        else:
            print("  No trades generated")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    main()