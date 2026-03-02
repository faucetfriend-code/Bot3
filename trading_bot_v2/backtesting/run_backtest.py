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


def main():
    parser = argparse.ArgumentParser(description="Run trading bot backtest")
    parser.add_argument("--start", default=config.backtest_start_date)
    parser.add_argument("--end", default=config.backtest_end_date)
    parser.add_argument("--symbol", default=config.backtest_symbol)
    parser.add_argument("--capital", type=float, default=config.backtest_initial_capital)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--report", default="backtest_report.html")
    parser.add_argument(
        "--strategy",
        default=getattr(config, "backtest_strategy", ""),
        help="Run only one strategy (e.g. MomentumScalping). Empty = all strategies.",
    )
    args = parser.parse_args()

    engine = BacktestEngine()
    strategy_filter = args.strategy or None

    if args.walk_forward:
        wf = WalkForwardAnalyzer(engine)
        results = wf.run(args.start, args.end, args.symbol, args.capital)
        # Save combined report for walk-forward
        for i, r in enumerate(results):
            r.save_html(f"backtest_wf_{i+1:02d}.html")
    else:
        result = engine.run(args.start, args.end, args.symbol, args.capital,
                            strategy_filter=strategy_filter)
        result.print_summary()
        result.save_html(args.report)
        print(f"Report saved: {args.report}")


if __name__ == "__main__":
    main()
