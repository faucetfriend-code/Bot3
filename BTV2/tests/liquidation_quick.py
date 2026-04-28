#!/usr/bin/env python3
"""
Liquidation Capture - Quick 6-month validation per year
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


def run_liq_test(df: pd.DataFrame, params: Dict) -> Dict:
    """Quick test on a dataframe."""
    closes = df["Close"].tolist()
    
    trades = []
    position = None
    equity_curve = 1.0
    
    price_threshold = params.get("price_threshold", 0.015)
    rsi_threshold = params.get("rsi_threshold", 25.0)
    trailing_stop_pct = params.get("trailing_stop_pct", 0.10)
    position_size_pct = params.get("position_size_pct", 0.05)
    cooldown_bars = params.get("cooldown_bars", 3)
    
    bars_since_trade = 999
    
    for i in range(50, len(closes) - 1):
        current_price = closes[i]
        
        if i < 2:
            continue
        
        price_change = (closes[i] - closes[i-1]) / closes[i-1]
        price_change_pct = abs(price_change)
        
        rsi = calculate_rsi(closes[:i+1], 14)
        
        if price_change_pct < price_threshold or len(rsi) < 2:
            if position is not None:
                if position["side"] == "long":
                    peak = position.get("peak", position["entry"])
                    if current_price > peak:
                        position["peak"] = current_price
                    trailing_trigger = peak * (1 - trailing_stop_pct)
                    if current_price < trailing_trigger:
                        pnl = (current_price - position["entry"]) / position["entry"]
                        equity_curve *= (1 + pnl * position["size"])
                        trades.append(pnl)
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
                        trades.append(pnl)
                        position = None
                        bars_since_trade = 0
            bars_since_trade += 1
            continue
        
        liquidation_signal = False
        direction = None
        
        if price_change > 0 and rsi[-1] < rsi_threshold:
            liquidation_signal = True
            direction = "long"
        elif price_change < 0 and rsi[-1] > (100 - rsi_threshold):
            liquidation_signal = True
            direction = "short"
        
        if position is not None:
            if position["side"] == "long":
                peak = position.get("peak", position["entry"])
                if current_price > peak:
                    position["peak"] = current_price
                trailing_trigger = peak * (1 - trailing_stop_pct)
                if current_price < trailing_trigger:
                    pnl = (current_price - position["entry"]) / position["entry"]
                    equity_curve *= (1 + pnl * position["size"])
                    trades.append(pnl)
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
                    trades.append(pnl)
                    position = None
                    bars_since_trade = 0
        
        bars_since_trade += 1
        
        if position is None and bars_since_trade >= cooldown_bars and liquidation_signal:
            position = {
                "entry": current_price,
                "side": direction,
                "size": position_size_pct,
                "peak": current_price,
                "low": current_price,
            }
            bars_since_trade = 0
    
    if position is not None:
        final_price = closes[-1]
        if position["side"] == "long":
            pnl = (final_price - position["entry"]) / position["entry"]
        else:
            pnl = (position["entry"] - final_price) / position["entry"]
        equity_curve *= (1 + pnl * position["size"])
        trades.append(pnl)
    
    total_return = (equity_curve - 1) * 100
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
    else:
        win_rate = 0
    
    return {"trades": len(trades), "win_rate": win_rate, "return": total_return}


def main():
    print("=" * 60)
    print("LIQUIDATION CAPTURE - 6 MONTH VALIDATION")
    print("=" * 60)
    
    df = load_parquet("BTCUSDT", "15m")
    if df is None:
        print("ERROR: Could not load data")
        return
    
    print(f"Loaded {len(df)} bars")
    
    # Sample every 4th bar for speed - keep index
    df = df.iloc[::4].copy()
    print(f"Sampled to {len(df)} bars (~1hr candles)")
    
    params = {
        "price_threshold": 0.008,  # 0.8% - more signals
        "rsi_threshold": 30.0,  # More flexible
        "trailing_stop_pct": 0.08,  # 8% trailing
        "position_size_pct": 0.05,
        "cooldown_bars": 2,
    }
    
    results = []
    
    for year in range(2018, 2025):
        # Test first 6 months
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year}-07-01", tz="UTC")
        df_h1 = df[(df.index >= start) & (df.index < end)]
        
        if len(df_h1) < 500:
            continue
        
        regime = YEAR_CLASSIFICATIONS.get(year, "BULLISH")
        result = run_liq_test(df_h1, params)
        results.append({
            "year": year, "period": "H1", "regime": regime, **result
        })
        
        # Test second 6 months
        start2 = pd.Timestamp(f"{year}-07-01", tz="UTC")
        end2 = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        df_h2 = df[(df.index >= start2) & (df.index < end2)]
        
        if len(df_h2) < 500:
            continue
        
        result2 = run_liq_test(df_h2, params)
        results.append({
            "year": year, "period": "H2", "regime": regime, **result2
        })
        
        print(f"{year}H1 ({regime}): Trades={result['trades']}, WR={result['win_rate']:.0f}%, Return={result['return']:+.1f}%")
        print(f"{year}H2 ({regime}): Trades={result2['trades']}, WR={result2['win_rate']:.0f}%, Return={result2['return']:+.1f}%")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    total_return = 1.0
    for r in results:
        total_return *= (1 + r["return"] / 100)
    combined = (total_return - 1) * 100
    
    total_trades = sum(r["trades"] for r in results)
    avg_wr = np.mean([r["win_rate"] for r in results])
    
    print(f"Total: {total_trades} trades, {avg_wr:.0f}% WR, {combined:+.1f}% return")


if __name__ == "__main__":
    main()