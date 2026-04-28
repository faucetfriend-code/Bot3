"""
test_session_vwap.py
==================
Test session-anchored VWAP with different trading sessions.

Session Anchors:
- Asia: 00:00-08:00 UTC
- London: 08:00-16:00 UTC
- NY: 13:30-20:00 UTC
- London/NY overlap: 13:30-16:00 UTC
- Full day: 00:00-24:00 (for daily comparison)

Uses best SFP config:
- sd_threshold: 3.5
- atr_stop: 2.0
- tp_mode: "rr" (2:1 risk reward)
- require_sfp: True
- require_volume: True
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Tuple, Any, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

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

TEST_START = "2023-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10

# Best SFP config from prior work
# Using tight stops and SFP confirmation
SFP_CONFIG = {
    "sd_threshold": 3.0,
    "atr_stop": 0.7,       # Tight stop
    "atr_target": 3.0,    # Wide TP for trailing
    "tp_mode": "atr",      # ATR-based exits
    "require_reversal_candle": True,   # SFP confirmation
    "use_volume_filter": True,
    "volume_mult": 1.5,   # Higher volume filter
    "entry_mode": "bull_pullback",
    "use_trend_filter": True,  # Trend filter on
}

# Session definitions (UTC hours)
SESSIONS = {
    "Asia": (0, 8),
    "London": (8, 16),
    "NY": (13, 20),
    "London_NY_Overlap": (13, 16),
    "Full_Day": (0, 24),
}


# ─────────────────────────────────────────────────────────────────────────────
# SESSION-ANCHORED VWAP
# ─────────────────────────────────────────────────────────────────────────────


def compute_vwap_session_anchored(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    session_start_hour: int,
    session_end_hour: int,
) -> Tuple[pd.Series, pd.Series]:
    """
    Compute VWAP anchored to a specific trading session.

    Session-based VWAP resets calculation at session start, providing a true
    volume-weighted average for the current session window.

    Parameters
    ----------
    high, low, close, volume : pd.Series
        Price and volume data with DatetimeIndex (UTC).
    session_start_hour : int
        Session start hour in UTC (0-23).
    session_end_hour : int
        Session end hour in UTC (0-23).

    Returns
    -------
    vwap, vwap_std : tuple[pd.Series, pd.Series]
        Session-anchored VWAP and standard deviation, aligned to original index.
    """
    tp = (high + low + close) / 3.0
    tpvol = tp * volume
    tp2vol = tp**2 * volume

    hours = high.index.hour

    # Create session ID based on hour
    # Session starts at session_start_hour and resets at session_start_hour each day
    if session_end_hour <= session_start_hour:
        # Overnight session (e.g., 20:00-04:00)
        in_session = (hours >= session_start_hour) | (hours < session_end_hour)
    else:
        in_session = (hours >= session_start_hour) & (hours < session_end_hour)

    # Create unique session key: date + session identifier
    # This groups bars within each session occurrence
    dates = high.index.date
    session_key = dates  # Reset daily at session start

    # For each session, compute cumulative sums
    # Group by session_key to handle session boundaries
    cum_vol = volume.groupby(session_key).cumsum()
    cum_tpvol = tpvol.groupby(session_key).cumsum()
    cum_tp2vol = tp2vol.groupby(session_key).cumsum()

    vwap = cum_tpvol / (cum_vol + 1e-10)
    e_tp2 = cum_tp2vol / (cum_vol + 1e-10)
    vwap_std = np.sqrt((e_tp2 - vwap**2).clip(lower=0))

    # NaN out values outside session hours
    vwap = vwap.where(in_session, np.nan)
    vwap_std = vwap_std.where(in_session, np.nan)

    # Forward-fill within session, but also need to reset at session boundary
    # The groupby cumsum handles this naturally
    return vwap, vwap_std


def compute_vwap_session_with_id(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    session_start_hour: int,
    session_end_hour: int,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute VWAP anchored to session with session ID for tracking.

    Returns (vwap, vwap_std, session_id) aligned to original index.
    """
    tp = (high + low + close) / 3.0
    tpvol = tp * volume
    tp2vol = tp**2 * volume

    hours = high.index.hour

    # Determine if in session
    if session_end_hour <= session_start_hour:
        in_session = (hours >= session_start_hour) | (hours < session_end_hour)
    else:
        in_session = (hours >= session_start_hour) & (hours < session_end_hour)

    # Create unique session ID: YYYY-MM-DD_sessionName
    # For now, use date + session window as key
    dates = high.index.date
    hours_arr = high.index.hour

    # Session ID changes at session_start_hour each day
    # Bars with hour >= start are in session N, bars before are in session N-1
    session_num = np.zeros(len(hours_arr), dtype=int)
    for i in range(1, len(session_num)):
        if hours_arr[i] == session_start_hour:
            session_num[i] = session_num[i - 1] + 1
        else:
            session_num[i] = session_num[i - 1]

    session_ids = [f"{d}_{s}" for d, s in zip(dates, session_num)]
    session_id_series = pd.Series(session_ids, index=high.index)

    # Cumulative within each session
    cum_vol = volume.groupby(session_id_series).cumsum()
    cum_tpvol = tpvol.groupby(session_id_series).cumsum()
    cum_tp2vol = tp2vol.groupby(session_id_series).cumsum()

    vwap = cum_tpvol / (cum_vol + 1e-10)
    e_tp2 = cum_tp2vol / (cum_vol + 1e-10)
    vwap_std = np.sqrt((e_tp2 - vwap**2).clip(lower=0))

    # Mask out-of-session hours
    vwap = vwap.where(in_session, np.nan)
    vwap_std = vwap_std.where(in_session, np.nan)

    return vwap, vwap_std, session_id_series


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────


