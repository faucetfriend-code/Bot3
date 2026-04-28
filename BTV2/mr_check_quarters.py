"""
Check 2023 only vs 2022-2023
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
    compute_atr as _atr,
    COST_PER_SIDE,
)
from strategies import INTERVAL_BARS_PER_YEAR

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"

N_MC_SIMS = 500


class MeanReversionLongOnly:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    
    def run(self, df):
        fc = apply_butterworth(df["Close"], self.cutoff)
        rsi = compute_rsi(fc, 14)
        bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
        atr = _atr(df["High"], df["Low"], df["Close"], 14)
        sma200 = compute_sma(fc, self.sma_period)
        
        prices = df["Close"].values.astype(float)
        highs = df["High"].values.astype(float)
        lows = df["Low"].values.astype(float)
        n = len(prices)
        
        equity_arr = np.ones(n, dtype=float)
        curr_equity = 1.0
        in_pos = [False]
        entry_eq = [1.0]
        sl_price = [0.0]
        tp_price = [0.0]
        closed_trades = []
        
        for i in range(1, n):
            r = rsi.iloc[i] if i < len(rsi) else 50
            bbl = bb_lo.iloc[i] if i < len(bb_lo) else np.nan
            at = atr.iloc[i] if i < len(atr) else np.nan
            sm = sma200.iloc[i] if i < len(sma200) else np.nan
            p = prices[i]
            p0 = prices[i-1]
            hi = highs[i]
            lo = lows[i]
            
            if any(np.isnan(x) for x in (r, bbl, at, sm)):
                equity_arr[i] = curr_equity
                continue
            
            if in_pos[0]:
                curr_equity *= p / p0
                
                if lo <= sl_price[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif hi >= tp_price[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
            else:
                if p > sm:
                    prox = (p - bbl) / (bbl + 1e-10)
                    
                    if r < self.rsi_oversold and 0.0 <= prox <= self.bb_proximity:
                        sl = p - self.atr_stop * at
                        tp = p + self.atr_target * at
                        entry_eq[0] = curr_equity
                        sl_price[0] = sl
                        tp_price[0] = tp
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


def main():
    df = load_data([2022, 2023])
    print(f"Total data: {len(df)} bars")
    
    strat = MeanReversionLongOnly(
        cutoff=0.04,
        rsi_oversold=30,
        bb_proximity=0.03,
        atr_stop=2.0,
        atr_target=4.0,
        sma_period=200,
    )
    
    # Test each quarter
    quarters = [
        ("2022 Q1", "2022-01-01", "2022-03-31"),
        ("2022 Q2", "2022-04-01", "2022-06-30"),
        ("2022 Q3", "2022-07-01", "2022-09-30"),
        ("2022 Q4", "2022-10-01", "2022-12-31"),
        ("2023 Q1", "2023-01-01", "2023-03-31"),
        ("2023 Q2", "2023-04-01", "2023-06-30"),
        ("2023 Q3", "2023-07-01", "2023-09-30"),
        ("2023 Q4", "2023-10-01", "2023-12-31"),
    ]
    
    print("\n=== QUARTERLY BREAKDOWN ===")
    for name, start, end in quarters:
        try:
            df_test = df.loc[start:end]
            if len(df_test) < 100:
                continue
            
            eq, trd = strat.run(df_test)
            
            if len(trd) >= 3:
                m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
                wr = 100 * len([t for t in trd if t > 0]) / len(trd)
                print(f"{name}: Return={m['total_return_pct']:+.1f}%, Sharpe={m['sharpe']:.2f}, WR={wr:.0f}%, Trades={len(trd)}")
            else:
                print(f"{name}: NO TRADES")
        except Exception as e:
            print(f"{name}: ERROR - {e}")
    
    # Full year 2023 only
    print("\n=== 2023 FULL YEAR ===")
    df_2023 = df.loc["2023-01-01":"2023-12-31"]
    eq, trd = strat.run(df_2023)
    
    if len(trd) >= 3:
        m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
        wr = 100 * len([t for t in trd if t > 0]) / len(trd)
        print(f"Return={m['total_return_pct']:+.1f}%, Sharpe={m['sharpe']:.2f}, WR={wr:.0f}%, Trades={len(trd)}")
    
    # Full year 2022 only
    print("\n=== 2022 FULL YEAR ===")
    df_2022 = df.loc["2022-01-01":"2022-12-31"]
    eq, trd = strat.run(df_2022)
    
    if len(trd) >= 3:
        m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
        wr = 100 * len([t for t in trd if t > 0]) / len(trd)
        print(f"Return={m['total_return_pct']:+.1f}%, Sharpe={m['sharpe']:.2f}, WR={wr:.0f}%, Trades={len(trd)}")


if __name__ == "__main__":
    main()