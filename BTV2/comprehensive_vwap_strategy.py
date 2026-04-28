"""
comprehensive_vwap_strategy.py — Clean SFP + Volume VWAP Strategy
=================================================================

Simplified strategy focusing ONLY on:
- SFP (Smart Fair Price) pattern: Wick swept band, close back toward VWAP
- Volume surge: Volume > 20-period MA

Hypothesis: SFP + Volume = high quality entries, nothing else needed.

Entry Rules:
-----------
LONG: SFP_long AND Volume_surge
SHORT: SFP_short AND Volume_surge

Where:
- SFP_long: Low touched below lower band AND close > lower band (rejection)
- SFP_short: High touched above upper band AND close < upper band (rejection)
- Volume_surge: Current volume > 20-bar MA

Exit Rules:
-----------
- SL = ATR × atr_multiplier
- TP = VWAP (mean reversion target) OR 2×SL for RR

Testing:
--------
    python comprehensive_vwap_strategy.py --test
    python comprehensive_vwap_strategy.py --test --sd 1.5 --atr 1.0 --period "2024-11-01"
"""

from __future__ import annotations

import argparse
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

import pandas as pd

# Ensure BTV2 is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    compute_atr,
    compute_vwap_anchored,
    compute_sma,
    COST_PER_SIDE,
)


# ============================================================================
# CONSTANTS
# ============================================================================

