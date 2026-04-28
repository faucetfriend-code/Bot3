"""
test_enhanced_regime_next_steps.py
===================================
Implements the three next steps for the enhanced regime detection system:

Task 1: Re-test ALL 6 strategies with enhanced regime filter
Task 2: Validate Mean Reversion with confidence-based position sizing
Task 3: Test Liquidation Capture on 4h with enhanced regime filter

Data: G:/Candle Data/BTCUSDT_5m.parquet (resampled to needed timeframes)

Saves:
  - BTV2/results/all_strategies_enhanced_regime.csv
  - BTV2/results/mean_reversion_confidence_sizing.csv
  - BTV2/results/liquidation_4h_enhanced_regime.csv
  - BTV2/results/enhanced_regime_comprehensive_report.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
    run_mean_reversion,
    run_vwap_scalping,
    run_momentum_scalping,
    run_liquidation_capture,
    run_grid_trading,
    run_ma_crossover,
    _resample_ohlcv,
)
from BTV2.regime_detector import (
    RegimeDetector,
    MarketRegime,
    STRATEGY_REGIME_COMPATIBILITY,
)

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

DATA_PATH = Path("G:/Candle Data/BTCUSDT_5m.parquet")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = 0.10

YEAR_RANGES = [
    ("2018-01-01", "2019-01-01"),
    ("2019-01-01", "2020-01-01"),
    ("2020-01-01", "2021-01-01"),
    ("2021-01-01", "2022-01-01"),
    ("2022-01-01", "2023-01-01"),
    ("2023-01-01", "2024-01-01"),
    ("2024-01-01", "2025-01-01"),
]

# ── Resampling helpers ────────────────────────────────────────────────────────

_df_5m_cache: pd.DataFrame | None = None


def _load_5m() -> pd.DataFrame:
    global _df_5m_cache
    if _df_5m_cache is not None:
        return _df_5m_cache
    print("  Loading 5m parquet (this may take a moment)...", flush=True)
    df = pd.read_parquet(DATA_PATH)
    df.index = pd.to_datetime(df.index, utc=True)
    _df_5m_cache = df
    return df


def _resample(rule: str, start: str, end: str) -> pd.DataFrame:
    """Resample 5m data to the given rule and slice by date range."""
    df5 = _load_5m()
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df5.index >= start_ts) & (df5.index < end_ts)
    sliced = df5.loc[mask].copy()
    if sliced.empty:
        return sliced
    return _resample_ohlcv(sliced, rule)


def load_daily(start: str, end: str) -> pd.DataFrame:
    return _resample("1D", start, end)


def load_4h(start: str, end: str) -> pd.DataFrame:
    return _resample("4H", start, end)


def load_15m(start: str, end: str) -> pd.DataFrame:
    return _resample("15min", start, end)


def load_5m(start: str, end: str) -> pd.DataFrame:
    df5 = _load_5m()
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df5.index >= start_ts) & (df5.index < end_ts)
    return df5.loc[mask].copy()


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY CONFIGURATIONS
# ─────────────────────────────────────────────────────────────────────────────

MR_PARAMS = {
    "rsi_oversold": 25, "rsi_overbought": 75, "bb_proximity": 0.05,
    "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False,
    "use_trailing_stop": True, "trailing_atr_mult": 1.0,
}

VS_PARAMS = {
    "sd_threshold": 2.0, "atr_stop": 0.7, "atr_target": 3.0,
    "ema_fast": 9, "ema_slow": 20, "use_trend_filter": True,
    "use_volume_filter": True, "volume_mult": 1.5,
    "use_trailing_stop": True, "trailing_atr": 1.2,
    "adx_max": 25.0, "rsi_max": 45.0,
    "entry_mode": "bull_pullback", "pullback_bars": 3,
    "use_htf_vwap": True, "use_htf_ema": True,
    "htf_adx_max": 25.0, "stoch_oversold": 40, "stoch_overbought": 60,
    "use_anchored_vwap": True, "use_session_filter": True,
    "require_reversal_candle": True, "tp_mode": "vwap",
    "require_mss": False, "mss_timeout_bars": 6,
    "use_fvg_filter": False, "use_ob_filter": False, "use_eqhl_filter": False,
    "use_cvd_filter": False, "use_ote": False, "use_dynamic_mode": False,
}

MS_PARAMS = {
    "ema_fast": 20, "ema_slow": 50, "atr_stop": 1.0, "atr_target": 2.0,
    "use_cvd_confirm": False, "cvd_window": 20,
}

# Best 4h config from previous test (Task 3)
# Note: run_liquidation_capture uses price_threshold, volume_mult, rsi_threshold
# and internally uses 3:1 RRR for TP/SL. We adjust thresholds for 4h sensitivity.
LC_4H_PARAMS = {
    "price_threshold": 0.03,
    "volume_mult": 2.0,
    "rsi_threshold": 20,
    "use_nearest_tp": False,
}

# Daily liquidation params (for reference)
LC_DAILY_PARAMS = {
    "price_threshold": 0.030, "volume_mult": 3.0, "rsi_threshold": 18.0,
    "use_nearest_tp": False,
}

GT_PARAMS = {
    "adx_threshold": 15, "spacing_mult": 0.30,
    "use_poc_center": False, "use_va_bounds": False,
}

MA_PARAMS = {
    "fast_period": 20, "slow_period": 50, "pullback_max": 0.06,
    "use_va_chop_filter": False,
}


def _get_strategy_func(strategy_name: str):
    """Get the strategy function by name."""
    funcs = {
        "Mean Reversion":      run_mean_reversion,
        "VWAP Scalping":       run_vwap_scalping,
        "Momentum Scalping":   run_momentum_scalping,
        "Liquidation Capture": run_liquidation_capture,
        "Grid Trading":        run_grid_trading,
        "MA Crossover":        run_ma_crossover,
    }
    return funcs[strategy_name]


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: run strategy with regime filter
# ─────────────────────────────────────────────────────────────────────────────

def _run_with_regime(func, df, cutoff, params, regime_series, allowed_regimes):
    """Run strategy with regime filter kwargs."""
    return func(
        df, cutoff,
        regime_series=regime_series,
        allowed_regimes=allowed_regimes,
        **params,
    )


def _run_no_regime(func, df, cutoff, params):
    """Run strategy without regime filter."""
    return func(df, cutoff, **params)


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1: Re-test ALL 6 strategies with enhanced regime filter
# ─────────────────────────────────────────────────────────────────────────────

def task1_all_strategies_enhanced_regime() -> pd.DataFrame:
    """
    Test all 6 strategies with no filter vs enhanced regime filter.
    Returns a DataFrame with per-year results for each strategy.
    """
    print("\n" + "=" * 80)
    print("TASK 1: ALL STRATEGIES WITH ENHANCED REGIME FILTER")
    print("=" * 80)

    strategy_configs = [
        ("Mean Reversion",      load_daily, MR_PARAMS, INTERVAL_BARS_PER_YEAR["1d"],  "1d"),
        ("VWAP Scalping",       load_5m,    VS_PARAMS, INTERVAL_BARS_PER_YEAR["5m"],  "5m"),
        ("Momentum Scalping",   load_15m,   MS_PARAMS, INTERVAL_BARS_PER_YEAR["15m"], "15m"),
        ("Liquidation Capture", load_daily, LC_DAILY_PARAMS, INTERVAL_BARS_PER_YEAR["1d"], "1d"),
        ("Grid Trading",        load_4h,    GT_PARAMS, INTERVAL_BARS_PER_YEAR["4h"],  "4h"),
        ("MA Crossover",        load_daily, MA_PARAMS, INTERVAL_BARS_PER_YEAR["1d"],  "1d"),
    ]

    all_rows = []

    for strat_name, load_fn, params, bpy, tf in strategy_configs:
        print(f"\n--- {strat_name} (timeframe={tf}) ---")
        allowed = STRATEGY_REGIME_COMPATIBILITY.get(strat_name, [])
        func = _get_strategy_func(strat_name)

        for start, end in YEAR_RANGES:
            year = int(start[:4])
            print(f"  {year}...", end=" ", flush=True)

            try:
                df = load_fn(start, end)
                if df.empty:
                    print("NO DATA")
                    for filt in ["no_filter", "enhanced"]:
                        all_rows.append({
                            "Strategy": strat_name, "Year": year, "Filter": filt,
                            "Trades": 0, "WR%": 0.0, "PF": 0.0,
                            "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                        })
                    continue

                # No filter
                eq_nf, tr_nf = _run_no_regime(func, df, CUTOFF, params)
                m_nf = compute_metrics(eq_nf, tr_nf, bars_per_year=bpy)

                # Enhanced filter
                det = RegimeDetector(df, method="combined", timeframe=tf)
                regimes = det.get_regimes()
                eq_ef, tr_ef = _run_with_regime(func, df, CUTOFF, params, regimes, allowed)
                m_ef = compute_metrics(eq_ef, tr_ef, bars_per_year=bpy)

                print(
                    f"NoFilter: Net={m_nf['total_return_pct']:+.2f}% | "
                    f"Enhanced: Net={m_ef['total_return_pct']:+.2f}%"
                )

                for filt, m in [("no_filter", m_nf), ("enhanced", m_ef)]:
                    all_rows.append({
                        "Strategy": strat_name, "Year": year, "Filter": filt,
                        "Trades": m["n_trades"],
                        "WR%": round(m["win_rate_pct"], 1),
                        "PF": round(m["profit_factor"], 3),
                        "Net%": round(m["total_return_pct"], 2),
                        "Sharpe": round(m["sharpe"], 3),
                        "MaxDD%": round(m["max_dd_pct"], 2),
                    })

            except Exception as e:
                print(f"ERROR: {e}")
                import traceback
                traceback.print_exc()
                for filt in ["no_filter", "enhanced"]:
                    all_rows.append({
                        "Strategy": strat_name, "Year": year, "Filter": filt,
                        "Trades": 0, "WR%": 0.0, "PF": 0.0,
                        "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                    })

    df_results = pd.DataFrame(all_rows)
    csv_path = RESULTS_DIR / "all_strategies_enhanced_regime.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nSaved: {csv_path}")
    return df_results


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2: Mean Reversion with confidence-based position sizing
# ─────────────────────────────────────────────────────────────────────────────

def task2_mean_reversion_confidence_sizing() -> pd.DataFrame:
    """
    Compare three methods for Mean Reversion:
    1. Fixed size (no filter)
    2. Binary filter (skip trades in bad regimes)
    3. Confidence-weighted (scale position size by regime confidence)
    """
    print("\n" + "=" * 80)
    print("TASK 2: MEAN REVERSION — CONFIDENCE-BASED POSITION SIZING")
    print("=" * 80)

    allowed = STRATEGY_REGIME_COMPATIBILITY.get("Mean Reversion", [])
    func = run_mean_reversion
    all_rows = []

    for start, end in YEAR_RANGES:
        year = int(start[:4])
        print(f"  {year}...", end=" ", flush=True)

        try:
            df = load_daily(start, end)
            if df.empty:
                print("NO DATA")
                continue

            # 1. Fixed size (no filter)
            eq_fixed, tr_fixed = _run_no_regime(func, df, CUTOFF, MR_PARAMS)
            m_fixed = compute_metrics(eq_fixed, tr_fixed, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

            # 2. Binary filter
            det = RegimeDetector(df, method="combined", timeframe="1d")
            regimes = det.get_regimes()
            eq_bin, tr_bin = _run_with_regime(func, df, CUTOFF, MR_PARAMS, regimes, allowed)
            m_bin = compute_metrics(eq_bin, tr_bin, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

            # 3. Confidence-weighted position sizing
            eq_conf = _run_with_confidence_sizing(
                func, df, CUTOFF, MR_PARAMS, det, "mean_reversion", allowed,
            )
            m_conf = compute_metrics(eq_conf, [], bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

            print(
                f"Fixed: Net={m_fixed['total_return_pct']:+.2f}%, MaxDD={m_fixed['max_dd_pct']:.1f}% | "
                f"Binary: Net={m_bin['total_return_pct']:+.2f}%, MaxDD={m_bin['max_dd_pct']:.1f}% | "
                f"Confidence: Net={m_conf['total_return_pct']:+.2f}%, MaxDD={m_conf['max_dd_pct']:.1f}%"
            )

            all_rows.append({
                "Year": year,
                "Fixed_Net%": round(m_fixed["total_return_pct"], 2),
                "Fixed_MaxDD%": round(m_fixed["max_dd_pct"], 2),
                "Fixed_Sharpe": round(m_fixed["sharpe"], 3),
                "Fixed_WR%": round(m_fixed["win_rate_pct"], 1),
                "Binary_Net%": round(m_bin["total_return_pct"], 2),
                "Binary_MaxDD%": round(m_bin["max_dd_pct"], 2),
                "Binary_Sharpe": round(m_bin["sharpe"], 3),
                "Binary_WR%": round(m_bin["win_rate_pct"], 1),
                "Confidence_Net%": round(m_conf["total_return_pct"], 2),
                "Confidence_MaxDD%": round(m_conf["max_dd_pct"], 2),
                "Confidence_Sharpe": round(m_conf["sharpe"], 3),
                "Confidence_WR%": round(m_conf.get("win_rate_pct", 0), 1),
            })

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

    df_results = pd.DataFrame(all_rows)
    csv_path = RESULTS_DIR / "mean_reversion_confidence_sizing.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nSaved: {csv_path}")
    return df_results


def _run_with_confidence_sizing(func, df, cutoff, params, detector, strategy_key, allowed_regimes):
    """
    Run strategy and scale equity changes by confidence-based position size.
    When regime is blocked, equity stays flat (same as binary filter).
    When regime is allowed, scale returns by position_size_multiplier.
    """
    eq_base, _ = func(df, cutoff, **params)
    if len(eq_base) == 0:
        return eq_base

    pos_sizes = detector.get_position_size_series(strategy_key)
    regimes = detector.get_regimes()

    scaled = pd.Series(1.0, index=eq_base.index)
    for i in range(1, len(eq_base)):
        if i >= len(pos_sizes):
            scaled.iloc[i] = scaled.iloc[i - 1]
            continue

        regime = regimes.iloc[i] if i < len(regimes) else MarketRegime.RANGING

        # If regime is blocked, equity stays flat
        if allowed_regimes and regime not in allowed_regimes:
            scaled.iloc[i] = scaled.iloc[i - 1]
        else:
            mult = pos_sizes.iloc[i]
            base_return = eq_base.iloc[i] / max(eq_base.iloc[i - 1], 1e-10)
            scaled_return = 1.0 + (base_return - 1.0) * mult
            scaled.iloc[i] = scaled.iloc[i - 1] * scaled_return

    return scaled


# ─────────────────────────────────────────────────────────────────────────────
# TASK 3: Liquidation Capture on 4h with enhanced regime filter
# ─────────────────────────────────────────────────────────────────────────────

def task3_liquidation_4h_enhanced_regime() -> pd.DataFrame:
    """
    Test Liquidation Capture on 4h timeframe with enhanced regime filter.
    Previous test showed it was destroyed in bear markets.
    The enhanced regime filter should skip bear regimes.
    """
    print("\n" + "=" * 80)
    print("TASK 3: LIQUIDATION CAPTURE ON 4h WITH ENHANCED REGIME FILTER")
    print("=" * 80)

    func = run_liquidation_capture
    params = LC_4H_PARAMS
    allowed_regimes = [MarketRegime.RANGING, MarketRegime.BULL_WEAK, MarketRegime.BULL_STRONG]

    all_rows = []

    for start, end in YEAR_RANGES:
        year = int(start[:4])
        print(f"  {year}...", end=" ", flush=True)

        try:
            df = load_4h(start, end)
            if df.empty:
                print("NO DATA")
                for filt in ["no_filter", "enhanced"]:
                    all_rows.append({
                        "Year": year, "Filter": filt,
                        "Trades": 0, "WR%": 0.0, "PF": 0.0,
                        "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                    })
                continue

            # No filter
            eq_nf, tr_nf = _run_no_regime(func, df, CUTOFF, params)
            m_nf = compute_metrics(eq_nf, tr_nf, bars_per_year=INTERVAL_BARS_PER_YEAR["4h"])

            # Enhanced filter
            det = RegimeDetector(df, method="combined", timeframe="4h")
            regimes = det.get_regimes()
            eq_ef, tr_ef = _run_with_regime(func, df, CUTOFF, params, regimes, allowed_regimes)
            m_ef = compute_metrics(eq_ef, tr_ef, bars_per_year=INTERVAL_BARS_PER_YEAR["4h"])

            print(
                f"NoFilter: Net={m_nf['total_return_pct']:+.2f}% | "
                f"Enhanced: Net={m_ef['total_return_pct']:+.2f}%"
            )

            for filt, m in [("no_filter", m_nf), ("enhanced", m_ef)]:
                all_rows.append({
                    "Year": year, "Filter": filt,
                    "Trades": m["n_trades"],
                    "WR%": round(m["win_rate_pct"], 1),
                    "PF": round(m["profit_factor"], 3),
                    "Net%": round(m["total_return_pct"], 2),
                    "Sharpe": round(m["sharpe"], 3),
                    "MaxDD%": round(m["max_dd_pct"], 2),
                })

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
            for filt in ["no_filter", "enhanced"]:
                all_rows.append({
                    "Year": year, "Filter": filt,
                    "Trades": 0, "WR%": 0.0, "PF": 0.0,
                    "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                })

    df_results = pd.DataFrame(all_rows)
    csv_path = RESULTS_DIR / "liquidation_4h_enhanced_regime.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nSaved: {csv_path}")
    return df_results


# ─────────────────────────────────────────────────────────────────────────────
# COMPREHENSIVE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def generate_comprehensive_report(
    df_task1: pd.DataFrame,
    df_task2: pd.DataFrame,
    df_task3: pd.DataFrame,
) -> str:
    """Generate the comprehensive markdown report."""
    lines = [
        "# Enhanced Regime Detection — Comprehensive Report",
        "",
        "## Overview",
        "",
        "Three tasks evaluated the enhanced regime detection system:",
        "",
        "1. **Task 1**: Re-test ALL 6 strategies with enhanced regime filter",
        "2. **Task 2**: Mean Reversion with confidence-based position sizing",
        "3. **Task 3**: Liquidation Capture on 4h with enhanced regime filter",
        "",
        "---",
        "",
        "## Task 1: All Strategies with Enhanced Regime Filter",
        "",
        "### Summary Table",
        "",
        "| Strategy | Net% (No Filter) | Net% (Enhanced Filter) | Improvement |",
        "|----------|------------------|------------------------|-------------|",
    ]

    strategy_order = [
        "Mean Reversion", "VWAP Scalping", "Momentum Scalping",
        "Liquidation Capture", "Grid Trading", "MA Crossover",
    ]

    for strat in strategy_order:
        sd = df_task1[df_task1["Strategy"] == strat]
        if sd.empty:
            continue
        nf = sd[sd["Filter"] == "no_filter"]
        ef = sd[sd["Filter"] == "enhanced"]
        if nf.empty or ef.empty:
            continue
        avg_nf = nf["Net%"].mean()
        avg_ef = ef["Net%"].mean()
        improvement = avg_ef - avg_nf
        lines.append(
            f"| {strat} | {avg_nf:+.2f}% | {avg_ef:+.2f}% | {improvement:+.2f}% |"
        )

    lines.append("")

    # Per-strategy yearly breakdown
    for strat in strategy_order:
        sd = df_task1[df_task1["Strategy"] == strat]
        if sd.empty:
            continue
        lines.extend([
            f"### {strat} — Yearly Breakdown",
            "",
            "| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |",
            "|------|--------|--------|-----|-----|------|--------|--------|",
        ])
        for _, row in sd.iterrows():
            lines.append(
                f"| {int(row['Year'])} | {row['Filter']} | "
                f"{int(row['Trades'])} | {row['WR%']:.1f} | "
                f"{row['PF']:.3f} | {row['Net%']:+.2f} | "
                f"{row['Sharpe']:.3f} | {row['MaxDD%']:.2f} |"
            )
        lines.append("")

        # Profitable years count
        nf_years = sd[(sd["Filter"] == "no_filter") & (sd["Net%"] > 0)]
        ef_years = sd[(sd["Filter"] == "enhanced") & (sd["Net%"] > 0)]
        total_years = len(YEAR_RANGES)
        lines.append(
            f"**Profitable Years**: No Filter: {len(nf_years)}/{total_years} → "
            f"Enhanced: {len(ef_years)}/{total_years}"
        )
        lines.append("")

    lines.extend([
        "---",
        "",
        "## Task 2: Mean Reversion — Confidence-Based Position Sizing",
        "",
        "### Comparison",
        "",
        "| Method | Avg Return | Avg MaxDD | Avg Sharpe | Avg Win Rate |",
        "|--------|------------|-----------|------------|--------------|",
    ])

    if not df_task2.empty:
        avg_fixed = df_task2["Fixed_Net%"].mean()
        avg_fixed_dd = df_task2["Fixed_MaxDD%"].mean()
        avg_fixed_sh = df_task2["Fixed_Sharpe"].mean()
        avg_fixed_wr = df_task2["Fixed_WR%"].mean()

        avg_bin = df_task2["Binary_Net%"].mean()
        avg_bin_dd = df_task2["Binary_MaxDD%"].mean()
        avg_bin_sh = df_task2["Binary_Sharpe"].mean()
        avg_bin_wr = df_task2["Binary_WR%"].mean()

        avg_conf = df_task2["Confidence_Net%"].mean()
        avg_conf_dd = df_task2["Confidence_MaxDD%"].mean()
        avg_conf_sh = df_task2["Confidence_Sharpe"].mean()
        avg_conf_wr = df_task2["Confidence_WR%"].mean()

        lines.append(
            f"| Fixed Size | {avg_fixed:+.2f}% | {avg_fixed_dd:.1f}% | "
            f"{avg_fixed_sh:.3f} | {avg_fixed_wr:.1f}% |"
        )
        lines.append(
            f"| Binary Filter | {avg_bin:+.2f}% | {avg_bin_dd:.1f}% | "
            f"{avg_bin_sh:.3f} | {avg_bin_wr:.1f}% |"
        )
        lines.append(
            f"| Confidence-Weighted | {avg_conf:+.2f}% | {avg_conf_dd:.1f}% | "
            f"{avg_conf_sh:.3f} | {avg_conf_wr:.1f}% |"
        )

        lines.append("")
        lines.append("### Yearly Breakdown")
        lines.append("")
        lines.append(
            "| Year | Fixed Net% | Binary Net% | Confidence Net% | "
            "Fixed MaxDD% | Binary MaxDD% | Confidence MaxDD% |"
        )
        lines.append(
            "|------|------------|-------------|-----------------|"
            "-------------|---------------|---------------------|"
        )
        for _, row in df_task2.iterrows():
            lines.append(
                f"| {int(row['Year'])} | {row['Fixed_Net%']:+.2f}% | "
                f"{row['Binary_Net%']:+.2f}% | {row['Confidence_Net%']:+.2f}% | "
                f"{row['Fixed_MaxDD%']:.2f}% | {row['Binary_MaxDD%']:.2f}% | "
                f"{row['Confidence_MaxDD%']:.2f}% |"
            )

    lines.extend([
        "",
        "---",
        "",
        "## Task 3: Liquidation Capture on 4h with Enhanced Regime Filter",
        "",
        "### Yearly Comparison",
        "",
        "| Year | Net% (No Filter) | Net% (With Filter) | Trades (No Filter) | Trades (With Filter) |",
        "|------|------------------|--------------------|--------------------|----------------------|",
    ])

    if not df_task3.empty:
        for year in range(2018, 2025):
            yd = df_task3[df_task3["Year"] == year]
            if yd.empty:
                continue
            nf_row = yd[yd["Filter"] == "no_filter"]
            ef_row = yd[yd["Filter"] == "enhanced"]
            nf_net = nf_row["Net%"].iloc[0] if not nf_row.empty else 0.0
            ef_net = ef_row["Net%"].iloc[0] if not ef_row.empty else 0.0
            nf_tr = int(nf_row["Trades"].iloc[0]) if not nf_row.empty else 0
            ef_tr = int(ef_row["Trades"].iloc[0]) if not ef_row.empty else 0
            lines.append(
                f"| {year} | {nf_net:+.2f}% | {ef_net:+.2f}% | {nf_tr} | {ef_tr} |"
            )

        # Summary
        nf_total = df_task3[df_task3["Filter"] == "no_filter"]["Net%"].sum()
        ef_total = df_task3[df_task3["Filter"] == "enhanced"]["Net%"].sum()
        lines.append("")
        lines.append(f"**Total Net% (No Filter)**: {nf_total:+.2f}%")
        lines.append(f"**Total Net% (With Filter)**: {ef_total:+.2f}%")
        lines.append(f"**Improvement**: {ef_total - nf_total:+.2f}%")

    lines.extend([
        "",
        "---",
        "",
        "## Conclusions",
        "",
    ])

    # Auto-generate conclusions based on results
    if not df_task1.empty:
        lines.append("### Task 1 Conclusions")
        lines.append("")
        for strat in strategy_order:
            sd = df_task1[df_task1["Strategy"] == strat]
            if sd.empty:
                continue
            nf = sd[sd["Filter"] == "no_filter"]
            ef = sd[sd["Filter"] == "enhanced"]
            if nf.empty or ef.empty:
                continue
            avg_nf = nf["Net%"].mean()
            avg_ef = ef["Net%"].mean()
            if avg_ef > avg_nf:
                lines.append(
                    f"- **{strat}**: Enhanced filter improved returns "
                    f"({avg_nf:+.2f}% → {avg_ef:+.2f}%)"
                )
            else:
                lines.append(
                    f"- **{strat}**: Enhanced filter reduced returns "
                    f"({avg_nf:+.2f}% → {avg_ef:+.2f}%), but may have reduced risk"
                )
        lines.append("")

    if not df_task2.empty:
        lines.append("### Task 2 Conclusions")
        lines.append("")
        avg_conf = df_task2["Confidence_Net%"].mean()
        avg_fixed = df_task2["Fixed_Net%"].mean()
        avg_bin = df_task2["Binary_Net%"].mean()
        avg_conf_dd = df_task2["Confidence_MaxDD%"].mean()
        avg_fixed_dd = df_task2["Fixed_MaxDD%"].mean()
        avg_bin_dd = df_task2["Binary_MaxDD%"].mean()

        lines.append(
            f"- Confidence-weighted sizing returned {avg_conf:+.2f}% vs "
            f"fixed {avg_fixed:+.2f}% and binary {avg_bin:+.2f}%"
        )
        lines.append(
            f"- MaxDD: confidence {avg_conf_dd:.1f}% vs fixed {avg_fixed_dd:.1f}% "
            f"vs binary {avg_bin_dd:.1f}%"
        )
        if avg_conf_dd < avg_fixed_dd:
            lines.append(
                f"- Confidence sizing reduced max drawdown by "
                f"{avg_fixed_dd - avg_conf_dd:.1f}pp vs fixed size"
            )
        lines.append("")

    if not df_task3.empty:
        lines.append("### Task 3 Conclusions")
        lines.append("")
        nf_total = df_task3[df_task3["Filter"] == "no_filter"]["Net%"].sum()
        ef_total = df_task3[df_task3["Filter"] == "enhanced"]["Net%"].sum()
        lines.append(
            f"- Total Net% improved from {nf_total:+.2f}% (no filter) to "
            f"{ef_total:+.2f}% (enhanced filter)"
        )
        # Check bear years specifically
        for year in [2018, 2022]:
            yd = df_task3[df_task3["Year"] == year]
            if yd.empty:
                continue
            nf_row = yd[yd["Filter"] == "no_filter"]
            ef_row = yd[yd["Filter"] == "enhanced"]
            if not nf_row.empty and not ef_row.empty:
                nf_net = nf_row["Net%"].iloc[0]
                ef_net = ef_row["Net%"].iloc[0]
                lines.append(
                    f"- {year} (bear market): {nf_net:+.2f}% → {ef_net:+.2f}% "
                    f"({'filter skipped trades' if ef_net >= 0 and nf_net < 0 else 'filter applied'})"
                )
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("ENHANCED REGIME DETECTION — NEXT STEPS")
    print("=" * 80)

    # Task 1
    df_task1 = task1_all_strategies_enhanced_regime()

    # Task 2
    df_task2 = task2_mean_reversion_confidence_sizing()

    # Task 3
    df_task3 = task3_liquidation_4h_enhanced_regime()

    # Generate report
    print("\n" + "=" * 80)
    print("GENERATING COMPREHENSIVE REPORT")
    print("=" * 80)

    report = generate_comprehensive_report(df_task1, df_task2, df_task3)
    report_path = RESULTS_DIR / "enhanced_regime_comprehensive_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report saved to {report_path}")

    # Print summary tables
    print("\n" + "=" * 80)
    print("TASK 1 SUMMARY: All Strategies with Enhanced Regime Filter")
    print("=" * 80)
    print(f"{'Strategy':<25} | {'No Filter':>10} | {'Enhanced':>10} | {'Improvement':>12}")
    print("-" * 70)
    for strat in [
        "Mean Reversion", "VWAP Scalping", "Momentum Scalping",
        "Liquidation Capture", "Grid Trading", "MA Crossover",
    ]:
        sd = df_task1[df_task1["Strategy"] == strat]
        if sd.empty:
            continue
        nf = sd[sd["Filter"] == "no_filter"]
        ef = sd[sd["Filter"] == "enhanced"]
        if nf.empty or ef.empty:
            continue
        avg_nf = nf["Net%"].mean()
        avg_ef = ef["Net%"].mean()
        imp = avg_ef - avg_nf
        print(f"{strat:<25} | {avg_nf:+10.2f}% | {avg_ef:+10.2f}% | {imp:+12.2f}%")

    print("\n" + "=" * 80)
    print("TASK 2 SUMMARY: Mean Reversion Position Sizing")
    print("=" * 80)
    if not df_task2.empty:
        print(f"{'Method':<25} | {'Avg Return':>10} | {'Avg MaxDD':>10} | {'Avg Sharpe':>10} | {'Avg WR':>8}")
        print("-" * 75)
        avg_f = df_task2["Fixed_Net%"].mean()
        avg_b = df_task2["Binary_Net%"].mean()
        avg_c = df_task2["Confidence_Net%"].mean()
        avg_f_dd = df_task2["Fixed_MaxDD%"].mean()
        avg_b_dd = df_task2["Binary_MaxDD%"].mean()
        avg_c_dd = df_task2["Confidence_MaxDD%"].mean()
        avg_f_sh = df_task2["Fixed_Sharpe"].mean()
        avg_b_sh = df_task2["Binary_Sharpe"].mean()
        avg_c_sh = df_task2["Confidence_Sharpe"].mean()
        avg_f_wr = df_task2["Fixed_WR%"].mean()
        avg_b_wr = df_task2["Binary_WR%"].mean()
        avg_c_wr = df_task2["Confidence_WR%"].mean()
        print(f"{'Fixed Size':<25} | {avg_f:+10.2f}% | {avg_f_dd:>9.1f}% | {avg_f_sh:>10.3f} | {avg_f_wr:>7.1f}%")
        print(f"{'Binary Filter':<25} | {avg_b:+10.2f}% | {avg_b_dd:>9.1f}% | {avg_b_sh:>10.3f} | {avg_b_wr:>7.1f}%")
        print(f"{'Confidence-Weighted':<25} | {avg_c:+10.2f}% | {avg_c_dd:>9.1f}% | {avg_c_sh:>10.3f} | {avg_c_wr:>7.1f}%")

    print("\n" + "=" * 80)
    print("TASK 3 SUMMARY: Liquidation Capture 4h")
    print("=" * 80)
    if not df_task3.empty:
        print(f"{'Year':<6} | {'No Filter':>10} | {'With Filter':>12} | {'Trades NF':>10} | {'Trades EF':>10}")
        print("-" * 60)
        for year in range(2018, 2025):
            yd = df_task3[df_task3["Year"] == year]
            if yd.empty:
                continue
            nf_row = yd[yd["Filter"] == "no_filter"]
            ef_row = yd[yd["Filter"] == "enhanced"]
            nf_net = nf_row["Net%"].iloc[0] if not nf_row.empty else 0.0
            ef_net = ef_row["Net%"].iloc[0] if not ef_row.empty else 0.0
            nf_tr = int(nf_row["Trades"].iloc[0]) if not nf_row.empty else 0
            ef_tr = int(ef_row["Trades"].iloc[0]) if not ef_row.empty else 0
            print(f"{year:<6} | {nf_net:+10.2f}% | {ef_net:+12.2f}% | {nf_tr:>10} | {ef_tr:>10}")


if __name__ == "__main__":
    main()
