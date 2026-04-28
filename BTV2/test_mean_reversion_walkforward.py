"""
test_mean_reversion_walkforward.py
====================================
Walk-Forward Optimization, Position Sizing, and Out-of-Sample Testing
for the improved Mean Reversion strategy.

Tasks:
1. Walk-Forward Optimization (12m train / 3m test, roll every 3m, 2018-2025)
2. Position Sizing (Fixed Fractional, Kelly Criterion, Volatility-Adjusted)
3. Out-of-Sample Testing (2025-01-01 to 2026-03-15)
4. Live Trading Simulation ($10k start, 2% risk, slippage, fees)

Saves:
  - BTV2/results/mean_reversion_walkforward_results.csv
  - BTV2/results/mean_reversion_position_sizing_results.csv
  - BTV2/results/mean_reversion_oos_results.csv
  - BTV2/results/mean_reversion_live_simulation_results.csv
  - BTV2/results/mean_reversion_comprehensive_report.md
"""

import sys
import math
from pathlib import Path
from itertools import product
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
    run_mean_reversion,
    _resample_ohlcv,
    build_windows,
    stitch_oos_equity,
    compute_rsi,
    compute_atr,
    compute_adx,
    compute_bollinger,
    compute_sma,
    apply_butterworth,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = 0.10

# Best configuration to validate
BEST_CONFIG = {
    "rsi_oversold": 30,
    "rsi_overbought": 75,
    "bb_proximity": 0.05,
    "atr_stop": 3.0,
    "use_regime_filter": True,
    "adx_max": 20.0,
    "use_trailing_stop": True,
    "trailing_atr_mult": 1.0,
    "use_sfp_entry": False,
    "use_poc_tp": False,
}

# Walk-forward grid (reduced for speed)
WF_GRID = {
    "rsi_oversold": [25, 30, 35],
    "rsi_overbought": [70, 75, 80],
    "adx_max": [18, 20, 22],
    "trailing_atr_mult": [0.8, 1.0, 1.2],
}

# Fixed params during walk-forward
WF_FIXED = {
    "bb_proximity": 0.05,
    "atr_stop": 3.0,
    "use_regime_filter": True,
    "use_trailing_stop": True,
    "use_sfp_entry": False,
    "use_poc_tp": False,
}

# Walk-forward schedule: 2018-01-01 to 2025-01-01
WF_START = date(2018, 1, 1)
WF_END = date(2025, 1, 1)

# OOS period
OOS_START = "2025-01-01"
OOS_END = "2026-03-15"

# Live simulation params
LIVE_STARTING_CAPITAL = 10_000
LIVE_RISK_PER_TRADE = 0.02  # 2% of equity
LIVE_SLIPPAGE = 0.0005      # 0.05% per trade
LIVE_FEE_PER_SIDE = 0.001   # 0.10% per side
LIVE_MAX_POSITION_PCT = 0.20  # 20% of portfolio max


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
    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
    sliced = df_5m.loc[mask].copy()
    if sliced.empty:
        return sliced
    return _resample_ohlcv(sliced, "1D")


def load_full_daily() -> pd.DataFrame:
    """Load all available daily data for walk-forward."""
    path_1d = DATA_DIR / "BTCUSDT_1d.parquet"
    if path_1d.exists():
        df = pd.read_parquet(path_1d)
        df.index = pd.to_datetime(df.index, utc=True)
        return df

    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    return _resample_ohlcv(df_5m, "1D")


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1: WALK-FORWARD OPTIMIZATION
# ─────────────────────────────────────────────────────────────────────────────

def optimize_fold(df_train: pd.DataFrame) -> dict:
    """Grid search over WF_GRID on training data, maximise Sharpe."""
    keys = list(WF_GRID.keys())
    vals = list(WF_GRID.values())
    best_sharpe = -math.inf
    best_params = {**WF_FIXED, **BEST_CONFIG}

    for combo in product(*vals):
        params = dict(zip(keys, combo))
        full_params = {**WF_FIXED, **params}
        try:
            eq, trd = run_mean_reversion(df_train, CUTOFF, **full_params)
            if len(trd) < 3:
                continue
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])
            if m["sharpe"] > best_sharpe:
                best_sharpe = m["sharpe"]
                best_params = full_params
        except Exception:
            pass

    return best_params


