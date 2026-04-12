# BTV2 — CLI Agent Integration Guide  (v5)

How to programmatically drive the walk-forward backtesting system, interpret its outputs, and wire it into an automated improvement loop with a CLI coding agent (Gemini, Claude Code, etc.).

---

## System Overview

```
Bot3/
  .env                    ← live trading bot config (100+ vars, strategy params)
  BTV2/
    app.py                ← Streamlit dashboard (human UI layer)
    strategies.py         ← pure-Python backtest engine (no UI imports)
    agent.py              ← .env reader/optimizer (coordinate descent)
    data_manager.py       ← Binance downloader + local Parquet cache
    requirements.txt
    CLI_AGENT_GUIDE.md    ← this file

G:\Candle Data\           ← local Parquet cache (auto-created on first run)
  BTCUSDT_1m.parquet
  BTCUSDT_5m.parquet
  BTCUSDT_15m.parquet
  BTCUSDT_1h.parquet
  BTCUSDT_4h.parquet
  BTCUSDT_1d.parquet
```

The key architectural decision: **`strategies.py`, `agent.py`, and `data_manager.py` are pure compute modules** — no Streamlit, no UI, no side effects. A CLI agent can import and call them directly as Python functions without ever touching the browser.

---

## Quick Start

```bash
cd C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3\BTV2
pip install -r requirements.txt

# Human UI
streamlit run app.py

# CLI agent entry point (no UI required)
python -c "import strategies, agent, data_manager; print('OK')"
```

---

## Module API Reference

### `data_manager.py`

#### `TICKER_MAP` and `SUPPORTED_INTERVALS`
```python
from data_manager import TICKER_MAP, SUPPORTED_INTERVALS

print(TICKER_MAP)
# {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT", ...}

print(SUPPORTED_INTERVALS)
# ["1m", "5m", "15m", "1h", "4h", "1d"]
```

#### `get_candles(symbol, interval, start, end, progress_cb) → pd.DataFrame`
Main data accessor.  Loads from local Parquet, downloads only missing bars.
```python
from datetime import datetime, timezone
from data_manager import get_candles

df = get_candles(
    "BTCUSDT", "1h",
    start=datetime(2022, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 1, 1, tzinfo=timezone.utc),
    progress_cb=lambda cur, end: None,   # optional progress callback
)
# Returns: pd.DataFrame with UTC DatetimeIndex, columns [Open, High, Low, Close, Volume]
# First call: downloads from Binance, saves to G:\Candle Data\BTCUSDT_1h.parquet
# Subsequent calls: loads from disk instantly (no network)
```

#### `get_cache_info(symbol, interval) → dict`
```python
from data_manager import get_cache_info

info = get_cache_info("BTCUSDT", "1h")
# {
#   "exists": True,
#   "path": "G:\\Candle Data\\BTCUSDT_1h.parquet",
#   "rows": 52341,
#   "start": "2017-09-01",
#   "end": "2024-03-11",
#   "size_mb": 1.1
# }
```

#### `list_all_cache_info(symbol) → dict[str, dict]`
```python
from data_manager import list_all_cache_info

cache = list_all_cache_info("BTCUSDT")
# {"1m": {...}, "5m": {...}, "15m": {...}, "1h": {...}, "4h": {...}, "1d": {...}}
```

---

### `strategies.py`

#### `STRATEGY_REGISTRY`
```python
from strategies import STRATEGY_REGISTRY

# Keys: strategy names
# Values: (backtest_func, param_grid, default_params)
print(list(STRATEGY_REGISTRY.keys()))
# ['Mean Reversion', 'VWAP Scalping', 'Momentum Scalping',
#  'Liquidation Capture', 'Grid Trading', 'MA Crossover']
```

#### `STRATEGY_TIMEFRAME_CONFIG`
```python
from strategies import STRATEGY_TIMEFRAME_CONFIG

# Per-strategy native interval + walk-forward window sizes
print(STRATEGY_TIMEFRAME_CONFIG["VWAP Scalping"])
# {"interval": "5m", "train_months": 3, "test_months": 1}

print(STRATEGY_TIMEFRAME_CONFIG["MA Crossover"])
# {"interval": "1d", "train_months": 12, "test_months": 3}
```

| Strategy | Interval | Train | Test | Rationale |
|----------|----------|-------|------|-----------|
| Mean Reversion | 1d | 12m | 3m | Daily regime detection |
| VWAP Scalping | 5m | 3m | 1m | LTF bars for high signal frequency; 15m VWAP + 1h EMA/ADX MTF filters; use 1m exit-res |
| Momentum Scalping | 15m | 3m | 1m | 15m gives faster EMA crosses than 1h with less noise than 5m |
| Liquidation Capture | 1d | 12m | 3m | Rare event, needs long history |
| Grid Trading | 4h | 6m | 2m | Range detection at 4h resolution |
| MA Crossover | 1d | 12m | 3m | Trend following on daily bars |

#### `INTERVAL_BARS_PER_YEAR`
```python
from strategies import INTERVAL_BARS_PER_YEAR

# BTC trades 24/7/365 — NOT 252 trading days like equities
print(INTERVAL_BARS_PER_YEAR)
# {"1m": 525600, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "1d": 365}

# Pass this to compute_metrics for correct Sharpe scaling
bars_py = INTERVAL_BARS_PER_YEAR["5m"]  # 105_120  (VWAP Scalping native interval)
```

#### Running a single backtest
```python
from data_manager import get_candles, TICKER_MAP
from strategies import STRATEGY_REGISTRY, STRATEGY_TIMEFRAME_CONFIG, INTERVAL_BARS_PER_YEAR
from datetime import datetime, timezone

# Load data at the strategy's native interval
strategy_name = "VWAP Scalping"
tf_cfg   = STRATEGY_TIMEFRAME_CONFIG[strategy_name]
interval = tf_cfg["interval"]   # "5m"  (VWAP Scalping native interval)

df = get_candles("BTCUSDT", interval,
                 datetime(2022, 1, 1, tzinfo=timezone.utc),
                 datetime(2024, 1, 1, tzinfo=timezone.utc))

func, grid, defaults = STRATEGY_REGISTRY[strategy_name]

# Basic backtest — defaults are now entry_mode="mean_reversion", use_htf_vwap=True,
# use_htf_ema=True.  The function resamples df internally to 15m and 1h for the
# MTF filters; NO separate data files are needed.
equity, trades = func(df, cutoff=0.10, **defaults)

# With 1m sub-bar exit simulation — RECOMMENDED for VWAP Scalping on 5m bars.
# Resolves SL-vs-TP ordering ambiguity within each 5m candle (only 5 sub-bars
# per native bar → very fast, ~60 MB extra Parquet for 7yr BTC history).
df_1m = get_candles("BTCUSDT", "1m",
                    datetime(2022, 1, 1, tzinfo=timezone.utc),
                    datetime(2024, 1, 1, tzinfo=timezone.utc))
equity, trades = func(df, cutoff=0.10, **defaults, df_exit=df_1m)

# Tuning all filters explicitly (bull_pullback mode shown):
equity, trades = func(df, cutoff=0.10,
    entry_mode       = "bull_pullback",  # dips in confirmed 1h uptrends
    sd_threshold     = 2.0,    # VWAP band depth — price must be this far below VWAP
    adx_max          = 25.0,   # used only by mean_reversion/cross modes (not bull_pullback)
    rsi_max          = 45.0,   # 5m RSI < 45 for longs (not overbought)
    stoch_oversold   = 40,     # %K < 40 AND %K > %D → long momentum exhaustion gate
    stoch_overbought = 60,     # %K > 60 AND %K < %D → short momentum exhaustion gate
    volume_mult      = 1.5,    # require 1.5× avg volume
    atr_stop         = 0.7,    # tight stop anchored to signal candle close
    atr_target       = 3.0,    # R:R anchored to signal candle close
    use_htf_vwap     = True,   # 15m VWAP alignment (mean_reversion/cross modes)
    use_htf_ema      = True,   # 1h EMA direction + 1h VWAP (required for pullback modes)
    htf_adx_max      = 25.0,   # 1h ADX threshold for mean_reversion mode
    df_exit          = df_1m,
)
# Dynamic mode — auto-selects entry_mode per bar based on 1h ADX + trend direction:
equity, trades = func(df, cutoff=0.10, use_dynamic_mode=True, **{
    k: v for k, v in defaults.items() if k != "entry_mode"
}, df_exit=df_1m)
# For mean_reversion mode (ranging markets without clear trend):
equity, trades = func(df, cutoff=0.10, entry_mode="mean_reversion", **{
    k: v for k, v in defaults.items() if k != "entry_mode"
}, df_exit=df_1m)

# ⚠️ MTF IMPORTANT: 15m and 1h data are derived by RESAMPLING the 5m df passed in.
# Do NOT load separate BTCUSDT_15m or BTCUSDT_1h files for this — it's automatic.
# The only extra file you need is df_1m for exit resolution.

# equity : pd.Series — portfolio value (starts at 1.0, bar-by-bar mark-to-market)
# trades : list[float] — closed trade returns (e.g. [0.023, -0.011, 0.047, ...])
```

