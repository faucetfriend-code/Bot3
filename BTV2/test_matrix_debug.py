#!/usr/bin/env python3
"""
VWAP Matrix Debug - Find why results differ
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.data_manager import get_candles
from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import run_vwap_scalping, compute_reference_levels, apply_butterworth

# Load data
print("Loading data...")
df = get_candles("BTCUSDT", "5m", "2020-01-01", "2025-12-31")
print(f"Loaded: {len(df)} bars")

# Compute Butterworth filtered close (cutoff is applied internally!)
print("Computing Butterworth filter...")
fc = apply_butterworth(df["Close"], 0.10)
df["fc"] = fc

# Compute SD of filtered close (used for entry triggers)
df["fc_std"] = df["fc"].rolling(20).std()
print(f"fc mean: {df['fc'].mean():.2f}, std: {df['fc_std'].mean():.4f}")

# Now detect regimes
print("Detecting regimes...")
detector = RegimeDetector(df, method="combined", timeframe="15m")
regimes = detector.get_regimes()

# Add regimes to df
df["regime"] = regimes

# Filter to RANGING only
ranging_mask = df["regime"] == MarketRegime.RANGING
df_ranging = df[ranging_mask].copy()
print(f"RANGING bars: {len(df_ranging)}")

# Now check: what's the VWAP deviation in RANGING?
# SD threshold = 3.0 means 3 * rolling_std deviation from VWAP
# But we need to compute VWAP first

# Check entry triggers for mean_reversion in RANGING
print("\n" + "=" * 70)
print("Checking mean_reversion entry triggers in RANGING bars")
print("=" * 70)

# Compute anchored VWAP
from BTV2.strategies import compute_vwap_anchored
session_vwap, session_std = compute_vwap_anchored(df["High"], df["Low"], df["Close"], df["Volume"])
df["session_vwap"] = session_vwap
df["session_vwap_std"] = session_std

# Compute deviation in terms of SD
df["vwap_deviation"] = (df["Close"] - df["session_vwap"]) / df["session_vwap_std"]

# For mean_reversion, entry when price deviates > sd_threshold SD
sd_threshold = 3.0

# Count potential entries in RANGING
ranging_df = df[df["regime"] == MarketRegime.RANGING].copy()
long_entries = (ranging_df["Close"] < ranging_df["session_vwap"] - sd_threshold * ranging_df["session_vwap_std"])
short_entries = (ranging_df["Close"] > ranging_df["session_vwap"] + sd_threshold * ranging_df["session_vwap_std"])

print(f"RANGING bars with long opportunity (>{sd_threshold} SD below VWAP): {long_entries.sum()}")
print(f"RANGING bars with short opportunity (>{sd_threshold} SD above VWAP): {short_entries.sum()}")
print(f"Total potential entries: {long_entries.sum() + short_entries.sum()}")

# What about smaller SD thresholds?
for sd in [0.5, 1.0, 2.0, 3.0]:
    long_entries = (ranging_df["Close"] < ranging_df["session_vwap"] - sd * ranging_df["session_vwap_std"])
    short_entries = (ranging_df["Close"] > ranging_df["session_vwap"] + sd * ranging_df["session_vwap_std"])
    print(f"  sd={sd}: {long_entries.sum() + short_entries.sum()} potential entries")

# Now check what filters might be blocking entries
print("\n" + "=" * 70)
print("Checking filter conditions for RANGING bars")
print("=" * 70)

# Compute ADX and RSI
from BTV2.strategies import compute_adx, compute_rsi

adx = compute_adx(df["High"], df["Low"], df["Close"])
df["adx"] = adx
rsi = compute_rsi(df["Close"])
df["rsi"] = rsi

# Volume filter
df["vol_ma20"] = df["Volume"].rolling(20).mean()
df["vol_ratio"] = df["Volume"] / df["vol_ma20"]

adx_max = 30.0
rsi_max = 55.0
volume_mult = 1.0

ranging_df = df[df["regime"] == MarketRegime.RANGING].copy()

# Check how many bars pass each filter
all_bars = len(ranging_df)
adx_pass = (ranging_df["adx"] < adx_max).sum()
rsi_pass_long = (ranging_df["rsi"] < rsi_max).sum()
rsi_pass_short = (ranging_df["rsi"] > (100 - rsi_max)).sum()
volume_pass = (ranging_df["vol_ratio"] >= volume_mult).sum()

print(f"Total RANGING bars: {all_bars}")
print(f"ADX < {adx_max}: {adx_pass} ({adx_pass/all_bars*100:.1f}%)")
print(f"RSI < {rsi_max} (long): {rsi_pass_long} ({rsi_pass_long/all_bars*100:.1f}%)")
print(f"RSI > {100-rsi_max} (short): {rsi_pass_short} ({rsi_pass_short/all_bars*100:.1f}%)")
print(f"Volume >= {volume_mult}x: {volume_pass} ({volume_pass/all_bars*100:.1f}%)")

# Combined filter
combined_pass = (
    (ranging_df["adx"] < adx_max) & 
    ((ranging_df["rsi"] < rsi_max) | (ranging_df["rsi"] > (100 - rsi_max))) &
    (ranging_df["vol_ratio"] >= volume_mult)
)
print(f"All filters pass: {combined_pass.sum()} ({combined_pass.sum()/all_bars*100:.1f}%)")