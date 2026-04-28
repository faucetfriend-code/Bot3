# Regime-Optimized Parameters
# Based on 2018-2025 backtesting analysis
# 
# Key finding: Mean Reversion dominates in 4/5 regimes
# Optimize parameters per regime for maximum edge

# ============================================================
# REGIME: RANGING (ADX < 20, no clear trend)
# Best for Mean Reversion: 54.9% WR, +41.1% Net
# Best for VWAP: mean_reversion mode - +240% WR, +240.7% Net (!)
# ============================================================
RANGING_PARAMS = {
    "strategy": "VWAP Scalping",  # VWAP dominates in ranging
    "entry_mode": "mean_reversion",
    "sd_threshold": 3.0,
    "atr_stop": 1.5,
    "tp_mode": "atr",
    "trailing_atr": 1.2,
}

# ============================================================
# REGIME: RANGING (ADX < 20, no clear trend)
# Best for VWAP: mean_reversion mode - +240% WR, +240.7% Net (THE GOLD MINE!)
# TUNED PARAMETERS:
# ============================================================
RANGING_PARAMS = {
    "strategy": "VWAP Scalping",
    "entry_mode": "mean_reversion",
    "sd_threshold": 3.5,      # Tuned: optimal deviation threshold
    "atr_stop": 1.5,           # Tuned: balanced stop
    "trailing_atr": 1.5,        # Tuned: proper trailing distance
    "tp_mode": "atr",           # Tuned: ATR-based target
    "volume_mult": 1.0,        # Tuned: max signal capture
    "expected_wr": 0.63,      # 63% historical
    "expected_net": 2.40,      # +240% historical
    "expected_sharpe": 2.19,
}

# ============================================================
# REGIME: BEAR_STRONG (ADX > 25, EMA down, price below EMA)
# Best for VWAP: cross (short breakouts) - 61.6% WR, +4.1%
# ============================================================
BEAR_STRONG_PARAMS = {
    "strategy": "VWAP Scalping",
    "entry_mode": "cross",
    "sd_threshold": 3.0,         # Tuned
    "atr_stop": 1.5,             # Tuned
    "trailing_atr": 1.5,          # Tuned
    "tp_mode": "atr",             # Tuned
    "volume_mult": 1.0,           # Tuned
    "expected_wr": 0.62,        # 62% historical
    "expected_net": 0.04,       # +4% historical
}

# ============================================================
# REGIME: BEAR_WEAK (ADX < 25, EMA flat/slightly down)
# Best for VWAP: bull_pullback (shorting) - 56.4% WR, +40.9% Net (!)
# ============================================================
BEAR_WEAK_PARAMS = {
    "strategy": "VWAP Scalping",
    "entry_mode": "bull_pullback",  # Shorting works in bear
    "sd_threshold": 3.0,          # Tuned
    "atr_stop": 1.5,              # Tuned
    "trailing_atr": 1.5,          # Tuned
    "tp_mode": "atr",              # Tuned
    "volume_mult": 1.0,           # Tuned
    "expected_wr": 0.56,         # 56% historical
    "expected_net": 0.41,        # +41% historical
    "expected_sharpe": 5.76,
}

# ============================================================
# REGIME: BULL_STRONG (ADX > 25, EMA up, price above EMA)
# Best for VWAP: cross entry (breakouts) - 63.5% WR, +5.3%
# ============================================================
BULL_STRONG_PARAMS = {
    "strategy": "VWAP Scalping",
    "entry_mode": "cross",
    "sd_threshold": 3.0,         # Tuned
    "atr_stop": 1.5,              # Tuned
    "trailing_atr": 1.5,           # Tuned
    "tp_mode": "atr",              # Tuned
    "volume_mult": 1.0,            # Tuned
    "expected_wr": 0.64,         # 64% historical
    "expected_net": 0.05,        # +5% historical
}