ROUND_TRIP_COST = COST_PER_SIDE * 2
DATA_DIR = Path("trading_bot_v2/backtesting/data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class TradeOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"


@dataclass
class SFPVolumeConfig:
    """
    Configuration for SFP + Volume VWAP strategy.
    
    Only parameters that matter for quality:
    - sd_threshold: SD bands (2.0 = outer band)
    - atr_multiplier: SL = ATR × this
    - volume_ma_period: Volume MA period
    - require_sfp: MUST have SFP
    - require_volume: MUST have volume surge
    """
    # Core parameters
    sd_threshold: float = 2.0
    atr_multiplier: float = 1.5
    volume_ma_period: int = 20
    
    # Filter requirements
    require_sfp: bool = True
    require_volume: bool = True
    
    # Risk management
    base_position_size: float = 0.3
    cooldown_seconds: int = 300
    
    # Take profit mode: "vwap" (mean reversion) or "rr" (2:1 risk reward)
    tp_mode: str = "vwap"

    def __post_init__(self):
        if self.sd_threshold < 0.5 or self.sd_threshold > 4.0:
            raise ValueError(f"sd_threshold must be 0.5-4.0, got {self.sd_threshold}")
        if self.atr_multiplier < 0.5 or self.atr_multiplier > 3.0:
            raise ValueError(f"atr_multiplier must be 0.5-3.0, got {self.atr_multiplier}")
        if self.volume_ma_period < 5 or self.volume_ma_period > 100:
            raise ValueError(f"volume_ma_period must be 5-100, got {self.volume_ma_period}")
        if self.tp_mode not in ("vwap", "rr"):
            raise ValueError(f"tp_mode must be 'vwap' or 'rr', got {self.tp_mode}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sd_threshold": self.sd_threshold,
            "atr_multiplier": self.atr_multiplier,
            "volume_ma_period": self.volume_ma_period,
            "require_sfp": self.require_sfp,
            "require_volume": self.require_volume,
            "base_position_size": self.base_position_size,
            "cooldown_seconds": self.cooldown_seconds,
            "tp_mode": self.tp_mode,
        }


# ============================================================================
# SFP + VOLUME VWAP STRATEGY
# ============================================================================


class SFPVolumeVWAPStrategy:
    """
    Clean VWAP strategy using ONLY SFP + Volume filters.
    
    Entry requires BOTH:
    - SFP pattern (wick swept band, close back toward VWAP)
    - Volume surge (volume > 20-bar MA)
    
    No RSI, no EMA, no ADX - just SFP + Volume.
    """
    
    def __init__(
        self,
        config: SFPVolumeConfig,
        symbol: str = "BTC-USDC",
    ):
        self.config = config
        self.symbol = symbol
        self.last_trade_time: Optional[datetime] = None
        
    def generate_signal(
        self,
        df: pd.DataFrame,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate trading signal based on SFP + Volume only.
        
        Args:
            df: Market data with OHLCV
            
        Returns:
            Signal dict or None
        """
        # Check cooldown
        if self.last_trade_time is not None:
            elapsed = datetime.now() - self.last_trade_time
            if elapsed.total_seconds() < self.config.cooldown_seconds:
                return None
        
        signal = self._calculate_sfp_volume_signal(df)
        
        if signal is not None:
            signal["position_size"] = self.config.base_position_size
            logger.info(
                f"SIGNAL | {signal['side'].upper()} | "
                f"Entry: {signal['entry_price']:.2f} | "
                f"SL: {signal['stop_loss']:.2f} | TP: {signal['take_profit']:.2f}"
            )
        
        return signal
    
    def _calculate_sfp_volume_signal(
        self,
        df: pd.DataFrame,
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate VWAP signal using ONLY SFP + Volume filters.
        
        Entry Requirements (ALL must be met):
        -------------------------------------
        - SFP_long: Low touched below lower band AND close > lower band
        - SFP_short: High touched above upper band AND close < upper band  
        - Volume_surge: Current volume > volume_ma_period MA
        - require_sfp and require_volume filters applied
        
        Take Profit / Stop Loss:
        -------------------------
        - TP = VWAP (mean reversion) OR entry + (SL_distance × 2) for RR mode
        - SL = entry -/+ ATR × atr_multiplier
        
        Args:
            df: Market data with OHLCV
            
        Returns:
            Signal dict or None
        """
        if len(df) < 50:
            return None
        
        # Extract price data
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]
        
        current_price = close.iloc[-1]
        prev_close = close.iloc[-2]
        
        # Calculate VWAP and bands
        try:
            vwap, vwap_std = compute_vwap_anchored(high, low, close, volume)
        except Exception:
            return None
        
        current_vwap = vwap.iloc[-1]
        current_std = vwap_std.iloc[-1]
        
        if pd.isna(current_vwap) or pd.isna(current_std) or current_std == 0:
            return None
        
        # Calculate deviation from VWAP
        deviation = (current_price - current_vwap) / current_std
        
        # Calculate bands
        upper_band = current_vwap + self.config.sd_threshold * current_std
        lower_band = current_vwap - self.config.sd_threshold * current_std
        
        # Get ATR for stop loss
        atr = compute_atr(high, low, close).iloc[-1]
        if pd.isna(atr) or atr == 0:
            return None
        
        # Calculate volume MA and check for volume surge
        vol_ma = compute_sma(volume, period=self.config.volume_ma_period)
        volume_surge = False
        if len(vol_ma) > 0 and not pd.isna(vol_ma.iloc[-1]):
            volume_surge = volume.iloc[-1] > vol_ma.iloc[-1]
        
        # Check volume filter
        if self.config.require_volume and not volume_surge:
            return None
        
        # =====================================================
        # SFP Pattern Detection
        # =====================================================
        # SFP_long: Low swept below lower band, close above band, AND close is moving toward VWAP
        # SFP_short: High swept above upper band, close below band, AND close is moving toward VWAP
        
        # Calculate price movement toward VWAP
        # For LONG: close should be closer to VWAP than the low that swept
        # For SHORT: close should be closer to VWAP than the high that swept
        deviation_current = deviation
        deviation_at_low = (low.iloc[-1] - current_vwap) / current_std if current_std > 0 else 0
        deviation_at_high = (high.iloc[-1] - current_vwap) / current_std if current_std > 0 else 0
        
        # SFP_long: Low swept below band AND close is recovering toward VWAP
        sfp_long = (
            low.iloc[-1] < lower_band  # Wick swept below lower band
            and close.iloc[-1] > lower_band  # Closed above lower band (rejection)
            and deviation_current > deviation_at_low  # Now closer to VWAP than the low
        )
        
        # SFP_short: High swept above band AND close is moving toward VWAP
        sfp_short = (
            high.iloc[-1] > upper_band  # Wick swept above upper band
            and close.iloc[-1] < upper_band  # Closed below upper band (rejection)
            and deviation_current < deviation_at_high  # Now closer to VWAP than the high
        )
        
        # Check SFP filter
        if self.config.require_sfp and not (sfp_long or sfp_short):
            return None
        
        # =====================================================
        # Entry Generation
        # =====================================================
        
        # Calculate stop loss distance
        sl_distance = atr * self.config.atr_multiplier
        
        # LONG entry: SFP_long + Volume_surge
        if sfp_long and (not self.config.require_volume or volume_surge):
            # Take profit: VWAP (mean reversion) or 2:1 RR
            if self.config.tp_mode == "vwap":
                take_profit = current_vwap  # Mean reversion to VWAP
            else:
                take_profit = current_price + sl_distance * 2  # 2:1 RR
            
            # Confidence based on how extreme the deviation is
            confidence = min(abs(deviation) / self.config.sd_threshold, 1.0)
            
            return {
                "side": "long",
                "action": "buy",
                "entry_price": current_price,
                "stop_loss": current_price - sl_distance,
                "take_profit": take_profit,
                "confidence": confidence,
                "sfp": True,
                "volume_surge": volume_surge,
                "deviation": deviation,
                "tp_mode": self.config.tp_mode,
            }
        
        # SHORT entry: SFP_short + Volume_surge
        if sfp_short and (not self.config.require_volume or volume_surge):
            # Take profit: VWAP (mean reversion) or 2:1 RR
            if self.config.tp_mode == "vwap":
                take_profit = current_vwap  # Mean reversion to VWAP
            else:
                take_profit = current_price - sl_distance * 2  # 2:1 RR
            
            # Confidence based on how extreme the deviation is
            confidence = min(abs(deviation) / self.config.sd_threshold, 1.0)
            
            return {
                "side": "short",
                "action": "sell",
                "entry_price": current_price,
                "stop_loss": current_price + sl_distance,
                "take_profit": take_profit,
                "confidence": confidence,
                "sfp": True,
                "volume_surge": volume_surge,
                "deviation": deviation,
                "tp_mode": self.config.tp_mode,
            }
        
        return None
    
    def record_trade_result(
        self,
        entry_price: float,
        exit_price: float,
        direction: str,
    ) -> None:
        """Record trade for tracking."""
        self.last_trade_time = datetime.now()


# ============================================================================
# BACKTESTING
# ============================================================================


@dataclass
class BacktestResult:
    """Results from a single backtest run."""
    config: Dict[str, Any]
    metrics: Dict[str, float]
    trades: List[Dict[str, Any]]


class SFPVolumeTester:
    """Testing utilities for SFP + Volume strategy."""
    
    def __init__(
        self,
        symbol: str = "BTC-USDC",
        data_dir: Optional[Path] = None,
    ):
        self.symbol = symbol
        self.data_dir = data_dir or DATA_DIR
    
    def load_data(
        self,
        timeframe: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Load candle data for testing."""
        data_paths = [
            self.data_dir / f"{self.symbol.replace('-', '_')}_{timeframe}.parquet",
            self.data_dir / f"{self.symbol}_{timeframe}.parquet",
            self.data_dir / f"{self.symbol.replace('-', '')}_{timeframe}.csv",
            self.data_dir / f"{self.symbol}_{timeframe}.csv",
        ]
        
        for path in data_paths:
            if path.exists():
                logger.info(f"Loading data from: {path}")
                df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(
                    path, index_col=0, parse_dates=False
                )
                
                # Normalize column names
                df.columns = [c.capitalize() for c in df.columns]
                
                # Parse timestamp
                if "Timestamp" in df.columns:
                    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
                    df = df.set_index("Timestamp")
                elif not pd.api.types.is_datetime64_any_dtype(df.index):
                    df.index = pd.to_datetime(df.index)
                
                # Filter date range
                df = df[start:end]  # type: ignore
                return df
        
        raise FileNotFoundError(f"No data found for {self.symbol} {timeframe}")
    
    def run_backtest(
        self,
        config: SFPVolumeConfig,
        start: str,
        end: str,
        timeframe: str = "5m",
    ) -> BacktestResult:
        """
        Run backtest for the strategy.
        
        Args:
            config: Strategy configuration
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            timeframe: Data timeframe
            
        Returns:
            BacktestResult with metrics and trades
        """
        logger.info(f"Running backtest: {start} to {end}")
        logger.info(f"Config: sd={config.sd_threshold}, atr={config.atr_multiplier}, "
                   f"vol_period={config.volume_ma_period}, require_sfp={config.require_sfp}, "
                   f"require_volume={config.require_volume}, tp_mode={config.tp_mode}")
        
        # Load data
        df = self.load_data(timeframe, start, end)
        
        if len(df) < 50:
            return BacktestResult(
                config=config.to_dict(),
                metrics={"win_rate": 0.0, "total_trades": 0, "return_pct": 0.0},
                trades=[],
            )
        
        # Create strategy
        strategy = SFPVolumeVWAPStrategy(config)
        
        # Get price data
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]
        
        # Pre-calculate VWAP
        try:
            vwap, vwap_std = compute_vwap_anchored(high, low, close, volume)
        except Exception:
            return BacktestResult(
                config=config.to_dict(),
                metrics={"win_rate": 0.0, "total_trades": 0, "return_pct": 0.0},
                trades=[],
            )
        
        # Run backtest
        trades = []
        position = None
        
        for i in range(50, len(df)):
            # Get current bar data
            current_df = df.iloc[: i + 1]
            current_price = close.iloc[i]
            current_high = high.iloc[i]
            current_low = low.iloc[i]
            current_volume = volume.iloc[i]
            
            # Get current VWAP values
            current_vwap = vwap.iloc[i]
            current_std = vwap_std.iloc[i]
            
            if pd.isna(current_vwap) or pd.isna(current_std) or current_std == 0:
                continue
            
            # Calculate bands
            upper_band = current_vwap + config.sd_threshold * current_std
            lower_band = current_vwap - config.sd_threshold * current_std
            
            # Get ATR
            atr = compute_atr(high, low, close).iloc[i]
            if pd.isna(atr) or atr == 0:
                continue
            
            # Calculate volume MA and check surge
            vol_ma = compute_sma(volume, period=config.volume_ma_period).iloc[i]
            volume_surge = not pd.isna(vol_ma) and current_volume > vol_ma
            
            # Check volume filter
            if config.require_volume and not volume_surge:
                continue
            
            # Check SFP pattern with recovery check
            deviation_current = (current_price - current_vwap) / current_std if current_std > 0 else 0
            deviation_at_low = (current_low - current_vwap) / current_std if current_std > 0 else 0
            deviation_at_high = (current_high - current_vwap) / current_std if current_std > 0 else 0
            
            sfp_long = (
                current_low < lower_band
                and close.iloc[i] > lower_band
                and deviation_current > deviation_at_low
            )
            sfp_short = (
                current_high > upper_band
                and close.iloc[i] < upper_band
                and deviation_current < deviation_at_high
            )
            
            # Check SFP filter
            if config.require_sfp and not (sfp_long or sfp_short):
                continue
            
            # If no position, check for entry
            if position is None:
                sl_distance = atr * config.atr_multiplier
                
                # LONG entry
                if sfp_long and (not config.require_volume or volume_surge):
                    tp = current_vwap if config.tp_mode == "vwap" else current_price + sl_distance * 2
                    
                    position = {
                        "entry_idx": i,
                        "entry_price": current_price,
                        "side": "long",
                        "stop_loss": current_price - sl_distance,
                        "take_profit": tp,
                    }
                    
                # SHORT entry
                elif sfp_short and (not config.require_volume or volume_surge):
                    tp = current_vwap if config.tp_mode == "vwap" else current_price - sl_distance * 2
                    
                    position = {
                        "entry_idx": i,
                        "entry_price": current_price,
                        "side": "short",
                        "stop_loss": current_price + sl_distance,
                        "take_profit": tp,
                    }
            
            # Check for exit
            if position is not None:
                exit_reason = None
                exit_price_val = None
                
                # LONG exit
                if position["side"] == "long":
                    # Stop loss
                    if current_price <= position["stop_loss"]:
                        exit_price_val = position["stop_loss"]
                        exit_reason = "stop_loss"
                    # Take profit
                    elif current_price >= position["take_profit"]:
                        exit_price_val = position["take_profit"]
                        exit_reason = "take_profit"
                
                # SHORT exit
                else:
                    # Stop loss
                    if current_price >= position["stop_loss"]:
                        exit_price_val = position["stop_loss"]
                        exit_reason = "stop_loss"
                    # Take profit
                    elif current_price <= position["take_profit"]:
                        exit_price_val = position["take_profit"]
                        exit_reason = "take_profit"
                
                # Process exit
                if exit_reason is not None and exit_price_val is not None:
                    # Calculate P&L
                    if position["side"] == "long":
                        pnl = (exit_price_val - position["entry_price"]) / position["entry_price"]
                    else:
                        pnl = (position["entry_price"] - exit_price_val) / position["entry_price"]
                    
                    # Subtract costs
                    pnl -= ROUND_TRIP_COST
                    
                    trades.append({
                        "entry_idx": position["entry_idx"],
                        "exit_idx": i,
                        "side": position["side"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_val,
                        "pnl": pnl,
                        "reason": exit_reason,
                    })
                    
                    position = None
        
        # Calculate metrics
        if trades:
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] < 0]
            breakeven = [t for t in trades if t["pnl"] <= 0 and t["pnl"] >= -ROUND_TRIP_COST]
            
            total_pnl = sum(t["pnl"] for t in trades)
            win_rate = len(wins) / len(trades) * 100 if trades else 0
            
            metrics = {
                "total_trades": len(trades),
                "win_rate": win_rate,
                "return_pct": total_pnl * 100,
                "avg_pnl": total_pnl / len(trades) if trades else 0,
                "wins": len(wins),
                "losses": len(losses),
                "breakeven": len(breakeven),
            }
        else:
            metrics = {
                "total_trades": 0,
                "win_rate": 0.0,
                "return_pct": 0.0,
                "avg_pnl": 0.0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
            }
        
        logger.info(f"Backtest complete: {metrics['total_trades']} trades, "
                   f"WR={metrics['win_rate']:.1f}%, Return={metrics['return_pct']:.2f}%")
        
        return BacktestResult(
            config=config.to_dict(),
            metrics=metrics,
            trades=trades,
        )
    
    def run_parameter_grid(
        self,
        sd_values: List[float],
        atr_values: List[float],
        start: str,
        end: str,
        timeframe: str = "5m",
        require_sfp: bool = True,
        require_volume: bool = True,
        tp_mode: str = "vwap",
    ) -> List[BacktestResult]:
        """
        Run parameter grid search.
        
        Args:
            sd_values: List of sd_threshold values to test
            atr_values: List of atr_multiplier values to test
            start: Start date
            end: End date
            timeframe: Data timeframe
            require_sfp: Require SFP pattern
            require_volume: Require volume surge
            tp_mode: Take profit mode ("vwap" or "rr")
            
        Returns:
            List of backtest results
        """
        results = []
        
        for sd in sd_values:
            for atr in atr_values:
                config = SFPVolumeConfig(
                    sd_threshold=sd,
                    atr_multiplier=atr,
                    require_sfp=require_sfp,
                    require_volume=require_volume,
                    tp_mode=tp_mode,
                )
                
                result = self.run_backtest(config, start, end, timeframe)
                results.append(result)
                
                logger.info(f"sd={sd}, atr={atr} => "
                           f"Trades={result.metrics['total_trades']}, "
                           f"WR={result.metrics['win_rate']:.1f}%, "
                           f"Return={result.metrics['return_pct']:.2f}%")
        
        return results


# ============================================================================
# MAIN
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="SFP + Volume VWAP Strategy")
    parser.add_argument("--test", action="store_true", help="Run backtest")
    parser.add_argument("--sd", type=float, default=2.0, help="SD threshold (default: 2.0)")
    parser.add_argument("--atr", type=float, default=1.5, help="ATR multiplier (default: 1.5)")
    parser.add_argument("--period", type=str, default="2024-11-01", help="Start period (YYYY-MM-DD)")
    parser.add_argument("--timeframe", type=str, default="5m", help="Timeframe (default: 5m)")
    parser.add_argument("--tp-mode", type=str, default="vwap", choices=["vwap", "rr"], 
                       help="Take profit mode (default: vwap)")
    parser.add_argument("--no-sfp", action="store_true", help="Disable SFP requirement")
    parser.add_argument("--no-volume", action="store_true", help="Disable volume requirement")
    
    args = parser.parse_args()
    
    # Default end date (current date)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = args.period
    
    # Create config
    config = SFPVolumeConfig(
        sd_threshold=args.sd,
        atr_multiplier=args.atr,
        require_sfp=not args.no_sfp,
        require_volume=not args.no_volume,
        tp_mode=args.tp_mode,
    )
    
    # Create tester
    tester = SFPVolumeTester()
    
    # Run backtest
    logger.info("=" * 60)
    logger.info("SFP + Volume VWAP Strategy Backtest")
    logger.info("=" * 60)
    logger.info(f"SD Threshold: {args.sd}")
    logger.info(f"ATR Multiplier: {args.atr}")
    logger.info(f"Period: {start_date} to {end_date}")
    logger.info(f"Require SFP: {not args.no_sfp}")
    logger.info(f"Require Volume: {not args.no_volume}")
    logger.info(f"TP Mode: {args.tp_mode}")
    logger.info("=" * 60)
    
    result = tester.run_backtest(config, start_date, end_date, args.timeframe)
    
    # Print results
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Total Trades:    {result.metrics['total_trades']}")
    print(f"Win Rate:        {result.metrics['win_rate']:.1f}%")
    print(f"Return:          {result.metrics['return_pct']:.2f}%")
    print(f"Avg PnL:         {result.metrics['avg_pnl']*100:.2f}%")
    print(f"Wins:            {result.metrics['wins']}")
    print(f"Losses:          {result.metrics['losses']}")
    print(f"Breakeven:       {result.metrics['breakeven']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
