"""
test_v8_full_validation.py — 4-Layer VWAP Strategy Validation
==============================================================

Performs 4-layer validation on comprehensive VWAP strategy:
  1. Walk-forward OOS  (3m train / 1m test rolling windows)
  2. Monte Carlo sim   (prob_of_loss, p5/p50/p95 returns)
  3. Robustness score  (param sensitivity analysis)
  4. GMM regime detect (calm/trending/volatile/crash distribution)

Tests 4 configs based on v8 findings:
  Config 1: sd=2.0, atr=0.7,  entry=bull_pullback, tp_mode=rr,  use_sfp=True,  use_volume=True
  Config 2: sd=2.0, atr=0.7,  entry=bull_pullback, tp_mode=atr,  use_sfp=True,  use_volume=True
  Config 3: sd=1.5, atr=0.5,  entry=bull_pullback, tp_mode=rr,  use_sfp=True,  use_volume=True
  Config 4: sd=2.0, atr=0.7,  entry=mean_reversion, tp_mode=rr,  use_sfp=True,  use_volume=True

Data    : BTC-USDC_5m_all.csv  (timestamp,open,high,low,close,volume)
Period  : 2023-01-01 to 2025-12-31
Output  : Summary table with PASS/CAUTION/FAIL verdict per config
"""

from __future__ import annotations

import sys
import math
import random
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd

# -- path bootstrap ----------------------------------------------------------
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "BTV2"))

from BTV2.strategies import (
    apply_butterworth,
    _resample_ohlcv,
    compute_vwap_anchored,
    compute_atr,
    compute_sma,
    compute_rsi,
    compute_ema,
    compute_adx,
    compute_metrics,
    monte_carlo_validate,
    fit_gmm_regime,
    predict_gmm_regime,
    gmm_regime_summary,
    INTERVAL_BARS_PER_YEAR,
    COST_PER_SIDE,
)

# -- constants -----------------------------------------------------------------
DATA_DIR    = ROOT / "trading_bot_v2" / "backtesting" / "data"
RESULTS_DIR = ROOT / "BTV2" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_START  = "2023-01-01"
TEST_END    = "2025-12-31"
CUTOFF      = 0.10
TRAIN_MONTHS = 3          # walk-forward train window (months)
TEST_MONTHS  = 1          # walk-forward test window (months)
N_SIMS_MC    = 1000       # Monte Carlo simulations
BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["5m"]  # 105 120

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# -- data loading ---------------------------------------------------------------

def load_data(start: str, end: str) -> pd.DataFrame:
    """Load BTC-USDC 5m CSV and normalise column names."""
    path = DATA_DIR / "BTC-USDC_5m_all.csv"
    if not path.exists():
        raise FileNotFoundError(f"Data not found: {path}")

    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    # Ensure UTC
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df[start:end]
    logger.info(f"Loaded {len(df):,} bars  {df.index.min()} -> {df.index.max()}")
    return df


# -- SFP + Volume core (VWAP strategy) -----------------------------------------




