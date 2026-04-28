"""
Mean Reversion v6 - Counter-Only
================================

After all iterations, aligned trades fail.
Only take counter (against trend) positions.

Key parameters:
- Only LONG when bearish (price < SMA)
- Only SHORT when bullish (price > SMA)  
- Use tight stops (1.0-1.5 ATR)
- Target 1:1 RR
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    apply_butterworth,
    compute_rsi,
    compute_bollinger,
    compute_sma,
    compute_atr,
    compute_adx,
    COST_PER_SIDE,
)
from strategies import INTERVAL_BARS_PER_YEAR

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"


class MeanRevCounterOnly:
    """Counter-trend only - fade the move, expect reversal."""
    
    def __init__(
        self,
        cutoff: float = 0.04,
        rsi_oversold: float = 20.0,
        rsi_overbought: float = 80.0,
        bb_proximity: float = 0.02,
        atr_stop: float = 1.5,
        target_r: float = 1.0,
        sma_period: int = 200,
        adx_max: float = 25.0,
    ):
        self.cutoff = cutoff
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_proximity = bb_proximity
        self.atr_stop = atr_stop
        self.target_r = target_r
        self.sma_period = sma_period
        self.adx_max = adx_max
    
    def run(self, df: pd.DataFrame) -> tuple[pd.Series, list]:
        fc = apply_butterworth(df["Close"], self.cutoff)
        rsi = compute_rsi(fc, 14)
        bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
        atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
        adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
        sma = compute_sma(fc, self.sma_period)
        
        prices = df["Close"].values.astype(float)
        highs = df["High"].values.astype(float)
        lows = df["Low"].values.astype(float)
        n = len(prices)
        
        equity_arr = np.ones(n, dtype=float)
        curr_equity = 1.0
        
        in_pos = [False]
        side = [""]
        entry_eq = [1.0]
        sl_price = [0.0]
        tp_price = [0.0]
        
        closed_trades = []
        
        for i in range(1, n):
            r = rsi.iloc[i]
            bbu = bb_up.iloc[i]
            bbl = bb_lo.iloc[i]
            at = atr.iloc[i]
            ad = adx.iloc[i]
            sm = sma.iloc[i]
            p = prices[i]
            p0 = prices[i-1]
            hi = highs[i]
            lo = lows[i]
            
            if np.any(np.isnan([r, bbu, bbl, at, ad, sm])):
                equity_arr[i] = curr_equity
                continue
            
            is_bull = p > sm
            
            # Only trade counter
            if in_pos[0]:
                curr_equity *= p / p0
                
                sl = sl_price[0]
                tp = tp_price[0]
                
                if side[0] == "long":
                    if lo <= sl or hi >= tp:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
                else:
                    if hi >= sl or lo <= tp:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
            else:
                # Only enter in ranging markets
                if ad >= self.adx_max:
                    equity_arr[i] = curr_equity
                    continue
                
                prox_lo = (p - bbl) / (bbl + 1e-10)
                prox_hi = (bbu - p) / (bbu + 1e-10)
                
                # LONG: Bear market (counter to trend)
                if r < self.rsi_oversold and 0.0 <= prox_lo <= self.bb_proximity:
                    if is_bull:
                        # Bull + Long = ALIGNED (skip!)
                        equity_arr[i] = curr_equity
                        continue
                    # Bear (downtrend) + LONG = counter - enter
                    sl = p - self.atr_stop * at
                    tp = p + self.target_r * self.atr_stop * at
                    
                    entry_eq[0] = curr_equity
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "long"
                    in_pos[0] = True
                
                # SHORT: Bull market (counter to trend)
                elif r > self.rsi_overbought and 0.0 <= prox_hi <= self.bb_proximity:
                    if not is_bull:
                        # Bear + Short = ALIGNED (skip!)
                        equity_arr[i] = curr_equity
                        continue
                    # Bull (uptrend) + SHORT = counter - enter
                    sl = p + self.atr_stop * at
                    tp = p - self.target_r * self.atr_stop * at
                    
                    entry_eq[0] = curr_equity
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "short"
                    in_pos[0] = True
            
            equity_arr[i] = curr_equity
        
        if in_pos[0]:
            curr_equity *= (1.0 - COST_PER_SIDE)
            closed_trades.append(curr_equity / entry_eq[0] - 1.0)
            equity_arr[-1] = curr_equity
        
        return pd.Series(equity_arr, index=df.index), closed_trades


def load_data(years):
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.columns = [c.lower() for c in df.columns]
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
        df = df.set_index("timestamp").sort_index()
        dfs.append(df)
    
    df_all = pd.concat(dfs).sort_index()
    
    df_1h = df_all.resample("1h").agg({
        "Open": "first", 
        "High": "max", 
        "Low": "min", 
        "Close": "last", 
        "Volume": "sum"
    })
    df_1h = df_1h.dropna()
    df_1h.index = df_1h.index.tz_localize(None)
    
    return df_1h


def test_windows(df, strat, windows):
    all_sharpes = []
    all_returns = []
    all_trades = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
        
        eq, trd = strat.run(df_test)
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.extend(trd)
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": 100 * len([t for t in all_trades if t > 0]) / len(all_trades) if all_trades else 0,
        "n_trades": len(all_trades),
    }


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    windows = [
        ("2022-01-01", "2022-03-31"),
        ("2022-04-01", "2022-06-30"),
        ("2022-07-01", "2022-09-30"),
        ("2022-10-01", "2022-12-31"),
        ("2023-01-01", "2023-03-31"),
        ("2023-04-01", "2023-06-30"),
        ("2023-07-01", "2023-09-30"),
        ("2023-10-01", "2023-12-31"),
    ]
    
    param_sets = [
        # v1: Base
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "atr_stop": 1.5, "target_r": 1.0, "sma_period": 200, "adx_max": 25,
        },
        # v2: Tighter
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "atr_stop": 1.0, "target_r": 1.0, "sma_period": 200, "adx_max": 25,
        },
        # v3: Wider ADX
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "atr_stop": 1.5, "target_r": 1.0, "sma_period": 200, "adx_max": 30,
        },
        # v4: RSI 25/75
        {
            "rsi_oversold": 25, "rsi_overbought": 75, "bb_proximity": 0.02,
            "atr_stop": 1.5, "target_r": 1.0, "sma_period": 200, "adx_max": 25,
        },
        # v5: Wider entry
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.04,
            "atr_stop": 1.5, "target_r": 1.0, "sma_period": 200, "adx_max": 25,
        },
    ]
    
    results = []
    
    for i, params in enumerate(param_sets):
        strat = MeanRevCounterOnly(cutoff=0.04, **params)
        r = test_windows(df, strat, windows)
        results.append((params, r))
        print(f"\nv{i+1}: Return={r['return']:+.1f}%, Sharpe={r['sharpe']:.2f}, WR={r['win_rate']:.0f}%, Trades={r['n_trades']}")
    
    results.sort(key=lambda x: x[1]["return"], reverse=True)
    print("\n=== BEST ===")
    best_params, best_r = results[0]
    print(f"Return: {best_r['return']:+.1f}%, Sharpe: {best_r['sharpe']:.2f}, WR: {best_r['win_rate']:.0f}%")
    print(f"Params: {best_params}")


if __name__ == "__main__":
    main()