#### `compute_metrics(equity, trades, bars_per_year=365) → dict`
```python
from strategies import compute_metrics, INTERVAL_BARS_PER_YEAR

metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["5m"])
# Returns:
# {
#   "total_return_pct": 34.2,   # total % return over the period
#   "cagr_pct":         16.1,   # annualised compound growth rate
#   "sharpe":            1.84,  # annualised Sharpe ratio (√105120 for 5m crypto)
#   "max_dd_pct":       -18.4,  # maximum drawdown (negative number)
#   "win_rate_pct":      54.0,  # % of closed trades that were profitable
#   "profit_factor":      1.8,  # gross_profit / gross_loss
#   "n_trades":          26,    # total closed trades
# }

# ⚠️  Always pass bars_per_year matching the candle interval:
#   daily → 365, 4h → 2190, 1h → 8760, 15m → 35040, 5m → 105120, 1m → 525600
#   Using 252 (equity-market convention) understates crypto Sharpe by ~√(365/252) ≈ 20%
```

#### `build_windows(start, end, train_months, test_months) → list[dict]`
```python
from datetime import date
from strategies import build_windows, STRATEGY_TIMEFRAME_CONFIG

tf_cfg = STRATEGY_TIMEFRAME_CONFIG["VWAP Scalping"]
windows = build_windows(
    date(2022, 1, 1), date(2024, 1, 1),
    train_months=tf_cfg["train_months"],   # 3
    test_months=tf_cfg["test_months"],     # 1
)
# Returns list of fold dicts:
# [
#   {"fold": 1,
#    "train_start": date(2022,1,1), "train_end": date(2022,3,31),
#    "test_start":  date(2022,4,1), "test_end":  date(2022,4,30)},
#   ...
# ]
# Windows roll forward by test_months per fold (non-overlapping OOS)
```

#### `optimize_strategy(df_train, cutoff, strategy_name) → dict`
```python
from strategies import optimize_strategy

best_params = optimize_strategy(df_train, cutoff=0.10, strategy_name="VWAP Scalping")
# Exhaustive Sharpe-maximizing grid search over VS_GRID (324 combos) on the training window
# Returns keys matching VS_GRID axes: sd_threshold, atr_stop, adx_max, stoch_oversold, stoch_overbought
# e.g. {"sd_threshold": 3.0, "atr_stop": 0.7, "adx_max": 20.0, "stoch_oversold": 20, "stoch_overbought": 80}
# atr_target, pullback_bars, MTF flags are NOT in the grid — they use VS_DEFAULTS / agent coordinate descent
#
# v4 performance optimisation: for strategies that accept a `levels` parameter (VWAP Scalping),
# compute_reference_levels() is called ONCE per fold here and injected as levels=...
# into every grid combo.  This avoids 324× redundant recomputation (~127s saved per fold).
```

#### `_resample_ohlcv(df, rule) → pd.DataFrame`
```python
from strategies import _resample_ohlcv

# Resample 5m OHLCV to any coarser timeframe — used internally by run_vwap_scalping
df_15m = _resample_ohlcv(df_5m, "15min")
df_1h  = _resample_ohlcv(df_5m, "60min")
df_4h  = _resample_ohlcv(df_5m, "240min")
# Returns pd.DataFrame with Open/High/Low/Close/Volume, same DatetimeIndex convention
```

#### Multi-Timeframe Filter Design (VWAP Scalping)

`run_vwap_scalping` computes its own 15m and 1h views by resampling the 5m `df` argument:

```
5m df (input)
    │
    ├─── resample("15min") → rolling VWAP(20) → forward-fill to 5m index
    │       htf_long_ok  &= (price < 15m_VWAP)   # still below 15m mean
    │       htf_short_ok &= (price > 15m_VWAP)   # still above 15m mean
    │
    └─── resample("60min") → EMA(9), EMA(21), ADX(14) → forward-fill to 5m index
            h_ranging = (1h_ADX < htf_adx_max)
            htf_long_ok  &= (1h_EMA9 > 1h_EMA21  OR  h_ranging)
            htf_short_ok &= (1h_EMA9 < 1h_EMA21  OR  h_ranging)
```

**Key architectural point:** no extra `get_candles()` calls are needed. The 15m and 1h
are derived on-the-fly from the same 5m Parquet file you already loaded.

**Logic for a LONG entry — `bull_pullback` mode (all 7 must pass):**
```
1. 5m:  price < VWAP − sd_threshold × σ_VWAP         (pulled back to lower VWAP band)
2. 5m:  RSI < rsi_max                                  (not overbought on 5m)
3. 5m:  volume ≥ volume_mult × 20-bar avg              (real participation)
4. 1h:  EMA(9) > EMA(21)                               (confirmed 1h uptrend — strict)
5. 1h:  price < 1h VWAP                                (dip not yet recovered to 1h mean)
6. 5m:  Stoch(14,3,3) %K < stoch_oversold AND %K > %D  (exhaustion + K crossing up from oversold)
7. 5m:  RSI divergence: price lower low but RSI higher low (selling pressure exhausting)
⟹ Entry at bar i+1 open (N+1), SL/TP anchored to bar i close
```

> **v5 note:** The 5m ADX ranging gate (`ADX < adx_max`) and `recent_hh` higher-high check were
> removed from `bull_pullback`. On actual pullback candles the current bar naturally has a lower
> high than 1-3 hours prior, so `recent_hh` passed only ~6% of the time and blocked all trades
> in trending periods. The ADX gate was similarly counterproductive — pullback bars in a strong
> trend often show localised 5m momentum that pushes ADX above the threshold.

**`bear_pullback` is the exact mirror** (1h EMA bearish, price above upper band, above 1h VWAP, Stochastic overbought cross down, RSI bearish divergence). The `recent_ll` check was also removed.

**Logic for a LONG entry — `mean_reversion` mode (fallback for ranging markets):**
```
1–4. Same 5m filters as above
5. 15m: price < 15m VWAP              (15m alignment via use_htf_vwap)
6. 1h:  EMA(9) > EMA(21) OR ADX < htf_adx_max  (bullish OR ranging on 1h)
7. 5m:  Stoch(14,3,3) %K < stoch_oversold AND %K > %D  (momentum exhaustion gate)
⟹ Entry at bar i+1 open (N+1), SL/TP anchored to bar i close
```

#### `stitch_oos_equity(segments) → pd.Series`
```python
from strategies import stitch_oos_equity

oos_equity = stitch_oos_equity([eq_fold1, eq_fold2, eq_fold3, ...])
# Chains fold equity curves into one continuous series (rebased at each join)
```

