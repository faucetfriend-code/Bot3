"""
vwap_2023_2025_focused.py
==========================
Focused VWAP optimization for 2023-2025 market.
Tests specific parameter combinations based on historical insights.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    INTERVAL_BARS_PER_YEAR,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_START = "2023-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10

# Goals
MIN_WIN_RATE = 45.0
MIN_NET_PCT = -5.0

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_data() -> Tuple[pd.DataFrame, Optional[pd.DataFrame], pd.Series]:
    """Load all data needed for backtesting."""
    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    start_ts = pd.Timestamp(TEST_START, tz="UTC")
    end_ts = pd.Timestamp(TEST_END, tz="UTC")
    mask_5m = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
    df_5m = df_5m.loc[mask_5m].copy()
    
    path_1m = DATA_DIR / "BTCUSDT_1m.parquet"
    df_1m = None
    if path_1m.exists():
        df_1m_tmp = pd.read_parquet(path_1m)
        df_1m_tmp.index = pd.to_datetime(df_1m_tmp.index, utc=True)
        mask_1m = (df_1m_tmp.index >= start_ts) & (df_1m_tmp.index < end_ts)
        df_1m = df_1m_tmp.loc[mask_1m].copy()
    
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    
    return df_5m, df_1m, regime_series


def run_backtest(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    levels: pd.DataFrame,
    regime_series: pd.Series,
    sd_threshold: float,
    entry_mode: str,
    atr_stop: float,
    tp_mode: str,
    allowed_regimes: Optional[List[MarketRegime]] = None,
) -> Dict[str, Any]:
    """Run VWAP backtest with specified parameters."""
    
    params = {
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "atr_target": 2.0,
        "trailing_atr": 1.5,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,
        "use_volume_filter": True,
        "volume_mult": 1.0,
        "use_trailing_stop": True,
        "adx_max": 30.0,
        "rsi_max": 55.0,
        "entry_mode": entry_mode,
        "pullback_bars": 3,
        "use_htf_vwap": False,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "stoch_oversold": 40,
        "stoch_overbought": 60,
        "use_anchored_vwap": True,
        "use_session_filter": False,
        "require_reversal_candle": False,
        "tp_mode": tp_mode,
        "require_mss": False,
        "mss_timeout_bars": 6,
        "use_fvg_filter": False,
        "use_ob_filter": False,
        "use_eqhl_filter": False,
        "use_cvd_filter": False,
        "cvd_window": 20,
        "use_ote": False,
        "use_dynamic_mode": False,
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": allowed_regimes,
    }
    
    equity, trades = run_vwap_scalping(df, CUTOFF, df_exit=df_exit, **params)
    
    if len(equity) < 2 or len(trades) == 0:
        return {
            "trades": 0, "win_rate": 0.0, "net_pct": 0.0,
            "sharpe": 0.0, "max_dd": 0.0, "profit_factor": 0.0,
        }
    
    m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    return {
        "trades": m.get("n_trades", 0),
        "win_rate": m.get("win_rate_pct", 0.0),
        "net_pct": m.get("total_return_pct", 0.0),
        "sharpe": m.get("sharpe", 0.0),
        "max_dd": m.get("max_dd_pct", 0.0),
        "profit_factor": m.get("profit_factor", 0.0),
    }


def run_yearly(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    levels: pd.DataFrame,
    regime_series: pd.Series,
    params: Dict[str, Any],
    allowed_regimes: Optional[List[MarketRegime]] = None,
) -> Dict[str, Any]:
    """Run backtest for each year."""
    yearly = {}
    for year in [2023, 2024, 2025]:
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        
        df_y = df.loc[(df.index >= start) & (df.index < end)].copy()
        ex_y = None if df_exit is None else df_exit.loc[(df_exit.index >= start) & (df_exit.index < end)].copy()
        reg_y = regime_series.loc[(regime_series.index >= start) & (regime_series.index < end)].copy()
        
        if df_y.empty:
            yearly[year] = {"trades": 0, "net": 0.0, "wr": 0.0}
            continue
        
        levels_y = compute_reference_levels(df_y)
        m = run_backtest(df_y, ex_y, levels_y, reg_y, **params, allowed_regimes=allowed_regimes)
        yearly[year] = {"trades": m["trades"], "net": m["net_pct"], "wr": m["win_rate"]}
    
    return yearly


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    start_time = time.time()
    
    print("=" * 80)
    print("VWAP PARAMETER OPTIMIZATION - 2023-2025 RECENT MARKET")
    print("=" * 80)
    print(f"\nTest Period: {TEST_START} to {TEST_END}")
    print(f"Goals: Win Rate > {MIN_WIN_RATE}%, Net >= {MIN_NET_PCT}%")
    
    # Load data
    print("\nLoading data...")
    df_5m, df_1m, regime_series = load_data()
    print(f"  5m bars: {len(df_5m):,}")
    print(f"  1m bars: {len(df_1m) if df_1m is not None else 0:,}")
    
    # Compute levels
    levels = compute_reference_levels(df_5m)
    
    # Regime distribution
    print("\nRegime distribution (2023-2025):")
    dist = regime_series.value_counts()
    for r in MarketRegime:
        cnt = dist.get(r, 0)
        print(f"  {r.value}: {cnt:,} ({cnt/len(regime_series)*100:.1f}%)")
    
    # Based on historical analysis, test these specific configurations
    # Historical best: RANGING + mean_reversion, sd=3.0, atr=2.0
    # Also test variations
    
    test_configs = [
        # (sd, entry_mode, atr_stop, tp_mode, allowed_regimes, description)
        # Full market tests
        (3.0, "mean_reversion", 2.0, "atr", None, "MR Full"),
        (3.0, "mean_reversion", 2.5, "atr", None, "MR Full ATR2.5"),
        (3.0, "mean_reversion", 2.0, "pdh", None, "MR Full PDH"),
        (3.5, "mean_reversion", 2.0, "atr", None, "MR Full SD3.5"),
        (4.0, "mean_reversion", 2.0, "atr", None, "MR Full SD4.0"),
        (4.0, "bull_pullback", 2.0, "atr", None, "BP SD4.0"),
        (4.0, "bull_pullback", 2.5, "atr", None, "BP SD4.0 ATR2.5"),
        (4.0, "bear_pullback", 2.0, "atr", None, "BearP SD4.0"),
        (4.0, "cross", 2.0, "atr", None, "Cross SD4.0"),
        
        # RANGING only
        (3.0, "mean_reversion", 2.0, "atr", [MarketRegime.RANGING], "MR RANGING"),
        (3.0, "mean_reversion", 2.5, "atr", [MarketRegime.RANGING], "MR RANGING ATR2.5"),
        (3.5, "mean_reversion", 2.0, "atr", [MarketRegime.RANGING], "MR RANGING SD3.5"),
        (3.0, "bull_pullback", 2.0, "atr", [MarketRegime.RANGING], "BP RANGING"),
        (3.0, "bear_pullback", 2.0, "atr", [MarketRegime.RANGING], "BearP RANGING"),
        
        # BEAR_WEAK + bull_pullback (historical winner)
        (3.0, "bull_pullback", 2.0, "atr", [MarketRegime.BEAR_WEAK], "BP BEAR_WEAK"),
        (3.5, "bull_pullback", 2.0, "atr", [MarketRegime.BEAR_WEAK], "BP BEAR_WEAK SD3.5"),
        (4.0, "bull_pullback", 2.0, "atr", [MarketRegime.BEAR_WEAK], "BP BEAR_WEAK SD4.0"),
        
        # BULL_WEAK + bear_pullback
        (3.0, "bear_pullback", 2.0, "atr", [MarketRegime.BULL_WEAK], "BearP BULL_WEAK"),
        (3.5, "bear_pullback", 2.0, "atr", [MarketRegime.BULL_WEAK], "BearP BULL_WEAK SD3.5"),
        
        # RANGING + BEAR_WEAK combined
        ([MarketRegime.RANGING, MarketRegime.BEAR_WEAK], "bull_pullback", 2.0, "atr", None, "BP RANGING+BEAR_WEAK"),
        
        # Higher SD thresholds (less trades but more selective)
        (5.0, "mean_reversion", 2.0, "atr", None, "MR SD5.0"),
        (5.0, "bull_pullback", 2.0, "atr", None, "BP SD5.0"),
        (5.5, "mean_reversion", 2.0, "atr", None, "MR SD5.5"),
        (6.0, "mean_reversion", 2.0, "atr", None, "MR SD6.0"),
        
        # ATR stop variations
        (3.0, "mean_reversion", 1.5, "atr", None, "MR ATR1.5"),
        (3.0, "mean_reversion", 3.0, "atr", None, "MR ATR3.0"),
        (4.0, "bull_pullback", 3.0, "atr", None, "BP ATR3.0"),
        
        # PDH target mode
        (3.0, "mean_reversion", 2.0, "pdh", None, "MR PDH"),
        (4.0, "bull_pullback", 2.0, "pdh", None, "BP PDH"),
        (4.0, "bull_pullback", 2.5, "pdh", None, "BP PDH ATR2.5"),
    ]
    
    print(f"\nTesting {len(test_configs)} configurations...")
    print("-" * 90)
    
    all_results = []
    passing_results = []
    
    for idx, config in enumerate(test_configs):
        sd, entry, atr, tp, allowed_regimes, desc = config
        
        # Handle list of regimes
        if isinstance(sd, list):
            allowed_regimes = sd
            sd = 3.0
        
        params = {
            "sd_threshold": sd,
            "entry_mode": entry,
            "atr_stop": atr,
            "tp_mode": tp,
        }
        
        # Run full backtest
        metrics = run_backtest(df_5m, df_1m, levels, regime_series, **params, allowed_regimes=allowed_regimes)
        
        # Run yearly breakdown
        yearly = run_yearly(df_5m, df_1m, levels, regime_series, params, allowed_regimes)
        
        passes = (metrics["win_rate"] >= MIN_WIN_RATE) and (metrics["net_pct"] >= MIN_NET_PCT)
        
        result = {
            "description": desc,
            "sd_threshold": sd,
            "entry_mode": entry,
            "atr_stop": atr,
            "tp_mode": tp,
            "allowed_regimes": [r.value for r in allowed_regimes] if allowed_regimes else "ALL",
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "net_pct": metrics["net_pct"],
            "sharpe": metrics["sharpe"],
            "max_dd": metrics["max_dd"],
            "profit_factor": metrics["profit_factor"],
            "passes_goals": passes,
            "year_2023_trades": yearly[2023]["trades"],
            "year_2023_net": yearly[2023]["net"],
            "year_2023_wr": yearly[2023]["wr"],
            "year_2024_trades": yearly[2024]["trades"],
            "year_2024_net": yearly[2024]["net"],
            "year_2024_wr": yearly[2024]["wr"],
            "year_2025_trades": yearly[2025]["trades"],
            "year_2025_net": yearly[2025]["net"],
            "year_2025_wr": yearly[2025]["wr"],
        }
        all_results.append(result)
        
        if passes:
            passing_results.append(result)
        
        status = "[PASS]" if passes else ""
        print(f"  [{idx+1:2d}/{len(test_configs)}] {desc:25s} SD={sd:.1f} {entry:15s} ATR={atr:.1f} TP={tp:3s} "
              f"=> WR={metrics['win_rate']:5.1f}% Net={metrics['net_pct']:+7.1f}% Trades={metrics['trades']:4d} {status}")
    
    # Save results
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(["win_rate", "net_pct"], ascending=[False, False])
    
    output_path = RESULTS_DIR / "vwap_2023_2025_focused.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 90)
    print("OPTIMIZATION RESULTS")
    print("=" * 90)
    
    # Top performers
    print("\nTOP 10 CONFIGURATIONS (by Win Rate):")
    print("-" * 100)
    top = results_df.head(10)
    print(f"{'Config':25s} {'SD':>4} {'Entry':15s} {'ATR':>5} {'TP':>4} {'Regimes':20s} {'Trades':>6} {'WR%':>6} {'Net%':>7}")
    print("-" * 100)
    for _, row in top.iterrows():
        regimes = row['allowed_regimes'] if isinstance(row['allowed_regimes'], str) else ",".join(row['allowed_regimes'])
        print(f"{row['description']:25s} {row['sd_threshold']:>4.1f} {row['entry_mode']:15s} {row['atr_stop']:>5.1f} "
              f"{row['tp_mode']:>4s} {regimes:20s} {row['trades']:>6d} {row['win_rate']:>6.1f}% {row['net_pct']:>+7.1f}%")
    
    # Top by net
    print("\nTOP 10 CONFIGURATIONS (by Net Return):")
    print("-" * 100)
    top_net = results_df.sort_values("net_pct", ascending=False).head(10)
    print(f"{'Config':25s} {'SD':>4} {'Entry':15s} {'ATR':>5} {'TP':>4} {'Regimes':20s} {'Trades':>6} {'WR%':>6} {'Net%':>7}")
    print("-" * 100)
    for _, row in top_net.iterrows():
        regimes = row['allowed_regimes'] if isinstance(row['allowed_regimes'], str) else ",".join(row['allowed_regimes'])
        print(f"{row['description']:25s} {row['sd_threshold']:>4.1f} {row['entry_mode']:15s} {row['atr_stop']:>5.1f} "
              f"{row['tp_mode']:>4s} {regimes:20s} {row['trades']:>6d} {row['win_rate']:>6.1f}% {row['net_pct']:>+7.1f}%")
    
    # Goals validation
    print("\n" + "=" * 90)
    print("GOALS VALIDATION")
    print("=" * 90)
    print(f"\nGoals: Win Rate > {MIN_WIN_RATE}% AND Net Return >= {MIN_NET_PCT}%")
    print(f"Passing: {len(passing_results)} / {len(all_results)}")
    
    if passing_results:
        passing_df = pd.DataFrame(passing_results)
        passing_df = passing_df.sort_values(["win_rate", "net_pct"], ascending=[False, False])
        
        print("\n✓ PASSING CONFIGURATIONS:")
        print(f"\n{'Config':25s} {'SD':>4} {'Entry':15s} {'ATR':>5} {'TP':>4} {'Trades':>6} {'WR%':>6} {'Net%':>7}")
        print("-" * 90)
        for _, row in passing_df.iterrows():
            print(f"{row['description']:25s} {row['sd_threshold']:>4.1f} {row['entry_mode']:15s} {row['atr_stop']:>5.1f} "
                  f"{row['tp_mode']:>4s} {row['trades']:>6d} {row['win_rate']:>6.1f}% {row['net_pct']:>+7.1f}%")
        
        # Best recommendation
        best = passing_df.iloc[0]
        print("\n" + "=" * 90)
        print("RECOMMENDED PARAMETERS FOR 2023-2025 MARKET")
        print("=" * 90)
        print(f"""
