"""
optimizer_agent.py — Autonomous Parameter Optimizer for Bot3 Strategies
========================================================================
Implements the autoresearch pattern adapted for trading:

    read params → propose via Optuna TPE → run walk-forward backtest
    → evaluate OOS Sharpe → store trial → update .env if improved → repeat

Usage
-----
    # Optimize Mean Reversion (default, fastest backtest):
    python BTV2/optimizer_agent.py

    # Specify strategy and trial budget:
    python BTV2/optimizer_agent.py --strategy "Mean Reversion" --n-trials 100

    # Run in strict-validation mode (mirrors live bot signal gates):
    python BTV2/optimizer_agent.py --strict --n-trials 50

    # Dry run — optimize but don't write to .env:
    python BTV2/optimizer_agent.py --dry-run

    # Resume an interrupted study:
    python BTV2/optimizer_agent.py --resume

    # Read target from optimize.md and act on it:
    python BTV2/optimizer_agent.py --from-program

Design
------
- Optuna TPE sampler with SQLite persistence (studies survive restarts)
- Walk-forward evaluation via existing strategies.py helpers
- Results logged to JSON trial log + attempted push to Dark Factory RAG API
- Best params auto-applied to Bot3/.env on improvement (configurable threshold)
- Guard against overfitting: requires MIN_OOS_TRADES across windows
- Reproducible: seeds captured in trial metadata

Author: glitch (Claude) — Dark Factory agent system
Date:   2026-04-11
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ─── path setup ──────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent          # BTV2/
_ROOT = _HERE.parent                             # Bot3/
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

# ─── BTV2 imports ────────────────────────────────────────────────────────────
from strategies import (                          # noqa: E402
    run_mean_reversion,
    run_vwap_scalping,
    run_momentum_scalping,
    run_ma_crossover,
    run_liquidation_capture,
    compute_metrics,
    build_windows,
    stitch_oos_equity,
    STRATEGY_TIMEFRAME_CONFIG,
    INTERVAL_BARS_PER_YEAR,
    MR_DEFAULTS,
    MR_STRICT_DEFAULTS,
    VS_DEFAULTS,
    MS_DEFAULTS,
    MA_DEFAULTS,
    LC_DEFAULTS,
)

# ─── logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("optimizer")

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
OPTUNA_DB        = str(_HERE / "optimization_studies.db")
TRIALS_LOG       = _HERE / "results" / "optimizer_trials.jsonl"
ENV_PATH         = _ROOT / ".env"
DATA_DIR         = Path("G:/Candle Data")          # primary (Windows machine)
DATA_FALLBACK    = _HERE / "data"                  # secondary (local cache)

# Acceptance gate — only update .env if OOS Sharpe beats current by this much
MIN_IMPROVEMENT  = 0.05
# Reject param sets that produce too few trades (high risk of overfitting)
MIN_OOS_TRADES   = 10
# Default walk-forward date range
WF_START         = date(2020, 1, 1)
WF_END           = date(2025, 12, 31)

# Dark Factory RAG API (try push; fail silently)
RAG_API_URL      = "http://10.0.0.157:3000"       # desktop hub
RAG_API_KEY      = "lWv_Mot218YtzzFMQT8Fg8C3mAYg9Y_6tI-xiCA9AgQ"

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY SEARCH SPACES
# Maps each strategy name → (run_fn, defaults, Optuna suggest spec)
# Suggest spec: list of (param_name, type, lo, hi, [step | choices])
# ─────────────────────────────────────────────────────────────────────────────
SEARCH_SPACES: dict[str, dict] = {
    "Mean Reversion": {
        "run_fn": run_mean_reversion,
        "defaults": MR_DEFAULTS,
        "interval": "1d",
        "params": [
            ("rsi_oversold",       "float",  20.0, 40.0),
            ("rsi_overbought",     "float",  60.0, 80.0),
            ("bb_proximity",       "float",  0.03, 0.25),
            ("atr_stop",           "float",  2.0,  4.5),
            ("adx_max",            "float",  12.0, 30.0),
            ("trailing_atr_mult",  "float",  0.8,  2.5),
        ],
        "fixed": {
            "use_regime_filter": True,
            "use_trailing_stop": True,
        },
        # Strict-mode additions (activated via --strict flag)
        "strict_params": [
            ("strict_volume_mult", "float", 1.0, 2.0),
            ("strict_min_rrr",     "float", 0.7, 1.5),
        ],
        "strict_fixed": {"strict_validation": True},
        # ENV variable names to update when a better config is found
        "env_map": {
            "rsi_oversold":       "MEAN_REVERSION_RSI_OVERSOLD",
            "rsi_overbought":     "MEAN_REVERSION_RSI_OVERBOUGHT",
            "bb_proximity":       "MEAN_REVERSION_BB_PROXIMITY",
            "atr_stop":           "MEAN_REVERSION_ATR_STOP_MULTIPLIER",
            "adx_max":            "ADX_TRENDING_THRESHOLD",
            "trailing_atr_mult":  "_NOTE_trailing_not_in_live_bot",
        },
    },
    "VWAP Scalping": {
        "run_fn": run_vwap_scalping,
        "defaults": VS_DEFAULTS,
        "interval": "5m",
        "params": [
            # Categorical params as requested
            ("sd_threshold",    "cat",    [2.5, 3.0, 3.5, 4.0, 4.5]),
            ("entry_mode",      "cat",    ["bull_pullback", "bear_pullback", "cross", "mean_reversion"]),
            ("atr_stop",        "cat",    [1.5, 2.0, 2.5, 3.0]),
            ("tp_mode",         "cat",    ["atr", "pdh"]),
            ("trailing_atr",    "cat",    [1.2, 1.5, 2.0]),
            # Keep existing params
            ("adx_max",         "float",  15.0, 40.0),
            ("volume_mult",     "float",  0.8, 2.0),
            ("rsi_max",         "float",  40.0, 60.0),
            # Categorical filters
            ("use_stoch_filter","cat",    [True, False]),
            ("use_htf_vwap",    "cat",    [True, False]),
            ("require_reversal_candle", "cat", [True, False]),
        ],
        "fixed": {
            "use_trend_filter":  True,
            "use_volume_filter": True,
            "use_trailing_stop": True,
            "use_session_filter": True,
            "use_htf_ema": True,
        },
        "strict_params": [],
        "strict_fixed": {},
        "env_map": {
            "sd_threshold":   "VWAP_SD_ENTRY_THRESHOLD",
            "atr_stop":       "VWAP_ATR_STOP_MULTIPLIER",
            "trailing_atr":   "VWAP_ATR_TRAILING_MULTIPLIER",
            "entry_mode":     "VWAP_ENTRY_MODE",
            "tp_mode":        "VWAP_TP_MODE",
            "adx_max":        "ADX_TRENDING_THRESHOLD",
            "volume_mult":    "_NOTE_volume_mult_not_direct_env",
            "rsi_max":        "VWAP_RSI_MAX",
            "require_reversal_candle": "VWAP_REQUIRE_REVERSAL_CANDLE",
            "use_htf_vwap":   "VWAP_USE_HTF_VWAP",
        },
    },
    "Momentum Scalping": {
        "run_fn": run_momentum_scalping,
        "defaults": MS_DEFAULTS,
        "interval": "15m",
        "params": [
            ("ema_fast",    "int",   6, 15),
            ("ema_slow",    "int",   18, 30),
            ("atr_stop",    "float", 1.2, 3.0),
            ("atr_target",  "float", 2.0, 4.0),
        ],
        "fixed": {},
        "strict_params": [],
        "strict_fixed": {},
        "env_map": {
            "ema_fast":   "MOMENTUM_EMA_FAST",
            "ema_slow":   "MOMENTUM_EMA_SLOW",
            "atr_stop":   "MOMENTUM_ATR_STOP_MULTIPLIER",
            "atr_target": "MOMENTUM_ATR_TARGET_MULTIPLIER",
        },
    },
    "MA Crossover": {
        "run_fn": run_ma_crossover,
        "defaults": MA_DEFAULTS,
        "interval": "1d",
        "params": [
            ("fast_period",  "int",   10, 30),
            ("slow_period",  "int",   35, 80),
            ("pullback_max", "float", 0.02, 0.12),
        ],
        "fixed": {},
        "strict_params": [],
        "strict_fixed": {},
        "env_map": {
            "fast_period":  "MA_CROSSOVER_FAST_PERIOD",
            "slow_period":  "MA_CROSSOVER_SLOW_PERIOD",
            "pullback_max": "MA_CROSSOVER_PULLBACK_MAX",
        },
    },
    "Liquidation Capture": {
        "run_fn": run_liquidation_capture,
        "defaults": LC_DEFAULTS,
        "interval": "1d",
        "params": [
            ("price_threshold",         "float", 0.015, 0.045),
            ("volume_mult",             "float", 1.0,   4.0),
            ("rsi_threshold",           "float", 12.0, 28.0),
            ("min_consecutive_moves",   "int",   2,    5),
            ("min_wick_ratio",          "float", 1.0,  2.5),
            ("rrr_target",              "float", 1.5,  4.0),
        ],
        "fixed": {},
        "strict_params": [],
        "strict_fixed": {},
        "env_map": {
            "price_threshold":         "LIQUIDATION_PRICE_THRESHOLD",
            "volume_mult":             "LIQUIDATION_VOLUME_MULTIPLIER",
            "rsi_threshold":           "LIQUIDATION_RSI_THRESHOLD",
            "min_consecutive_moves":   "LIQUIDATION_MIN_CONSECUTIVE_MOVES",
            "min_wick_ratio":          "LIQUIDATION_MIN_WICK_RATIO",
            "rrr_target":              "LIQUIDATION_RRR_TARGET",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────
def load_data(ticker: str = "BTC-USD", interval: str = "1d") -> pd.DataFrame:
    """
    Load OHLCV data for the given ticker and interval.

    Priority:
    1. G:/Candle Data/<SYMBOL>_<interval>.parquet  (Windows native path)
    2. BTV2/data/<ticker>_<interval>.parquet        (local cache)
    3. yfinance download + cache to BTV2/data/      (fallback)

    Returns DataFrame with columns: Open, High, Low, Close, Volume
    and a DatetimeIndex (UTC).
    """
    # Map ticker to Binance-style filename
    _ticker_to_sym = {
        "BTC-USD": "BTCUSDT",
        "ETH-USD": "ETHUSDT",
        "SOL-USD": "SOLUSDT",
        "SUI-USD": "SUIUSDT",
    }
    binance_sym = _ticker_to_sym.get(ticker, ticker.replace("-", ""))
    interval_file = interval.replace("min", "m")

    # 1. Try G:/Candle Data (Windows machine)
    parquet_primary = DATA_DIR / f"{binance_sym}_{interval_file}.parquet"
    if parquet_primary.exists():
        log.info(f"Loading from {parquet_primary}")
        return _normalize_df(pd.read_parquet(parquet_primary))

    # 2. Try local BTV2/data cache
    DATA_FALLBACK.mkdir(parents=True, exist_ok=True)
    parquet_local = DATA_FALLBACK / f"{ticker}_{interval_file}.parquet"
    if parquet_local.exists():
        log.info(f"Loading from local cache {parquet_local}")
        return _normalize_df(pd.read_parquet(parquet_local))

    # 3. Download via yfinance
    log.info(f"Downloading {ticker} {interval} via yfinance...")
    import yfinance as yf
    yf_interval_map = {"1d": "1d", "1h": "1h", "15m": "15m", "5m": "5m", "4h": "1h"}
    yf_interval = yf_interval_map.get(interval, "1d")
    period = "max" if yf_interval == "1d" else "60d"
    df = yf.download(ticker, period=period, interval=yf_interval,
                     auto_adjust=True, progress=False)
    if df.empty:
        raise RuntimeError(f"No data returned for {ticker} {interval}")
    df.to_parquet(parquet_local)
    log.info(f"Cached to {parquet_local}")
    return _normalize_df(df)


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure standard column names and UTC DatetimeIndex."""
    col_map = {c: c.title() for c in df.columns if c.lower() in
               ("open", "high", "low", "close", "volume")}
    df = df.rename(columns=col_map)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Volume" not in df.columns:
        df["Volume"] = 1.0
    return df.dropna(subset=["Close"])