---

### `agent.py`

#### `read_env_params(strategy_name) → dict`
```python
from agent import read_env_params

params = read_env_params("Mean Reversion")
# Reads Bot3/.env, type-casts values, falls back to defaults if key missing
# Returns: {"rsi_oversold": 30.0, "rsi_overbought": 70.0, ...}
```

#### `run_parameter_sweep(...) → tuple` (4-tuple in v3)
```python
from agent import run_parameter_sweep
from strategies import INTERVAL_BARS_PER_YEAR, STRATEGY_TIMEFRAME_CONFIG

tf_cfg = STRATEGY_TIMEFRAME_CONFIG["VWAP Scalping"]
bars_py = INTERVAL_BARS_PER_YEAR[tf_cfg["interval"]]   # 105_120  (5m native interval)

best_params, best_cutoff, best_sharpe, trial_log = run_parameter_sweep(
    df, cutoff=0.10,
    strategy_name="VWAP Scalping",
    current_params=params,
    windows=windows,
    bars_per_year=bars_py,          # ← new in v3
)
# best_params  : dict — parameter set with highest OOS Sharpe found
# best_cutoff  : float — optimal Butterworth cutoff found by sweep
# best_sharpe  : float — OOS Sharpe of (best_cutoff, best_params)
# trial_log    : list[dict] — one row per (parameter × multiplier) trial
#   First rows: butterworth_cutoff sweep (7 candidates)
#   Later rows: strategy param sweep (5 candidates each)
#   Each row: {"Parameter": "sd_threshold", "Candidate": 3.5,
#              "OOS Sharpe": 1.12, "OOS Return %": 28.4, "OOS Max DD %": -14.2}
```

**v3 sweep order:**
1. **Butterworth cutoff** — swept first (7 candidates: ×[0.50, 0.70, 0.85, 1.00, 1.15, 1.30, 1.50], clamped to [0.05, 0.45])
2. **Strategy parameters** — swept using best cutoff found in step 1 (×[0.70, 0.85, 1.00, 1.15, 1.30])

**VWAP Scalping .env keys swept (v3):**

| ENV Variable | Internal kwarg | Default | Sweep range | Notes |
|---|---|---|---|---|
| `VWAP_SD_ENTRY_THRESHOLD` | `sd_threshold` | 2.0 | ×[0.70–1.30] | VWAP band depth for pullback/reversion entry |
| `VWAP_ATR_STOP_MULTIPLIER` | `atr_stop` | 0.7 | ×[0.70–1.30] | tight stop on 5m LTF |
| `VWAP_ATR_TARGET_MULTIPLIER` | `atr_target` | 3.0 | ×[0.70–1.30] | R:R target multiplier |
| `VWAP_VOLUME_MULTIPLIER` | `volume_mult` | 1.5 | ×[0.70–1.30] | |
| `VWAP_ADX_MAX` | `adx_max` | 25.0 | ×[0.70–1.30] (range 17–33) | used by mean_reversion/cross modes |
| `VWAP_RSI_MAX` | `rsi_max` | 45.0 | ×[0.70–1.30] (range 31–58) | 5m strength gate |
| `VWAP_STOCH_OVERSOLD` | `stoch_oversold` | 40 | ×[0.70–1.30] (range 28–52) | Stochastic %K oversold threshold for longs |
| `VWAP_STOCH_OVERBOUGHT` | `stoch_overbought` | 60 | ×[0.70–1.30] (range 42–78) | Stochastic %K overbought threshold for shorts |
| `VWAP_PULLBACK_BARS` | `pullback_bars` | 3 | ×[0.70–1.30] (range 2–4) | 1h lookback for Stoch/RSI state window |

**VS_GRID exhaustive axes (216 combos — 4×3×3×3×3):**

| Grid param | Values | Default | Notes |
|---|---|---|---|
| `sd_threshold` | [1.5, 2.0, 2.5, 3.0] | 2.0 | VWAP band depth for pullback entry |
| `atr_stop` | [0.5, 0.7, 1.0] | 0.7 | Stop multiplier (tight on 5m LTF) |
| `adx_max` | [20.0, 25.0, 30.0] | 25.0 | ADX threshold (mean_reversion/cross modes) |
| `stoch_oversold` | [30, 40, 50] | 40 | Stochastic %K oversold threshold for longs |
| `stoch_overbought` | [50, 60, 70] | 60 | Stochastic %K overbought threshold for shorts |

*`atr_target` and `pullback_bars` are swept by the agent's coordinate descent (not in exhaustive grid) — this keeps the training sweep tractable.*

**Multi-timeframe filter parameters (always-on, not in sweep — tune manually):**

| Python kwarg | Default | Effect |
|---|---|---|
| `use_htf_vwap` | `True` | Enable 15m VWAP alignment gate |
| `use_htf_ema` | `True` | Enable 1h EMA(9/21) + 1h VWAP gate (required for pullback modes) |
| `htf_adx_max` | `25.0` | 1h ADX < this = ranging context (mean_reversion / cross); ≥ this = trending (dynamic mode) |
| `use_dynamic_mode` | `False` | Auto-select `entry_mode` per bar using 1h ADX — see Dynamic Regime Mode section |
| `fvg_proximity_atr` | `0.5` | FVG/OB zone tolerance: entries within N×ATR of zone edge are accepted |

#### `build_proposal(strategy_name, current_params, best_params) → list[dict]`
```python
from agent import build_proposal

proposal = build_proposal("VWAP Scalping", current_params, best_params)
# Returns only parameters that actually changed:
# [{"Parameter": "sd_threshold", "ENV Variable": "VWAP_SD_ENTRY_THRESHOLD",
#   "Current Value": 3.0, "Proposed Value": 3.5, "Change": "+16.7%"}]
```

#### `apply_to_env(proposal) → list[str]`
```python
from agent import apply_to_env

updated_keys = apply_to_env(proposal)
# Surgically writes each changed key to Bot3/.env using python-dotenv set_key
# Does NOT disturb comments or unrelated variables
# Returns: ["VWAP_SD_ENTRY_THRESHOLD"]
```

---

## Full CLI Agent Workflow (v3)

Complete self-contained script using the v3 API with multi-timeframe support.

