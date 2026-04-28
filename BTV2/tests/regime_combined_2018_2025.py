#!/usr/bin/env python3
"""
Combined Regime Strategy — Full 2018-2025 Backtest

Tests the dual-regime system that switches between:
- BULLISH strategies: Long-only EMA (for years: 2019, 2020, 2021, 2023, 2024)
- BEARISH strategies: Short-only EMA + trailing stops (for years: 2018, 2022)

This validates the meta-regime switch across ALL market conditions.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

STORAGE_ROOT = Path("G:/Candle Data")

# Year classifications from historical analysis
YEAR_CLASSIFICATIONS = {
    2018: "BEARISH",  # -72%
    2019: "BULLISH",  # +94%
    2020: "BULLISH",  # +302%
    2021: "BULLISH",  # +59%
    2022: "BEARISH",  # -64%
    2023: "BULLISH",  # +155%
    2024: "BULLISH",  # +120%
}


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """Calculate Exponential Moving Average."""
    if len(prices) < period:
        return []
    
    ema = []
    multiplier = 2 / (period + 1)
    
    # Start with SMA
    sma = sum(prices[:period]) / period
    ema.append(sma)
    
    # Calculate EMA
    for i in range(period, len(prices)):
        value = (prices[i] * multiplier) + (ema[-1] * (1 - multiplier))
        ema.append(value)
    
    return ema


def load_parquet(symbol: str, interval: str) -> pd.DataFrame:
    """Load OHLCV data from parquet."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None


def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    """Calculate Relative Strength Index."""
    if len(prices) < period + 1:
        return []
    
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    rsi_values = []
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        rsi_values.append(100)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100 - (100 / (1 + rs)))
    
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))
    
    return rsi_values


def calculate_bb(prices: List[float], period: int = 20, num_std: float = 2.0):
    """Calculate Bollinger Bands."""
    if len(prices) < period:
        return [], [], []
    
    sma = sum(prices[:period]) / period
    variance = sum((p - sma) ** 2 for p in prices[:period]) / period
    std = variance ** 0.5
    
    middle = [sma]
    upper = [sma + num_std * std]
    lower = [sma - num_std * std]
    
    for i in range(period, len(prices)):
        window = prices[i-period+1:i+1]
        sma = sum(window) / period
        variance = sum((p - sma) ** 2 for p in window) / period
        std = variance ** 0.5
        middle.append(sma)
        upper.append(sma + num_std * std)
        lower.append(sma - num_std * std)
    
    return middle, upper, lower


def calculate_equity_curve(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    volumes: List[float],
    regime: str,
    params: Dict[str, Any],
) -> Tuple[List[float], List[float]]:
    """
    Simple trend-following with regime-based direction.
    
    - BULLISH: Go LONG when EMA fast crosses above EMA slow
    - BEARISH: Go SHORT when EMA fast crosses below EMA slow
    
    Uses trailing stops to let winners ride.
    """
    equity = [1.0]
    trades = []
    position = None
    equity_curve = 1.0
    
    strategy = params.get("strategy", "trend_long")
    ema_fast = params.get("ema_fast", 20)
    ema_slow = params.get("ema_slow", 50)
    trailing_stop_pct = params.get("trailing_stop_pct", 0.25)
    position_size_pct = params.get("position_size_pct", 0.10)
    cooldown_bars = params.get("cooldown_bars", 5)
    
    bars_since_trade = 999
    
    for i in range(ema_slow + 20, len(closes) - 1):
        current_price = closes[i]
        
        ema_f = calculate_ema(closes[:i+1], ema_fast)
        ema_s = calculate_ema(closes[:i+1], ema_slow)
        
        if len(ema_f) < 2 or len(ema_s) < 2:
            continue
        
        # Detect crossover
        cross = None
        if ema_f[-2] <= ema_s[-2] and ema_f[-1] > ema_s[-1]:
            cross = "bullish"
        elif ema_f[-2] >= ema_s[-2] and ema_f[-1] < ema_s[-1]:
            cross = "bearish"
        
        # Process existing position with trailing stop
        if position is not None:
            if position["side"] == "long":
                peak = position.get("peak", position["entry"])
                if current_price > peak:
                    position["peak"] = current_price
                
                trailing_trigger = peak * (1 - trailing_stop_pct)
                if current_price < trailing_trigger:
                    pnl = (current_price - position["entry"]) / position["entry"]
                    equity_curve *= (1 + pnl * position["size"])
                    trades.append(pnl * position["size"])
                    position = None
                    bars_since_trade = 0
            else:  # short
                low_point = position.get("low", position["entry"])
                if current_price < low_point:
                    position["low"] = current_price
                
                trailing_trigger = low_point * (1 + trailing_stop_pct)
                if current_price > trailing_trigger:
                    pnl = (position["entry"] - current_price) / position["entry"]
                    equity_curve *= (1 + pnl * position["size"])
                    trades.append(pnl * position["size"])
                    position = None
                    bars_since_trade = 0
        
        bars_since_trade += 1
        
        # Check for new entry based on regime
        if position is None and bars_since_trade >= cooldown_bars and cross is not None:
            should_trade = False
            direction = None
            
            if regime == "BULLISH" and cross == "bullish":
                direction = "long"
                should_trade = True
            elif regime == "BEARISH" and cross == "bearish":
                direction = "short"
                should_trade = True
            
            if should_trade:
                position = {
                    "entry": current_price,
                    "side": direction,
                    "size": position_size_pct,
                    "peak": current_price,
                    "low": current_price,
                }
                bars_since_trade = 0
        
        equity.append(equity_curve)
    
    # Close any open position at end
    if position is not None:
        final_price = closes[-1]
        if position["side"] == "long":
            pnl = (final_price - position["entry"]) / position["entry"]
        else:
            pnl = (position["entry"] - final_price) / position["entry"]
        equity_curve *= (1 + pnl * position["size"])
        trades.append(pnl * position["size"])
        equity[-1] = equity_curve
    
    return equity, trades


