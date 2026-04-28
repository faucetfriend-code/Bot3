#!/usr/bin/env python3
"""
MA Crossover Strategy — 2018-2025 Validation

Tests EMA/MA crossover across all market regimes with regime-aware direction:
- BULLISH: Go LONG on bullish crossover
- BEARISH: Go SHORT on bearish crossover

Strategy parameters from production.
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


def calculate_ema(prices: List[float], period: int) -> List[float]:
    if len(prices) < period:
        return []
    ema = []
    multiplier = 2 / (period + 1)
    sma = sum(prices[:period]) / period
    ema.append(sma)
    for i in range(period, len(prices)):
        value = (prices[i] * multiplier) + (ema[-1] * (1 - multiplier))
        ema.append(value)
    return ema


def calculate_sma(prices: List[float], period: int) -> List[float]:
    if len(prices) < period:
        return []
    sma = []
    for i in range(period - 1, len(prices)):
        sma.append(sum(prices[i-period+1:i+1]) / period)
    return sma


def load_parquet(symbol: str, interval: str) -> pd.DataFrame:
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None


def calculate_equity_curve(
    closes: List[float],
    regime: str,
    params: Dict[str, Any],
) -> Tuple[List[float], List[float]]:
    """MA Crossover with regime-based direction."""
    equity = [1.0]
    trades = []
    position = None
    equity_curve = 1.0
    
    ma_fast = params.get("ma_fast", 20)
    ma_slow = params.get("ma_slow", 50)
    trailing_stop_pct = params.get("trailing_stop_pct", 0.15)
    position_size_pct = params.get("position_size_pct", 0.05)
    cooldown_bars = params.get("cooldown_bars", 5)
    
    bars_since_trade = 999
    
    for i in range(ma_slow + 20, len(closes) - 1):
        current_price = closes[i]
        
        # Calculate both EMA and SMA for MA crossover
        ma_f = calculate_ema(closes[:i+1], ma_fast)
        ma_s = calculate_sma(closes[:i+1], ma_slow)
        
        if len(ma_f) < 2 or len(ma_s) < 2:
            continue
        
        # Detect crossover
        cross = None
        if ma_f[-2] <= ma_s[-2] and ma_f[-1] > ma_s[-1]:
            cross = "bullish"
        elif ma_f[-2] >= ma_s[-2] and ma_f[-1] < ma_s[-1]:
            cross = "bearish"
        
        # Process position with trailing stop
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
        
        # Check for new entry
        if position is None and bars_since_trade >= cooldown_bars and cross is not None:
            should_trade = False
            if regime == "BULLISH" and cross == "bullish":
                should_trade = True
            elif regime == "BEARISH" and cross == "bearish":
                should_trade = True
            
            if should_trade:
                direction = "long" if cross == "bullish" else "short"
                position = {
                    "entry": current_price,
                    "side": direction,
                    "size": position_size_pct,
                    "peak": current_price,
                    "low": current_price,
                }
                bars_since_trade = 0
        
        equity.append(equity_curve)
    
    # Close position at end
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
    print("MA CROSSOVER STRATEGY — 2018-2025 VALIDATION")
    print("=" * 60)
    
    df = load_parquet("BTCUSDT", "1d")
    if df is None:
        print("ERROR: Could not load data")
        return
    
    print(f"Data: {len(df)} bars, {df.index[0]} to {df.index[-1]}")
    
    # Production params (from ma_crossover.py)
    params = {
        "ma_fast": 20,
        "ma_slow": 50,
        "trailing_stop_pct": 0.15,
        "position_size_pct": 0.05,
        "cooldown_bars": 5,
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
    
    # Summary
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