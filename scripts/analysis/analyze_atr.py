"""Analyze 5m ATR for ETH, BTC, SUI to understand transaction cost impact."""
import sys
from loguru import logger
logger.remove()

from trading_bot_v2.backtesting.data_loader import BacktestDataLoader

def analyze(symbol):
    loader = BacktestDataLoader(symbol=symbol, data_dir='trading_bot_v2/backtesting/data')
    candles = loader.get_candles('5m', '2024-01-01', '2024-12-31')
    closes = candles['close']
    highs  = candles['high']
    lows   = candles['low']

    period = 14
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)

    atr = sum(trs[:period]) / period
    atrs = [atr]
    for i in range(period, len(trs)):
        atr = (atr*(period-1) + trs[i]) / period
        atrs.append(atr)

    avg_atr   = sum(atrs) / len(atrs)
    avg_price = sum(closes[period:]) / len(closes[period:])
    atr_pct   = avg_atr / avg_price * 100
    slippage  = avg_price * 0.002
    cost_drag = avg_price * 0.0032

    print(f"{symbol}:")
    print(f"  Avg 5m price       : ${avg_price:>10,.2f}")
    print(f"  Avg 5m ATR (abs)   : ${avg_atr:>10.4f}  ({atr_pct:.4f}%)")
    print(f"  Slippage (0.2%)    : ${slippage:>10.4f}  = {slippage/avg_atr:.2f}x ATR per side")
    print(f"  Cost model (0.32%) : ${cost_drag:>10.4f}  = {cost_drag/avg_atr:.2f}x ATR")
    print(f"  Stop @ 2.0x ATR    : ${avg_atr*2:>10.4f}  | entry slip = {slippage/(avg_atr*2)*100:.1f}% of stop dist")
    print(f"  Stop @ 3.0x ATR    : ${avg_atr*3:>10.4f}  | entry slip = {slippage/(avg_atr*3)*100:.1f}% of stop dist")
    print()

for sym in ['ETH-USDC', 'BTC-USDC', 'SUI-USDC']:
    analyze(sym)
