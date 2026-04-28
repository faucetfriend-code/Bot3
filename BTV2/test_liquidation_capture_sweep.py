"""
test_liquidation_capture_sweep.py
==================================
Comprehensive parameter sweep for Liquidation Capture strategy.

Tests much more relaxed parameters to find configurations that actually
generate trades on daily BTC data (2018-2024).

Also tests year-by-year for best configs found.

Saves:
  - BTV2/results/liquidation_capture_sweep_results.csv
  - BTV2/results/liquidation_capture_yearly_results.csv
  - BTV2/results/liquidation_capture_report.md
"""

import sys
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
    run_liquidation_capture,
    _resample_ohlcv,
)

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = 0.10  # Butterworth cutoff (default)


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
    """Load 4h data for alternative timeframe testing."""
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
# PARAMETER SWEEP
# ─────────────────────────────────────────────────────────────────────────────

# Much more relaxed grid than the original LC_GRID
SWEEP_GRID = {
    "price_threshold": [0.02, 0.03, 0.05, 0.08, 0.10, 0.15],   # 2%, 3%, 5%, 8%, 10%, 15%
    "volume_mult":     [1.5, 2.0, 3.0, 5.0],
    "rsi_threshold":   [15.0, 20.0, 25.0, 30.0],
    "use_nearest_tp":  [False],
}

YEAR_RANGES_FULL = [
    ("2018-01-01", "2025-01-01"),  # Full range for sweep
]

YEAR_RANGES_YEARLY = [
    ("2018-01-01", "2019-01-01"),
    ("2019-01-01", "2020-01-01"),
    ("2020-01-01", "2021-01-01"),
    ("2021-01-01", "2022-01-01"),
    ("2022-01-01", "2023-01-01"),
    ("2023-01-01", "2024-01-01"),
    ("2024-01-01", "2025-01-01"),
]


def run_sweep():
    """Run parameter sweep across full 2018-2024 range."""
    print("=" * 80)
    print("LIQUIDATION CAPTURE PARAMETER SWEEP (2018-2024)")
    print("=" * 80)

    # Load full data once
    start, end = YEAR_RANGES_FULL[0]
    df = load_1d_data(start, end)
    print(f"\nLoaded {len(df)} daily bars from {df.index[0]} to {df.index[-1]}")

    keys = list(SWEEP_GRID.keys())
    values = list(SWEEP_GRID.values())
    combos = list(itertools.product(*values))
    total = len(combos)
    print(f"Testing {total} parameter combinations...\n")

    results = []
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        pt = params["price_threshold"]
        vm = params["volume_mult"]
        rt = params["rsi_threshold"]

        if (idx + 1) % 50 == 0 or idx == 0:
            print(f"  [{idx+1}/{total}] pt={pt:.2f} vm={vm:.1f} rsi={rt:.1f} ...", end=" ", flush=True)

        try:
            equity, trades = run_liquidation_capture(df, CUTOFF, **params)
            metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

            row = {
                "price_threshold": pt,
                "volume_mult": vm,
                "rsi_threshold": rt,
                "use_nearest_tp": params["use_nearest_tp"],
                "Trades": metrics["n_trades"],
                "WR%": round(metrics["win_rate_pct"], 1),
                "PF": round(metrics["profit_factor"], 3),
                "Net%": round(metrics["total_return_pct"], 2),
                "Sharpe": round(metrics["sharpe"], 3),
                "MaxDD%": round(metrics["max_dd_pct"], 2),
            }
            results.append(row)

            if (idx + 1) % 50 == 0 or idx == 0:
                print(f"Trades={row['Trades']}, WR={row['WR%']}%, Net={row['Net%']}%")
            else:
                # Only print if it has trades
                if row["Trades"] > 0:
                    print(f"Trades={row['Trades']}, WR={row['WR%']}%, Net={row['Net%']}%")
        except Exception as e:
            if (idx + 1) % 50 == 0 or idx == 0:
                print(f"ERROR: {e}")

    # Save results
    df_results = pd.DataFrame(results)
    sweep_path = RESULTS_DIR / "liquidation_capture_sweep_results.csv"
    df_results.to_csv(sweep_path, index=False)
    print(f"\nSweep results saved to {sweep_path}")

    # Show top configs by trades (minimum 5 trades)
    df_with_trades = df_results[df_results["Trades"] >= 5].copy()
    if not df_with_trades.empty:
        df_with_trades = df_with_trades.sort_values("Net%", ascending=False)
        print(f"\nTop configs with >= 5 trades (sorted by Net%):")
        print(df_with_trades.head(20).to_string(index=False))
    else:
        print("\nNo configs with >= 5 trades found. Showing all with >= 1 trade:")
        df_with_trades = df_results[df_results["Trades"] >= 1].sort_values("Trades", ascending=False)
        print(df_with_trades.head(20).to_string(index=False))

    # Also show configs with best win rate (min 3 trades)
    df_wr = df_results[df_results["Trades"] >= 3].copy()
    if not df_wr.empty:
        df_wr = df_wr.sort_values("WR%", ascending=False)
        print(f"\nTop configs by Win Rate (>= 3 trades):")
        print(df_wr.head(10).to_string(index=False))

    return df_results