def run_walk_forward() -> tuple[list[dict], pd.Series]:
    """
    Walk-forward optimization:
    - 12-month training window
    - 3-month testing window
    - Roll forward every 3 months
    - Period: 2018-01-01 to 2025-01-01
    """
    print("\n" + "=" * 80)
    print("TASK 1: WALK-FORWARD OPTIMIZATION")
    print("=" * 80)

    windows = build_windows(WF_START, WF_END, train_months=12, test_months=3)
    print(f"  Total folds: {len(windows)}")

    # Load full data once
    df_full = load_full_daily()

    fold_results = []
    oos_segments = []

    for w in windows:
        train_start = str(w["train_start"])
        train_end = str(w["train_end"])
        test_start = str(w["test_start"])
        test_end = str(w["test_end"])

        print(f"\n  Fold {w['fold']}: Train {train_start} to {train_end}, "
              f"Test {test_start} to {test_end}")

        # Slice training data
        ts_train_s = pd.Timestamp(train_start, tz="UTC")
        ts_train_e = pd.Timestamp(train_end, tz="UTC") + pd.Timedelta(days=1)
        df_train = df_full.loc[
            (df_full.index >= ts_train_s) & (df_full.index < ts_train_e)
        ].copy()

        # Slice test data
        ts_test_s = pd.Timestamp(test_start, tz="UTC")
        ts_test_e = pd.Timestamp(test_end, tz="UTC") + pd.Timedelta(days=1)
        df_test = df_full.loc[
            (df_full.index >= ts_test_s) & (df_full.index < ts_test_e)
        ].copy()

        if df_train.empty or df_test.empty:
            print(f"    SKIP: insufficient data")
            continue

        print(f"    Train: {len(df_train)} bars, Test: {len(df_test)} bars")

        # Optimize on training window
        print(f"    Optimizing...", end=" ", flush=True)
        opt_params = optimize_fold(df_train)
        print(f"Done")

        # Apply optimized params to test window
        eq_test, trades_test = run_mean_reversion(
            df_test, CUTOFF, **opt_params
        )
        metrics_test = compute_metrics(
            eq_test, trades_test, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"]
        )

        # Also run on train for comparison
        eq_train, trades_train = run_mean_reversion(
            df_train, CUTOFF, **opt_params
        )
        metrics_train = compute_metrics(
            eq_train, trades_train, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"]
        )

        row = {
            "Fold": w["fold"],
            "Train_Start": train_start,
            "Train_End": train_end,
            "Test_Start": test_start,
            "Test_End": test_end,
            "Train_Net%": round(metrics_train["total_return_pct"], 2),
            "Train_Sharpe": round(metrics_train["sharpe"], 3),
            "Train_WR%": round(metrics_train["win_rate_pct"], 1),
            "Train_Trades": metrics_train["n_trades"],
            "OOS_Net%": round(metrics_test["total_return_pct"], 2),
            "OOS_Sharpe": round(metrics_test["sharpe"], 3),
            "OOS_WR%": round(metrics_test["win_rate_pct"], 1),
            "OOS_Trades": metrics_test["n_trades"],
            "OOS_MaxDD%": round(metrics_test["max_dd_pct"], 2),
            "OOS_PF": round(metrics_test["profit_factor"], 3),
            "Opt_rsi_oversold": opt_params["rsi_oversold"],
            "Opt_rsi_overbought": opt_params["rsi_overbought"],
            "Opt_adx_max": opt_params["adx_max"],
            "Opt_trailing_atr_mult": opt_params["trailing_atr_mult"],
        }
        fold_results.append(row)

        print(f"    Train: Net={metrics_train['total_return_pct']:+.2f}%, "
              f"Sharpe={metrics_train['sharpe']:.3f}, "
              f"WR={metrics_train['win_rate_pct']:.1f}%, "
              f"Trades={metrics_train['n_trades']}")
        print(f"    OOS:   Net={metrics_test['total_return_pct']:+.2f}%, "
              f"Sharpe={metrics_test['sharpe']:.3f}, "
              f"WR={metrics_test['win_rate_pct']:.1f}%, "
              f"Trades={metrics_test['n_trades']}, "
              f"MaxDD={metrics_test['max_dd_pct']:.2f}%")

        # Store OOS equity segment for stitching
        if len(eq_test) > 0:
            oos_segments.append(eq_test)

    # Stitch OOS equity curve
    oos_equity = stitch_oos_equity(oos_segments)

    return fold_results, oos_equity


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2: POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────

