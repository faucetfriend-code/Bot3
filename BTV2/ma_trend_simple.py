"""
Simplified MA Trend Following - Direct Crossover
============================================
Simpler: Enter on crossover, no pullback requirement.
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    apply_butterworth,
    compute_sma,
    compute_atr,
    COST_PER_SIDE,
)
from strategies import INTERVAL_BARS_PER_YEAR

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"


class MATrendFollowing:
    """Simple MA trend following - enter immediately on crossover."""
    
    def __init__(
        self,
        cutoff=0.04,
        fast_period=10,
        slow_period=30,
        atr_stop=2.5,
        target_r=2.0,
    ):
        self.cutoff = cutoff
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.atr_stop = atr_stop
        self.target_r = target_r
    
    def run(self, df):
        fc = apply_butterworth(df["Close"], self.cutoff)
        sma_f = compute_sma(fc, self.fast_period)
        sma_s = compute_sma(fc, self.slow_period)
        atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
        
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
        
        last_cross_up = -999
        last_cross_down = -999
        
        for i in range(1, n):
            sf = sma_f.iloc[i]
            sf0 = sma_f.iloc[i-1]
            ss = sma_s.iloc[i]
            ss0 = sma_s.iloc[i-1]
            at = atr.iloc[i]
            p = prices[i]
            p0 = prices[i-1]
            hi = highs[i]
            lo = lows[i]
            
            if np.any(np.isnan([sf, ss, at])):
                equity_arr[i] = curr_equity
                continue
            
            # Track crossovers
            if sf0 < ss0 and sf >= ss:
                last_cross_up = i
            if sf0 > ss0 and sf <= ss:
                last_cross_down = i
            
            if in_pos[0]:
                curr_equity *= p / p0
                
                if side[0] == "long":
                    if lo <= sl_price[0] or hi >= tp_price[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
                else:
                    if hi >= sl_price[0] or lo <= tp_price[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
            else:
                # Enter immediately on crossover (no pullback requirement)
                if 0 < (i - last_cross_up) <= 3 and sf > ss:
                    sl = p - self.atr_stop * at
                    tp = p + self.target_r * self.atr_stop * at
                    entry_eq[0] = curr_equity
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "long"
                    in_pos[0] = True
                elif 0 < (i - last_cross_down) <= 3 and sf < ss:
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


def test_params(df, windows, param_sets):
    results = []
    
    for params in param_sets:
        strat = MATrendFollowing(cutoff=0.04, **params)
        
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
        
        if all_returns:
            results.append((params, {
                "sharpe": np.mean(all_sharpes),
                "return": np.mean(all_returns),
                "n_trades": len(all_trades),
                "trades": all_trades,
            }))
    
    return results


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
        {"fast_period": 10, "slow_period": 30, "atr_stop": 2.5, "target_r": 2.0},
        {"fast_period": 10, "slow_period": 30, "atr_stop": 2.0, "target_r": 2.5},
        {"fast_period": 10, "slow_period": 30, "atr_stop": 3.0, "target_r": 2.0},
        {"fast_period": 8, "slow_period": 21, "atr_stop": 2.5, "target_r": 2.0},
        {"fast_period": 5, "slow_period": 20, "atr_stop": 2.5, "target_r": 2.0},
        {"fast_period": 12, "slow_period": 26, "atr_stop": 2.0, "target_r": 2.0},
    ]
    
    results = test_params(df, windows, param_sets)
    
    for params, r in results:
        print(f"{params}: Return={r['return']:+.1f}%, Sharpe={r['sharpe']:.2f}, Trades={r['n_trades']}")
    
    if results:
        results.sort(key=lambda x: x[1]["return"], reverse=True)
        print("\n=== BEST ===")
        best_params, best_r = results[0]
        print(f"Return: {best_r['return']:+.1f}%, Sharpe: {best_r['sharpe']:.2f}")
        print(f"Params: {best_params}")


if __name__ == "__main__":
    main()