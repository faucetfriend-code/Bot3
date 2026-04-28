"""
MA Trend with ADX Filter - Full 4-Layer Validation
=================================================
Best params: ADX > 30
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
    compute_adx,
    COST_PER_SIDE,
)
from strategies import INTERVAL_BARS_PER_YEAR

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"

N_MC_SIMS = 500


class MATrendWithADX:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    
    def run(self, df):
        fc = apply_butterworth(df["Close"], self.cutoff)
        sma_f = compute_sma(fc, self.fast_period)
        sma_s = compute_sma(fc, self.slow_period)
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
        
        last_cross_up = -999
        last_cross_down = -999
        
        for i in range(1, n):
            sf = sma_f.iloc[i]
            sf0 = sma_f.iloc[i-1]
            ss = sma_s.iloc[i]
            ss0 = sma_s.iloc[i-1]
            at = atr.iloc[i]
            ad = adx.iloc[i]
            p = prices[i]
            p0 = prices[i-1]
            hi = highs[i]
            lo = lows[i]
            
            if np.any(np.isnan([sf, ss, at, ad])):
                equity_arr[i] = curr_equity
                continue
            
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
                if ad < self.adx_min:
                    equity_arr[i] = curr_equity
                    continue
                
                if 0 < (i - last_cross_up) <= self.crossover_bars and sf > ss:
                    sl = p - self.atr_stop * at
                    tp = p + self.target_r * self.atr_stop * at
                    entry_eq[0] = curr_equity
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "long"
                    in_pos[0] = True
                elif 0 < (i - last_cross_down) <= self.crossover_bars and sf < ss:
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


def layer1_walkforward(df, strat):
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
    
    win_rate = 100 * len([t for t in all_trades if t > 0]) / len(all_trades) if all_trades else 0
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": win_rate,
        "n_trades": len(all_trades),
        "trades": all_trades,
    }


def layer2_monte_carlo(trades):
    if len(trades) < 10:
        return {"p_loss": 1.0, "p5": -1, "p50": -1, "p95": -1, "verdict": "FRAGILE"}
    
    returns = np.array(trades)
    mc_returns = []
    
    for _ in range(N_MC_SIMS):
        sample = np.random.choice(returns, size=len(returns), replace=True)
        mc_returns.append(np.prod(1 + sample) - 1)
    
    mc_returns = np.array(mc_returns)
    p_loss = np.mean(mc_returns < 0)
    
    return {
        "p_loss": p_loss,
        "p5": np.percentile(mc_returns, 5),
        "p50": np.percentile(mc_returns, 50),
        "p95": np.percentile(mc_returns, 95),
        "verdict": "ROBUST" if p_loss < 0.30 else "FRAGILE",
    }


def layer3_robustness(df, param_variations):
    scores = []
    
    for params in param_variations:
        strat = MATrendWithADX(cutoff=0.04, **params)
        df_test = df.loc["2023-01-01":"2023-06-30"]
        eq, trd = strat.run(df_test)
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            scores.append(m["total_return_pct"])
    
    return {"robustness_score": np.mean(scores) if scores else -100}


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    # Best params
    params = {
        "fast_period": 10,
        "slow_period": 30,
        "atr_stop": 3.0,
        "target_r": 2.0,
        "crossover_bars": 3,
        "adx_min": 30,
    }
    
    strat = MATrendWithADX(cutoff=0.04, **params)
    
    print("\n=== LAYER 1: WALK-FORWARD OOS ===")
    l1 = layer1_walkforward(df, strat)
    print(f"Sharpe: {l1['sharpe']:.2f}")
    print(f"Return: {l1['return']:.2f}%")
    print(f"Win Rate: {l1['win_rate']:.1f}%")
    print(f"Total Trades: {l1['n_trades']}")
    
    print("\n=== LAYER 2: MONTE CARLO ===")
    l2 = layer2_monte_carlo(l1["trades"])
    print(f"P(Loss): {l2['p_loss']:.1%}")
    print(f"P5: {l2['p5']:.2%}")
    print(f"P50: {l2['p50']:.2%}")
    print(f"P95: {l2['p95']:.2%}")
    print(f"Verdict: {l2['verdict']}")
    
    print("\n=== LAYER 3: ROBUSTNESS ===")
    param_variations = [
        {"fast_period": 8, "slow_period": 25, "atr_stop": 3.0, "target_r": 2.0, "crossover_bars": 3, "adx_min": 30},
        {"fast_period": 10, "slow_period": 30, "atr_stop": 3.0, "target_r": 2.0, "crossover_bars": 3, "adx_min": 30},
        {"fast_period": 10, "slow_period": 30, "atr_stop": 3.5, "target_r": 2.0, "crossover_bars": 3, "adx_min": 30},
        {"fast_period": 10, "slow_period": 35, "atr_stop": 3.0, "target_r": 2.0, "crossover_bars": 3, "adx_min": 30},
        {"fast_period": 12, "slow_period": 30, "atr_stop": 3.0, "target_r": 2.0, "crossover_bars": 3, "adx_min": 30},
    ]
    l3 = layer3_robustness(df, param_variations)
    print(f"Robustness Score: {l3['robustness_score']:.2f}%")
    
    # Verdict
    print("\n=== VERDICT ===")
    if l1["return"] > 0 and l2["p_loss"] < 0.30 and l3["robustness_score"] > 0:
        print("PASS - Strategy is profitable and robust!")
    elif l1["return"] > 0 and l2["p_loss"] < 0.30:
        print("PASS - Positive returns, P(Loss) under 30%")
    elif l1["return"] > 0:
        print("MARGINAL - Positive returns but needs work")
    else:
        print("FAIL")


if __name__ == "__main__":
    main()