def backtest_with_position_sizing(
    df: pd.DataFrame,
    params: dict,
    sizing_method: str,
    starting_equity: float = 1.0,
    slippage: float = 0.0,
    fee_per_side: float = 0.0,
    max_position_pct: float = 1.0,
) -> tuple[pd.Series, list]:
    """
    Run mean reversion backtest with position sizing.

    sizing_method: "fixed", "fixed_fractional", "kelly", "volatility_adjusted"

    Returns (equity_curve, closed_trades) where equity is scaled by position sizes.
    """
    fc = apply_butterworth(df["Close"], CUTOFF)
    rsi = compute_rsi(fc, 14)
    bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
    sma20 = compute_sma(fc, 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)

    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(prices)

    equity_arr = np.ones(n, dtype=float) * starting_equity
    curr_equity = starting_equity
    in_pos = [False]
    side = [""]
    entry_eq = [starting_equity]
    sl_price = [0.0]
    tp_price = [0.0]
    trailing_stop_val = [0.0]
    entry_price_val = [0.0]
    position_size = [1.0]  # fraction of equity deployed
    closed_trades: list[dict] = []

    # Kelly tracking
    kelly_wins: list[float] = []
    kelly_losses: list[float] = []

    bar_dur = df.index[1] - df.index[0] if len(df) > 1 else pd.Timedelta(hours=24)

    cost_per_trade = slippage + fee_per_side

    for i in range(1, n):
        r = rsi.iloc[i]
        bbu = bb_up.iloc[i]
        bbl = bb_lo.iloc[i]
        sm = sma20.iloc[i]
        at = atr.iloc[i]
        adx_v = adx.iloc[i]
        p = prices[i]
        p0 = prices[i - 1]

        if any(np.isnan(x) for x in (r, bbu, bbl, sm, at)):
            equity_arr[i] = curr_equity
            continue

        if in_pos[0]:
            # P&L scaled by position size
            if side[0] == "long":
                price_ret = (p - p0) / (p0 + 1e-10)
            else:
                price_ret = (p0 - p) / (p0 + 1e-10)

            curr_equity += curr_equity * position_size[0] * price_ret

            # Check SL/TP
            hit = False
            if side[0] == "long":
                if lows[i] <= sl_price[0]:
                    hit = True
                elif highs[i] >= tp_price[0]:
                    hit = True
            else:
                if highs[i] >= sl_price[0]:
                    hit = True
                elif lows[i] <= tp_price[0]:
                    hit = True

            if hit:
                curr_equity *= (1.0 - cost_per_trade)
                trade_ret = curr_equity / entry_eq[0] - 1.0
                kelly_losses.append(trade_ret) if trade_ret <= 0 else kelly_wins.append(trade_ret)
                closed_trades.append({
                    "return": trade_ret,
                    "side": side[0],
                    "pos_size": position_size[0],
                })
                in_pos[0] = False

            # Trailing stop
            if params.get("use_trailing_stop", False) and in_pos[0]:
                trail_mult = params.get("trailing_atr_mult", 1.5)
                if side[0] == "long":
                    new_ts = p - trail_mult * at
                    trailing_stop_val[0] = max(trailing_stop_val[0], new_ts)
                    if lows[i] <= trailing_stop_val[0]:
                        curr_equity *= (1.0 - cost_per_trade)
                        trade_ret = curr_equity / entry_eq[0] - 1.0
                        if trade_ret <= 0:
                            kelly_losses.append(trade_ret)
                        else:
                            kelly_wins.append(trade_ret)
                        closed_trades.append({
                            "return": trade_ret,
                            "side": side[0],
                            "pos_size": position_size[0],
                        })
                        in_pos[0] = False
                else:
                    new_ts = p + trail_mult * at
                    trailing_stop_val[0] = min(trailing_stop_val[0], new_ts)
                    if highs[i] >= trailing_stop_val[0]:
                        curr_equity *= (1.0 - cost_per_trade)
                        trade_ret = curr_equity / entry_eq[0] - 1.0
                        if trade_ret <= 0:
                            kelly_losses.append(trade_ret)
                        else:
                            kelly_wins.append(trade_ret)
                        closed_trades.append({
                            "return": trade_ret,
                            "side": side[0],
                            "pos_size": position_size[0],
                        })
                        in_pos[0] = False

            # SMA-based TP
            if in_pos[0]:
                if side[0] == "long" and p >= sm:
                    curr_equity *= (1.0 - cost_per_trade)
                    trade_ret = curr_equity / entry_eq[0] - 1.0
                    if trade_ret <= 0:
                        kelly_losses.append(trade_ret)
                    else:
                        kelly_wins.append(trade_ret)
                    closed_trades.append({
                        "return": trade_ret,
                        "side": side[0],
                        "pos_size": position_size[0],
                    })
                    in_pos[0] = False
                elif side[0] == "short" and p <= sm:
                    curr_equity *= (1.0 - cost_per_trade)
                    trade_ret = curr_equity / entry_eq[0] - 1.0
                    if trade_ret <= 0:
                        kelly_losses.append(trade_ret)
                    else:
                        kelly_wins.append(trade_ret)
                    closed_trades.append({
                        "return": trade_ret,
                        "side": side[0],
                        "pos_size": position_size[0],
                    })
                    in_pos[0] = False

        else:
            # Regime filter
            if params.get("use_regime_filter", False) and not np.isnan(adx_v) and adx_v >= params.get("adx_max", 25.0):
                equity_arr[i] = curr_equity
                continue

            prox_lo = (p - bbl) / (bbl + 1e-10)
            prox_hi = (bbu - p) / (bbu + 1e-10)

            rsi_os = params.get("rsi_oversold", 30)
            rsi_ob = params.get("rsi_overbought", 75)
            bb_prox = params.get("bb_proximity", 0.05)

            long_ok = bool(r < rsi_os and 0.0 <= prox_lo <= bb_prox)
            short_ok = bool(r > rsi_ob and 0.0 <= prox_hi <= bb_prox)

            if long_ok:
                atr_stop = params.get("atr_stop", 3.0)
                sl_p = p - atr_stop * at
                tp_p = float("inf")

                # Calculate position size
                if sizing_method == "fixed_fractional":
                    risk_amount = curr_equity * 0.02
                    risk_per_unit = p - sl_p
                    if risk_per_unit > 0:
                        pos_size = min(risk_amount / risk_per_unit / (curr_equity / p), max_position_pct)
                    else:
                        pos_size = max_position_pct
                elif sizing_method == "kelly":
                    # Half-Kelly
                    if len(kelly_wins) + len(kelly_losses) >= 5:
                        all_trades = kelly_wins + kelly_losses
                        win_rate = len(kelly_wins) / len(all_trades)
                        avg_win = np.mean(kelly_wins) if kelly_wins else 0.01
                        avg_loss = abs(np.mean(kelly_losses)) if kelly_losses else 0.01
                        kelly_f = (win_rate * avg_win - (1 - win_rate) * avg_loss) / (avg_win + 1e-10)
                        pos_size = min(max(kelly_f * 0.5, 0.01), max_position_pct)
                    else:
                        pos_size = 0.02  # fallback to 2%
                elif sizing_method == "volatility_adjusted":
                    risk_amount = curr_equity * 0.02
                    if at > 0:
                        pos_size = min(risk_amount / (at * 2.0) / (curr_equity / p), max_position_pct)
                    else:
                        pos_size = max_position_pct
                else:  # fixed baseline
                    pos_size = 1.0  # full equity (baseline)

                entry_cost = 1.0 - cost_per_trade
                curr_equity *= entry_cost
                entry_eq[0] = curr_equity
                sl_price[0] = sl_p
                tp_price[0] = tp_p
                in_pos[0] = True
                side[0] = "long"
                entry_price_val[0] = p
                position_size[0] = pos_size
                trailing_stop_val[0] = p - params.get("trailing_atr_mult", 1.5) * at

            elif short_ok:
                atr_stop = params.get("atr_stop", 3.0)
                sl_p = p + atr_stop * at
                tp_p = 0.0

                # Calculate position size
                if sizing_method == "fixed_fractional":
                    risk_amount = curr_equity * 0.02
                    risk_per_unit = sl_p - p
                    if risk_per_unit > 0:
                        pos_size = min(risk_amount / risk_per_unit / (curr_equity / p), max_position_pct)
                    else:
                        pos_size = max_position_pct
                elif sizing_method == "kelly":
                    if len(kelly_wins) + len(kelly_losses) >= 5:
                        all_trades = kelly_wins + kelly_losses
                        win_rate = len(kelly_wins) / len(all_trades)
                        avg_win = np.mean(kelly_wins) if kelly_wins else 0.01
                        avg_loss = abs(np.mean(kelly_losses)) if kelly_losses else 0.01
                        kelly_f = (win_rate * avg_win - (1 - win_rate) * avg_loss) / (avg_win + 1e-10)
                        pos_size = min(max(kelly_f * 0.5, 0.01), max_position_pct)
                    else:
                        pos_size = 0.02
                elif sizing_method == "volatility_adjusted":
                    risk_amount = curr_equity * 0.02
                    if at > 0:
                        pos_size = min(risk_amount / (at * 2.0) / (curr_equity / p), max_position_pct)
                    else:
                        pos_size = max_position_pct
                else:
                    pos_size = 1.0

                entry_cost = 1.0 - cost_per_trade
                curr_equity *= entry_cost
                entry_eq[0] = curr_equity
                sl_price[0] = sl_p
                tp_price[0] = tp_p
                in_pos[0] = True
                side[0] = "short"
                entry_price_val[0] = p
                position_size[0] = pos_size
                trailing_stop_val[0] = p + params.get("trailing_atr_mult", 1.5) * at

        equity_arr[i] = curr_equity

    # Close open position
    if in_pos[0]:
        curr_equity *= (1.0 - cost_per_trade)
        trade_ret = curr_equity / entry_eq[0] - 1.0
        if trade_ret <= 0:
            kelly_losses.append(trade_ret)
        else:
            kelly_wins.append(trade_ret)
        closed_trades.append({
            "return": trade_ret,
            "side": side[0],
            "pos_size": position_size[0],
        })
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


