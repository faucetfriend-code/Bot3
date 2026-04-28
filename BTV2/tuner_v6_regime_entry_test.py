"""
tuner_v6_regime_entry_test.py
==============================
Test V6 tuner with regime-specific entry modes.

V6 Now Uses:
- RANGING → mean_reversion
- BEAR_WEAK → bull_pullback  
- BEAR_STRONG → cross
- BULL_WEAK → bull_pullback
- BULL_STRONG → cross

This Should Work Because:
- We know from analysis that different regimes work with different modes
- RANGING + mean_reversion should work better
- The tuner adapts SD/ATR but uses correct entry mode per regime

Test:
- 2023-2025
- Compare V6 with regime entry modes vs V6 without (fixed bull_pullback)

Output:
Save to BTV2/results/tuner_v6_regime_test.csv
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

# Test period: 2023-2025
TEST_START = "2023-01-01"
TEST_END = "2026-01-01"

# Butterworth cutoff
CUTOFF = 0.10

# ─────────────────────────────────────────────────────────────────────────────
# REGIME-SPECIFIC ENTRY MODES (from analysis)
# ─────────────────────────────────────────────────────────────────────────────

REGIME_TO_ENTRY_MODE = {
    MarketRegime.RANGING: "mean_reversion",
    MarketRegime.BEAR_WEAK: "bull_pullback",
    MarketRegime.BEAR_STRONG: "cross",
    MarketRegime.BULL_WEAK: "bull_pullback",
    MarketRegime.BULL_STRONG: "cross",
}

# Fixed entry mode for comparison (V6 without regime)
FIXED_ENTRY_MODE = "bull_pullback"

# ─────────────────────────────────────────────────────────────────────────────
# V6 ADAPTATION PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

# Base parameters
V6_BASE_SD = 3.0
V6_BASE_ATR_STOP = 1.87
V6_BASE_POSITION_SIZE = 1.0
V6_BASE_COOLDOWN = 0

# General
ADAPT_LOOKBACK = 30
ADAPT_MIN_TRADES = 20

# SD Adaptation
WR_LOW_SD = 40.0
WR_HIGH_SD = 55.0
SD_TIGHTEN = 0.20
SD_LOOSEN = 0.15
SD_MIN = 2.5
SD_MAX = 6.0

# ATR Stop Adaptation
ATR_TIGHTEN_THRESHOLD = 35.0
ATR_LOOSEN_THRESHOLD = 60.0
ATR_TIGHTEN = 0.15
ATR_LOOSEN = 0.10
ATR_STOP_MIN = 1.0
ATR_STOP_MAX = 3.0

# Position Size Adaptation
SIZE_REDUCE_THRESHOLD = 35.0
SIZE_INCREASE_THRESHOLD = 55.0
SIZE_REDUCE_FACTOR = 0.75
SIZE_INCREASE_FACTOR = 1.25
SIZE_MIN = 0.25
SIZE_MAX = 1.5

# Cooldown Adaptation
COOLDOWN_INCREASE_THRESHOLD = 35.0
COOLDOWN_DECREASE_THRESHOLD = 55.0
COOLDOWN_STEP = 3
COOLDOWN_MAX = 12

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

def compute_win_rate(trades: List[float]) -> float:
    """Compute win rate from list of P&L values."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t > 0)
    return wins / len(trades) * 100


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
# V6 STATE (shared logic for both variants)
# ─────────────────────────────────────────────────────────────────────────────