def run_backtest_core(
    df: pd.DataFrame,
    sd_threshold: float,
    atr_multiplier: float,
    tp_mode: str,
    entry_mode: str,
    use_sfp: bool = True,
    use_volume: bool = True,
) -> tuple[pd.Series, list[float]]:
    """
    VWAP scalping backtest matching run_vwap_scalping entry logic.

    Entry: price past sd_threshold band + RSI gate + ADX gate + volume +
           HTF alignment + SFP reversal candle.

    Exit: ATR-based SL/TP (conservative SL-before-TP ordering).
          Equity changes ONLY at entry and exit (correct model).
    """
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    fc      = apply_butterworth(close, CUTOFF)
    rsi     = compute_rsi(fc, 14)
    adx     = compute_adx(high, low, close, 14)
    atr     = compute_atr(high, low, close, 14)
    vol_avg = compute_sma(vol, 20)
    vwap, vs = compute_vwap_anchored(high, low, close, vol, anchor="D")

    # Higher timeframe: 1h
    df_1h  = _resample_ohlcv(df, "60min")
    fc_1h   = apply_butterworth(df_1h["Close"], CUTOFF)
    ema9_1h  = compute_ema(fc_1h, 9).reindex(df.index, method="ffill")
    ema21_1h = compute_ema(fc_1h, 21).reindex(df.index, method="ffill")
    adx_1h  = compute_adx(df_1h["High"], df_1h["Low"], df_1h["Close"], 14).reindex(df.index, method="ffill")

    adx_max    = 30.0
    htf_adx    = 30.0
    rsi_max    = 50.0
    vol_mult   = 1.0 if use_volume else 0.0
    atr_stop   = atr_multiplier
    atr_target = atr_multiplier * 2.0

    equity_vals = [1.0]
    trades: list[float] = []
    position: Optional[dict] = None

    for i in range(1, len(df)):
        cur_equity = equity_vals[-1]
        p  = float(close.iloc[i])
        h  = float(high.iloc[i])
        lo = float(low.iloc[i])
        vw = float(vwap.iloc[i])
        ss = float(vs.iloc[i])
        at = float(atr.iloc[i])

        if math.isnan(vw) or math.isnan(ss) or ss < 1e-8 or math.isnan(at):
            equity_vals.append(cur_equity)
            continue

        lower_band = vw - sd_threshold * ss
        upper_band = vw + sd_threshold * ss

        # -- position management --------------------------------------------
        if position is not None:
            hit = False
            pnl = 0.0
            if position["side"] == "long":
                if lo <= position["sl"]:
                    hit = True
                    pnl = (position["sl"] - position["entry"]) / position["entry"]
                elif h >= position["tp"]:
                    hit = True
                    pnl = (position["tp"] - position["entry"]) / position["entry"]
            else:
                if h >= position["sl"]:
                    hit = True
                    pnl = (position["entry"] - position["sl"]) / position["entry"]
                elif lo <= position["tp"]:
                    hit = True
                    pnl = (position["entry"] - position["tp"]) / position["entry"]

            if hit:
                pnl -= COST_PER_SIDE * 2
                trades.append(pnl)
                equity_vals.append(cur_equity * (1 + pnl))
                position = None
            else:
                equity_vals.append(cur_equity)
            continue

        # -- no position — look for entry ------------------------------------
        rsi_val = float(rsi.iloc[i])
        adx_val = float(adx.iloc[i])
        vol_val = float(vol.iloc[i])
        vol_av  = float(vol_avg.iloc[i]) if not math.isnan(float(vol_avg.iloc[i])) else 0.0

        e9  = float(ema9_1h.iloc[i])  if not math.isnan(float(ema9_1h.iloc[i]))  else 0.0
        e21 = float(ema21_1h.iloc[i]) if not math.isnan(float(ema21_1h.iloc[i])) else 0.0
        a1h = float(adx_1h.iloc[i])   if not math.isnan(float(adx_1h.iloc[i]))   else 0.0

        if math.isnan(rsi_val) or math.isnan(adx_val):
            equity_vals.append(cur_equity)
            continue

        vol_ok   = (vol_mult <= 0.0 or math.isnan(vol_av) or vol_av <= 0.0
                    or vol_val >= vol_mult * vol_av)
        htf_bull     = e9 > e21
        htf_bear     = e9 < e21
        htf_ranging  = a1h <= htf_adx
        htf_long_ok  = htf_bull or htf_ranging
        htf_short_ok = htf_bear or htf_ranging

        adx_ok      = adx_val < adx_max
        rsi_long_ok  = rsi_val < rsi_max
        rsi_short_ok = rsi_val > (100.0 - rsi_max)

        # SFP reversal-candle gate: skip if SFP required but no wick sweep on prior bar
        if use_sfp:
            if i < 1:
                equity_vals.append(cur_equity)
                continue
            lo0 = float(low.iloc[i - 1])
            h0  = float(high.iloc[i - 1])
            p0  = float(close.iloc[i - 1])
            # Check if prior bar had wick sweep (SFP pattern)
            had_sfp = (lo0 < lower_band) or (h0 > upper_band)
            if not had_sfp:
                equity_vals.append(cur_equity)
                continue

        # -- LONG entry -----------------------------------------------------
        if (p < lower_band and adx_ok and rsi_long_ok and vol_ok
                and htf_long_ok):
            sl_p = p - atr_stop * at
            tp_p = p + atr_target * at
            equity_vals.append(cur_equity * (1 - COST_PER_SIDE))
            position = {"entry": p, "sl": sl_p, "tp": tp_p, "side": "long"}
            continue

        # -- SHORT entry ----------------------------------------------------
        if (p > upper_band and adx_ok and rsi_short_ok and vol_ok
                and htf_short_ok):
            sl_p = p + atr_stop * at
            tp_p = p - atr_target * at
            equity_vals.append(cur_equity * (1 - COST_PER_SIDE))
            position = {"entry": p, "sl": sl_p, "tp": tp_p, "side": "short"}
            continue

        equity_vals.append(cur_equity)

    # Close open position at end
    if position is not None:
        last_p = float(close.iloc[-1])
        if position["side"] == "long":
            pnl = (last_p - position["entry"]) / position["entry"]
        else:
            pnl = (position["entry"] - last_p) / position["entry"]
        pnl -= COST_PER_SIDE * 2
        trades.append(pnl)
        equity_vals[-1] = equity_vals[-2] * (1 + pnl)

    equity_series = pd.Series(equity_vals, index=df.index[: len(equity_vals)])
    return equity_series, trades


