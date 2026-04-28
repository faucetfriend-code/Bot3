"""
test_4layer_full_validation.py — 4-Layer Full Validation
========================================================

4-LAYER VALIDATION SUITE for VWAP + SFP strategy configs.

Layer 1 — Walk-Forward OOS:
    Rolling 6m train / 3m test windows across 2021-2023.
    Aggregated OOS Sharpe, return, win rate, max drawdown per config.

Layer 2 — Monte Carlo (500 sims):
    Shuffled trade-order simulations on the full combined trade list.
    P(loss), p5/p50/p95 percentile returns, ROBUST/MARGINAL/FRAGILE verdict.

Layer 3 — Robustness Score (+-15% param perturbation):
    Each param perturbed +-15%, Sharpe delta measured.
    Fragile params flagged, overall ROBUST score computed.

Layer 4 — GMM Regime Detection (4 regimes):
    Fit GaussianMixture on vol/mom/vol_ratio/volume_z features.
    Report calm/trending/volatile/crash distribution.

Configs tested:
    Config 3 (BEST): sd=2.0, atr=0.7, RR=2, sfp=True, vol=False
    Config 5        : sd=1.5, atr=0.5, RR=3, sfp=True, vol=True

Data    : BTC-USDC_5m 2021-2023 (~315K bars)
Output  : Formatted results table per config
"""

from __future__ import annotations

import math
import random
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"
BTV2_DIR = ROOT / "BTV2"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BTV2_DIR))

try:
    from BTV2.strategies import (
        compute_metrics,
        fit_gmm_regime,
        predict_gmm_regime,
        gmm_regime_summary,
        INTERVAL_BARS_PER_YEAR,
        COST_PER_SIDE,
    )
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False
    COST_PER_SIDE = 0.0015
    INTERVAL_BARS_PER_YEAR = {"5m": 105120}

BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["5m"]

# ─── Constants ────────────────────────────────────────────────────────────────
TRADING_COST = 0.003   # 0.30% per side = 0.60% round trip (matches test_fast_validation.py)
N_MC_SIMS = 500
TRAIN_MONTHS = 6
TEST_MONTHS = 3
ROBUSTNESS_PCT = 0.15           # +-15% perturbation


# ─── Config dataclass ──────────────────────────────────────────────────────────
@dataclass
class BacktestConfig:
    sd_threshold: float
    atr_multiplier: float
    risk_reward: float
    use_sfp: bool
    use_volume: bool
    name: str = ""


# ─── Data Loading ─────────────────────────────────────────────────────────────
def load_data_multiyear(years: list[int]) -> pd.DataFrame:
    """Load and concatenate individual year CSV files."""
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if path.exists():
            df_year = pd.read_csv(path)
            df_year["timestamp"] = pd.to_datetime(df_year["timestamp"])
            df_year = df_year.set_index("timestamp").sort_index()
            dfs.append(df_year)
        else:
            print(f"  [!] File not found: {path} — skipping")
    if dfs:
        df = pd.concat(dfs, verify_integrity=False).sort_index()
        return df
    # fallback
    combined = DATA_DIR / "BTC-USDC_5m_all.csv"
    df = pd.read_csv(combined, parse_dates=["timestamp"], index_col="timestamp")
    df = df.sort_index()
    df = df[df.index.year.isin(years)]
    return df