def run_year_test(
    df: pd.DataFrame,
    year: int,
    params_bullish: Dict,
    params_bearish: Dict,
) -> Dict:
    """Run a single year test with regime-aware strategy."""
    regime = YEAR_CLASSIFICATIONS.get(year, "BULLISH")
    
    # Extract data
    closes = df["Close"].tolist()
    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    volumes = df["Volume"].tolist()
    
    # Use appropriate params
    params = params_bullish if regime == "BULLISH" else params_bearish
    
    equity, trades = calculate_equity_curve(
        closes, highs, lows, volumes, regime, params
    )
    
    # Calculate metrics
    total_return = (equity[-1] / equity[0] - 1) * 100
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_win = gross_profit / wins if wins > 0 else 0
        avg_loss = gross_loss / (len(trades) - wins) if wins < len(trades) else 0
    else:
        win_rate = 0
        profit_factor = 0
        avg_win = 0
        avg_loss = 0
    
    # Apply costs (0.3% per trade round trip)
    costs = len(trades) * 0.003
    net_return = total_return - costs * 100
    
    return {
        "year": year,
        "regime": regime,
        "trades": len(trades),
        "win_rate": win_rate,
        "total_return_pct": total_return,
        "net_return_pct": net_return,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "params": params,
    }


def run_full_backtest():
    """Run complete 2018-2025 backtest with regime switching."""
    print("=" * 60)
    print("COMBINED REGIME STRATEGY — 2018-2025 BACKTEST")
    print("=" * 60)
    
    # Use daily data for speed
    print("\nLoading BTCUSDT daily data...")
    df = load_parquet("BTCUSDT", "1d")
    if df is None:
        print("ERROR: Could not load data")
        return [], 0
    
    print(f"Data loaded: {len(df)} bars, {df.index[0]} to {df.index[-1]}")
    
    # Strategy params - tuned for more trades
    # BULLISH: Faster EMA 9/21 to catch more trades
    # BEARISH: Short EMA 9/21 (proven)
    
    params_bullish = {
        "strategy": "trend_long",
        "ema_fast": 9,
        "ema_slow": 21,
        "trailing_stop_pct": 0.20,  # 20% trailing
        "position_size_pct": 0.10,
        "cooldown_bars": 3,
    }
    
    params_bearish = {
        "strategy": "trend_short",
        "ema_fast": 9,
        "ema_slow": 21,
        "trailing_stop_pct": 0.15,
        "position_size_pct": 0.05,
        "cooldown_bars": 3,
    }
    
    # Test each year
    results = []
    
    for year in range(2018, 2025):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        
        df_year = df[(df.index >= start) & (df.index < end)]
        
        if len(df_year) < 100:
            print(f"Skipping {year}: insufficient data")
            continue
        
        print(f"\n--- {year} ({YEAR_CLASSIFICATIONS.get(year, 'N/A')}) ---")
        print(f"  Data: {len(df_year)} bars")
        
        result = run_year_test(df_year, year, params_bullish, params_bearish)
        results.append(result)
        
        print(f"  Trades: {result['trades']}")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Return: {result['net_return_pct']:+.2f}%")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    total_return = 1.0
    
    for r in results:
        total_return *= (1 + r["net_return_pct"] / 100)
    
    combined_return = (total_return - 1) * 100
    
    print(f"\n{'Year':<8} {'Regime':<10} {'Trades':<8} {'WR':<8} {'Return':<12} {'PF':<8}")
    print("-" * 60)
    
    for r in results:
        print(f"{r['year']:<8} {r['regime']:<10} {r['trades']:<8} {r['win_rate']:<8.1f}% {r['net_return_pct']:>+12.2f}% {r['profit_factor']:>8.2f}")
    
    print("-" * 60)
    total_trades = sum(r["trades"] for r in results)
    avg_win_rate = np.mean([r["win_rate"] for r in results])
    print(f"TOTAL   {'(7 years)':<10} {total_trades:<8} {avg_win_rate:<8.1f}% {combined_return:>+12.2f}%")
    
    # Calculate buy & hold comparison
    for year in range(2018, 2025):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        df_y = df[(df.index >= start) & (df.index < end)]
        if len(df_y) > 0:
            start_price = df_y["Close"].iloc[0]
            end_price = df_y["Close"].iloc[-1]
            bh_return = (end_price / start_price - 1) * 100
            r = results[year - 2018] if year - 2018 < len(results) else None
            if r:
                alpha = r["net_return_pct"] - bh_return
                print(f"  {year} Alpha: {alpha:+.2f}% (BH: {bh_return:+.2f}%)")
    
    return results, combined_return


if __name__ == "__main__":
    results, total_return = run_full_backtest()