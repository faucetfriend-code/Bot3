"""
Momentum with ADX Filter
=========================
Only trade when trending (ADX > threshold)
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    apply_butterworth,
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_atr,
    compute_adx,
    COST_PER_SIDE,
)
from strategies import INTERVAL_BARS_PER_YEAR

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"

N_MC_SIMS = 500


class MomentumWithADX:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    
    def run(self, df):
        fc = apply_butterworth(df["Close"], self.cutoff)
        ema_f = compute_ema(fc, self.ema_fast)
        ema_s = compute_ema(fc, self.ema_slow)
        rsi = compute_rsi(fc, 14)
        _, _, mh = compute_macd(fc, 12, 26, 9)
        atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
        adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
        
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
            ef = ema_f.iloc[i]
            ef0 = ema_f.iloc[i-1]
            es = ema_s.iloc[i]
            es0 = ema_s.iloc[i-1]
            r = rsi.iloc[i]
            mhi = mh.iloc[i]
            mhi0 = mh.iloc[i-1]
            at = atr.iloc[i]
            ad = adx.iloc[i]
            p = prices[i]
            p0 = prices[i-1]
            hi = highs[i]
            lo = lows[i]
            
            if np.any(np.isnan([ef, es, r, mhi, at, ad])):
                equity_arr[i] = curr_equity
                continue
            
            # ADX filter - only trade in trending markets
            if ad < self.adx_min:
                equity_arr[i] = curr_equity
                continue
            
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
                cross_up = ef0 < es0 and ef > es
                cross_dn = ef0 > es0 and ef < es
                
                if cross_up and p > ef and p > es and 45 <= r <= 65 and mhi > 0 and mhi > mhi0:
                    sl = p - self.atr_stop * at
                    tp = p + self.atr_target * at
                    entry_eq[0] = curr_equity
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "long"
                    in_pos[0] = True
                elif cross_dn and p < ef and p < es and 35 <= r <= 55 and mhi < 0 and mhi < mhi0:
                    sl = p + self.atr_stop * at
                    tp = p - self.atr_target * at
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


def test_adx(df, windows, adx_vals):
    results = []
    
    for adx_min in adx_vals:
        params = {
            "cutoff": 0.04,
            "ema_fast": 9,
            "ema_slow": 21,
            "atr_stop": 1.5,
            "atr_target": 3.0,
            "adx_min": adx_min,
        }
        
        strat = MomentumWithADX(**params)
        
        all_returns = []
        all_trades = []
        
        for start, end in windows:
            df_test = df.loc[start:end]
            if len(df_test) < 100:
                continue
            
            eq, trd = strat.run(df_test)
            
            if len(trd) >= 1:
                all_trades.extend(trd)
        
        if all_trades:
            # Simple Sharpe approximation
            mean = np.mean(all_trades)
            std = np.std(all_trades)
            sharpe = mean / std if std > 0 else 0
            results.append((adx_min, {
                "return": sum(all_trades),
                "sharpe": sharpe,
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
    
    print("\n=== TESTING ADX THRESHOLDS ===")
    adx_vals = [20, 25, 30, 35, 40]
    results = test_adx(df, windows, adx_vals)
    
    for adx_min, r in results:
        print(f"ADX > {adx_min}: Return={r['return']*100:+.1f}%, Sharpe={r['sharpe']:.2f}, Trades={r['n_trades']}")
    
    if results:
        results.sort(key=lambda x: x[1]["return"], reverse=True)
        best_adx, best_r = results[0]
        print(f"\n=== BEST: ADX > {best_adx} ===")
        print(f"Return: {best_r['return']*100:+.1f}%")


if __name__ == "__main__":
    main()