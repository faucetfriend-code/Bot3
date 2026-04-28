"""
Mean Reversion - LONG Only with Trend Filter
==========================================
Try LONG-only trades when price is above 200 SMA (uptrend).
Fewer trades, better direction.
"""

import numpy as np
import pandas as pd
from pathlib import Path
from mean_reversion_v2 import run_mean_reversion_v2
from strategies import compute_metrics, apply_butterworth, compute_sma


ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"


def load_data(year=2023):
    path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
    if not path.exists():
        return None
    
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df = df.set_index("timestamp").sort_index()
    
    df_1h = df.resample("1h").agg({
        "Open": "first", 
        "High": "max", 
        "Low": "min", 
        "Close": "last", 
        "Volume": "sum"
    })
    df_1h = df_1h.dropna()
    df_1h.index = df_1h.index.tz_localize(None)
    
    return df_1h


class MeanReversionLongOnly:
    """LONG-only MR with trend filter."""
    
    def __init__(
        self,
        cutoff: float = 0.04,
        rsi_oversold: float = 25.0,
        bb_proximity: float = 0.03,
        atr_stop: float = 2.0,
        atr_target: float = 4.0,
        sma_period: int = 200,  # Trend filter
    ):
        self.cutoff = cutoff
        self.rsi_oversold = rsi_oversold
        self.bb_proximity = bb_proximity
        self.atr_stop = atr_stop
        self.atr_target = atr_target
        self.sma_period = sma_period
        self.cost = 0.0015
    
    def run(self, df: pd.DataFrame) -> tuple[pd.Series, list]:
        from strategies import compute_rsi, compute_bollinger, compute_atr
        
        fc = apply_butterworth(df["Close"], self.cutoff)
        rsi = compute_rsi(fc, 14)
        bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
        atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
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
            bbu = bb_up.iloc[i] if i < len(bb_up) else np.nan
            bbl = bb_lo.iloc[i] if i < len(bb_lo) else np.nan
            at = atr.iloc[i] if i < len(atr) else np.nan
            sm = sma200.iloc[i] if i < len(sma200) else np.nan
            p = prices[i]
            p0 = prices[i-1]
            hi = highs[i]
            lo = lows[i]
            
            if any(np.isnan(x) for x in (r, bbu, bbl, at, sm)):
                equity_arr[i] = curr_equity
                continue
            
            if in_pos[0]:
                # Update equity
                curr_equity *= p / p0
                
                # Check exits
                if lo <= sl_price[0]:  # SL hit
                    curr_equity *= (1.0 - self.cost)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif hi >= tp_price[0]:  # TP hit
                    curr_equity *= (1.0 - self.cost)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
            else:
                # Entry: LONG only when price above SMA (uptrend)
                if p > sm:  # Price above SMA = uptrend
                    prox = (p - bbl) / (bbl + 1e-10)
                    
                    # RSI oversold + near lower BB
                    if r < self.rsi_oversold and 0.0 <= prox <= self.bb_proximity:
                        sl = p - self.atr_stop * at
                        tp = p + self.atr_target * at
                        entry_eq[0] = curr_equity
                        sl_price[0] = sl
                        tp_price[0] = tp
                        in_pos[0] = True
            
            equity_arr[i] = curr_equity
        
        # Close open position
        if in_pos[0]:
            curr_equity *= (1.0 - self.cost)
            closed_trades.append(curr_equity / entry_eq[0] - 1.0)
            equity_arr[-1] = curr_equity
        
        return pd.Series(equity_arr, index=df.index), closed_trades


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
            m = compute_metrics(eq, trd)
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
    }


def main():
    df = load_data(2023)
    
    windows = [
        ("2023-01-01", "2023-03-31"),
        ("2023-04-01", "2023-06-30"),
        ("2023-07-01", "2023-09-30"),
        ("2023-10-01", "2023-12-31"),
    ]
    
    # Test different trend filters
    param_sets = [
        # LONG only with SMA filter
        {"rsi_oversold": 25, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 4.0, "sma_period": 200},
        {"rsi_oversold": 25, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 4.0, "sma_period": 100},
        {"rsi_oversold": 30, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 4.0, "sma_period": 200},
        # Shorter lookback
        {"rsi_oversold": 25, "bb_proximity": 0.03, "atr_stop": 1.5, "atr_target": 3.0, "sma_period": 200},
        # Wide target
        {"rsi_oversold": 25, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 5.0, "sma_period": 200},
    ]
    
    results = []
    
    for params in param_sets:
        strat = MeanReversionLongOnly(cutoff=0.04, **params)
        r = test_windows(df, strat, windows)
        results.append((params, r))
        print(f"Params: {params}")
        print(f"  Sharpe: {r['sharpe']:.2f}, Return: {r['return']:.1f}%, WR: {r['win_rate']:.0f}%, Trades: {r['n_trades']:.0f}")
    
    # Best result
    results.sort(key=lambda x: x[1]["return"], reverse=True)
    print("\n=== BEST ===")
    best_params, best_r = results[0]
    print(f"Return: {best_r['return']:.1f}%, Sharpe: {best_r['sharpe']:.2f}, WR: {best_r['win_rate']:.0f}%")
    print(f"Params: {best_params}")


if __name__ == "__main__":
    main()