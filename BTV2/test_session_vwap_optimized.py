#!/usr/bin/env python3
"""
Quick session VWAP analysis summary from results.
"""

import pandas as pd
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"

def main():
    print("=" * 80)
    print("SESSION VWAP OPTIMIZED TEST - SUMMARY")
    print("=" * 80)
    
    # Load session results from test_session_vwap.py
    print("\n### SESSION COMPARISON (from prior test_session_vwap.py) ###")
    print("-" * 60)
    
    session_results = pd.read_csv(RESULTS_DIR / "session_vwap_results.csv")
    print(session_results.to_string(index=False))
    
    print("\n\n### LONDON/NY OVERLAP PARAMETER SEARCH (partial results) ###")
    print("-" * 60)
    
    # Load optimized results
    try:
        opt_results = pd.read_csv(RESULTS_DIR / "session_vwap_optimized_results.csv")
        
        # Filter to configs with trades
        with_trades = opt_results[opt_results['n_trades'] > 0].sort_values(
            'total_return_pct', ascending=False
        )
        
        print("\nConfigs with trades (sorted by return):")
        print(with_trades.to_string(index=False))
        
        print("\n\n### BEST CONFIGURATIONS ###")
        print("-" * 60)
        
        # Top by return
        if len(with_trades) > 0:
            print(f"\nBest by RETURN:")
            best = with_trades.iloc[0]
            print(f"  sd_threshold: {best['sd_threshold']}")
            print(f"  atr_stop: {best['atr_stop']}")  
            print(f"  tp_mode: {best['tp_mode']}")
            print(f"  atr_target: {best['atr_target']}")
            print(f"  entry_mode: {best['entry_mode']}")
            print(f"  require_sfp: {best['require_sfp']}")
            print(f"  ---")
            print(f"  Trades: {best['n_trades']}")
            print(f"  Win Rate: {best['win_rate']:.1f}%")
            print(f"  Return: {best['total_return_pct']:.2f}%")
            print(f"  Sharpe: {best['sharpe']:.2f}")
            print(f"  Max DD: {best['max_dd_pct']:.2f}%")
        
        # Top by win rate
        if len(with_trades) > 0:
            by_wr = with_trades.sort_values('win_rate', ascending=False).iloc[0]
            print(f"\nBest by WIN RATE:")
            print(f"  sd_threshold: {by_wr['sd_threshold']}")
            print(f"  win_rate: {by_wr['win_rate']:.1f}%")
            print(f"  trades: {by_wr['n_trades']}")
            print(f"  return: {by_wr['total_return_pct']:.2f}%")
            
    except Exception as e:
        print(f"Could not load optimized results: {e}")
    
    print("\n\n### ANALYSIS ###")
    print("-" * 60)
    
    print("""
From the session comparison (test_session_vwap.py results):
- London/NY Overlap (13:00-16:00) : 36 trades, 50.0% WR, -8.17% return (BEST)
- NY (13:00-20:00)            : 45 trades, 46.7% WR, -9.78% return
- London (08:00-16:00)        : 70 trades, 34.3% WR, -17.19% return
- Asia (00:00-08:00)          : 64 trades, 20.3% WR, -15.53% return  
- Full Day (00:00-24:00)      : 147 trades, 29.3% WR, -31.13% return

KEY FINDINGS:
1. London/NY Overlap (13:00-16:00 UTC) is the BEST session for VWAP scalping
   - Highest win rate (50.0%) 
   - Best return (-8.17% is least negative)
   - Smallest max drawdown

2. More selective SD thresholds work better:
   - sd_threshold=3.5 generated 3 trades with 100% WR
   - sd_threshold >= 4.0 generated 0 trades (too restrictive)
   
3. require_sfp=True reduces trades but improves WR:
   - With SFP: 17 trades, 58.8% WR
   - Without SFP: often more trades but lower WR

RECOMMENDED CONFIG FOR LONDON/NY OVERLAP SESSION:
- session_start_hour: 13
- session_end_hour: 16  
- sd_threshold: 3.0-3.5
- atr_stop: 1.0
- tp_mode: "atr"
- entry_mode: "bull_pullback" 
- require_sfp: True (improves WR at cost of fewer trades)
- use_volume_filter: True
- volume_mult: 1.5

NOTE: The full grid search timed out due to the large parameter space.
For better results, run a focused grid with fewer iterations.
""")

if __name__ == "__main__":
    main()