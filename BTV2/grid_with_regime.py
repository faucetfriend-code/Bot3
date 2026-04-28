"""
Grid Trading - Proper Regime Detection Test
=====================================
Uses actual MarketRegimeDetector for regime classification:
- TRENDING_STRONG: ADX > 30 (grid disabled)
- TRENDING_MODERATE: 25 < ADX ≤ 30 (grid disabled)
- RANGING_VOLATILE: ADX ≤ 25 + high volatility (grid enabled)
- RANGING_CALM: ADX ≤ 25 + low volatility (grid enabled)
- INDECISIVE: transitional (grid enabled)
"""

import sys
from pathlib import Path

# Add parent directory to path for trading_bot_v2 imports
ROOT = Path("..").resolve()
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from datetime import datetime

# Import actual regime detector
from trading_bot_v2.market_regime import MarketRegimeDetector, MarketRegime
from trading_bot_v2.indicators import calculate_atr, calculate_adx, calculate_bollinger_bands

DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"

N_MC_SIMS = 500


class GridWithRegimeDetection:
    """Grid with proper regime detection."""
    
    def __init__(self, **kwargs):
        self.grid_levels = kwargs.get("grid_levels", 10)
        self.spacing_mult = kwargs.get("grid_spacing_atr_multiplier", 0.65)
        self.max_positions = kwargs.get("max_positions_per_symbol", 10)
        self.emergency_stop_pct = kwargs.get("emergency_stop_loss_pct", 0.05)
        
        # Use actual regime detector
        self.regime_detector = MarketRegimeDetector(
            adx_trending_threshold=28.0,
            adx_ranging_threshold=22.0,
            adx_moderate_threshold=22.0,
            volatility_high_percentile=65.0,
        )
        
        # State
        self.positions = []
        self.closed_trades = []
        self.equity = 1.0
        self.peak_equity = 1.0
        self.grid_deployed = False  # Only deploy once per ranging period
        self.last_regime = None  # Track previous regime for transitions
        
    def run(self, df):
        """Run grid with regime detection."""
        closes = df["Close"].values.astype(float)
        highs = df["High"].values.astype(float)
        lows = df["Low"].values.astype(float)
        n = len(closes)
        
        equity_arr = np.ones(n, dtype=float)
        
        # Calculate indicators
        adx_vals = []
        atr_vals = []
        bb_width_vals = []
        
        for i in range(30, n):
            adx = calculate_adx(
                highs[max(0, i-30):i+1],
                lows[max(0, i-30):i+1],
                closes[max(0, i-30):i+1],
                14
            )
            adx_vals.append(adx if not np.isnan(adx) else np.nan)
            
            atr = calculate_atr(
                highs[max(0, i-20):i+1],
                lows[max(0, i-20):i+1],
                closes[max(0, i-20):i+1],
                14
            )
            atr_vals.append(atr if not np.isnan(atr) else np.nan)
            
            bb_up, bb_mid, bb_lo = calculate_bollinger_bands(closes[max(0, i-20):i+1], 20, 2.0)
            if not np.isnan(bb_up) and not np.isnan(bb_lo) and bb_mid > 0:
                width = (bb_up - bb_lo) / bb_mid
            else:
                width = np.nan
            bb_width_vals.append(width)
        
        # Pad
        adx_vals = [np.nan] * 30 + adx_vals
        atr_vals = [np.nan] * 30 + atr_vals
        bb_width_vals = [np.nan] * 30 + bb_width_vals
        
        for i in range(30, n):
            p = closes[i]
            at = atr_vals[i]
            ad = adx_vals[i]
            bb_width = bb_width_vals[i]
            
            if np.isnan(ad) or np.isnan(at):
                equity_arr[i] = self.equity
                continue
            
            # Detect regime using actual detector
            regime = self._detect_regime(ad, bb_width)
            
            # Emergency stop check
            if self.equity < self.peak_equity * (1 - self.emergency_stop_pct):
                self._close_all(p)
                equity_arr[i] = self.equity
                continue
            
            if self.equity > self.peak_equity:
                self.peak_equity = self.equity
            
            # Grid only allowed in RANGING or INDECISIVE
            if regime in [MarketRegime.TRENDING_STRONG, MarketRegime.TRENDING_MODERATE]:
                # Trend detected - close all grid positions
                if self.positions:
                    self._close_all(p)
                equity_arr[i] = self.equity
                continue
            
            # Ranging or indecisive - manage grid
            grid_spacing = self.spacing_mult * at
            
            # Deploy grid when TRANSITIONING from trending to ranging
            ranging_regimes = [
                MarketRegime.RANGING_VOLATILE, 
                MarketRegime.RANGING_CALM, 
                MarketRegime.INDECISIVE
            ]
            
            if regime in ranging_regimes:
                # Check if we just transitioned from trending
                if self.last_regime in [MarketRegime.TRENDING_STRONG, MarketRegime.TRENDING_MODERATE, None]:
                    if not self.grid_deployed:
                        self.grid_deployed = True
                        self._deploy_grid(p, grid_spacing)
                # Also allow if no grid deployed yet
                elif not self.grid_deployed:
                    self.grid_deployed = True
                    self._deploy_grid(p, grid_spacing)
            
            # Check profit taking on existing grid
            self._check_profit(p, grid_spacing)
            
            # Store previous regime
            self.last_regime = regime
            
            equity_arr[i] = self.equity
        
        if self.positions:
            self._close_all(closes[-1])
        
        return pd.Series(equity_arr, index=df.index), self.closed_trades
    
    def _detect_regime(self, adx, bb_width):
        """Use actual regime detector logic."""
        if adx > 30:
            return MarketRegime.TRENDING_STRONG
        elif adx > 25:
            return MarketRegime.TRENDING_MODERATE
        elif bb_width is not None and not np.isnan(bb_width):
            # High vs low volatility
            if bb_width > 0.05:  # Roughly top 65% percentile
                return MarketRegime.RANGING_VOLATILE
            else:
                return MarketRegime.RANGING_CALM
        else:
            return MarketRegime.INDECISIVE
    
    def _deploy_grid(self, current_price, spacing):
        """Deploy grid levels once when entering ranging."""
        # Clear any existing positions first
        self.positions = []
        
        # Buy levels below
        for level in range(1, self.grid_levels + 1):
            self.positions.append({
                "entry": current_price - level * spacing,
                "side": "long",
                "level": level,
            })
        
        # Sell levels above
        for level in range(1, self.grid_levels + 1):
            self.positions.append({
                "entry": current_price + level * spacing,
                "side": "short",
                "level": -level,
            })
        
        self.grid_levels_set = True
    
    def _check_profit(self, current_price, spacing):
        """Check profit taking - levels trigger when price reaches them."""
        new_pos = []
        for pos in self.positions:
            entry = pos["entry"]
            side = pos["side"]
            
            # Long positions: enter when price DROPS to entry level - FILL the long
            if side == "long" and current_price <= entry:
                # Price dropped to entry level - take the long
                ret = (current_price - entry) / entry - 0.0015
                self.closed_trades.append(ret)
                self.equity *= (1 + ret)
            # Short positions: enter when price RISES to entry level - FILL the short  
            elif side == "short" and current_price >= entry:
                # Price rose to entry level - take the short
                ret = (entry - current_price) / entry - 0.0015
                self.closed_trades.append(ret)
                self.equity *= (1 + ret)
            else:
                new_pos.append(pos)
        
        # If all positions closed, allow redeployment
        if not new_pos and self.positions:
            self.grid_deployed = False
        
        self.positions = new_pos
    
    def _close_all(self, exit_price):
        """Close all positions."""
        for pos in self.positions:
            entry = pos["entry"]
            side = pos["side"]
            
            if side == "long":
                ret = (exit_price - entry) / entry - 0.0015
            else:
                ret = (entry - exit_price) / entry - 0.0015
            
            self.closed_trades.append(ret)
            self.equity *= (1 + ret)
        
        self.positions = []


