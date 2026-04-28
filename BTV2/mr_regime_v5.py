"""
Mean Reversion v5 - Fixed Regime Logic
=====================================

Key insight from analysis:
- Aligned (with trend) trades losing badly in trending markets
- Counter trades working better

Fix:
1. Only take aligned positions in RANGING markets (ADX < 25)
2. Skip aligned positions when trending (ADX > 35)
3. Take counter positions with tight stops
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


class MeanReversionRegimeV5:
    def __init__(
        self,
        cutoff: float = 0.04,
        rsi_oversold: float = 20.0,
        rsi_overbought: float = 80.0,
        bb_proximity: float = 0.02,
        # Aligned - only when ranging!
        aligned_atr_stop: float = 2.0,
        aligned_target_r: float = 2.0,
        aligned_size: float = 1.5,
        # Counter - can take in any condition
        counter_atr_stop: float = 1.5,
        counter_target_r: float = 1.0,
        counter_size: float = 1.0,
        # Regime
        sma_period: int = 200,
        adx_ranging: float = 25.0,  # Max ADX for ranging
        adx_trending: float = 35.0,  # Above this = skip aligned
    ):
        self.cutoff = cutoff
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_proximity = bb_proximity
        self.aligned_atr_stop = aligned_atr_stop
        self.aligned_target_r = aligned_target_r
        self.aligned_size = aligned_size
        self.counter_atr_stop = counter_atr_stop
        self.counter_target_r = counter_target_r
        self.counter_size = counter_size
        self.sma_period = sma_period
        self.adx_ranging = adx_ranging
        self.adx_trending = adx_trending
    
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
        position_mult = [1.0]
        aligned = [False]
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
            is_ranging = ad < self.adx_ranging
            is_strong_trend = ad > self.adx_trending
            
            # ====== EXIT ======
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
                # ====== ENTRY ======
                prox_lo = (p - bbl) / (bbl + 1e-10)
                prox_hi = (bbu - p) / (bbu + 1e-10)
                
                # LONG entry
                if r < self.rsi_oversold and 0.0 <= prox_lo <= self.bb_proximity:
                    if is_bull:
                        # Bull market: longs = with trend
                        if is_ranging and not is_strong_trend:
                            aligned[0] = True
                            atr_stop = self.aligned_atr_stop
                            target_r = self.aligned_target_r
                            pos_mult = self.aligned_size
                        else:
                            # Skip aligned in strong trend
                            equity_arr[i] = curr_equity
                            continue
                    else:
                        # Bear market: longs = against trend
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
                
                # SHORT entry
                elif r > self.rsi_overbought and 0.0 <= prox_hi <= self.bb_proximity:
                    if is_bull:
                        # Bull market: shorts = against trend
                        aligned[0] = False
                        atr_stop = self.counter_atr_stop
                        target_r = self.counter_target_r
                        pos_mult = self.counter_size
                    else:
                        # Bear market: shorts = with trend
                        if is_ranging and not is_strong_trend:
                            aligned[0] = True
                            atr_stop = self.aligned_atr_stop
                            target_r = self.aligned_target_r
                            pos_mult = self.aligned_size
                        else:
                            equity_arr[i] = curr_equity
                            continue
                    
                    sl = p + atr_stop * at
                    tp = p - target_r * atr_stop * at
                    
                    entry_eq[0] = curr_equity
                    position_mult[0] = pos_mult
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "short"
                    in_pos[0] = True
            
            equity_arr[i] = curr_equity
        
        # Close open
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
            # Unpack trades from (return, aligned) tuples
            trades_only = [t[0] for t in trd]
            m = compute_metrics(eq, trades_only, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
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
        # v1: Only aligned when ranging (ADX < 25)
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 1.5,
            "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_ranging": 25, "adx_trending": 35,
        },
        # v2: No aligned at all - only counter
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 0.0,  # DISABLED
            "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_ranging": 25, "adx_trending": 35,
        },
        # v3: Tighter counter
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 1.0,
            "counter_atr_stop": 1.2, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_ranging": 25, "adx_trending": 35,
        },
        # v4: More aligned
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 2.0,
            "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_ranging": 20, "adx_trending": 30,
        },
    ]
    
    results = []
    
    for i, params in enumerate(param_sets):
        strat = MeanReversionRegimeV5(cutoff=0.04, **params)
        r = test_windows(df, strat, windows)
        results.append((params, r))
        print(f"\nv{i+1}: Return={r['return']:+.1f}%, Sharpe={r['sharpe']:.2f}")
        print(f"  {params}")
    
    results.sort(key=lambda x: x[1]["return"], reverse=True)
    print("\n=== BEST ===")
    best_params, best_r = results[0]
    print(f"Return: {best_r['return']:+.1f}%, Sharpe: {best_r['sharpe']:.2f}")


if __name__ == "__main__":
    main()