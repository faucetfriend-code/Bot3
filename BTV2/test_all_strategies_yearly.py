"""
test_all_strategies_yearly.py
==============================
Test ALL 5 strategies year-by-year from 2018-2024 to find which ones
are consistently profitable across ALL years.

Strategies:
  1. Mean Reversion      (daily)
  2. Momentum Scalping   (15m)
  3. Liquidation Capture (daily)
  4. Grid Trading        (4h)
  5. MA Crossover        (daily)

Uses best parameters from previous tests.
Loads data from G:/Candle Data/ parquet files, resampling 5m → 4h/15m as needed.

Saves:
  - BTV2/results/all_strategies_yearly_results.csv
  - BTV2/results/all_strategies_yearly_report.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
    run_mean_reversion,
    run_momentum_scalping,
    run_liquidation_capture,
    run_grid_trading,
    run_ma_crossover,
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


def load_4h_data(start: str, end: str) -> pd.DataFrame:
    """Load 4h data, resample from 5m if native 4h doesn't cover the range."""
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

    # Fallback: resample from 5m
    return _load_and_resample("5m", "240min", start, end)


def load_15m_data(start: str, end: str) -> pd.DataFrame:
    """Load 15m data, resample from 5m if native 15m doesn't cover the range."""
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

    # Fallback: resample from 5m
    return _load_and_resample("5m", "15min", start, end)


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
# STRATEGY CONFIGURATIONS (best params from previous tests)
# ─────────────────────────────────────────────────────────────────────────────

# Grid Trading (best performer)
GRID_PARAMS = {
    "adx_threshold": 15,
    "spacing_mult": 0.30,
    # take_profit and stop_loss are hardcoded in run_grid_trading as 2*ATR SL
    # and mid-point TP — the function signature doesn't expose them as params
    "use_poc_center": False,
    "use_va_bounds": False,
}

# Mean Reversion (best config)
MEANREV_PARAMS = {
    "rsi_oversold": 25,
    "rsi_overbought": 75,
    "bb_proximity": 0.05,
    "atr_stop": 3.0,
    "use_sfp_entry": False,
    "use_poc_tp": False,
}

# Momentum Scalping (best config)
MOMENTUM_PARAMS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "atr_stop": 1.0,
    "atr_target": 2.0,
    "use_cvd_confirm": False,
    "cvd_window": 20,
}

# MA Crossover (defaults)
MA_PARAMS = {
    "fast_period": 20,
    "slow_period": 50,
    "pullback_max": 0.06,
    "use_va_chop_filter": False,
}

# Liquidation Capture (defaults)
LIQ_PARAMS = {
    "price_threshold": 0.030,
    "volume_mult": 3.0,
    "rsi_threshold": 18.0,
    "use_nearest_tp": False,
}


