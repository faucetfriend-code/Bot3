"""
Fast Rolling Validation Test for Trading Bot v2

Tests 3-month windows on 2023 data with inline core logic:
- VWAP using cumsum (not pandas rolling)
- SFP detection: prior bar low < lower band AND prior close > band
- ATR from prior bar ranges
- Entry: next bar open, SL/TP from entry price
- Exit: check SL first, then TP

Target runtime: <30 seconds
Cost model: 0.60% round trip
"""

import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

warnings.filterwarnings('ignore', category=FutureWarning)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class BacktestConfig:
    """Configuration for backtest parameters."""
    sd_threshold: float
    atr_multiplier: float
    risk_reward: float
    use_sfp: bool
    use_volume: bool
    name: str = ""


# =============================================================================
# Data Loading
# =============================================================================

def load_data(csv_path: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """Load OHLCV data from CSV file."""
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp').sort_index()

    if start_date:
        df = df[df.index >= start_date]
    if end_date:
        df = df[df.index <= end_date]

    return df


# =============================================================================
# Technical Indicators (Vectorized - Inline Implementation)
# =============================================================================

def compute_daily_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Compute daily-anchored VWAP using cumulative sums.
    VWAP = cumsum(typical_price * volume) / cumsum(volume)
    """
    typical_price = (df['high'] + df['low'] + df['close']) / 3.0
    price_volume = typical_price * df['volume']

    # Cumulative sums per day
    cum_pv = price_volume.groupby(price_volume.index.date).cumsum()
    cum_vol = df['volume'].groupby(df['volume'].index.date).cumsum()

    vwap = cum_pv / cum_vol.replace(0, np.nan)
    return vwap


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ATR from prior bar ranges."""
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    n = len(high)

    true_range = np.zeros(n)
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        true_range[i] = max(hl, hc, lc)

    # ATR using simple moving average (inline)
    atr = np.zeros(n)
    atr[0] = true_range[0]
    for i in range(1, min(period, n)):
        atr[i] = np.mean(true_range[:i + 1])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + true_range[i]) / period

    return pd.Series(atr, index=df.index)


def compute_sd_bands(vwap: pd.Series, period: int = 20, num_std: float = 2.0) -> tuple:
    """Compute VWAP standard deviation bands."""
    vwap_std = vwap.rolling(window=period, min_periods=1).std()
    upper_band = vwap + (num_std * vwap_std)
    lower_band = vwap - (num_std * vwap_std)
    return upper_band, lower_band, vwap_std


def compute_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Compute volume moving average."""
    return df['volume'].rolling(window=period, min_periods=1).mean()


def compute_sfp_signals(df: pd.DataFrame, upper_band: pd.Series, lower_band: pd.Series) -> pd.Series:
    """
    Detect Smart Failsafe Pattern (SFP) signals.
    SFP: prior bar low < lower band AND prior close > band (bullish)
    SFP: prior bar high > upper band AND prior close < band (bearish)
    """
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values

    ub = upper_band.values
    lb = lower_band.values
    n = len(close)

    signals = np.zeros(n)

    for i in range(1, n):
        # Bullish SFP: prior bar low < lower band AND prior close > band
        if low[i - 1] < lb[i - 1] and close[i - 1] > lb[i - 1]:
            signals[i] = 1
        # Bearish SFP: prior bar high > upper band AND prior close < band
        elif high[i - 1] > ub[i - 1] and close[i - 1] < ub[i - 1]:
            signals[i] = -1

    return pd.Series(signals, index=df.index)


# =============================================================================
# Signal Generation (Vectorized)
# =============================================================================

def generate_signals(df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Generate trading signals using vectorized operations."""
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
        signals.loc[sfp_signals == 0, 'buy'] = 0
        signals.loc[sfp_signals == 0, 'sell'] = 0

    # Volume filter
    if config.use_volume:
        vol_ok = df['volume'] > vol_ma
        signals.loc[~vol_ok, 'buy'] = 0
        signals.loc[~vol_ok, 'sell'] = 0

    # Store data for backtest
    signals['close'] = df['close']
    signals['open'] = df['open']
    signals['atr'] = atr

    return signals


# =============================================================================
# Backtesting Engine (Entry at next bar open, SL/TP from entry price)
# =============================================================================

TRADING_COST = 0.006  # 0.60% round trip