```python
#!/usr/bin/env python3
"""
btv2_agent_run.py — CLI entry point for automated strategy optimization (v3)
Usage: python btv2_agent_run.py --strategy "VWAP Scalping" --apply
       python btv2_agent_run.py --strategy "MA Crossover" --exit-res 5m --json-out results.json
"""
import argparse, json, sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))   # ensure BTV2/ is on path

from strategies import (
    STRATEGY_REGISTRY, STRATEGY_TIMEFRAME_CONFIG, INTERVAL_BARS_PER_YEAR,
    build_windows, compute_metrics, optimize_strategy, stitch_oos_equity,
)
from data_manager import get_candles, TICKER_MAP, SUPPORTED_INTERVALS
from agent import (
    read_env_params, run_parameter_sweep,
    build_proposal, apply_to_env, _eval_config,
)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy",  default="Mean Reversion",
                        choices=list(STRATEGY_REGISTRY.keys()))
    parser.add_argument("--ticker",    default="BTC-USD")
    parser.add_argument("--start",     default="2020-01-01")
    parser.add_argument("--end",       default=str(date.today()))
    parser.add_argument("--cutoff",    type=float, default=0.10,
                        help="Starting Butterworth cutoff (agent will also sweep this)")
    parser.add_argument("--interval",  default=None,
                        choices=SUPPORTED_INTERVALS,
                        help="Override native interval (default: from STRATEGY_TIMEFRAME_CONFIG)")
    parser.add_argument("--exit-res",  default="Off", choices=["Off", "5m", "1m"],
                        help="Sub-bar exit resolution (5m or 1m for SL/TP accuracy)")
    parser.add_argument("--apply",     action="store_true",
                        help="Write proposed changes to .env")
    parser.add_argument("--json-out",  help="Write results to JSON file")
    args = parser.parse_args()

    # Resolve interval and window sizes
    tf_cfg       = STRATEGY_TIMEFRAME_CONFIG[args.strategy]
    interval     = args.interval or tf_cfg["interval"]
    train_months = tf_cfg["train_months"]
    test_months  = tf_cfg["test_months"]
    bars_py      = INTERVAL_BARS_PER_YEAR[interval]

    symbol = TICKER_MAP.get(args.ticker)

    # ── 1. Load main candle data ───────────────────────────────────────────────
    print(f"[1/5] Loading {args.ticker} {interval} candles…")
    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    if symbol:
        df = get_candles(symbol, interval, start_dt, end_dt)
    else:
        from data_manager import get_candles_yfinance
        df = get_candles_yfinance(args.ticker, args.start, args.end)

    print(f"      {len(df):,} bars loaded  ({df.index[0].date()} → {df.index[-1].date()})")

    # ── 1b. Optional sub-bar exit data ───────────────────────────────────────
    df_exit = None
    if args.exit_res != "Off" and symbol:
        print(f"[1b]  Loading {args.exit_res} exit-resolution data…")
        df_exit = get_candles(symbol, args.exit_res, start_dt, end_dt)
        print(f"      {len(df_exit):,} {args.exit_res} bars loaded")

    # ── 2. Walk-forward baseline ───────────────────────────────────────────────
    print(f"[2/5] Running walk-forward baseline ({args.strategy}, {interval})…")
    windows = build_windows(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        train_months=train_months,
        test_months=test_months,
    )
    func, _, defaults = STRATEGY_REGISTRY[args.strategy]
    segments, all_trades = [], []
    for w in windows:
        df_train = df.loc[str(w["train_start"]):str(w["train_end"])]
        df_test  = df.loc[str(w["test_start"]):str(w["test_end"])]
        if len(df_train) < 50 or len(df_test) < 10:
            continue
        df_exit_slice = (
            df_exit.loc[str(w["test_start"]):str(w["test_end"])]
            if df_exit is not None else None
        )
        best = optimize_strategy(df_train, args.cutoff, args.strategy)
        eq, trd = func(df_test, args.cutoff, **best, df_exit=df_exit_slice)
        segments.append(eq); all_trades.extend(trd)

    oos_equity  = stitch_oos_equity(segments)
    oos_metrics = compute_metrics(oos_equity, all_trades, bars_per_year=bars_py)
    print(f"      OOS Sharpe={oos_metrics['sharpe']:.3f}  "
          f"Return={oos_metrics['total_return_pct']:+.1f}%  "
          f"MaxDD={oos_metrics['max_dd_pct']:.1f}%  "
          f"Trades={oos_metrics['n_trades']}")
    print(f"      ({len(windows)} folds · {train_months}m train → {test_months}m test"
          f" · Sharpe √{bars_py})")

    # ── 3. Read current .env params ────────────────────────────────────────────
    print(f"[3/5] Reading current .env parameters…")
    current_params  = read_env_params(args.strategy)
    baseline_sharpe = _eval_config(df, args.cutoff, args.strategy,
                                   current_params, windows, bars_py)
    print(f"      Current .env OOS Sharpe: {baseline_sharpe:.3f}")

    # ── 4. Coordinate-descent sweep (cutoff + params) ─────────────────────────
    print(f"[4/5] Running parameter sweep (Butterworth cutoff + strategy params)…")
    best_params, best_cutoff, best_sharpe, trial_log = run_parameter_sweep(
        df, args.cutoff, args.strategy, current_params, windows,
        bars_per_year=bars_py,
    )
    proposal = build_proposal(args.strategy, current_params, best_params)

    print(f"      Best OOS Sharpe: {best_sharpe:.3f}  "
          f"(Δ{best_sharpe - baseline_sharpe:+.3f} vs current .env)")
    if abs(best_cutoff - args.cutoff) > 1e-4:
        print(f"      Optimal Butterworth cutoff: {best_cutoff:.3f}  "
              f"(was {args.cutoff:.3f})")

    if proposal:
        print(f"      {len(proposal)} .env parameter change(s) proposed:")
        for p in proposal:
            print(f"        {p['ENV Variable']}: "
                  f"{p['Current Value']} → {p['Proposed Value']}  ({p['Change']})")
    else:
        print("      No .env parameter improvements found — current values near-optimal.")

    # ── 5. Optionally apply ────────────────────────────────────────────────────
    if args.apply and proposal:
        print(f"[5/5] Writing {len(proposal)} change(s) to .env…")
        updated = apply_to_env(proposal)
        print(f"      Updated: {', '.join(updated)}")
        print("      ⚠️  Restart the trading bot to activate new parameters.")
    else:
        print("[5/5] Dry run — no .env changes written.")

    # ── JSON output for downstream agents ─────────────────────────────────────
    result = {
        "strategy":         args.strategy,
        "ticker":           args.ticker,
        "interval":         interval,
        "period":           f"{args.start} → {args.end}",
        "train_months":     train_months,
        "test_months":      test_months,
        "bars_per_year":    bars_py,
        "butterworth":      args.cutoff,
        "best_cutoff":      best_cutoff,
        "cutoff_delta":     round(best_cutoff - args.cutoff, 4),
        "exit_resolution":  args.exit_res,
        "oos_metrics":      oos_metrics,
        "baseline_sharpe":  baseline_sharpe,
        "best_sharpe":      best_sharpe,
        "sharpe_delta":     best_sharpe - baseline_sharpe,
        "proposal":         proposal,
        "trial_log":        trial_log,
        "applied":          args.apply and bool(proposal),
    }

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2))
        print(f"      Results written to {args.json_out}")
    else:
        print("\n--- JSON RESULT ---")
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
```

**Example CLI calls:**
```bash
# Dry run — report what would change
python btv2_agent_run.py --strategy "Mean Reversion" --json-out results.json

# 5m scalping with 1m exit resolution (VWAP native interval is 5m)
python btv2_agent_run.py --strategy "VWAP Scalping" --exit-res 1m --json-out vwap_results.json

# Apply changes to .env after sweep
python btv2_agent_run.py --strategy "VWAP Scalping" --apply

# Loop all strategies
for strategy in "Mean Reversion" "VWAP Scalping" "Momentum Scalping" "Grid Trading" "MA Crossover"; do
    python btv2_agent_run.py --strategy "$strategy" --json-out "results_${strategy// /_}.json"
done
```

---

## Interpreting the Output

### Key metrics and what they mean for the live bot

| Metric | What it measures | Good threshold |
|--------|-----------------|----------------|
| `sharpe` | Risk-adjusted return (higher = better) | > 0.8 OOS |
| `total_return_pct` | Raw % gain over the period | Context-dependent |
| `cagr_pct` | Annualised compound return | > 15% on BTC |
| `max_dd_pct` | Worst peak-to-trough drawdown (negative) | Better than −30% |
| `win_rate_pct` | % of trades closed at profit | > 45% overall; 52–65% target for VWAP Scalping on 5m with ADX+RSI filters |
| `profit_factor` | Gross wins ÷ gross losses | > 1.3; target 1.4–1.8 for VWAP Scalping |
| `n_trades` | Total closed trades in period | ≥ 10 for statistical significance; 1000–3000/yr healthy for VWAP Scalping on 5m bars |

> **Sharpe scaling note (v3):** Sharpe is now scaled by `√bars_per_year` instead of `√252`. A Sharpe of 1.5 on 1h data is directly comparable to a Sharpe of 1.5 on daily data — they both represent the same risk-adjusted return. The old `√252` convention was wrong for 24/7 crypto and undervalued intraday strategies.

### Reading the IS vs OOS gap