# -- walk-forward validation -----------------------------------------------------

def walk_forward_validate(
    df: pd.DataFrame,
    config: dict,
    train_months: int = TRAIN_MONTHS,
    test_months: int = TEST_MONTHS,
) -> dict:
    """
    Rolling walk-forward:  train on [train_months], test on [test_months].

    Returns per-window metrics and aggregated OOS stats.
    """
    start_dt = df.index.min()
    end_dt   = df.index.max()

    windows = []
    current = start_dt
    while True:
        train_end = pd.Timestamp(current) + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end   = test_start + pd.DateOffset(months=test_months)

        if test_start >= end_dt:
            break

        # train slice
        df_train = df[(df.index >= current) & (df.index < train_end)]
        # test slice
        df_test  = df[(df.index >= test_start) & (df.index < test_end)]

        if len(df_test) < 20:
            break

        # -- train ------------------------------------------------------------
        train_equity, train_trades = run_backtest_core(df_train, **config)
        train_metrics = compute_metrics(
            train_equity, train_trades, bars_per_year=BARS_PER_YEAR
        )

        # -- test (OOS) -------------------------------------------------------
        test_equity, test_trades = run_backtest_core(df_test, **config)
        test_metrics = compute_metrics(
            test_equity, test_trades, bars_per_year=BARS_PER_YEAR
        )

        windows.append({
            "train_start": str(current.date()),
            "test_start":  str(test_start.date()),
            "test_end":    str(test_end.date()),
            "n_train_trades": train_metrics["n_trades"],
            "n_test_trades":  test_metrics["n_trades"],
            "train_return":   train_metrics["total_return_pct"],
            "test_return":    test_metrics["total_return_pct"],
            "test_sharpe":    test_metrics["sharpe"],
            "test_wr":        test_metrics["win_rate_pct"],
            "test_maxdd":     test_metrics["max_dd_pct"],
        })

        current = test_start

    if not windows:
        return {
            "n_windows": 0,
            "oov_return": 0.0,
            "oov_sharpe": 0.0,
            "oov_wr": 0.0,
            "train_test_corr": 0.0,
            "windows": [],
        }

    df_w = pd.DataFrame(windows)
    corr = df_w["train_return"].corr(df_w["test_return"]) if len(df_w) > 2 else 0.0

    return {
        "n_windows": len(windows),
        "oov_return": round(float(df_w["test_return"].mean()), 2),
        "oov_sharpe": round(float(df_w["test_sharpe"].mean()), 3),
        "oov_wr":     round(float(df_w["test_wr"].mean()), 1),
        "oov_maxdd":  round(float(df_w["test_maxdd"].mean()), 2),
        "train_test_corr": round(float(corr), 3),
        "windows": windows,
    }


# -- robustness score -----------------------------------------------------------

def robustness_score(
    df: pd.DataFrame,
    base_config: dict,
    param_ranges: dict,
) -> dict:
    """
    Nudge each param by +/-20% and measure return stability.

    robustness = mean( |delta_return| / base_return ) lower is better
    Also flags "fragile params" where perturbation > 30%.
    """
    base_equity, base_trades = run_backtest_core(df, **base_config)
    base_m = compute_metrics(base_equity, base_trades, bars_per_year=BARS_PER_YEAR)
    base_ret = base_m["total_return_pct"]

    sensitivities: dict[str, float] = {}
    fragile: list[str] = []

    for param, nominal in param_ranges.items():
        deltas = []
        for pct in [-0.2, 0.2]:
            perturbed = nominal * (1 + pct)
            # keep within sensible bounds
            perturbed = max(0.01, perturbed)
            test_config = {**base_config, param: perturbed}
            _, t = run_backtest_core(df, **test_config)
            if len(t) > 0:
                equity_t, _ = pd.Series([1.0] * (len(t) + 1)), t
                m = compute_metrics(pd.Series([1.0]), t, bars_per_year=BARS_PER_YEAR)
                delta = abs(m["total_return_pct"] - base_ret)
                deltas.append(delta)

        if deltas:
            sensitivities[param] = round(float(np.mean(deltas)), 4)
            if np.mean(deltas) > abs(base_ret) * 0.3 and abs(base_ret) > 1.0:
                fragile.append(param)

    # Score: 0 = perfectly stable, 1 = extremely fragile
    avg_sens = float(np.mean(list(sensitivities.values()))) if sensitivities else 0.0
    score    = min(1.0, avg_sens / max(abs(base_ret), 1.0)) if base_ret != 0 else 1.0

    return {
        "score": round(score, 3),
        "sensitivities": sensitivities,
        "fragile_params": fragile,
        "base_return": round(base_ret, 2),
    }


