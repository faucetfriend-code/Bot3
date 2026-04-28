#!/usr/bin/env python3
"""
btv2_agent_run.py — CLI entry point for automated strategy optimization
Usage: python btv2_agent_run.py --strategy "Mean Reversion" --apply
"""
import argparse, json, sys
from datetime import date
from pathlib import Path

# Add BTV2 to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    STRATEGY_REGISTRY, STRATEGY_TIMEFRAME_CONFIG, build_windows, compute_metrics,
    optimize_strategy, stitch_oos_equity,
)
from agent import (
    read_env_params, run_parameter_sweep,
    build_proposal, apply_to_env, _eval_config,
)
from data_manager import get_candles, TICKER_MAP

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy",  default="Mean Reversion",
                        choices=list(STRATEGY_REGISTRY.keys()))
    parser.add_argument("--ticker",    default="BTC-USD")
    parser.add_argument("--start",     default="2020-01-01")
    parser.add_argument("--end",       default=str(date.today()))
    parser.add_argument("--cutoff",    type=float, default=0.10)
    parser.add_argument("--interval",  help="Override timeframe (e.g., 5m, 15m, 1h)")
    parser.add_argument("--apply",     action="store_true",
                        help="Write proposed changes to .env")
    parser.add_argument("--json-out",  help="Write results to JSON file")
    args = parser.parse_args()

    # Get interval from strategy config or CLI override
    tf_config = STRATEGY_TIMEFRAME_CONFIG[args.strategy]
    interval = args.interval if args.interval else tf_config.get("interval", "1d")
    
    # 1. Load data from Binance (or fallback to yfinance)
    print(f"[1/5] Loading {args.ticker} ({interval})...")
    
    # Map ticker to Binance symbol if available
    binance_symbol = TICKER_MAP.get(args.ticker)
    
    if binance_symbol and interval in ["1m", "5m", "15m", "1h", "4h", "1d"]:
        # Use Binance data
        df = get_candles(binance_symbol, interval, args.start, args.end)
        if df is not None and not df.empty:
            # Standardize column names to match yfinance format (capitalized)
            df.columns = [c.capitalize() for c in df.columns]
            print(f"      Loaded {len(df)} {interval} bars from Binance.")
        else:
            print(f"      WARNING: No data from Binance, trying yfinance...")
            import yfinance as yf
            df = yf.download(args.ticker, start=args.start, end=args.end, interval=interval,
                            auto_adjust=True, progress=False)
            df.columns = df.columns.get_level_values(0)
            print(f"      Loaded {len(df)} {interval} bars from yfinance.")
    else:
        # Fallback to yfinance for daily/daily+ intervals
        import yfinance as yf
        df = yf.download(args.ticker, start=args.start, end=args.end, interval=interval,
                        auto_adjust=True, progress=False)
        df.columns = df.columns.get_level_values(0)
        print(f"      Loaded {len(df)} {interval} bars from yfinance.")

    # 2. Walk-forward baseline
    print(f"[2/5] Running walk-forward baseline ({args.strategy})...")
    windows = build_windows(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
    )
    func, _, defaults = STRATEGY_REGISTRY[args.strategy]
    segments, all_trades = [], []
    for w in windows:
        df_train = df.loc[str(w["train_start"]):str(w["train_end"])]
        df_test  = df.loc[str(w["test_start"]):str(w["test_end"])]
        if len(df_train) < 50 or len(df_test) < 10:
            continue
        best = optimize_strategy(df_train, args.cutoff, args.strategy)
        eq, trd = func(df_test, args.cutoff, **best)
        segments.append(eq); all_trades.extend(trd)

    oos_equity  = stitch_oos_equity(segments)
    oos_metrics = compute_metrics(oos_equity, all_trades)
    print(f"      OOS Sharpe={oos_metrics['sharpe']:.3f}  "
          f"Return={oos_metrics['total_return_pct']:+.1f}%  "
          f"MaxDD={oos_metrics['max_dd_pct']:.1f}%  "
          f"Trades={oos_metrics['n_trades']}")

    # 3. Read current .env params
    print(f"[3/5] Reading current .env parameters...")
    current_params = read_env_params(args.strategy)
    baseline_sharpe = _eval_config(df, args.cutoff, args.strategy,
                                   current_params, windows)
    print(f"      Current .env OOS Sharpe: {baseline_sharpe:.3f}")

    # 4. Coordinate-descent parameter sweep
    print(f"[4/5] Running parameter sweep (coordinate descent)...")
    best_params, best_cutoff, best_sharpe, trial_log = run_parameter_sweep(
        df, args.cutoff, args.strategy, current_params, windows,
    )
    proposal = build_proposal(args.strategy, current_params, best_params)

    print(f"      Best OOS Sharpe: {best_sharpe:.3f}  "
          f"(Delta {best_sharpe - baseline_sharpe:+.3f} vs current .env)")

    if proposal:
        print(f"      {len(proposal)} parameter change(s) proposed:")
        for p in proposal:
            print(f"        {p['ENV Variable']}: "
                  f"{p['Current Value']} -> {p['Proposed Value']}  ({p['Change']})")
    else:
        print("      No improvements found - current .env is near-optimal.")

    # 5. Optionally apply
    if args.apply and proposal:
        print(f"[5/5] Writing {len(proposal)} change(s) to .env...")
        updated = apply_to_env(proposal)
        print(f"      Updated: {', '.join(updated)}")
        print("      Restart the trading bot to activate new parameters.")
    else:
        print("[5/5] Dry run - no changes written.")

    # JSON output
    result = {
        "strategy":        args.strategy,
        "ticker":          args.ticker,
        "period":          f"{args.start} -> {args.end}",
        "cutoff":          args.cutoff,
        "oos_metrics":     oos_metrics,
        "baseline_sharpe": baseline_sharpe,
        "best_sharpe":    best_sharpe,
        "sharpe_delta":   best_sharpe - baseline_sharpe,
        "proposal":        proposal,
        "trial_log":       trial_log,
        "applied":         args.apply and bool(proposal),
    }

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2))
        print(f"      Results written to {args.json_out}")
    else:
        print("\n--- JSON RESULT ---")
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
