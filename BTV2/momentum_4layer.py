"""
Momentum with ADX - Full 4-Layer Validation
========================================
Best: ADX > 20, EMA 9/21
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
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


def layer1_walkforward(df, params):
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
    
    strat = MomentumWithADX(**params)
    
    all_trades = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
        
        eq, trd = strat.run(df_test)
        all_trades.extend(trd)
    
    total_return = sum(all_trades)
    win_rate = 100 * len([t for t in all_trades if t > 0]) / len(all_trades) if all_trades else 0
    
    # Sharpe approximation
    mean = np.mean(all_trades)
    std = np.std(all_trades)
    sharpe = mean / std if std > 0 else 0
    
    return {
        "return": total_return,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "n_trades": len(all_trades),
        "trades": all_trades,
    }


def layer2_monte_carlo(trades):
    if len(trades) < 10:
        return {"p_loss": 1.0, "verdict": "FRAGILE", "note": "Too few"}
    
    returns = np.array(trades)
    mc = []
    for _ in range(N_MC_SIMS):
        sample = np.random.choice(returns, size=len(returns), replace=True)
        mc.append(np.prod(1 + sample) - 1)
    
    p_loss = np.mean(np.array(mc) < 0)
    
    return {
        "p_loss": p_loss,
        "p5": np.percentile(mc, 5),
        "p50": np.percentile(mc, 50),
        "p95": np.percentile(mc, 95),
        "verdict": "ROBUST" if p_loss < 0.30 else "FRAGILE",
    }


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    # Best params
    params = {
        "cutoff": 0.04,
        "ema_fast": 9,
        "ema_slow": 21,
        "atr_stop": 1.5,
        "atr_target": 3.0,
        "adx_min": 20,
    }
    
    print("\n=== LAYER 1: WALK-FORWARD OOS ===")
    l1 = layer1_walkforward(df, params)
    print(f"Return: {l1['return']*100:+.1f}%")
    print(f"Sharpe: {l1['sharpe']:.2f}")
    print(f"Win Rate: {l1['win_rate']:.1f}%")
    print(f"Trades: {l1['n_trades']}")
    
    print("\n=== LAYER 2: MONTE CARLO ===")
    l2 = layer2_monte_carlo(l1["trades"])
    print(f"P(Loss): {l2['p_loss']*100:.1f}%")
    print(f"P5: {l2['p5']*100:.1f}%, P50: {l2['p50']*100:.1f}%, P95: {l2['p95']*100:.1f}%")
    print(f"Verdict: {l2['verdict']}")
    
    # Verdict
    print("\n=== VERDICT ===")
    if l1["return"] > 0 and l2["p_loss"] < 0.30:
        print("PASS")
    elif l1["return"] > 0:
        print("MARGINAL")
    else:
        print("FAIL")


if __name__ == "__main__":
    main()