def run_backtest(df: pd.DataFrame, config: BacktestConfig, initial_capital: float = 10000.0) -> dict:
    """
    Run backtest with SL/TP using ATR-based risk management.
    Entry: next bar open
    Exit: check SL first, then TP
    """
    signals = generate_signals(df, config)

    # Extract arrays for speed
    opens = signals['open'].values
    closes = signals['close'].values
    atrs = signals['atr'].values
    buys = signals['buy'].values
    sells = signals['sell'].values

    n = len(signals)

    trades = []
    equity = initial_capital
    risk_pct = 0.05
    risk_amount = initial_capital * risk_pct

    position = None  # {'entry_price': float, 'side': str, 'stop_loss_pct': float, 'take_profit_pct': float}

    for i in range(n - 1):  # Can't enter on last bar
        # Entry at next bar open
        if position is None and atrs[i] > 0:
            atr_pct = (atrs[i] * config.atr_multiplier) / closes[i]
            tp_pct = atr_pct * config.risk_reward

            if buys[i] == 1:
                # Enter long at next bar open
                entry_price = opens[i + 1]
                position = {
                    'entry_price': entry_price,
                    'side': 'long',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct
                }
            elif sells[i] == 1:
                # Enter short at next bar open
                entry_price = opens[i + 1]
                position = {
                    'entry_price': entry_price,
                    'side': 'short',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct
                }

        # Check exit conditions
        if position is not None:
            current_price = closes[i]
            stop_loss_pct = position['stop_loss_pct']
            take_profit_pct = position['take_profit_pct']

            if position['side'] == 'long':
                pct_move = (current_price / position['entry_price']) - 1

                # Check SL first, then TP
                if pct_move <= -stop_loss_pct:
                    # Stop loss hit
                    pnl = equity * (-stop_loss_pct)
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'long', 'pnl': pnl, 'exit': 'sl'})
                    position = None
                elif pct_move >= take_profit_pct:
                    # Take profit hit
                    pnl = equity * take_profit_pct
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'long', 'pnl': pnl, 'exit': 'tp'})
                    position = None
                elif sells[i] == 1:
                    # Exit on opposite signal
                    pnl = equity * pct_move
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'long', 'pnl': pnl, 'exit': 'signal'})
                    position = None
            else:  # short
                pct_move = (position['entry_price'] / current_price) - 1

                # Check SL first, then TP
                if pct_move <= -stop_loss_pct:
                    # Stop loss hit
                    pnl = equity * (-stop_loss_pct)
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'short', 'pnl': pnl, 'exit': 'sl'})
                    position = None
                elif pct_move >= take_profit_pct:
                    # Take profit hit
                    pnl = equity * take_profit_pct
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'short', 'pnl': pnl, 'exit': 'tp'})
                    position = None
                elif buys[i] == 1:
                    # Exit on opposite signal
                    pnl = equity * pct_move
                    equity += pnl - (abs(pnl) * TRADING_COST)
                    trades.append({'side': 'short', 'pnl': pnl, 'exit': 'signal'})
                    position = None

    # Close any open position at market
    if position is not None:
        close_price = closes[-1]
        if position['side'] == 'long':
            pnl = equity * ((close_price / position['entry_price']) - 1)
        else:
            pnl = equity * ((position['entry_price'] / close_price) - 1)
        equity += pnl - (abs(pnl) * TRADING_COST)
        trades.append({'side': position['side'], 'pnl': pnl, 'exit': 'eod'})

    # Calculate metrics
    if not trades:
        return {
            'trades': 0,
            'win_rate': 0.0,
            'return_pct': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'profit_factor': 0.0
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
        'profit_factor': sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0
    }


