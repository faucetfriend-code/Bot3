"""
tuner_v6_comprehensive_test.py
================================
V6 Comprehensive Tuner - Multi-Parameter Adaptation

V6 Adjusts:
1. SD threshold (selectivity)
2. ATR stop (risk)
3. Entry mode (direction - switches after loss streaks)
4. Position size (exposure)
5. Cooldown (frequency)

Test:
- 2023-2025
- Compare to V5 (SD only) and static

This Should Work Better Because:
- More variables = more ways to adapt
- Can switch entry modes after losses
- Can reduce position size after losses
- Can increase cooldown when WR is low
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

import numpy as np
import pandas as pd

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    COST_PER_SIDE,
    INTERVAL_BARS_PER_YEAR,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Test period: 2023-2025 (as requested)
TEST_START = "2023-01-01"
TEST_END = "2026-01-01"

# Butterworth cutoff
CUTOFF = 0.10

# V6 Tuner Base Parameters
V6_BASE_SD = 3.0  # Proven sweet spot
V6_BASE_ATR_STOP = 1.87  # Risk parameter
V6_BASE_ENTRY_MODE = "bull_pullback"
V6_BASE_POSITION_SIZE = 1.0  # 100% of base
V6_BASE_COOLDOWN = 0  # No cooldown

# V5 Tuner for comparison (only adapts SD)
V5_BASE_SD = 3.0

# ─────────────────────────────────────────────────────────────────────────────
# V6 ADAPTATION PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

# General
ADAPT_LOOKBACK = 30  # Number of trades to evaluate
ADAPT_MIN_TRADES = 20  # Minimum trades before adapting

# SD Adaptation (selectivity)
WR_LOW_SD = 40.0
WR_HIGH_SD = 55.0
SD_TIGHTEN = 0.20  # Add when WR is too low
SD_LOOSEN = 0.15   # Subtract when WR is too high
SD_MIN = 2.5
SD_MAX = 6.0

# ATR Stop Adaptation (risk)
ATR_TIGHTEN_THRESHOLD = 35.0  # If WR < 35%, tighten stop
ATR_LOOSEN_THRESHOLD = 60.0   # If WR > 60%, loosen stop
ATR_TIGHTEN = 0.15  # Subtract from ATR multiplier
ATR_LOOSEN = 0.10   # Add to ATR multiplier
ATR_STOP_MIN = 1.0
ATR_STOP_MAX = 3.0

# Entry Mode Switching (direction)
LOSS_STREAK_THRESHOLD = 5  # Switch after 5 consecutive losses
ENTRY_MODES = ["bull_pullback", "bear_rally", "breakout", "mean_reversion"]
ENTRY_STREAK_BULL = 3  # Consecutive wins in bull = stay bullish

# Position Size Adaptation
SIZE_REDUCE_THRESHOLD = 35.0  # Reduce if WR < 35%
SIZE_INCREASE_THRESHOLD = 55.0  # Increase if WR > 55%
SIZE_REDUCE_FACTOR = 0.75  # Multiply by this when reducing
SIZE_INCREASE_FACTOR = 1.25  # Multiply by this when increasing
SIZE_MIN = 0.25  # Never go below 25%
SIZE_MAX = 1.5   # Never go above 150%

# Cooldown Adaptation
COOLDOWN_INCREASE_THRESHOLD = 35.0  # Add cooldown if WR < 35%
COOLDOWN_DECREASE_THRESHOLD = 55.0  # Remove cooldown if WR > 55%
COOLDOWN_STEP = 3  # Bars to add/remove
COOLDOWN_MAX = 12  # Maximum cooldown bars

# ─────────────────────────────────────────────────────────────────────────────
# STATIC BASELINE PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

STATIC_PARAMS = {
    "sd_threshold": 3.0,
    "atr_stop": 1.87,
    "atr_target": 2.0,
    "trailing_atr": 1.93,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": False,
    "use_volume_filter": True,
    "volume_mult": 1.0,
    "use_trailing_stop": True,
    "adx_max": 30.0,
    "rsi_max": 55.0,
    "entry_mode": "bull_pullback",
    "pullback_bars": 3,
    "use_htf_vwap": False,
    "use_htf_ema": False,
    "htf_adx_max": 30.0,
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    "use_anchored_vwap": True,
    "use_session_filter": False,
    "require_reversal_candle": False,
    "tp_mode": "atr",
    "require_mss": False,
    "mss_timeout_bars": 6,
    "use_fvg_filter": False,
    "use_ob_filter": False,
    "use_eqhl_filter": False,
    "use_cvd_filter": False,
    "cvd_window": 20,
    "use_ote": False,
    "use_dynamic_mode": False,
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_5m_data(start: str, end: str) -> pd.DataFrame:
    """Load BTCUSDT 5m data."""
    path = DATA_DIR / "BTCUSDT_5m.parquet"
    if not path.exists():
        print(f"ERROR: No data found at {path}")
        return pd.DataFrame()
    
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


def load_1m_data(start: str, end: str) -> Optional[pd.DataFrame]:
    """Load BTCUSDT 1m data for exits."""
    path = DATA_DIR / "BTCUSDT_1m.parquet"
    if not path.exists():
        return None
    
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


# ─────────────────────────────────────────────────────────────────────────────
# METRICS COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics_for_result(
    equity: pd.Series,
    trades: list,
) -> Dict[str, float]:
    """Compute performance metrics from equity curve and trades."""
    if len(equity) < 2 or len(trades) == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
        }
    
    metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    
    return {
        "trades": metrics.get("n_trades", 0),
        "win_rate": metrics.get("win_rate_pct", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "net_pct": metrics.get("total_return_pct", 0.0),
        "sharpe": metrics.get("sharpe", 0.0),
        "max_drawdown_pct": metrics.get("max_dd_pct", 0.0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# V5 ADAPTATION LOGIC (for comparison)
# ─────────────────────────────────────────────────────────────────────────────

def compute_win_rate(trades: List[float]) -> float:
    """Compute win rate from list of P&L values."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t > 0)
    return wins / len(trades) * 100