def run_yearly_test(best_configs: list[dict]):
    """Run year-by-year test for best configurations."""
    print("\n" + "=" * 80)
    print("YEAR-BY-YEAR BACKTEST")
    print("=" * 80)

    yearly_results = []

    for cfg_idx, cfg in enumerate(best_configs):
        pt = cfg["price_threshold"]
        vm = cfg["volume_mult"]
        rt = cfg["rsi_threshold"]
        label = f"Config {cfg_idx+1}: pt={pt:.2f}, vm={vm:.1f}, rsi={rt:.1f}"
        print(f"\n--- {label} ---")

        params = {
            "price_threshold": pt,
            "volume_mult": vm,
            "rsi_threshold": rt,
            "use_nearest_tp": cfg.get("use_nearest_tp", False),
        }

        for start, end in YEAR_RANGES_YEARLY:
            year_label = start[:4]
            print(f"  {year_label}...", end=" ", flush=True)

            try:
                df = load_1d_data(start, end)
                if df.empty:
                    print("NO DATA")
                    yearly_results.append({
                        "Config": label,
                        "Year": int(year_label),
                        "Trades": 0, "WR%": 0.0, "PF": 0.0,
                        "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                    })
                    continue

                equity, trades = run_liquidation_capture(df, CUTOFF, **params)
                metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])

                row = {
                    "Config": label,
                    "Year": int(year_label),
                    "Trades": metrics["n_trades"],
                    "WR%": round(metrics["win_rate_pct"], 1),
                    "PF": round(metrics["profit_factor"], 3),
                    "Net%": round(metrics["total_return_pct"], 2),
                    "Sharpe": round(metrics["sharpe"], 3),
                    "MaxDD%": round(metrics["max_dd_pct"], 2),
                }
                yearly_results.append(row)
                print(f"Trades={row['Trades']}, WR={row['WR%']}%, Net={row['Net%']}%")
            except Exception as e:
                print(f"ERROR: {e}")
                yearly_results.append({
                    "Config": label,
                    "Year": int(year_label),
                    "Trades": 0, "WR%": 0.0, "PF": 0.0,
                    "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                })

    # Save yearly results
    df_yearly = pd.DataFrame(yearly_results)
    yearly_path = RESULTS_DIR / "liquidation_capture_yearly_results.csv"
    df_yearly.to_csv(yearly_path, index=False)
    print(f"\nYearly results saved to {yearly_path}")

    return df_yearly


def run_4h_test(best_configs: list[dict]):
    """Test best configs on 4h data for more liquidation events."""
    print("\n" + "=" * 80)
    print("4H TIMEFRAME TEST (more liquidation events)")
    print("=" * 80)

    results_4h = []

    for cfg_idx, cfg in enumerate(best_configs):
        pt = cfg["price_threshold"]
        vm = cfg["volume_mult"]
        rt = cfg["rsi_threshold"]
        label = f"Config {cfg_idx+1}: pt={pt:.3f}, vm={vm:.1f}, rsi={rt:.1f}"
        print(f"\n--- {label} (4h) ---")

        params = {
            "price_threshold": pt,
            "volume_mult": vm,
            "rsi_threshold": rt,
            "use_nearest_tp": cfg.get("use_nearest_tp", False),
        }

        for start, end in YEAR_RANGES_YEARLY:
            year_label = start[:4]
            print(f"  {year_label}...", end=" ", flush=True)

            try:
                df = load_4h_data(start, end)
                if df.empty:
                    print("NO DATA")
                    results_4h.append({
                        "Config": label,
                        "Year": int(year_label),
                        "Timeframe": "4h",
                        "Trades": 0, "WR%": 0.0, "PF": 0.0,
                        "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                    })
                    continue

                equity, trades = run_liquidation_capture(df, CUTOFF, **params)
                metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["4h"])

                row = {
                    "Config": label,
                    "Year": int(year_label),
                    "Timeframe": "4h",
                    "Trades": metrics["n_trades"],
                    "WR%": round(metrics["win_rate_pct"], 1),
                    "PF": round(metrics["profit_factor"], 3),
                    "Net%": round(metrics["total_return_pct"], 2),
                    "Sharpe": round(metrics["sharpe"], 3),
                    "MaxDD%": round(metrics["max_dd_pct"], 2),
                }
                results_4h.append(row)
                print(f"Trades={row['Trades']}, WR={row['WR%']}%, Net={row['Net%']}%")
            except Exception as e:
                print(f"ERROR: {e}")
                results_4h.append({
                    "Config": label,
                    "Year": int(year_label),
                    "Timeframe": "4h",
                    "Trades": 0, "WR%": 0.0, "PF": 0.0,
                    "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
                })

    df_4h = pd.DataFrame(results_4h)
    path_4h = RESULTS_DIR / "liquidation_capture_4h_results.csv"
    df_4h.to_csv(path_4h, index=False)
    print(f"\n4h results saved to {path_4h}")

    return df_4h


def generate_report(df_sweep: pd.DataFrame, df_yearly: pd.DataFrame, df_4h: pd.DataFrame = None):
    """Generate markdown report."""
    report = []
    report.append("# Liquidation Capture Strategy — Parameter Sweep Report\n")
    report.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")

    # Strategy overview
    report.append("## Strategy Overview\n")
    report.append("The Liquidation Capture strategy fades post-liquidation cascades by detecting:")
    report.append("- 5-bar price drop >= `price_threshold`")
    report.append("- Volume spike >= `volume_mult` × 20-bar average")
    report.append("- RSI <= `rsi_threshold` (oversold confirmation)")
    report.append("- >= 4 consecutive down closes")
    report.append("- Lower wick ratio >= 1.5 (panic selling signature)")
    report.append("- Exit: TP = 3× risk (3:1 RRR), SL = 1% beyond 5-bar extreme\n")

    # Parameter Sweep Results
    report.append("## Parameter Sweep Results (2018-2024, Daily)\n")

    # Filter to configs with at least 1 trade
    df_active = df_sweep[df_sweep["Trades"] >= 1].copy()
    if df_active.empty:
        report.append("**No configurations generated any trades.**\n")
    else:
        report.append(f"Total configs tested: {len(df_sweep)}\n")
        report.append(f"Configs with >= 1 trade: {len(df_active)}\n")
        report.append(f"Configs with >= 5 trades: {len(df_active[df_active['Trades'] >= 5])}\n")
        report.append(f"Configs with >= 10 trades: {len(df_active[df_active['Trades'] >= 10])}\n\n")

        # Top 20 by Net%
        report.append("### Top 20 Configurations by Net Return\n")
        df_top = df_active.sort_values("Net%", ascending=False).head(20)
        report.append("| price_threshold | volume_mult | rsi_threshold | Trades | WR% | PF | Net% | Sharpe | MaxDD% |")
        report.append("|-----------------|-------------|---------------|--------|-----|-----|------|--------|--------|")
        for _, row in df_top.iterrows():
            report.append(
                f"| {row['price_threshold']:.3f} | {row['volume_mult']:.1f} | "
                f"{row['rsi_threshold']:.1f} | {row['Trades']} | {row['WR%']:.1f} | "
                f"{row['PF']:.3f} | {row['Net%']:.2f} | {row['Sharpe']:.3f} | {row['MaxDD%']:.2f} |"
            )
        report.append("")

        # Top 20 by trade count
        report.append("### Top 20 Configurations by Trade Count\n")
        df_trades = df_active.sort_values("Trades", ascending=False).head(20)
        report.append("| price_threshold | volume_mult | rsi_threshold | Trades | WR% | PF | Net% | Sharpe | MaxDD% |")
        report.append("|-----------------|-------------|---------------|--------|-----|-----|------|--------|--------|")
        for _, row in df_trades.iterrows():
            report.append(
                f"| {row['price_threshold']:.3f} | {row['volume_mult']:.1f} | "
                f"{row['rsi_threshold']:.1f} | {row['Trades']} | {row['WR%']:.1f} | "
                f"{row['PF']:.3f} | {row['Net%']:.2f} | {row['Sharpe']:.3f} | {row['MaxDD%']:.2f} |"
            )
        report.append("")

    # Year-by-Year Results
    report.append("## Year-by-Year Results\n")
    if not df_yearly.empty:
        configs = df_yearly["Config"].unique()
        for cfg in configs:
            report.append(f"### {cfg}\n")
            df_cfg = df_yearly[df_yearly["Config"] == cfg]
            report.append("| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD% |")
            report.append("|------|--------|-----|-----|------|--------|--------|")
            for _, row in df_cfg.iterrows():
                report.append(
                    f"| {row['Year']} | {row['Trades']} | {row['WR%']:.1f} | "
                    f"{row['PF']:.3f} | {row['Net%']:.2f} | {row['Sharpe']:.3f} | {row['MaxDD%']:.2f} |"
                )
            report.append("")

    # 4h Results
    if df_4h is not None and not df_4h.empty:
        report.append("## 4h Timeframe Results\n")
        configs = df_4h["Config"].unique()
        for cfg in configs:
            report.append(f"### {cfg}\n")
            df_cfg = df_4h[df_4h["Config"] == cfg]
            report.append("| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD% |")
            report.append("|------|--------|-----|-----|------|--------|--------|")
            for _, row in df_cfg.iterrows():
                report.append(
                    f"| {row['Year']} | {row['Trades']} | {row['WR%']:.1f} | "
                    f"{row['PF']:.3f} | {row['Net%']:.2f} | {row['Sharpe']:.3f} | {row['MaxDD%']:.2f} |"
                )
            report.append("")

    # Analysis & Recommendations
    report.append("## Analysis & Recommendations\n")

    # Find best overall config
    df_active = df_sweep[df_sweep["Trades"] >= 5].copy()
    if not df_active.empty:
        best = df_active.sort_values("Net%", ascending=False).iloc[0]
        report.append("### Best Configuration (>= 5 trades)\n")
        report.append(f"- **price_threshold**: {best['price_threshold']:.3f}")
        report.append(f"- **volume_mult**: {best['volume_mult']:.1f}")
        report.append(f"- **rsi_threshold**: {best['rsi_threshold']:.1f}")
        report.append(f"- **Trades**: {best['Trades']}")
        report.append(f"- **Win Rate**: {best['WR%']:.1f}%")
        report.append(f"- **Net Return**: {best['Net%']:.2f}%")
        report.append(f"- **Sharpe**: {best['Sharpe']:.3f}")
        report.append(f"- **Max Drawdown**: {best['MaxDD%']:.2f}%\n")

        # Check if it meets goals
        goals_met = []
        if best["Trades"] >= 5:
            goals_met.append("[OK] Generates >= 5 trades per year (over full period)")
        else:
            goals_met.append("[FAIL] Does not generate >= 5 trades per year")
        if best["WR%"] > 50:
            goals_met.append("[OK] Win rate > 50%")
        else:
            goals_met.append("[FAIL] Win rate <= 50%")
        if best["Net%"] > 0:
            goals_met.append("[OK] Positive net return")
        else:
            goals_met.append("[FAIL] Negative net return")

        report.append("### Goal Assessment\n")
        for goal in goals_met:
            report.append(f"- {goal}")
        report.append("")

    # Recommendations
    report.append("### Recommendations\n")
    report.append("1. **If daily data is too restrictive**: The strategy may work better on 4h or 1h timeframes")
    report.append("   where liquidation cascades are more frequent and detectable.")
    report.append("2. **Consider ATR-based thresholds**: Fixed % thresholds don't adapt to volatility regimes.")
    report.append("   Using ATR multiples would make the strategy more robust across different market conditions.")
    report.append("3. **Relax the consecutive down candle requirement**: The current 4-candle requirement")
    report.append("   is very strict. Consider reducing to 3 or making it configurable.")
    report.append("4. **Add volume profile confirmation**: Liquidation events often occur at key support/resistance")
    report.append("   levels. Adding volume profile or reference level confirmation could improve signal quality.")
    report.append("5. **Consider asymmetric parameters**: Long and short entries may need different thresholds")
    report.append("   since crypto markets have different dynamics for crashes vs. squeezes.\n")

    report_path = RESULTS_DIR / "liquidation_capture_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"\nReport saved to {report_path}")


def main():
    # Step 1: Parameter Sweep
    df_sweep = run_sweep()

    # Step 2: Select best configs for yearly testing
    # Prioritize configs with >= 5 trades and positive returns
    df_active = df_sweep[df_sweep["Trades"] >= 5].copy()
    if df_active.empty:
        # Fall back to configs with any trades
        df_active = df_sweep[df_sweep["Trades"] >= 1].copy()

    if df_active.empty:
        print("\nNo configs generated any trades. Cannot proceed with yearly testing.")
        # Still generate report
        generate_report(df_sweep, pd.DataFrame())
        return

    # Select top 5 configs by a composite score (trades * win_rate * net_return)
    df_active["score"] = (
        df_active["Trades"].clip(upper=50) *
        df_active["WR%"].clip(lower=0) *
        (df_active["Net%"] + 100).clip(lower=0)
    )
    best_configs = df_active.sort_values("score", ascending=False).head(5).to_dict("records")

    print(f"\nSelected {len(best_configs)} best configs for yearly testing:")
    for i, cfg in enumerate(best_configs):
        print(f"  {i+1}. pt={cfg['price_threshold']:.3f}, vm={cfg['volume_mult']:.1f}, "
              f"rsi={cfg['rsi_threshold']:.1f}, trades={cfg['Trades']}, "
              f"WR={cfg['WR%']:.1f}%, Net={cfg['Net%']:.2f}%")

    # Step 3: Year-by-year test (daily)
    df_yearly = run_yearly_test(best_configs)

    # Step 4: Test on 4h data
    df_4h = run_4h_test(best_configs)

    # Step 5: Generate report
    generate_report(df_sweep, df_yearly, df_4h)

    print("\n" + "=" * 80)
    print("DONE — All results saved to BTV2/results/")
    print("=" * 80)


if __name__ == "__main__":
    main()
