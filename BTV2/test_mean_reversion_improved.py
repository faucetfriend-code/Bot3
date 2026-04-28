"""
test_mean_reversion_improved.py
================================
Test improved Mean Reversion strategy with:
  1. Regime Filter (ADX < 25)
  2. Trailing Stops (1.5 ATR)
  3. Combined improvements
  4. Parameter sweep

Runs year-by-year backtests 2018-2024 on BTC-USDC daily data.

Saves:
  - BTV2/results/mean_reversion_improved_results.csv
  - BTV2/results/mean_reversion_improved_report.md
"""

import sys
from pathlib import Path
from itertools import product

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
    run_mean_reversion,
    _resample_ohlcv,
)

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = 0.10  # Butterworth cutoff (default)

YEAR_RANGES = [
    ("2018-01-01", "2019-01-01"),
    ("2019-01-01", "2020-01-01"),
    ("2020-01-01", "2021-01-01"),
    ("2021-01-01", "2022-01-01"),
    ("2022-01-01", "2023-01-01"),
    ("2023-01-01", "2024-01-01"),
    ("2024-01-01", "2025-01-01"),
]


def load_1d_data(start: str, end: str) -> pd.DataFrame:
    """Load daily data, resample from 5m if native 1d doesn't cover the range."""
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

    # Fallback: resample from 5m
    return _load_and_resample("5m", "1D", start, end)


def _load_and_resample(source_interval: str, rule: str, start: str, end: str) -> pd.DataFrame:
    """Load source interval parquet and resample to target."""
    path = DATA_DIR / f"BTCUSDT_{source_interval}.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    sliced = df.loc[mask].copy()
    if sliced.empty:
        return sliced
    resampled = _resample_ohlcv(sliced, rule)
    return resampled


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATIONS
# ─────────────────────────────────────────────────────────────────────────────

# Baseline (current best params, no improvements)
BASELINE = {
    "rsi_oversold": 25,
    "rsi_overbought": 75,
    "bb_proximity": 0.05,
    "atr_stop": 3.0,
    "use_sfp_entry": False,
    "use_poc_tp": False,
    "use_regime_filter": False,
    "adx_max": 25.0,
    "use_trailing_stop": False,
    "trailing_atr_mult": 1.5,
}

# + Regime Filter only
REGIME_ONLY = {
    **BASELINE,
    "use_regime_filter": True,
    "adx_max": 25.0,
}

# + Trailing Stop only
TRAIL_ONLY = {
    **BASELINE,
    "use_trailing_stop": True,
    "trailing_atr_mult": 1.5,
}

# Both improvements
BOTH = {
    **BASELINE,
    "use_regime_filter": True,
    "adx_max": 25.0,
    "use_trailing_stop": True,
    "trailing_atr_mult": 1.5,
}

CONFIGS = {
    "Baseline": BASELINE,
    "+Regime": REGIME_ONLY,
    "+Trail": TRAIL_ONLY,
    "Both": BOTH,
}


# ─────────────────────────────────────────────────────────────────────────────
# TEST RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_config_yearly(config_name: str, params: dict) -> list[dict]:
    """Run a single configuration across all year ranges."""
    results = []

    for start, end in YEAR_RANGES:
        year_label = start[:4]
        print(f"  [{config_name}] {year_label}...", end=" ", flush=True)

        try:
            df = load_1d_data(start, end)
            if df.empty:
                print(f"NO DATA")
                results.append({
                    "Config": config_name,
                    "Year": int(year_label),
                    "Trades": 0, "WR%": 0.0, "PF": 0.0,
                    "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                })
                continue

            print(f"{len(df)} bars -> ", end="", flush=True)

            equity, trades = run_mean_reversion(df, CUTOFF, **params)
            metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

            row = {
                "Config": config_name,
                "Year": int(year_label),
                "Trades": metrics["n_trades"],
                "WR%": round(metrics["win_rate_pct"], 1),
                "PF": round(metrics["profit_factor"], 3),
                "Net%": round(metrics["total_return_pct"], 2),
                "Sharpe": round(metrics["sharpe"], 3),
                "MaxDD%": round(metrics["max_dd_pct"], 2),
            }
            results.append(row)
            print(
                f"Trades={metrics['n_trades']}, WR={metrics['win_rate_pct']:.1f}%, "
                f"Net={metrics['total_return_pct']:.2f}%, Sharpe={metrics['sharpe']:.3f}, "
                f"PF={metrics['profit_factor']:.3f}, MaxDD={metrics['max_dd_pct']:.2f}%"
            )
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "Config": config_name,
                "Year": int(year_label),
                "Trades": 0, "WR%": 0.0, "PF": 0.0,
                "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
            })

    return results


