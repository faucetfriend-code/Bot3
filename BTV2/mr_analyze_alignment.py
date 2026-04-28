"""
Analyze trade breakdown by regime alignment
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

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"


class MeanReversionRegime:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    
    def run(self, df):
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
        position_mult = [1.0]
        aligned = [False]  # Track if position is aligned with trend
        sl_price = [0.0]
        tp_price = [0.0]
        
        closed_trades = []  # (return, aligned)
        
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
            
            if in_pos[0]:
                curr_equity *= p / p0
                
                sl = sl_price[0]
                tp = tp_price[0]
                
                if side[0] == "long":
                    if lo <= sl or hi >= tp:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
                        closed_trades.append((result, aligned[0]))
                        in_pos[0] = False
                else:
                    if hi >= sl or lo <= tp:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
                        closed_trades.append((result, aligned[0]))
                        in_pos[0] = False
            else:
                if ad >= self.adx_max:
                    equity_arr[i] = curr_equity
                    continue
                
                prox_lo = (p - bbl) / (bbl + 1e-10)
                prox_hi = (bbu - p) / (bbu + 1e-10)
                
                if r < self.rsi_oversold and 0.0 <= prox_lo <= self.bb_proximity:
                    # LONG
                    if is_bull:
                        aligned[0] = True
                        atr_stop = self.aligned_atr_stop
                        target_r = self.aligned_target_r
                        pos_mult = self.aligned_size
                    else:
                        aligned[0] = False
                        atr_stop = self.counter_atr_stop
                        target_r = self.counter_target_r
                        pos_mult = self.counter_size
                    
                    sl = p - atr_stop * at
                    tp = p + target_r * atr_stop * at
                    
                    entry_eq[0] = curr_equity
                    position_mult[0] = pos_mult
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "long"
                    in_pos[0] = True
                
                elif r > self.rsi_overbought and 0.0 <= prox_hi <= self.bb_proximity:
                    # SHORT
                    if is_bull:
                        aligned[0] = False
                        atr_stop = self.counter_atr_stop
                        target_r = self.counter_target_r
                        pos_mult = self.counter_size
                    else:
                        aligned[0] = True
                        atr_stop = self.aligned_atr_stop
                        target_r = self.aligned_target_r
                        pos_mult = self.aligned_size
                    
                    sl = p + atr_stop * at
                    tp = p - target_r * atr_stop * at
                    
                    entry_eq[0] = curr_equity
                    position_mult[0] = pos_mult
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "short"
                    in_pos[0] = True
            
            equity_arr[i] = curr_equity
        
        if in_pos[0]:
            curr_equity *= (1.0 - COST_PER_SIDE)
            result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
            closed_trades.append((result, aligned[0]))
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
    df = load_data([2023])
    print(f"Data (2023): {len(df)} bars")
    
    params = {
        "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
        "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 2.0,
        "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
        "sma_period": 200, "adx_max": 30,
    }
    
    strat = MeanReversionRegime(cutoff=0.04, **params)
    
    df_test = df.loc["2023-01-01":"2023-12-31"]
    eq, trades = strat.run(df_test)
    
    # Analyze
    aligned = [t for t, a in trades if a]
    counter = [t for t, a in trades if not a]
    
    print(f"\nTotal trades: {len(trades)}")
    print(f"Aligned (with trend): {len(aligned)} ({100*len(aligned)/len(trades):.0f}%)")
    print(f"Counter (against trend): {len(counter)} ({100*len(counter)/len(trades):.0f}%)")
    
    if aligned:
        aw = [t for t in aligned if t > 0]
        al = [t for t in aligned if t < 0]
        print(f"\nAligned wins: {len(aw)} ({100*len(aw)/len(aligned):.0f}%)")
        if aw: print(f"  Avg win: {np.mean(aw)*100:.2f}%")
        if al: print(f"  Avg loss: {np.mean(al)*100:.2f}%")
        print(f"  Total: {sum(aligned)*100:.2f}%")
    
    if counter:
        cw = [t for t in counter if t > 0]
        cl = [t for t in counter if t < 0]
        print(f"\nCounter wins: {len(cw)} ({100*len(cw)/len(counter):.0f}%)")
        if cw: print(f"  Avg win: {np.mean(cw)*100:.2f}%")
        if cl: print(f"  Avg loss: {np.mean(cl)*100:.2f}%")
        print(f"  Total: {sum(counter)*100:.2f}%")
    
    # Full stats
    total = sum(t for t, a in trades)
    print(f"\n=== TOTAL PnL: {total*100:.2f}% ===")


if __name__ == "__main__":
    main()