"""
test_regime_filter.py
=====================
Test the universal regime detection system across ALL strategies.

Compares each strategy with and without regime filtering to measure
performance improvement from regime-aware trading.

Data: BTCUSDT 2018-2024 (daily for most, 4h for grid, 15m for momentum)

Saves:
  - BTV2/results/regime_detection_accuracy.csv
  - BTV2/results/strategies_with_regime_filter.csv
  - BTV2/results/universal_regime_filter_report.md
"""

import sys
import math
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

DATA_DIR = Path("G:/Candle Data")
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

# Known market character for regime accuracy validation
KNOWN_MARKET_CHARACTER = {
    2018: ("Bear market (-73%)", MarketRegime.BEAR_STRONG),
    2019: ("Recovery (+95%)",    MarketRegime.BULL_WEAK),
    2020: ("Bull run (+300%)",   MarketRegime.BULL_STRONG),
    2021: ("Peak + crash",       MarketRegime.BULL_STRONG),
    2022: ("Bear market (-65%)", MarketRegime.BEAR_STRONG),
    2023: ("Recovery (+155%)",   MarketRegime.BULL_WEAK),
    2024: ("New ATH (+120%)",    MarketRegime.BULL_STRONG),
}


def load_1d_data(start: str, end: str) -> pd.DataFrame:
    path = DATA_DIR / "BTCUSDT_1d.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        sliced = df.loc[mask].copy()
        if not sliced.empty:
            return sliced
    return _load_and_resample("5m", "1D", start, end)


def load_4h_data(start: str, end: str) -> pd.DataFrame:
    path = DATA_DIR / "BTCUSDT_4h.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        sliced = df.loc[mask].copy()
        if not sliced.empty:
            return sliced
    return _load_and_resample("5m", "240min", start, end)


def load_15m_data(start: str, end: str) -> pd.DataFrame:
    path = DATA_DIR / "BTCUSDT_15m.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        sliced = df.loc[mask].copy()
        if not sliced.empty:
            return sliced
    return _load_and_resample("5m", "15min", start, end)


def _load_and_resample(source_interval: str, rule: str, start: str, end: str) -> pd.DataFrame:
    path = DATA_DIR / f"BTCUSDT_{source_interval}.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    sliced = df.loc[mask].copy()
    if sliced.empty:
        return sliced
    return _resample_ohlcv(sliced, rule)


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY CONFIGURATIONS (best params from previous tests)
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

