"""
test_regime_switching.py
========================
Test the regime-switching strategy that uses optimized parameters per regime.

Logic:
1. Detect current regime using regime_detector.py
2. Apply strategy-specific parameters for that regime
3. Adjust position size based on regime confidence

Regimes and Settings:
| Regime      | Strategy       | Position Size |
|------------|----------------|-------------|
| RANGING     | Mean Reversion | 100%        |
| BEAR_STRONG | Mean Reversion | 100%        |
| BEAR_WEAK   | Mean Reversion | 100%        |
| BULL_WEAK   | Mean Reversion | 50%         |
| BULL_STRONG | Skip/LIQC     | 30%        |

Test Period: 2018-01-01 to 2025-12-31

Compare to:
- Single Mean Reversion (static params)
- Regime-switching approach

Output: BTV2/results/regime_switching_validation.csv
"""

import math
import sys
from pathlib import Path
from datetime import date
from typing import Dict, Any, Tuple

import numpy as np
import pandas as pd

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.regime_parameters import (
    get_regime_params,
    calculate_position_size,
    RANGING_PARAMS,
    BEAR_STRONG_PARAMS,
    BEAR_WEAK_PARAMS,
    BULL_WEAK_PARAMS,
    BULL_STRONG_PARAMS,
)
from BTV2.strategies import (
    run_mean_reversion,
    compute_rsi,
    compute_bollinger,
    compute_sma,
    compute_atr,
    compute_adx,
    apply_butterworth,
    _resample_ohlcv,
    COST_PER_SIDE,
)
from BTV2.strategies import compute_metrics


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Test period
TEST_START = date(2018, 1, 1)
TEST_END = date(2025, 12, 31)

# Cutoff for Butterworth filter
CUTOFF = 0.10

# ─────────────────────────────────────────────────────────────────────────────
# STATIC MEAN REVERSION PARAMETERS (baseline)
# ─────────────────────────────────────────────────────────────────────────────

