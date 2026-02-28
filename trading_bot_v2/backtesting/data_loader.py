"""
Historical Data Loader
======================

Loads OHLCV candles from local parquet cache or fetches from
public APIs (CoinGecko, Binance public, or raw CSV).

Provides the same interface as MultiTimeframeFetcher so the
live strategy code sees no difference between backtest and live.

Timeframes provided: 1m, 5m, 15m, 1h, 4h
Minimum history for regime detection: 29 × 4h candles (4.8 days)
"""

import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
from loguru import logger


class BacktestDataLoader:
    """
    Loads multi-timeframe historical candles for backtesting.

    Usage:
        loader = BacktestDataLoader(symbol="SUI-USDC", data_dir="backtesting/data")
        candles = loader.get_candles("4h", start="2024-01-01", end="2024-12-31")
    """

    SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

    def __init__(self, symbol: str, data_dir: str = "backtesting/data"):
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, pd.DataFrame] = {}

    def get_candles(
        self,
        timeframe: str,
        start: str,
        end: str,
    ) -> Dict[str, List]:
        """
        Returns candles in the same format as MultiTimeframeFetcher.

        Returns dict with keys: open, high, low, close, volume, timestamp
        Each value is a list ordered oldest → newest.
        """
        df = self._load_or_fetch(timeframe, start, end)
        mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
        df = df[mask].copy()
        return {
            "open": df["open"].tolist(),
            "high": df["high"].tolist(),
            "low": df["low"].tolist(),
            "close": df["close"].tolist(),
            "volume": df["volume"].tolist(),
            "timestamp": df["timestamp"].tolist(),
        }

    def _load_or_fetch(self, timeframe: str, start: str, end: str) -> pd.DataFrame:
        cache_key = f"{self.symbol}_{timeframe}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        base = self.symbol.replace("/", "_")
        parquet_path = self.data_dir / f"{base}_{timeframe}.parquet"
        csv_path = self.data_dir / f"{base}_{timeframe}.csv"

        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            logger.info(f"Loaded {len(df)} {timeframe} candles from {parquet_path}")
        elif csv_path.exists():
            df = pd.read_csv(csv_path, dtype={"timestamp": str})
            logger.info(f"Loaded {len(df)} {timeframe} candles from {csv_path}")
        else:
            df = self._fetch_from_api(timeframe, start, end)
            df.to_csv(csv_path, index=False)
            logger.info(f"Fetched and cached {len(df)} {timeframe} candles to {csv_path}")

        self._cache[cache_key] = df
        return df

    def _fetch_from_api(self, timeframe: str, start: str, end: str) -> pd.DataFrame:
        """
        Fetch historical data from public API.

        Priority order:
        1. Binance public API (most liquid, good history)
        2. CoinGecko (fallback, 1-day granularity only)
        3. Raw CSV in data_dir (manual import fallback)

        For SUI-USDC on Pacifica testnet: use Binance SUI-USDT
        as the price proxy (same underlying, negligible USDT/USDC spread).
        """
        raise NotImplementedError(
            f"Auto-fetch not yet implemented for {timeframe}. "
            f"Place a CSV file at {self.data_dir}/{self.symbol}_{timeframe}.csv "
            f"with columns: timestamp,open,high,low,close,volume"
        )