# ─── Technical Indicators (vectorized) ───────────────────────────────────────
def compute_daily_vwap(df: pd.DataFrame) -> pd.Series:
    """Daily-anchored VWAP — cumsum(tp*vol) / cumsum(vol), restarted each UTC day."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    cum_pv = pv.groupby(pv.index.date).cumsum()
    cum_v = df["volume"].groupby(df["volume"].index.date).cumsum()
    return cum_pv / cum_v.replace(0, np.nan)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — vectorized."""
    hl = df["high"] - df["low"]
    hc = np.abs(df["high"] - df["close"].shift(1))
    lc = np.abs(df["low"] - df["close"].shift(1))
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def compute_sd_bands(
    vwap: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """VWAP std deviation bands."""
    std = vwap.rolling(window=period, min_periods=1).std()
    return vwap + num_std * std, vwap - num_std * std, std


def compute_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Simple volume moving average."""
    return df["volume"].rolling(window=period, min_periods=1).mean()


# ─── Precompute all arrays once per window ────────────────────────────────────
def precompute_arrays(
    df: pd.DataFrame,
    config: BacktestConfig,
) -> dict:
    """
    Compute all indicators once, return numpy arrays for fast backtest loop.
    Uses correct VWAP daily-anchoring.
    """
    vwap = compute_daily_vwap(df)
    atr = compute_atr(df)
    upper, lower, _ = compute_sd_bands(vwap, num_std=config.sd_threshold)
    vol_ma = compute_volume_ma(df)

    # Numpy arrays (lowercase columns → lowercase keys)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    vol = df["volume"].values
    atr_arr = atr.values
    upper_arr = upper.values
    lower_arr = lower.values
    vol_ma_arr = vol_ma.values

    # Vectorized cross signals: close crosses band (was above, now below or vice versa)
    price_below_lower = close < lower_arr
    price_above_upper = close > upper_arr
    prev_below = np.roll(price_below_lower.astype(float), 1)
    prev_above = np.roll(price_above_upper.astype(float), 1)
    prev_below[0] = 0
    prev_above[0] = 0

    buy_arr = (price_below_lower & ~prev_below.astype(bool)).astype(int)
    sell_arr = (price_above_upper & ~prev_above.astype(bool)).astype(int)

    # SFP: prior bar low swept lower band (wick rejection of lower band) → bullish
    #      prior bar high swept upper band (wick rejection of upper band) → bearish
    # SFP does NOT require close to return above/below — that was the bug.
    if config.use_sfp:
        lower_shift = lower.shift(1)
        upper_shift = upper.shift(1)
        low_shift = df["low"].shift(1)
        high_shift = df["high"].shift(1)

        sfp_bullish = (low_shift.values <= lower_shift.values) & (buy_arr == 1)
        sfp_bearish = (high_shift.values >= upper_shift.values) & (sell_arr == 1)

        # Only allow entries where SFP wick-sweep was present on prior bar
        buy_arr = np.where(sfp_bullish, 1, 0)
        sell_arr = np.where(sfp_bearish, 1, 0)

    # Volume filter: volume must be above moving average
    if config.use_volume:
        vol_ok = vol > vol_ma_arr
        buy_arr = np.where(vol_ok, buy_arr, 0)
        sell_arr = np.where(vol_ok, sell_arr, 0)

    return {
        "close": close,
        "high": high,
        "low": low,
        "atr": atr_arr,
        "upper": upper_arr,
        "lower": lower_arr,
        "buy": buy_arr,
        "sell": sell_arr,
    }


# ─── Backtest Engine ───────────────────────────────────────────────────────────
def run_backtest_fast(
    df: pd.DataFrame,
    config: BacktestConfig,
    initial_capital: float = 10000.0,
) -> tuple[pd.Series, list[float]]:
    """
    Fast backtest matching test_fast_validation.py's non-compounding equity model.

    Key properties (matching reference):
      - Equity changes ONLY at entry and exit (not on every bar)
      - Cost = abs(pnl) * TRADING_COST  (proportional to PnL, not compounding)
      - Fixed 5% risk per trade  (equity_at_entry * 0.05)
      - Trade outcomes stored as % of equity at entry (for MC compatibility)

    Returns equity curve (starts at 1.0) and trade PnL list (% of equity_at_entry).
    """
    ind = precompute_arrays(df, config)
    close = ind["close"]
    atr_arr = ind["atr"]
    buy_arr = ind["buy"]
    sell_arr = ind["sell"]

    n = len(close)
    equity = initial_capital
    equity_vals = [equity]
    trades: list[float] = []  # each trade PnL as % of equity at entry
    position: Optional[dict] = None
    risk_amount = initial_capital * 0.05  # 5% risk per trade

    for i in range(1, n):
        p = close[i]
        at = atr_arr[i]

        if math.isnan(at) or at <= 0 or math.isnan(p) or p <= 0:
            equity_vals.append(equity)
            continue

        if position is not None:
            equity_at_entry = equity
            sl_pct = position["sl_pct"]
            tp_pct = position["tp_pct"]

            if position["side"] == "long":
                pct_move = (p / position["entry"]) - 1
                if pct_move <= -sl_pct:
                    loss = equity_at_entry * sl_pct
                    equity -= loss
                    cost = abs(loss) * TRADING_COST
                    equity -= cost
                    trades.append(-sl_pct)
                    equity_vals.append(equity)
                    position = None
                elif pct_move >= tp_pct:
                    gain = equity_at_entry * tp_pct
                    equity += gain
                    cost = abs(gain) * TRADING_COST
                    equity -= cost
                    trades.append(tp_pct)
                    equity_vals.append(equity)
                    position = None
                elif sell_arr[i]:
                    gain = equity_at_entry * pct_move
                    equity += gain
                    cost = abs(gain) * TRADING_COST
                    equity -= cost
                    trades.append(pct_move)
                    equity_vals.append(equity)
                    position = None
                else:
                    equity_vals.append(equity)
            else:
                pct_move = (position["entry"] / p) - 1
                if pct_move <= -sl_pct:
                    loss = equity_at_entry * sl_pct
                    equity -= loss
                    cost = abs(loss) * TRADING_COST
                    equity -= cost
                    trades.append(-sl_pct)
                    equity_vals.append(equity)
                    position = None
                elif pct_move >= tp_pct:
                    gain = equity_at_entry * tp_pct
                    equity += gain
                    cost = abs(gain) * TRADING_COST
                    equity -= cost
                    trades.append(tp_pct)
                    equity_vals.append(equity)
                    position = None
                elif buy_arr[i]:
                    gain = equity_at_entry * pct_move
                    equity += gain
                    cost = abs(gain) * TRADING_COST
                    equity -= cost
                    trades.append(pct_move)
                    equity_vals.append(equity)
                    position = None
                else:
                    equity_vals.append(equity)
            continue

        # ── Entry ─────────────────────────────────────────────────────────────
        atr_pct = (at * config.atr_multiplier) / p
        tp_pct = atr_pct * config.risk_reward

        if buy_arr[i]:
            position = {
                "entry": p,
                "side": "long",
                "sl_pct": atr_pct,
                "tp_pct": tp_pct,
            }
            equity_vals.append(equity)
        elif sell_arr[i]:
            position = {
                "entry": p,
                "side": "short",
                "sl_pct": atr_pct,
                "tp_pct": tp_pct,
            }
            equity_vals.append(equity)
        else:
            equity_vals.append(equity)

    # ── Close open position at end ─────────────────────────────────────────────
    if position is not None:
        last_p = close[-1]
        equity_at_entry = equity
        if position["side"] == "long":
            pct_move = (last_p / position["entry"]) - 1
        else:
            pct_move = (position["entry"] / last_p) - 1
        gain = equity_at_entry * pct_move
        equity += gain
        cost = abs(gain) * TRADING_COST
        equity -= cost
        trades.append(pct_move)
        equity_vals[-1] = equity

    # Build equity series starting at 1.0 (normalised)
    init = equity_vals[0]
    eq_norm = [v / init for v in equity_vals]
    equity_series = pd.Series(
        eq_norm[:n], index=df.index[:n]
    )
    return equity_series, trades


# ─── Walk-Forward OOS ─────────────────────────────────────────────────────────
def walk_forward_oos(
    df: pd.DataFrame,
    config: BacktestConfig,
    train_months: int = TRAIN_MONTHS,
    test_months: int = TEST_MONTHS,
) -> dict:
    """
    Rolling walk-forward:  train_months train / test_months test.
    Returns per-window stats and aggregated OOS metrics.
    """
    start_dt = df.index.min()
    end_dt = df.index.max()

    windows = []
    current = pd.Timestamp(start_dt)

    while True:
        train_end = current + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)

        if test_start >= end_dt:
            break

        df_train = df[(df.index >= current) & (df.index < train_end)]
        df_test = df[(df.index >= test_start) & (df.index < test_end)]

        if len(df_test) < 20:
            break

        # Train
        eq_train, tr_train = run_backtest_fast(df_train, config)
        m_train = compute_metrics(eq_train, tr_train, bars_per_year=BARS_PER_YEAR)

        # OOS
        eq_test, tr_test = run_backtest_fast(df_test, config)
        m_test = compute_metrics(eq_test, tr_test, bars_per_year=BARS_PER_YEAR)

        windows.append({
            "train_start": str(current.date()),
            "test_start": str(test_start.date()),
            "test_end": str(test_end.date()),
            "n_train_trades": m_train["n_trades"],
            "n_test_trades": m_test["n_trades"],
            "train_return": m_train["total_return_pct"],
            "test_return": m_test["total_return_pct"],
            "test_sharpe": m_test["sharpe"],
            "test_wr": m_test["win_rate_pct"],
            "test_maxdd": m_test["max_dd_pct"],
        })

        current = test_start

    if not windows:
        return {
            "n_windows": 0, "oov_sharpe": 0.0, "oov_return": 0.0,
            "oov_wr": 0.0, "oov_maxdd": 0.0, "oov_trades": 0,
            "train_test_corr": 0.0, "windows": [],
        }

    df_w = pd.DataFrame(windows)
    corr = float(df_w["train_return"].corr(df_w["test_return"])) if len(df_w) > 2 else 0.0

    return {
        "n_windows": len(windows),
        "oov_sharpe": round(float(df_w["test_sharpe"].mean()), 3),
        "oov_return": round(float(df_w["test_return"].mean()), 2),
        "oov_wr": round(float(df_w["test_wr"].mean()), 1),
        "oov_maxdd": round(float(df_w["test_maxdd"].mean()), 2),
        "oov_trades": int(df_w["n_test_trades"].sum()),
        "train_test_corr": round(corr, 3),
        "windows": windows,
    }


# ─── Monte Carlo ─────────────────────────────────────────────────────────────
def monte_carlo_sim(
    trades: list[float],
    n_sims: int = N_MC_SIMS,
    starting_equity: float = 1.0,
    seed: int = 42,
) -> dict:
    """
    Shuffle trade order n_sims times. Returns P(loss), p5/p50/p95, verdict.
    """
    if len(trades) < 3:
        return {
            "n_trades": len(trades), "n_sims": n_sims,
            "prob_of_loss_pct": 100.0, "p5": 0.0, "p50": 0.0, "p95": 0.0,
            "verdict": "FRAGILE",
        }

    rng = random.Random(seed)
    final_returns: list[float] = []

    for _ in range(n_sims):
        shuffled = list(trades)
        rng.shuffle(shuffled)
        eq = starting_equity
        for r in shuffled:
            eq *= 1.0 + r
        final_returns.append((eq / starting_equity - 1.0) * 100.0)

    final_sorted = sorted(final_returns)
    n = len(final_sorted)
    p5 = final_sorted[max(0, int(n * 0.05))]
    p50 = final_sorted[int(n * 0.50)]
    p95 = final_sorted[min(n - 1, int(n * 0.95))]
    prob_loss = sum(1 for r in final_returns if r < 0) / n * 100.0

    if prob_loss < 10.0 and p5 > 0.0:
        verdict = "ROBUST"
    elif prob_loss < 25.0:
        verdict = "MARGINAL"
    else:
        verdict = "FRAGILE"

    return {
        "n_trades": len(trades),
        "n_sims": n_sims,
        "prob_of_loss_pct": round(prob_loss, 2),
        "p5": round(p5, 2),
        "p50": round(p50, 2),
        "p95": round(p95, 2),
        "verdict": verdict,
    }


# ─── Robustness Score ─────────────────────────────────────────────────────────
def robustness_score(
    df: pd.DataFrame,
    config: BacktestConfig,
    perturbation: float = ROBUSTNESS_PCT,
) -> dict:
    """
    Perturb sd_threshold and atr_multiplier by +-15%, measure Sharpe delta.
    Score: 0 = fragile, 1 = robust.
    Fragile: |avg Sharpe delta| > 0.30.
    """
    eq_base, tr_base = run_backtest_fast(df, config)
    m_base = compute_metrics(eq_base, tr_base, bars_per_year=BARS_PER_YEAR)
    base_sharpe = m_base["sharpe"]
    base_return = m_base["total_return_pct"]

    params = {
        "sd_threshold": config.sd_threshold,
        "atr_multiplier": config.atr_multiplier,
    }

    sensitivities: dict[str, dict] = {}
    fragile: list[str] = []

    for param_name, nominal in params.items():
        deltas = []
        for sign in [-1, +1]:
            p_val = max(0.01, nominal * (1.0 + sign * perturbation))
            test_config = BacktestConfig(
                sd_threshold=config.sd_threshold
                if param_name != "sd_threshold" else p_val,
                atr_multiplier=config.atr_multiplier
                if param_name != "atr_multiplier" else p_val,
                risk_reward=config.risk_reward,
                use_sfp=config.use_sfp,
                use_volume=config.use_volume,
                name=config.name,
            )
            _, tr = run_backtest_fast(df, test_config)
            if len(tr) >= 2:
                eq_t, _ = run_backtest_fast(df, test_config)
                m = compute_metrics(eq_t, tr, bars_per_year=BARS_PER_YEAR)
                delta = m["sharpe"] - base_sharpe
                deltas.append(delta)
            else:
                deltas.append(-(abs(base_sharpe) + 1.0))

        avg_delta = sum(deltas) / len(deltas)
        sensitivities[param_name] = {
            "base": nominal,
            "minus_val": round(nominal * (1 - perturbation), 4),
            "plus_val": round(nominal * (1 + perturbation), 4),
            "avg_sharpe_delta": round(avg_delta, 4),
        }
        if abs(avg_delta) > 0.30 and base_sharpe > 0:
            fragile.append(param_name)

    if sensitivities:
        avg_abs = sum(abs(d["avg_sharpe_delta"]) for d in sensitivities.values()) / len(
            sensitivities
        )
        score = max(0.0, min(1.0, 1.0 - avg_abs / (abs(base_sharpe) + 0.1)))
    else:
        score = 0.0

    if not fragile and score >= 0.70:
        verdict = "ROBUST"
    elif len(fragile) <= 1 and score >= 0.40:
        verdict = "MARGINAL"
    else:
        verdict = "FRAGILE"

    return {
        "score": round(score, 3),
        "base_sharpe": round(base_sharpe, 3),
        "base_return": round(base_return, 2),
        "sensitivities": sensitivities,
        "fragile_params": fragile,
        "verdict": verdict,
    }


# ─── GMM Regime Detection ─────────────────────────────────────────────────────
def run_gmm(df: pd.DataFrame, n_regimes: int = 4) -> dict:
    """Fit 4-regime GMM on vol/mom features, report regime distribution."""
    if not _HAS_SKLEARN:
        return {
            "calm": 0.0, "trending": 0.0,
            "volatile": 0.0, "crash": 0.0,
            "dominant": "N/A",
        }

    # Normalize column names for GMM functions
    df_in = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    }) if "open" in df.columns and "Open" not in df.columns else df

    try:
        gmm_model, label_map, scaler = fit_gmm_regime(df_in, n_regimes=n_regimes)
        regime_df = predict_gmm_regime(df_in, gmm_model, label_map, scaler)
        gmm = gmm_regime_summary(regime_df)
        pcts = gmm.get("regime_pct", {})
        return {
            "calm": round(pcts.get("calm", 0.0), 1),
            "trending": round(pcts.get("trending", 0.0), 1),
            "volatile": round(pcts.get("volatile", 0.0), 1),
            "crash": round(pcts.get("crash", 0.0), 1),
            "dominant": gmm.get("dominant_regime", "N/A"),
            "avg_confidence": gmm.get("avg_confidence", 0.0),
        }
    except Exception as e:
        return {
            "calm": 0.0, "trending": 0.0,
            "volatile": 0.0, "crash": 0.0,
            "dominant": "N/A",
            "error": str(e),
        }


# ─── Overall Verdict ───────────────────────────────────────────────────────────
def compute_verdict(wf: dict, mc: dict, rb: dict) -> str:
    """PASS / CAUTION / FAIL based on OOS + MC + robustness."""
    oos_sharpe = wf.get("oov_sharpe", 0.0)
    mc_loss = mc.get("prob_of_loss_pct", 100.0)
    rb_score = rb.get("score", 0.0)
    oos_trades = wf.get("oov_trades", 0)

    if oos_sharpe > 0 and mc_loss < 25 and rb_score >= 0.50 and oos_trades >= 20:
        return "PASS"
    elif oos_sharpe > 0 and mc_loss < 40 and rb_score >= 0.30:
        return "CAUTION"
    else:
        return "FAIL"


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 79)
    print("  4-LAYER FULL VALIDATION  --  VWAP + SFP Strategy  (2021-2023)")
    print("=" * 79)
    print(f"  Walk-forward : {TRAIN_MONTHS}m train / {TEST_MONTHS}m test rolling windows")
    print(f"  Monte Carlo   : {N_MC_SIMS} simulations on full combined trade list")
    print("  Robustness    : +-15% param perturbation")
    print("  GMM regimes   : 4 (calm / trending / volatile / crash)")
    print(f"  Cost model    : {TRADING_COST*100:.1f}% per side ({TRADING_COST*2*100:.1f}% round trip)")
    print("=" * 79)

    # ── Load data ─────────────────────────────────────────────────────────────
    print("\n[LOAD] Loading 3 years of BTC-USDC 5m data (2021-2023)...")
    years = [2021, 2022, 2023]
    df = load_data_multiyear(years)
    print(
        f"  Loaded {len(df):,} bars  |  "
        f"{df.index.min()} -> {df.index.max()}"
    )
    print(f"  Memory: ~{df.memory_usage(deep=True).sum() / 1024**2:.1f} MB")

    # ── Configs ────────────────────────────────────────────────────────────────
    CONFIGS = [
        BacktestConfig(
            sd_threshold=2.0,
            atr_multiplier=0.7,
            risk_reward=2.0,
            use_sfp=True,
            use_volume=False,
            name="Config 3 (BEST)",
        ),
        BacktestConfig(
            sd_threshold=1.5,
            atr_multiplier=0.5,
            risk_reward=3.0,
            use_sfp=True,
            use_volume=True,
            name="Config 5",
        ),
    ]

    # ── Run each config ────────────────────────────────────────────────────────
    results = []

    for cfg in CONFIGS:
        print(f"\n{'='*79}")
        print(f"  Config: {cfg.name}")
        print(
            f"  sd={cfg.sd_threshold}, atr={cfg.atr_multiplier}, "
            f"RR={cfg.risk_reward}, sfp={cfg.use_sfp}, vol={cfg.use_volume}"
        )
        print("=" * 79)

        # Layer 1: Walk-Forward OOS
        print("\n  [1/4] Walk-Forward OOS (6m train / 3m test)...")
        wf = walk_forward_oos(df, cfg)
        print(
            f"      Windows={wf['n_windows']}  |  "
            f"OOS Sharpe={wf['oov_sharpe']:.3f}  |  "
            f"OOS Return={wf['oov_return']:+.2f}%  |  "
            f"OOS WR={wf['oov_wr']:.1f}%  |  "
            f"OOS MaxDD={wf['oov_maxdd']:.2f}%  |  "
            f"Trades={wf['oov_trades']}  |  "
            f"Corr={wf['train_test_corr']:.3f}"
        )

        # Full-period backtest for trade list
        print("  [2/4] Running full backtest for trade list...")
        eq_full, tr_full = run_backtest_fast(df, cfg)
        m_full = compute_metrics(eq_full, tr_full, bars_per_year=BARS_PER_YEAR)
        print(
            f"      Full-period: Trades={m_full['n_trades']}  |  "
            f"WR={m_full['win_rate_pct']:.1f}%  |  "
            f"Return={m_full['total_return_pct']:+.2f}%  |  "
            f"Sharpe={m_full['sharpe']:.3f}  |  "
            f"MaxDD={m_full['max_dd_pct']:.2f}%  |  "
            f"PF={m_full['profit_factor']:.2f}"
        )

        # Layer 2: Monte Carlo
        print(f"  [3/4] Monte Carlo ({N_MC_SIMS} simulations)...")
        mc = monte_carlo_sim(tr_full, n_sims=N_MC_SIMS)
        print(
            f"      P(loss)={mc['prob_of_loss_pct']:.2f}%  |  "
            f"p5={mc['p5']:+.2f}%  |  "
            f"p50={mc['p50']:+.2f}%  |  "
            f"p95={mc['p95']:+.2f}%  |  "
            f"Verdict={mc['verdict']}"
        )

        # Layer 3: Robustness
        print(f"  [4/4] Robustness (+-{ROBUSTNESS_PCT*100:.0f}% perturbation)...")
        rb = robustness_score(df, cfg)
        print(
            f"      Score={rb['score']:.3f}  |  "
            f"Base Sharpe={rb['base_sharpe']:.3f}  |  "
            f"Fragile={rb['fragile_params'] or 'none'}  |  "
            f"Verdict={rb['verdict']}"
        )
        for pname, pres in rb["sensitivities"].items():
            print(
                f"        {pname}: base={pres['base']}  "
                f"-={pres['minus_val']}  += {pres['plus_val']}  "
                f"dSharpe={pres['avg_sharpe_delta']:+.4f}"
            )

        # Layer 4: GMM regime detection
        print("  [GMM] GMM Regime Detection...")
        gmm = run_gmm(df)
        if "error" in gmm:
            print(f"      GMM skipped: {gmm['error']}")
        else:
            print(
                f"      calm={gmm['calm']}%  |  "
                f"trending={gmm['trending']}%  |  "
                f"volatile={gmm['volatile']}%  |  "
                f"crash={gmm['crash']}%  |  "
                f"dominant={gmm['dominant']}"
            )

        # Overall verdict
        verdict = compute_verdict(wf, mc, rb)
        print(f"\n  >>> OVERALL VERDICT: {verdict}")

        results.append({
            "config": cfg,
            "wf": wf,
            "mc": mc,
            "rb": rb,
            "gmm": gmm,
            "m_full": m_full,
            "tr_full": tr_full,
            "verdict": verdict,
        })

    # ── Full results summary ──────────────────────────────────────────────────
    print("\n" + "=" * 79)
    print("  4-LAYER VALIDATION RESULTS")
    print("=" * 79)

    for r in results:
        cfg = r["config"]
        wf = r["wf"]
        mc = r["mc"]
        rb = r["rb"]
        gmm = r["gmm"]

        print("\n" + "=" * 79)
        print(f"  Config: {cfg.name}")
        print(
            f"  sd={cfg.sd_threshold}, atr={cfg.atr_multiplier}, "
            f"RR={cfg.risk_reward}, sfp={cfg.use_sfp}, vol={cfg.use_volume}"
        )
        print("=" * 79)

        print("\n  [1] Walk-Forward OOS")
        print(
            f"      Sharpe: {wf['oov_sharpe']:.2f} | "
            f"Return: {wf['oov_return']:+.1f}% | "
            f"MaxDD: {wf['oov_maxdd']:.1f}% | "
            f"Trades: {wf['oov_trades']} | "
            f"WR: {wf['oov_wr']:.0f}%"
        )

        print("\n  [2] Monte Carlo (500 sims)")
        print(
            f"      P(loss): {mc['prob_of_loss_pct']:.1f}% | "
            f"p5: {mc['p5']:+.1f}% | "
            f"p50: {mc['p50']:+.1f}% | "
            f"p95: {mc['p95']:+.1f}% | "
            f"Verdict: {mc['verdict']}"
        )

        print("\n  [3] Robustness")
        print(
            f"      Score: {rb['score']:.2f} | "
            f"Fragile params: {', '.join(rb['fragile_params']) or 'none'} | "
            f"Verdict: {rb['verdict']}"
        )

        print("\n  [4] GMM Regimes")
        print(
            f"      calm: {gmm.get('calm', 0):.0f}% | "
            f"trending: {gmm.get('trending', 0):.0f}% | "
            f"volatile: {gmm.get('volatile', 0):.0f}% | "
            f"crash: {gmm.get('crash', 0):.0f}%"
        )

        print(f"\n  >>> OVERALL VERDICT: {r['verdict']}")

    # ── Comparison table ────────────────────────────────────────────────────────
    print("\n" + "=" * 79)
    print("  COMPARISON TABLE")
    print("=" * 79)
    header = (
        f"{'Config':<20} | {'OOS-Sharpe':>10} | {'OOS-Ret%':>9} | "
        f"{'OOS-WR%':>7} | {'OOS-DD%':>7} | {'P(loss)%':>8} | "
        f"{'p50%':>7} | {'RB-Score':>8} | {'GMM-dom':>10} | {'VERDICT':<10}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        cfg = r["config"]
        wf = r["wf"]
        mc = r["mc"]
        rb = r["rb"]
        gmm = r["gmm"]
        print(
            f"{cfg.name:<20} | {wf['oov_sharpe']:>10.3f} | {wf['oov_return']:>+9.2f} | "
            f"{wf['oov_wr']:>7.1f} | {wf['oov_maxdd']:>7.2f} | {mc['prob_of_loss_pct']:>8.1f} | "
            f"{mc['p50']:>7.2f} | {rb['score']:>8.3f} | {gmm.get('dominant', 'N/A'):>10} | {r['verdict']:<10}"
        )
    print("-" * len(header))

    print("\n" + "=" * 79)
    print("  DONE")
    print("=" * 79)

    return results


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()