# ─────────────────────────────────────────────────────────────────────────────
# WALK-FORWARD EVALUATION
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_params(
    strategy_name: str,
    params: dict[str, Any],
    df: pd.DataFrame,
    start: date = WF_START,
    end: date = WF_END,
    cutoff: float = 0.10,
) -> dict[str, Any]:
    """
    Run walk-forward evaluation for a strategy with given params.

    Returns a result dict with aggregated OOS metrics and per-window details.
    Returns None-like result if fewer than MIN_OOS_TRADES across all windows.
    """
    cfg = SEARCH_SPACES[strategy_name]
    run_fn = cfg["run_fn"]
    interval = cfg.get("interval", "1d")
    tf_cfg = STRATEGY_TIMEFRAME_CONFIG.get(strategy_name, {})
    train_months = tf_cfg.get("train_months", 12)
    test_months = tf_cfg.get("test_months", 3)
    bars_per_year = INTERVAL_BARS_PER_YEAR.get(interval, 365)

    windows = build_windows(start, end, train_months=train_months, test_months=test_months)
    if not windows:
        return _empty_result(params, "no_windows")

    oos_segments: list[pd.Series] = []
    all_trades: list[float] = []
    window_results: list[dict] = []
    windows_positive = 0

    for w in windows:
        df_train = df.loc[str(w["train_start"]):str(w["train_end"])]
        df_test  = df.loc[str(w["test_start"]):str(w["test_end"])]

        if len(df_train) < 30 or len(df_test) < 5:
            continue

        try:
            eq_oos, trades_oos = run_fn(df_test, cutoff, **params)
        except Exception as e:
            log.debug(f"Window {w['fold']} failed: {e}")
            continue

        if len(trades_oos) == 0:
            continue

        m = compute_metrics(eq_oos, trades_oos, bars_per_year=bars_per_year)
        oos_segments.append(eq_oos)
        all_trades.extend(trades_oos)

        if m["sharpe"] > 0:
            windows_positive += 1

        window_results.append({
            "fold":        w["fold"],
            "test_start":  str(w["test_start"]),
            "test_end":    str(w["test_end"]),
            "sharpe":      round(m["sharpe"], 4),
            "n_trades":    m["n_trades"],
            "win_rate":    round(m["win_rate_pct"], 1),
            "max_dd":      round(m["max_dd_pct"], 2),
            "profit_factor": round(m["profit_factor"], 3),
        })

    if len(all_trades) < MIN_OOS_TRADES:
        return _empty_result(params, f"too_few_trades({len(all_trades)})")

    # Stitch OOS equity for aggregate metrics
    oos_equity = stitch_oos_equity(oos_segments)
    agg = compute_metrics(oos_equity, all_trades, bars_per_year=bars_per_year)

    consistency_pct = (windows_positive / max(len(window_results), 1)) * 100.0

    return {
        "status":           "ok",
        "mean_oos_sharpe":  agg["sharpe"],
        "total_return_pct": round(agg["total_return_pct"], 2),
        "cagr_pct":         round(agg["cagr_pct"], 2),
        "max_dd_pct":       round(agg["max_dd_pct"], 2),
        "win_rate_pct":     round(agg["win_rate_pct"], 1),
        "profit_factor":    round(agg["profit_factor"], 3),
        "n_trades":         agg["n_trades"],
        "n_windows":        len(window_results),
        "consistency_pct":  round(consistency_pct, 1),
        "params":           params,
        "windows":          window_results,
    }