class V6State:
    """Track V6 tuner state across adaptation cycles."""
    
    def __init__(self, use_regime_entry: bool = False):
        self.use_regime_entry = use_regime_entry
        
        self.sd = V6_BASE_SD
        self.atr_stop = V6_BASE_ATR_STOP
        self.position_size = V6_BASE_POSITION_SIZE
        self.cooldown = V6_BASE_COOLDOWN
        
        # Entry mode - either fixed or regime-based
        self.fixed_entry_mode = FIXED_ENTRY_MODE
        self.regime_entry_mode = FIXED_ENTRY_MODE  # Updated per regime
        
        # Track consecutive losses for entry mode switching
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        
        # Adaptation history
        self.history = []
    
    def reset(self):
        """Reset to base values."""
        self.sd = V6_BASE_SD
        self.atr_stop = V6_BASE_ATR_STOP
        self.position_size = V6_BASE_POSITION_SIZE
        self.cooldown = V6_BASE_COOLDOWN
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.history = []
    
    def get_entry_mode(self, regime: MarketRegime = None) -> str:
        """Get entry mode based on settings."""
        if self.use_regime_entry and regime is not None:
            return REGIME_TO_ENTRY_MODE.get(regime, self.fixed_entry_mode)
        return self.fixed_entry_mode
    
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
        
        # 3. Position Size Adaptation (exposure)
        if wr < SIZE_REDUCE_THRESHOLD:
            old_size = self.position_size
            self.position_size = max(self.position_size * SIZE_REDUCE_FACTOR, SIZE_MIN)
            adaptations.append(f"size: {old_size:.2f}->{self.position_size:.2f}")
        elif wr > SIZE_INCREASE_THRESHOLD:
            old_size = self.position_size
            self.position_size = min(self.position_size * SIZE_INCREASE_FACTOR, SIZE_MAX)
            adaptations.append(f"size: {old_size:.2f}->{self.position_size:.2f}")
        
        # 4. Cooldown Adaptation (frequency)
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


