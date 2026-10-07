"""
Run a backtest from the command line.

Usage:
    python -m trading_bot_v2.backtesting.run_backtest
    python -m trading_bot_v2.backtesting.run_backtest --start 2024-01-01 --end 2024-06-30
    python -m trading_bot_v2.backtesting.run_backtest --symbol BTC-USDC
    python -m trading_bot_v2.backtesting.run_backtest --strategy MomentumScalping
    python -m trading_bot_v2.backtesting.run_backtest --walk-forward

Strategies (--strategy):
    MeanReversion, MACrossover, GridTrading, LiquidationCapture,
    VWAPScalping, MomentumScalping, FundingArb, OrderBookImbalance
"""

import argparse
from trading_bot_v2.backtesting import BacktestEngine, WalkForwardAnalyzer
from trading_bot_v2.config import config
from trading_bot_v2.diagnostics.report import print_funnel_report


def main():
    parser = argparse.ArgumentParser(description="Run trading bot backtest")
    parser.add_argument("--start", default=config.backtest_start_date)
    parser.add_argument("--end", default=config.backtest_end_date)
    parser.add_argument("--symbol", default=config.backtest_symbol)
    parser.add_argument(
        "--capital", type=float, default=config.backtest_initial_capital
    )
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--report", default="backtest_report.html")
    parser.add_argument(
        "--strategy",
        default=getattr(config, "backtest_strategy", ""),
        help="Run only one strategy (e.g. MomentumScalping). Empty = all strategies.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Candle store to read. THE ONLY RELIABLE WAY to redirect it: "
            "exporting BACKTEST_DATA_DIR does nothing, because .env sets it "
            "and config.py calls load_dotenv(override=True). The configured "
            "path is also relative, so running from a git worktree silently "
            "resolves to that worktree's own (usually empty) data directory."
        ),
    )
    args = parser.parse_args()

    if args.data_dir:
        # The engine reads config.backtest_data_dir when it builds its loader,
        # so overriding it here is what makes the flag take effect.
        config.backtest_data_dir = args.data_dir
        print(f"Candle store: {args.data_dir}")

    engine = BacktestEngine()
    strategy_filter = args.strategy or None

    if args.walk_forward:
        wf = WalkForwardAnalyzer(engine)
        results = wf.run(
            args.start,
            args.end,
            args.symbol,
            args.capital,
            strategy=strategy_filter,
        )
        # Save combined report for walk-forward
        for i, r in enumerate(results):
            r.save_html(f"backtest_wf_{i + 1:02d}.html")
    else:
        result = engine.run(
            args.start,
            args.end,
            args.symbol,
            args.capital,
            strategy_filter=strategy_filter,
        )
        result.print_summary()
        # Shown on every run, not just empty ones: on a profitable run it
        # still names the stage with the largest attrition.
        print_funnel_report(
            result.diagnostics,
            title=f"{strategy_filter or 'all strategies'} | {args.symbol}",
        )
        result.save_html(args.report)
        print(f"Report saved: {args.report}")


if __name__ == "__main__":
    main()