def run_parameter_sweep() -> list[dict]:
    """
    Test 5: Parameter Sweep
    adx_max: [20, 25, 30]
    trailing_atr_mult: [1.0, 1.5, 2.0]
    rsi_oversold: [20, 25, 30]
    rsi_overbought: [70, 75, 80]
    """
    print("\n--- Parameter Sweep ---")
    results = []

    adx_values = [20, 25, 30]
    trail_values = [1.0, 1.5, 2.0]
    rsi_os_values = [20, 25, 30]
    rsi_ob_values = [70, 75, 80]

    total_combos = len(adx_values) * len(trail_values) * len(rsi_os_values) * len(rsi_ob_values)
    print(f"  Testing {total_combos} combinations (both improvements enabled)...")

    combo_num = 0
    for adx_max, trail_mult, rsi_os, rsi_ob in product(adx_values, trail_values, rsi_os_values, rsi_ob_values):
        combo_num += 1
        params = {
            "rsi_oversold": rsi_os,
            "rsi_overbought": rsi_ob,
            "bb_proximity": 0.05,
            "atr_stop": 3.0,
            "use_sfp_entry": False,
            "use_poc_tp": False,
            "use_regime_filter": True,
            "adx_max": float(adx_max),
            "use_trailing_stop": True,
            "trailing_atr_mult": trail_mult,
        }

        # Run on full 2018-2024 period
        try:
            df = load_1d_data("2018-01-01", "2025-01-01")
            if df.empty:
                continue

            equity, trades = run_mean_reversion(df, CUTOFF, **params)
            metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

            row = {
                "Config": f"adx={adx_max}_trail={trail_mult}_rsios={rsi_os}_rsiob={rsi_ob}",
                "Year": "2018-2024",
                "Trades": metrics["n_trades"],
                "WR%": round(metrics["win_rate_pct"], 1),
                "PF": round(metrics["profit_factor"], 3),
                "Net%": round(metrics["total_return_pct"], 2),
                "Sharpe": round(metrics["sharpe"], 3),
                "MaxDD%": round(metrics["max_dd_pct"], 2),
            }
            results.append(row)

            if combo_num % 20 == 0 or combo_num == total_combos:
                print(f"  [{combo_num}/{total_combos}] {row['Config']}: Net={row['Net%']:.2f}%, Sharpe={row['Sharpe']:.3f}, WR={row['WR%']:.1f}%")
        except Exception as e:
            print(f"  ERROR on combo {combo_num}: {e}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("MEAN REVERSION IMPROVED BACKTEST 2018-2024")
    print("=" * 80)

    all_results = []

    # Test 1-4: Core configurations
    for config_name, params in CONFIGS.items():
        print(f"\n--- {config_name} ---")
        config_results = run_config_yearly(config_name, params)
        all_results.extend(config_results)

    # Test 5: Parameter sweep
    sweep_results = run_parameter_sweep()
    all_results.extend(sweep_results)

    # ── Save CSV ──
    df_results = pd.DataFrame(all_results)
    csv_path = RESULTS_DIR / "mean_reversion_improved_results.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nResults saved to {csv_path}")

    # ── Generate Markdown Report ──
    report_lines = [
        "# Mean Reversion Improved Backtest Report (2018-2024)",
        "",
        "## Configurations Tested",
        "",
        "### Baseline (Current Best)",
        "```python",
        "rsi_oversold = 25",
        "rsi_overbought = 75",
        "bb_proximity = 0.05",
        "atr_stop = 3.0",
        "use_regime_filter = False",
        "use_trailing_stop = False",
        "```",
        "",
        "### + Regime Filter",
        "```python",
        "# Baseline params +",
        "use_regime_filter = True",
        "adx_max = 25.0",
        "```",
        "",
        "### + Trailing Stop",
        "```python",
        "# Baseline params +",
        "use_trailing_stop = True",
        "trailing_atr_mult = 1.5",
        "```",
        "",
        "### Both Improvements",
        "```python",
        "# Baseline params +",
        "use_regime_filter = True",
        "adx_max = 25.0",
        "use_trailing_stop = True",
        "trailing_atr_mult = 1.5",
        "```",
        "",
    ]

    # Year-by-Year Comparison Table
    report_lines.extend([
        "## Year-by-Year Comparison",
        "",
        "| Year | Baseline Net% | +Regime Net% | +Trail Net% | Both Net% |",
        "|------|---------------|--------------|-------------|-----------|",
    ])

    core_configs = ["Baseline", "+Regime", "+Trail", "Both"]
    core_df = df_results[df_results["Config"].isin(core_configs)]

    for year in range(2018, 2025):
        year_data = core_df[core_df["Year"] == year]
        vals = {}
        for _, row in year_data.iterrows():
            vals[row["Config"]] = row["Net%"]

        report_lines.append(
            f"| {year} | {vals.get('Baseline', 0):.2f} | "
            f"{vals.get('+Regime', 0):.2f} | {vals.get('+Trail', 0):.2f} | "
            f"{vals.get('Both', 0):.2f} |"
        )

    report_lines.append("")

    # Summary Table
    report_lines.extend([
        "## Summary",
        "",
        "| Config | Avg Net% | Avg WR% | Avg Sharpe | Avg MaxDD | Profitable Years |",
        "|--------|----------|---------|------------|-----------|------------------|",
    ])

    for config_name in core_configs:
        config_data = core_df[core_df["Config"] == config_name]
        if config_data.empty:
            continue

        avg_net = config_data["Net%"].mean()
        avg_wr = config_data["WR%"].mean()
        avg_sharpe = config_data["Sharpe"].mean()
        avg_dd = config_data["MaxDD%"].mean()
        profitable_years = (config_data["Net%"] > 0).sum()
        total_years = len(config_data)

        report_lines.append(
            f"| {config_name} | {avg_net:.2f} | {avg_wr:.1f} | "
            f"{avg_sharpe:.3f} | {avg_dd:.2f} | {profitable_years}/{total_years} |"
        )

    report_lines.append("")

    # Per-Config Year-by-Year Tables
    for config_name in core_configs:
        config_data = core_df[core_df["Config"] == config_name]
        if config_data.empty:
            continue

        report_lines.extend([
            f"## {config_name} — Year by Year",
            "",
            "| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD% |",
            "|------|--------|-----|-----|------|--------|--------|",
        ])

        for _, row in config_data.iterrows():
            report_lines.append(
                f"| {int(row['Year'])} | {int(row['Trades'])} | "
                f"{row['WR%']:.1f} | {row['PF']:.3f} | "
                f"{row['Net%']:.2f} | {row['Sharpe']:.3f} | "
                f"{row['MaxDD%']:.2f} |"
            )
        report_lines.append("")

    # Parameter Sweep Results (top 10 by Sharpe)
    if sweep_results:
        sweep_df = pd.DataFrame(sweep_results)
        sweep_df_sorted = sweep_df.sort_values("Sharpe", ascending=False)

        report_lines.extend([
            "## Parameter Sweep — Top 10 by Sharpe",
            "",
            "| Config | Trades | WR% | PF | Net% | Sharpe | MaxDD% |",
            "|--------|--------|-----|-----|------|--------|--------|",
        ])

        for _, row in sweep_df_sorted.head(10).iterrows():
            report_lines.append(
                f"| {row['Config']} | {int(row['Trades'])} | "
                f"{row['WR%']:.1f} | {row['PF']:.3f} | "
                f"{row['Net%']:.2f} | {row['Sharpe']:.3f} | "
                f"{row['MaxDD%']:.2f} |"
            )
        report_lines.append("")

    # ── Goal Assessment ──
    report_lines.extend([
        "## Goal Assessment",
        "",
        "Goals:",
        "- Profitable in MORE years than baseline",
        "- HIGHER average return than baseline",
        "- LOWER max drawdown than baseline",
        "- Maintain win rate > 50%",
        "",
    ])

    baseline_data = core_df[core_df["Config"] == "Baseline"]
    if not baseline_data.empty:
        baseline_profitable = (baseline_data["Net%"] > 0).sum()
        baseline_avg_net = baseline_data["Net%"].mean()
        baseline_avg_dd = baseline_data["MaxDD%"].mean()
        baseline_avg_wr = baseline_data["WR%"].mean()

        report_lines.append(f"**Baseline Performance:**")
        report_lines.append(f"- Profitable years: {baseline_profitable}/7")
        report_lines.append(f"- Average Net Return: {baseline_avg_net:.2f}%")
        report_lines.append(f"- Average Max Drawdown: {baseline_avg_dd:.2f}%")
        report_lines.append(f"- Average Win Rate: {baseline_avg_wr:.1f}%")
        report_lines.append("")

        for config_name in ["+Regime", "+Trail", "Both"]:
            config_data = core_df[core_df["Config"] == config_name]
            if config_data.empty:
                continue

            cfg_profitable = (config_data["Net%"] > 0).sum()
            cfg_avg_net = config_data["Net%"].mean()
            cfg_avg_dd = config_data["MaxDD%"].mean()
            cfg_avg_wr = config_data["WR%"].mean()

            more_years = cfg_profitable > baseline_profitable
            higher_return = cfg_avg_net > baseline_avg_net
            lower_dd = cfg_avg_dd > baseline_avg_dd  # less negative = lower drawdown
            good_wr = cfg_avg_wr > 50.0

            checks = []
            checks.append(f"{'✅' if more_years else '❌'} More profitable years: {cfg_profitable} vs {baseline_profitable}")
            checks.append(f"{'✅' if higher_return else '❌'} Higher avg return: {cfg_avg_net:.2f}% vs {baseline_avg_net:.2f}%")
            checks.append(f"{'✅' if lower_dd else '❌'} Lower max drawdown: {cfg_avg_dd:.2f}% vs {baseline_avg_dd:.2f}%")
            checks.append(f"{'✅' if good_wr else '❌'} Win rate > 50%: {cfg_avg_wr:.1f}%")

            report_lines.append(f"**{config_name}:**")
            report_lines.extend([f"- {c}" for c in checks])
            report_lines.append("")

    # ── Final Verdict ──
    report_lines.extend([
        "## Final Verdict",
        "",
    ])

    best_config = None
    best_sharpe = -999
    for config_name in core_configs:
        config_data = core_df[core_df["Config"] == config_name]
        if not config_data.empty:
            avg_sharpe = config_data["Sharpe"].mean()
            if avg_sharpe > best_sharpe:
                best_sharpe = avg_sharpe
                best_config = config_name

    if best_config:
        report_lines.append(f"**Best configuration by average Sharpe: {best_config}** (Sharpe: {best_sharpe:.3f})")
    else:
        report_lines.append("**No configuration produced valid results.**")

    report_lines.append("")

    # Save report
    report_path = RESULTS_DIR / "mean_reversion_improved_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"Report saved to {report_path}")

    # ── Print Summary ──
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for config_name in core_configs:
        config_data = core_df[core_df["Config"] == config_name]
        if config_data.empty:
            continue
        profitable = (config_data["Net%"] > 0).sum()
        total = len(config_data)
        avg_net = config_data["Net%"].mean()
        avg_sharpe = config_data["Sharpe"].mean()
        avg_wr = config_data["WR%"].mean()
        avg_dd = config_data["MaxDD%"].mean()
        print(
            f"{config_name:<12} {profitable}/{total} profitable  "
            f"Avg Net: {avg_net:+.2f}%  Avg Sharpe: {avg_sharpe:.3f}  "
            f"Avg WR: {avg_wr:.1f}%  Avg MaxDD: {avg_dd:.2f}%"
        )

    # Print top 5 from parameter sweep
    if sweep_results:
        sweep_df = pd.DataFrame(sweep_results)
        sweep_df_sorted = sweep_df.sort_values("Sharpe", ascending=False)
        print("\nTop 5 Parameter Sweep Results:")
        for _, row in sweep_df_sorted.head(5).iterrows():
            print(
                f"  {row['Config']:<50} Net={row['Net%']:+.2f}%  "
                f"Sharpe={row['Sharpe']:.3f}  WR={row['WR%']:.1f}%  "
                f"MaxDD={row['MaxDD%']:.2f}%"
            )


if __name__ == "__main__":
    main()
