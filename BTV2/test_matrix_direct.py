#!/usr/bin/env python3
"""
VWAP Matrix Comparison - Direct Test
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.data_manager import get_candles
from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import run_vwap_scalping, compute_reference_levels, compute_metrics, COST_PER_SIDE, INTERVAL_BARS_PER_YEAR

# Load data using get_candles (like original script)
print("Loading data via get_candles...")
df = get_candles("BTCUSDT", "5m", "2020-01-01", "2025-12-31")
print(f"Loaded: {len(df):,} bars")

# Detect regimes
print("Detecting regimes...")
detector = RegimeDetector(df, method="combined", timeframe="15m")
regimes = detector.get_regimes()
print(f"Regimes: {regimes.value_counts().to_dict()}")

# Compute levels
print("Computing reference levels...")
levels = compute_reference_levels(df)

# Test RANGING + mean_reversion with EXACT matrix params
print("\n" + "=" * 70)
print("TEST: RANGING + mean_reversion (EXACT MATRIX PARAMS)")
print("=" * 70)

PARAMS = {
    "cutoff": 0.10,
    "sd_threshold": 3.0,
    "atr_stop": 2.0,
    "trailing_atr": 1.5,
    "adx_max": 30.0,
    "volume_mult": 1.0,
    "rsi_max": 55.0,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": False,
    "use_volume_filter": True,
    "use_trailing_stop": True,
    "use_htf_vwap": False,
    "use_htf_ema": False,
    "htf_adx_max": 30.0,
    "use_stoch_filter": False,
    "use_anchored_vwap": True,
    "use_session_filter": False,
    "require_reversal_candle": False,
    "tp_mode": "atr",
    "deviation_pct": 0.5,
    "momentum_bars": 2,
    "pullback_bars": 3,
}

eq, trades = run_vwap_scalping(
    df,
    regime_series=regimes,
    allowed_regimes=[MarketRegime.RANGING],
    entry_mode="mean_reversion",
    **PARAMS
)

if trades:
    wins = sum(1 for t in trades if t > 0)
    wr = wins / len(trades) * 100
    gp = sum(t for t in trades if t > 0)
    gl = abs(sum(t for t in trades if t < 0))
    pf = gp / gl if gl > 0 else 999.99
    costs = len(trades) * COST_PER_SIDE
    m = compute_metrics(eq, trades, bars_per_year=105120)
    net = m["total_return_pct"] - costs
    
    print(f"Trades: {len(trades)}")
    print(f"Win Rate: {wr:.1f}%")
    print(f"Gross Profit: {gp:+.2f}%")
    print(f"Gross Loss: {gl:+.2f}%")
    print(f"Net Return: {net:+.2f}%")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Sharpe: {m['sharpe']:.2f}")
    print(f"Max DD: {m['max_dd_pct']:.2f}%")
    print(f"\nMatrix had: 4578 trades, 63.4% WR, +240.66% Net")
else:
    print("No trades!")

# Test BEAR_WEAK + bull_pullback
print("\n" + "=" * 70)
print("TEST: BEAR_WEAK + bull_pullback")
print("=" * 70)

eq2, trades2 = run_vwap_scalping(
    df,
    regime_series=regimes,
    allowed_regimes=[MarketRegime.BEAR_WEAK],
    entry_mode="bull_pullback",
    **PARAMS
)

if trades2:
    wins2 = sum(1 for t in trades2 if t > 0)
    wr2 = wins2 / len(trades2) * 100
    gp2 = sum(t for t in trades2 if t > 0)
    gl2 = abs(sum(t for t in trades2 if t < 0))
    pf2 = gp2 / gl2 if gl2 > 0 else 999.99
    costs2 = len(trades2) * COST_PER_SIDE
    m2 = compute_metrics(eq2, trades2, bars_per_year=105120)
    net2 = m2["total_return_pct"] - costs2
    
    print(f"Trades: {len(trades2)}")
    print(f"Win Rate: {wr2:.1f}%")
    print(f"Net Return: {net2:+.2f}%")
    print(f"Profit Factor: {pf2:.2f}")
    print(f"\nMatrix had: 2706 trades, 56.4% WR, +40.87% Net")
else:
    print("No trades!")