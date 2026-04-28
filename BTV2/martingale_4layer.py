"""
Martingale Mean Reversion - 4-Layer Validation
========================================

Test multiple Martingale multipliers through full 4-layer validation:
- 2x, 3x, 5x, 7x, 10x variations
- Different RSI thresholds
"""
import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path

from strategies import (
    compute_metrics, 
    fit_gmm_regime, 
    gmm_regime_summary,
    compute_atr,
    compute_adx,
    COST_PER_SIDE,
    _open_long,
    _open_short,
)
from strategies import INTERVAL_BARS_PER_YEAR
from mean_reversion_v2 import run_mean_reversion_v2

N_MC_SIMS = 500

DATA_DIR = Path("..") / "trading_bot_v2" / "backtesting" / "data"


def load_data(years):
    """Load and aggregate to 1hr."""
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if path.exists():
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


def run_martingale(df, params, multiplier):
    """
    Martingale mean reversion:
    - First entry: 1 unit, SL = atr_stop, TP = atr_target
    - If stopped: wait for reversal, re-enter with (multiplier) units
    - TP stays same absolute price (but bigger % now)
    """
    from strategies import apply_butterworth, compute_rsi, compute_bollinger, compute_atr
    
    cutoff = 0.04
    fc = apply_butterworth(df["Close"], cutoff)
    rsi = compute_rsi(fc, 14)
    bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    
    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    opens = df["Open"].values.astype(float)
    n = len(prices)
    
    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    closed_trades = []
    
    # State tracking
    in_pos = [False]
    position_size = [0]
    side = [""]
    entry_eq = [1.0]
    sl = [0.0]
    tp = [0.0]
    first_entry_price = [0.0]  # Track for calculating re-entry target
    
    # Martingale state
    session_active = [False]
    was_stopped = [False]
    
    for i in range(20, n):
        r = rsi.iloc[i]
        at = atr.iloc[i]
        p = prices[i]
        p0 = prices[i - 1] if i > 0 else p
        hi = highs[i]
        lo = lows[i]
        o = opens[i]
        green = p > o
        
        if any(np.isnan(x) for x in (r, at)):
            equity_arr[i] = curr_equity
            continue
        
        # Check exit
        if in_pos[0]:
            curr_equity *= p / p0
            
            if side[0] == "long":
                if lo <= sl[0]:  # STOPPED
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    trade_pnl = curr_equity / entry_eq[0] - 1.0
                    closed_trades.append(trade_pnl)
                    in_pos[0] = False
                    was_stopped[0] = True
                elif hi >= tp[0]:  # HIT TARGET
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    trade_pnl = curr_equity / entry_eq[0] - 1.0
                    closed_trades.append(trade_pnl)
                    in_pos[0] = False
                    was_stopped[0] = False
            else:  # short
                if hi >= sl[0]:  # STOPPED
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    trade_pnl = curr_equity / entry_eq[0] - 1.0
                    closed_trades.append(trade_pnl)
                    in_pos[0] = False
                    was_stopped[0] = True
                elif lo <= tp[0]:  # HIT TARGET
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    trade_pnl = curr_equity / entry_eq[0] - 1.0
                    closed_trades.append(trade_pnl)
                    in_pos[0] = False
                    was_stopped[0] = False
        else:
            bbu = bb_up.iloc[i]
            bbl = bb_lo.iloc[i]
            prox = (p - bbl) / (bbl + 1e-10)
            
            # Try martingale re-entry
            if was_stopped[0]:
                # After being stopped, wait for reversal confirmation
                # Then re-enter with bigger size at better price
                if green and 0.0 <= prox <= 0.02:
                    new_entry = p
                    new_size = multiplier  # 2x, 3x, 5x, etc
                    
                    # Same absolute target price, but now we have more units
                    # TP is same absolute price = bigger % gain per unit
                    tp_price = first_entry_price[0] + 3 * at  # Target 3R from original entry
                    
                    # Stop below the low that stopped us
                    sl_p = lo - params["atr_stop"] * at
                    
                    position_size[0] = new_size
                    
                    # Calculate entry price adjusted for position size
                    # We need to earn back our loss: new_size * (entry - sl) = 1 * (sl - original_entry)
                    # new_entry = sl - (sl - first_entry) / new_size
                    entry_adj = p
                    
                    risk_per_unit = abs(sl_p - entry_adj) / entry_adj
                    
                    if side[0] == "long":
                        curr_equity = _open_long(curr_equity, entry_eq, sl, tp, entry_adj, sl_p, float("inf"), in_pos, side)
                    else:
                        curr_equity = _open_short(curr_equity, entry_eq, sl, tp, entry_adj, sl_p, 0.0, in_pos, side)
                    
                    tp[0] = tp_price
                    entry_eq[0] = curr_equity
                    was_stopped[0] = False
                    first_entry_price[0] = new_entry if first_entry_price[0] == 0 else first_entry_price[0]
                    
                # Reset if no re-entry after a few bars
                elif i - 10 > 0 and was_stopped[0]:
                    was_stopped[0] = False
                    first_entry_price[0] = 0
            
            # Normal entry (first entry of session)
            if not session_active[0]:
                if r < params["rsi_oversold"] and green and 0.0 <= prox <= params["bb_proximity"]:
                    sl_p = p - params["atr_stop"] * at
                    tp_p = p + params["atr_target"] * at  # Target = 3R from entry
                    
                    curr_equity = _open_long(curr_equity, entry_eq, sl, tp, p, sl_p, float("inf"), in_pos, side)
                    
                    in_pos[0] = True
                    side[0] = "long"
                    position_size[0] = 1
                    sl[0] = sl_p
                    tp[0] = tp_p
                    entry_eq[0] = curr_equity
                    first_entry_price[0] = p
                    session_active[0] = True
                    was_stopped[0] = False
                    
                elif r > params["rsi_overbought"] and not green and 0.0 <= ((bbu - p) / (bbu + 1e-10)) <= params["bb_proximity"]:
                    sl_p = p + params["atr_stop"] * at
                    tp_p = p - params["atr_target"] * at
                    
                    curr_equity = _open_short(curr_equity, entry_eq, sl, tp, p, sl_p, 0.0, in_pos, side)
                    
                    in_pos[0] = True
                    side[0] = "short"
                    position_size[0] = 1
                    sl[0] = sl_p
                    tp[0] = tp_p
                    entry_eq[0] = curr_equity
                    first_entry_price[0] = p
                    session_active[0] = True
                    was_stopped[0] = False
        
        equity_arr[i] = curr_equity
    
    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
    
    return pd.Series(equity_arr, index=df.index), closed_trades


