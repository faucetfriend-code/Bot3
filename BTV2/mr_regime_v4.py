"""
Mean Reversion v4 - Regime-Based Position Sizing
=========================================

Key features:
1. Dual direction (LONG + SHORT)
2. Regime detection (bull vs bear via 200 SMA slope)
3. Trend strength (ADX filter)
4. Position sizing based on regime alignment:
   - WITH TREND (bull longs, bear shorts): 2x size, 2.0R target
   - AGAINST TREND (bull shorts, bear longs): 1x size, 1.0R target
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
    """
    Mean reversion with regime-based position sizing.
    """
    
    def __init__(
        self,
        cutoff: float = 0.04,
        # Entry thresholds
        rsi_oversold: float = 25.0,
        rsi_overbought: float = 75.0,
        bb_proximity: float = 0.03,
        # Stops
        atr_stop_aligned: float = 2.0,    # SL for aligned positions
        atr_stop_counter: float = 1.5,  # SL for counter positions
        # Targets (in R)
        target_aligned: float = 2.0,    # TP for aligned (WITH trend)
        target_counter: float = 1.0,    # TP for counter (AGAINST trend)
        # Position sizing
        aligned_size_mult: float = 2.0,    # Size multiplier for aligned
        # Regime
        sma_period: int = 200,
        adx_max: float = 30.0,         # Max ADX for ranging
        adx_strong: float = 40.0,     # ADX above this = trending
    ):
        self.cutoff = cutoff
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_proximity = bb_proximity
        self.atr_stop_aligned = atr_stop_aligned
        self.atr_stop_counter = atr_stop_counter
        self.target_aligned = target_aligned
        self.target_counter = target_counter
        self.aligned_size_mult = aligned_size_mult
        self.sma_period = sma_period
        self.adx_max = adx_max
        self.adx_strong = adx_strong
    
    def run(self, df: pd.DataFrame) -> tuple[pd.Series, list]:
        fc = apply_butterworth(df["Close"], self.cutoff)
        rsi = compute_rsi(fc, 14)
        bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
        atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
        adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
        
        # Price SMA for regime
        sma = compute_sma(fc, self.sma_period)
        
        prices = df["Close"].values.astype(float)
        highs = df["High"].values.astype(float)
        lows = df["Low"].values.astype(float)
        n = len(prices)
        
        equity_arr = np.ones(n, dtype=float)
        curr_equity = 1.0
        
        # Position state
        in_pos = [False]
        side = [""]
        entry_eq = [1.0]
        size_mult = [1.0]  # Track position size
        sl_price = [0.0]
        tp_price = [0.0]
        
        closed_trades = []
        
        for i in range(1, n):
            r = rsi.iloc[i] if i < len(rsi) else 50
            bbu = bb_up.iloc[i] if i < len(bb_up) else np.nan
            bbl = bb_lo.iloc[i] if i < len(bb_lo) else np.nan
            at = atr.iloc[i] if i < len(atr) else np.nan
            ad = adx.iloc[i] if i < len(adx) else 50
            sm = sma.iloc[i] if i < len(sma) else np.nan
            p = prices[i]
            p0 = prices[i-1]
            hi = highs[i]
            lo = lows[i]
            
            # Skip if missing data
            if any(np.isnan(x) for x in (r, bbu, bbl, at, ad, sm)):
                equity_arr[i] = curr_equity
                continue
            
            # Determine regime
            # Bull: price above SMA, Bear: price below SMA
            is_bull = p > sm
            is_strong_trend = ad >= self.adx_strong
            is_ranging = ad < self.adx_max
            
            # Position sizing based on regime alignment
            if is_bull:
                # Bull market: longs = with trend, shorts = against trend
                aligned_side = "long"
                counter_side = "short"
            else:
                # Bear market: shorts = with trend, longs = against trend
                aligned_side = "short"
                counter_side = "long"
            
            # Handle existing position
            if in_pos[0]:
                # Update equity
                curr_equity *= p / p0
                
                # Check exits
                if side[0] == "long":
                    if lo <= sl_price[0]:  # SL hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        # Adjust for size
                        result = curr_equity / entry_eq[0] - 1.0
                        result = result * size_mult[0]  # Scale by position size
                        closed_trades.append(result)
                        in_pos[0] = False
                    elif hi >= tp_price[0]:  # TP hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = curr_equity / entry_eq[0] - 1.0
                        result = result * size_mult[0]
                        closed_trades.append(result)
                        in_pos[0] = False
                else:  # short
                    if hi >= sl_price[0]:  # SL hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = curr_equity / entry_eq[0] - 1.0
                        result = result * size_mult[0]
                        closed_trades.append(result)
                        in_pos[0] = False
                    elif lo <= tp_price[0]:  # TP hit
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        result = curr_equity / entry_eq[0] - 1.0
                        result = result * size_mult[0]
                        closed_trades.append(result)
                        in_pos[0] = False
            else:
                # Entry logic
                # Only enter in ranging markets (for mean reversion)
                if not is_ranging:
                    equity_arr[i] = curr_equity
                    continue
                
                prox_lo = (p - bbl) / (bbl + 1e-10)
                prox_hi = (bbu - p) / (bbu + 1e-10)
                
                # LONG entry
                if r < self.rsi_oversold and 0.0 <= prox_lo <= self.bb_proximity:
                    aligned = (side == aligned_side for side in ["long"])  # Check alignment
                    
                    # Determine position size based on regime alignment
                    if is_bull:
                        # Bull longs = with trend
                        size_mult[0] = self.aligned_size_mult
                        atr_stop = self.atr_stop_aligned
                        target_r = self.target_aligned
                    else:
                        # Bull longs = against trend in bear market
                        size_mult[0] = 1.0
                        atr_stop = self.atr_stop_counter
                        target_r = self.target_counter
                    
                    sl = p - atr_stop * at
                    tp = p + target_r * atr_stop * at  # TP in R terms
                    
                    entry_eq[0] = curr_equity
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "long"
                    in_pos[0] = True
                
                # SHORT entry
                elif r > self.rsi_overbought and 0.0 <= prox_hi <= self.bb_proximity:
                    if is_bull:
                        # Bear shorts = against trend in bull market
                        size_mult[0] = 1.0
                        atr_stop = self.atr_stop_counter
                        target_r = self.target_counter
                    else:
                        # Bear shorts = with trend
                        size_mult[0] = self.aligned_size_mult
                        atr_stop = self.atr_stop_aligned
                        target_r = self.target_aligned
                    
                    sl = p + atr_stop * at
                    tp = p - target_r * atr_stop * at
                    
                    entry_eq[0] = curr_equity
                    sl_price[0] = sl
                    tp_price[0] = tp
                    side[0] = "short"
                    in_pos[0] = True
            
            equity_arr[i] = curr_equity
        
        # Close open position
        if in_pos[0]:
            curr_equity *= (1.0 - COST_PER_SIDE)
            result = curr_equity / entry_eq[0] - 1.0
            result = result * size_mult[0]
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
    win_rates = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
        
        eq, trd = strat.run(df_test)
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.append(len(trd))
            win_rates.append(m["win_rate_pct"])
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": np.mean(win_rates) if win_rates else 0,
        "n_trades": np.mean(all_trades) if all_trades else 0,
        "n_folds": len(all_sharpes),
        "all_trades": all_trades,
    }


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    # Test windows
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
    
    # Test different parameter sets
    param_sets = [
        # Base
        {
            "rsi_oversold": 25, "rsi_overbought": 75,
            "bb_proximity": 0.03,
            "atr_stop_aligned": 2.0, "atr_stop_counter": 1.5,
            "target_aligned": 2.0, "target_counter": 1.0,
            "aligned_size_mult": 2.0,
            "sma_period": 200, "adx_max": 30, "adx_strong": 40,
        },
        # Tighter entries
        {
            "rsi_oversold": 20, "rsi_overbought": 80,
            "bb_proximity": 0.02,
            "atr_stop_aligned": 2.0, "atr_stop_counter": 1.5,
            "target_aligned": 2.0, "target_counter": 1.0,
            "aligned_size_mult": 2.0,
            "sma_period": 200, "adx_max": 30, "adx_strong": 40,
        },
        # Lower ADX threshold for ranging
        {
            "rsi_oversold": 25, "rsi_overbought": 75,
            "bb_proximity": 0.03,
            "atr_stop_aligned": 2.0, "atr_stop_counter": 1.5,
            "target_aligned": 2.0, "target_counter": 1.0,
            "aligned_size_mult": 2.0,
            "sma_period": 200, "adx_max": 25, "adx_strong": 35,
        },
        # More aggressive alignment
        {
            "rsi_oversold": 25, "rsi_overbought": 75,
            "bb_proximity": 0.03,
            "atr_stop_aligned": 2.5, "atr_stop_counter": 1.0,
            "target_aligned": 2.5, "target_counter": 1.0,
            "aligned_size_mult": 2.5,
            "sma_period": 200, "adx_max": 30, "adx_strong": 40,
        },
    ]
    
    results = []
    
    for params in param_sets:
        strat = MeanReversionRegime(cutoff=0.04, **params)
        r = test_windows(df, strat, windows)
        results.append((params, r))
        print(f"\nParams: {params}")
        print(f"  Sharpe: {r['sharpe']:.2f}, Return: {r['return']:.1f}%, WR: {r['win_rate']:.0f}%, Trades: {r['n_trades']:.0f}")
    
    # Best
    results.sort(key=lambda x: x[1]["return"], reverse=True)
    print("\n=== BEST ===")
    best_params, best_r = results[0]
    print(f"Return: {best_r['return']:+.1f}%, Sharpe: {best_r['sharpe']:.2f}, WR: {best_r['win_rate']:.0f}%")
    print(f"Params: {best_params}")


if __name__ == "__main__":
    main()