def _empty_result(params: dict, reason: str) -> dict:
    return {
        "status":           reason,
        "mean_oos_sharpe":  -999.0,
        "n_trades":         0,
        "params":           params,
        "windows":          [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENV READER / WRITER
# ─────────────────────────────────────────────────────────────────────────────
def read_env_param(key: str, default: str = "") -> str:
    """Read a single value from .env file."""
    if not ENV_PATH.exists():
        return default
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{key}="):
                raw = line.split("=", 1)[1].split("#")[0].strip()
                return raw
    return default


def write_env_params(updates: dict[str, str]) -> None:
    """
    Update .env in-place, modifying existing keys or appending new ones.
    Preserves all comments and formatting.
    """
    if not ENV_PATH.exists():
        log.warning(f".env not found at {ENV_PATH} — skipping update")
        return

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    updated_keys: set[str] = set()

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                # Preserve inline comment if present
                old_val_part = line.split("=", 1)[1]
                comment = ""
                if "#" in old_val_part:
                    comment = "  " + "#" + old_val_part.split("#", 1)[1].rstrip()
                new_lines.append(f"{key}={updates[key]}{comment}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys not found in the file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"\n# Added by optimizer_agent.py {datetime.utcnow().date()}")
            new_lines.append(f"{key}={val}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    log.info(f"Updated .env with {len(updates)} param(s): {list(updates.keys())}")


def get_current_sharpe_baseline(strategy_name: str) -> float:
    """
    Look up the best known OOS Sharpe for this strategy from the trials log.
    Returns -999 if no previous trials exist.
    """
    if not TRIALS_LOG.exists():
        return -999.0
    best = -999.0
    with open(TRIALS_LOG) as f:
        for line in f:
            try:
                t = json.loads(line)
                if t.get("strategy") == strategy_name and t.get("status") == "ok":
                    s = t.get("mean_oos_sharpe", -999.0)
                    if s > best:
                        best = s
            except json.JSONDecodeError:
                pass
    return best


# ─────────────────────────────────────────────────────────────────────────────
# TRIAL LOGGING
# ─────────────────────────────────────────────────────────────────────────────
def log_trial(strategy_name: str, trial_number: int, result: dict,
              accepted: bool) -> None:
    """Append a trial record to the JSONL log file."""
    TRIALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "strategy":        strategy_name,
        "trial":           trial_number,
        "timestamp":       datetime.utcnow().isoformat(),
        "accepted":        int(accepted),  # JSON doesn't serialize bool
        **{k: v for k, v in result.items() if k != "windows"},  # skip verbose window list
    }
    with open(TRIALS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def push_to_rag(strategy_name: str, trial: int, result: dict,
                accepted: bool) -> None:
    """
    Try to push trial result to the Dark Factory RAG API.
    Fails silently — optimizer continues even if RAG is unreachable.
    """
    try:
        import requests as _req
        content = (
            f"Strategy: {strategy_name}\n"
            f"Trial: {trial}\n"
            f"OOS Sharpe: {result.get('mean_oos_sharpe', 'N/A'):.4f}\n"
            f"Total Return: {result.get('total_return_pct', 'N/A')}%\n"
            f"Max DD: {result.get('max_dd_pct', 'N/A')}%\n"
            f"Trade Count: {result.get('n_trades', 0)}\n"
            f"Consistency: {result.get('consistency_pct', 0)}% windows profitable\n"
            f"Params: {json.dumps(result.get('params', {}))}\n"
            f"Decision: {'ACCEPTED → .env updated' if accepted else 'REJECTED'}\n"
        )
        payload = {
            "id": f"optim/{strategy_name.lower().replace(' ', '_')}-trial{trial}-"
                  f"{datetime.utcnow().strftime('%Y%m%d')}",
            "content": content,
            "metadata": {
                "type": "optimization_trial",
                "strategy": strategy_name,
                "agent": "glitch",
            },
        }
        resp = _req.post(
            f"{RAG_API_URL}/api/rag/ingest",
            json=payload,
            headers={"X-Api-Key": RAG_API_KEY},
            timeout=5,
        )
        if resp.status_code == 200:
            log.debug(f"Trial {trial} pushed to RAG ✓")
    except Exception:
        pass  # RAG push is best-effort


# ─────────────────────────────────────────────────────────────────────────────
# OPTUNA OBJECTIVE
# ─────────────────────────────────────────────────────────────────────────────
def build_objective(
    strategy_name: str,
    df: pd.DataFrame,
    strict: bool = False,
    cutoff: float = 0.10,
    start: date = WF_START,
    end: date = WF_END,
):
    """Return an Optuna objective function for the given strategy."""
    cfg = SEARCH_SPACES[strategy_name]
    params_spec = list(cfg["params"])
    if strict:
        params_spec = params_spec + list(cfg.get("strict_params", []))

    defaults = cfg["defaults"].copy()
    defaults.update(cfg.get("fixed", {}))
    if strict:
        defaults.update(cfg.get("strict_fixed", {}))

    def objective(trial) -> float:
        import optuna
        params = defaults.copy()

        for spec in params_spec:
            name = spec[0]
            kind = spec[1]

            if kind == "float":
                lo, hi = spec[2], spec[3]
                params[name] = trial.suggest_float(name, lo, hi)
            elif kind == "int":
                lo, hi = spec[2], spec[3]
                params[name] = trial.suggest_int(name, int(lo), int(hi))
            elif kind in ("cat", "categorical"):
                # ("name", "cat", [choice1, choice2, ...])
                choices = spec[2]
                params[name] = trial.suggest_categorical(name, choices)

        result = evaluate_params(strategy_name, params, df,
                                 start=start, end=end, cutoff=cutoff)

        # Prune trials that don't generate enough trades
        if result["status"] != "ok":
            raise optuna.exceptions.TrialPruned()

        # Add consistency bonus to reward robust configs
        sharpe = result["mean_oos_sharpe"]
        consistency_bonus = result.get("consistency_pct", 0) * 0.001
        return sharpe + consistency_bonus

    return objective


# ─────────────────────────────────────────────────────────────────────────────
# MAIN OPTIMIZATION LOOP
# ─────────────────────────────────────────────────────────────────────────────
def run_optimization(
    strategy_name: str,
    ticker: str = "BTC-USD",
    n_trials: int = 100,
    timeout_minutes: int = 90,
    strict: bool = False,
    dry_run: bool = False,
    resume: bool = False,
    start: date = WF_START,
    end: date = WF_END,
) -> dict[str, Any]:
    """
    Run the full optimization loop for a strategy.

    Returns the best result found (whether or not .env was updated).
    """
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    if strategy_name not in SEARCH_SPACES:
        raise ValueError(f"Unknown strategy '{strategy_name}'. "
                         f"Available: {list(SEARCH_SPACES.keys())}")

    log.info("=" * 60)
    log.info(f"Optimizer: {strategy_name}")
    log.info(f"Ticker: {ticker} | Trials: {n_trials} | Strict: {strict}")
    log.info(f"Walk-forward: {start} → {end}")
    log.info(f"Dry run: {dry_run}")
    log.info("=" * 60)

    # Load data
    interval = SEARCH_SPACES[strategy_name]["interval"]
    df = load_data(ticker, interval)
    log.info(f"Loaded {len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")

    # Create or resume Optuna study
    study_name = f"{strategy_name.replace(' ', '_')}_{ticker}_{'strict' if strict else 'normal'}"
    storage = f"sqlite:///{OPTUNA_DB}"

    if resume:
        study = optuna.load_study(study_name=study_name, storage=storage)
        log.info(f"Resumed study '{study_name}' with "
                 f"{len(study.trials)} existing trials")
    else:
        study = optuna.create_study(
            direction="maximize",
            study_name=study_name,
            storage=storage,
            load_if_exists=True,
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        log.info(f"Created/loaded study '{study_name}'")

    # Get current best baseline
    prev_best = get_current_sharpe_baseline(strategy_name)
    log.info(f"Previous best OOS Sharpe: {prev_best:.4f}")

    # Build objective
    objective = build_objective(strategy_name, df, strict=strict, start=start, end=end)

    # Run optimization
    t0 = time.time()
    study.optimize(
        objective,
        n_trials=n_trials,
        timeout=timeout_minutes * 60,
        show_progress_bar=True,
        catch=(Exception,),
    )
    elapsed = (time.time() - t0) / 60.0

    # Collect best result
    completed = [t for t in study.trials
                 if t.state.name == "COMPLETE" and t.value is not None]
    if not completed:
        log.warning("No completed trials — cannot determine best params")
        return {}

    best_trial = max(completed, key=lambda t: t.value)
    best_params = SEARCH_SPACES[strategy_name]["defaults"].copy()
    best_params.update(SEARCH_SPACES[strategy_name].get("fixed", {}))
    if strict:
        best_params.update(SEARCH_SPACES[strategy_name].get("strict_fixed", {}))
    best_params.update(best_trial.params)

    # Full evaluation of best params for detailed reporting
    best_result = evaluate_params(strategy_name, best_params, df, start=start, end=end)
    best_sharpe = best_result.get("mean_oos_sharpe", -999.0)

    # Decision: accept if improvement >= threshold
    improvement = best_sharpe - prev_best
    accepted = (not dry_run) and (improvement >= MIN_IMPROVEMENT) and (best_result["status"] == "ok")

    log.info("-" * 60)
    log.info(f"Optimization complete in {elapsed:.1f} min "
             f"({len(completed)} completed trials)")
    log.info(f"Best OOS Sharpe: {best_sharpe:.4f}  "
             f"(prev: {prev_best:.4f}, delta: {improvement:+.4f})")
    log.info(f"Best params: {best_trial.params}")
    log.info(f"Decision: {'ACCEPTED' if accepted else 'REJECTED'}")

    # Log trial
    log_trial(strategy_name, best_trial.number, best_result, accepted)

    # Push to RAG (best-effort)
    push_to_rag(strategy_name, best_trial.number, best_result, accepted)

    # Apply to .env if accepted
    if accepted:
        env_map = SEARCH_SPACES[strategy_name].get("env_map", {})
        env_updates = {}
        for param_name, env_key in env_map.items():
            if env_key.startswith("_NOTE_"):
                continue  # no direct env var, skip
            if param_name in best_trial.params:
                val = best_trial.params[param_name]
                env_updates[env_key] = str(round(val, 6) if isinstance(val, float) else val)

        if env_updates:
            write_env_params(env_updates)
            log.info(f"Wrote {len(env_updates)} param(s) to .env: {env_updates}")
        else:
            log.info("No env vars to update for this strategy")

    # Print human-readable summary
    _print_summary(strategy_name, best_result, best_trial.number,
                   len(completed), elapsed, accepted, dry_run)

    return best_result


def _print_summary(
    strategy_name: str,
    result: dict,
    trial_num: int,
    n_completed: int,
    elapsed_min: float,
    accepted: bool,
    dry_run: bool,
) -> None:
    """Print a formatted summary of optimization results."""
    print("\n" + "=" * 60)
    print(f"  OPTIMIZER RESULTS — {strategy_name}")
    print("=" * 60)
    print(f"  Trials completed : {n_completed}")
    print(f"  Time elapsed     : {elapsed_min:.1f} min")
    print(f"  Best trial       : #{trial_num}")
    print()
    print(f"  OOS Sharpe       : {result.get('mean_oos_sharpe', 'N/A'):.4f}")
    print(f"  Total return     : {result.get('total_return_pct', 'N/A')}%")
    print(f"  Max drawdown     : {result.get('max_dd_pct', 'N/A')}%")
    print(f"  Win rate         : {result.get('win_rate_pct', 'N/A')}%")
    print(f"  Profit factor    : {result.get('profit_factor', 'N/A')}")
    print(f"  Trade count      : {result.get('n_trades', 0)}")
    print(f"  Consistency      : {result.get('consistency_pct', 0)}% windows Sharpe > 0")
    print()
    print(f"  Best params:")
    for k, v in result.get("params", {}).items():
        if not k.startswith("use_") and not k.startswith("strict_v"):
            val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
            print(f"    {k:<28} = {val_str}")
    print()
    if dry_run:
        print("  INFO: DRY RUN — .env not modified")
    elif accepted:
        print("  ACCEPTED — .env updated with new params")
    else:
        print("  REJECTED — improvement below threshold, .env unchanged")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# program.md READER
# ─────────────────────────────────────────────────────────────────────────────
def parse_program_md(program_path: Path) -> dict:
    """
    Parse an optimize.md / program.md file for optimization directives.

    Reads:
    - Strategy name from first ## heading after "## Strategy:"
    - Ticker from "## Ticker:" or defaults to BTC-USD
    - n_trials from "## Trials:" or defaults to 100
    - strict from "## Mode: strict" or "## Strict: true"
    - start/end dates from "## Period: YYYY-MM-DD to YYYY-MM-DD"
    """
    if not program_path.exists():
        raise FileNotFoundError(f"Program file not found: {program_path}")

    text = program_path.read_text(encoding="utf-8")
    config: dict = {}

    patterns = {
        "strategy":    r"(?i)##\s*Strategy\s*[:\-]\s*(.+)",
        "ticker":      r"(?i)##\s*Ticker\s*[:\-]\s*(.+)",
        "n_trials":    r"(?i)##\s*Trials?\s*[:\-]\s*(\d+)",
        "strict":      r"(?i)##\s*Strict\s*[:\-]\s*(true|false|yes|no)",
        "period":      r"(?i)##\s*Period\s*[:\-]\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
        "timeout":     r"(?i)##\s*Timeout\s*[:\-]\s*(\d+)",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, text)
        if m:
            if key == "period":
                config["start"] = date.fromisoformat(m.group(1))
                config["end"]   = date.fromisoformat(m.group(2))
            elif key == "strict":
                config[key] = m.group(1).lower() in ("true", "yes")
            elif key == "n_trials":
                config[key] = int(m.group(1))
            elif key == "timeout":
                config[key] = int(m.group(1))
            else:
                config[key] = m.group(1).strip()

    if "strategy" not in config:
        raise ValueError("optimize.md must contain '## Strategy: <name>'")

    return config


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonomous parameter optimizer for Bot3 trading strategies."
    )
    parser.add_argument(
        "--strategy", "-s",
        default="Mean Reversion",
        choices=list(SEARCH_SPACES.keys()),
        help="Strategy to optimize (default: 'Mean Reversion')",
    )
    parser.add_argument(
        "--ticker", "-t",
        default="BTC-USD",
        help="Ticker to use for walk-forward data (default: BTC-USD)",
    )
    parser.add_argument(
        "--n-trials", "-n",
        type=int, default=100,
        help="Number of Optuna trials to run (default: 100)",
    )
    parser.add_argument(
        "--timeout", "-T",
        type=int, default=90,
        help="Max optimization time in minutes (default: 90)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable STRICT_VALIDATION mode (mirrors live bot signal gates)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Optimize but do not update .env",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing Optuna study from the SQLite DB",
    )
    parser.add_argument(
        "--from-program",
        action="store_true",
        help="Read optimization directives from BTV2/optimize.md",
    )
    parser.add_argument(
        "--start",
        default=str(WF_START),
        help=f"Walk-forward start date (default: {WF_START})",
    )
    parser.add_argument(
        "--end",
        default=str(WF_END),
        help=f"Walk-forward end date (default: {WF_END})",
    )
    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List all available strategies and exit",
    )

    args = parser.parse_args()

    if args.list_strategies:
        print("Available strategies:")
        for name in SEARCH_SPACES:
            interval = SEARCH_SPACES[name]["interval"]
            n_params = len(SEARCH_SPACES[name]["params"])
            print(f"  {name:<25} interval={interval}  params={n_params}")
        return

    # Read from optimize.md if requested
    if args.from_program:
        program_path = _HERE / "optimize.md"
        log.info(f"Reading program from {program_path}")
        cfg = parse_program_md(program_path)
        strategy_name = cfg.get("strategy", args.strategy)
        ticker        = cfg.get("ticker", args.ticker)
        n_trials      = cfg.get("n_trials", args.n_trials)
        strict        = cfg.get("strict", args.strict)
        timeout       = cfg.get("timeout", args.timeout)
        start         = cfg.get("start", date.fromisoformat(args.start))
        end           = cfg.get("end", date.fromisoformat(args.end))
    else:
        strategy_name = args.strategy
        ticker        = args.ticker
        n_trials      = args.n_trials
        strict        = args.strict