BEST PARAMETERS FOUND:
  sd_threshold: {best['sd_threshold']}
  entry_mode:  {best['entry_mode']}
  atr_stop:    {best['atr_stop']}
  tp_mode:     {best['tp_mode']}
  allowed_regimes: {best['allowed_regimes']}

EXPECTED PERFORMANCE:
  Win Rate:      {best['win_rate']:.1f}%
  Net Return:   {best['net_pct']:+.1f}%
  Sharpe:        {best['sharpe']:.2f}
  Max Drawdown: {best['max_dd']:.1f}%
  Trade Count:  {best['trades']} trades
  Profit Factor: {best['profit_factor']:.2f}x

YEARLY BREAKDOWN:
  2023: {best['year_2023_trades']} trades, {best['year_2023_wr']:.1f}% WR, {best['year_2023_net']:+.1f}% net
  2024: {best['year_2024_trades']} trades, {best['year_2024_wr']:.1f}% WR, {best['year_2024_net']:+.1f}% net
  2025: {best['year_2025_trades']} trades, {best['year_2025_wr']:.1f}% WR, {best['year_2025_net']:+.1f}% net
""")
    else:
        print("\n✗ NO PASSING CONFIGURATIONS FOUND")
        
        # Find closest to goals
        results_df["score"] = (
            (results_df["win_rate"] / 100) * 0.5 +
            (results_df["net_pct"].clip(lower=0) / 100 + 0.5) * 0.5
        )
        closest = results_df.nlargest(5, "score")
        print("\nMost promising (closest to goals):")
        for _, row in closest.iterrows():
            print(f"  {row['description']}: WR={row['win_rate']:.1f}%, Net={row['net_pct']:+.1f}%")
    
    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed:.1f} seconds")
    print("\n" + "=" * 90)
    print("Done!")
    print("=" * 90)


if __name__ == "__main__":
    main()