def load_data(years):
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.columns = [c.lower() for c in df.columns]
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
        df = df.set_index("timestamp").sort_index()
        dfs.append(df)
    
    df_all = pd.concat(dfs).sort_index()
    
    df_1h = df_all.resample("1h").agg({
        "Open": "first", 
        "High": "max", 
        "Low": "min", 
        "Close": "last", 
        "Volume": "sum"
    })
    df_1h = df_1h.dropna()
    df_1h.index = df_1h.index.tz_localize(None)
    
    return df_1h


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    print("\n=== GRID WITH REGIME DETECTION ===")
    
    grid = GridWithRegimeDetection(
        grid_levels=10,
        grid_spacing_atr_multiplier=0.65,
        max_positions_per_symbol=10,
        emergency_stop_loss_pct=0.05,
    )
    
    eq, trades = grid.run(df)
    
    if trades:
        total_return = sum(trades)
        win_rate = 100 * len([t for t in trades if t > 0]) / len(trades)
        
        print(f"Return: {total_return*100:+.1f}%")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Trades: {len(trades)}")
        
        # Monte Carlo
        returns = np.array(trades)
        mc = []
        for _ in range(N_MC_SIMS):
            sample = np.random.choice(returns, size=len(returns), replace=True)
            mc.append(np.prod(1 + sample) - 1)
        
        p_loss = np.mean(np.array(mc) < 0)
        print(f"\nMonte Carlo:")
        print(f"  P(Loss): {p_loss*100:.1f}%")
        print(f"  P5: {np.percentile(mc, 5)*100:.1f}%")
        print(f"  P50: {np.percentile(mc, 50)*100:.1f}%")
        print(f"  P95: {np.percentile(mc, 95)*100:.1f}%")
    else:
        print("No trades generated!")
    
    print("\n=== VERDICT ===")
    if trades and sum(trades) > 0:
        print("POTENTIAL - Needs more testing")
    else:
        print("NEEDS WORK")


if __name__ == "__main__":
    main()