# ============================================================
# REGIME: BULL_STRONG - Liquidation Capture (on big moves)
# ============================================================
BULL_STRONG_LIQC_PARAMS = {
    "strategy": "Liquidation Capture",
    "price_threshold": 0.017,   # From optimized LC
    "volume_mult": 1.07,
    "min_consecutive_moves": 2,
    "min_wick_ratio": 2.23,
    "rrr_target": 3.5,
}

# ============================================================
# REGIME: BULL_WEAK (ADX < 25, EMA flat/slightly up)
# Best for VWAP: bull_pullback - 84.6% WR, +3.7%, high Sharpe
# ============================================================
BULL_WEAK_PARAMS = {
    "strategy": "VWAP Scalping",
    "entry_mode": "bull_pullback",  # Highest Sharpe (17.37)
    "sd_threshold": 3.0,         # Tuned
    "atr_stop": 1.5,             # Tuned
    "trailing_atr": 1.5,          # Tuned
    "tp_mode": "atr",             # Tuned
    "volume_mult": 1.0,           # Tuned
    "expected_wr": 0.85,        # 85% historical
    "expected_net": 0.04,        # +4% historical
    "expected_sharpe": 17.37,
}

# ============================================================
# REGIME SWITCHING LOGIC
# ============================================================

def get_regime_params(regime_type: str) -> dict:
    """
    Return optimized parameters for the given regime type.
    
    Args:
        regime_type: One of RANGING, BEAR_STRONG, BEAR_WEAK, BULL_STRONG, BULL_WEAK
        
    Returns:
        dict: Parameter dict for the regime
    """
    regime_map = {
        "RANGING": RANGING_PARAMS,
        "BEAR_STRONG": BEAR_STRONG_PARAMS,
        "BEAR_WEAK": BEAR_WEAK_PARAMS,
        "BULL_STRONG": BULL_STRONG_PARAMS,
        "BULL_WEAK": BULL_WEAK_PARAMS,
    }
    return regime_map.get(regime_type, RANGING_PARAMS)


def calculate_position_size(regime_type: str, base_size_pct: float) -> float:
    """
    Adjust position size based on regime confidence.
    
    - RANGING: Full size (highest confidence)
    - BEAR_STRONG: Full size (high confidence)
    - BEAR_WEAK: Full size (moderate confidence)
    - BULL_WEAK: Half size (uncertain)
    - BULL_STRONG: Reduced (only on liquidations)
    """
    size_map = {
        "RANGING": 1.0,
        "BEAR_STRONG": 1.0,
        "BEAR_WEAK": 1.0,
        "BULL_WEAK": 0.5,  # Half size
        "BULL_STRONG": 0.3,  # Minimal - only LC triggers
    }
    multiplier = size_map.get(regime_type, 0.5)
    return base_size_pct * multiplier


# ============================================================
# ON-THE-FLY TUNING FRAMEWORK V6 - COMPREHENSIVE
# ============================================================
# Focus on entry modes that trade BOTH directions:
# - mean_reversion: fade extremes (long at lows, short at highs)
# - cross: breakout trades (long breakouts, short breakdowns)

# Regime-specific entry modes (BOTH SIDES):
REGIME_TO_ENTRY_MODE = {
    "RANGING": "mean_reversion",     # Fades both extremes
    "BEAR_WEAK": "mean_reversion",  # Both sides in weak bear
    "BEAR_STRONG": "cross",         # Breakouts in strong bear
    "BULL_WEAK": "mean_reversion",  # Both sides in weak bull
    "BULL_STRONG": "cross",        # Breakouts in strong bull
    "default": "mean_reversion",    # Default to both-sided
}

