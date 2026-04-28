# VWAP Scalping Strategy — Year-by-Year Backtest (2018-2025)

## Configuration

### bull_pullback mode
```python
    sd_threshold = 2.0
    entry_mode = bull_pullback
    adx_max = 30
    rsi_max = 50
    volume_mult = 2.0
    atr_stop = 0.7
    atr_target = 3.5
    pullback_bars = 3
    use_session_filter = True
    use_htf_vwap = True
    use_htf_ema = False
    htf_adx_max = 25
    use_trailing_stop = True
    trailing_atr = 1.2
    use_anchored_vwap = True
    require_reversal_candle = True
    tp_mode = vwap
```

### mean_reversion mode
```python
    sd_threshold = 2.5
    entry_mode = mean_reversion
    adx_max = 30
    rsi_max = 50
    volume_mult = 2.0
    atr_stop = 0.7
    atr_target = 3.5
    pullback_bars = 3
    use_session_filter = True
    use_htf_vwap = True
    use_htf_ema = False
    htf_adx_max = 25
    use_trailing_stop = True
    trailing_atr = 1.2
    use_anchored_vwap = True
    require_reversal_candle = True
    tp_mode = vwap
```

## Results

| Year | Mode | Trades | WR% | PF | Net% | Sharpe | MaxDD% | CAGR% |
|------|------|--------|-----|-----|------|--------|--------|-------|
| 2018 | bull_pullback | 89 | 23.6% | 0.50 | -52.94% | -3.67 | -28.00% | -26.32% |
| 2019 | bull_pullback | 127 | 26.8% | 0.54 | -65.02% | -6.18 | -26.92% | -27.00% |
| 2020 | bull_pullback | 128 | 32.0% | 0.47 | -72.34% | -3.32 | -34.32% | -33.96% |
| 2021 | bull_pullback | 121 | 26.4% | 0.52 | -69.62% | -3.84 | -36.21% | -33.41% |
| 2022 | bull_pullback | 83 | 20.5% | 0.17 | -53.22% | -7.11 | -28.32% | -28.40% |
| 2023 | bull_pullback | 121 | 19.0% | 0.19 | -68.04% | -9.41 | -31.74% | -31.83% |
| 2024 | bull_pullback | 122 | 37.7% | 0.63 | -60.49% | -5.17 | -25.08% | -23.91% |
| 2018 | mean_reversion | 75 | 24.0% | 0.25 | -46.22% | -5.80 | -24.87% | -23.79% |
| 2019 | mean_reversion | 126 | 23.8% | 0.26 | -70.48% | -9.20 | -32.68% | -32.77% |
| 2020 | mean_reversion | 104 | 24.0% | 0.31 | -58.30% | -7.13 | -27.32% | -27.11% |
| 2021 | mean_reversion | 79 | 25.3% | 0.29 | -49.18% | -5.90 | -25.58% | -25.56% |
| 2022 | mean_reversion | 90 | 27.8% | 0.37 | -49.96% | -6.49 | -22.96% | -23.03% |
| 2023 | mean_reversion | 117 | 18.8% | 0.19 | -63.75% | -9.86 | -28.65% | -28.73% |
| 2024 | mean_reversion | 80 | 35.0% | 0.41 | -42.46% | -6.21 | -18.56% | -18.47% |

** = Profitable with >=10 trades

## Analysis

### No Profitable Configurations Found

No year/mode combination produced a net positive return with >=10 trades.


### Unprofitable Configurations (14)

- **2020 bull_pullback**: Net=-72.34%, Trades=128, WR=32.0%
- **2019 mean_reversion**: Net=-70.48%, Trades=126, WR=23.8%
- **2021 bull_pullback**: Net=-69.62%, Trades=121, WR=26.4%
- **2023 bull_pullback**: Net=-68.04%, Trades=121, WR=19.0%
- **2019 bull_pullback**: Net=-65.02%, Trades=127, WR=26.8%
- **2023 mean_reversion**: Net=-63.75%, Trades=117, WR=18.8%
- **2024 bull_pullback**: Net=-60.49%, Trades=122, WR=37.7%
- **2020 mean_reversion**: Net=-58.30%, Trades=104, WR=24.0%
- **2022 bull_pullback**: Net=-53.22%, Trades=83, WR=20.5%
- **2018 bull_pullback**: Net=-52.94%, Trades=89, WR=23.6%
- **2022 mean_reversion**: Net=-49.96%, Trades=90, WR=27.8%
- **2021 mean_reversion**: Net=-49.18%, Trades=79, WR=25.3%
- **2018 mean_reversion**: Net=-46.22%, Trades=75, WR=24.0%
- **2024 mean_reversion**: Net=-42.46%, Trades=80, WR=35.0%

## Best Overall

**2024 mean_reversion** with Net=-42.46%, Trades=80, WR=35.0%, PF=0.41, Sharpe=-6.21