```
IS Sharpe  = strategy run on full date range with DEFAULT .env params
OOS Sharpe = strategy run only on test windows with OPTIMISED params per fold

If OOS Sharpe > IS Sharpe:
    Walk-forward optimization is adding genuine value — params generalise.
    Safe to apply agent proposals.

If OOS Sharpe ≈ IS Sharpe:
    Borderline — params aren't hurting but aren't helping either.
    Consider widening the PARAM_GRID or checking for regime change.

If OOS Sharpe << IS Sharpe:
    Overfitting on training window or structural regime change.
    Do NOT apply agent proposals — params found in-sample don't generalise.
    Consider: longer training windows, smaller param grids, different strategy.
```

### Reading `best_cutoff` vs `butterworth`

```
butterworth  = starting cutoff (passed to sweep)
best_cutoff  = optimal cutoff found by sweep
cutoff_delta = best_cutoff - butterworth

If |cutoff_delta| > 0.02:
    The Butterworth filter tuning is materially affecting results.
    Update the slider in the app sidebar to best_cutoff and re-run analysis.

If cutoff_delta > 0 (higher cutoff):
    Less smoothing — strategy benefits from faster price response.
    Common for scalping strategies on intraday data.

If cutoff_delta < 0 (lower cutoff):
    More smoothing — strategy benefits from reduced noise.
    Common for trend-following strategies on coarse bars.
```

### Reading the trial_log

The v3 trial_log starts with Butterworth cutoff trials, then moves to strategy params:
```json
{"Parameter": "butterworth_cutoff", "Candidate": 0.085,
 "OOS Sharpe": 1.24, "OOS Return %": 31.2, "OOS Max DD %": -11.8}

{"Parameter": "sd_threshold", "Candidate": 3.5,
 "OOS Sharpe": 1.18, "OOS Return %": 28.4, "OOS Max DD %": -14.2}
```
- **OOS Sharpe > baseline** → this candidate value outperforms current .env
- **OOS Return % high but OOS Max DD % very negative** → aggressive, not recommended
- **Multiple multipliers improving Sharpe** → strong signal the parameter needs adjustment
- **All multipliers near baseline Sharpe** → parameter is already well-tuned

### Reading the proposal

