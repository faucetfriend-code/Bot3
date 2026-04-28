"""Download historical BTCUSDT 5m and 1m data from Binance for 2018-2024."""

import time
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent.parent / "trading_bot_v2" / "backtesting" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BINANCE_KLINE = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch all klines between start_ms and end_ms (inclusive)."""
    all_rows = []
    current = start_ms
    while current <= end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(BINANCE_KLINE, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_rows.extend(data)
        current = data[-1][0] + 1
        time.sleep(0.1)  # rate limit

    df = pd.DataFrame(all_rows, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].set_index("timestamp")
    return df


def download_year(symbol: str, interval: str, year: int) -> pd.DataFrame:
    """Download a full year of klines."""
    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="UTC") - pd.Timedelta(seconds=1)
    print(f"Downloading {symbol} {interval} for {year}...")
    return fetch_klines(symbol, interval, int(start.timestamp() * 1000), int(end.timestamp() * 1000))


def main():
    years = list(range(2018, 2025))
    symbol = "BTCUSDT"

    for interval in ["5m", "1m"]:
        print(f"\n{'=' * 60}")
        print(f"Downloading {interval} data for {years[0]}-{years[-1]}")
        print(f"{'=' * 60}")

        all_frames = []
        for year in years:
            out_path = DATA_DIR / f"BTC-USDC_{interval}_{year}.csv"
            if out_path.exists():
                print(f"  {year}: already exists, loading...")
                df = pd.read_csv(out_path, parse_dates=["timestamp"], index_col="timestamp")
                print(f"  {year}: {len(df)} bars ({df.index.min()} -> {df.index.max()})")
                all_frames.append(df)
                continue

            df = download_year(symbol, interval, year)
            print(f"  {year}: {len(df)} bars ({df.index.min()} -> {df.index.max()})")
            
            # Save each year separately
            df.to_csv(out_path)
            print(f"  Saved to {out_path}")
            all_frames.append(df)

        # Combine all years
        combined = pd.concat(all_frames).sort_index()
        combined = combined[~combined.index.duplicated(keep="first")]

        out_path = DATA_DIR / f"BTC-USDC_{interval}.csv"
        combined.to_csv(out_path)
        print(f"\nSaved combined {len(combined)} bars to {out_path}")


if __name__ == "__main__":
    main()