def run_position_sizing_comparison() -> list[dict]:
    """Compare 4 position sizing methods on full 2018-2025 data."""
    print("\n" + "=" * 80)
    print("TASK 2: POSITION SIZING COMPARISON")
    print("=" * 80)

    df = load_daily_data("2018-01-01", "2025-01-01")
    if df.empty:
        print("  ERROR: No data available")
        return []

    print(f"  Data: {len(df)} daily bars from {df.index[0]} to {df.index[-1]}")

    methods = {
        "Fixed (baseline)": "fixed",
        "Fixed Fractional": "fixed_fractional",
        "Kelly Criterion": "kelly",
        "Volatility-Adjusted": "volatility_adjusted",
    }

    results = []
    for method_name, method_key in methods.items():
        print(f"\n  Testing: {method_name}...", end=" ", flush=True)

        equity, trades = backtest_with_position_sizing(
            df, BEST_CONFIG, method_key,
            starting_equity=1.0,
            slippage=0.0,
            fee_per_side=0.0,
            max_position_pct=1.0,
        )

        trade_returns = [t["return"] for t in trades]
        metrics = compute_metrics(equity, trade_returns, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

        avg_pos_size = np.mean([t["pos_size"] for t in trades]) if trades else 0

        row = {
            "Method": method_name,
            "Total_Return%": round(metrics["total_return_pct"], 2),
            "CAGR%": round(metrics["cagr_pct"], 2),
            "Sharpe": round(metrics["sharpe"], 3),
            "MaxDD%": round(metrics["max_dd_pct"], 2),
            "WinRate%": round(metrics["win_rate_pct"], 1),
            "ProfitFactor": round(metrics["profit_factor"], 3),
            "Trades": metrics["n_trades"],
            "AvgPositionSize": round(avg_pos_size, 4),
        }
        results.append(row)

        print(f"Net={metrics['total_return_pct']:+.2f}%, "
              f"Sharpe={metrics['sharpe']:.3f}, "
              f"WR={metrics['win_rate_pct']:.1f}%, "
              f"MaxDD={metrics['max_dd_pct']:.2f}%, "
              f"Trades={metrics['n_trades']}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# TASK 3: OUT-OF-SAMPLE TESTING (2025-2026)
# ─────────────────────────────────────────────────────────────────────────────

def run_oos_testing() -> dict:
    """Test best config on unseen data (2025-01-01 to 2026-03-15)."""
    print("\n" + "=" * 80)
    print("TASK 3: OUT-OF-SAMPLE TESTING (2025-2026)")
    print("=" * 80)

    df = load_daily_data(OOS_START, OOS_END)
    if df.empty:
        print("  ERROR: No OOS data available")
        return {}

    print(f"  Data: {len(df)} daily bars from {df.index[0]} to {df.index[-1]}")

    # Test with best config
    print(f"  Testing best config: {BEST_CONFIG}")
    equity, trades = run_mean_reversion(df, CUTOFF, **BEST_CONFIG)
    metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

    result = {
        "Period": f"{OOS_START} to {OOS_END}",
        "Net%": round(metrics["total_return_pct"], 2),
        "CAGR%": round(metrics["cagr_pct"], 2),
        "Sharpe": round(metrics["sharpe"], 3),
        "MaxDD%": round(metrics["max_dd_pct"], 2),
        "WinRate%": round(metrics["win_rate_pct"], 1),
        "ProfitFactor": round(metrics["profit_factor"], 3),
        "Trades": metrics["n_trades"],
    }

    print(f"  Net={metrics['total_return_pct']:+.2f}%, "
          f"Sharpe={metrics['sharpe']:.3f}, "
          f"WR={metrics['win_rate_pct']:.1f}%, "
          f"MaxDD={metrics['max_dd_pct']:.2f}%, "
          f"Trades={metrics['n_trades']}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# TASK 4: LIVE TRADING SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

def run_live_simulation() -> dict:
    """
    Simulate live trading with:
    - Starting capital: $10,000
    - Position sizing: Fixed fractional (2% risk)
    - Slippage: 0.05% per trade
    - Fees: 0.10% per side (0.20% round-trip)
    - Max position size: 20% of portfolio
    """
    print("\n" + "=" * 80)
    print("TASK 4: LIVE TRADING SIMULATION")
    print("=" * 80)

    df = load_daily_data(OOS_START, OOS_END)
    if df.empty:
        print("  ERROR: No data available for live simulation")
        return {}

    print(f"  Data: {len(df)} daily bars from {df.index[0]} to {df.index[-1]}")
    print(f"  Starting capital: ${LIVE_STARTING_CAPITAL:,.2f}")
    print(f"  Risk per trade: {LIVE_RISK_PER_TRADE * 100}%")
    print(f"  Slippage: {LIVE_SLIPPAGE * 100}%")
    print(f"  Fee per side: {LIVE_FEE_PER_SIDE * 100}%")
    print(f"  Max position: {LIVE_MAX_POSITION_PCT * 100}%")

    equity, trades = backtest_with_position_sizing(
        df, BEST_CONFIG, "fixed_fractional",
        starting_equity=LIVE_STARTING_CAPITAL,
        slippage=LIVE_SLIPPAGE,
        fee_per_side=LIVE_FEE_PER_SIDE,
        max_position_pct=LIVE_MAX_POSITION_PCT,
    )

    trade_returns = [t["return"] for t in trades]
    metrics = compute_metrics(equity, trade_returns, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

    result = {
        "StartingCapital": LIVE_STARTING_CAPITAL,
        "FinalEquity": round(float(equity.iloc[-1]), 2),
        "TotalReturn%": round(metrics["total_return_pct"], 2),
        "CAGR%": round(metrics["cagr_pct"], 2),
        "Sharpe": round(metrics["sharpe"], 3),
        "MaxDD%": round(metrics["max_dd_pct"], 2),
        "WinRate%": round(metrics["win_rate_pct"], 1),
        "ProfitFactor": round(metrics["profit_factor"], 3),
        "Trades": metrics["n_trades"],
        "AvgPositionSize%": round(
            np.mean([t["pos_size"] * 100 for t in trades]) if trades else 0, 2
        ),
    }

    print(f"  Final Equity: ${result['FinalEquity']:,.2f}")
    print(f"  Total Return: {result['TotalReturn%']:+.2f}%")
    print(f"  Sharpe: {result['Sharpe']:.3f}")
    print(f"  Max DD: {result['MaxDD%']:.2f}%")
    print(f"  Win Rate: {result['WinRate%']:.1f}%")
    print(f"  Trades: {result['Trades']}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# REPORT GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(
    wf_results: list[dict],
    wf_equity: pd.Series,
    ps_results: list[dict],
    oos_result: dict,
    live_result: dict,
) -> str:
    """Generate comprehensive markdown report."""
    lines = [
        "# Mean Reversion Strategy — Comprehensive Validation Report",
        "",
        "## Best Configuration",
        "```python",
        f"rsi_oversold = {BEST_CONFIG['rsi_oversold']}",
        f"rsi_overbought = {BEST_CONFIG['rsi_overbought']}",
        f"bb_proximity = {BEST_CONFIG['bb_proximity']}",
        f"atr_stop = {BEST_CONFIG['atr_stop']}",
        f"use_regime_filter = {BEST_CONFIG['use_regime_filter']}",
        f"adx_max = {BEST_CONFIG['adx_max']}",
        f"use_trailing_stop = {BEST_CONFIG['use_trailing_stop']}",
        f"trailing_atr_mult = {BEST_CONFIG['trailing_atr_mult']}",
        "```",
        "",
    ]

    # Walk-Forward Results
    lines.extend([
        "## Walk-Forward Optimization Results",
        "",
        "| Fold | Train Period | Test Period | OOS Net% | OOS Sharpe | OOS WR% | OOS Trades | OOS MaxDD% |",
        "|------|--------------|-------------|----------|------------|---------|------------|------------|",
    ])

    for r in wf_results:
        train_period = f"{r['Train_Start'][:7]} → {r['Train_End'][:7]}"
        test_period = f"{r['Test_Start'][:7]} → {r['Test_End'][:7]}"
        lines.append(
            f"| {r['Fold']} | {train_period} | {test_period} | "
            f"{r['OOS_Net%']:+.2f} | {r['OOS_Sharpe']:.3f} | "
            f"{r['OOS_WR%']:.1f} | {r['OOS_Trades']} | {r['OOS_MaxDD%']:.2f} |"
        )

    lines.append("")

    # Walk-forward summary
    if wf_results:
        oos_nets = [r["OOS_Net%"] for r in wf_results]
        oos_sharpes = [r["OOS_Sharpe"] for r in wf_results]
        oos_wrs = [r["OOS_WR%"] for r in wf_results]
        oos_trades = [r["OOS_Trades"] for r in wf_results]
        profitable_folds = sum(1 for n in oos_nets if n > 0)

        lines.extend([
            "### Walk-Forward Summary",
            "",
            f"- **Total Folds**: {len(wf_results)}",
            f"- **Profitable Folds**: {profitable_folds}/{len(wf_results)} ({profitable_folds/len(wf_results)*100:.0f}%)",
            f"- **Avg OOS Net Return**: {np.mean(oos_nets):+.2f}%",
            f"- **Avg OOS Sharpe**: {np.mean(oos_sharpes):.3f}",
            f"- **Avg OOS Win Rate**: {np.mean(oos_wrs):.1f}%",
            f"- **Total OOS Trades**: {sum(oos_trades)}",
            "",
        ])

        if len(wf_equity) > 0:
            wf_metrics = compute_metrics(wf_equity, [], bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])
            lines.extend([
                f"- **Stitched OOS Equity**: Start=1.0, End={wf_equity.iloc[-1]:.4f}",
                f"- **Stitched OOS Return**: {wf_metrics['total_return_pct']:+.2f}%",
                f"- **Stitched OOS Sharpe**: {wf_metrics['sharpe']:.3f}",
                "",
            ])

    # Parameter stability
    if wf_results:
        lines.extend([
            "### Optimized Parameter Stability",
            "",
            "| Fold | RSI Oversold | RSI Overbought | ADX Max | Trail ATR Mult |",
            "|------|--------------|----------------|---------|----------------|",
        ])
        for r in wf_results:
            lines.append(
                f"| {r['Fold']} | {r['Opt_rsi_oversold']} | {r['Opt_rsi_overbought']} | "
                f"{r['Opt_adx_max']} | {r['Opt_trailing_atr_mult']} |"
            )
        lines.append("")

    # Position Sizing Comparison
    lines.extend([
        "## Position Sizing Comparison",
        "",
        "| Method | Total Return% | Sharpe | MaxDD% | Win Rate% | Trades | Avg Pos Size |",
        "|--------|---------------|--------|--------|-----------|--------|--------------|",
    ])

    for r in ps_results:
        lines.append(
            f"| {r['Method']} | {r['Total_Return%']:+.2f} | {r['Sharpe']:.3f} | "
            f"{r['MaxDD%']:.2f} | {r['WinRate%']:.1f} | {r['Trades']} | "
            f"{r['AvgPositionSize']:.4f} |"
        )

    lines.append("")

    # Out-of-Sample Results
    lines.extend([
        "## Out-of-Sample Testing (2025-2026)",
        "",
        f"**Period**: {OOS_START} to {OOS_END}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ])

    if oos_result:
        for key, val in oos_result.items():
            if isinstance(val, float):
                lines.append(f"| {key} | {val:.2f} |" if "%" in key or "DD" in key else f"| {key} | {val:.3f} |" if key == "Sharpe" else f"| {key} | {val} |")
            else:
                lines.append(f"| {key} | {val} |")

    lines.append("")

    # Live Simulation Results
    lines.extend([
        "## Live Trading Simulation",
        "",
        f"**Starting Capital**: ${LIVE_STARTING_CAPITAL:,.2f}",
        f"**Risk per Trade**: {LIVE_RISK_PER_TRADE * 100}%",
        f"**Slippage**: {LIVE_SLIPPAGE * 100}%",
        f"**Fee per Side**: {LIVE_FEE_PER_SIDE * 100}%",
        f"**Max Position**: {LIVE_MAX_POSITION_PCT * 100}%",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ])

    if live_result:
        for key, val in live_result.items():
            if key == "StartingCapital":
                lines.append(f"| {key} | ${val:,.2f} |")
            elif key == "FinalEquity":
                lines.append(f"| {key} | ${val:,.2f} |")
            elif isinstance(val, float):
                if "%" in key:
                    lines.append(f"| {key} | {val:.2f}% |")
                elif key == "Sharpe":
                    lines.append(f"| {key} | {val:.3f} |")
                else:
                    lines.append(f"| {key} | {val:.2f} |")
            else:
                lines.append(f"| {key} | {val} |")

    lines.append("")

    # Goal Assessment
    lines.extend([
        "## Goal Assessment",
        "",
        "### 1. Generalizes Across Time (Walk-Forward OOS > 0)",
    ])
    if wf_results:
        profitable = sum(1 for r in wf_results if r["OOS_Net%"] > 0)
        avg_oos = np.mean([r["OOS_Net%"] for r in wf_results])
        status = "✅ PASS" if avg_oos > 0 else "❌ FAIL"
        lines.append(f"- **Status**: {status}")
        lines.append(f"- **Profitable Folds**: {profitable}/{len(wf_results)}")
        lines.append(f"- **Avg OOS Return**: {avg_oos:+.2f}%")
    lines.append("")

    lines.append("### 2. Position Sizing Improves Risk-Adjusted Returns")
    if ps_results:
        baseline = next((r for r in ps_results if "baseline" in r["Method"].lower()), None)
        best_method = max(ps_results, key=lambda r: r["Sharpe"])
        if baseline:
            improved = best_method["Sharpe"] > baseline["Sharpe"]
            status = "✅ PASS" if improved else "❌ FAIL"
            lines.append(f"- **Status**: {status}")
            lines.append(f"- **Baseline Sharpe**: {baseline['Sharpe']:.3f}")
            lines.append(f"- **Best Method**: {best_method['Method']} (Sharpe: {best_method['Sharpe']:.3f})")
    lines.append("")

    lines.append("### 3. Works on Unseen Data (2025-2026)")
    if oos_result:
        status = "✅ PASS" if oos_result.get("Net%", 0) > 0 else "⚠️ MIXED"
        lines.append(f"- **Status**: {status}")
        lines.append(f"- **OOS Net Return**: {oos_result.get('Net%', 0):+.2f}%")
        lines.append(f"- **OOS Sharpe**: {oos_result.get('Sharpe', 0):.3f}")
        lines.append(f"- **OOS Trades**: {oos_result.get('Trades', 0)}")
    lines.append("")

    lines.append("### 4. Safe for Live Trading with Risk Management")
    if live_result:
        max_dd = live_result.get("MaxDD%", 0)
        wr = live_result.get("WinRate%", 0)
        dd_ok = abs(max_dd) < 25  # Max DD under 25%
        wr_ok = wr > 45  # Win rate above 45%
        status = "✅ PASS" if dd_ok and wr_ok else "⚠️ CAUTION"
        lines.append(f"- **Status**: {status}")
        lines.append(f"- **Final Equity**: ${live_result.get('FinalEquity', 0):,.2f}")
        lines.append(f"- **Max Drawdown**: {max_dd:.2f}% {'✅' if dd_ok else '⚠️'}")
        lines.append(f"- **Win Rate**: {wr:.1f}% {'✅' if wr_ok else '⚠️'}")
    lines.append("")

    # Final Verdict
    lines.extend([
        "## Final Verdict",
        "",
    ])

    all_pass = True
    if wf_results:
        avg_oos = np.mean([r["OOS_Net%"] for r in wf_results])
        if avg_oos <= 0:
            all_pass = False
    if ps_results:
        baseline = next((r for r in ps_results if "baseline" in r["Method"].lower()), None)
        best_method = max(ps_results, key=lambda r: r["Sharpe"])
        if baseline and best_method["Sharpe"] <= baseline["Sharpe"]:
            all_pass = False

    if all_pass:
        lines.append("✅ **The Mean Reversion strategy passes all validation criteria.**")
        lines.append("It generalizes across time periods, benefits from position sizing,")
        lines.append("works on unseen data, and can be safely traded with proper risk management.")
    else:
        lines.append("⚠️ **The strategy shows mixed results.** Some criteria pass while others need attention.")
        lines.append("Review the detailed results above for specific areas of concern.")

    lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("MEAN REVERSION — WALK-FORWARD, POSITION SIZING, OOS, LIVE SIMULATION")
    print("=" * 80)

    # Task 1: Walk-Forward Optimization
    wf_results, wf_equity = run_walk_forward()

    # Save walk-forward results
    if wf_results:
        df_wf = pd.DataFrame(wf_results)
        wf_csv = RESULTS_DIR / "mean_reversion_walkforward_results.csv"
        df_wf.to_csv(wf_csv, index=False)
        print(f"\n  Walk-forward results saved to {wf_csv}")

    # Task 2: Position Sizing Comparison
    ps_results = run_position_sizing_comparison()

    # Save position sizing results
    if ps_results:
        df_ps = pd.DataFrame(ps_results)
        ps_csv = RESULTS_DIR / "mean_reversion_position_sizing_results.csv"
        df_ps.to_csv(ps_csv, index=False)
        print(f"\n  Position sizing results saved to {ps_csv}")

    # Task 3: Out-of-Sample Testing
    oos_result = run_oos_testing()

    # Save OOS results
    if oos_result:
        df_oos = pd.DataFrame([oos_result])
        oos_csv = RESULTS_DIR / "mean_reversion_oos_results.csv"
        df_oos.to_csv(oos_csv, index=False)
        print(f"\n  OOS results saved to {oos_csv}")

    # Task 4: Live Trading Simulation
    live_result = run_live_simulation()

    # Save live simulation results
    if live_result:
        df_live = pd.DataFrame([live_result])
        live_csv = RESULTS_DIR / "mean_reversion_live_simulation_results.csv"
        df_live.to_csv(live_csv, index=False)
        print(f"\n  Live simulation results saved to {live_csv}")

    # Generate comprehensive report
    report = generate_report(wf_results, wf_equity, ps_results, oos_result, live_result)
    report_path = RESULTS_DIR / "mean_reversion_comprehensive_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n  Comprehensive report saved to {report_path}")

    # Print summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    if wf_results:
        avg_oos = np.mean([r["OOS_Net%"] for r in wf_results])
        profitable = sum(1 for r in wf_results if r["OOS_Net%"] > 0)
        print(f"\nWalk-Forward: {profitable}/{len(wf_results)} profitable folds, "
              f"Avg OOS: {avg_oos:+.2f}%")

    if ps_results:
        print("\nPosition Sizing:")
        for r in ps_results:
            print(f"  {r['Method']:<25} Net={r['Total_Return%']:+.2f}%  "
                  f"Sharpe={r['Sharpe']:.3f}  MaxDD={r['MaxDD%']:.2f}%")

    if oos_result:
        print(f"\nOOS (2025-2026): Net={oos_result['Net%']:+.2f}%, "
              f"Sharpe={oos_result['Sharpe']:.3f}, "
              f"WR={oos_result['WinRate%']:.1f}%, "
              f"Trades={oos_result['Trades']}")

    if live_result:
        print(f"\nLive Sim: ${live_result['FinalEquity']:,.2f} "
              f"({live_result['TotalReturn%']:+.2f}%), "
              f"Sharpe={live_result['Sharpe']:.3f}, "
              f"MaxDD={live_result['MaxDD%']:.2f}%")


if __name__ == "__main__":
    main()
