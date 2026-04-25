"""
agent.py — .env Parameter Optimization Agent
=============================================
Reads current strategy parameters from the trading bot's .env file,
runs a coordinate-descent parameter sweep using the walk-forward engine,
proposes improvements as a diff table, and can write changes back to .env.

Coordinate descent: vary one parameter at a time, hold others at the best value
found so far.  This is O(5 × n_params × n_folds) — tractable in ~30-90 seconds
depending on strategy and date range.

All strategy logic is imported from strategies.py (no Streamlit imports here).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from dotenv import dotenv_values, set_key

from strategies import (
    STRATEGY_REGISTRY,
    build_windows,
    compute_metrics,
    optimize_strategy,
    stitch_oos_equity,
)

# ─────────────────────────────────────────────────────────────────────────────
# ENV FILE LOCATION
# Uses `git rev-parse --show-toplevel` so it resolves correctly whether the
# app is run from the main working tree or from a git worktree.
# Falls back to two levels up from this file if git is unavailable.
# ─────────────────────────────────────────────────────────────────────────────
def _find_env_path() -> Path:
    """
    Walk up from BTV2/ to find the trading bot's .env file.
    Works from both the main repo (Bot3/BTV2/) and git worktrees.
    Identifies the correct .env by the presence of AGENT_WALLET_PRIVATE_KEY.
    """
    candidate = Path(__file__).resolve().parent   # BTV2/
    for _ in range(8):
        candidate = candidate.parent
        env_file  = candidate / ".env"
        if env_file.exists():
            try:
                content = env_file.read_text(encoding="utf-8", errors="ignore")
                if "AGENT_WALLET_PRIVATE_KEY" in content:
                    return env_file
            except Exception:
                pass
    return Path(__file__).resolve().parent.parent / ".env"   # last resort

ENV_PATH = _find_env_path()


# ─────────────────────────────────────────────────────────────────────────────
# MAPPING: strategy name → .env key → (internal_kwarg, type_cast)
# ─────────────────────────────────────────────────────────────────────────────
STRATEGY_ENV_KEYS: dict[str, dict[str, tuple[str, type]]] = {
    "Mean Reversion": {
        "MEAN_REVERSION_RSI_OVERSOLD":       ("rsi_oversold",  float),
        "MEAN_REVERSION_RSI_OVERBOUGHT":     ("rsi_overbought",float),
        "MEAN_REVERSION_BB_PROXIMITY":       ("bb_proximity",  float),
        "MEAN_REVERSION_ATR_STOP_MULTIPLIER":("atr_stop",      float),
    },
    "VWAP Scalping": {
        "VWAP_SD_ENTRY_THRESHOLD":   ("sd_threshold", float),
        "VWAP_ATR_STOP_MULTIPLIER":  ("atr_stop",     float),
    },
    "Momentum Scalping": {
        "MOMENTUM_EMA_FAST":             ("ema_fast",   int),
        "MOMENTUM_EMA_SLOW":             ("ema_slow",   int),
        "MOMENTUM_ATR_STOP_MULTIPLIER":  ("atr_stop",  float),
        "MOMENTUM_ATR_TARGET_MULTIPLIER":("atr_target", float),
    },
    "Liquidation Capture": {
        "LIQUIDATION_PRICE_THRESHOLD":  ("price_threshold", float),
        "LIQUIDATION_VOLUME_MULTIPLIER":("volume_mult",     float),
        "LIQUIDATION_RSI_OVERSOLD":     ("rsi_threshold",   float),
    },
    "Grid Trading": {
        "GRID_ADX_THRESHOLD":          ("adx_threshold", float),
        "GRID_SPACING_ATR_MULTIPLIER": ("spacing_mult",  float),
    },
    "MA Crossover": {
        "MA_CROSSOVER_FAST_PERIOD":   ("fast_period",  int),
        "MA_CROSSOVER_SLOW_PERIOD":   ("slow_period",  int),
        "MA_CROSSOVER_PULLBACK_MAX":  ("pullback_max", float),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# READ .env
# ─────────────────────────────────────────────────────────────────────────────
def read_env_params(strategy_name: str) -> dict[str, Any]:
    """
    Read current parameter values from .env for the given strategy.
    Falls back to DEFAULT_PARAMS from STRATEGY_REGISTRY if .env key is missing.
    Returns dict keyed by internal kwarg name (e.g. 'rsi_oversold').
    """
    _, _, defaults = STRATEGY_REGISTRY[strategy_name]
    mapping        = STRATEGY_ENV_KEYS.get(strategy_name, {})

    if not ENV_PATH.exists():
        return defaults.copy()

    env_vals = dotenv_values(ENV_PATH)
    result   = defaults.copy()

    for env_key, (kwarg, cast) in mapping.items():
        raw = env_vals.get(env_key, "").strip()
        # Strip inline comments (e.g. "30  # Reset to original")
        raw = raw.split("#")[0].strip()
        if raw:
            try:
                result[kwarg] = cast(raw)
            except (ValueError, TypeError):
                pass

    return result


def read_env_display(strategy_name: str) -> dict[str, str]:
    """
    Return a display dict: {env_key: current_raw_value} for UI inspection.
    """
    mapping  = STRATEGY_ENV_KEYS.get(strategy_name, {})
    if not ENV_PATH.exists():
        return {"(env file not found)": str(ENV_PATH)}

    env_vals = dotenv_values(ENV_PATH)
    return {
        env_key: env_vals.get(env_key, "(not set)")
        for env_key in mapping
    }


# ─────────────────────────────────────────────────────────────────────────────
# COORDINATE DESCENT SWEEP
# ─────────────────────────────────────────────────────────────────────────────
def run_parameter_sweep(
    df: pd.DataFrame,
    cutoff: float,
    strategy_name: str,
    current_params: dict[str, Any],
    windows: list[dict],
) -> tuple[dict[str, Any], float, list[dict]]:
    """
    Coordinate descent parameter optimisation.

    For each parameter in STRATEGY_ENV_KEYS, try 5 candidate values
    centred on the current value (×0.7, ×0.85, ×1.0, ×1.15, ×1.30).
    Update the best value before moving to the next parameter.

    Returns
    -------
    best_params   : dict of best-found kwarg values
    best_sharpe   : OOS Sharpe of best config
    trial_log     : list of {param, value, oos_sharpe, oos_return, oos_maxdd}
    """
    func, grid, defaults = STRATEGY_REGISTRY[strategy_name]
    mapping = STRATEGY_ENV_KEYS.get(strategy_name, {})
    kwarg_names = [v[0] for v in mapping.values()]

    # Work with internal kwarg names only
    best_params  = current_params.copy()
    best_sharpe  = _eval_config(df, cutoff, strategy_name, best_params, windows)
    trial_log: list[dict] = []

    MULTIPLIERS = [0.70, 0.85, 1.00, 1.15, 1.30]

    for kwarg in kwarg_names:
        if kwarg not in best_params:
            continue
        base_val = best_params[kwarg]
        local_best_val    = base_val
        local_best_sharpe = best_sharpe

        for mult in MULTIPLIERS:
            candidate = base_val * mult
            # Enforce integer types
            cast = next((v[1] for v in mapping.values() if v[0] == kwarg), float)
            if cast == int:
                candidate = max(1, round(candidate))
            else:
                candidate = round(candidate, 4)

            trial_params = {**best_params, kwarg: candidate}
            sharpe = _eval_config(df, cutoff, strategy_name, trial_params, windows)

            oos_eq, oos_trd = _run_oos(df, cutoff, strategy_name, trial_params, windows)
            m = compute_metrics(oos_eq, oos_trd)

            trial_log.append({
                "Parameter": kwarg,
                "Candidate": candidate,
                "OOS Sharpe": round(sharpe, 3),
                "OOS Return %": round(m["total_return_pct"], 2),
                "OOS Max DD %": round(m["max_dd_pct"], 2),
            })

            if sharpe > local_best_sharpe:
                local_best_sharpe = sharpe
                local_best_val    = candidate

        best_params[kwarg]   = local_best_val
        best_sharpe          = local_best_sharpe

    return best_params, best_sharpe, trial_log


def _run_oos(
    df: pd.DataFrame,
    cutoff: float,
    strategy_name: str,
    params: dict[str, Any],
    windows: list[dict],
) -> tuple:
    """Run full walk-forward OOS with fixed params (no per-fold optimisation)."""
    func, _, _ = STRATEGY_REGISTRY[strategy_name]
    segments: list = []
    all_trades: list[float] = []

    for w in windows:
        df_test = df.loc[str(w["test_start"]) : str(w["test_end"])]
        if len(df_test) < 10:
            continue
        try:
            eq, trd = func(df_test, cutoff, **params)
            segments.append(eq)
            all_trades.extend(trd)
        except Exception:
            pass

    return stitch_oos_equity(segments), all_trades


def _eval_config(
    df: pd.DataFrame,
    cutoff: float,
    strategy_name: str,
    params: dict[str, Any],
    windows: list[dict],
) -> float:
    """Run OOS with fixed params, return OOS Sharpe (used by sweep)."""
    eq, trd = _run_oos(df, cutoff, strategy_name, params, windows)
    if eq.empty or len(trd) < 3:
        return -math.inf
    return compute_metrics(eq, trd)["sharpe"]


# ─────────────────────────────────────────────────────────────────────────────
# PROPOSAL DIFF
# ─────────────────────────────────────────────────────────────────────────────
def build_proposal(
    strategy_name: str,
    current_params: dict[str, Any],
    best_params:    dict[str, Any],
) -> list[dict]:
    """
    Compare current vs best params. Return list of changed entries with
    env_var_name, old_value, new_value, and pct_change.
    """
    mapping  = STRATEGY_ENV_KEYS.get(strategy_name, {})
    kwarg_to_env = {v[0]: k for k, v in mapping.items()}

    proposal = []
    for kwarg, new_val in best_params.items():
        old_val = current_params.get(kwarg, new_val)
        if abs(float(new_val) - float(old_val)) < 1e-9:
            continue
        env_key = kwarg_to_env.get(kwarg, kwarg.upper())
        pct_chg = (float(new_val) - float(old_val)) / (abs(float(old_val)) + 1e-10) * 100.0
        proposal.append({
            "Parameter":     kwarg,
            "ENV Variable":  env_key,
            "Current Value": old_val,
            "Proposed Value":new_val,
            "Change":        f"{pct_chg:+.1f}%",
        })

    return proposal


# ─────────────────────────────────────────────────────────────────────────────
# WRITE TO .env
# ─────────────────────────────────────────────────────────────────────────────
def apply_to_env(proposal: list[dict]) -> list[str]:
    """
    Write proposed parameter changes to the .env file using python-dotenv set_key.
    Returns list of env variables updated.
    """
    if not ENV_PATH.exists():
        raise FileNotFoundError(f".env not found at {ENV_PATH}")

    updated = []
    for change in proposal:
        env_key  = change["ENV Variable"]
        new_val  = str(change["Proposed Value"])
        set_key(str(ENV_PATH), env_key, new_val)
        updated.append(env_key)

    return updated