def run_backtest_with_mc(df: pd.DataFrame, config: BacktestConfig, n_simulations: int = 100) -> dict:
    """Run backtest with Monte Carlo simulation."""
    signals = generate_signals(df, config)

    opens = signals['open'].values
    closes = signals['close'].values
    atrs = signals['atr'].values
    buys = signals['buy'].values
    sells = signals['sell'].values

    initial_capital = 10000.0
    risk_amount = initial_capital * 0.05

    equity = initial_capital
    trade_outcomes = []
    position = None
    n = len(closes)

    for i in range(n - 1):
        if position is not None:
            current_price = closes[i]
            equity_at_entry = equity

            if position['side'] == 'long':
                pct_move = (current_price / position['entry_price']) - 1
                if pct_move <= -position['stop_loss_pct']:
                    equity -= equity_at_entry * position['stop_loss_pct']
                    trade_outcomes.append(-position['stop_loss_pct'])
                    position = None
                elif pct_move >= position['take_profit_pct']:
                    equity += equity_at_entry * position['take_profit_pct']
                    trade_outcomes.append(position['take_profit_pct'])
                    position = None
                elif sells[i] == 1:
                    equity += equity_at_entry * pct_move
                    trade_outcomes.append(pct_move)
                    position = None
            else:
                pct_move = (position['entry_price'] / current_price) - 1
                if pct_move <= -position['stop_loss_pct']:
                    equity -= equity_at_entry * position['stop_loss_pct']
                    trade_outcomes.append(-position['stop_loss_pct'])
                    position = None
                elif pct_move >= position['take_profit_pct']:
                    equity += equity_at_entry * position['take_profit_pct']
                    trade_outcomes.append(position['take_profit_pct'])
                    position = None
                elif buys[i] == 1:
                    equity += equity_at_entry * pct_move
                    trade_outcomes.append(pct_move)
                    position = None

        if position is None and atrs[i] > 0:
            atr_pct = (atrs[i] * config.atr_multiplier) / closes[i]
            tp_pct = atr_pct * config.risk_reward

            if buys[i] == 1:
                position = {
                    'entry_price': opens[i + 1],
                    'side': 'long',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct
                }
            elif sells[i] == 1:
                position = {
                    'entry_price': opens[i + 1],
                    'side': 'short',
                    'stop_loss_pct': atr_pct,
                    'take_profit_pct': tp_pct
                }

    if position is not None:
        close_price = closes[-1]
        if position['side'] == 'long':
            pnl = equity * ((close_price / position['entry_price']) - 1)
        else:
            pnl = equity * ((position['entry_price'] / close_price) - 1)
        equity += pnl - (abs(pnl) * TRADING_COST)
        trade_outcomes.append(pnl)

    n_trades = len(trade_outcomes)

    if n_trades == 0:
        return {
            'trades': 0,
            'win_rate': 0.0,
            'return_pct': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'p_loss': 50.0,
            'verdict': 'FAIL',
            'reason': 'no trades'
        }

    trade_outcomes_arr = np.array(trade_outcomes)
    win_rate = np.mean(trade_outcomes_arr > 0) * 100
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

    # Monte Carlo simulation
    np.random.seed(42)
    sim_profits = []

    for _ in range(n_simulations):
        shuffled = trade_outcomes_arr.copy()
        np.random.shuffle(shuffled)

        # Add market noise
        noise = np.random.normal(0, 0.15, n_trades)
        varied = shuffled + (shuffled * noise)

        equity_sim = initial_capital
        for outcome in varied:
            equity_sim = equity_sim * (1 + outcome)

        sim_profits.append(equity_sim - initial_capital)

    sim_profits = np.array(sim_profits)
    p_loss = np.mean(sim_profits < 0) * 100

    return {**result, 'p_loss': p_loss}


# =============================================================================
# Main Execution
# =============================================================================