def v5_adapt_sd(current_sd: float, recent_trades: List[float]) -> Tuple[float, str]:
    """V5-style SD adaptation."""
    if len(recent_trades) < ADAPT_LOOKBACK:
        return current_sd, "insufficient_trades"
    
    wr = compute_win_rate(recent_trades)
    
    if wr < WR_LOW_SD:
        new_sd = min(current_sd + SD_TIGHTEN, SD_MAX)
        reason = f"wr_low({wr:.1f}% < {WR_LOW_SD}%)"
        return new_sd, reason
    elif wr > WR_HIGH_SD:
        new_sd = max(current_sd - SD_LOOSEN, SD_MIN)
        reason = f"wr_high({wr:.1f}% > {WR_HIGH_SD}%)"
        return new_sd, reason
    else:
        return current_sd, "no_change"


# ─────────────────────────────────────────────────────────────────────────────
# V6 ADAPTATION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

class V6State:
    """Track V6 tuner state across adaptation cycles."""
    
    def __init__(self):
        self.sd = V6_BASE_SD
        self.atr_stop = V6_BASE_ATR_STOP
        self.entry_mode = V6_BASE_ENTRY_MODE
        self.position_size = V6_BASE_POSITION_SIZE
        self.cooldown = V6_BASE_COOLDOWN
        
        # Track consecutive losses for entry mode switching
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        
        # Adaptation history
        self.history = []
    
    def reset(self):
        """Reset to base values."""
        self.sd = V6_BASE_SD
        self.atr_stop = V6_BASE_ATR_STOP
        self.entry_mode = V6_BASE_ENTRY_MODE
        self.position_size = V6_BASE_POSITION_SIZE
        self.cooldown = V6_BASE_COOLDOWN
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.history = []
    
    def adapt(self, recent_trades: List[float]) -> Dict[str, Any]:
        """Run all V6 adaptations based on recent trade performance."""
        if len(recent_trades) < ADAPT_MIN_TRADES:
            return {"adaptations": [], "reason": "insufficient_trades"}
        
        wr = compute_win_rate(recent_trades)
        adaptations = []
        
        # 1. SD Adaptation (selectivity)
        if wr < WR_LOW_SD:
            old_sd = self.sd
            self.sd = min(self.sd + SD_TIGHTEN, SD_MAX)
            adaptations.append(f"sd: {old_sd:.2f}->{self.sd:.2f}")
        elif wr > WR_HIGH_SD:
            old_sd = self.sd
            self.sd = max(self.sd - SD_LOOSEN, SD_MIN)
            adaptations.append(f"sd: {old_sd:.2f}->{self.sd:.2f}")
        
        # 2. ATR Stop Adaptation (risk)
        if wr < ATR_TIGHTEN_THRESHOLD:
            old_atr = self.atr_stop
            self.atr_stop = max(self.atr_stop - ATR_TIGHTEN, ATR_STOP_MIN)
            adaptations.append(f"atr_stop: {old_atr:.2f}->{self.atr_stop:.2f}")
        elif wr > ATR_LOOSEN_THRESHOLD:
            old_atr = self.atr_stop
            self.atr_stop = min(self.atr_stop + ATR_LOOSEN, ATR_STOP_MAX)
            adaptations.append(f"atr_stop: {old_atr:.2f}->{self.atr_stop:.2f}")
        
        # 3. Entry Mode Switching (direction)
        if len(recent_trades) >= LOSS_STREAK_THRESHOLD:
            # Check last N trades for consecutive losses
            last_n = recent_trades[-LOSS_STREAK_THRESHOLD:]
            if all(t < 0 for t in last_n):
                old_mode = self.entry_mode
                # Switch to opposite entry mode
                if self.entry_mode == "bull_pullback":
                    self.entry_mode = "bear_rally"
                elif self.entry_mode == "bear_rally":
                    self.entry_mode = "bull_pullback"
                elif self.entry_mode == "breakout":
                    self.entry_mode = "mean_reversion"
                else:
                    self.entry_mode = "breakout"
                self.consecutive_losses = LOSS_STREAK_THRESHOLD
                self.consecutive_wins = 0
                adaptations.append(f"entry_mode: {old_mode}->{self.entry_mode} (loss_streak)")
            elif all(t > 0 for t in last_n):
                self.consecutive_wins = LOSS_STREAK_THRESHOLD
                self.consecutive_losses = 0
        
        # 4. Position Size Adaptation (exposure)
        if wr < SIZE_REDUCE_THRESHOLD:
            old_size = self.position_size
            self.position_size = max(self.position_size * SIZE_REDUCE_FACTOR, SIZE_MIN)
            adaptations.append(f"size: {old_size:.2f}->{self.position_size:.2f}")
        elif wr > SIZE_INCREASE_THRESHOLD:
            old_size = self.position_size
            self.position_size = min(self.position_size * SIZE_INCREASE_FACTOR, SIZE_MAX)
            adaptations.append(f"size: {old_size:.2f}->{self.position_size:.2f}")
        
        # 5. Cooldown Adaptation (frequency)
        if wr < COOLDOWN_INCREASE_THRESHOLD:
            old_cd = self.cooldown
            self.cooldown = min(self.cooldown + COOLDOWN_STEP, COOLDOWN_MAX)
            adaptations.append(f"cooldown: {old_cd}->{self.cooldown}")
        elif wr > COOLDOWN_DECREASE_THRESHOLD:
            old_cd = self.cooldown
            self.cooldown = max(self.cooldown - COOLDOWN_STEP, 0)
            adaptations.append(f"cooldown: {old_cd}->{self.cooldown}")
        
        self.history.append({
            "wr": wr,
            "sd": self.sd,
            "atr_stop": self.atr_stop,
            "entry_mode": self.entry_mode,
            "position_size": self.position_size,
            "cooldown": self.cooldown,
            "adaptations": adaptations.copy(),
        })
        
        return {
            "wr": wr,
            "adaptations": adaptations,
            "reason": "adapted" if adaptations else "no_change",
        }


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_vwap_with_params(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    sd_threshold: float,
    atr_stop: float,
    entry_mode: str,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run VWAP with specific parameters."""
    levels = compute_reference_levels(df)
    
    params = STATIC_PARAMS.copy()
    params.update({
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "entry_mode": entry_mode,
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": [
            MarketRegime.RANGING,
            MarketRegime.BEAR_WEAK,
            MarketRegime.BULL_WEAK,
            MarketRegime.BEAR_STRONG,
            MarketRegime.BULL_STRONG,
        ],
    })
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


def run_static_baseline(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run with STATIC params (no adaptation) - baseline."""
    return run_vwap_with_params(
        df, df_exit, regime_series,
        sd_threshold=STATIC_PARAMS["sd_threshold"],
        atr_stop=STATIC_PARAMS["atr_stop"],
        entry_mode=STATIC_PARAMS["entry_mode"],
    )


def run_v5_adaptive(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
) -> Tuple[pd.Series, list, Dict[str, float], List[Dict[str, Any]]]:
    """
    Run V5 adaptive strategy (SD only).
    """
    if len(df) < 1000:
        params = STATIC_PARAMS.copy()
        params["levels"] = compute_reference_levels(df)
        params["regime_series"] = regime_series
        params["sd_threshold"] = V5_BASE_SD
        equity, trades = run_vwap_scalping(df, CUTOFF, df_exit=df_exit, **params)
        metrics = compute_metrics_for_result(equity, trades)
        return equity, trades, metrics, []
    
    # Process in monthly segments
    adaptation_log = []
    current_sd = V5_BASE_SD
    all_equity = []
    all_trades = []
    
    # Remove tz for consistent comparison
    df_copy = df.copy()
    df_copy.index = df_copy.index.tz_localize(None)
    if df_exit is not None:
        df_exit_copy = df_exit.copy()
        df_exit_copy.index = df_exit_copy.index.tz_localize(None)
    else:
        df_exit_copy = None
    regime_copy = regime_series.copy()
    regime_copy.index = regime_copy.index.tz_localize(None)
    
    months = df_copy.index.to_period("M").unique()
    
    for month in months:
        month_start = month.to_timestamp()
        month_end = (month + 1).to_timestamp()
        
        df_month = df_copy.loc[
            (df_copy.index >= month_start) & (df_copy.index < month_end)
        ].copy()
        
        df_exit_month = None
        if df_exit_copy is not None:
            df_exit_month = df_exit_copy.loc[
                (df_exit_copy.index >= month_start) & (df_exit_copy.index < month_end)
            ].copy()
        
        regime_month = regime_copy.loc[
            (regime_copy.index >= month_start) & (regime_copy.index < month_end)
        ].copy()
        
        if df_month.empty or regime_month.empty:
            continue
        
        params = STATIC_PARAMS.copy()
        params["sd_threshold"] = current_sd
        params["levels"] = compute_reference_levels(df_month)
        params["regime_series"] = regime_month
        
        equity, trades = run_vwap_scalping(df_month, CUTOFF, df_exit=df_exit_month, **params)
        
        trade_count = len(trades)
        wr = compute_win_rate(trades) if trade_count > 0 else 0.0
        
        adaptation_log.append({
            "month": str(month),
            "sd_before": current_sd,
            "trades": trade_count,
            "wr": wr,
        })
        
        all_equity.append(equity)
        all_trades.extend(trades)
        
        # Adapt for next month
        if len(all_trades) >= ADAPT_LOOKBACK:
            recent_trades = all_trades[-ADAPT_LOOKBACK:]
            new_sd, reason = v5_adapt_sd(current_sd, recent_trades)
            
            if new_sd != current_sd:
                adaptation_log[-1]["adapt_reason"] = reason
                adaptation_log[-1]["sd_after"] = new_sd
                current_sd = new_sd
            else:
                adaptation_log[-1]["adapt_reason"] = "no_change"
                adaptation_log[-1]["sd_after"] = current_sd
    
    if all_equity:
        combined_equity = pd.concat(all_equity)
        combined_equity = combined_equity.sort_index()
    else:
        combined_equity = pd.Series()
    
    metrics = compute_metrics_for_result(combined_equity, all_trades)
    return combined_equity, all_trades, metrics, adaptation_log


def run_v6_comprehensive(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
) -> Tuple[pd.Series, list, Dict[str, float], List[Dict[str, Any]]]:
    """
    Run V6 comprehensive adaptive strategy (5 parameters).
    """
    if len(df) < 1000:
        params = STATIC_PARAMS.copy()
        params["levels"] = compute_reference_levels(df)
        params["regime_series"] = regime_series
        params["sd_threshold"] = V6_BASE_SD
        params["atr_stop"] = V6_BASE_ATR_STOP
        params["entry_mode"] = V6_BASE_ENTRY_MODE
        equity, trades = run_vwap_scalping(df, CUTOFF, df_exit=df_exit, **params)
        metrics = compute_metrics_for_result(equity, trades)
        return equity, trades, metrics, []
    
    # Process in monthly segments
    adaptation_log = []
    state = V6State()
    all_equity = []
    all_trades = []
    
    # Remove tz for consistent comparison
    df_copy = df.copy()
    df_copy.index = df_copy.index.tz_localize(None)
    if df_exit is not None:
        df_exit_copy = df_exit.copy()
        df_exit_copy.index = df_exit_copy.index.tz_localize(None)
    else:
        df_exit_copy = None
    regime_copy = regime_series.copy()
    regime_copy.index = regime_copy.index.tz_localize(None)
    
    months = df_copy.index.to_period("M").unique()
    
    for month in months:
        month_start = month.to_timestamp()
        month_end = (month + 1).to_timestamp()
        
        df_month = df_copy.loc[
            (df_copy.index >= month_start) & (df_copy.index < month_end)
        ].copy()
        
        df_exit_month = None
        if df_exit_copy is not None:
            df_exit_month = df_exit_copy.loc[
                (df_exit_copy.index >= month_start) & (df_exit_copy.index < month_end)
            ].copy()
        
        regime_month = regime_copy.loc[
            (regime_copy.index >= month_start) & (regime_copy.index < month_end)
        ].copy()
        
        if df_month.empty or regime_month.empty:
            continue
        
        params = STATIC_PARAMS.copy()
        params["sd_threshold"] = state.sd
        params["atr_stop"] = state.atr_stop
        params["entry_mode"] = state.entry_mode
        params["levels"] = compute_reference_levels(df_month)
        params["regime_series"] = regime_month
        
        equity, trades = run_vwap_scalping(df_month, CUTOFF, df_exit=df_exit_month, **params)
        
        trade_count = len(trades)
        wr = compute_win_rate(trades) if trade_count > 0 else 0.0
        
        adaptation_log.append({
            "month": str(month),
            "sd": state.sd,
            "atr_stop": state.atr_stop,
            "entry_mode": state.entry_mode,
            "position_size": state.position_size,
            "cooldown": state.cooldown,
            "trades": trade_count,
            "wr": wr,
        })
        
        all_equity.append(equity)
        all_trades.extend(trades)
        
        # Adapt for next month
        if len(all_trades) >= ADAPT_MIN_TRADES:
            recent_trades = all_trades[-ADAPT_LOOKBACK:]
            adapt_result = state.adapt(recent_trades)
            
            if adapt_result["adaptations"]:
                adaptation_log[-1]["adaptations"] = "; ".join(adapt_result["adaptations"])
            else:
                adaptation_log[-1]["adaptations"] = "no_change"
    
    if all_equity:
        combined_equity = pd.concat(all_equity)
        combined_equity = combined_equity.sort_index()
    else:
        combined_equity = pd.Series()
    
    metrics = compute_metrics_for_result(combined_equity, all_trades)
    return combined_equity, all_trades, metrics, adaptation_log


# ─────────────────────────────────────────────────────────────────────────────
# YEAR-BY-YEAR BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    year: int,
    config_name: str,
) -> Dict[str, Any]:
    """Run backtest for a specific year."""
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    df_exit_year = None
    if df_exit is not None:
        df_exit_year = df_exit.loc[(df_exit.index >= year_start) & (df_exit.index < year_end)].copy()
    
    regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
    
    if df_year.empty or regime_year.empty:
        return {
            "Year": year,
            "Config": config_name,
            "Trades": 0,
            "WR%": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
            "SD": 3.0,
            "ATR_Stop": 1.87,
            "Entry_Mode": "bull_pullback",
            "Position_Size": 1.0,
            "Cooldown": 0,
        }
    
    if config_name == "static":
        _, trades, metrics = run_static_baseline(df_year, df_exit_year, regime_year)
        return {
            "Year": year,
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
            "SD": 3.0,
            "ATR_Stop": 1.87,
            "Entry_Mode": "bull_pullback",
            "Position_Size": 1.0,
            "Cooldown": 0,
        }
    
    elif config_name == "v5_sd_only":
        _, trades, metrics, _ = run_v5_adaptive(df_year, df_exit_year, regime_year)
        # Get final SD from adaptation log
        return {
            "Year": year,
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
            "SD": 3.0,  # Will be updated by adaptive
            "ATR_Stop": 1.87,
            "Entry_Mode": "bull_pullback",
            "Position_Size": 1.0,
            "Cooldown": 0,
        }
    
    elif config_name == "v6_comprehensive":
        _, trades, metrics, adapt_log = run_v6_comprehensive(df_year, df_exit_year, regime_year)
        
        # Get final params from adaptation log
        final_sd = V6_BASE_SD
        final_atr = V6_BASE_ATR_STOP
        final_mode = V6_BASE_ENTRY_MODE
        final_size = V6_BASE_POSITION_SIZE
        final_cd = V6_BASE_COOLDOWN
        
        if adapt_log:
            last = adapt_log[-1]
            final_sd = last.get("sd", V6_BASE_SD)
            final_atr = last.get("atr_stop", V6_BASE_ATR_STOP)
            final_mode = last.get("entry_mode", V6_BASE_ENTRY_MODE)
            final_size = last.get("position_size", V6_BASE_POSITION_SIZE)
            final_cd = last.get("cooldown", V6_BASE_COOLDOWN)
        
        return {
            "Year": year,
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
            "SD": final_sd,
            "ATR_Stop": final_atr,
            "Entry_Mode": final_mode,
            "Position_Size": final_size,
            "Cooldown": final_cd,
        }
    
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("V6 Comprehensive Tuner Test - Multi-Parameter Adaptation")
    print("=" * 70)
    print("\n--- V6 Adjusts 5 Parameters:")
    print("  1. SD threshold (selectivity)")
    print("  2. ATR stop (risk)")
    print("  3. Entry mode (direction - switches after loss streaks)")
    print("  4. Position size (exposure)")
    print("  5. Cooldown (frequency)")
    print("\n--- V6 Configuration:")
    print(f"  Base SD: {V6_BASE_SD}")
    print(f"  Base ATR Stop: {V6_BASE_ATR_STOP}")
    print(f"  Base Entry Mode: {V6_BASE_ENTRY_MODE}")
    print(f"  Base Position Size: {V6_BASE_POSITION_SIZE}")
    print(f"  Base Cooldown: {V6_BASE_COOLDOWN} bars")
    print(f"  Adaptation Lookback: {ADAPT_LOOKBACK} trades")
    print(f"  Loss Streak Threshold: {LOSS_STREAK_THRESHOLD} consecutive losses")
    print("\n--- Test Period: 2023-2025")
    print("\n--- Comparing:")
    print("  1. Static (no adaptation)")
    print("  2. V5 (SD only adaptation)")
    print("  3. V6 (comprehensive 5-parameter adaptation)")
    print()
    
    # Load data
    print("Loading 5m data...")
    df_5m = load_5m_data(TEST_START, TEST_END)
    if df_5m.empty:
        print("ERROR: No 5m data loaded!")
        return
    print(f"  Loaded {len(df_5m):,} bars")
    
    print("Loading 1m data for exits...")
    df_1m = load_1m_data(TEST_START, TEST_END)
    if df_1m is not None:
        print(f"  Loaded {len(df_1m):,} bars")
    else:
        print("  No 1m data (will use 5m for exits)")
    
    # Compute regimes
    print("\nComputing regimes...")
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    regime_dist = regime_series.value_counts()
    for r in MarketRegime:
        cnt = regime_dist.get(r, 0)
        print(f"  {r.value}: {cnt} bars ({cnt/len(regime_series)*100:.1f}%)")
    
    # Test years: 2023-2025
    years = [2023, 2024, 2025]
    print(f"\nTesting years: {years}")
    
    # Storage for results
    all_results = []
    all_adaptations = []
    
    configs = ["static", "v5_sd_only", "v6_comprehensive"]
    
    for config in configs:
        print("\n" + "=" * 50)
        print(f"Running {config.upper()}")
        print("=" * 50)
        
        for year in years:
            print(f"  Processing {year}...")
            result = backtest_year(df_5m, df_1m, regime_series, year, config)
            all_results.append(result)
            
            # Collect adaptations for V5 and V6
            if config in ["v5_sd_only", "v6_comprehensive"]:
                year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
                year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
                
                df_year = df_5m.loc[(df_5m.index >= year_start) & (df_5m.index < year_end)].copy()
                df_exit_year = None
                if df_1m is not None:
                    df_exit_year = df_1m.loc[(df_1m.index >= year_start) & (df_1m.index < year_end)].copy()
                regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
                
                if not df_year.empty and not regime_year.empty:
                    if config == "v5_sd_only":
                        _, _, _, adapt_log = run_v5_adaptive(df_year, df_exit_year, regime_year)
                        for log in adapt_log:
                            if log.get("adapt_reason", "no_change") != "no_change":
                                all_adaptations.append({
                                    "Year": year,
                                    "Config": config,
                                    **log
                                })
                    elif config == "v6_comprehensive":
                        _, _, _, adapt_log = run_v6_comprehensive(df_year, df_exit_year, regime_year)
                        for log in adapt_log:
                            if log.get("adaptations", "no_change") != "no_change":
                                all_adaptations.append({
                                    "Year": year,
                                    "Config": config,
                                    **log
                                })
    
    # Create DataFrame
    results_df = pd.DataFrame(all_results)
    
    # Save main results
    output_path = RESULTS_DIR / "tuner_v6_test.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Save adaptation log
    if all_adaptations:
        adapt_df = pd.DataFrame(all_adaptations)
        adapt_path = RESULTS_DIR / "tuner_v6_adaptations.csv"
        adapt_df.to_csv(adapt_path, index=False)
        print(f"Saved adaptation log to: {adapt_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    for config in configs:
        config_results = results_df[results_df["Config"] == config]
        total_trades = config_results["Trades"].sum()
        weighted_wr = (config_results["WR%"] * config_results["Trades"]).sum() / max(total_trades, 1)
        total_net = config_results["Net%"].sum()
        avg_sharpe = config_results["Sharpe"].mean()
        max_dd = config_results["MaxDD%"].max()
        
        print(f"\n{config}:")
        print(f"  Total Trades: {total_trades}")
        print(f"  Avg WR%: {weighted_wr:.1f}%")
        print(f"  Total Net%: {total_net:.1f}%")
        print(f"  Avg Sharpe: {avg_sharpe:.2f}")
        print(f"  Max DD%: {max_dd:.1f}%")
    
    # Comparison table
    print("\n" + "=" * 70)
    print("COMPARISON TABLE")
    print("=" * 70)
    print(f"\n{'Config':<25} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-" * 65)
    
    for config in configs:
        config_results = results_df[results_df["Config"] == config]
        total_trades = config_results["Trades"].sum()
        weighted_wr = (config_results["WR%"] * config_results["Trades"]).sum() / max(total_trades, 1)
        total_net = config_results["Net%"].sum()
        avg_sharpe = config_results["Sharpe"].mean()
        max_dd = config_results["MaxDD%"].max()
        
        print(f"{config:<25} {total_trades:>8} {weighted_wr:>7.1f}% {total_net:>7.1f}% {avg_sharpe:>8.2f} {max_dd:>7.1f}%")
    
    # Year-by-year breakdown
    print("\n" + "=" * 70)
    print("YEAR-BY-YEAR BREAKDOWN")
    print("=" * 70)
    print(f"\n{'Year':<8} {'Config':<25} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'Final_SD':>10}")
    print("-" * 75)
    
    for year in years:
        for config in configs:
            row = results_df[(results_df["Year"] == year) & (results_df["Config"] == config)]
            if not row.empty:
                r = row.iloc[0]
                print(f"{year:<8} {config:<25} {r['Trades']:>8} {r['WR%']:>7.1f}% {r['Net%']:>7.1f}% {r['SD']:>10.2f}")
    
    # V6 final parameters
    print("\n" + "=" * 70)
    print("V6 FINAL PARAMETERS (BY YEAR)")
    print("=" * 70)
    v6_results = results_df[results_df["Config"] == "v6_comprehensive"]
    print(f"\n{'Year':<8} {'SD':>8} {'ATR':>8} {'Entry_Mode':<20} {'Size':>8} {'Cooldown':>10}")
    print("-" * 70)
    for _, r in v6_results.iterrows():
        print(f"{r['Year']:<8} {r['SD']:>8.2f} {r['ATR_Stop']:>8.2f} {r['Entry_Mode']:<20} {r['Position_Size']:>8.2f} {r['Cooldown']:>10}")
    
    # Adaptation log
    if all_adaptations:
        print("\n" + "=" * 70)
        print("ADAPTATION LOG (V6)")
        print("=" * 70)
        for record in all_adaptations:
            if "adaptations" in record:
                print(f"  {record['month']}: {record['adaptations']}")
            elif "adapt_reason" in record:
                print(f"  {record['month']}: {record['adapt_reason']} (sd {record['sd_before']:.2f} -> {record['sd_after']:.2f})")
    else:
        print("\n" + "=" * 70)
        print("ADAPTATION LOG")
        print("=" * 70)
        print("  (No adaptations triggered)")
    
    # Validation checks
    print("\n" + "=" * 70)
    print("VALIDATION CHECKS")
    print("=" * 70)
    
    static_results = results_df[results_df["Config"] == "static"]
    v5_results = results_df[results_df["Config"] == "v5_sd_only"]
    v6_results = results_df[results_df["Config"] == "v6_comprehensive"]
    
    static_trades = static_results["Trades"].sum()
    v5_trades = v5_results["Trades"].sum()
    v6_trades = v6_results["Trades"].sum()
    
    static_wr = (static_results["WR%"] * static_results["Trades"]).sum() / max(static_trades, 1)
    v5_wr = (v5_results["WR%"] * v5_results["Trades"]).sum() / max(v5_trades, 1)
    v6_wr = (v6_results["WR%"] * v6_results["Trades"]).sum() / max(v6_trades, 1)
    
    static_net = static_results["Net%"].sum()
    v5_net = v5_results["Net%"].sum()
    v6_net = v6_results["Net%"].sum()
    
    print(f"\n1. Win Rate Comparison:")
    print(f"   Static: {static_wr:.1f}%")
    print(f"   V5 (SD only): {v5_wr:.1f}%")
    print(f"   V6 (comprehensive): {v6_wr:.1f}%")
    
    print(f"\n2. Net Return Comparison:")
    print(f"   Static: {static_net:+.1f}%")
    print(f"   V5 (SD only): {v5_net:+.1f}%")
    print(f"   V6 (comprehensive): {v6_net:+.1f}%")
    
    print(f"\n3. Trade Count:")
    print(f"   Static: {static_trades}")
    print(f"   V5: {v5_trades}")
    print(f"   V6: {v6_trades}")
    
    print(f"\n4. Adaptation Activity:")
    print(f"   Total adaptations: {len(all_adaptations)}")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    # Determine winner
    best_wr = max(static_wr, v5_wr, v6_wr)
    best_net = max(static_net, v5_net, v6_net)
    
    winners = []
    if static_wr == best_wr and static_net == best_net:
        winners.append("Static")
    if v5_wr == best_wr and v5_net == best_net:
        winners.append("V5")
    if v6_wr == best_wr and v6_net == best_net:
        winners.append("V6")
    
    print(f"\nBest Win Rate: {best_wr:.1f}%")
    print(f"Best Net Return: {best_net:+.1f}%")
    print(f"Winner(s): {', '.join(winners) if winners else 'TBD'}")
    
    if v6_wr >= 40 and v6_net > 0:
        print("\n[V6 PASSED] Achieved 40%+ WR and positive returns!")
        print("   Comprehensive adaptation works as designed.")
    elif v6_wr >= 35:
        print("\n[V6 PARTIAL] Achieved 35%+ WR but returns need review")
    else:
        print("\n[V6 REVIEW] Below expected range - check adaptation thresholds")
    
    if len(all_adaptations) > 0:
        v6_adapt_count = sum(1 for a in all_adaptations if a.get("Config") == "v6_comprehensive")
        print(f"\nV6 adaptations triggered: {v6_adapt_count}")
    
    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()