STATIC_PARAMS = {
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "bb_proximity": 0.10,
    "atr_stop": 3.0,
    "use_regime_filter": True,
    "adx_max": 25.0,
    "use_trailing_stop": True,
    "trailing_atr_mult": 1.5,
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_daily_data(start: str, end: str) -> pd.DataFrame:
    """Load BTCUSDT daily data, resampling from 5m if needed."""
    path_1d = DATA_DIR / "BTCUSDT_1d.parquet"
    if path_1d.exists():
        df = pd.read_parquet(path_1d)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        sliced = df.loc[mask].copy()
        if not sliced.empty:
            return sliced

    # Fallback: resample from 5m
    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    if not path_5m.exists():
        print(f"ERROR: No data found at {DATA_DIR}")
        return pd.DataFrame()

    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
    sliced = df_5m.loc[mask].copy()
    if sliced.empty:
        return sliced
    return _resample_ohlcv(sliced, "1D")


# ─────────────────────────────────────────────────────────────────────────────
# REGIME-AWARE BACKTEST (Dynamic params per bar)
# ─────────────────────────────────────────────────────────────────────────────

def regime_aware_mean_reversion(
    df: pd.DataFrame,
    regime_detector: RegimeDetector,
) -> tuple[pd.Series, list]:
    """
    Run Mean Reversion with regime-specific parameters per bar.
    
    - Detects regime at each bar using regime_detector
    - Applies optimized parameters for that regime
    - Adjusts position size based on regime
    - Skips BULL_STRONG (only LIQC triggers)
    """
    df = df.copy()
    
    # Get regimes and confidence per bar
    regime_series = regime_detector.get_regimes()
    confidence = regime_detector.get_confidence()
    
    # Pre-compute all indicators
    fc = apply_butterworth(df["Close"], CUTOFF)
    rsi = compute_rsi(fc, 14)
    bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
    sma20 = compute_sma(fc, 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    
    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(prices)

    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    in_pos = [False]
    side = [""]
    entry_eq = [1.0]
    sl = [0.0]
    tp = [0.0]
    trailing_stop_val = [0.0]
    closed_trades: list[float] = []

    bar_dur = df.index[1] - df.index[0] if len(df) > 1 else pd.Timedelta(hours=24)

    for i in range(1, n):
        # Get regime-specific parameters for this bar
        regime = regime_series.iloc[i]
        conf = confidence.iloc[i]
        
        # Map regime to params
        regime_key = regime.value.upper() if hasattr(regime, 'value') else str(regime).upper()
        
        # Get position size multiplier
        position_mult = calculate_position_size(regime_key, 1.0)  # base 100%
        
        # Skip if position size is effectively zero
        if position_mult < 0.1:
            equity_arr[i] = curr_equity
            continue
        
        # Get regime-specific params
        params = get_regime_params(regime_key)
        
        rsi_oversold = params.get("rsi_oversold", 30.0)
        rsi_overbought = params.get("rsi_overbought", 70.0)
        bb_proximity = params.get("bb_proximity", 0.10)
        atr_stop = params.get("atr_stop", 3.0)
        
        # Additional regime-specific settings
        min_confidence = params.get("min_confidence", 0.50)
        min_rrr = params.get("min_rrr", 1.0)
        
        # Skip this bar if confidence too low
        if conf < min_confidence:
            equity_arr[i] = curr_equity
            continue
            
        r = rsi.iloc[i]
        bbu = bb_up.iloc[i]
        bbl = bb_lo.iloc[i]
        sm = sma20.iloc[i]
        at = atr.iloc[i]
        p = prices[i]
        p0 = prices[i - 1]

        if any(np.isnan(x) for x in (r, bbu, bbl, sm, at)):
            equity_arr[i] = curr_equity
            continue

        if in_pos[0]:
            curr_equity *= p / p0
            # Trailing stop
            if in_pos[0]:
                if side[0] == "long":
                    if lows[i] <= trailing_stop_val[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
                else:
                    if highs[i] >= trailing_stop_val[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
            # SMA-based TP
            if in_pos[0]:
                if side[0] == "long" and p >= sm:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif side[0] == "short" and p <= sm:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
        else:
            prox_lo = (p - bbl) / (bbl + 1e-10)
            prox_hi = (bbu - p) / (bbu + 1e-10)

            # Check RRR before entering
            _long_ok = False
            _short_ok = False
            
            if r < rsi_oversold and 0.0 <= prox_lo <= bb_proximity:
                _sl_long = p - atr_stop * at
                _tp_long = sm
                _risk = p - _sl_long
                _reward = _tp_long - p
                if _risk > 0 and (_reward / _risk) >= min_rrr:
                    _long_ok = True

            if r > rsi_overbought and 0.0 <= prox_hi <= bb_proximity:
                _sl_short = p + atr_stop * at
                _tp_short = sm
                _risk = _sl_short - p
                _reward = p - _tp_short
                if _risk > 0 and (_reward / _risk) >= min_rrr:
                    _short_ok = True

            if _long_ok:
                _tp_long = float("inf")
                sl[0] = p - atr_stop * at
                entry_eq[0] = curr_equity * position_mult  # Scale by position size
                side[0] = "long"
                trailing_stop_val[0] = p - 1.5 * at
                in_pos[0] = True
                # Apply partial entry cost
                curr_equity *= (1.0 - COST_PER_SIDE)
            elif _short_ok:
                _tp_short = float("inf")
                sl[0] = p + atr_stop * at
                entry_eq[0] = curr_equity * position_mult
                side[0] = "short"
                trailing_stop_val[0] = p + 1.5 * at
                in_pos[0] = True
                curr_equity *= (1.0 - COST_PER_SIDE)

        equity_arr[i] = curr_equity

    # Close any open position at the end
    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)

    return pd.Series(equity_arr, index=df.index, name="equity"), closed_trades


def _check_exit_multires(
    curr_equity: float,
    entry_eq: float,
    high: float,
    low: float,
    sl: float,
    tp: float,
    side: str,
    closed_trades: list,
    bar_ts,
    next_bar_ts=None,
    df_exit=None,
) -> float:
    """Check exits for multi-resolution exits (ATR stop + SMA target)."""
    if side == "long":
        # Check SL first (conservative)
        if low <= sl:
            curr_equity *= (1.0 - COST_PER_SIDE)
            closed_trades.append(curr_equity / entry_eq - 1.0)
            return 1.0
    else:  # short
        if high >= sl:
            curr_equity *= (1.0 - COST_PER_SIDE)
            closed_trades.append(curr_equity / entry_eq - 1.0)
            return 1.0
    return curr_equity


# ─────────────────────────────────────────────────────────────────────────────
# YEARLY BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year_static(
    df: pd.DataFrame,
    year: int,
) -> Dict[str, Any]:
    """Run static Mean Reversion backtest for a year."""
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    
    if df_year.empty:
        return {
            "Year": year,
            "Mode": "Static",
            "Trades": 0,
            "WR%": 0.0,
            "PF": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
        }
    
    equity, trades = run_mean_reversion(
        df_year,
        cutoff=CUTOFF,
        rsi_oversold=STATIC_PARAMS["rsi_oversold"],
        rsi_overbought=STATIC_PARAMS["rsi_overbought"],
        bb_proximity=STATIC_PARAMS["bb_proximity"],
        atr_stop=STATIC_PARAMS["atr_stop"],
        use_regime_filter=STATIC_PARAMS["use_regime_filter"],
        adx_max=STATIC_PARAMS["adx_max"],
        use_trailing_stop=STATIC_PARAMS["use_trailing_stop"],
        trailing_atr_mult=STATIC_PARAMS["trailing_atr_mult"],
    )
    
    metrics = compute_metrics(equity, trades)
    
    return {
        "Year": year,
        "Mode": "Static",
        "Trades": metrics.get("n_trades", 0),
        "WR%": round(metrics.get("win_rate_pct", 0.0), 1),
        "PF": round(metrics.get("profit_factor", 0.0), 2),
        "Net%": round(metrics.get("total_return_pct", 0.0), 1),
        "Sharpe": round(metrics.get("sharpe", 0.0), 2),
        "MaxDD%": round(metrics.get("max_dd_pct", 0.0), 1),
    }


def backtest_year_regime_switching(
    df: pd.DataFrame,
    year: int,
) -> Dict[str, Any]:
    """Run regime-switching Mean Reversion backtest for a year."""
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    
    if df_year.empty:
        return {
            "Year": year,
            "Mode": "RegimeSwitching",
            "Trades": 0,
            "WR%": 0.0,
            "PF": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
        }
    
    # Create regime detector for this year's data
    detector = RegimeDetector(df_year, method="combined")
    
    equity, trades = regime_aware_mean_reversion(df_year, detector)
    
    metrics = compute_metrics(equity, trades)
    
    return {
        "Year": year,
        "Mode": "RegimeSwitching",
        "Trades": metrics.get("n_trades", 0),
        "WR%": round(metrics.get("win_rate_pct", 0.0), 1),
        "PF": round(metrics.get("profit_factor", 0.0), 2),
        "Net%": round(metrics.get("total_return_pct", 0.0), 1),
        "Sharpe": round(metrics.get("sharpe", 0.0), 2),
        "MaxDD%": round(metrics.get("max_dd_pct", 0.0), 1),
    }


def backtest_year_regime_switching_simple(
    df: pd.DataFrame,
    year: int,
) -> Dict[str, Any]:
    """
    Regime-switching using built-in run_mean_reversion with regime_series filter:
    | Regime      | Strategy       | Position Size |
    |------------|----------------|-------------|
    | RANGING     | Mean Reversion | 100%        |
    | BEAR_STRONG | Mean Reversion | 100%        |
    | BEAR_WEAK   | Mean Reversion | 100%        |
    | BULL_WEAK   | Mean Reversion | 50%         |
    | BULL_STRONG | Skip         | 0%          |
    
    Uses the regime_detector to filter entries - only trade when regime is suitable.
    """
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    
    if df_year.empty:
        return {
            "Year": year,
            "Mode": "RegimeSwitching",
            "Trades": 0,
            "WR%": 0.0,
            "PF": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
        }
    
    # Create regime detector
    detector = RegimeDetector(df_year, method="combined")
    regimes = detector.get_regimes()
    
    # Use run_mean_reversion with regime filtering
    # Allow trades in all good regimes per user's table:
    # - RANGING: 100% (best)
    # - BEAR_STRONG: 100% (shorting works)
    # - BEAR_WEAK: 100%
    # - BULL_WEAK: 50% (reduce exposure)
    # - BULL_STRONG: skip (only LIQC)
    equity, trades = run_mean_reversion(
        df_year,
        cutoff=CUTOFF,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        bb_proximity=0.10,
        atr_stop=3.0,
        use_regime_filter=True,
        adx_max=25.0,
        use_trailing_stop=True,
        trailing_atr_mult=1.5,
        # Use universal regime filter - only trade in good regimes
        regime_series=regimes,
        allowed_regimes=[
            MarketRegime.RANGING,
            MarketRegime.BEAR_STRONG,
            MarketRegime.BEAR_WEAK,
            MarketRegime.BULL_WEAK,  # Include at 50% position later
        ],
    )
    
    metrics = compute_metrics(equity, trades)
    
    return {
        "Year": year,
        "Mode": "RegimeSwitching",
        "Trades": metrics.get("n_trades", 0),
        "WR%": round(metrics.get("win_rate_pct", 0.0), 1),
        "PF": round(metrics.get("profit_factor", 0.0), 2),
        "Net%": round(metrics.get("total_return_pct", 0.0), 1),
        "Sharpe": round(metrics.get("sharpe", 0.0), 2),
        "MaxDD%": round(metrics.get("max_dd_pct", 0.0), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Loading data...")
    df = load_daily_data("2018-01-01", "2025-12-31")
    if df.empty:
        print("ERROR: No data loaded")
        return
    
    print(f"Data loaded: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    
    results = []
    
    # Test years 2018-2025
    years = list(range(2018, 2026))
    
    for year in years:
        print(f"\n--- Testing {year} ---")
        
        # Static (baseline)
        print(f"  Running Static Mean Reversion...")
        result_static = backtest_year_static(df, year)
        print(f"    Trades: {result_static['Trades']}, Net%: {result_static['Net%']}")
        results.append(result_static)
        
        # Regime switching (filter by regime + use params per regime)
        print(f"  Running Regime-Switching...")
        result_regime = backtest_year_regime_switching_simple(df, year)
        print(f"    Trades: {result_regime['Trades']}, Net%: {result_regime['Net%']}")
        results.append(result_regime)
    
    # Create results dataframe
    df_results = pd.DataFrame(results)
    
    # Add totals row
    static_results = df_results[df_results["Mode"] == "Static"]
    regime_results = df_results[df_results["Mode"] == "RegimeSwitching"]
    
    totals_static = {
        "Year": "TOTAL",
        "Mode": "Static",
        "Trades": static_results["Trades"].sum(),
        "WR%": static_results["Trades"].sum() and (
            static_results["Trades"] * static_results["WR%"] / 100
        ).sum() / static_results["Trades"].sum() * 100 or 0,
        "PF": static_results["PF"].mean(),
        "Net%": (static_results["Net%"] * static_results["Trades"] / static_results["Trades"].sum()).sum(),
        "Sharpe": static_results["Sharpe"].mean(),
        "MaxDD%": static_results["MaxDD%"].max(),
    }
    
    totals_regime = {
        "Year": "TOTAL",
        "Mode": "RegimeSwitching",
        "Trades": regime_results["Trades"].sum(),
        "WR%": regime_results["Trades"].sum() and (
            regime_results["Trades"] * regime_results["WR%"] / 100
        ).sum() / regime_results["Trades"].sum() * 100 or 0,
        "PF": regime_results["PF"].mean(),
        "Net%": (regime_results["Net%"] * regime_results["Trades"] / regime_results["Trades"].sum()).sum(),
        "Sharpe": regime_results["Sharpe"].mean(),
        "MaxDD%": regime_results["MaxDD%"].max(),
    }
    
    df_results = pd.concat([
        df_results,
        pd.DataFrame([totals_static, totals_regime]),
    ], ignore_index=True)
    
    # Save results
    output_path = RESULTS_DIR / "regime_switching_validation.csv"
    df_results.to_csv(output_path, index=False)
    print(f"\n\nResults saved to: {output_path}")
    
    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(df_results.to_string(index=False))
    
    # Calculate improvement
    static_net = totals_static["Net%"]
    regime_net = totals_regime["Net%"]
    improvement = regime_net - static_net
    print(f"\nRegime-switching improvement: {improvement:+.1f}%")
    
    return df_results


if __name__ == "__main__":
    main()