```json
[{
  "Parameter": "sd_threshold",
  "ENV Variable": "VWAP_SD_ENTRY_THRESHOLD",
  "Current Value": 3.0,
  "Proposed Value": 3.5,
  "Change": "+16.7%"
}]
```
- Only parameters that **materially improve OOS Sharpe** appear here
- Empty proposal = current .env is already near-optimal for this dataset
- `ENV Variable` is the exact key `apply_to_env()` writes to `.env`
- The Butterworth cutoff does **not** appear here (it's a UI slider, not a `.env` variable) — check `best_cutoff` and `cutoff_delta` fields instead

---

## Automation Loop Pattern (for a CLI coding agent)

```
┌──────────────────────────────────────────────────┐
│  Phase 1: MEASURE (daily or weekly)              │
│  python btv2_agent_run.py --strategy X           │
│  → results.json                                  │
└──────────────────────┬───────────────────────────┘
                       │ results.json
                       ▼
┌──────────────────────────────────────────────────┐
│  Phase 2: DECIDE                                 │
│  CLI agent reads results.json                    │
│                                                  │
│  if sharpe_delta > 0.10 and n_trades >= 10:      │
│      apply = True   (parameter improvement)      │
│  elif |cutoff_delta| > 0.02:                     │
│      update slider  (Butterworth tuning)         │
│  elif oos_sharpe < 0.3:                          │
│      flag = True    (strategy underperforming)   │
│  else:                                           │
│      no_action = True                            │
└──────────────────────┬───────────────────────────┘
                       │
          ┌────────────┴──────────────┐
          ▼                           ▼
┌─────────────────────┐   ┌─────────────────────────┐
│  Apply .env change  │   │  Edit strategies.py      │
│  --apply flag       │   │  (CLI agent edits code)  │
│  restart bot        │   │  widen PARAM_GRID        │
└─────────────────────┘   │  adjust entry conditions │
                          └─────────────────────────┘
```

### Decision rules (recommended thresholds)

```python
# After reading results.json:
sharpe_delta  = result["best_sharpe"] - result["baseline_sharpe"]
oos_sharpe    = result["oos_metrics"]["sharpe"]
n_trades      = result["oos_metrics"]["n_trades"]
cutoff_delta  = result["cutoff_delta"]

if sharpe_delta > 0.10 and n_trades >= 10:
    # Meaningful improvement with statistical confidence → apply
    import subprocess
    subprocess.run(["python", "btv2_agent_run.py",
                    "--strategy", strategy, "--apply"])

elif abs(cutoff_delta) > 0.02:
    # Butterworth filter needs tuning → note best_cutoff for sidebar
    print(f"Update Butterworth slider to {result['best_cutoff']:.3f}")

elif oos_sharpe < 0.30 and n_trades >= 5:
    # Strategy is underperforming → flag for logic review
    # CLI agent should edit strategies.py entry conditions, not just params

elif n_trades < 5:
    # Too few trades to evaluate → widen entry conditions:
    # - Raise sd_threshold (lower entry bar → more signals before MTF filter)
    # - Lower htf_adx_max (allow wider ranging context on 1h)
    # - Set use_htf_vwap=False temporarily to confirm MTF is the bottleneck
    pass
```

---

## Parameter Sweep: How It Works (v3)

The coordinate-descent sweep in `agent.py` runs in two stages:

**Stage 0 — Butterworth cutoff sweep (new in v3)**
1. Try 7 candidates: current_cutoff × [0.50, 0.70, 0.85, 1.00, 1.15, 1.30, 1.50]
2. Each candidate is clamped to [0.05, 0.45]
3. Run full walk-forward OOS with each candidate
4. Keep the cutoff that maximises OOS Sharpe
5. All subsequent strategy-param trials use this best cutoff

**Stage 1+ — Strategy parameter sweep**
1. **Start** with current `.env` params as the baseline, using best cutoff from Stage 0
2. **For each parameter** (one at a time, in order):
   - Try 5 multipliers: `× [0.70, 0.85, 1.00, 1.15, 1.30]`
   - Run full walk-forward OOS with each candidate
   - Keep the value that maximises OOS Sharpe
3. **Move to next parameter**, holding improved previous values
4. **Return** `(best_params, best_cutoff, best_sharpe, trial_log)` — 4-tuple

**Total evaluations:** `7 + (5 × n_params)` per run (tractable in 30–120 seconds)

**Why coordinate descent vs exhaustive grid?**
- Exhaustive grid over 5 multipliers per param = `5^N` evaluations (intractable for N > 3)
- Coordinate descent = `5 × N` evaluations (tractable)
- Tradeoff: may miss interactions between parameters — acceptable for minor tuning

**Why not re-optimize each fold?**
- The sweep evaluates **all folds with fixed params** (no per-fold optimization)
- This gives a true OOS estimate of how the new params perform
- It avoids "double-dipping" bias from optimizing within the test window

---

## Exit Resolution: How It Works

The `df_exit` parameter on all 6 strategy functions enables sub-bar SL/TP detection.

**Problem it solves:** On a 1h bar with both a `low` that breaches SL and a `high` that hits TP, the coarse bar can't tell you which happened first — so SL is assumed (conservative). On the actual 5m sub-bars within that hour, you can see the real sequence.

```
Native bar (1h):     Low = 41,200  High = 43,800   Entry = 42,000
                     SL  = 41,500  TP   = 43,500

Without df_exit:   Low (41,200) <= SL (41,500) → SL hit, trade is a loss

With df_exit (5m):  5m bars 00:00–01:00:
   00:05  H=42,100  L=41,900  — no hit
   00:10  H=43,600  L=42,800  — TP (43,500) hit FIRST → trade is a winner!
   00:15  H=42,400  L=41,100  — would have been SL, but we already closed
```

**When to use it:**
- `"Off"` — default, uses native bar H/L (fast, conservative, existing behaviour)
- `"5m"` — recommended for Momentum Scalping on 15m bars (3 sub-bars per native bar)
- `"1m"` — recommended for VWAP Scalping on 5m bars (5 sub-bars per native bar); ~60 MB extra Parquet per 7yr BTC

**Performance impact:** Sub-bar exit simulation is `O(sub_bars_per_native_bar)` per bar with an open position:
- 5m native + 1m exit: **5 iterations** max per position check (very fast)
- 15m native + 5m exit: **3 iterations** max (trivial)
- 1h native + 5m exit: 12 iterations max
- 1d native + 5m/1m exit: 288–1440 sub-bars (negligible improvement, not recommended)

---

## Data: Binance vs yfinance

| Feature | Binance (v3 default) | yfinance (fallback) |
|---------|---------------------|---------------------|
| Intraday history | Full from 2017 | 1m=7d, 5m=60d, 1h=730d |
| Crypto tickers | All major pairs | Limited |
| Auth required | No | No |
| Rate limits | 1000 bars/request | Strict |
| Local cache | Yes (Parquet) | No |
| Network on re-run | No (disk) | Yes |
| Equities/ETFs | No | Yes |

Tickers in `TICKER_MAP` use Binance. Everything else falls back to yfinance (daily only).

---

## Market Structure Upgrade (v4 — Phases 1–3)

Three phases of new infrastructure were added to `strategies.py` to improve signal quality, entry precision, and TP targeting across all strategies.  All features default to `False`/`None` so existing backtests are unaffected — they must be explicitly enabled.

---

### Phase 1 — Reference Levels & Anchored VWAP

**New function: `compute_reference_levels(df) → pd.DataFrame`**

Pre-computes 14 market-structure price columns aligned to `df.index` with **zero lookahead** (all values derived from completed prior periods):

| Column | Description |
|--------|-------------|
| `session_vwap` | Daily-reset VWAP (developing, current session) |
| `session_vwap_std` | VWAP σ — NOT a price level, used only for band width |
| `pdh` / `pdl` | Prior-day high / low |
| `prev_day_poc` / `prev_day_vah` / `prev_day_val` | Prior day's volume profile (POC, Value Area High/Low) |
| `pwh` / `pwl` | Prior-week high / low |
| `prev_week_poc` / `prev_week_vah` / `prev_week_val` | Prior week's volume profile |
| `asian_high` / `asian_low` | Today's 00:00–07:59 UTC accumulation range |

```python
from strategies import compute_reference_levels

levels = compute_reference_levels(df)   # ~78ms on full 5m BTC history
# Pass to run_vwap_scalping to enable PDH/PDL and "nearest" TP mode:
equity, trades = run_vwap_scalping(df, cutoff=0.10, levels=levels, tp_mode="nearest", **params)
```

**`tp_mode` options (VWAP Scalping):**

| Mode | Long TP | Short TP |
|------|---------|----------|
| `"vwap"` | Session VWAP (default) | Session VWAP |
| `"pdh"` | Prior-day high | Prior-day low |
| `"atr"` | entry + atr_target×ATR | entry − atr_target×ATR |
| `"nearest"` | Closest reference level above entry | Closest reference level below entry |

`"nearest"` requires `levels` to be passed. It scans all 13 price-level columns (excludes `session_vwap_std`) and picks the nearest valid target. Falls back to ATR target if no level found.

**Performance note:** `compute_reference_levels` takes ~78ms. It is pre-computed once per fold inside `optimize_strategy()` and injected via `levels=` kwarg — saves ~127s per walk-forward run vs calling it inside every grid combo.

**UTM (Unified Trade Model) parameters — VWAP Scalping:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `use_anchored_vwap` | `True` | Daily-reset session VWAP instead of rolling 20-bar window |
| `use_session_filter` | `True` | Only trade London + NY sessions (08:00–22:00 UTC) |
| `require_reversal_candle` | `True` | SFP confirmation: wick past band + close back inside = liquidity grab |
| `require_mss` | `False` | UTM Step 3: wait for BOS above SFP candle high before entry |
| `mss_timeout_bars` | `6` | Cancel pending SFP if BOS not confirmed within N bars |

**SFP + MSS state machine (when `require_reversal_candle=True, require_mss=True`):**
```
Bar i:   Low wicks below lower VWAP band AND Close back above band → SFP detected
         → set mss_long_pending=True, record bos_level=High[i], sl=Low[i]
Bar i+1: Close > bos_level → MSS/BOS confirmed → LONG entry at Open[i+2]
         (if not confirmed within mss_timeout_bars → reset pending state)
```

**All 6 VWAP entry modes support SFP + MSS:**
- `mean_reversion`, `cross`, `bull_pullback`, `bear_pullback` — full SFP detection (wick past band + close back)
- `deviation` — SFP via wick past %-threshold + close back
- `momentum` — MSS only (breakout mode, no wick reversal applicable)

---

### Phase 2 — Signal Quality Filters

Three optional pre-entry filters that can block low-probability setups.  All default to `False`.

#### Fair Value Gaps (`use_fvg_filter=True`)
```python
from strategies import compute_fair_value_gaps

fvg = compute_fair_value_gaps(df, min_gap_pct=0.1)
# Columns: bull_fvg_top, bull_fvg_bot, bear_fvg_top, bear_fvg_bot
# Active = gap not yet filled.
# Mitigation: zone removed only when price CLOSES through its bottom/top
#   (NOT when price merely overlaps — overlap-based removal caused all zones
#   to disappear at the entry bar, blocking every trade)

equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_fvg_filter=True, fvg_data=fvg,
    fvg_proximity_atr=0.5)   # accept entries within 0.5×ATR of zone edge
# Semantics (v5): OPTIONAL confirmation, not a hard requirement.
#   No active FVG at this bar → trade passes through (FVGs are not always open).
#   Active FVG exists AND price is outside zone+tolerance → trade blocked.
#   Active FVG exists AND price is inside zone+tolerance → trade allowed.
```

#### Order Blocks (`use_ob_filter=True`)
```python
from strategies import compute_order_blocks

ob = compute_order_blocks(df, atr_mult=1.5, min_move_atr=1.0)
# Columns: bull_ob_high, bull_ob_low, bear_ob_high, bear_ob_low
# Bullish OB  = last bearish candle before a ≥1×ATR impulse move up (demand zone)
# Bearish OB  = last bullish candle before a ≥1×ATR impulse move down (supply zone)
# Mitigation: zone removed when price CLOSES through its low/high (not on overlap)
#   min_move_atr default changed from 2.0 → 1.0 (2.0 was only qualifying rare ≥2×ATR bars)

equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_ob_filter=True, ob_data=ob,
    fvg_proximity_atr=0.5)
# Semantics (v5): same optional-confirmation semantics as FVG filter above.
#   No active OB → trade passes through.
```

#### Equal Highs/Lows (`use_eqhl_filter=True`)
```python
from strategies import compute_equal_highs_lows

eqhl = compute_equal_highs_lows(df, tolerance_pct=0.05, lookback=20)
# Columns: eqh_level, eql_level (NaN if no cluster at this bar)
# Equal Highs (EQH) = two+ swing highs within tolerance_pct% → resting sell-side liquidity
# Equal Lows  (EQL) = two+ swing lows  within tolerance_pct% → resting buy-side liquidity

equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_eqhl_filter=True, eqhl_data=eqhl)
# Effect: LONG entries require price near an EQL cluster (buy-side liquidity sweep)
#         SHORT entries require price near an EQH cluster (sell-side liquidity sweep)
```

---

### Phase 3 — Order Flow & Entry Precision

#### CVD (Cumulative Volume Delta) Confirmation
```python
from strategies import compute_cvd

# CVD is computed internally by run_vwap_scalping when use_cvd_filter=True
equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_cvd_filter=True, cvd_window=20)
# Effect: LONG entries blocked if CVD slope is negative (selling pressure not exhausted)
#         SHORT entries blocked if CVD slope is positive (buying pressure not exhausted)
# cvd_window: bars for rolling CVD sum (default 20)
```

CVD is also available for Momentum Scalping:
```python
equity, trades = run_momentum_scalping(df, cutoff,
    use_cvd_confirm=True, cvd_window=20, **params)
```

#### OTE (Optimal Trade Entry) Zone
```python
equity, trades = run_vwap_scalping(df, cutoff, **params,
    require_mss=True, use_ote=True)
# OTE requires require_mss=True (needs the dealing range: SFP low → BOS level)
# Fib levels: 0.618–0.786 of the SFP dealing range
# Effect: after BOS confirmed, wait for price to retrace into the OTE zone before entry
# Gives tighter entries (better R:R) but reduces trade frequency
```

---

### Dynamic Regime Mode (v5)

**`use_dynamic_mode=True`** — auto-selects `entry_mode` per bar from 1h ADX instead of using a single fixed mode.

```python
equity, trades = run_vwap_scalping(df, cutoff=0.10, **VS_DEFAULTS,
    use_dynamic_mode=True,   # overrides entry_mode on a per-bar basis
    htf_adx_max=25.0)        # 1h ADX threshold — above = trending, below = ranging
```

**Per-bar mode selection logic:**
```
1h ADX ≥ htf_adx_max AND 1h EMA(9) > EMA(21)  →  bull_pullback
1h ADX ≥ htf_adx_max AND 1h EMA(9) < EMA(21)  →  bear_pullback
1h ADX  < htf_adx_max (ranging 1h)             →  mean_reversion
No 1h data available                            →  cross  (fallback)
```

> **Why 1h ADX, not 5m ADX:** Individual 5m pullback candles often have localised momentum
> that pushes 5m ADX above the threshold even during a clear macro trend. Using 1h ADX gives
> a stable regime classification that doesn't flip bar-by-bar within a trending move.

**Verified behaviour (BTC, default params):**

| Period | Mode | Trades | Win Rate | Profit Factor |
|--------|------|--------|----------|---------------|
| 2021 Q1–Q3 bull run | `dynamic` | 20 | 30% | 0.40 |
| 2021 Q1–Q3 bull run | `bull_pullback` fixed | 12 | 50% | 1.63 |
| 2022 Q1–Q3 bear run | `dynamic` | 18 | 11% | 0.19 |
| 2022 Q1–Q3 bear run | `bear_pullback` fixed | 8 | 25% | 0.16 |

Dynamic mode produces more trades (mixes all modes) but lower per-trade quality in single-direction markets. It is most valuable in markets that alternate between trending and ranging phases within the test window.

---

### New parameters for other strategies (v4)

| Strategy | Parameter | Default | Effect |
|----------|-----------|---------|--------|
| Mean Reversion | `use_sfp_entry` | `False` | Require SFP wick on daily bar before RSI+BB entry |
| Mean Reversion | `use_poc_tp` | `False` | Use prev_day_poc as TP instead of fixed ATR target |
| Grid Trading | `use_poc_center` | `False` | Center grid around prev_day_poc instead of current price |
| Grid Trading | `use_va_bounds` | `False` | Set grid outer bounds to prev_day_vah / prev_day_val |
| MA Crossover | `use_va_chop_filter` | `False` | Block entries when price is inside prior-day Value Area (choppy) |
| Momentum Scalping | `use_cvd_confirm` | `False` | CVD slope must agree with entry direction |
| Momentum Scalping | `cvd_window` | `20` | Bars for CVD rolling window |
| Liquidation Capture | `use_nearest_tp` | `False` | Use _nearest_tp to target the closest reference level |

All require `levels=compute_reference_levels(df)` to be passed when the feature is active.

---

### Recommended Phase 1–3 test sequence

Start conservative — enable one feature at a time, compare OOS Sharpe before/after:

```python
# Step 1: Baseline (UTM already on by default via VS_DEFAULTS)
equity, trades = run_vwap_scalping(df, 0.10, **VS_DEFAULTS)

# Step 2: Add reference levels + nearest TP
levels = compute_reference_levels(df)
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "levels": levels, "tp_mode": "nearest"})

# Step 3: Add MSS confirmation
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "levels": levels, "tp_mode": "nearest",
    "require_mss": True, "mss_timeout_bars": 6})

# Step 4: Add FVG filter (optional confirmation — no FVG = trade passes through)
fvg = compute_fair_value_gaps(df)
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "levels": levels, "tp_mode": "nearest",
    "require_mss": True,
    "use_fvg_filter": True, "fvg_data": fvg,
    "fvg_proximity_atr": 0.5})

# Step 5: Try dynamic mode (auto-selects bull/bear/reversion per bar)
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "levels": levels, "tp_mode": "nearest",
    "use_dynamic_mode": True, "htf_adx_max": 25.0})

# Step 6: Add OTE zone (tightest — lowest trade count, highest quality)
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "levels": levels, "tp_mode": "nearest",
    "require_mss": True, "use_ote": True,
    "use_fvg_filter": True, "fvg_data": fvg,
    "fvg_proximity_atr": 0.5})
```

Keep a feature if it improves OOS Sharpe by >0.1 and maintains ≥10 trades/year.  Drop it if trade count falls below 5/year — signal quality is meaningless without statistical volume.

---

## Critical Rules (Do Not Violate)

### 1. Butterworth filter must be `lfilter`, never `filtfilt`
```python
# CORRECT — causal, forward-only, no lookahead
from scipy.signal import butter, lfilter
b, a = butter(2, cutoff, btype='low')
filtered = lfilter(b, a, close.values)

# WRONG — non-causal, introduces future data into indicators
from scipy.signal import butter, filtfilt
filtered = filtfilt(b, a, close.values)   # ← DO NOT USE in backtests
```

### 2. Cost deducted at every entry AND exit
```python
COST_PER_SIDE = 0.0015   # 0.10% fee + 0.05% slippage

# Entry
curr_equity *= (1.0 - COST_PER_SIDE)

# Exit
curr_equity *= (1.0 - COST_PER_SIDE)
```
Total round-trip cost: **0.30%**. Realistic for major exchanges.

### 3. SL checked before TP on the same bar (coarse mode)
```python
if side == "long":
    if low <= sl_price:    hit = True   # SL first (conservative)
    elif high >= tp_price: hit = True   # TP only if SL not triggered
```
Sub-bar mode (`df_exit`) eliminates this ambiguity by iterating actual 5m/1m candles.

### 4. Sharpe scaling must match candle interval
```python
# WRONG — equity-market convention, understates crypto Sharpe
sharpe = daily_rets.mean() / daily_rets.std() * math.sqrt(252)

# CORRECT — 24/7 crypto, 5m bars (VWAP Scalping native interval)
sharpe = bar_rets.mean() / bar_rets.std() * math.sqrt(105120)

# Other intervals:
#   15m (Momentum Scalping)  → math.sqrt(35040)
#   1h                       → math.sqrt(8760)
#   4h (Grid Trading)        → math.sqrt(2190)
#   1d (Mean Reversion, etc) → math.sqrt(365)
```

### 5. Never apply proposals without OOS confidence
Only apply agent proposals when:
- `n_trades >= 10` (statistical floor)
- `sharpe_delta > 0.10` (meaningful improvement)
- `oos_sharpe > 0.0` (strategy is net positive after costs)

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'strategies'`**
```bash
cd Bot3/BTV2
python your_script.py   # must run from BTV2/ directory
# OR
sys.path.insert(0, "/path/to/Bot3/BTV2")
```

**`G:\Candle Data\ does not exist`**
`data_manager.py` auto-creates the directory on first call to `get_candles()`. Ensure `G:` drive is mounted before running.

**`.env` not found / wrong path**
`agent.py` walks up 8 directory levels looking for a `.env` containing `AGENT_WALLET_PRIVATE_KEY`. If your `.env` uses a different key, edit `_find_env_path()` in `agent.py`.

**VWAP Scalping win rate stuck at 33–41% (or below)**

Root cause: entries firing during impulse legs, not genuine pullbacks. Even with HTF filters, if the entry mode is too permissive the bot enters while the market is still accelerating.

Systematic fix in order:

1. **Sidebar setup**: Strategy = VWAP Scalping, Interval = 5m, Exit Resolution = 1m
2. **Confirm entry mode**: `entry_mode = "bull_pullback"` in VS_DEFAULTS. Requires strict 1h uptrend + price below 1h VWAP + Stochastic oversold cross + RSI divergence.
3. **Confirm N+1 is active**: entries use `opens[i+1]` (next bar open) and SL/TP anchored to signal candle close — prevents entering mid-spike.
4. **Confirm MTF**: `use_htf_ema=True` in VS_DEFAULTS (required for pullback modes to compute 1h VWAP and EMA direction).
5. **Run Agent Sweep** — tunes 8 .env keys (`sd_threshold`, `atr_stop`, `atr_target`, `volume_mult`, `adx_max`, `rsi_max`, `stoch_oversold`, `stoch_overbought`) + Butterworth cutoff
6. **Expected**: WR 33–41% → 50–65%, fewer but higher-probability trades

If WR remains below 46% after sweep:
- Raise `sd_threshold` to 2.5–3.0 (require deeper pullback before entry)
- Lower `rsi_max` to 35–40 (require more extreme oversold)
- Lower `stoch_oversold` to 25–30 (stricter exhaustion gate)
- Try `entry_mode = "bear_pullback"` for shorts only (if long setups underperform)
- Check OOS Sharpe: if < 0.3 after all tuning, VWAP scalping has structural edge issues this period; switch primary to Grid Trading

**`Liquidation Capture` returns 0 trades**
Expected on daily BTC bars — this strategy fires ~0–5 times per year. Use a shorter `--start` / `--end` range around known crash events (Mar 2020, May 2021, Nov 2022) to see it activate.

**`MA Crossover` / `Momentum Scalping` return very few trades**
Both strategies have tight entry filters (volume gate, MACD confirmation, pullback window). On calm data this is correct. OOS metrics with < 5 trades should be treated as inconclusive.

**Sweep takes > 5 minutes**
Reduce the date range (shorter period = fewer folds = faster sweep). Number of OOS evaluations = `7 + 5 × n_params × n_folds`. For VWAP Scalping (6 .env params, 5m data, 2yr ≈ 21 folds): `7 + (5 × 6 × 21) = 637` evaluations. ⚠️ 5m data is 12× denser than 1h AND the MTF pre-computation (15m VWAP + 1h EMA/ADX resample) adds ~10ms per fold. For faster iteration reduce to 1yr (≈ 9 folds → `7 + (5 × 6 × 9) = 277` evaluations). The walk-forward train sweep (`optimize_strategy`) over VS_GRID = 324 combos runs once per fold — this is the bottleneck on 5m data with large date ranges.

**Sharpe looks unexpectedly low/high after changing interval**
Verify you're passing the correct `bars_per_year` to `compute_metrics`. Common mistakes:
- Using default `365` for VWAP Scalping on 5m data (should be `105120`)
- Using `8760` (old 1h convention) for VWAP Scalping after the LTF migration
- Using `8760` for Momentum Scalping on 15m data (should be `35040`)

**`compute_reference_levels` AttributeError on daily-resolution data**
`df.loc["2020-01-01"]` returns a Series (not DataFrame) when `df` has daily bars. The code guards this:
```python
if isinstance(day_data, pd.Series):
    day_data = day_data.to_frame().T
```
If you see `AttributeError: 'Series' object has no attribute 'High'`, you are running reference-level computation on a non-intraday `df` — this is supported but the guard must be present (check your `strategies.py` version).

**`require_mss=True` gives 0 trades**
MSS requires a BOS candle within `mss_timeout_bars` bars after the SFP.  If the timeout is too short for the interval, nearly all pending signals expire. Defaults: `mss_timeout_bars=6` (6 × 5m = 30 minutes). For 1h data try `mss_timeout_bars=3`. Verify SFP signals are firing by temporarily setting `require_mss=False` and confirming trade count is > 0.

**`tp_mode="nearest"` picks very distant levels**
`_nearest_tp_long` selects the minimum price above entry × 1.001. On low-volatility sessions, all reference levels may be far away — the fallback ATR target is used instead. This is correct behaviour. If you want tighter TP, switch to `tp_mode="vwap"` or `"pdh"`.

**Phase 2 filters (`use_fvg_filter`, `use_ob_filter`, `use_eqhl_filter`) give 0 trades**
Root cause is usually stale `.pyc` cache serving old code. Delete `__pycache__/` and retry:
```bash
Get-ChildItem -Recurse -Filter "*.pyc" | Remove-Item -Force
```
If still zero after cache clear: the filter is using overlap-based mitigation (old code) that removes zones at the exact entry bar. Verify `strategies.py` uses close-based mitigation:
```python
# CORRECT (v5) — zone survives while price is inside:
active_bull = [(b, t) for (b, t) in active_bull if not (closes[i] < b)]
# WRONG (pre-v5) — zone removed on first overlap:
# active_bull = [(b, t) for (b, t) in active_bull if not (lows[i] <= t and highs[i] >= b)]
```
Also verify filter semantics: `if not math.isnan(bft) and not (...)` (v5 — optional)
vs `if math.isnan(bft)` (pre-v5 — required, always blocks when no zone).

**Phase 2 filters drastically reduce but don't zero trade count**
Expected behaviour. Each filter reduces count 30–70%. Enable one at a time and validate OOS Sharpe improvement before stacking. Tune `fvg_proximity_atr` (default 0.5) upward to relax the zone proximity requirement if count is too low.

**`use_ote=True` gives 0 trades**
OTE requires `require_mss=True` (to establish the dealing range for Fib calculation). Without MSS, no dealing range exists and OTE is never triggered. Always set `require_mss=True, use_ote=True` together.

**`bull_pullback` or `bear_pullback` gives 0 trades on trending data**
Pre-v5 code had two gates that collectively blocked all pullback trades:
1. `ranging` gate: required 5m ADX < adx_max — pullback candles inside a trend often have localised 5m momentum above the threshold.
2. `recent_hh` / `recent_ll`: required current bar's high > highs 1–3 hours ago — pullback bars by definition have lower highs.
Both were removed in v5. Verify your `strategies.py` does NOT contain `recent_hh` or `ranging` in the `bull_pullback` condition block.

**`use_dynamic_mode=True` always routes to `mean_reversion` (no pullback trades)**
Dynamic mode uses 1h ADX vs `htf_adx_max` (default 25.0) to classify trending vs ranging. If your data period has consistently low 1h ADX (e.g. 2019 or late 2022 sideways), the mode will classify all bars as ranging and route to `mean_reversion`. To confirm: print `htf_adx_1h_ff.describe()` — if mean ADX < 20 for your period, lower `htf_adx_max` to 18–20.

**`_nearest_tp` selects an unreasonably high/low price (e.g. 250 or 0)**
`session_vwap_std` is a standard deviation value (~250), not a price level. In v5 it is excluded from TP candidate selection via `_NEAREST_TP_EXCLUDE`. If you see nonsensical TP values, verify your `strategies.py` defines:
```python
_NEAREST_TP_EXCLUDE = {"session_vwap_std"}
```
and that `_nearest_tp_long/short` iterate `levels_row.items()` (not `.values()`) to check keys against the exclusion set.
