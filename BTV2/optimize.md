# optimize.md — Optimization Program for Bot3 Strategies

This file is the `program.md` equivalent from the autoresearch pattern.
Edit this file to control what the optimizer focuses on each run.
The optimizer reads it with `--from-program`.

---

## Strategy: VWAP Scalping

## Ticker: BTC-USD

## Period: 2018-01-01 to 2025-12-31

## Trials: 100

## Timeout: 120

## Strict: false

---

## Objective

Maximize mean OOS Sharpe across all walk-forward windows.
Minimum 10 OOS trades across the full test period required to accept a config.
Reject any config with max drawdown worse than -30%.

## Search Space (VWAP Scalping)

- sd_threshold: [2.5, 3.0, 3.5, 4.0, 4.5] (categorical)
- entry_mode: [bull_pullback, bear_pullback, cross, mean_reversion]
- atr_stop: [1.5, 2.0, 2.5, 3.0]
- tp_mode: [atr, pdh]
- use_trailing_stop: [True] (fixed)
- trailing_atr: [1.2, 1.5, 2.0]

## Cost Configuration

- Realistic costs: 0.30% per trade (0.15% per side)
- Session filter: enabled
- HTF filter: enabled

## Goals

- Win rate > 45%
- Net positive or minimal loss
- Sharpe > 0

## Output

- Trial log: BTV2/results/optimizer_trials.jsonl
- Update .env if OOS Sharpe improves by ≥ 0.05
