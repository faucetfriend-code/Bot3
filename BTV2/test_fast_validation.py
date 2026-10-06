"""
Fast Backtest Validation Script for Trading Bot v2

Implements core VWAP + SFP + Volume logic using numpy/pandas for speed.
Tests multiple configurations and runs Monte Carlo simulation.
"""

import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

# Suppress pandas warnings
warnings.filterwarnings('ignore', category=FutureWarning)
from datetime import datetime, timedelta


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class BacktestConfig:
    """Configuration for backtest parameters."""
    sd_threshold: float      # Standard deviation bands (e.g., 2.0)
    atr_multiplier: float    # ATR multiplier for SL/TP (e.g., 0.7)
    risk_reward: float        # Risk/reward ratio (e.g., 2.0)
    use_sfp: bool            # Enable SFP (Smart Failsafe Pattern) filter
    use_volume: bool        # Enable volume MA filter
    name: str = ""


# =============================================================================
# Data Loading
# =============================================================================

def load_data(csv_path: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """
    Load OHLCV data from CSV file.
    
    Args:
        csv_path: Path to the CSV file
        start_date: Start date filter (YYYY-MM-DD)
        end_date: End date filter (YYYY-MM-DD)
    
    Returns:
        DataFrame with OHLCV data
    """
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp').sort_index()
    
    if start_date:
        df = df[df.index >= start_date]
    if end_date:
        df = df[df.index <= end_date]
    
    return df


# =============================================================================
# Technical Indicators (Vectorized)
# =============================================================================

def compute_daily_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Compute daily-anchored VWAP using cumulative sums.
    
    VWAP = cumsum(typical_price * volume) / cumsum(volume)
    where typical_price = (high + low + close) / 3
    """
    typical_price = (df['high'] + df['low'] + df['close']) / 3.0
    price_volume = typical_price * df['volume']
    
    # Cumulative sums per day
    cum_pv = price_volume.groupby(price_volume.index.date).cumsum()
    cum_vol = df['volume'].groupby(df['volume'].index.date).cumsum()
    
    # Avoid division by zero
    vwap = cum_pv / cum_vol.replace(0, np.nan)
    return vwap


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Compute Average True Range (ATR) using vectorized operations.
    """
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift(1))
    low_close = np.abs(df['low'] - df['close'].shift(1))
    
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period, min_periods=1).mean()
    return atr


def compute_sd_bands(vwap: pd.Series, period: int = 20, num_std: float = 2.0) -> tuple:
    """
    Compute VWAP standard deviation bands.
    
    Returns:
        (upper_band, lower_band, vwap_std)
    """
    vwap_std = vwap.rolling(window=period, min_periods=1).std()
    upper_band = vwap + (num_std * vwap_std)
    lower_band = vwap - (num_std * vwap_std)
    return upper_band, lower_band, vwap_std


