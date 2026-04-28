#!/usr/bin/env python3
"""
Liquidation Capture Strategy — 2018-2025 Validation (15m Data)

Tests liquidation capture with proper intraday data:
- Uses 15m timeframe for more frequent large moves
- 3% threshold should trigger more signals
- Regime-aware direction
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

STORAGE_ROOT = Path("G:/Candle Data")

YEAR_CLASSIFICATIONS = {
    2018: "BEARISH", 2019: "BULLISH", 2020: "BULLISH",
    2021: "BULLISH", 2022: "BEARISH", 2023: "BULLISH", 2024: "BULLISH",
}


def load_parquet(symbol: str, interval: str) -> pd.DataFrame:
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None


def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
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


def calculate_equity_curve(
    closes: List[float],
    regime: str,
    params: Dict[str, Any],
) -> Tuple[List[float], List[float]]:
    """Liquidation Capture with regime-based direction."""
    equity = [1.0]
    trades = []
    position = None
    equity_curve = 1.0
    
    price_threshold = params.get("price_threshold", 0.030)
    rsi_threshold = params.get("rsi_threshold", 18.0)
    trailing_stop_pct = params.get("trailing_stop_pct", 0.15)
    position_size_pct = params.get("position_size_pct", 0.05)
    cooldown_bars = params.get("cooldown_bars", 5)
    
    bars_since_trade = 999
    
    for i in range(50, len(closes) - 1):
        current_price = closes[i]
        
        # Detect large price movements
        if i < 2:
            continue
        
        price_change = (closes[i] - closes[i-1]) / closes[i-1]
        price_change_pct = abs(price_change)
        
        rsi = calculate_rsi(closes[:i+1], 14)
        
        if price_change_pct < price_threshold or len(rsi) < 2:
            # Process existing position
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
                else:
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
            equity.append(equity_curve)
            bars_since_trade += 1
            continue
        
        # Large move detected
        liquidation_signal = False
        direction = None
        
        if price_change > 0 and rsi[-1] < rsi_threshold:
            # Large up move + oversold = bullish
            liquidation_signal = True
            direction = "long" if regime == "BULLISH" else "short"
        elif price_change < 0 and rsi[-1] > (100 - rsi_threshold):
            # Large down move + overbought = bearish
            liquidation_signal = True
            direction = "short" if regime == "BULLISH" else "long"
        
        # Process existing position first
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
            else:
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
        
        # Enter on liquidation signal
        if position is None and bars_since_trade >= cooldown_bars and liquidation_signal:
            position = {
                "entry": current_price,
                "side": direction,
                "size": position_size_pct,
                "peak": current_price,
                "low": current_price,
            }
            bars_since_trade = 0
        
        equity.append(equity_curve)
    
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


def run_year_test(df: pd.DataFrame, year: int, params: Dict) -> Dict:
    regime = YEAR_CLASSIFICATIONS.get(year, "BULLISH")
    closes = df["Close"].tolist()
    
    equity, trades = calculate_equity_curve(closes, regime, params)
    
    total_return = (equity[-1] / equity[0] - 1) * 100
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        win_rate = 0
        profit_factor = 0
    
    # Higher costs for intraday (more trades)
    costs = len(trades) * 0.003
    net_return = total_return - costs * 100
    
    return {
        "year": year, "regime": regime, "trades": len(trades),
        "win_rate": win_rate, "net_return_pct": net_return,
        "profit_factor": profit_factor,
    }


def run_backtest():
    print("=" * 60)
    print("LIQUIDATION CAPTURE STRATEGY — 2018-2025 (15m)")
    print("=" * 60)
    
    # Use 15m data
    print("\nLoading BTCUSDT 15m data...")
    df = load_parquet("BTCUSDT", "15m")
    if df is None:
        print("ERROR: Could not load data")
        return
    
    print(f"Data: {len(df)} bars, {df.index[0]} to {df.index[-1]}")
    
    # Sample every 4th bar to speed up (~15min -> 1hr equivalent)
    df = df.iloc[::4].reset_index(drop=True)
    print(f"Sampled: {len(df)} bars")
    
    # Production params - lower threshold for more signals
    params = {
        "price_threshold": 0.015,  # 1.5% (lower for intraday)
        "rsi_threshold": 25.0,  # More flexible
        "trailing_stop_pct": 0.10,  # 10% trailing
        "position_size_pct": 0.05,
        "cooldown_bars": 3,
    }
    
    results = []
    for year in range(2018, 2025):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        df_year = df[(df.index >= start) & (df.index < end)]
        
        if len(df_year) < 1000:  # Need more bars for 15m
            print(f"Skipping {year}: insufficient data ({len(df_year)} bars)")
            continue
        
        result = run_year_test(df_year, year, params)
        results.append(result)
        
        print(f"\n{year} ({result['regime']}): Trades={result['trades']}, WR={result['win_rate']:.1f}%, "
              f"Return={result['net_return_pct']:+.2f}%, PF={result['profit_factor']:.2f}")
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    total_return = 1.0
    for r in results:
        total_return *= (1 + r["net_return_pct"] / 100)
    combined_return = (total_return - 1) * 100
    
    total_trades = sum(r["trades"] for r in results)
    avg_wr = np.mean([r["win_rate"] for r in results])
    
    print(f"\n{'Year':<8} {'Regime':<10} {'Trades':<8} {'WR':<8} {'Return':<12} {'PF':<8}")
    print("-" * 60)
    for r in results:
        print(f"{r['year']:<8} {r['regime']:<10} {r['trades']:<8} {r['win_rate']:<8.1f}% "
              f"{r['net_return_pct']:>+12.2f}% {r['profit_factor']:>8.2f}")
    
    print("-" * 60)
    print(f"TOTAL   {'(7 years)':<10} {total_trades:<8} {avg_wr:<8.1f}% {combined_return:>+12.2f}%")
    
    return results, combined_return


if __name__ == "__main__":
    results, total_return = run_backtest()