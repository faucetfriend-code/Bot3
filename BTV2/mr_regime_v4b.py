"""
Mean Reversion v4 - Regime-Based Position Sizing (FIXED)
================================================

Key features:
1. Dual direction (LONG + SHORT)
2. Regime detection (bull vs bear via 200 SMA)
3. Position sizing based on regime alignment:
   - WITH TREND: 2x size, 2.0R target (bull longs, bear shorts)
   - AGAINST TREND: 1x size, 1.0R target (bull shorts, bear longs)
4. Only enter in ranging markets (ADX < 25)
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


class MeanReversionRegime:
    def __init__(
        self,
        cutoff: float = 0.04,
        rsi_oversold: float = 20.0,
        rsi_overbought: float = 80.0,
        bb_proximity: float = 0.02,
        # With trend (aligned)
        aligned_atr_stop: float = 2.0,
        aligned_target_r: float = 2.0,
        aligned_size: float = 2.0,
        # Against trend (counter)
        counter_atr_stop: float = 1.5,
        counter_target_r: float = 1.0,
        counter_size: float = 1.0,
        # Regime
        sma_period: int = 200,
        adx_max: float = 25.0,
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
        position_mult = [1.0]
        sl_price = [0.0]
        tp_price = [0.0]
        
        closed_trades = []
        
        for i in range(1, n):
            # Get indicators
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
            
            # Determine regime
            is_bull = p > sm  # Bull: price above SMA
            
            if in_pos[0]:
                # Update equity
                curr_equity *= p / p0
                
                # Get exit boundaries
                sl = sl_price[0]
                tp = tp_price[0]
                
                if side[0] == "long":
                    if lo <= sl:  # SL hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
                        closed_trades.append(result)
                        in_pos[0] = False
                    elif hi >= tp:  # TP hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
                        closed_trades.append(result)
                        in_pos[0] = False
                else:  # short
                    if hi >= sl:  # SL hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
                        closed_trades.append(result)
                        in_pos[0] = False
                    elif lo <= tp:  # TP hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
                        closed_trades.append(result)
                        in_pos[0] = False
            else:
                # Entry - only in ranging markets
                if ad >= self.adx_max:
                    equity_arr[i] = curr_equity
                    continue
                
                prox_lo = (p - bbl) / (bbl + 1e-10)
                prox_hi = (bbu - p) / (bbu + 1e-10)
                
                # LONG entry
                if r < self.rsi_oversold and 0.0 <= prox_lo <= self.bb_proximity:
                    # Determine sizing based on regime
                    if is_bull:
                        # Bull + Long = WITH TREND
                        atr_stop = self.aligned_atr_stop
                        target_r = self.aligned_target_r
                        pos_mult = self.aligned_size
                    else:
                        # Bear + Long = AGAINST TREND
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
                        # Bull + Short = AGAINST TREND
                        atr_stop = self.counter_atr_stop
                        target_r = self.counter_target_r
                        pos_mult = self.counter_size
                    else:
                        # Bear + Short = WITH TREND
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
        
        # Close open position
        if in_pos[0]:
            curr_equity *= (1.0 - COST_PER_SIDE)
            result = (curr_equity / entry_eq[0] - 1.0) * position_mult[0]
            closed_trades.append(result)
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
    
    if not all_returns:
        return {"sharpe": -999, "return": -100, "win_rate": 0, "n_trades": 0, "all_trades": []}
    
    win_rate = 100 * len([t for t in all_trades if t > 0]) / len(all_trades)
    
    return {
        "sharpe": np.mean(all_sharpes),
        "return": np.mean(all_returns),
        "win_rate": win_rate,
        "n_trades": len(all_trades),
        "all_trades": all_trades,
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
    
    # Test parameter sets
    param_sets = [
        # v1: 1:1 RR for counter, 2:1 for aligned
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 2.0,
            "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_max": 25,
        },
        # v2: More aggressive aligned
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.5, "aligned_target_r": 2.5, "aligned_size": 2.5,
            "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_max": 25,
        },
        # v3: Tighter counter stops
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 2.0,
            "counter_atr_stop": 1.0, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_max": 25,
        },
        # v4: Balanced
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 1.5,
            "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_max": 25,
        },
        # v5: Higher ADX (more trades)
        {
            "rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02,
            "aligned_atr_stop": 2.0, "aligned_target_r": 2.0, "aligned_size": 2.0,
            "counter_atr_stop": 1.5, "counter_target_r": 1.0, "counter_size": 1.0,
            "sma_period": 200, "adx_max": 30,
        },
    ]
    
    results = []
    
    for i, params in enumerate(param_sets):
        strat = MeanReversionRegime(cutoff=0.04, **params)
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