def run_v6_adaptive(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    use_regime_entry: bool = False,
) -> Tuple[pd.Series, list, Dict[str, float], List[Dict[str, Any]]]:
    """
    Run V6 adaptive strategy with optional regime-specific entry modes.
    
    Args:
        use_regime_entry: If True, use REGIME_TO_ENTRY_MODE mapping
                          If False, use fixed bull_pullback
    """
    if len(df) < 1000:
        params = STATIC_PARAMS.copy()
        params["levels"] = compute_reference_levels(df)
        params["regime_series"] = regime_series
        params["sd_threshold"] = V6_BASE_SD
        params["atr_stop"] = V6_BASE_ATR_STOP
        params["entry_mode"] = FIXED_ENTRY_MODE
        equity, trades = run_vwap_scalping(df, CUTOFF, df_exit=df_exit, **params)
        metrics = compute_metrics_for_result(equity, trades)
        return equity, trades, metrics, []
    
    # Process in monthly segments
    adaptation_log = []
    state = V6State(use_regime_entry=use_regime_entry)
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
        
        # Get entry mode - either fixed or regime-based
        entry_mode = FIXED_ENTRY_MODE
        if use_regime_entry:
            # Get most common regime in the month
            if not regime_month.empty:
                most_common_regime = regime_month.mode().iloc[0] if len(regime_month.mode()) > 0 else None
                if most_common_regime:
                    entry_mode = REGIME_TO_ENTRY_MODE.get(most_common_regime, FIXED_ENTRY_MODE)
        
        params = STATIC_PARAMS.copy()
        params["sd_threshold"] = state.sd
        params["atr_stop"] = state.atr_stop
        params["entry_mode"] = entry_mode
        params["levels"] = compute_reference_levels(df_month)
        params["regime_series"] = regime_month
        
        equity, trades = run_vwap_scalping(df_month, CUTOFF, df_exit=df_exit_month, **params)
        
        trade_count = len(trades)
        wr = compute_win_rate(trades) if trade_count > 0 else 0.0
        
        adaptation_log.append({
            "month": str(month),
            "sd": state.sd,
            "atr_stop": state.atr_stop,
            "entry_mode": entry_mode,
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
            "Avg_SD": 3.0,
            "Avg_ATR": 1.87,
            "Entry_Mode": FIXED_ENTRY_MODE,
            "Uses_Regime": config_name == "v6_regime_entry",
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
            "Avg_SD": 3.0,
            "Avg_ATR": 1.87,
            "Entry_Mode": STATIC_PARAMS["entry_mode"],
            "Uses_Regime": False,
        }
    
    elif config_name == "v6_fixed_entry":
        _, trades, metrics, adapt_log = run_v6_adaptive(df_year, df_exit_year, regime_year, use_regime_entry=False)
        
        # Get average params from adaptation log
        avg_sd = V6_BASE_SD
        avg_atr = V6_BASE_ATR_STOP
        final_mode = FIXED_ENTRY_MODE
        
        if adapt_log:
            avg_sd = sum(l.get("sd", V6_BASE_SD) for l in adapt_log) / len(adapt_log)
            avg_atr = sum(l.get("atr_stop", V6_BASE_ATR_STOP) for l in adapt_log) / len(adapt_log)
            final_mode = adapt_log[-1].get("entry_mode", FIXED_ENTRY_MODE)
        
        return {
            "Year": year,
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
            "Avg_SD": round(avg_sd, 2),
            "Avg_ATR": round(avg_atr, 2),
            "Entry_Mode": final_mode,
            "Uses_Regime": False,
        }
    
    elif config_name == "v6_regime_entry":
        _, trades, metrics, adapt_log = run_v6_adaptive(df_year, df_exit_year, regime_year, use_regime_entry=True)
        
        # Get average params from adaptation log
        avg_sd = V6_BASE_SD
        avg_atr = V6_BASE_ATR_STOP
        final_mode = "varies"
        
        if adapt_log:
            avg_sd = sum(l.get("sd", V6_BASE_SD) for l in adapt_log) / len(adapt_log)
            avg_atr = sum(l.get("atr_stop", V6_BASE_ATR_STOP) for l in adapt_log) / len(adapt_log)
            # Count mode usage
            modes = [l.get("entry_mode", FIXED_ENTRY_MODE) for l in adapt_log]
            final_mode = "/".join(sorted(set(modes)))
        
        return {
            "Year": year,
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
            "Avg_SD": round(avg_sd, 2),
            "Avg_ATR": round(avg_atr, 2),
            "Entry_Mode": final_mode,
            "Uses_Regime": True,
        }
    
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("V6 Tuner - Regime-Specific Entry Modes Test")
    print("=" * 70)
    print("\n--- Regime Entry Mode Mapping:")
    for regime, mode in REGIME_TO_ENTRY_MODE.items():
        print(f"  {regime.value}: {mode}")
    print(f"\n--- Fixed Entry Mode (V6 without): {FIXED_ENTRY_MODE}")
    print("\n--- Test Period: 2023-2025")
    print("\n--- Comparing:")
    print("  1. Static (baseline, no adaptation)")
    print("  2. V6 Fixed Entry (adapt SD/ATR, fixed bull_pullback)")
    print("  3. V6 Regime Entry (adapt SD/ATR, regime-specific modes)")
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
    print("\nRegime Distribution:")
    for r in MarketRegime:
        cnt = regime_dist.get(r, 0)
        print(f"  {r.value}: {cnt} bars ({cnt/len(regime_series)*100:.1f}%)")
    
    # Test years: 2023-2025
    years = [2023, 2024, 2025]
    print(f"\nTesting years: {years}")
    
    # Storage for results
    all_results = []
    all_adaptations = []
    
    configs = ["static", "v6_fixed_entry", "v6_regime_entry"]
    
    for config in configs:
        print("\n" + "=" * 50)
        print(f"Running {config.upper()}")
        print("=" * 50)
        
        for year in years:
            print(f"  Processing {year}...")
            result = backtest_year(df_5m, df_1m, regime_series, year, config)
            all_results.append(result)
            
            # Collect adaptations for adaptive configs
            if config in ["v6_fixed_entry", "v6_regime_entry"]:
                year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
                year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
                
                df_year = df_5m.loc[(df_5m.index >= year_start) & (df_5m.index < year_end)].copy()
                df_exit_year = None
                if df_1m is not None:
                    df_exit_year = df_1m.loc[(df_1m.index >= year_start) & (df_1m.index < year_end)].copy()
                regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
                
                if not df_year.empty and not regime_year.empty:
                    use_regime = config == "v6_regime_entry"
                    _, _, _, adapt_log = run_v6_adaptive(df_year, df_exit_year, regime_year, use_regime_entry=use_regime)
                    
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
    output_path = RESULTS_DIR / "tuner_v6_regime_test.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Save adaptation log
    if all_adaptations:
        adapt_df = pd.DataFrame(all_adaptations)
        adapt_path = RESULTS_DIR / "tuner_v6_regime_adaptations.csv"
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
    print(f"\n{'Year':<8} {'Config':<25} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'Entry_Mode':<20}")
    print("-" * 85)
    
    for year in years:
        for config in configs:
            row = results_df[(results_df["Year"] == year) & (results_df["Config"] == config)]
            if not row.empty:
                r = row.iloc[0]
                print(f"{year:<8} {config:<25} {r['Trades']:>8} {r['WR%']:>7.1f}% {r['Net%']:>7.1f}% {r['Entry_Mode']:<20}")
    
    # Final parameters comparison
    print("\n" + "=" * 70)
    print("FINAL PARAMETERS (V6 VARIANTS)")
    print("=" * 70)
    for config in ["v6_fixed_entry", "v6_regime_entry"]:
        config_results = results_df[results_df["Config"] == config]
        print(f"\n{config}:")
        print(f"  Avg SD: {config_results['Avg_SD'].mean():.2f}")
        print(f"  Avg ATR: {config_results['Avg_ATR'].mean():.2f}")
        print(f"  Entry Mode: {config_results['Entry_Mode'].iloc[-1]}")
    
    # Regime entry mode advantage
    print("\n" + "=" * 70)
    print("REGIME ENTRY MODE ADVANTAGE")
    print("=" * 70)
    
    fixed_results = results_df[results_df["Config"] == "v6_fixed_entry"]
    regime_results = results_df[results_df["Config"] == "v6_regime_entry"]
    
    fixed_trades = fixed_results["Trades"].sum()
    regime_trades = regime_results["Trades"].sum()
    fixed_wr = (fixed_results["WR%"] * fixed_results["Trades"]).sum() / max(fixed_trades, 1)
    regime_wr = (regime_results["WR%"] * regime_results["Trades"]).sum() / max(regime_trades, 1)
    fixed_net = fixed_results["Net%"].sum()
    regime_net = regime_results["Net%"].sum()
    
    wr_diff = regime_wr - fixed_wr
    net_diff = regime_net - fixed_net
    
    print(f"\nV6 Fixed Entry (bull_pullback):")
    print(f"  WR%: {fixed_wr:.1f}%")
    print(f"  Net%: {fixed_net:+.1f}%")
    print(f"  Trades: {fixed_trades}")
    
    print(f"\nV6 Regime Entry (regime-specific):")
    print(f"  WR%: {regime_wr:.1f}%")
    print(f"  Net%: {regime_net:+.1f}%")
    print(f"  Trades: {regime_trades}")
    
    print(f"\nAdvantage of Regime Entry:")
    print(f"  WR Diff: {wr_diff:+.1f}%")
    print(f"  Net Diff: {net_diff:+.1f}%")
    
    # Validation checks
    print("\n" + "=" * 70)
    print("VALIDATION CHECKS")
    print("=" * 70)
    
    if regime_wr >= fixed_wr:
        print(f"[PASS] Regime entry achieves >= WR than fixed: {regime_wr:.1f}% >= {fixed_wr:.1f}%")
    else:
        print(f"[REVIEW] Regime entry lower WR: {regime_wr:.1f}% < {fixed_wr:.1f}%")
    
    if regime_net >= fixed_net:
        print(f"[PASS] Regime entry achieves >= Net than fixed: {regime_net:+.1f}% >= {fixed_net:+.1f}%")
    else:
        print(f"[REVIEW] Regime entry lower Net: {regime_net:+.1f}% < {fixed_net:+.1f}%")
    
    if regime_wr >= 40 and regime_net > 0:
        print(f"\n[PASS] V6 with regime entry modes achieved 40%+ WR and positive returns!")
    elif regime_wr >= 35:
        print(f"\n[PARTIAL] V6 with regime entry modes achieved 35%+ WR but returns need review")
    else:
        print(f"\n[REVIEW] Below expected range - check regime-entry mapping")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    if wr_diff > 0 and net_diff > 0:
        print("\n[SUCCESS] Regime-specific entry modes WORK!")
        print(f"  +{wr_diff:.1f}% WR, +{net_diff:.1f}% Net improvement")
        print("\n  Key insight: Different regimes benefit from different entry modes.")
        print("  - RANGING + mean_reversion is the gold mine")
        print("  - BULL/BEAR WEAK + bull_pullback for pullback trades")
        print("  - BULL/BEAR STRONG + cross for breakout trades")
    elif wr_diff > 0:
        print("\n[MARGINAL] Regime entry modes improve WR but not Net")
        print("  Consider reviewing risk parameters (ATR stop/target)")
    elif net_diff > 0:
        print("\n[MARGINAL] Regime entry modes improve Net but not WR")
        print("  This is still valuable - better risk-adjusted returns")
    else:
        print("\n[NO ADVANTAGE] Regime entry modes did not help")
        print("  The fixed entry mode may already be optimal")
    
    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()