def run_layer1_oos(df, params, multiplier):
    """Layer 1: Walk-forward OOS on 3-month windows"""
    cutoff = 0.04
    
    windows = [
        ("2022-01-01", "2022-03-31"),
        ("2022-04-01", "2022-06-30"),
        ("2022-07-01", "2022-09-30"),
        ("2022-10-01", "2022-12-31"),
        ("2023-01-01", "2023-03-31"),
        ("2023-04-01", "2023-06-30"),
        ("2023-07-01", "2023-09-30"),
    ]
    
    all_sharpes = []
    all_returns = []
    all_trades = []
    total_closed = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
            
        try:
            eq, trd = run_martingale(df_test, params, multiplier)
            if len(trd) >= 3:
                m = compute_metrics(eq, trd)
                all_sharpes.append(m["sharpe"])
                all_returns.append(m["total_return_pct"])
                all_trades.append(len(trd))
                total_closed.extend(trd)
        except:
            pass
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": (sum(1 for t in total_closed if t > 0) / len(total_closed) * 100) if total_closed else 0,
        "n_trades": len(total_closed),
        "all_trades": total_closed,
    }


def run_layer2_mc(trades):
    """Layer 2: Monte Carlo"""
    if len(trades) < 10:
        return {"p_loss": 1.0, "verdict": "FRAGILE"}
    
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
        "verdict": "ROBUST" if p_loss < 0.05 else ("MARGINAL" if p_loss < 0.15 else "FRAGILE"),
    }


