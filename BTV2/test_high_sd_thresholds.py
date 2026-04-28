"""
test_high_sd_thresholds.py
==========================
Test HIGH SD thresholds to find sweet spot: WR > 50% with reasonable trades.

Test:
- sd_threshold: [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]
- Keep entry_mode=bull_pullback, tp_mode=pdh, atr_stop=1.87
- Period: 2023-2025

Hypothesis:
- Higher SD = higher selectivity = higher WR
- But fewer trades
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    COST_PER_SIDE,
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

# ─── FIXED PARAMS (from user request) ───
ENTRY_MODE = "bull_pullback"
TP_MODE = "pdh"
ATR_STOP = 2.0

# ─── TEST RANGE: Higher SD thresholds ───
SD_THRESHOLDS = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]

# Goals
MIN_WIN_RATE = 50.0  # %
MIN_TRADES = 50  # Reasonable sample size


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_5m_data(start: str, end: str) -> pd.DataFrame:
    """Load BTCUSDT 5m data for test period."""
    path = DATA_DIR / "BTCUSDT_5m.parquet"
    if not path.exists():
        print(f"ERROR: No data found at {path}")
        return pd.DataFrame()
    
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


def load_1m_data(start: str, end: str) -> pd.DataFrame | None:
    """Load BTCUSDT 1m data for exits."""
    path = DATA_DIR / "BTCUSDT_1m.parquet"
    if not path.exists():
        return None
    
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    df_exit: pd.DataFrame | None,
    regime_series: pd.Series,
    sd_threshold: float,
    entry_mode: str,
    atr_stop: float,
    tp_mode: str,
    allowed_regimes: List[MarketRegime] | None = None,
) -> Dict[str, Any]:
    """Run VWAP backtest with specified parameters."""
    
    levels = compute_reference_levels(df)
    
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
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    # Compute metrics
    if len(equity) < 2 or len(trades) == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_pct": 0.0,
        }
    
    m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    return {
        "trades": m.get("n_trades", 0),
        "win_rate": m.get("win_rate_pct", 0.0),
        "profit_factor": m.get("profit_factor", 0.0),
        "net_pct": m.get("total_return_pct", 0.0),
        "sharpe": m.get("sharpe", 0.0),
        "max_drawdown_pct": m.get("max_dd_pct", 0.0),
        "avg_trade_pct": m.get("avg_trade_pct", 0.0),
    }


def run_yearly_backtest(
    df: pd.DataFrame,
    df_exit: pd.DataFrame | None,
    regime_series: pd.Series,
    params: Dict[str, Any],
    years: List[int],
) -> List[Dict[str, Any]]:
    """Run backtest for each year separately."""
    results = []
    
    for year in years:
        year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        
        df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
        df_exit_year = None
        if df_exit is not None:
            df_exit_year = df_exit.loc[(df_exit.index >= year_start) & (df_exit.index < year_end)].copy()
        
        regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
        
        if df_year.empty or regime_year.empty:
            results.append({
                "year": year,
                "trades": 0,
                "win_rate": 0.0,
                "net_pct": 0.0,
            })
            continue
        
        # Run backtest for this year
        metrics = run_backtest(
            df_year,
            df_exit_year,
            regime_year,
            params["sd_threshold"],
            params["entry_mode"],
            params["atr_stop"],
            params["tp_mode"],
            allowed_regimes=params.get("allowed_regimes"),
        )
        
        results.append({
            "year": year,
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "net_pct": metrics["net_pct"],
            "sharpe": metrics["sharpe"],
            "max_dd": metrics["max_drawdown_pct"],
        })
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("HIGH SD THRESHOLD TEST - Finding WR > 50% Sweet Spot")
    print("=" * 80)
    print(f"\nFixed params: entry_mode={ENTRY_MODE}, tp_mode={TP_MODE}, atr_stop={ATR_STOP}")
    print(f"Testing SD thresholds: {SD_THRESHOLDS}")
    print(f"Period: {TEST_START} to {TEST_END}")
    print()
    
    # Load data
    print("Loading 5m data...")
    df = load_5m_data(TEST_START, TEST_END)
    if df.empty:
        print("ERROR: No data loaded!")
        return
    
    print(f"  Loaded {len(df):,} bars from {df.index[0]} to {df.index[-1]}")
    
    # Load 1m for exits
    print("Loading 1m exit data...")
    df_exit = load_1m_data(TEST_START, TEST_END)
    if df_exit is not None:
        print(f"  Loaded {len(df_exit):,} bars for exit tracking")
    
    # Detect regimes
    print("Detecting market regimes...")
    detector = RegimeDetector(df, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    regime_dist = regime_series.value_counts()
    print("Regime distribution (2023-2025):")
    for r in MarketRegime:
        cnt = regime_dist.get(r, 0)
        print(f"  {r.value}: {cnt:,} bars ({cnt/len(regime_series)*100:.1f}%)")
    print(f"  Regime distribution:\n{regime_series.value_counts().to_string()}")
    print()
    
    # ─── RUN TEST FOR EACH SD THRESHOLD ───
    results = []
    
    print("-" * 80)
    print(f"{'SD':>5} {'Trades':>7} {'WR%':>6} {'Net%':>7} {'PF':>5} {'Sharpe':>7} {'MaxDD%':>7}")
    print("-" * 80)
    
    for sd in SD_THRESHOLDS:
        params = {
            "sd_threshold": sd,
            "entry_mode": ENTRY_MODE,
            "atr_stop": ATR_STOP,
            "tp_mode": TP_MODE,
        }
        
        # Run full period backtest
        metrics = run_backtest(
            df,
            df_exit,
            regime_series,
            sd_threshold=sd,
            entry_mode=ENTRY_MODE,
            atr_stop=ATR_STOP,
            tp_mode=TP_MODE,
        )
        
        result = {
            "sd_threshold": sd,
            **metrics,
        }
        results.append(result)
        
        # Print row
        flag = ""
        if metrics["win_rate"] >= MIN_WIN_RATE and metrics["trades"] >= MIN_TRADES:
            flag = " *"
        
        print(
            f"{sd:>5.1f} "
            f"{metrics['trades']:>7} "
            f"{metrics['win_rate']:>6.1f}% "
            f"{metrics['net_pct']:>+7.2f}% "
            f"{metrics['profit_factor']:>5.2f} "
            f"{metrics['sharpe']:>+7.2f} "
            f"{metrics['max_drawdown_pct']:>7.2f}%{flag}"
        )
    
    print("-" * 80)
    print()
    
    # ─── YEARLY BREAKDOWN FOR TOP CONFIGS ───
    print("\n" + "=" * 80)
    print("YEARLY BREAKDOWN - Top 3 by Win Rate (min 30 trades/year)")
    print("=" * 80)
    
    # Sort by win rate
    results_sorted = sorted(results, key=lambda x: x["win_rate"], reverse=True)
    top3 = [r for r in results_sorted if r["trades"] >= 30][:3]
    
    years = [2023, 2024, 2025]
    
    for r in top3:
        sd = r["sd_threshold"]
        print(f"\nSD = {sd} (Overall WR: {r['win_rate']:.1f}%, Trades: {r['trades']})")
        print("-" * 50)
        
        yearly = run_yearly_backtest(
            df, df_exit, regime_series,
            {"sd_threshold": sd, "entry_mode": ENTRY_MODE, "atr_stop": ATR_STOP, "tp_mode": TP_MODE},
            years
        )
        
        for y in yearly:
            flag = "*" if y["win_rate"] >= MIN_WIN_RATE and y["trades"] >= 10 else ""
            print(f"  {y['year']}: {y['trades']:>4} trades, WR={y['win_rate']:>5.1f}%, Net={y['net_pct']:>+7.2f}% {flag}")
    
    # ─── FIND SWEET SPOT ───
    print("\n" + "=" * 80)
    print("ANALYSIS: Finding Sweet Spot (WR > 50%, Reasonable Trades)")
    print("=" * 80)
    
    # Filter configs meeting goals
    sweet_configs = [
        r for r in results 
        if r["win_rate"] >= MIN_WIN_RATE and r["trades"] >= MIN_TRADES
    ]
    
    if sweet_configs:
        sweet_configs_sorted = sorted(sweet_configs, key=lambda x: x["trades"], reverse=True)
        best = sweet_configs_sorted[0]
        print(f"\n* SWEET SPOT FOUND: SD = {best['sd_threshold']}")
        print(f"   - Win Rate: {best['win_rate']:.1f}% (target: >{MIN_WIN_RATE}%)")
        print(f"   - Trades: {best['trades']} (target: >{MIN_TRADES})")
        print(f"   - Net: {best['net_pct']:+.2f}%")
        print(f"   - Profit Factor: {best['profit_factor']:.2f}")
        print(f"   - Sharpe: {best['sharpe']:.2f}")
        print(f"   - Max Drawdown: {best['max_drawdown_pct']:.2f}%")
    else:
        print("\n- No config met ALL criteria (WR > 50%, Trades > 50)")
        print("\nCandidates with best WR (regardless of trades):")
        top5 = sorted(results, key=lambda x: x["win_rate"], reverse=True)[:5]
        for r in top5:
            trade_flag = "OK" if r["trades"] >= MIN_TRADES else "LOW"
            print(f"  SD={r['sd_threshold']}: WR={r['win_rate']:.1f}%, Trades={r['trades']} {trade_flag}")
    
    # ─── TREND ANALYSIS ───
    print("\n" + "=" * 80)
    print("TREND ANALYSIS: SD Threshold vs Win Rate / Trades")
    print("=" * 80)
    
    df_results = pd.DataFrame(results)
    
    # Calculate trends
    wr_trend = "UP" if df_results["win_rate"].iloc[-1] > df_results["win_rate"].iloc[0] else "DOWN"
    trade_trend = "DOWN" if df_results["trades"].iloc[-1] < df_results["trades"].iloc[0] else "UP"
    
    print(f"\nAs SD increases from {SD_THRESHOLDS[0]} to {SD_THRESHOLDS[-1]}:")
    print(f"  - Win Rate: {wr_trend} ({df_results['win_rate'].iloc[0]:.1f}% -> {df_results['win_rate'].iloc[-1]:.1f}%)")
    print(f"  - Trades: {trade_trend} ({df_results['trades'].iloc[0]} -> {df_results['trades'].iloc[-1]})")
    
    # Find optimal trade-off point
    # Use score = WR - penalty_for_low_trades
    df_results["trade_score"] = df_results["trades"].apply(
        lambda t: 1.0 if t >= 100 else (0.5 if t >= 50 else 0.0)
    )
    df_results["combined_score"] = df_results["win_rate"] - (1 - df_results["trade_score"]) * 20
    
    optimal_idx = df_results["combined_score"].idxmax()
    optimal = df_results.loc[optimal_idx]
    
    print(f"\n-> Optimal trade-off (WR vs volume): SD = {optimal['sd_threshold']}")
    print(f"   Combined Score: {optimal['combined_score']:.1f}")
    print(f"   WR: {optimal['win_rate']:.1f}%, Trades: {optimal['trades']}")
    
    # Save results
    output_file = RESULTS_DIR / f"high_sd_test_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df_results.to_csv(output_file, index=False)
    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()