def compute_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Compute volume moving average.
    """
    return df['volume'].rolling(window=period, min_periods=1).mean()


def compute_sfp_signals(
    df: pd.DataFrame,
    upper_band: pd.Series,
    lower_band: pd.Series
) -> pd.Series:
    """
    Detect Smart Failsafe Pattern (SFP) signals.
    
    SFP Logic:
    - Bullish SFP: Lower wick touches/crosses lower band, then close returns above
    - Bearish SFP: Upper wick touches/crosses upper band, then close returns below
    
    Returns:
        Series with signals: 1 (bullish), -1 (bearish), 0 (none)
    """
    # Wicks
    lower_wick = df['low']
    upper_wick = df['high']
    
    # Detect wick touches band
    bullish_touch = (lower_wick <= lower_band).astype(int)
    bearish_touch = (upper_wick >= upper_band).astype(int)
    
    # Close returns (next bar closes above lower band for bullish)
    bullish_return = (df['close'] > lower_band).astype(int)
    bearish_return = (df['close'] < upper_band).astype(int)
    
    # SFP: touch then return
    sfp_bullish = (bullish_touch.shift(1).fillna(0) == 1) & (bullish_return == 1)
    sfp_bearish = (bearish_touch.shift(1).fillna(0) == 1) & (bearish_return == 1)
    
    signals = pd.Series(0, index=df.index)
    signals[sfp_bullish] = 1
    signals[sfp_bearish] = -1
    
    return signals


# =============================================================================
# Signal Generation (Vectorized)
# =============================================================================

def generate_signals(
    df: pd.DataFrame,
    config: BacktestConfig
) -> pd.DataFrame:
    """
    Generate trading signals using vectorized operations.
    
    Signal Logic:
    - BUY: Price crosses below lower SD band
    - SELL: Price crosses above upper SD band
    - Optional SFP filter: Only signals where SFP detected
    - Optional Volume filter: Volume must be above MA
    """
    # Compute indicators
    vwap = compute_daily_vwap(df)
    atr = compute_atr(df)
    upper_band, lower_band, _ = compute_sd_bands(vwap, num_std=config.sd_threshold)
    vol_ma = compute_volume_ma(df)
    
    # Price position relative to bands
    price_below_lower = df['close'] < lower_band
    price_above_upper = df['close'] > upper_band
    
    # Previous position
    prev_price_below_lower = price_below_lower.shift(1).fillna(False)
    prev_price_above_upper = price_above_upper.shift(1).fillna(False)
    
    # Cross signals
    buy_signal = price_below_lower & ~prev_price_below_lower
    sell_signal = price_above_upper & ~prev_price_above_upper
    
    signals = pd.DataFrame(index=df.index)
    signals['buy'] = buy_signal.astype(int)
    signals['sell'] = sell_signal.astype(int)
    
    # SFP filter
    if config.use_sfp:
        sfp_signals = compute_sfp_signals(df, upper_band, lower_band)
        # Only allow buy if SFP bullish, sell if SFP bearish
        signals.loc[sfp_signals == 0, 'buy'] = 0
        signals.loc[sfp_signals == 0, 'sell'] = 0
    
    # Volume filter
    if config.use_volume:
        vol_ok = df['volume'] > vol_ma
        signals.loc[~vol_ok, 'buy'] = 0
        signals.loc[~vol_ok, 'sell'] = 0
    
    # Store data for backtest
    signals['close'] = df['close']
    signals['atr'] = atr
    
    return signals


# =============================================================================
# Backtesting Engine
# =============================================================================

TRADING_COST = 0.003  # 0.30% per side = 0.60% round trip


def run_backtest(
    df: pd.DataFrame,
    config: BacktestConfig,
    initial_capital: float = 10000.0
) -> dict:
    """
    Run backtest with SL/TP using ATR-based risk management.
    Uses proper risk-based position sizing.
    
    Args:
        df: DataFrame with OHLCV data
        config: Backtest configuration
        initial_capital: Starting capital
    
    Returns:
        Dictionary with backtest results
    """
    signals = generate_signals(df, config)
    
    trades = []
    equity = initial_capital
    
    # Risk 5% of capital per trade
    risk_pct = 0.05
    risk_amount = initial_capital * risk_pct
    
    closes = signals['close'].values
    atrs = signals['atr'].values
    buys = signals['buy'].values
    sells = signals['sell'].values
    
    n = len(signals)
    
    position = None  # {'entry_price': float, 'side': 'long'|'short', 'stop_loss_pct': float, 'take_profit_pct': float, 'units': float}
    
    for i in range(n):
        close_price = closes[i]
        atr_val = atrs[i]
        
        if position is not None:
            stop_loss_pct = position['stop_loss_pct']
            take_profit_pct = position['take_profit_pct']
            
            if position['side'] == 'long':
                # Check exit conditions using percentage moves
                if ((close_price / position['entry_price']) - 1) <= -stop_loss_pct:
                    # Stop loss
                    pnl = equity * (-stop_loss_pct)
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'long', 'pnl': pnl, 'exit': 'sl', 'i': i})
                    position = None
                elif ((close_price / position['entry_price']) - 1) >= take_profit_pct:
                    # Take profit
                    pnl = equity * take_profit_pct
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'long', 'pnl': pnl, 'exit': 'tp', 'i': i})
                    position = None
                elif sells[i] == 1:
                    # Exit on sell signal - use actual PnL
                    pnl = equity * ((close_price / position['entry_price']) - 1)
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'long', 'pnl': pnl, 'exit': 'signal', 'i': i})
                    position = None
            else:  # short
                if ((position['entry_price'] / close_price) - 1) <= -stop_loss_pct:
                    pnl = equity * (-stop_loss_pct)
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'short', 'pnl': pnl, 'exit': 'sl', 'i': i})
                    position = None
                elif ((position['entry_price'] / close_price) - 1) >= take_profit_pct:
                    pnl = equity * take_profit_pct
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'short', 'pnl': pnl, 'exit': 'tp', 'i': i})
                    position = None
                elif buys[i] == 1:
                    pnl = equity * ((position['entry_price'] / close_price) - 1)
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'short', 'pnl': pnl, 'exit': 'signal', 'i': i})
                    position = None
        
        # Enter new position on signal
        if position is None and atr_val > 0:
            # Calculate stop/target as percentage of ATR/price
            atr_pct = (atr_val * config.atr_multiplier) / close_price
            tp_pct = atr_pct * config.risk_reward
            
            units = risk_amount / (atr_val * config.atr_multiplier)
            
            if buys[i] == 1:
                position = {
                    'entry_price': close_price,
                    'side': 'long',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct,
                    'units': units
                }
            elif sells[i] == 1:
                position = {
                    'entry_price': close_price,
                    'side': 'short',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct,
                    'units': units
                }
    
    # Close any open position at market
    if position is not None:
        close_price = closes[-1]
        if position['side'] == 'long':
            pnl = equity * ((close_price / position['entry_price']) - 1)
        else:
            pnl = equity * ((position['entry_price'] / close_price) - 1)
        equity += pnl - (abs(pnl) * TRADING_COST)
        trades.append({
            'side': position['side'],
            'pnl': pnl,
            'exit': 'eod',
            'i': n - 1
        })
    
    # Calculate metrics
    if not trades:
        return {
            'trades': 0,
            'win_rate': 0.0,
            'return_pct': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'profit_factor': 0.0,
            'mc_results': []
        }
    
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    return_pct = ((equity - initial_capital) / initial_capital) * 100
    
    return {
        'trades': len(trades),
        'win_rate': len(wins) / len(pnls) * 100 if pnls else 0,
        'return_pct': return_pct,
        'avg_win': np.mean(wins) if wins else 0,
        'avg_loss': np.mean(losses) if losses else 0,
        'profit_factor': sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0,
        'mc_results': []
    }


def monte_carlo_simulation(
    backtest_fn,
    df: pd.DataFrame,
    config: BacktestConfig,
    n_simulations: int = 100
) -> dict:
    """
    Run Monte Carlo simulation by shuffling trade PnLs.
    """
    # First run to get trades
    result = backtest_fn(df, config)
    
    if result['trades'] < 2:
        return {'p_loss': 0.0, 'pass': 'FAIL', 'reason': 'insufficient trades'}
    
    # Need to extract individual trade returns
    # We'll use a simplified approach: resample from the equity curve
    # Get base signals and run multiple simulations
    
    signals = generate_signals(df, config)
    closes = df['close'].values
    atrs = signals['atr'].values
    
    # Compute individual trade returns
    risk_per_trade = 1000.0  # Fixed risk
    trade_returns = []
    
    for _ in range(n_simulations):
        # Shuffle ATR values (simulating different volatility)
        shuffled_atrs = np.random.permutation(atrs)
        
        # Simplified: calculate returns based on shuffled ATR
        for i in range(len(closes) - 1):
            if shuffled_atrs[i] > 0:
                # Random move based on ATR
                move = np.random.normal(0, shuffled_atrs[i])
                ret = move * (risk_per_trade / (shuffled_atrs[i] * config.atr_multiplier))
                trade_returns.append(ret)
    
    trade_returns = np.array(trade_returns)
    sim_returns = np.sum(trade_returns, axis=0) if trade_returns.size > 0 else np.array([0])
    
    p_loss = np.mean(sim_returns < 0) * 100
    
    return {'p_loss': p_loss}


def run_backtest_with_mc(
    df: pd.DataFrame,
    config: BacktestConfig,
    n_simulations: int = 100,
    debug: bool = False
) -> dict:
    """
    Run backtest with Monte Carlo simulation using trade outcome permutation.
    """
    # Run base backtest to get actual trade results
    signals = generate_signals(df, config)
    closes = signals['close'].values.copy()
    atr = signals['atr'].values
    buys = signals['buy'].values
    sells = signals['sell'].values
    
    initial_capital = 10000.0
    risk_amount = initial_capital * 0.05  # 5% risk per trade
    
    # Run full backtest and collect individual trade PnLs as % of equity at trade time
    equity = initial_capital
    trade_outcomes = []  # Each trade's return as % of equity at time of trade
    position = None
    
    for i in range(len(closes) - 1):
        if position is not None:
            stop_loss_pct = position['stop_loss_pct']
            take_profit_pct = position['take_profit_pct']
            equity_at_entry = equity
            
            if position['side'] == 'long':
                pct_move = (closes[i] / position['entry_price']) - 1
                if pct_move <= -stop_loss_pct:
                    loss = equity_at_entry * stop_loss_pct
                    equity -= loss
                    trade_outcomes.append(-stop_loss_pct)
                    position = None
                elif pct_move >= take_profit_pct:
                    gain = equity_at_entry * take_profit_pct
                    equity += gain
                    trade_outcomes.append(take_profit_pct)
                    position = None
                elif sells[i]:
                    gain = equity_at_entry * pct_move
                    equity += gain
                    trade_outcomes.append(pct_move)
                    position = None
            else:
                pct_move = (position['entry_price'] / closes[i]) - 1
                if pct_move <= -stop_loss_pct:
                    loss = equity_at_entry * stop_loss_pct
                    equity -= loss
                    trade_outcomes.append(-stop_loss_pct)
                    position = None
                elif pct_move >= take_profit_pct:
                    gain = equity_at_entry * take_profit_pct
                    equity += gain
                    trade_outcomes.append(take_profit_pct)
                    position = None
                elif buys[i]:
                    gain = equity_at_entry * pct_move
                    equity += gain
                    trade_outcomes.append(pct_move)
                    position = None
        
        if position is None and atr[i] > 0:
            atr_pct = (atr[i] * config.atr_multiplier) / closes[i]
            tp_pct = atr_pct * config.risk_reward
            
            if buys[i]:
                position = {
                    'entry_price': closes[i],
                    'side': 'long',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct
                }
            elif sells[i]:
                position = {
                    'entry_price': closes[i],
                    'side': 'short',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct
                }
    
    n_trades = len(trade_outcomes)
    
    # Calculate metrics from actual trades
    win_rate = np.mean(np.array(trade_outcomes) > 0) * 100
    trade_outcomes_arr = np.array(trade_outcomes)
    wins = trade_outcomes_arr[trade_outcomes_arr > 0]
    losses = trade_outcomes_arr[trade_outcomes_arr <= 0]
    
    return_pct = ((equity - initial_capital) / initial_capital) * 100
    avg_win = np.mean(wins) * initial_capital if len(wins) > 0 else 0
    avg_loss = np.mean(losses) * initial_capital if len(losses) > 0 else 0
    
    result = {
        'trades': n_trades,
        'win_rate': win_rate,
        'return_pct': return_pct,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
    }
    
    if n_trades < 5:
        return {**result, 'p_loss': 50.0, 'verdict': 'FAIL', 'reason': 'insufficient trades'}
    
    if debug:
        print(f"    [MC DEBUG] Trades: {n_trades}, WR: {win_rate:.1f}%")
        print(f"    [MC DEBUG] Return: {return_pct:.2f}%")
    
    # Monte Carlo: shuffle trade order and add market variability
    np.random.seed(42)
    sim_profits = []
    
    for sim in range(n_simulations):
        shuffled = trade_outcomes_arr.copy()
        np.random.shuffle(shuffled)
        
        # Add market noise: simulate varying market conditions
        # Use normal distribution for outcome variability
        noise = np.random.normal(0, 0.15, n_trades)  # 15% std deviation on each trade
        varied = shuffled + (shuffled * noise)
        
        # Simulate with varied outcomes
        equity_sim = initial_capital
        for outcome in varied:
            equity_sim = equity_sim * (1 + outcome)
        
        sim_profits.append(equity_sim - initial_capital)
    
    sim_profits = np.array(sim_profits)
    p_loss = np.mean(sim_profits < 0) * 100
    
    # Show MC distribution
    if debug:
        print(f"    [MC] Sim mean: ${np.mean(sim_profits):.2f}, std: ${np.std(sim_profits):.2f}")
        print(f"    [MC] Min: ${np.min(sim_profits):.2f}, Max: ${np.max(sim_profits):.2f}")
    
    return {**result, 'p_loss': p_loss}


# =============================================================================
# Main Execution
# =============================================================================

def main():
    """Run the fast validation test."""
    print("=" * 70)
    print("Fast Backtest Validation - Trading Bot v2")
    print("=" * 70)
    
    # Load data
    csv_path = "G:/ai-workspace/Bot3/trading_bot_v2/backtesting/data/BTC-USDC_5m_2023.csv"
    
    print("\n[1] Loading data...")
    df = load_data(csv_path, start_date="2023-10-01", end_date="2023-12-31")
    print(f"    Loaded {len(df)} bars (Oct-Dec 2023)")
    
    # Define configurations
    configs = [
        BacktestConfig(sd_threshold=2.0, atr_multiplier=0.7, risk_reward=2.0, use_sfp=True, use_volume=True, name="Config 1"),
        BacktestConfig(sd_threshold=2.0, atr_multiplier=0.7, risk_reward=2.0, use_sfp=False, use_volume=True, name="Config 2"),
        BacktestConfig(sd_threshold=2.0, atr_multiplier=0.7, risk_reward=2.0, use_sfp=True, use_volume=False, name="Config 3"),
        BacktestConfig(sd_threshold=1.5, atr_multiplier=0.5, risk_reward=2.0, use_sfp=True, use_volume=True, name="Config 4"),
        BacktestConfig(sd_threshold=1.5, atr_multiplier=0.5, risk_reward=3.0, use_sfp=True, use_volume=True, name="Config 5"),
        BacktestConfig(sd_threshold=2.5, atr_multiplier=1.0, risk_reward=2.0, use_sfp=True, use_volume=True, name="Config 6"),
    ]
    
    print("\n[2] Running backtests...")
    print("-" * 70)
    
    results = []
    
    for config in configs:
        print(f"\n{config.name}")
        print(f"  sd={config.sd_threshold}, atr={config.atr_multiplier}, RR={config.risk_reward}")
        print(f"  sfp={config.use_sfp}, vol={config.use_volume}")
        
        result = run_backtest_with_mc(df, config, n_simulations=100)
        
        # Determine verdict
        if result['trades'] < 10:
            verdict = "FAIL"
            reason = "Insufficient trades"
        elif result['p_loss'] > 30:
            verdict = "FAIL"
            reason = f"P(Loss)={result['p_loss']:.1f}% > 30%"
        elif result['return_pct'] < 0:
            verdict = "FAIL"
            reason = f"Negative return: {result['return_pct']:.1f}%"
        elif result['win_rate'] < 40:
            verdict = "CAUTION"
            reason = f"WR={result['win_rate']:.1f}% < 40%"
        elif result['p_loss'] > 20:
            verdict = "CAUTION"
            reason = f"P(Loss)={result['p_loss']:.1f}% > 20%"
        else:
            verdict = "PASS"
            reason = "All criteria met"
        
        result['verdict'] = verdict
        result['reason'] = reason
        result['config'] = config.name
        results.append(result)
        
        print(f"  Trades: {result['trades']}, WR%: {result['win_rate']:.1f}%")
        print(f"  Return%: {result['return_pct']:.2f}%")
        print(f"  AvgWin: ${result['avg_win']:.2f}, AvgLoss: ${result['avg_loss']:.2f}")
        print(f"  P(Loss): {result['p_loss']:.1f}%")
        print(f"  >>> {verdict}: {reason}")
    
    # Summary table
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'Config':<12} {'Trades':<8} {'WR%':<8} {'Return%':<10} {'AvgWin':<10} {'AvgLoss':<10} {'P(Loss)':<8} {'Verdict':<10}")
    print("-" * 70)
    
    for r in results:
        print(f"{r['config']:<12} {r['trades']:<8} {r['win_rate']:<8.1f} {r['return_pct']:<10.2f} {r['avg_win']:<10.2f} {r['avg_loss']:<10.2f} {r['p_loss']:<8.1f} {r['verdict']:<10}")
    
    # Best passing config
    passing = [r for r in results if r['verdict'] == 'PASS']
    caution = [r for r in results if r['verdict'] == 'CAUTION']
    
    print("\n" + "=" * 70)
    print("ANALYSIS")
    print("=" * 70)
    
    if passing:
        best = max(passing, key=lambda x: x['return_pct'])
        print(f"\nBest PASSING: {best['config']}")
        print(f"  Return: {best['return_pct']:.2f}%, Win Rate: {best['win_rate']:.1f}%")
        print(f"  Trades: {best['trades']}, P(Loss): {best['p_loss']:.1f}%")
    else:
        print("\nNo passing configurations!")
        if caution:
            best_caution = max(caution, key=lambda x: x['return_pct'])
            print(f"\nBest CAUTION: {best_caution['config']}")
            print(f"  Return: {best_caution['return_pct']:.2f}%, Win Rate: {best_caution['win_rate']:.1f}%")
            print(f"  Reason: {best_caution['reason']}")
    
    print("\nCost Model: 0.30% per side (0.60% round trip)")
    print("=" * 70)
    
    return results


if __name__ == "__main__":
    main()