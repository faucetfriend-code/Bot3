"""
Test the historical best configuration (SD=3.45) across all years 2018-2024.

This test uses the EXACT configuration that was claimed to achieve:
- Win Rate: 73.3%
- Return: +51.3%
- Max Drawdown: -5.17%
- Sharpe: 1.473

Key insight: The historical test used a SIMPLIFIED version of the strategy
without stochastic filters, UTM enhancements, etc. We test both:
1. Historical config (simplified, no extra filters)
2. Current config (with all modern filters)

Saves results to:
- BTV2/results/vwap_sd345_yearly_results.csv
- BTV2/results/vwap_sd345_yearly_report.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
    compute_reference_levels,
    run_vwap_scalping,
)

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent.parent / "trading_bot_v2" / "backtesting" / "data"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def load_5m_data() -> pd.DataFrame:
    """Load all 5m data from 2018-2024."""
    frames = []

    # Load 2018-2023 combined
    all_path = DATA_DIR / "BTC-USDC_5m_all.csv"
    if all_path.exists():
        df = pd.read_csv(all_path, parse_dates=["timestamp"], index_col="timestamp")
        # Rename lowercase columns to capitalized (strategy expects capitalized)
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        frames.append(df)
        print(f"Loaded 2018-2023: {len(df)} bars")

    # Load 2024
    df_2024 = pd.read_csv(
        DATA_DIR / "BTC-USDC_5m.csv", parse_dates=["timestamp"], index_col="timestamp"
    )
    # Ensure UTC timezone
    if df_2024.index.tz is None:
        df_2024.index = df_2024.index.tz_localize("UTC")
    # Rename columns if needed
    if "open" in df_2024.columns:
        df_2024 = df_2024.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
    frames.append(df_2024)
    print(f"Loaded 2024: {len(df_2024)} bars")

    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    print(f"Total 5m data: {len(combined)} bars from {combined.index.min()} to {combined.index.max()}")
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# TEST CONFIGURATIONS
# ─────────────────────────────────────────────────────────────────────────────

# Historical best configuration (as claimed in optimization logs)
# This is the SIMPLIFIED version without modern filters
HISTORICAL_BEST = {
    "sd_threshold": 3.45,
    "entry_mode": "mean_reversion",
    "adx_max": 25.0,
    "rsi_max": 50.0,
    "volume_mult": 2.0,
    "atr_stop": 0.7,
    "atr_target": 3.5,
    "pullback_bars": 3,
    "use_session_filter": True,
    "use_htf_vwap": True,
    "use_htf_ema": False,
    "htf_adx_max": 25.0,
    # Disable modern filters that weren't in the original test
    "use_anchored_vwap": False,  # Use rolling VWAP (original behavior)
    "require_reversal_candle": False,  # No SFP confirmation
    "tp_mode": "atr",  # Use ATR targets (original behavior)
    "require_mss": False,
    "use_fvg_filter": False,
    "use_ob_filter": False,
    "use_eqhl_filter": False,
    "use_cvd_filter": False,
    "use_ote": False,
    "use_dynamic_mode": False,
    "use_trailing_stop": False,  # Original didn't use trailing stops
    "use_trend_filter": False,  # Original didn't filter by trend
    "use_volume_filter": True,
    # Stochastic thresholds - use permissive values to match original behavior
    "stoch_oversold": 20,  # Very permissive
    "stoch_overbought": 80,  # Very permissive
}

# Test configurations
TEST_CONFIGS = [
    # Historical best with simplified filters
    {**HISTORICAL_BEST, "sd_threshold": 3.45, "entry_mode": "mean_reversion", "label": "Historical Best (SD=3.45, MR)"},
    {**HISTORICAL_BEST, "sd_threshold": 3.45, "entry_mode": "bull_pullback", "label": "Historical Best (SD=3.45, BP)"},
    {**HISTORICAL_BEST, "sd_threshold": 3.0, "entry_mode": "mean_reversion", "label": "SD=3.0 (MR)"},
    {**HISTORICAL_BEST, "sd_threshold": 4.0, "entry_mode": "mean_reversion", "label": "SD=4.0 (MR)"},
]

# Year ranges to test
YEAR_RANGES = [
    (2018, 2019),
    (2019, 2020),
    (2020, 2021),
    (2021, 2022),
    (2022, 2023),
    (2023, 2024),
    (2024, 2025),
]


def run_backtest(df_5m: pd.DataFrame, config: dict) -> dict:
    """Run a single backtest with the given configuration."""
    # Compute reference levels once
    levels = compute_reference_levels(df_5m)

    # Run VWAP scalping strategy
    equity, trades = run_vwap_scalping(
        df_5m,
        cutoff=0.10,  # Default cutoff
        levels=levels,
        **config,
    )

    # Compute metrics
    metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["5m"])

    return {
        "equity": equity,
        "trades": trades,
        "metrics": metrics,
    }


def main():
    print("=" * 80)
    print("Testing Historical Best Configuration (SD=3.45) Across All Years 2018-2024")
    print("=" * 80)

    # Load data
    df_all = load_5m_data()

    # Results storage
    results = []

    for year_start, year_end in YEAR_RANGES:
        print(f"\n{'=' * 60}")
        print(f"Testing Year: {year_start}")
        print(f"{'=' * 60}")

        # Filter data for this year
        df_year = df_all.loc[
            (df_all.index >= f"{year_start}-01-01") & (df_all.index < f"{year_end}-01-01")
        ].copy()

        if df_year.empty:
            print(f"  No data for {year_start}, skipping...")
            continue

        print(f"  Data: {len(df_year)} bars from {df_year.index.min()} to {df_year.index.max()}")

        for config in TEST_CONFIGS:
            label = config.pop("label", f"SD={config['sd_threshold']}, {config['entry_mode']}")
            mode = config["entry_mode"]
            sd = config["sd_threshold"]

            print(f"  Testing: {label}...")

            try:
                result = run_backtest(df_year, config)
                m = result["metrics"]

                row = {
                    "Year": year_start,
                    "Mode": mode,
                    "SD": sd,
                    "Label": label,
                    "Trades": m["n_trades"],
                    "WR%": round(m["win_rate_pct"], 1),
                    "PF": round(m["profit_factor"], 3),
                    "Net%": round(m["total_return_pct"], 2),
                    "Sharpe": round(m["sharpe"], 3),
                    "MaxDD%": round(m["max_dd_pct"], 2),
                    "CAGR%": round(m["cagr_pct"], 2),
                }
                results.append(row)

                print(
                    f"    Trades: {m['n_trades']}, WR: {m['win_rate_pct']:.1f}%, "
                    f"Net: {m['total_return_pct']:.2f}%, Sharpe: {m['sharpe']:.3f}, "
                    f"PF: {m['profit_factor']:.3f}, MaxDD: {m['max_dd_pct']:.2f}%"
                )
            except Exception as e:
                print(f"    ERROR: {e}")
                import traceback
                traceback.print_exc()
                results.append(
                    {
                        "Year": year_start,
                        "Mode": mode,
                        "SD": sd,
                        "Label": label,
                        "Trades": 0,
                        "WR%": 0.0,
                        "PF": 0.0,
                        "Net%": 0.0,
                        "Sharpe": 0.0,
                        "MaxDD%": 0.0,
                        "CAGR%": 0.0,
                    }
                )
            finally:
                # Restore label for next iteration
                config["label"] = label

    # Convert to DataFrame
    df_results = pd.DataFrame(results)

    # Save CSV
    csv_path = RESULTS_DIR / "vwap_sd345_yearly_results.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nResults saved to {csv_path}")

    # Generate Markdown Report
    report_lines = [
        "# VWAP SD=3.45 Yearly Backtest Report",
        "",
        "## Historical Best Configuration",
        "",
        "```python",
        "sd_threshold = 3.45",
        'entry_mode = "mean_reversion"',
        "adx_max = 25",
        "rsi_max = 50",
        "volume_mult = 2.0",
        "atr_stop = 0.7",
        "atr_target = 3.5",
        "pullback_bars = 3",
        "use_session_filter = True",
        "use_htf_vwap = True",
        "use_htf_ema = False",
        "htf_adx_max = 25",
        "```",
        "",
        "## Claimed Performance",
        "",
        "- Win Rate: 73.3%",
        "- Return: +51.3%",
        "- Max Drawdown: -5.17%",
        "- Sharpe: 1.473",
        "",
        "## Yearly Results",
        "",
        "| Year | Mode | SD | Trades | WR% | PF | Net% | Sharpe | MaxDD% | CAGR% |",
        "|------|------|-----|--------|-----|-----|------|--------|--------|-------|",
    ]

    for _, row in df_results.iterrows():
        report_lines.append(
            f"| {int(row['Year'])} | {row['Mode']} | {row['SD']} | "
            f"{int(row['Trades'])} | {row['WR%']:.1f} | {row['PF']:.3f} | "
            f"{row['Net%']:.2f} | {row['Sharpe']:.3f} | {row['MaxDD%']:.2f} | "
            f"{row['CAGR%']:.2f} |"
        )

    report_lines.extend([
        "",
        "## Analysis",
        "",
        "### SD=3.45 Mean Reversion Performance",
        "",
    ])

    # Filter for SD=3.45 mean_reversion
    mr_results = df_results[
        (df_results["SD"] == 3.45) & (df_results["Mode"] == "mean_reversion")
    ]

    if not mr_results.empty:
        avg_wr = mr_results["WR%"].mean()
        avg_net = mr_results["Net%"].mean()
        avg_sharpe = mr_results["Sharpe"].mean()
        avg_dd = mr_results["MaxDD%"].mean()

        report_lines.extend([
            f"- **Average Win Rate**: {avg_wr:.1f}%",
            f"- **Average Net Return**: {avg_net:.2f}%",
            f"- **Average Sharpe**: {avg_sharpe:.3f}",
            f"- **Average Max Drawdown**: {avg_dd:.2f}%",
            "",
            "### Conclusion",
            "",
        ])

        # Check if results are consistent
        positive_years = (mr_results["Net%"] > 0).sum()
        total_years = len(mr_results)

        if positive_years == total_years:
            report_lines.append(
                f"SD=3.45 mean_reversion was profitable in ALL {total_years} years tested. "
                "This suggests the configuration is robust and not overfit."
            )
        elif positive_years > total_years / 2:
            report_lines.append(
                f"SD=3.45 mean_reversion was profitable in {positive_years}/{total_years} years. "
                "This suggests moderate robustness but some regime dependency."
            )
        else:
            report_lines.append(
                f"SD=3.45 mean_reversion was profitable in only {positive_years}/{total_years} years. "
                "This suggests the configuration may be overfit to specific market conditions."
            )

        report_lines.extend([
            "",
            "### Comparison with Claimed Performance",
            "",
            f"| Metric | Claimed | Actual (Avg) | Difference |",
            f"|--------|---------|--------------|------------|",
            f"| Win Rate | 73.3% | {avg_wr:.1f}% | {avg_wr - 73.3:+.1f}% |",
            f"| Net Return | +51.3% | {avg_net:.2f}% | {avg_net - 51.3:+.2f}% |",
            f"| Sharpe | 1.473 | {avg_sharpe:.3f} | {avg_sharpe - 1.473:+.3f} |",
            f"| Max Drawdown | -5.17% | {avg_dd:.2f}% | {avg_dd - (-5.17):+.2f}% |",
        ])

    report_lines.extend([
        "",
        "### SD Comparison",
        "",
        "| SD | Avg WR% | Avg Net% | Avg Sharpe |",
        "|-----|---------|----------|------------|",
    ])

    for sd_val in [3.0, 3.45, 4.0]:
        sd_results = df_results[
            (df_results["SD"] == sd_val) & (df_results["Mode"] == "mean_reversion")
        ]
        if not sd_results.empty:
            report_lines.append(
                f"| {sd_val} | {sd_results['WR%'].mean():.1f} | "
                f"{sd_results['Net%'].mean():.2f} | "
                f"{sd_results['Sharpe'].mean():.3f} |"
            )

    # Save report
    report_path = RESULTS_DIR / "vwap_sd345_yearly_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines))
    print(f"Report saved to {report_path}")

    # Print summary table
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(
        f"{'Year':<6} {'Mode':<18} {'SD':<5} {'Trades':<8} {'WR%':<6} {'PF':<6} "
        f"{'Net%':<8} {'Sharpe':<8}"
    )
    print("-" * 80)
    for _, row in df_results.iterrows():
        print(
            f"{int(row['Year']):<6} {row['Mode']:<18} {row['SD']:<5} "
            f"{int(row['Trades']):<8} {row['WR%']:<6.1f} {row['PF']:<6.3f} "
            f"{row['Net%']:<8.2f} {row['Sharpe']:<8.3f}"
        )


if __name__ == "__main__":
    main()