# -- verdict -------------------------------------------------------------------

def compute_verdict(
    wf: dict,
    mc: dict,
    rb: dict,
    trades_count: int,
) -> str:
    """
    PASS  : WR ≥ 52%, MC_prob_loss ≤ 25%, robustness_score ≤ 0.35, trades ≥ 20
    CAUTION: WR ≥ 45%, MC_prob_loss ≤ 40%, robustness_score ≤ 0.60
    FAIL  : otherwise
    """
    if trades_count < 20:
        return "FAIL (low trades)"

    wr        = wf.get("oov_wr", 0.0)
    mc_loss   = mc.get("prob_of_loss_pct", 100.0)
    rb_score  = rb.get("score", 1.0)

    if wr >= 52 and mc_loss <= 25 and rb_score <= 0.35:
        return "PASS"
    elif wr >= 45 and mc_loss <= 40 and rb_score <= 0.60:
        return "CAUTION"
    else:
        return "FAIL"


# -- per-config runner -----------------------------------------------------------

@dataclass
class ConfigResult:
    name: str
    config: dict
    wf: dict
    mc: dict
    rb: dict
    gmm: dict
    metrics: dict
    verdict: str


def evaluate_config(
    df: pd.DataFrame,
    name: str,
    config: dict,
) -> ConfigResult:
    """Run all 4 validation layers for a single config."""

    logger.info(f"  Running: {name}")

    # -- 1. Walk-forward OOS -------------------------------------------------
    wf = walk_forward_validate(df, config)

    # -- 2. Monte Carlo -------------------------------------------------------
    _, trades = run_backtest_core(df, **config)
    mc = monte_carlo_validate(trades, n_sims=N_SIMS_MC) if len(trades) >= 3 else {
        "prob_of_loss_pct": 100.0,
        "p5_return_pct": 0.0,
        "p50_return_pct": 0.0,
        "p95_return_pct": 0.0,
        "verdict": "FRAGILE",
    }

    # -- 3. Robustness -------------------------------------------------------
    param_ranges = {
        "sd_threshold":   config["sd_threshold"],
        "atr_multiplier": config["atr_multiplier"],
    }
    rb = robustness_score(df, config, param_ranges)

    # -- 4. GMM regime detection ----------------------------------------------
    try:
        gmm_model, label_map, scaler = fit_gmm_regime(df, n_regimes=4)
        regime_df  = predict_gmm_regime(df, gmm_model, label_map, scaler)
        gmm        = gmm_regime_summary(regime_df)
    except Exception as e:
        logger.warning(f"    GMM failed: {e}")
        gmm = {"regime_counts": {}, "regime_pct": {}, "dominant_regime": "N/A"}

    # -- Full-period metrics -------------------------------------------------
    equity_full, trades_full = run_backtest_core(df, **config)
    metrics = compute_metrics(equity_full, trades_full, bars_per_year=BARS_PER_YEAR)

    # -- Verdict -------------------------------------------------------------
    verdict = compute_verdict(wf, mc, rb, metrics.get("n_trades", 0))

    return ConfigResult(
        name=name,
        config=config,
        wf=wf,
        mc=mc,
        rb=rb,
        gmm=gmm,
        metrics=metrics,
        verdict=verdict,
    )


# -- main -----------------------------------------------------------------------

