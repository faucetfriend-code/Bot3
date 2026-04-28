import sys
from pathlib import Path
import pandas as pd
import time
from itertools import product

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies import run_vwap_scalping, compute_metrics

# Load data
df = pd.read_parquet(Path('G:/Candle Data/BTCUSDT_5m.parquet'))
df = df[(df.index >= '2022-01-01') & (df.index < '2024-01-01')]
print(f'Data: {len(df)} bars (2022-2024)')

# Stage 1: Core params
SD_THRESHOLD = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
ENTRY_MODES = ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross', 'momentum', 'deviation']

stage1 = []
print('=== STAGE 1: Testing SD + Entry Mode ===')
for sd in SD_THRESHOLD:
    for em in ENTRY_MODES:
        params = {
            'sd_threshold': sd,
            'entry_mode': em,
            'stoch_oversold': 20,
            'stoch_overbought': 80,
            'adx_max': 25,
            'rsi_max': 50,
            'volume_mult': 2.0,
            'atr_stop': 1.0,
            'atr_target': 4.0,
            'pullback_bars': 3,
            'use_htf_vwap': True,
            'use_htf_ema': True,
            'htf_adx_max': 25,
            'df_exit': df
        }
        
        equity, trades = run_vwap_scalping(df, cutoff=0.08, **params)
        
        if trades:
            metrics = compute_metrics(equity, trades, bars_per_year=105120)
            costs = len(trades) * 0.30
            net_return = metrics['total_return_pct'] - costs
        else:
            metrics = {'win_rate_pct': 0, 'total_return_pct': 0, 'profit_factor': 0}
            net_return = 0
            
        stage1.append({
            'sd': sd, 'entry_mode': em, 'trades': len(trades),
            'win_rate': metrics['win_rate_pct'], 'net_return': net_return,
            'pf': metrics['profit_factor'], 'params': params
        })

print(f'Stage 1 complete: {len(stage1)} configs')

# Top from Stage 1
top_s1 = sorted(stage1, key=lambda x: x['net_return'], reverse=True)[:10]
print('=== STAGE 1 TOP 10 ===')
for i, r in enumerate(top_s1, 1):
    print(f'{i}. SD={r["sd"]}, {r["entry_mode"]}: Net={r["net_return"]:+.1f}%, WR={r["win_rate"]:.1f}%, Trades={r["trades"]}')

# Stage 2: Stoch + ADX + RSI
STOCH_OVERSOLD = [15, 20, 25]
STOCH_OVERBOUGHT = [75, 80, 85]
ADX_MAX = [20, 25, 30, 35]
RSI_MAX = [40, 45, 50, 55]

stage2 = []
print('=== STAGE 2: Testing Stoch + ADX + RSI ===')
for t1 in top_s1:
    p = t1['params']
    for so in STOCH_OVERSOLD:
        for sb in STOCH_OVERBOUGHT:
            for adx in ADX_MAX:
                for rsi in RSI_MAX:
                    params = {
                        'sd_threshold': p['sd_threshold'],
                        'entry_mode': p['entry_mode'],
                        'stoch_oversold': so,
                        'stoch_overbought': sb,
                        'adx_max': adx,
                        'rsi_max': rsi,
                        'volume_mult': 2.0,
                        'atr_stop': 1.0,
                        'atr_target': 4.0,
                        'pullback_bars': 3,
                        'use_htf_vwap': True,
                        'use_htf_ema': True,
                        'htf_adx_max': 25,
                        'df_exit': df
                    }
                    
                    equity, trades = run_vwap_scalping(df, cutoff=0.08, **params)
                    
                    if trades:
                        metrics = compute_metrics(equity, trades, bars_per_year=105120)
                        costs = len(trades) * 0.30
                        net_return = metrics['total_return_pct'] - costs
                    else:
                        metrics = {'win_rate_pct': 0, 'total_return_pct': 0, 'profit_factor': 0}
                        net_return = 0
                        
                    stage2.append({
                        'sd': p['sd_threshold'], 'entry_mode': p['entry_mode'],
                        'stoch_oversold': so, 'stoch_overbought': sb,
                        'adx_max': adx, 'rsi_max': rsi,
                        'trades': len(trades), 'win_rate': metrics['win_rate_pct'],
                        'net_return': net_return, 'pf': metrics['profit_factor'],
                        'params': params
                    })

print(f'Stage 2 complete: {len(stage2)} configs')

# Top from Stage 2
top_s2 = sorted(stage2, key=lambda x: x['net_return'], reverse=True)[:10]
print('=== STAGE 2 TOP 10 ===')
for i, r in enumerate(top_s2, 1):
    print(f'{i}. SD={r["sd"]}, {r["entry_mode"]}, stoch=({r["stoch_oversold"]},{r["stoch_overbought"]}), adx={r["adx_max"]}, rsi={r["rsi_max"]}: Net={r["net_return"]:+.1f}%, WR={r["win_rate"]:.1f}%')