class ComprehensiveTuner:
    """
    V6: Comprehensive multi-parameter adaptation.
    
    Adjusts:
    1. SD threshold (entry selectivity)
    2. ATR stop (risk management)
    3. Entry mode (REGIME-SPECIFIC - not fixed!)
    4. Position size (risk exposure)
    5. Cooldown (frequency control)
    
    Key: Uses correct entry mode per regime from our analysis!
    """
    
    def __init__(self, regime: str = "default"):
        self.regime = regime
        
        # Base parameters (proven good)
        self.sd = 3.5
        self.atr_stop = 2.0
        self.entry_mode = REGIME_TO_ENTRY_MODE.get(regime, "bull_pullback")  # REGIME-SPECIFIC!
        self.position_size = 1.0
        self.cooldown = 20
        self.volume_mult = 1.0
        
        # Track performance
        self.trade_log: list[dict] = []
        self.win_streak = 0
        self.loss_streak = 0
        
        # Adaptation thresholds
        self.WR_GOOD = 0.55
        self.WR_BAD = 0.40
        self.MIN_TRADES = 15
        
    def record(self, won: bool, pnl_pct: float, volatility: float = 0.0):
        """Record trade with full context."""
        self.trade_log.append({
            "won": won,
            "pnl": pnl_pct,
            "volatility": volatility,
        })
        self.trade_log = self.trade_log[-50:]
        
        # Track streaks
        if won:
            self.win_streak += 1
            self.loss_streak = 0
        else:
            self.loss_streak += 1
            self.win_streak = 0
            
    def get_params(self) -> dict:
        """Get adapted parameters."""
        if len(self.trade_log) < self.MIN_TRADES:
            return self._base_params()
            
        wr = self._calc_wr()
        recent_pnl = self._calc_recent_pnl()
        avg_vol = self._calc_avg_volatility()
        
        # Adapt each parameter based on conditions
        
        # 1. SD - based on WR
        if wr > self.WR_GOOD:
            self.sd = max(3.0, self.sd - 0.2)  # Looser = more trades
        elif wr < self.WR_BAD:
            self.sd = min(5.0, self.sd + 0.3)   # Tighter = quality
        # Middle: no change
        
        # 2. ATR Stop - based on volatility
        if avg_vol > 0.02:  # High vol
            self.atr_stop = min(3.0, self.atr_stop + 0.3)
        elif avg_vol < 0.01:  # Low vol
            self.atr_stop = max(1.5, self.atr_stop - 0.2)
            
        # 3. Entry mode - based on streaks
        if self.loss_streak >= 3:
            # After 3 losses, switch to safer mode
            self.entry_mode = "mean_reversion"
        elif self.win_streak >= 3:
            # After 3 wins, can be more aggressive
            self.entry_mode = "bull_pullback"
            
        # 4. Position size - based on recent P&L
        if recent_pnl < -0.10:  # Down 10%
            self.position_size = max(0.3, self.position_size - 0.2)
        elif recent_pnl > 0.10:  # Up 10%
            self.position_size = min(1.5, self.position_size + 0.1)
            
        # 5. Cooldown - based on WR
        if wr < self.WR_BAD:
            self.cooldown = min(60, self.cooldown + 5)
        elif wr > self.WR_GOOD:
            self.cooldown = max(10, self.cooldown - 5)
            
        return {
            "sd_threshold": self.sd,
            "atr_stop": self.atr_stop,
            "entry_mode": self.entry_mode,
            "position_size_pct": self.position_size,
            "cooldown_minutes": self.cooldown,
            "volume_mult": self.volume_mult,
            "tp_mode": "atr",
            "trailing_atr": 1.5,
        }
    
    def _base_params(self) -> dict:
        return {
            "sd_threshold": self.sd,
            "atr_stop": self.atr_stop,
            "entry_mode": self.entry_mode,
            "position_size_pct": self.position_size,
            "cooldown_minutes": self.cooldown,
            "volume_mult": self.volume_mult,
            "tp_mode": "atr",
            "trailing_atr": 1.5,
        }
    
    def _calc_wr(self) -> float:
        if not self.trade_log:
            return 0.5
        recent = self.trade_log[-self.MIN_TRADES:]
        return sum(1 for t in recent if t["won"]) / len(recent)
    
    def _calc_recent_pnl(self) -> float:
        if not self.trade_log:
            return 0.0
        return sum(t["pnl"] for t in self.trade_log[-10:]) / 10
    
    def _calc_avg_volatility(self) -> float:
        if not self.trade_log:
            return 0.015
        vols = [t["volatility"] for t in self.trade_log if t.get("volatility", 0) > 0]
        if not vols:
            return 0.015
        return sum(vols) / len(vols)
    
    def get_stats(self) -> dict:
        return {
            "wr": self._calc_wr(),
            "trades": len(self.trade_log),
            "pnl": self._calc_recent_pnl(),
            "sd": self.sd,
            "atr_stop": self.atr_stop,
            "entry": self.entry_mode,
            "position_size": self.position_size,
            "cooldown": self.cooldown,
            "win_streak": self.win_streak,
            "loss_streak": self.loss_streak,
        }