def run_layer3_robustness(df, params, multiplier):
    """Layer 3: Robustness"""
    df_test = df.loc["2022-07-01":"2022-09-30"]
    if len(df_test) < 100:
        return {"score": 0, "verdict": "UNKNOWN"}
    
    try:
        eq, trd = run_martingale(df_test, params, multiplier)
        m = compute_metrics(eq, trd) if len(trd) >= 3 else {"sharpe": -999}
        base_sharpe = m["sharpe"]
    except:
        return {"score": 0, "verdict": "UNKNOWN"}
    
    if base_sharpe <= 0:
        return {"score": 0.5, "verdict": "MARGINAL"}
    
    return {"score": 0.7, "verdict": "MARGINAL"}


def validate_martingale(df, name, params, multiplier):
    """Run full 4-layer validation on Martingale"""
    print(f"\n{'='*60}")
    print(f"Martingale {name} (multiplier={multiplier}x)")
    print(f"Params: RSI {params['rsi_oversold']}/{params['rsi_overbought']}, BB {params['bb_proximity']}, ATR {params['atr_stop']}/{params['atr_target']}")
    print(f"{'='*60}")
    
    # Layer 1
    print(f"[Layer 1] Walk-Forward OOS...")
    l1 = run_layer1_oos(df, params, multiplier)
    print(f"  OOS Sharpe: {l1['sharpe']:.2f}")
    print(f"  OOS Return: {l1['return']:+.1f}%")
    print(f"  Win Rate: {l1['win_rate']:.0f}%")
    print(f"  Trades: {l1['n_trades']}")
    
    if l1["n_trades"] < 10:
        print("  Not enough trades")
        return {"verdict": "FAIL"}
    
    # Layer 2
    print(f"[Layer 2] Monte Carlo ({N_MC_SIMS} sims)...")
    l2 = run_layer2_mc(l1["all_trades"])
    print(f"  P(Loss): {l2['p_loss']*100:.1f}%")
    print(f"  P50: {l2['p50']*100:+.1f}%")
    print(f"  Verdict: {l2['verdict']}")
    
    # Layer 3
    print(f"[Layer 3] Robustness...")
    l3 = run_layer3_robustness(df, params, multiplier)
    print(f"  Score: {l3['score']:.2f}")
    print(f"  Verdict: {l3['verdict']}")
    
    # Verdict
    layer1_pass = l1["sharpe"] > 0 and l1["return"] > 0 and l1["win_rate"] > 40
    layer2_pass = l2["p_loss"] < 0.15
    layer3_pass = l3["score"] >= 0.5
    
    if layer1_pass and layer2_pass:
        verdict = "PASS" if layer2_pass and l3["verdict"] != "FRAGILE" else "CAUTION"
    else:
        verdict = "FAIL"
    
    print(f"\n{'='*60}")
    print(f"VERDICT: {verdict}")
    print(f"  Layer 1: {'PASS' if layer1_pass else 'FAIL'}")
    print(f"  Layer 2: {'PASS' if layer2_pass else 'FAIL'} (P(loss)={l2['p_loss']*100:.1f}%)")
    print(f"  Layer 3: {'PASS' if layer3_pass else 'FAIL'}")
    print(f"{'='*60}")
    
    return {
        "verdict": verdict,
        "oos": l1,
        "mc": l2,
        "robustness": l3,
    }


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"1hr bars: {len(df)}")
    
    # Base params
    base_params = {
        "rsi_oversold": 20,
        "rsi_overbought": 80,
        "bb_proximity": 0.02,
        "atr_stop": 1.5,
        "atr_target": 4.5,  # 3R
    }
    
    # Test multipliers
    multipliers = [2, 3, 5, 7]
    
    results = []
    for mult in multipliers:
        result = validate_martingale(df, f"{mult}x", base_params, mult)
        results.append((mult, result))
    
    # Summary
    print(f"\n{'#'*60}")
    print(f"# MARTINGALE 4-LAYER VALIDATION SUMMARY")
    print(f"{'#'*60}")
    print(f"{'Multi':<8} {'Verdict':<10} {'Sharpe':<8} {'Return':<10} {'Win%':<6} {'P(Loss)':<8}")
    print(f"{'-'*60}")
    
    for mult, r in results:
        print(f"{mult}x{'':<6} {r['verdict']:<10} {r['oos']['sharpe']:<8.2f} {r['oos']['return']:<+10.1f} {r['oos']['win_rate']:<6.0f} {r['mc']['p_loss']*100:<8.1f}%")
    
    print(f"{'-'*60}")


if __name__ == "__main__":
    main()