# Stage 3: Volume + ATR
VOLUME_MULT = [1.5, 2.0, 2.5]
ATR_STOP = [0.7, 1.0, 1.5]
ATR_TARGET = [3.0, 4.0, 5.0]

stage3 = []
print('=== STAGE 3: Testing Volume + ATR ===')
for t2 in top_s2:
    p = t2['params']
    for vol in VOLUME_MULT:
        for atr_s in ATR_STOP:
            for atr_t in ATR_TARGET:
                params = {
                    'sd_threshold': p['sd_threshold'],
                    'entry_mode': p['entry_mode'],
                    'stoch_oversold': p['stoch_oversold'],
                    'stoch_overbought': p['stoch_overbought'],
                    'adx_max': p['adx_max'],
                    'rsi_max': p['rsi_max'],
                    'volume_mult': vol,
                    'atr_stop': atr_s,
                    'atr_target': atr_t,
                    'pullback_bars': 3,
                    'use_htf_vwap': True,
                    'use_htf_ema': True,
                    'htf_adx_max': 25,
                    'df_exit': df
                }
                
                equity, trades = run_vwap_scalping(df, cutoff=0.08, **params)
                
                if trades:
                    metrics = compute_metrics(equity, trades, bars_per_year=105120)
                    costs = len(trades) * 0.30
                    net_return = metrics['total_return_pct'] - costs
                else:
                    metrics = {'win_rate_pct': 0, 'total_return_pct': 0, 'profit_factor': 0, 'sharpe': 0, 'max_dd_pct': 0}
                    net_return = 0
                    
                stage3.append({
                    'params': params,
                    'trades': len(trades), 
                    'win_rate': metrics['win_rate_pct'],
                    'net_return': net_return, 
                    'pf': metrics['profit_factor'],
                    'sharpe': metrics.get('sharpe', 0),
                    'max_dd': metrics.get('max_dd_pct', 0)
                })

print(f'Stage 3 complete: {len(stage3)} configs')

# Final results
stage3_sorted = sorted(stage3, key=lambda x: x['net_return'], reverse=True)
qualifying = [r for r in stage3_sorted if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 10]

print('=== FINAL RESULTS ===')
print(f'Total configs tested: {len(stage3)}')
print(f'Qualifying (WR>48%, Net>0%, Trades>=10): {len(qualifying)}')

final = qualifying[:20] if qualifying else stage3_sorted[:20]

print('=== TOP 20 ===')
for i, r in enumerate(final, 1):
    p = r['params']
    print(f'{i:>2} SD={p["sd_threshold"]:>4.1f} EM={p["entry_mode"]:14} Stoch=({p["stoch_oversold"]},{p["stoch_overbought"]}) ADX={p["adx_max"]} RSI={p["rsi_max"]} Vol={p["volume_mult"]:>4.1f} ATR-S={p["atr_stop"]:>4.1f} T={r["trades"]:>3} WR={r["win_rate"]:>5.1f} Net={r["net_return"]:>+7.1f}')

print('=== DETAILED TOP 10 ===')
for i, r in enumerate(final[:10], 1):
    p = r['params']
    print(f'Rank #{i}: sd={p["sd_threshold"]}, entry={p["entry_mode"]}, stoch=({p["stoch_oversold"]},{p["stoch_overbought"]}), adx={p["adx_max"]}, rsi={p["rsi_max"]}, vol={p["volume_mult"]}, atr_s={p["atr_stop"]}, atr_t={p["atr_target"]} | trades={r["trades"]}, WR={r["win_rate"]:.1f}%, PF={r["pf"]:.2f}, Net={r["net_return"]:.1f}%, Sharpe={r["sharpe"]:.2f}, DD={r["max_dd"]:.1f}%')

# SD Analysis
print('=== SD THRESHOLD ANALYSIS ===')
sd_stats = {}
for r in stage3:
    sd = r['params']['sd_threshold']
    if sd not in sd_stats:
        sd_stats[sd] = {'total': 0, 'qualifying': 0, 'best_net': -999}
    sd_stats[sd]['total'] += 1
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 10:
        sd_stats[sd]['qualifying'] += 1
    if r['net_return'] > sd_stats[sd]['best_net']:
        sd_stats[sd]['best_net'] = r['net_return']

for sd in sorted(sd_stats.keys()):
    print(f'SD={sd:.1f}: Tests={sd_stats[sd]["total"]}, Qualifying={sd_stats[sd]["qualifying"]}, Best Net={sd_stats[sd]["best_net"]:+.1f}%')
