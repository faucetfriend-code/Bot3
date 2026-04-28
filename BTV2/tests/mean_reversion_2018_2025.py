#!/usr/bin/env python3
"""
Mean Reversion Strategy — 2018-2025 Validation

RSI + Bollinger Bands mean reversion with regime-aware direction:
- BULLISH: Go LONG when oversold, sell when overbought
- BEARISH: Go SHORT when overbought, cover when oversold
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


def calculate_bb(prices: List[float], period: int = 20, num_std: float = 2.0):
    if len(prices) < period:
        return [], [], []
    middle = []
    upper = []
    lower = []
    for i in range(period - 1, len(prices)):
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
    regime: str,
    params: Dict[str, Any],
) -> Tuple[List[float], List[float]]:
    """Mean Reversion with regime-based direction."""
    equity = [1.0]
    trades = []
    position = None
    equity_curve = 1.0
    
    rsi_oversold = params.get("rsi_oversold", 25)
    rsi_overbought = params.get("rsi_overbought", 75)
    bb_proximity = params.get("bb_proximity", 0.05)
    position_size_pct = params.get("position_size_pct", 0.05)
    cooldown_bars = params.get("cooldown_bars", 3)
    
    bars_since_trade = 999
    
    for i in range(50, len(closes) - 1):
        current_price = closes[i]
        
        rsi = calculate_rsi(closes[:i+1], 14)
        _, upper_bb, lower_bb = calculate_bb(closes[:i+1], 20)
        
        if len(rsi) < 2 or len(lower_bb) < 2:
            continue
        
        # Process position
        if position is not None:
            exit_signal = False
            if position["side"] == "long":
                # Exit when RSI overbought or near upper BB
                if rsi[-1] > rsi_overbought:
                    exit_signal = True
                if current_price > upper_bb[-1] * (1 - bb_proximity):
                    exit_signal = True
            else:  # short
                # Cover when RSI oversold or near lower BB
                if rsi[-1] < rsi_oversold:
                    exit_signal = True
                if current_price < lower_bb[-1] * (1 + bb_proximity):
                    exit_signal = True
            
            if exit_signal:
                if position["side"] == "long":
                    pnl = (current_price - position["entry"]) / position["entry"]
                else:
                    pnl = (position["entry"] - current_price) / position["entry"]
                equity_curve *= (1 + pnl * position["size"])
                trades.append(pnl * position["size"])
                position = None
                bars_since_trade = 0
        
        bars_since_trade += 1
        
        # Check for new entry
        if position is None and bars_since_trade >= cooldown_bars:
            entry_signal = False
            direction = None
            
            if regime == "BULLISH":
                # Buy when oversold and near lower BB
                if rsi[-1] < rsi_oversold and current_price < lower_bb[-1] * (1 + bb_proximity):
                    entry_signal = True
                    direction = "long"
            elif regime == "BEARISH":
                # Short when overbought and near upper BB
                if rsi[-1] > rsi_overbought and current_price > upper_bb[-1] * (1 - bb_proximity):
                    entry_signal = True
                    direction = "short"
            
            if entry_signal:
                position = {
                    "entry": current_price,
                    "side": direction,
                    "size": position_size_pct,
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
    
    costs = len(trades) * 0.003
    net_return = total_return - costs * 100
    
    return {
        "year": year, "regime": regime, "trades": len(trades),
        "win_rate": win_rate, "net_return_pct": net_return,
        "profit_factor": profit_factor,
    }


def run_backtest():
    print("=" * 60)
    print("MEAN REVERSION STRATEGY — 2018-2025 VALIDATION")
    print("=" * 60)
    
    df = load_parquet("BTCUSDT", "1d")
    if df is None:
        print("ERROR: Could not load data")
        return
    
    print(f"Data: {len(df)} bars, {df.index[0]} to {df.index[-1]}")
    
    # Production params (from mean_reversion.py)
    params = {
        "rsi_oversold": 25,
        "rsi_overbought": 75,
        "bb_proximity": 0.05,
        "position_size_pct": 0.05,
        "cooldown_bars": 3,
    }
    
    results = []
    for year in range(2018, 2025):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        df_year = df[(df.index >= start) & (df.index < end)]
        
        if len(df_year) < 100:
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