# ─────────────────────────────────────────────────────────────────────────────
# TEST RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_strategy_yearly(strategy_name: str, load_func, params: dict,
                       bars_per_year: int) -> list[dict]:
    """Run a single strategy across all year ranges."""
    results = []

    for start, end in YEAR_RANGES:
        year_label = start[:4]
        print(f"  [{strategy_name}] {year_label}...", end=" ", flush=True)

        try:
            df = load_func(start, end)
            if df.empty:
                print(f"NO DATA")
                results.append({
                    "Strategy": strategy_name,
                    "Year": int(year_label),
                    "Trades": 0, "WR%": 0.0, "PF": 0.0,
                    "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                })
                continue

            print(f"{len(df)} bars -> ", end="", flush=True)

            # Dispatch to correct function
            if strategy_name == "Mean Reversion":
                equity, trades = run_mean_reversion(df, CUTOFF, **params)
            elif strategy_name == "Momentum Scalping":
                equity, trades = run_momentum_scalping(df, CUTOFF, **params)
            elif strategy_name == "Liquidation Capture":
                equity, trades = run_liquidation_capture(df, CUTOFF, **params)
            elif strategy_name == "Grid Trading":
                equity, trades = run_grid_trading(df, CUTOFF, **params)
            elif strategy_name == "MA Crossover":
                equity, trades = run_ma_crossover(df, CUTOFF, **params)
            else:
                raise ValueError(f"Unknown strategy: {strategy_name}")

            metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)

            row = {
                "Strategy": strategy_name,
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
                "Strategy": strategy_name,
                "Year": int(year_label),
                "Trades": 0, "WR%": 0.0, "PF": 0.0,
                "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
            })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("ALL STRATEGIES YEARLY BACKTEST 2018-2024")
    print("=" * 80)

    all_results = []

    # 1. Mean Reversion (daily)
    print("\n--- Mean Reversion (daily) ---")
    mr_results = run_strategy_yearly(
        "Mean Reversion", load_1d_data, MEANREV_PARAMS,
        bars_per_year=INTERVAL_BARS_PER_YEAR["1d"],
    )
    all_results.extend(mr_results)

    # 2. Momentum Scalping (15m)
    print("\n--- Momentum Scalping (15m) ---")
    ms_results = run_strategy_yearly(
        "Momentum Scalping", load_15m_data, MOMENTUM_PARAMS,
        bars_per_year=INTERVAL_BARS_PER_YEAR["15m"],
    )
    all_results.extend(ms_results)

    # 3. Liquidation Capture (daily)
    print("\n--- Liquidation Capture (daily) ---")
    lc_results = run_strategy_yearly(
        "Liquidation Capture", load_1d_data, LIQ_PARAMS,
        bars_per_year=INTERVAL_BARS_PER_YEAR["1d"],
    )
    all_results.extend(lc_results)

    # 4. Grid Trading (4h)
    print("\n--- Grid Trading (4h) ---")
    gt_results = run_strategy_yearly(
        "Grid Trading", load_4h_data, GRID_PARAMS,
        bars_per_year=INTERVAL_BARS_PER_YEAR["4h"],
    )
    all_results.extend(gt_results)

    # 5. MA Crossover (daily)
    print("\n--- MA Crossover (daily) ---")
    ma_results = run_strategy_yearly(
        "MA Crossover", load_1d_data, MA_PARAMS,
        bars_per_year=INTERVAL_BARS_PER_YEAR["1d"],
    )
    all_results.extend(ma_results)

    # ── Save CSV ──
    df_results = pd.DataFrame(all_results)
    csv_path = RESULTS_DIR / "all_strategies_yearly_results.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nResults saved to {csv_path}")

    # ── Generate Markdown Report ──
    report_lines = [
        "# All Strategies Yearly Backtest Report (2018-2024)",
        "",
        "## Parameters Used",
        "",
        "### Grid Trading (4h)",
        "```python",
        "adx_threshold = 15",
        "spacing_mult = 0.30",
        "```",
        "",
        "### Mean Reversion (daily)",
        "```python",
        "rsi_oversold = 25",
        "rsi_overbought = 75",
        "bb_proximity = 0.05",
        "```",
        "",
        "### Momentum Scalping (15m)",
        "```python",
        "ema_fast = 20",
        "ema_slow = 50",
        "atr_stop = 1.0",
        "atr_target = 2.0",
        "```",
        "",
        "### MA Crossover (daily)",
        "```python",
        "ma_fast = 20",
        "ma_slow = 50",
        "```",
        "",
        "### Liquidation Capture (daily)",
        "```python",
        "drop_threshold = 10  (price_threshold=0.030)",
        "recovery_bars = 10   (volume_mult=3.0, rsi_threshold=18.0)",
        "```",
        "",
    ]

    # Per-strategy tables
    strategy_order = [
        "Grid Trading", "Mean Reversion", "Momentum Scalping",
        "MA Crossover", "Liquidation Capture",
    ]
    strategy_timeframes = {
        "Grid Trading": "4h",
        "Mean Reversion": "daily",
        "Momentum Scalping": "15m",
        "MA Crossover": "daily",
        "Liquidation Capture": "daily",
    }

    for strat in strategy_order:
        tf = strategy_timeframes[strat]
        report_lines.extend([
            f"## {strat} ({tf})",
            "",
            "| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD |",
            "|------|--------|-----|-----|------|--------|-------|",
        ])

        strat_data = df_results[df_results["Strategy"] == strat]
        for _, row in strat_data.iterrows():
            report_lines.append(
                f"| {int(row['Year'])} | {int(row['Trades'])} | "
                f"{row['WR%']:.1f} | {row['PF']:.3f} | "
                f"{row['Net%']:.2f} | {row['Sharpe']:.3f} | "
                f"{row['MaxDD%']:.2f} |"
            )
        report_lines.append("")

    # ── Consistency Analysis ──
    report_lines.extend([
        "## Consistency Analysis",
        "",
        "A strategy is **consistently profitable** if it has positive Net% in ALL 7 years.",
        "",
    ])

    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue

        profitable_years = (strat_data["Net%"] > 0).sum()
        total_years = len(strat_data)
        avg_net = strat_data["Net%"].mean()
        avg_wr = strat_data["WR%"].mean()
        avg_sharpe = strat_data["Sharpe"].mean()
        avg_pf = strat_data["PF"].mean()
        avg_dd = strat_data["MaxDD%"].mean()

        status = "[CONSISTENT]" if profitable_years == total_years else "[INCONSISTENT]"
        if profitable_years == 0:
            status = "[NEVER PROFITABLE]"
        elif profitable_years < total_years:
            status = f"[MIXED {profitable_years}/{total_years} years]"

        report_lines.extend([
            f"### {strat}: {status}",
            "",
            f"- **Profitable years**: {profitable_years}/{total_years}",
            f"- **Average Net Return**: {avg_net:.2f}%",
            f"- **Average Win Rate**: {avg_wr:.1f}%",
            f"- **Average Sharpe**: {avg_sharpe:.3f}",
            f"- **Average Profit Factor**: {avg_pf:.3f}",
            f"- **Average Max Drawdown**: {avg_dd:.2f}%",
            "",
        ])

        # Year-by-year profitability
        profitable_list = []
        unprofitable_list = []
        for _, row in strat_data.iterrows():
            if row["Net%"] > 0:
                profitable_list.append(int(row["Year"]))
            else:
                unprofitable_list.append(int(row["Year"]))

        if profitable_list:
            report_lines.append(f"- **Profitable years**: {profitable_list}")
        if unprofitable_list:
            report_lines.append(f"- **Unprofitable years**: {unprofitable_list}")
        report_lines.append("")

    # ── Summary Table ──
    report_lines.extend([
        "## Summary: All Strategies Side-by-Side",
        "",
    ])

    # Wide table: strategies as rows, years as columns for Net%
    report_lines.append("### Net Return (%) by Year")
    report_lines.append("")
    header = "| Strategy | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Avg |"
    report_lines.append(header)
    report_lines.append("|" + "|".join(["--------"] * 9) + "|")

    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        vals = {}
        for _, row in strat_data.iterrows():
            vals[int(row["Year"])] = row["Net%"]
        cells = [f"{vals.get(y, 0):.2f}" for y in range(2018, 2025)]
        avg = strat_data["Net%"].mean() if not strat_data.empty else 0
        report_lines.append(f"| {strat} | {' | '.join(cells)} | {avg:.2f} |")

    report_lines.append("")

    # Win Rate by Year
    report_lines.append("### Win Rate (%) by Year")
    report_lines.append("")
    report_lines.append("| Strategy | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Avg |")
    report_lines.append("|" + "|".join(["--------"] * 9) + "|")

    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        vals = {}
        for _, row in strat_data.iterrows():
            vals[int(row["Year"])] = row["WR%"]
        cells = [f"{vals.get(y, 0):.1f}" for y in range(2018, 2025)]
        avg = strat_data["WR%"].mean() if not strat_data.empty else 0
        report_lines.append(f"| {strat} | {' | '.join(cells)} | {avg:.1f} |")

    report_lines.append("")

    # Sharpe by Year
    report_lines.append("### Sharpe Ratio by Year")
    report_lines.append("")
    report_lines.append("| Strategy | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Avg |")
    report_lines.append("|" + "|".join(["--------"] * 9) + "|")

    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        vals = {}
        for _, row in strat_data.iterrows():
            vals[int(row["Year"])] = row["Sharpe"]
        cells = [f"{vals.get(y, 0):.3f}" for y in range(2018, 2025)]
        avg = strat_data["Sharpe"].mean() if not strat_data.empty else 0
        report_lines.append(f"| {strat} | {' | '.join(cells)} | {avg:.3f} |")

    report_lines.append("")

    # ── Final Verdict ──
    report_lines.extend([
        "## Final Verdict",
        "",
    ])

    consistent_strategies = []
    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue
        if (strat_data["Net%"] > 0).sum() == len(strat_data):
            consistent_strategies.append(strat)

    if consistent_strategies:
        report_lines.append(
            f"**Consistently profitable across ALL years (2018-2024):** "
            f"{', '.join(consistent_strategies)}"
        )
    else:
        report_lines.append(
            "**No strategy was profitable in ALL 7 years.** "
            "This is normal — market regimes change, and no single set of "
            "parameters works in every environment."
        )

    report_lines.append("")

    # Rank by average Sharpe
    avg_sharpes = {}
    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if not strat_data.empty:
            avg_sharpes[strat] = strat_data["Sharpe"].mean()

    ranked = sorted(avg_sharpes.items(), key=lambda x: x[1], reverse=True)
    report_lines.append("### Ranking by Average Sharpe Ratio")
    report_lines.append("")
    report_lines.append("| Rank | Strategy | Avg Sharpe |")
    report_lines.append("|------|----------|------------|")
    for i, (strat, sharpe) in enumerate(ranked, 1):
        report_lines.append(f"| {i} | {strat} | {sharpe:.3f} |")

    report_lines.append("")

    # Save report
    report_path = RESULTS_DIR / "all_strategies_yearly_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"Report saved to {report_path}")

    # ── Print Summary ──
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for strat in strategy_order:
        strat_data = df_results[df_results["Strategy"] == strat]
        if strat_data.empty:
            continue
        profitable = (strat_data["Net%"] > 0).sum()
        total = len(strat_data)
        avg_net = strat_data["Net%"].mean()
        avg_sharpe = strat_data["Sharpe"].mean()
        status = "CONSISTENT" if profitable == total else "INCONSISTENT"
        if profitable == 0:
            status = "NEVER PROFITABLE"
        elif profitable < total:
            status = f"MIXED ({profitable}/{total} years)"
        print(
            f"[{status}] {strat:<25} {profitable}/{total} profitable  "
            f"Avg Net: {avg_net:+.2f}%  Avg Sharpe: {avg_sharpe:.3f}"
        )


if __name__ == "__main__":
    main()
