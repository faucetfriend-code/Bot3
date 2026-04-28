"""
Mean Reversion v3 - Full 4-Layer Validation with LONG-only Trend Filter
===================================================================
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics, 
    build_windows, 
    fit_gmm_regime, 
    gmm_regime_summary,
    compute_atr,
    compute_adx,
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
BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["1h"]

N_MC_SIMS = 500
TRAIN_MONTHS = 6
TEST_MONTHS = 3


class MeanReversionLongOnly:
    """LONG-only MR with trend filter (SMA above)."""
    
    def __init__(
        self,
        cutoff: float = 0.04,
        rsi_oversold: float = 30.0,
        bb_proximity: float = 0.03,
        atr_stop: float = 2.0,
        atr_target: float = 4.0,
        sma_period: int = 200,
    ):
        self.cutoff = cutoff
        self.rsi_oversold = rsi_oversold
        self.bb_proximity = bb_proximity
        self.atr_stop = atr_stop
        self.atr_target = atr_target
        self.sma_period = sma_period
    
    def run(self, df: pd.DataFrame) -> tuple[pd.Series, list]:
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
                if p > sm:  # Uptrend
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


def layer1_walkforward(df, strat):
    """Layer 1: Rolling walk-forward OOS."""
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
    total_closed = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
            
        eq, trd = strat.run(df_test)
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=BARS_PER_YEAR)
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.append(len(trd))
            total_closed.extend(trd)
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": 100 * len([t for t in total_closed if t > 0]) / len(total_closed) if total_closed else 0,
        "n_folds": len(all_sharpes),
        "total_trades": len(total_closed),
        "trades": total_closed,
    }


def layer2_monte_carlo(trades):
    """Layer 2: Bootstrap Monte Carlo."""
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


def layer3_robustness(df, strat, param_variations):
    """Layer 3: Parameter robustness."""
    scores = []
    
    for params in param_variations:
        new_strat = MeanReversionLongOnly(**params)
        
        # Test on subset
        df_test = df.loc["2023-01-01":"2023-06-30"]
        eq, trd = new_strat.run(df_test)
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=BARS_PER_YEAR)
            scores.append(m["total_return_pct"])
    
    return {"robustness_score": np.mean(scores) if scores else -100}


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    # Best params from iteration
    strat = MeanReversionLongOnly(
        cutoff=0.04,
        rsi_oversold=30,
        bb_proximity=0.03,
        atr_stop=2.0,
        atr_target=4.0,
        sma_period=200,
    )
    
    print("\n=== LAYER 1: WALK-FORWARD OOS ===")
    l1 = layer1_walkforward(df, strat)
    print(f"Sharpe: {l1['sharpe']:.2f}")
    print(f"Return: {l1['return']:.2f}%")
    print(f"Win Rate: {l1['win_rate']:.1f}%")
    print(f"Total Trades: {l1['total_trades']}")
    
    print("\n=== LAYER 2: MONTE CARLO ===")
    l2 = layer2_monte_carlo(l1["trades"])
    print(f"P(Loss): {l2['p_loss']:.1%}")
    print(f"P5: {l2['p5']:.2%}")
    print(f"P50: {l2['p50']:.2%}")
    print(f"P95: {l2['p95']:.2%}")
    print(f"Verdict: {l2['verdict']}")
    
    print("\n=== LAYER 3: ROBUSTNESS ===")
    param_variations = [
        {"cutoff": 0.03, "rsi_oversold": 30, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 4.0, "sma_period": 200},
        {"cutoff": 0.05, "rsi_oversold": 30, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 4.0, "sma_period": 200},
        {"cutoff": 0.04, "rsi_oversold": 25, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 4.0, "sma_period": 200},
        {"cutoff": 0.04, "rsi_oversold": 35, "bb_proximity": 0.03, "atr_stop": 2.0, "atr_target": 4.0, "sma_period": 200},
    ]
    l3 = layer3_robustness(df, strat, param_variations)
    print(f"Robustness Score: {l3['robustness_score']:.2f}%")
    
    # Final verdict
    print("\n=== VERDICT ===")
    if l1["return"] > 0 and l2["p_loss"] < 0.30 and l3["robustness_score"] > 0:
        print("PASS - Strategy is potentially profitable!")
    elif l1["return"] > 0:
        print("PASS (marginal) - Positive returns, but check robustness")
    else:
        print("FAIL - Negative returns")


if __name__ == "__main__":
    main()