def get_comprehensive_tuner(regime: str = "default") -> ComprehensiveTuner:
    """Get a comprehensive tuner."""
    return ComprehensiveTuner(regime)
        
    def record_trade(self, won: bool, pnl_pct: float):
        """Record trade result for adaptive tuning."""
        self.performance_history.append({"won": won, "pnl_pct": pnl_pct})
        self.performance_history = self.performance_history[-30:]
        
    def get_adapted_params(self) -> dict:
        """Return parameters with regime-appropriate entry mode."""
        adapted = {
            "sd_threshold": self.current_sd,
            "atr_stop": self.current_atr,
            "entry_mode": self.entry_mode,  # USE THE RIGHT MODE!
            "tp_mode": "atr",
            "trailing_atr": 1.5,
            "volume_mult": 1.0,
        }
        
        # Adapt based on WR (but keep entry mode!)
        if len(self.performance_history) >= self.MIN_TRADES:
            wr = self.get_current_wr()
            
            if wr > self.WR_GOOD:
                # Good WR - can tighten slightly for more signals
                self.current_sd = max(self.MIN_SD, self.current_sd - 0.1)
            elif wr < self.WR_BAD:
                # Bad WR - loosen for quality
                self.current_sd = min(self.MAX_SD, self.current_sd + 0.2)
                self.current_atr = min(self.MAX_ATR, self.current_atr + 0.2)
                
            adapted["sd_threshold"] = self.current_sd
            adapted["atr_stop"] = self.current_atr
            
        return adapted
    
    def get_current_wr(self) -> float:
        """Get recent win rate."""
        if not self.performance_history:
            return 0.5
        recent = self.performance_history[-15:]
        return sum(1 for t in recent if t["won"]) / len(recent)
    
    def get_stats(self) -> dict:
        """Get tuning stats."""
        if not self.performance_history:
            return {"wr": 0.5, "trades": 0, "pnl": 0, "mode": self.entry_mode}
        recent = self.performance_history[-20:]
        return {
            "wr": sum(1 for t in recent if t["won"]) / len(recent),
            "trades": len(self.performance_history),
            "pnl": sum(t["pnl_pct"] for t in recent),
            "mode": self.entry_mode,
            "sd": self.current_sd,
        }


def get_tuner_for_regime(regime_type: str) -> ComprehensiveTuner:
    """Get a pre-configured tuner for the given regime."""
    return ComprehensiveTuner(regime_type)


# ============================================================
# SUMMARY TABLE - VWAP Regime-Entry Optimization
# ============================================================

REGIME_SUMMARY = """
| Regime      | VWAP Entry Mode     | WR%  | Net%   | Sharpe |
|------------|-------------------|------|--------|--------|
| RANGING     | mean_reversion    | 63%  | +241%  | 2.19   |
| BEAR_WEAK   | bull_pullback   | 56%  | +41%   | 5.76   |
| BULL_WEAK   | bull_pullback   | 85%  | +3.7%  | 17.37  |
| BULL_STRONG | cross          | 64%  | +5.3%  | -0.07  |
| BEAR_STRONG | cross          | 62%  | +4.1%  | -1.87  |

Key: RANGING + mean_reversion = +241% is the GOLD MINE
"""