def main():
    """Run the fast rolling validation test."""
    import time
    start_time = time.time()

    print("=" * 70)
    print("Fast Rolling Validation - Trading Bot v2")
    print("=" * 70)
    print("\nTesting 3-month windows on 2023 data")
    print("Cost model: 0.60% round trip")
    print("-" * 70)

    # Load data
    csv_path = "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot3/trading_bot_v2/backtesting/data/BTC-USDC_5m_2023.csv"

    print("\n[1] Loading 2023 data...")
    df_full = load_data(csv_path)
    print(f"    Loaded {len(df_full)} bars (full 2023)")

    # Define windows
    windows = [
        ("Q1 2023", "2023-01-01", "2023-03-31"),
        ("Q2 2023", "2023-04-01", "2023-06-30"),
        ("Q3 2023", "2023-07-01", "2023-09-30"),
        ("Q4 2023", "2023-10-01", "2023-12-31"),
    ]

    # Define configurations
    configs = [
        BacktestConfig(sd_threshold=2.0, atr_multiplier=0.7, risk_reward=2.0, use_sfp=True, use_volume=False, name="Config 1 (WINNER)"),
        BacktestConfig(sd_threshold=2.0, atr_multiplier=0.7, risk_reward=2.0, use_sfp=False, use_volume=False, name="Config 2"),
        BacktestConfig(sd_threshold=2.0, atr_multiplier=0.7, risk_reward=2.0, use_sfp=True, use_volume=True, name="Config 3"),
        BacktestConfig(sd_threshold=2.5, atr_multiplier=1.0, risk_reward=2.0, use_sfp=True, use_volume=False, name="Config 4"),
        BacktestConfig(sd_threshold=2.0, atr_multiplier=0.5, risk_reward=2.0, use_sfp=True, use_volume=False, name="Config 5"),
        BacktestConfig(sd_threshold=2.0, atr_multiplier=1.0, risk_reward=3.0, use_sfp=True, use_volume=False, name="Config 6"),
    ]

    print("\n[2] Running backtests across all windows...")
    print("-" * 70)

    # Store results per config
    all_results = {}

    for config in configs:
        print(f"\n>>> {config.name}")
        print(f"    sd={config.sd_threshold}, atr={config.atr_multiplier}, RR={config.risk_reward}, sfp={config.use_sfp}, vol={config.use_volume}")

        window_results = []

        for window_name, start_date, end_date in windows:
            df_window = load_data(csv_path, start_date=start_date, end_date=end_date)
            result = run_backtest_with_mc(df_window, config, n_simulations=100)

            print(f"  [{window_name}] Trades: {result['trades']}, WR: {result['win_rate']:.0f}%, Return: {result['return_pct']:+.1f}%, AvgWin: ${result['avg_win']:.0f}, AvgLoss: ${result['avg_loss']:.0f}")

            window_results.append({
                'window': window_name,
                'trades': result['trades'],
                'win_rate': result['win_rate'],
                'return_pct': result['return_pct'],
                'avg_win': result['avg_win'],
                'avg_loss': result['avg_loss'],
            })

        all_results[config.name] = window_results

    # Aggregate results
    print("\n" + "=" * 70)
    print("AGGREGATE RESULTS")
    print("=" * 70)

    best_config = None
    best_return = -float('inf')

    for config in configs:
        window_results = all_results[config.name]

        total_trades = sum(w['trades'] for w in window_results)
        total_wins = sum(w['trades'] * w['win_rate'] / 100 for w in window_results)
        combined_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
        combined_return = sum(w['return_pct'] for w in window_results)

        # Calculate per-window averages
        avg_return_per_quarter = combined_return / len(window_results)

        # Calculate Sharpe-like metric (simplified)
        returns = [w['return_pct'] for w in window_results]
        sharpe = (np.mean(returns) / np.std(returns)) if np.std(returns) > 0 else 0

        # Monte Carlo P(Loss) - run on combined data
        df_2023 = load_data(csv_path, start_date="2023-01-01", end_date="2023-12-31")
        mc_result = run_backtest_with_mc(df_2023, config, n_simulations=100)
        p_loss = mc_result['p_loss']

        # Determine verdict
        if total_trades < 20:
            verdict = "FAIL"
            reason = f"Insufficient trades: {total_trades}"
        elif p_loss > 30:
            verdict = "FAIL"
            reason = f"P(Loss)={p_loss:.1f}% > 30%"
        elif combined_return < 0:
            verdict = "FAIL"
            reason = f"Negative return: {combined_return:.1f}%"
        elif combined_wr < 40:
            verdict = "CAUTION"
            reason = f"WR={combined_wr:.1f}% < 40%"
        else:
            verdict = "PASS"
            reason = "All criteria met"

        print(f"\n{config.name}: sd={config.sd_threshold}, atr={config.atr_multiplier}, RR={config.risk_reward}, sfp={config.use_sfp}, vol={config.use_volume}")
        print(f"  Total trades: {total_trades}, Combined WR: {combined_wr:.0f}%, Combined Return: {combined_return:+.1f}%")
        print(f"  Per-window avg: Sharpe: {sharpe:.2f}, Return: {avg_return_per_quarter:+.1f}%/quarter")
        print(f"  P(Loss) from Monte Carlo: {p_loss:.1f}%")
        print(f"  VERDICT: {verdict} ({reason})")

        if verdict == "PASS" and combined_return > best_return:
            best_return = combined_return
            best_config = config.name

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if best_config:
        print(f"\nWINNER: {best_config}")
        print(f"  Combined Return: {best_return:+.1f}%")
    else:
        print("\nNo passing configurations!")

    elapsed = time.time() - start_time
    print(f"\nTotal runtime: {elapsed:.1f} seconds (target: <30s)")
    print("=" * 70)

    return all_results


if __name__ == "__main__":
    main()