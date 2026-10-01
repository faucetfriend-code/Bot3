"""Reproducible offline historical comparison; writes only beside this script."""

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["DATA_AUTODOWNLOAD"] = "false"
os.environ["DATABASE_BACKEND"] = "sqlite"
os.environ["DATABASE_PATH"] = str(OUTPUT / "isolated.sqlite")
os.environ["BACKTEST_SEED"] = "0"


def deny_network(*args, **kwargs):
    raise RuntimeError("Historical audit forbids network access")


socket.socket.connect = deny_network
socket.create_connection = deny_network

# Safety guards must be installed before any application import.
from loguru import logger  # noqa: E402
import trading_bot_v2.backtesting.engine as engine_module  # noqa: E402
import trading_bot_v2.backtesting.performance as performance_module  # noqa: E402
import trading_bot_v2.backtesting.simulated_exchange as exchange_module  # noqa: E402
from trading_bot_v2.config import config  # noqa: E402


def clean_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", choices=["current", "head"], required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--start", default="2024-03-01")
    parser.add_argument("--end", default="2024-03-22")
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()
    name = f"{args.revision}_{args.symbol}_{args.strategy}_{args.start}_{args.end}"
    if args.suffix:
        name += "_" + args.suffix
    logger.remove()
    logger.add(str(OUTPUT / f"{name}.log"), level="WARNING")
    # dotenv is allowed to load ordinary strategy settings, then safety settings
    # are forced again. Never serialize the entire configuration/environment.
    os.environ["DATA_AUTODOWNLOAD"] = "false"
    os.environ["DATABASE_BACKEND"] = "sqlite"
    os.environ["DATABASE_PATH"] = str(OUTPUT / "isolated.sqlite")
    os.environ["BACKTEST_SEED"] = "0"
    config.backtest_data_dir = str(ROOT / "trading_bot_v2/backtesting/data")
    config.backtest_strategy = ""
    config.backtest_funding_model = "historical"
    config.backtest_hedge_mode = False
    fingerprints = {}
    for module in (exchange_module, performance_module, engine_module):
        relative = Path(module.__file__).relative_to(ROOT).as_posix()
        source = (
            subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
            if args.revision == "head"
            else Path(module.__file__).read_bytes()
        )
        fingerprints[relative] = hashlib.sha256(source).hexdigest()
        if args.revision == "head":
            exec(compile(source, relative, "exec"), module.__dict__)
    instances = []
    base_exchange = exchange_module.SimulatedExchange

    class ObservedExchange(base_exchange):
        def __init__(self, *values, **options):
            super().__init__(*values, **options)
            instances.append(self)

    engine_module.SimulatedExchange = ObservedExchange
    visibility_checks = 0
    if args.revision == "current":
        original_completed = engine_module.BacktestEngine._completed_idx

        def checked_completed(mapping, timestamps, stamp, minutes):
            nonlocal visibility_checks
            index = original_completed(mapping, timestamps, stamp, minutes)
            if index >= 0:
                assert datetime.fromisoformat(timestamps[index]) + timedelta(
                    minutes=minutes
                ) <= datetime.fromisoformat(stamp) + timedelta(minutes=5)
            visibility_checks += 1
            return index

        engine_module.BacktestEngine._completed_idx = staticmethod(checked_completed)
    started = time.monotonic()
    result = engine_module.BacktestEngine().run(
        args.start,
        args.end,
        args.symbol,
        10000,
        strategy_filter=None if args.strategy == "all" else args.strategy,
    )
    ex = instances[-1]
    gross = sum(trade.get("pnl", 0) for trade in ex.trade_log)
    fees = sum(trade.get("fee", 0) for trade in ex.trade_log)
    unrealised = sum(position.unrealised_pnl for position in ex._positions.values())
    expected = 10000 + gross - fees + ex.total_funding + unrealised
    stamps = [datetime.fromisoformat(trade["timestamp"]) for trade in ex.trade_log]
    audits = {
        "finite_equity": math.isfinite(result.final_equity),
        "accounting_residual": result.final_equity - expected,
        "trade_timestamps_monotonic": stamps == sorted(stamps),
        "trade_timestamps_in_window": all(
            datetime.fromisoformat(args.start)
            <= stamp
            <= datetime.fromisoformat(args.end) + timedelta(days=1)
            for stamp in stamps
        ),
        "completed_candle_checks": visibility_checks,
        "terminal_cash": ex.balance,
        "terminal_unrealised": unrealised,
        "terminal_positions": ex.get_positions(),
    }
    effective_config = {
        key: value
        for key, value in vars(config).items()
        if key.startswith("backtest_") and isinstance(value, (str, bool, int, float))
    }
    payload = {
        "arguments": vars(args),
        "source_sha256": fingerprints,
        "effective_backtest_config": effective_config,
        "seconds": time.monotonic() - started,
        "audits": audits,
        "result": asdict(result),
    }
    (OUTPUT / f"{name}.json").write_text(
        json.dumps(clean_json(payload), indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "run": name,
                "equity": result.final_equity,
                "fills": result.total_trades,
                "closed": result.closed_trades,
                "residual": audits["accounting_residual"],
                "seconds": payload["seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