LC_PARAMS = {
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


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY FUNCTION MAP
# ─────────────────────────────────────────────────────────────────────────────

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
# TEST: REGIME DETECTION ACCURACY
# ─────────────────────────────────────────────────────────────────────────────

def test_regime_detection_accuracy() -> list[dict]:
    """
    Test regime detection accuracy against known market character.
    """
    print("\n" + "=" * 80)
    print("REGIME DETECTION ACCURACY TEST")
    print("=" * 80)

    results = []

    for start, end in YEAR_RANGES:
        year = int(start[:4])
        print(f"\n  Year {year}...", end=" ", flush=True)

        try:
            df = load_1d_data(start, end)
            if df.empty:
                print("NO DATA")
                continue

            detector = RegimeDetector(df, method="combined")
            regimes = detector.get_regimes()
            distribution = detector.get_regime_distribution()
            periods = detector.get_regime_periods()

            # Dominant regime = most frequent
            dominant = regimes.mode().iloc[0] if len(regimes) > 0 else MarketRegime.RANGING
            expected_desc, expected_regime = KNOWN_MARKET_CHARACTER[year]

            # Check accuracy: dominant regime matches expected
            def regimes_match(detected: MarketRegime, expected: MarketRegime) -> bool:
                if expected == MarketRegime.BULL_STRONG:
                    return detected in (MarketRegime.BULL_STRONG, MarketRegime.BULL_WEAK)
                elif expected == MarketRegime.BEAR_STRONG:
                    return detected in (MarketRegime.BEAR_STRONG, MarketRegime.BEAR_WEAK)
                elif expected == MarketRegime.BULL_WEAK:
                    return detected in (MarketRegime.BULL_WEAK, MarketRegime.RANGING)
                elif expected == MarketRegime.RANGING:
                    return detected == MarketRegime.RANGING
                return detected == expected

            accurate = regimes_match(dominant, expected_regime)

            check = "OK" if accurate else "FAIL"
            print(
                f"Dominant={dominant.value}, "
                f"Expected~={expected_regime.value}, "
                f"Accuracy={check}"
            )

            results.append({
                "Year": year,
                "Detected_Regime": dominant.value,
                "Expected_Market": expected_desc,
                "Expected_Regime": expected_regime.value,
                "Accurate": accurate,
                "Distribution": str(distribution),
                "Num_Periods": len(periods),
            })

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

    return results


# ─────────────────────────────────────────────────────────────────────────────
# TEST: STRATEGY PERFORMANCE WITH/WITHOUT REGIME FILTER
# ─────────────────────────────────────────────────────────────────────────────

def test_strategy_with_regime_filter(
    strategy_name: str,
    load_func,
    params: dict,
    bars_per_year: int,
) -> list[dict]:
    """
    Run a strategy across all years, with and without regime filtering.
    """
    print(f"\n--- {strategy_name} ---")
    results = []

    allowed_regimes = STRATEGY_REGIME_COMPATIBILITY.get(strategy_name, [])

    for start, end in YEAR_RANGES:
        year_label = start[:4]
        print(f"  {year_label}...", end=" ", flush=True)

        try:
            df = load_func(start, end)
            if df.empty:
                print("NO DATA")
                for filt in ["none", "regime"]:
                    results.append({
                        "Strategy": strategy_name,
                        "Year": int(year_label),
                        "Filter": filt,
                        "Trades": 0, "WR%": 0.0, "PF": 0.0,
                        "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                    })
                continue

            # Without regime filter
            func = _get_strategy_func(strategy_name)
            eq_no, trd_no = func(df, CUTOFF, **params)
            m_no = compute_metrics(eq_no, trd_no, bars_per_year=bars_per_year)

            # With regime filter: pass regime_series + allowed_regimes
            detector = RegimeDetector(df, method="combined")
            regime_series = detector.get_regimes()
            eq_rf, trd_rf = func(
                df, CUTOFF,
                regime_series=regime_series,
                allowed_regimes=allowed_regimes,
                **params,
            )
            m_rf = compute_metrics(eq_rf, trd_rf, bars_per_year=bars_per_year)

            print(
                f"NoFilter: Net={m_no['total_return_pct']:.2f}%, "
                f"Trades={m_no['n_trades']}, "
                f"MaxDD={m_no['max_dd_pct']:.2f}% | "
                f"RegimeFilter: Net={m_rf['total_return_pct']:.2f}%, "
                f"Trades={m_rf['n_trades']}, "
                f"MaxDD={m_rf['max_dd_pct']:.2f}%"
            )

            for filt, m in [("none", m_no), ("regime", m_rf)]:
                results.append({
                    "Strategy": strategy_name,
                    "Year": int(year_label),
                    "Filter": filt,
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
            for filt in ["none", "regime"]:
                results.append({
                    "Strategy": strategy_name,
                    "Year": int(year_label),
                    "Filter": filt,
                    "Trades": 0, "WR%": 0.0, "PF": 0.0,
                    "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("UNIVERSAL REGIME FILTER TEST - BTV2")
    print("=" * 80)

    # Task 1: Regime Detection Accuracy
    regime_accuracy = test_regime_detection_accuracy()

    # Save regime accuracy CSV
    df_accuracy = pd.DataFrame(regime_accuracy)
    accuracy_csv = RESULTS_DIR / "regime_detection_accuracy.csv"
    df_accuracy.to_csv(accuracy_csv, index=False)
    print(f"\nRegime accuracy saved to {accuracy_csv}")

    # Task 2: Strategy Performance With/Without Regime Filter
    all_results = []

    strategies = [
        ("Mean Reversion",      load_1d_data, MR_PARAMS, INTERVAL_BARS_PER_YEAR["1d"]),
        ("VWAP Scalping",       load_15m_data, VS_PARAMS, INTERVAL_BARS_PER_YEAR["15m"]),
        ("Momentum Scalping",   load_15m_data, MS_PARAMS, INTERVAL_BARS_PER_YEAR["15m"]),
        ("Liquidation Capture", load_1d_data, LC_PARAMS, INTERVAL_BARS_PER_YEAR["1d"]),
        ("Grid Trading",        load_4h_data, GT_PARAMS, INTERVAL_BARS_PER_YEAR["4h"]),
        ("MA Crossover",        load_1d_data, MA_PARAMS, INTERVAL_BARS_PER_YEAR["1d"]),
    ]

    for name, load_fn, params, bpy in strategies:
        strat_results = test_strategy_with_regime_filter(name, load_fn, params, bpy)
        all_results.extend(strat_results)

    # Save strategy results CSV
    df_results = pd.DataFrame(all_results)
    results_csv = RESULTS_DIR / "strategies_with_regime_filter.csv"
    df_results.to_csv(results_csv, index=False)
    print(f"\nStrategy results saved to {results_csv}")

    # Task 3: Generate Report
    report = generate_report(regime_accuracy, df_results, df_accuracy)
    report_path = RESULTS_DIR / "universal_regime_filter_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report saved to {report_path}")

    # Print Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    strategy_order = [
        "Mean Reversion", "VWAP Scalping", "Momentum Scalping",
        "Liquidation Capture", "Grid Trading", "MA Crossover",
    ]

    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue

        no_filter = strat_data[strat_data["Filter"] == "none"]
        rf_filter = strat_data[strat_data["Filter"] == "regime"]

        avg_net_no = no_filter["Net%"].mean() if len(no_filter) > 0 else 0
        avg_net_rf = rf_filter["Net%"].mean() if len(rf_filter) > 0 else 0
        avg_dd_no  = no_filter["MaxDD%"].mean() if len(no_filter) > 0 else 0
        avg_dd_rf  = rf_filter["MaxDD%"].mean() if len(rf_filter) > 0 else 0
        improvement = avg_net_rf - avg_net_no
        dd_improvement = avg_dd_rf - avg_dd_no

        print(
            f"{strat:<25} | NoFilter Net: {avg_net_no:+7.2f}% | "
            f"RegimeFilter Net: {avg_net_rf:+7.2f}% | "
            f"Improvement: {improvement:+7.2f}% | "
            f"DD: {avg_dd_no:+6.2f}% -> {avg_dd_rf:+6.2f}% ({dd_improvement:+6.2f}%)"
        )


def generate_report(
    regime_accuracy: list[dict],
    df_results: pd.DataFrame,
    df_accuracy: pd.DataFrame,
) -> str:
    """Generate the markdown report."""
    lines = [
        "# Universal Regime Filter Report",
        "",
        "## Overview",
        "",
        "This report evaluates the universal regime detection system across all",
        "BTV2 strategies. The regime detector identifies market conditions and",
        "filters out trades in regimes unsuitable for each strategy.",
        "",
        "## Regime Detection Accuracy",
        "",
        "| Year | Detected Regime | Actual Market | Accuracy |",
        "|------|-----------------|---------------|----------|",
    ]

    for row in regime_accuracy:
        icon = "PASS" if row["Accurate"] else "FAIL"
        lines.append(
            f"| {row['Year']} | {row['Detected_Regime']} | "
            f"{row['Expected_Market']} | {icon} |"
        )

    lines.append("")

    # Regime distribution summary
    lines.extend([
        "### Regime Distribution by Year",
        "",
    ])
    for row in regime_accuracy:
        lines.append(f"**{row['Year']}**: {row['Distribution']}")
    lines.append("")

    # Strategy compatibility matrix
    lines.extend([
        "## Strategy Regime Compatibility",
        "",
        "| Strategy | Allowed Regimes |",
        "|----------|----------------|",
    ])
    for strat, regimes in STRATEGY_REGIME_COMPATIBILITY.items():
        regime_names = ", ".join(r.value for r in regimes)
        lines.append(f"| {strat} | {regime_names} |")
    lines.append("")

    # Per-strategy comparison tables
    strategy_order = [
        "Mean Reversion", "VWAP Scalping", "Momentum Scalping",
        "Liquidation Capture", "Grid Trading", "MA Crossover",
    ]

    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue

        lines.extend([
            f"## {strat}",
            "",
            "| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |",
            "|------|--------|--------|-----|-----|------|--------|--------|",
        ])

        for _, row in strat_data.iterrows():
            lines.append(
                f"| {int(row['Year'])} | {row['Filter']} | "
                f"{int(row['Trades'])} | {row['WR%']:.1f} | "
                f"{row['PF']:.3f} | {row['Net%']:.2f} | "
                f"{row['Sharpe']:.3f} | {row['MaxDD%']:.2f} |"
            )

        lines.append("")

        # Summary for this strategy
        no_filter = strat_data[strat_data["Filter"] == "none"]
        rf_filter = strat_data[strat_data["Filter"] == "regime"]

        if len(no_filter) > 0 and len(rf_filter) > 0:
            avg_net_no = no_filter["Net%"].mean()
            avg_net_rf = rf_filter["Net%"].mean()
            avg_dd_no  = no_filter["MaxDD%"].mean()
            avg_dd_rf  = rf_filter["MaxDD%"].mean()
            avg_trades_no = no_filter["Trades"].mean()
            avg_trades_rf = rf_filter["Trades"].mean()

            improvement = avg_net_rf - avg_net_no
            dd_improvement = avg_dd_rf - avg_dd_no

            lines.extend([
                f"### {strat} - Summary",
                "",
                f"- **Avg Net Return (no filter)**: {avg_net_no:.2f}%",
                f"- **Avg Net Return (regime filter)**: {avg_net_rf:.2f}%",
                f"- **Improvement**: {improvement:+.2f}%",
                f"- **Avg Max Drawdown (no filter)**: {avg_dd_no:.2f}%",
                f"- **Avg Max Drawdown (regime filter)**: {avg_dd_rf:.2f}%",
                f"- **Drawdown Improvement**: {dd_improvement:+.2f}%",
                f"- **Avg Trades (no filter)**: {avg_trades_no:.1f}",
                f"- **Avg Trades (regime filter)**: {avg_trades_rf:.1f}",
                f"- **Trade Reduction**: {(1 - avg_trades_rf / max(avg_trades_no, 1)) * 100:.1f}%",
                "",
            ])

    # Overall summary table
    lines.extend([
        "## Overall Summary: All Strategies",
        "",
        "| Strategy | Net% (No Filter) | Net% (With Filter) | Improvement | "
        "MaxDD% (No Filter) | MaxDD% (With Filter) | DD Improvement |",
        "|----------|------------------|--------------------|-------------|"
        "--------------------|----------------------|----------------|",
    ])

    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue

        no_filter = strat_data[strat_data["Filter"] == "none"]
        rf_filter = strat_data[strat_data["Filter"] == "regime"]

        if len(no_filter) > 0 and len(rf_filter) > 0:
            avg_net_no = no_filter["Net%"].mean()
            avg_net_rf = rf_filter["Net%"].mean()
            avg_dd_no  = no_filter["MaxDD%"].mean()
            avg_dd_rf  = rf_filter["MaxDD%"].mean()
            improvement = avg_net_rf - avg_net_no
            dd_improvement = avg_dd_rf - avg_dd_no

            lines.append(
                f"| {strat} | {avg_net_no:.2f} | {avg_net_rf:.2f} | "
                f"{improvement:+.2f} | {avg_dd_no:.2f} | {avg_dd_rf:.2f} | "
                f"{dd_improvement:+.2f} |"
            )

    lines.append("")

    # Final verdict
    lines.extend([
        "## Final Verdict",
        "",
    ])

    # Count strategies improved by regime filter
    improved_count = 0
    total_count = 0
    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue
        no_filter = strat_data[strat_data["Filter"] == "none"]
        rf_filter = strat_data[strat_data["Filter"] == "regime"]
        if len(no_filter) > 0 and len(rf_filter) > 0:
            total_count += 1
            if rf_filter["Net%"].mean() > no_filter["Net%"].mean():
                improved_count += 1

    lines.append(
        f"**{improved_count}/{total_count} strategies improved by regime filtering.**"
    )
    lines.append("")

    # Key findings
    lines.extend([
        "### Key Findings",
        "",
    ])

    # Find the strategy with the biggest improvement
    improvements = {}
    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue
        no_filter = strat_data[strat_data["Filter"] == "none"]
        rf_filter = strat_data[strat_data["Filter"] == "regime"]
        if len(no_filter) > 0 and len(rf_filter) > 0:
            improvements[strat] = rf_filter["Net%"].mean() - no_filter["Net%"].mean()

    if improvements:
        best_improvement = max(improvements, key=improvements.get)
        worst_improvement = min(improvements, key=improvements.get)
        lines.append(
            f"- **Biggest improvement**: {best_improvement} "
            f"({improvements[best_improvement]:+.2f}%)"
        )
        lines.append(
            f"- **Least improvement**: {worst_improvement} "
            f"({improvements[worst_improvement]:+.2f}%)"
        )

    lines.append("")
    lines.append("### Recommendations")
    lines.append("")
    lines.append(
        "1. Regime filtering is most beneficial for strategies that trade in "
        "specific market conditions (e.g., mean reversion in ranging markets)."
    )
    lines.append(
        "2. Strategies that already have built-in regime filters (e.g., VWAP "
        "Scalping with ADX gate) see less additional benefit."
    )
    lines.append(
        "3. The combined detection method provides the most robust regime "
        "classification by weighting ADX, EMA alignment, and volatility."
    )
    lines.append(
        "4. Consider using regime detection as a position sizing multiplier "
        "(reduce size in low-confidence regimes) rather than a binary filter."
    )

    return "\n".join(lines)


if __name__ == "__main__":
    main()