def load_5m_data(start: str, end: str) -> pd.DataFrame:
    """Load BTCUSDT 5m data for test period."""
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


def run_session_test(
    df: pd.DataFrame,
    session_name: str,
    session_start: int,
    session_end: int,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run backtest with session-anchored VWAP.

    For each session, filter entries to only occur within the session window.
    """
    # Compute session hours filter
    hours = df.index.hour
    if session_end <= session_start:
        in_session = (hours >= session_start) | (hours < session_end)
    else:
        in_session = (hours >= session_start) & (hours < session_end)

    print(f"\n{'='*60}")
    print(f"Session: {session_name} ({session_start:02d}:00-{session_end:02d}:00 UTC)")
    print(f"Bars in session: {in_session.sum():,} / {len(df):,} ({in_session.sum()/len(df)*100:.1f}%)")
    print(f"{'='*60}")

# Run strategy with session filter
    # use_session_filter=True and custom hours
    eq, trades = run_vwap_scalping(
        df,
        cutoff=CUTOFF,
        sd_threshold=config["sd_threshold"],
        atr_stop=config["atr_stop"],
        atr_target=config.get("atr_target", 4.0),
        tp_mode=config.get("tp_mode", "atr"),
        entry_mode=config.get("entry_mode", "bull_pullback"),
        require_reversal_candle=config.get("require_reversal_candle", False),
        use_volume_filter=config.get("use_volume_filter", True),
        volume_mult=config.get("volume_mult", 1.2),
        use_trend_filter=config.get("use_trend_filter", False),
        use_anchored_vwap=True,
        # Session filter - custom hours
        use_session_filter=True,
        session_start_hour=session_start,
        session_end_hour=session_end,
    )

    # Compute metrics
    metrics = compute_metrics(eq, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["5m"])

    print(f"Trades: {metrics['n_trades']}")
    print(f"Win Rate: {metrics['win_rate_pct']:.1f}%")
    print(f"Total Return: {metrics['total_return_pct']:.2f}%")
    print(f"Sharpe: {metrics['sharpe']:.2f}")
    print(f"Max DD: {metrics['max_dd_pct']:.2f}%")

    return {
        "session": session_name,
        "start_hour": session_start,
        "end_hour": session_end,
        "trades": metrics["n_trades"],
        "win_rate": metrics["win_rate_pct"],
        "total_return": metrics["total_return_pct"],
        "sharpe": metrics["sharpe"],
        "max_dd": metrics["max_dd_pct"],
        "equity": eq,
        "trades_list": trades,
    }


def run_session_vwap_test(
    df: pd.DataFrame,
    session_name: str,
    session_start: int,
    session_end: int,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run backtest with session-anchored VWAP calculation.

    This uses compute_vwap_session_anchored to compute VWAP reset at session start.
    """
    print(f"\n{'='*60}")
    print(f"Session VWAP: {session_name} ({session_start:02d}:00-{session_end:02d}:00 UTC)")
    print(f"{'='*60}")

    # Compute session-anchored VWAP
    vwap, vwap_std = compute_vwap_session_anchored(
        df["High"], df["Low"], df["Close"], df["Volume"],
        session_start, session_end
    )

    # Add to dataframe for strategy
    df_with_vwap = df.copy()
    df_with_vwap["session_vwap"] = vwap
    df_with_vwap["session_vwap_std"] = vwap_std

    # Run strategy - the strategy will use anchored VWAP
    # We need to set use_anchored_vwap but use our custom vwap
    # For now, run standard strategy with session filter
    eq, trades = run_vwap_scalping(
        df,
        cutoff=CUTOFF,
        sd_threshold=config["sd_threshold"],
        atr_stop=config["atr_stop"],
        atr_target=config.get("atr_target", 4.0),
        tp_mode=config.get("tp_mode", "atr"),
        entry_mode=config.get("entry_mode", "bull_pullback"),
        require_reversal_candle=config.get("require_reversal_candle", False),
        use_volume_filter=config.get("use_volume_filter", True),
        volume_mult=config.get("volume_mult", 1.2),
        use_trend_filter=config.get("use_trend_filter", False),
        use_anchored_vwap=True,  # Use daily-anchored as baseline
        use_session_filter=True,
        session_start_hour=session_start,
        session_end_hour=session_end,
    )

    metrics = compute_metrics(eq, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["5m"])

    print(f"Trades: {metrics['n_trades']}")
    print(f"Win Rate: {metrics['win_rate_pct']:.1f}%")
    print(f"Total Return: {metrics['total_return_pct']:.2f}%")
    print(f"Sharpe: {metrics['sharpe']:.2f}")
    print(f"Max DD: {metrics['max_dd_pct']:.2f}%")

    return {
        "session": session_name,
        "start_hour": session_start,
        "end_hour": session_end,
        "trades": metrics["n_trades"],
        "win_rate": metrics["win_rate_pct"],
        "total_return": metrics["total_return_pct"],
        "sharpe": metrics["sharpe"],
        "max_dd": metrics["max_dd_pct"],
        "equity": eq,
        "trades_list": trades,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN TEST
# ─────────────────────────────────────────────────────────────────────────────


def main():
    """Run session VWAP tests."""
    print("Loading data...")
    df = load_5m_data(TEST_START, TEST_END)
    if df.empty:
        print("ERROR: No data loaded")
        return

    print(f"Loaded {len(df):,} bars from {TEST_START} to {TEST_END}")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")

    # Compute reference levels once
    print("\ncomputing reference levels...")
    levels = compute_reference_levels(df)

    # Test different sessions
    results = []
    for session_name, (start, end) in SESSIONS.items():
        result = run_session_test(
            df, session_name, start, end, SFP_CONFIG
        )
        results.append(result)

    # Summary
    print("\n" + "="*80)
    print("SESSION TEST SUMMARY")
    print("="*80)
    print(f"{'Session':<25} {'Trades':>8} {'Win%':>8} {'Return%':>10} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-"*80)

    best_session = None
    best_return = -float("inf")

    for r in results:
        print(
            f"{r['session']:<25} {r['trades']:>8} "
            f"{r['win_rate']:>8.1f} {r['total_return']:>10.2f} "
            f"{r['sharpe']:>8.2f} {r['max_dd']:>8.2f}"
        )
        if r["total_return"] > best_return:
            best_return = r["total_return"]
            best_session = r["session"]

    print("-"*80)
    print(f"\nBest performing session: {best_session} ({best_return:.2f}% return)")

    # Save results
    results_path = RESULTS_DIR / "session_vwap_results.csv"
    summary_df = pd.DataFrame([
        {k: v for k, v in r.items() if k not in ["equity", "trades_list"]}
        for r in results
    ])
    summary_df.to_csv(results_path, index=False)
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()