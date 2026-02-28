"""
Download historical OHLCV data from Binance public API.

No API key required. Uses SUI/USDT as a price proxy for SUI-USDC
(same underlying asset, USDT/USDC spread is negligible).

Downloads all 5 timeframes required by the backtesting engine:
  1m, 5m, 15m, 1h, 4h

Saves CSV files to BACKTEST_DATA_DIR matching BacktestDataLoader's
expected naming convention: {symbol}_{timeframe}.csv

Usage:
    python -m trading_bot_v2.backtesting.download_data
    python -m trading_bot_v2.backtesting.download_data --start 2024-01-01 --end 2024-12-31
    python -m trading_bot_v2.backtesting.download_data --timeframes 1h 4h
"""

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd
from loguru import logger


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Maps our internal symbol names to Binance trading pair symbols
SYMBOL_MAP = {
    "SUI-USDC": "SUIUSDT",
    "BTC-USDC": "BTCUSDT",
    "ETH-USDC": "ETHUSDT",
    "SOL-USDC": "SOLUSDT",
    "BNB-USDC": "BNBUSDT",
}

# Map engine timeframes to Binance interval codes
INTERVAL_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "1h":  "1h",
    "4h":  "4h",
}

# Approx candles per request window (Binance max is 1000)
BINANCE_LIMIT = 1000


def fetch_klines(interval: str, start_ms: int, end_ms: int, binance_symbol: str = "SUIUSDT") -> list:
    """Fetch all klines for a given interval between start_ms and end_ms (epoch ms)."""
    all_rows = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": binance_symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": BINANCE_LIMIT,
        }
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()

        if not rows:
            break

        all_rows.extend(rows)

        # Advance to the candle after the last one returned
        last_open_time = rows[-1][0]
        current_start = last_open_time + 1

        logger.info(f"  [{interval}] fetched {len(all_rows)} candles so far "
                    f"(last: {datetime.fromtimestamp(last_open_time/1000, tz=timezone.utc).date()})")

        # Polite rate-limiting: ~5 requests/sec (well within Binance's 1200/min)
        time.sleep(0.2)

    return all_rows


def rows_to_dataframe(rows: list) -> pd.DataFrame:
    """Convert Binance klines list to a clean DataFrame."""
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trade_count",
        "taker_buy_volume", "taker_buy_quote", "ignore",
    ])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S")
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


def download(
    symbol: str = "SUI-USDC",
    start: str = "2024-01-01",
    end: str = "2024-12-31",
    timeframes: list = None,
    data_dir: str = "trading_bot_v2/backtesting/data",
) -> None:
    if timeframes is None:
        timeframes = list(INTERVAL_MAP.keys())

    binance_symbol = SYMBOL_MAP.get(symbol, symbol.replace("-", "").replace("/", "") + "T")
    out_dir = Path(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Convert date strings to epoch ms
    start_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000  # inclusive of end date

    logger.info(f"Downloading {symbol} data from Binance ({binance_symbol})")
    logger.info(f"Range: {start} -> {end}")
    logger.info(f"Timeframes: {timeframes}")
    logger.info(f"Output: {out_dir.resolve()}")

    for tf in timeframes:
        interval = INTERVAL_MAP[tf]
        out_path = out_dir / f"{symbol.replace('/', '_')}_{tf}.csv"

        if out_path.exists():
            logger.info(f"[{tf}] Already exists: {out_path} -- skipping (delete to re-download)")
            continue

        logger.info(f"[{tf}] Fetching from Binance...")
        rows = fetch_klines(interval, start_ms, end_ms, binance_symbol)

        if not rows:
            logger.warning(f"[{tf}] No data returned from Binance")
            continue

        df = rows_to_dataframe(rows)
        df.to_csv(out_path, index=False)
        logger.info(f"[{tf}] Saved {len(df)} candles to {out_path}")

    logger.info("Download complete.")


def main():
    parser = argparse.ArgumentParser(description="Download historical OHLCV data from Binance")
    parser.add_argument("--start", default="2024-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--symbol", default="SUI-USDC", help="Symbol name for file naming")
    parser.add_argument(
        "--timeframes", nargs="+",
        default=["1m", "5m", "15m", "1h", "4h"],
        choices=list(INTERVAL_MAP.keys()),
        help="Timeframes to download",
    )
    parser.add_argument(
        "--data-dir", default="trading_bot_v2/backtesting/data",
        help="Output directory for CSV files",
    )
    args = parser.parse_args()

    download(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        timeframes=args.timeframes,
        data_dir=args.data_dir,
    )


if __name__ == "__main__":
    main()