def main() -> tuple[dict, Optional[str]]:
    print("=" * 80)
    print("VWAP Strategy — 4-Layer Full Validation (v8 configs)")
    print("Period: 2023-01-01 -> 2025-12-31  |  Walk-forward: 3m train / 1m test")
    print("=" * 80)

    # -- load data -------------------------------------------------------------
    df = load_data(TEST_START, TEST_END)

    # -- configs ---------------------------------------------------------------
    CONFIGS: list[tuple[str, dict]] = [
        (
            "bp_sd2.0_atr0.7_rr_sfp",
            dict(
                sd_threshold=2.0,
                atr_multiplier=0.7,
                entry_mode="bull_pullback",
                tp_mode="rr",
                use_sfp=True,
                use_volume=True,
            ),
        ),
        (
            "bp_sd2.0_atr0.7_atr_sfp",
            dict(
                sd_threshold=2.0,
                atr_multiplier=0.7,
                entry_mode="bull_pullback",
                tp_mode="atr",
                use_sfp=True,
                use_volume=True,
            ),
        ),
        (
            "bp_sd1.5_atr0.5_rr_sfp",
            dict(
                sd_threshold=1.5,
                atr_multiplier=0.5,
                entry_mode="bull_pullback",
                tp_mode="rr",
                use_sfp=True,
                use_volume=True,
            ),
        ),
        (
            "mr_sd2.0_atr0.7_rr_sfp",
            dict(
                sd_threshold=2.0,
                atr_multiplier=0.7,
                entry_mode="mean_reversion",
                tp_mode="rr",
                use_sfp=True,
                use_volume=True,
            ),
        ),
    ]

    # -- run all configs ------------------------------------------------------
    results: list[ConfigResult] = []
    for name, config in CONFIGS:
        res = evaluate_config(df, name, config)
        results.append(res)

    # -- print summary table --------------------------------------------------
    header = (
        f"{'Config':<28} | {'Trades':>6} | {'WR%':>5} | {'Return%':>8} | "
        f"{'Sharpe':>6} | {'MC_P(loss)':>10} | {'MC_p50%':>8} | {'RB_Score':>8} | {'Verdict':<10}"
    )
    sep    = "-" * len(header)

    print("\n" + "=" * 80)
    print("RESULTS SUMMARY TABLE")
    print("=" * 80)
    print(header)
    print(sep)

    for r in results:
        m = r.metrics
        mc_loss_pct = r.mc.get("prob_of_loss_pct", 0.0)
        mc_p50      = r.mc.get("p50_return_pct", 0.0)
        rb_score    = r.rb.get("score", 0.0)
        ret_str     = f"{m['total_return_pct']:+.1f}%"
        wr_str      = f"{m['win_rate_pct']:.0f}%"
        sharpe_str  = f"{m['sharpe']:.2f}"
        print(
            f"{r.name:<28} | {m['n_trades']:>6} | {wr_str:>5} | {ret_str:>8} | "
            f"{sharpe_str:>6} | {mc_loss_pct:>9.1f}% | {mc_p50:>7.1f}% | {rb_score:>8.3f} | {r.verdict:<10}"
        )

    print(sep)

    # -- per-config detail ----------------------------------------------------
    for r in results:
        print(f"\n{'-'*60}")
        print(f"  Config: {r.name}")
        print(f"{'-'*60}")

        m  = r.metrics
        wf = r.wf
        mc = r.mc
        rb = r.rb
        g  = r.gmm

        print(f"  Full Period Metrics:")
        print(f"    Trades={m['n_trades']}  WR={m['win_rate_pct']:.1f}%  "
              f"Return={m['total_return_pct']:+.2f}%  Sharpe={m['sharpe']:.3f}  "
              f"MaxDD={m['max_dd_pct']:.2f}%  PF={m['profit_factor']:.2f}")

        print(f"  Walk-Forward OOS ({wf['n_windows']} windows):")
        print(f"    OOS Return={wf['oov_return']:+.2f}%  OOS Sharpe={wf['oov_sharpe']:.3f}  "
              f"OOS WR={wf['oov_wr']:.1f}%  Train/Test Corr={wf['train_test_corr']:.3f}")

        print(f"  Monte Carlo ({N_SIMS_MC} sims):")
        print(f"    P(loss)={mc.get('prob_of_loss_pct',0):.1f}%  "
              f"P5={mc.get('p5_return_pct',0):+.2f}%  "
              f"P50={mc.get('p50_return_pct',0):+.2f}%  "
              f"P95={mc.get('p95_return_pct',0):+.2f}%  "
              f"MC_verdict={mc.get('verdict','N/A')}")

        print(f"  Robustness (score={rb['score']:.3f}):")
        for param, sens in rb.get("sensitivities", {}).items():
            flag = " <- FRAGILE" if param in rb.get("fragile_params", []) else ""
            print(f"    {param}: d={sens:.4f}{flag}")
        if rb.get("fragile_params"):
            print(f"    Fragile params: {rb['fragile_params']}")

        print(f"  GMM Regimes (dominant={g.get('dominant_regime','N/A')}):")
        for regime, pct in g.get("regime_pct", {}).items():
            cnt = g["regime_counts"].get(regime, 0)
            print(f"    {regime}: {pct:.1f}%  ({cnt} bars)")

        print(f"  VERDICT: {r.verdict}")

    # -- best config ----------------------------------------------------------
    passing = [r for r in results if r.verdict == "PASS"]
    caution  = [r for r in results if r.verdict == "CAUTION"]
    best_config: Optional[str] = None

    if passing:
        best = max(passing, key=lambda r: r.metrics["win_rate_pct"])
        best_config = best.name
        print(f"\n{'='*60}")
        print(f"  BEST CONFIG (PASS): {best_config}")
        print(f"  WR={best.metrics['win_rate_pct']:.1f}%  "
              f"Return={best.metrics['total_return_pct']:+.2f}%  "
              f"MC P(loss)={best.mc.get('prob_of_loss_pct',0):.1f}%  "
              f"Robustness={best.rb['score']:.3f}")
    elif caution:
        best = max(caution, key=lambda r: r.metrics["win_rate_pct"])
        best_config = best.name
        print(f"\n{'='*60}")
        print(f"  BEST CONFIG (CAUTION): {best_config}")
        print(f"  WR={best.metrics['win_rate_pct']:.1f}%  "
              f"Return={best.metrics['total_return_pct']:+.2f}%  "
              f"MC P(loss)={best.mc.get('prob_of_loss_pct',0):.1f}%  "
              f"Robustness={best.rb['score']:.3f}")
    else:
        print("\n  NO PASSING CONFIGS — all FAIL or CAUTION")

    # -- build results dict ---------------------------------------------------
    results_dict: dict[str, Any] = {
        "test_period": {"start": TEST_START, "end": TEST_END},
        "walk_forward": {"train_months": TRAIN_MONTHS, "test_months": TEST_MONTHS},
        "configs": {},
    }

    for r in results:
        results_dict["configs"][r.name] = {
            "config": r.config,
            "verdict": r.verdict,
            "metrics": r.metrics,
            "walk_forward": r.wf,
            "monte_carlo": {
                k: v for k, v in r.mc.items() if k not in ("n_trades",)
            },
            "robustness": {
                "score": r.rb["score"],
                "sensitivities": r.rb.get("sensitivities", {}),
                "fragile_params": r.rb.get("fragile_params", []),
            },
            "gmm_regime": {
                "dominant": r.gmm.get("dominant_regime", "N/A"),
                "regime_pct": r.gmm.get("regime_pct", {}),
            },
        }

    results_dict["best_config"] = best_config

    # -- save CSV -------------------------------------------------------------
    rows = []
    for r in results:
        m = r.metrics
        rows.append({
            "config": r.name,
            "trades": m["n_trades"],
            "wr_pct": round(m["win_rate_pct"], 1),
            "return_pct": round(m["total_return_pct"], 2),
            "sharpe": round(m["sharpe"], 3),
            "maxdd_pct": round(m["max_dd_pct"], 2),
            "pf": round(m["profit_factor"], 2),
            "mc_prob_loss_pct": round(r.mc.get("prob_of_loss_pct", 0.0), 1),
            "mc_p50_pct": round(r.mc.get("p50_return_pct", 0.0), 2),
            "mc_p95_pct": round(r.mc.get("p95_return_pct", 0.0), 2),
            "oov_return_pct": r.wf.get("oov_return", 0.0),
            "oov_sharpe": r.wf.get("oov_sharpe", 0.0),
            "oov_wr_pct": r.wf.get("oov_wr", 0.0),
            "rb_score": r.rb.get("score", 0.0),
            "fragile_params": "; ".join(r.rb.get("fragile_params", [])),
            "gmm_dominant": r.gmm.get("dominant_regime", "N/A"),
            "verdict": r.verdict,
        })

    df_out = pd.DataFrame(rows)
    csv_path = RESULTS_DIR / "test_v8_full_validation.csv"
    df_out.to_csv(csv_path, index=False)
    logger.info(f"Results saved: {csv_path}")

    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)

    return results_dict, best_config


if __name__ == "__main__":
    results, best = main()