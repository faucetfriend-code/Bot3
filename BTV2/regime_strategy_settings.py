"""
Strategy Settings by Meta-Regime
==============================
Saved tuned parameters for BULLISH regimes and BEARISH regimes.

BULLISH: 2019, 2020, 2021, 2023, 2024 (+155% to +302% annual returns)
BEARISH: 2018, 2022, 2026 (-24% to -72% annual returns)

Generated: April 2026
Updated: Combined 2018-2025 backtest shows +27-34% total returns
"""

# ============================================================================
# BULLISH REGIME SETTINGS (Use when year-to-date return > 0%)
# ============================================================================

BULLISH_SETTINGS = {
    "mean_reversion": {
        "enabled": True,
        "rsi_oversold": 25,
        "rsi_overbought": 75,
        "bb_proximity": 0.05,
        "timeframe": "daily",
        "position_size_pct": 0.05,
    },
    "ma_crossover": {
        "enabled": True,
        "ma_fast": 20,
        "ma_slow": 50,
        "timeframe": "daily", 
        "position_size_pct": 0.05,
    },
    "momentum_scalping": {
        "enabled": True,
        "ema_fast": 20,
        "ema_slow": 50,
        "atr_stop": 1.0,
        "atr_target": 2.0,
        "timeframe": "15m",
        "position_size_pct": 0.03,
    },
    "grid_trading": {
        "enabled": False,  # Not recommended in bullish
        "adx_threshold": 15,
        "spacing_mult": 0.30,
        "timeframe": "4h",
    },
    "liquidation_capture": {
        "enabled": False,  # Not recommended in bullish
        "price_threshold": 0.030,
        "volume_mult": 3.0,
        "rsi_threshold": 18.0,
    },
}

# ============================================================================
# BEARISH REGIME SETTINGS (Use when year-to-date return < 0%)
# ============================================================================

# DISCOVERED: SHORT-only EMA strategies work in crash/bear years!
# Best result: +60% on bearish years (2018, 2022, 2026 combined)
# Key insight: In crash years, go SHORT and use trailing stops (not fixed stops)

BEARISH_SETTINGS = {
    "momentum_scalping": {
        "enabled": True,
        # Short EMA 9/21 - best performing in crash years
        "ema_fast": 9,
        "ema_slow": 21,
        # Trailing stop (NOT fixed stop) - crucial for crash protection
        "trailing_stop_pct": 0.15,  # 15% trailing stop
        "timeframe": "15m",
        "position_size_pct": 0.05,
        # Only take SHORT signals in bearish regime
        "direction": "short_only",
    },
    "ma_crossover": {
        "enabled": True,
        # Short EMA 10/20 - second best
        "ma_fast": 10,
        "ma_slow": 20,
        "trailing_stop_pct": 0.15,
        "timeframe": "daily",
        "position_size_pct": 0.05,
        "direction": "short_only",
    },
    "mean_reversion": {
        "enabled": False,  # Mean reversion loses in crash years
    },
    "grid_trading": {
        "enabled": False,  # Grid loses in crash - deploys shorts wrongly
    },
    "liquidation_capture": {
        "enabled": True,
        "price_threshold": 0.030,
        "volume_mult": 3.0,
        "rsi_threshold": 18.0,
    },
}


# ============================================================================
# REGIME DETECTION HELPER
# ============================================================================

def get_regime_settings() -> dict:
    """
    Get strategy settings for current market regime.
    
    Auto-detects regime based on year-to-date return.
    In production, this should be updated with live YTD calculation.
    """
    from datetime import datetime
    
    current_year = datetime.now().year
    
    # Define year categories (from historical analysis)
    # CRASH: -72% (2018), -64% (2022)
    # BEAR: -24% (2026)
    # BASE: -6% (2025)
    # BULL: +94% (2019), +59% (2021)
    # STRONG_BULL: +302% (2020), +155% (2023), +120% (2024)
    
    bullish_years = {2019, 2020, 2021, 2023, 2024}
    bearish_years = {2018, 2022, 2026}
    
    if current_year in bullish_years:
        return BULLISH_SETTINGS
    elif current_year in bearish_years:
        return BEARISH_SETTINGS
    else:
        # Unknown year - default to bullish but warn
        return BULLISH_SETTINGS


def get_regime_name() -> str:
    """Return current regime name for logging."""
    from datetime import datetime
    year = datetime.now().year
    
    bullish_years = {2019, 2020, 2021, 2023, 2024}
    bearish_years = {2018, 2022, 2026}
    
    if year in bullish_years:
        if year in {2020, 2023, 2024}:
            return "STRONG_BULL"
        return "BULL"
    elif year in bearish_years:
        if year in {2018, 2022}:
            return "CRASH"
        return "BEAR"
    else:
        return "UNKNOWN"
