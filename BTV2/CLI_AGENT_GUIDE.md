# BTV2 — CLI Agent Integration Guide  (v8)

How to programmatically drive the walk-forward backtesting system, interpret its outputs, and wire it into an automated improvement loop with a CLI coding agent (Gemini, Claude Code, glitch, etc.).

> **v5 additions:** `optimizer_agent.py` (Optuna TPE autonomous optimizer), `optimize.md` (program file), `STRICT_VALIDATION` mode in `strategies.py`, Grid Trading `.env` parameter fix, Dark Factory RAG experiment logging.
>
> **v6 additions (2026-04-12):** VWAP strategy root-cause fix — 5 structural issues patched in `strategies.py`. Butterworth no longer distorts VWAP bands; SD threshold raised to 3.0+; filter stack reduced; exit logic redesigned. Optimizer `SEARCH_SPACES` expanded with categorical params (`entry_mode`, `tp_mode`, filter toggles). `build_objective` updated to handle `"cat"` type specs. Truncated `compute_metrics` repaired. See [VWAP v6 Fix](#vwap-v6-fix) for full details.
>
> **v7 additions (2026-04-24):** Three validation layers added to `strategies.py`: Monte Carlo simulation (`monte_carlo_validate`), parameter robustness/fragility score (`compute_robustness_score`), and GMM regime detection (`fit_gmm_regime` / `predict_gmm_regime` / `gmm_regime_summary`). New `btv2_validate.py` runner combines all layers into a single PASS/CAUTION/FAIL verdict. `compute_metrics` now accepts `bars_per_year` param (default 365); `INTERVAL_BARS_PER_YEAR` constant added. `scikit-learn>=1.3.0` added to requirements. See [Validation Layers (v7)](#validation-layers-v7) for full details.
>
> **v8 additions (2026-04-25):** VS_GRID and VS_DEFAULTS corrected based on 350+ empirical test findings (`VWAP_Complete_Results_Summary.md`). `use_htf_ema` default changed True → **False** (True blocks ALL trades — empirically confirmed). `entry_mode` default changed `mean_reversion` → **`bull_pullback`** (#1 variable by importance, +118%). `sd_threshold` grid floor lowered 2.5 → **1.5**. `volume_mult` raised 1.2 → **2.0**. Full v6 walk-forward validation run: **FAIL** (OOS Sharpe -3.84, 64 trades, WR 27%, MC 100% loss prob). Live bot `vwap_scalping.py` NOT updated — validation prerequisite not met. See [v8 Validation Findings](#v8-validation-findings) for full details.
>
> **v8 additions (2026-04-28):** Major new section [Multi-Strategy & Regime Testing — Correct Workflow](#multi-strategy--regime-testing--correct-workflow) added. Prohibits creating custom test files with own EMA/RSI implementations. Documents correct workflow using `STRATEGY_REGISTRY` + `data_manager.py` + `btv2_validate.py` per year. Raises `n_trades` floors: 30 (daily strategies) / 50 (intraday). ADX/GMM-based regime detection required — hardcoded year labels are prohibited (lookahead bias). Documents `regime_strategy_settings.py` 2026-hardcoded-BEARISH lookahead bias. Clarifies grid regime transition test is a logic unit test, not P&L validation. Updated Quality Gates table and Critical Rule #6 to new trade-count thresholds.

---

## System Overview

```
Bot3/
  .env                      ← live trading bot config (100+ vars, strategy params)
  research/
    OPTIMIZATION_PIPELINE_DESIGN.md  ← gap analysis + design doc (2026-04-11)
  BTV2/
    app.py                  ← Streamlit dashboard (human UI layer)
    strategies.py           ← pure-Python backtest engine (no UI imports)
    agent.py                ← .env reader/optimizer (coordinate descent, v3)
    optimizer_agent.py      ← Optuna TPE autonomous optimizer (v5)
    optimize.md             ← program file: what to optimize + constraints
    btv2_validate.py        ← 3-layer validation runner (v7)  ← NEW
    data_manager.py         ← Binance downloader + local Parquet cache
    requirements.txt
    CLI_AGENT_GUIDE.md      ← this file
    results/
      optimizer_trials.jsonl    ← trial log (one JSON line per Optuna trial)
    optimization_studies.db     ← Optuna SQLite (persistent across restarts)

G:\Candle Data\             ← local Parquet cache (auto-created on first run)
  BTCUSDT_1m.parquet
  BTCUSDT_5m.parquet
  BTCUSDT_15m.parquet
  BTCUSDT_1h.parquet
  BTCUSDT_4h.parquet
  BTCUSDT_1d.parquet
```

The key architectural decision: **`strategies.py`, `agent.py`, `optimizer_agent.py`, and `data_manager.py` are pure compute modules** — no Streamlit, no UI, no side effects. A CLI agent can import and call them directly as Python functions without ever touching the browser.

### Two optimization paths (v3 vs v5)

| | `agent.py` (v3) | `optimizer_agent.py` (v5) |
|---|---|---|
| Search method | Coordinate descent × multipliers | Optuna TPE (Bayesian) |
| Parameter proposals | 5 × N evaluations | 50–100 trials total |
| Speed | Fast (< 2 min/strategy) | Slower but smarter (20–90 min) |
| Persistent study | No | Yes (SQLite, resumable) |
| .env update | Manual `--apply` flag | Auto if Sharpe Δ ≥ 0.05 |
| RAG logging | No | Yes (Dark Factory Hub) |
| Strict-validation mode | No | Yes (`--strict` flag) |
| Best for | Quick daily checks | Deep overnight optimization |

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
| VWAP Scalping | 5m | 3m | 1m | LTF bars for high signal frequency; 1h EMA/ADX MTF filters; use 1m exit-res |
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
interval = tf_cfg["interval"]   # "5m"

df = get_candles("BTCUSDT", interval,
                 datetime(2022, 1, 1, tzinfo=timezone.utc),
                 datetime(2024, 1, 1, tzinfo=timezone.utc))

func, grid, defaults = STRATEGY_REGISTRY[strategy_name]

# Basic backtest — v8 defaults (empirically derived from 350+ tests):
# entry_mode="bull_pullback", sd_threshold=2.0, use_htf_ema=False, volume_mult=2.0
equity, trades = func(df, cutoff=0.10, **defaults)

# With 1m sub-bar exit simulation — RECOMMENDED for VWAP Scalping on 5m bars.
df_1m = get_candles("BTCUSDT", "1m",
                    datetime(2022, 1, 1, tzinfo=timezone.utc),
                    datetime(2024, 1, 1, tzinfo=timezone.utc))
equity, trades = func(df, cutoff=0.10, **defaults, df_exit=df_1m)

# Tuning key parameters explicitly (bull_pullback mode — v8 default, #1 by variable importance):
equity, trades = func(df, cutoff=0.10,
    entry_mode       = "bull_pullback", # best mode: +118% vs mean_reversion (Phase 3)
    sd_threshold     = 2.0,    # sweet spot (Phase 10) — 2.0 = first positive config
    atr_stop         = 0.7,    # tight stop (v8 empirical)
    atr_target       = 3.0,    # wider ATR TP
    trailing_atr     = 1.2,    # trailing stop ratchet distance (key exit param)
    tp_mode          = "atr",  # "atr" = ATR backstop (trailing stop is primary)
    adx_max          = 30.0,   # 5m ADX < 30 (Phase 5 optimal)
    rsi_max          = 50.0,   # 5m RSI < 50 for longs
    volume_mult      = 2.0,    # 2.0x volume filter (Phase 6 optimal)
    use_stoch_filter = False,  # stochastic gate disabled by default
    use_htf_vwap     = True,   # 15m VWAP alignment (recommended in Phase 10 final config)
    use_htf_ema      = False,  # KEEP FALSE — True blocks ALL trades (Phase 7 confirmed)
    htf_adx_max      = 25.0,
    require_reversal_candle = False,  # SFP disabled
    df_exit          = df_1m,
)

# Mean reversion mode (secondary — worse than bull_pullback but trades both sides):
equity, trades = func(df, cutoff=0.10,
    entry_mode       = "mean_reversion",
    sd_threshold     = 2.5,
    use_htf_ema      = False,  # MUST be False — True blocks all trades
    use_htf_vwap     = True,
    volume_mult      = 2.0,
    df_exit=df_1m,
)

# ⚠️ MTF IMPORTANT: 15m and 1h data are derived by RESAMPLING the 5m df passed in.
# Do NOT load separate BTCUSDT_15m or BTCUSDT_1h files — it's automatic.
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
#   "avg_win_pct":        2.1,  # average winning trade return %  ← v6 added
#   "avg_loss_pct":      -1.0,  # average losing trade return %   ← v6 added
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
# Grid search over VS_GRID on training window (Sharpe-maximizing)
# v6 VS_GRID axes: sd_threshold [2.5,3.0,3.5,4.0], atr_stop [0.5,0.7,1.0,1.5],
#   adx_max [20,25,30], stoch_oversold [30,40,50], stoch_overbought [50,60,70],
#   trailing_atr [0.8,1.2,1.8]
# Returns keys matching VS_GRID axes
#
# Note: atr_target, MTF flags, tp_mode are NOT in grid — use VS_DEFAULTS or agent sweep
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
    │       [use_htf_vwap=True only — disabled by default in v6]
    │       htf_long_ok  &= (price < 15m_VWAP)
    │       htf_short_ok &= (price > 15m_VWAP)
    │
    └─── resample("60min") → EMA(9), EMA(21), ADX(14) → forward-fill to 5m index
            h_ranging = (1h_ADX < htf_adx_max)
            htf_long_ok  &= (1h_EMA9 > 1h_EMA21  OR  h_ranging)
            htf_short_ok &= (1h_EMA9 < 1h_EMA21  OR  h_ranging)
```

**Key architectural point:** no extra `get_candles()` calls are needed. The 15m and 1h are derived on-the-fly from the same 5m Parquet file you already loaded.

**Logic for a LONG entry — `mean_reversion` mode (v6 default — ranging markets):**
```
1. 5m:  price < VWAP − sd_threshold × σ_VWAP   (pulled back to lower VWAP band; SD≥3.0)
2. 5m:  ADX < adx_max                            (not strongly trending on 5m)
3. 5m:  RSI < rsi_max                            (not overbought on 5m; default 50)
4. 5m:  volume ≥ volume_mult × 20-bar avg        (real participation; default 1.2×)
5. 1h:  EMA(9) > EMA(21) OR ADX < htf_adx_max   (bullish OR ranging on 1h; default 30)
   [6. Stochastic gate: %K < stoch_oversold AND %K > %D  — optional, use_stoch_filter=False]
   [7. SFP reversal candle: wick below band + close above — optional, require_reversal_candle=False]
⟹ Entry at bar i+1 open (N+1), SL/TP anchored to bar i close
Exit: trailing stop is PRIMARY exit; ATR TP (2.0×) is backstop
```

**Logic for a LONG entry — `bull_pullback` mode (higher-quality, fewer trades):**
```
1. 5m:  price < VWAP − sd_threshold × σ_VWAP         (pulled back to lower VWAP band)
2. 5m:  RSI < rsi_max                                  (not overbought on 5m)
3. 5m:  volume ≥ volume_mult × 20-bar avg              (real participation)
4. 1h:  EMA(9) > EMA(21)                               (confirmed 1h uptrend — strict)
5. 1h:  price < 1h VWAP                                (dip not yet recovered to 1h mean)
[6. 5m:  Stoch(14,3,3) %K < stoch_oversold AND %K > %D (optional)]
[7. 5m:  RSI divergence: price lower low but RSI higher low (optional)]
⟹ Entry at bar i+1 open (N+1), SL/TP anchored to bar i close
```

**`bear_pullback` is the exact mirror** (1h EMA bearish, price above upper band, above 1h VWAP).

**VWAP band computation (v6 fix — RAW close only):**
```
VWAP uses raw df["Close"] — NOT Butterworth-filtered close.
Butterworth compresses price deviation, making SD thresholds unreachable on real data.
EMA and RSI still use Butterworth-filtered close (trend smoothing is appropriate there).
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
    bars_per_year=bars_py,
)
# best_params  : dict — parameter set with highest OOS Sharpe found
# best_cutoff  : float — optimal Butterworth cutoff found by sweep
# best_sharpe  : float — OOS Sharpe of (best_cutoff, best_params)
# trial_log    : list[dict] — one row per (parameter × multiplier) trial
```

**v3 sweep order:**
1. **Butterworth cutoff** — swept first (7 candidates: ×[0.50, 0.70, 0.85, 1.00, 1.15, 1.30, 1.50], clamped to [0.05, 0.45])
2. **Strategy parameters** — swept using best cutoff found in step 1 (×[0.70, 0.85, 1.00, 1.15, 1.30])

**VWAP Scalping .env keys swept (v3):**

| ENV Variable | Internal kwarg | v6 Default | Sweep range | Notes |
|---|---|---|---|---|
| `VWAP_SD_ENTRY_THRESHOLD` | `sd_threshold` | 3.0 | ×[0.70–1.30] | Raised from 2.0 — below 2.5 loses after costs |
| `VWAP_ATR_STOP_MULTIPLIER` | `atr_stop` | 1.0 | ×[0.70–1.30] | Raised from 0.7 — 5m noise needs room |
| `VWAP_ATR_TARGET_MULTIPLIER` | `atr_target` | 2.0 | ×[0.70–1.30] | Lowered from 3.8 — trailing stop is primary exit |
| `VWAP_TRAILING_ATR` | `trailing_atr` | 1.2 | ×[0.70–1.30] | NEW — key exit param |
| `VWAP_VOLUME_MULTIPLIER` | `volume_mult` | 1.2 | ×[0.70–1.30] | Lowered from 2.0 |
| `ADX_TRENDING_THRESHOLD` | `adx_max` | 25.0 | ×[0.70–1.30] | 5m ranging gate |
| `VWAP_RSI_MAX` | `rsi_max` | 50.0 | ×[0.70–1.30] | Raised from 40 — more permissive |

**VS_GRID exhaustive axes (v8 corrected — ranked by variable importance):**

| Grid param | Values | v8 Default | Notes |
|---|---|---|---|
| `entry_mode` | ["mean_reversion", "bull_pullback"] | "bull_pullback" | **#1 variable** — bull_pullback +118% vs MR (Phase 3) |
| `sd_threshold` | [1.5, 2.0, 2.5, 3.0] | 2.0 | Sweet spot at 2.0 (Phase 10) |
| `volume_mult` | [1.5, 2.0, 2.5] | 2.0 | **#2 variable** — higher = better quality (Phase 6) |
| `adx_max` | [20.0, 25.0, 30.0] | 30.0 | 30 optimal (Phase 5) |

72 combos total. `atr_stop`, `atr_target`, `trailing_atr`, and filter toggles are explored by the Optuna optimizer (not exhaustive grid).

**Multi-timeframe filter parameters (v8 empirically derived defaults):**

| Python kwarg | v8 Default | Effect |
|---|---|---|
| `use_htf_ema` | **`False`** | **KEEP FALSE** — True blocks ALL trades (Phase 7, empirically confirmed) |
| `use_htf_vwap` | `True` | 15m VWAP alignment gate — recommended in final config (Phase 10) |
| `htf_adx_max` | `25.0` | 1h ADX threshold |
| `use_stoch_filter` | `False` | Stochastic momentum exhaustion gate — optional |
| `require_reversal_candle` | `False` | SFP: wick into band + close outside — disabled |
| `tp_mode` | `"atr"` | Exit target mode (trailing stop is primary, ATR is backstop) |

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

### `optimizer_agent.py`  (v5 — Optuna TPE autonomous optimizer)

The v5 optimizer implements the **autoresearch pattern** adapted for trading:

```
read optimize.md → propose params via Optuna TPE → run walk-forward
→ evaluate OOS Sharpe → log trial to RAG + JSONL → update .env if improved → repeat
```

#### Quick Start
```bash
cd C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3

# Run from optimize.md (recommended — reads strategy/period/trials from file)
python BTV2/optimizer_agent.py --from-program

# Or specify directly
python BTV2/optimizer_agent.py --strategy "VWAP Scalping" --n-trials 80

# Dry run — find best params but don't touch .env
python BTV2/optimizer_agent.py -s "VWAP Scalping" -n 50 --dry-run

# Enable STRICT_VALIDATION (mirrors live bot signal gates — volume + RRR filters)
python BTV2/optimizer_agent.py --from-program --strict

# Resume an interrupted overnight run
python BTV2/optimizer_agent.py --from-program --resume

# List available strategies + their search-space sizes
python BTV2/optimizer_agent.py --list-strategies
```

#### `SEARCH_SPACES` — strategy search space registry (v6 updated)
```python
from optimizer_agent import SEARCH_SPACES

# Keys: strategy names
# Each entry has: run_fn, defaults, params (Optuna spec), fixed, strict_params, env_map
print(list(SEARCH_SPACES.keys()))
# ['Mean Reversion', 'VWAP Scalping', 'Momentum Scalping', 'MA Crossover', 'Liquidation Capture']

# VWAP Scalping search space (v6 — expanded with categoricals):
for spec in SEARCH_SPACES["VWAP Scalping"]["params"]:
    name, kind = spec[0], spec[1]
    rest = spec[2]
    print(f"  {name}: {kind} → {rest}")
# sd_threshold:    float → (2.5, 4.5)           # raised floor
# atr_stop:        float → (0.5, 2.0)            # widened
# trailing_atr:    float → (0.6, 2.0)            # NEW — key exit param
# adx_max:         float → (15.0, 40.0)
# volume_mult:     float → (0.8, 2.0)            # lowered floor
# rsi_max:         float → (40.0, 60.0)          # NEW
# entry_mode:      cat   → ["mean_reversion", "bull_pullback", "cross", "deviation"]
# tp_mode:         cat   → ["atr", "vwap", "pdh"]
# use_stoch_filter:cat   → [True, False]
# use_htf_vwap:    cat   → [True, False]
# require_reversal_candle: cat → [True, False]

# ⚠️ "cat" type uses suggest_categorical(name, choices) — choices at spec[2]
# Optuna samples from the full space; VS_GRID is used for the exhaustive training sweep only.
```

#### `evaluate_params(strategy_name, params, df, start, end) → dict`
```python
from optimizer_agent import evaluate_params, load_data, SEARCH_SPACES

df = load_data("BTC-USD", "1d")
params = {**SEARCH_SPACES["Mean Reversion"]["defaults"], "rsi_overbought": 75.0}

result = evaluate_params("Mean Reversion", params, df)
# {
#   "status":           "ok",
#   "mean_oos_sharpe":  0.724,
#   "total_return_pct": 41.2,
#   "cagr_pct":         7.1,
#   "max_dd_pct":       -28.4,
#   "win_rate_pct":     54.3,
#   "profit_factor":    1.38,
#   "n_trades":         87,
#   "n_windows":        20,
#   "consistency_pct":  65.0,    # % of windows with Sharpe > 0
#   "params":           {...},
#   "windows":          [{"fold": 1, "sharpe": 0.8, "n_trades": 4, ...}, ...]
# }
```

#### `run_optimization(strategy_name, ticker, n_trials, strict, dry_run) → dict`
```python
from optimizer_agent import run_optimization

result = run_optimization(
    strategy_name = "VWAP Scalping",
    ticker        = "BTC-USD",
    n_trials      = 80,
    timeout_minutes = 90,
    strict        = False,
    dry_run       = True,        # set False to auto-update .env on improvement
    resume        = False,
)
# Best params written to .env if OOS Sharpe improves by ≥ 0.05 (and dry_run=False)
```

#### `optimize.md` — the program file
Edit `BTV2/optimize.md` to control what the optimizer works on.
```markdown
## Strategy: VWAP Scalping
## Ticker: BTC-USD
## Period: 2022-01-01 to 2025-12-31
## Trials: 80
## Timeout: 90
## Strict: false
```
Run with `python BTV2/optimizer_agent.py --from-program` to consume it.
Switch the strategy line to advance the rotation (see **Research Plan** below).

#### Reading results
```bash
# Quick best-params lookup from the trial log
python -c "
import json
from pathlib import Path
log = Path('BTV2/results/optimizer_trials.jsonl')
trials = [json.loads(l) for l in log.read_text().splitlines() if l]
best = max([t for t in trials if t.get('status')=='ok' and t.get('strategy')=='VWAP Scalping'],
           key=lambda t: t['mean_oos_sharpe'], default=None)
if best:
    print(f'OOS Sharpe: {best[\"mean_oos_sharpe\"]:.4f}')
    print(f'Params: {best[\"params\"]}')
"

# Visual search-space exploration (requires optuna-dashboard)
optuna-dashboard sqlite:///BTV2/optimization_studies.db
```

#### `STRICT_VALIDATION` mode — bridging backtest and live bot
The live bot requires all 8 flags to be True before placing any order.
BTV2 doesn't model that — so raw backtest results are optimistic.

Run with `--strict` to enable two flags in `run_mean_reversion`:
- **`strict_volume_mult`**: bar volume must exceed N× rolling 20-bar average
- **`strict_min_rrr`**: reward/risk at signal time must be ≥ threshold

```python
# Normal vs strict mode comparison (useful for calibration)
eq1, t1 = run_mean_reversion(df, 0.10, use_regime_filter=True)
eq2, t2 = run_mean_reversion(df, 0.10, use_regime_filter=True,
                              strict_validation=True,
                              strict_volume_mult=1.2,
                              strict_min_rrr=1.0)
# strict mode will produce <= trades
# Calibration target: strict-mode OOS Sharpe ≥ 70% of normal-mode
```

---

## VWAP v6 Fix

_Added 2026-04-12 · Agent: glitch_

Root-cause analysis revealed 5 structural issues that caused VWAP Scalping to lose money even with apparently good parameters.

### Issue 1 — Butterworth Was Distorting VWAP Bands (CRITICAL)

**Root cause:** `compute_vwap_anchored()` was receiving `fc` (Butterworth low-pass filtered close) instead of raw close prices. The filter compresses price deviation around the mean — so VWAP bands became narrower than they should be, making `sd_threshold=2.0` effectively trigger on much smaller deviations than intended on real data.

**Fix:** VWAP and its standard deviation now use `raw_close = df["Close"]`. EMA and RSI still use the Butterworth-filtered `fc` (trend smoothing is appropriate there).

```python
# v5 (broken):
fc = apply_butterworth(df["Close"], cutoff)
vwap, vs = compute_vwap_anchored(df["High"], df["Low"], fc, df["Volume"])  # ← wrong

# v6 (fixed):
fc = apply_butterworth(df["Close"], cutoff)
raw_close = df["Close"]
vwap, vs = compute_vwap_anchored(df["High"], df["Low"], raw_close, df["Volume"])  # ← raw
```

### Issue 2 — SD Threshold Too Low (CRITICAL)

**Root cause:** Default `sd_threshold=2.0`. With correct raw VWAP bands, 2σ entries occur too frequently and don't have enough edge to overcome the 0.30% round-trip cost. Historical best-found was `sd_threshold=3.45` with 73.3% WR.

**Fix:**
- `VS_DEFAULTS["sd_threshold"]` raised from 2.0 → **3.0**
- `VS_GRID["sd_threshold"]` range raised from [1.5, 2.0, 2.5, 3.0] → **[2.5, 3.0, 3.5, 4.0]**
- Optimizer search range widened to **[2.5, 4.5]**

### Issue 3 — Exit Logic Mismatch (HIGH)

**Root cause:** `tp_mode="vwap"` set TP at the session VWAP mean. For a 3σ entry, the VWAP mean is already very close to entry — resulting in tiny gains on winning trades. Meanwhile, the trailing stop was responsible for 56.3% of all exits (capturing larger moves). The strategy was profiting from trend-following behavior but fighting its own fixed TP.

**Fix:**
- `tp_mode` changed from `"vwap"` → **`"atr"`** (ATR backstop, not VWAP mean)
- `atr_target` lowered from 3.8 → **2.0** (realistic backstop; trailing stop does heavy lifting)
- `trailing_atr` added to VS_GRID and optimizer search space as a first-class param

### Issue 4 — Filter Stack Too Restrictive (HIGH)

**Root cause:** 6+ sequential filters were required simultaneously. With real BTC data this produced as few as 3 trades in 2024. Stochastic crossout + SFP reversal candle + 15m VWAP alignment together filtered >95% of valid setups.

**Fix (all togglable by optimizer):**
- `use_stoch_filter` new param, **default False** — reduces filter count by 1
- `use_htf_vwap` changed from True → **False** — removes 15m VWAP alignment
- `require_reversal_candle` changed from True → **False** — removes SFP requirement
- `rsi_max` raised from 45 → **50** — more permissive oversold gate
- `volume_mult` lowered from 1.5 → **1.2** — less aggressive volume gate
- `htf_adx_max` raised from 25 → **30** — wider 1h ranging tolerance

### Issue 5 — Cost Model vs Win Rate (CONTEXT)

**Root cause:** 0.30% round-trip cost on 5m bars is lethal for low-WR configs. A 40% WR needs ≥2.5:1 R:R to break even, plus margin for slippage variance. The prior `sd_threshold=2.0` + `atr_stop=0.7` combination produced configs that mathematically couldn't survive costs.

**Fix:** The combination of SD≥3.0 (higher-quality entries), `atr_stop=1.0` (stops that survive 5m noise), and trailing stop as primary exit creates a cost-viable structure. The optimizer is now free to find the exact params that achieve this.

### Summary of v6 Default Changes (updated to v8 empirical values)

| Parameter | v5 Default | v6 Default | v8 Default (empirical) | Rationale |
|-----------|-----------|-----------|------------------------|-----------|
| `sd_threshold` | 2.0 | 3.0 | **2.0** | Sweet spot; Phase 10: SD=2.0 = first positive config |
| `atr_stop` | 0.7 | 1.0 | **0.7** | Empirically reverted |
| `atr_target` | 3.0 | 2.0 | **3.0** | Wider target with tight stop |
| `tp_mode` | "vwap" | "atr" | **"atr"** | VWAP mean too close to entry |
| `entry_mode` | "bull_pullback" | "mean_reversion" | **"bull_pullback"** | #1 variable: bull_pullback +118% vs MR (Phase 3) |
| `volume_mult` | 1.5 | 1.2 | **2.0** | Higher volume = better quality (Phase 6) |
| `rsi_max` | 45.0 | 50.0 | **50.0** | Unchanged |
| `adx_max` | 25.0 | 25.0 | **30.0** | Phase 5: 30 is optimal |
| `htf_adx_max` | 25.0 | 30.0 | **25.0** | Empirically reverted |
| `use_htf_ema` | True | True | **False** | Phase 7: True blocks ALL trades |
| `use_htf_vwap` | True | False | **True** | Phase 10: recommended in final config |
| `require_reversal_candle` | True | False | **False** | Too restrictive |
| `use_stoch_filter` | N/A | False (new) | **False** | Reduce filter stack |

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

    tf_cfg       = STRATEGY_TIMEFRAME_CONFIG[args.strategy]
    interval     = args.interval or tf_cfg["interval"]
    train_months = tf_cfg["train_months"]
    test_months  = tf_cfg["test_months"]
    bars_py      = INTERVAL_BARS_PER_YEAR[interval]

    symbol = TICKER_MAP.get(args.ticker)

    print(f"[1/5] Loading {args.ticker} {interval} candles…")
    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    if symbol:
        df = get_candles(symbol, interval, start_dt, end_dt)
    else:
        from data_manager import get_candles_yfinance
        df = get_candles_yfinance(args.ticker, args.start, args.end)

    print(f"      {len(df):,} bars loaded  ({df.index[0].date()} → {df.index[-1].date()})")

    df_exit = None
    if args.exit_res != "Off" and symbol:
        print(f"[1b]  Loading {args.exit_res} exit-resolution data…")
        df_exit = get_candles(symbol, args.exit_res, start_dt, end_dt)
        print(f"      {len(df_exit):,} {args.exit_res} bars loaded")

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

    print(f"[3/5] Reading current .env parameters…")
    current_params  = read_env_params(args.strategy)
    baseline_sharpe = _eval_config(df, args.cutoff, args.strategy,
                                   current_params, windows, bars_py)
    print(f"      Current .env OOS Sharpe: {baseline_sharpe:.3f}")

    print(f"[4/5] Running parameter sweep (Butterworth cutoff + strategy params)…")
    best_params, best_cutoff, best_sharpe, trial_log = run_parameter_sweep(
        df, args.cutoff, args.strategy, current_params, windows,
        bars_per_year=bars_py,
    )
    proposal = build_proposal(args.strategy, current_params, best_params)

    print(f"      Best OOS Sharpe: {best_sharpe:.3f}  "
          f"(Δ{best_sharpe - baseline_sharpe:+.3f} vs current .env)")
    if proposal:
        print(f"      {len(proposal)} .env parameter change(s) proposed:")
        for p in proposal:
            print(f"        {p['ENV Variable']}: "
                  f"{p['Current Value']} → {p['Proposed Value']}  ({p['Change']})")
    else:
        print("      No .env parameter improvements found — current values near-optimal.")

    if args.apply and proposal:
        print(f"[5/5] Writing {len(proposal)} change(s) to .env…")
        updated = apply_to_env(proposal)
        print(f"      Updated: {', '.join(updated)}")
        print("      ⚠️  Restart the trading bot to activate new parameters.")
    else:
        print("[5/5] Dry run — no .env changes written.")

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
python btv2_agent_run.py --strategy "VWAP Scalping" --json-out vwap_results.json

# 5m scalping with 1m exit resolution (v6 default entry_mode=mean_reversion)
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
| `win_rate_pct` | % of trades closed at profit | > 45%; target 55–70% for VWAP at SD≥3.0 |
| `profit_factor` | Gross wins ÷ gross losses | > 1.3; target 1.4–1.8 for VWAP |
| `n_trades` | Total closed trades in period | ≥ 30 daily / ≥ 50 intraday for validation; ≥ 10 for `.env` updates only |
| `avg_win_pct` | Average winning trade size | Should be ≥ 2× avg_loss_pct |
| `avg_loss_pct` | Average losing trade size | Negative; ATR stop controls this |

> **Sharpe scaling note (v3):** Sharpe is now scaled by `√bars_per_year` instead of `√252`. A Sharpe of 1.5 on 1h data is directly comparable to a Sharpe of 1.5 on daily data. The old `√252` convention was wrong for 24/7 crypto and undervalued intraday strategies.

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

⚠️  VWAP NOTE (v6): The Butterworth cutoff no longer affects VWAP band computation.
It only affects EMA/RSI smoothing. Sweeping it for VWAP Scalping now primarily tunes
the trend filter, NOT the VWAP entry threshold.

If |cutoff_delta| > 0.02:
    The filter tuning is materially affecting EMA/RSI signals.
    Update the slider in the app sidebar to best_cutoff and re-run analysis.
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

---

## Automation Loop Pattern (for a CLI coding agent)

### v5 Loop — Overnight Autonomous (optimizer_agent.py)

```
┌──────────────────────────────────────────────────────┐
│  Edit optimize.md                                    │
│  (set Strategy, Trials, Period, Strict mode)         │
└──────────────────────────┬───────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────┐
│  python BTV2/optimizer_agent.py --from-program       │
│                                                      │
│  Optuna TPE loop (runs overnight):                   │
│    propose params → walk-forward → evaluate          │
│    → log to JSONL + RAG → update .env if better      │
│    → repeat N trials                                 │
└──────────────────────────┬───────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────┐
│  Morning review                                      │
│  mcp__rag__rag_search("vwap scalping best params")   │
│  → see best trial + accepted/rejected decision       │
│  Update optimize.md → next strategy in rotation     │
└──────────────────────────────────────────────────────┘
```

### v3 Loop — Quick Daily Check (agent.py / btv2_agent_run.py)

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
│  if sharpe_delta > 0.10 and n_trades >= 30:      │
│      apply = True   (≥30 daily / ≥50 intraday)  │
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

## Trade count floors — per-strategy minimum for statistical confidence:
# daily strategies (Mean Reversion, MA Crossover): n_trades >= 30
# intraday strategies (Momentum 15m, VWAP 5m):    n_trades >= 50
# See "Multi-Strategy & Regime Testing" section for per-strategy floors
MIN_TRADES_DAILY    = 30
MIN_TRADES_INTRADAY = 50
# For .env updates (not strategy validation), the floor is lower:
min_trades_for_apply = MIN_TRADES_DAILY if tf_cfg["interval"] == "1d" else MIN_TRADES_INTRADAY

if sharpe_delta > 0.10 and n_trades >= min_trades_for_apply:
    # Meaningful improvement with statistical confidence → apply
    import subprocess
    subprocess.run(["python", "btv2_agent_run.py",
                    "--strategy", strategy, "--apply"])

elif abs(cutoff_delta) > 0.02:
    print(f"Update Butterworth slider to {result['best_cutoff']:.3f}")

elif oos_sharpe < 0.30 and n_trades >= 10:
    # Strategy is underperforming → flag for logic review
    # (use 10 as floor here — a low-trade strategy with negative Sharpe still needs review)

elif n_trades < 10:
    # Too few trades to evaluate → widen entry conditions:
    # VWAP: lower sd_threshold toward 2.0, set use_htf_vwap=False,
    #       set use_stoch_filter=False to reduce filter stack
    # Note: < 10 trades means the filter stack is too tight — loosen before optimizing
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

---

## Exit Resolution: How It Works

The `df_exit` parameter on all 6 strategy functions enables sub-bar SL/TP detection.

**Problem it solves:** On a 5m bar with both a `low` that breaches SL and a `high` that hits TP, the coarse bar can't tell you which happened first — so SL is assumed (conservative). On the actual 1m sub-bars within that 5m candle, you can see the real sequence.

```
Native bar (5m):     Low = 41,200  High = 43,800   Entry = 42,000
                     SL  = 41,500  TP   = 43,500

Without df_exit:   Low (41,200) <= SL (41,500) → SL hit, trade is a loss

With df_exit (1m):  1m bars 00:00–00:05:
   00:01  H=42,100  L=41,900  — no hit
   00:02  H=43,600  L=42,800  — TP (43,500) hit FIRST → trade is a winner!
   00:03  H=42,400  L=41,100  — would have been SL, but we already closed
```

**When to use it:**
- `"Off"` — default, uses native bar H/L (fast, conservative, existing behaviour)
- `"5m"` — recommended for Momentum Scalping on 15m bars
- `"1m"` — recommended for VWAP Scalping on 5m bars (~60 MB extra Parquet per 7yr BTC)

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

Pre-computes 14 market-structure price columns aligned to `df.index` with **zero lookahead**:

| Column | Description |
|--------|-------------|
| `session_vwap` | Daily-reset VWAP (developing, current session) |
| `session_vwap_std` | VWAP σ — NOT a price level, used only for band width |
| `pdh` / `pdl` | Prior-day high / low |
| `prev_day_poc` / `prev_day_vah` / `prev_day_val` | Prior day's volume profile |
| `pwh` / `pwl` | Prior-week high / low |
| `prev_week_poc` / `prev_week_vah` / `prev_week_val` | Prior week's volume profile |
| `asian_high` / `asian_low` | Today's 00:00–07:59 UTC accumulation range |

```python
from strategies import compute_reference_levels

levels = compute_reference_levels(df)   # ~78ms on full 5m BTC history
equity, trades = run_vwap_scalping(df, cutoff=0.10, levels=levels, tp_mode="nearest", **params)
```

**`tp_mode` options (VWAP Scalping):**

| Mode | Long TP | Short TP | v6 Notes |
|------|---------|----------|----------|
| `"atr"` | entry + atr_target×ATR | entry − atr_target×ATR | **v6 default** — trailing stop is primary |
| `"vwap"` | Session VWAP | Session VWAP | Was v5 default; too close for 3σ entries |
| `"pdh"` | Prior-day high | Prior-day low | |
| `"nearest"` | Closest reference level above entry | Closest reference level below entry | Requires `levels=` |

**UTM (Unified Trade Model) parameters — VWAP Scalping:**

| Parameter | v6 Default | Description |
|-----------|---------|-------------|
| `use_anchored_vwap` | `True` | Daily-reset session VWAP instead of rolling 20-bar window |
| `use_session_filter` | `True` | Only trade London + NY sessions (08:00–22:00 UTC) |
| `require_reversal_candle` | `False` | SFP: wick past band + close back — **disabled in v6** |
| `require_mss` | `False` | UTM Step 3: wait for BOS above SFP candle high before entry |
| `mss_timeout_bars` | `6` | Cancel pending SFP if BOS not confirmed within N bars |

---

### Phase 2 — Signal Quality Filters

Three optional pre-entry filters.  All default to `False`.

#### Fair Value Gaps (`use_fvg_filter=True`)
```python
from strategies import compute_fair_value_gaps

fvg = compute_fair_value_gaps(df, min_gap_pct=0.1)
equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_fvg_filter=True, fvg_data=fvg)
```

#### Order Blocks (`use_ob_filter=True`)
```python
from strategies import compute_order_blocks

ob = compute_order_blocks(df, atr_mult=1.5)
equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_ob_filter=True, ob_data=ob)
```

#### Equal Highs/Lows (`use_eqhl_filter=True`)
```python
from strategies import compute_equal_highs_lows

eqhl = compute_equal_highs_lows(df, tolerance_pct=0.05, lookback=20)
equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_eqhl_filter=True, eqhl_data=eqhl)
```

---

### Phase 3 — Order Flow & Entry Precision

#### CVD (Cumulative Volume Delta) Confirmation
```python
equity, trades = run_vwap_scalping(df, cutoff, **params,
    use_cvd_filter=True, cvd_window=20)
# LONG entries blocked if CVD slope is negative (selling pressure not exhausted)
```

#### OTE (Optimal Trade Entry) Zone
```python
equity, trades = run_vwap_scalping(df, cutoff, **params,
    require_mss=True, use_ote=True)
# Fib 0.618–0.786 of SFP dealing range — tighter entries, fewer trades
```

---

### New parameters for other strategies (v4)

| Strategy | Parameter | Default | Effect |
|----------|-----------|---------|--------|
| Mean Reversion | `use_sfp_entry` | `False` | Require SFP wick on daily bar before RSI+BB entry |
| Mean Reversion | `use_poc_tp` | `False` | Use prev_day_poc as TP instead of fixed ATR target |
| Grid Trading | `use_poc_center` | `False` | Center grid around prev_day_poc instead of current price |
| Grid Trading | `use_va_bounds` | `False` | Set grid outer bounds to prev_day_vah / prev_day_val |
| MA Crossover | `use_va_chop_filter` | `False` | Block entries when price is inside prior-day Value Area |
| Momentum Scalping | `use_cvd_confirm` | `False` | CVD slope must agree with entry direction |
| Liquidation Capture | `use_nearest_tp` | `False` | Use _nearest_tp to target the closest reference level |

All require `levels=compute_reference_levels(df)` to be passed when active.

---

### Recommended Phase 1–3 test sequence (v6 baseline)

```python
# Step 1: v8 Baseline (bull_pullback, SD=2.0, use_htf_ema=False, volume_mult=2.0)
from strategies import VS_DEFAULTS   # v8 empirical defaults — not v6
equity, trades = run_vwap_scalping(df, 0.10, **VS_DEFAULTS)

# Step 2: Add reference levels + nearest TP
levels = compute_reference_levels(df)
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "levels": levels, "tp_mode": "nearest"})

# Step 3: Add stochastic filter (now optional toggle)
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "use_stoch_filter": True, "stoch_oversold": 45, "stoch_overbought": 55})

# Step 4: Re-enable 15m VWAP alignment
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "use_htf_vwap": True})

# Step 5: Full UTM chain (strictest — highest quality, fewest trades)
equity, trades = run_vwap_scalping(df, 0.10, **{**VS_DEFAULTS,
    "require_reversal_candle": True, "require_mss": True,
    "use_stoch_filter": True, "use_htf_vwap": True})
```

Keep a feature if it improves OOS Sharpe by >0.1 and maintains ≥15 trades/year (daily) or ≥40 trades/year (intraday). Fewer trades is acceptable if Monte Carlo verdict remains ROBUST.

---

## Validation Layers (v7)

Three stacked validation functions added to `strategies.py` to answer the key question — *"is this edge real?"* — before committing parameters to `.env` or going live.  All are pure Python functions that operate on trade lists and DataFrames — no extra data needed beyond what the backtest already produces.

```
Walk-forward OOS  ─► trade list
                         │
                         ├─► monte_carlo_validate()   → ROBUST / MARGINAL / FRAGILE
                         │     "Is the edge real or lucky sequencing?"
                         │
                         └─► compute_robustness_score() → score 0–1 + fragile param list
                               "Will the params survive real markets?"

df (OHLCV)  ────────────────► fit_gmm_regime() + predict_gmm_regime()
                               "What regime is the market in?"
```

---

### `monte_carlo_validate(trades, n_sims=1000) → dict`

Shuffles the trade list N times to test whether the strategy's edge survives path-ordering uncertainty.  If profits vanish when trades arrive in a different order, the win was sequential luck, not structural edge.

```python
from strategies import monte_carlo_validate

# trades = list returned by any run_* function
mc = monte_carlo_validate(trades, n_sims=1_000, seed=42)
# {
#   "n_trades"         : 12,
#   "n_sims"           : 1000,
#   "prob_of_loss_pct" : 3.2,      # % of sims ending below starting equity
#   "p5_return_pct"    : 2.1,      # 5th-percentile final return
#   "p50_return_pct"   : 18.4,     # median final return
#   "p95_return_pct"   : 51.7,     # 95th-percentile final return
#   "worst5_maxdd_pct" : -14.2,    # avg max-drawdown of worst-5% sims (negative)
#   "median_maxdd_pct" : -8.1,
#   "spread_ratio"     : 24.6,     # p95/|p5| — > 4 = wide outcome spread
#   "overfitting_flag" : False,    # True when p5 < -5% AND spread_ratio > 4
#   "verdict"          : "ROBUST", # ROBUST / MARGINAL / FRAGILE
# }
```

**Verdict rules:**

| Verdict | Condition |
|---------|-----------|
| `ROBUST` | `prob_of_loss < 10%` AND `p5 > 0%` |
| `MARGINAL` | `prob_of_loss < 25%` AND no overfitting flag |
| `FRAGILE` | Otherwise |

**Interpretation:**
- `prob_of_loss_pct < 10%` — strong signal: the strategy is net positive in 90%+ of orderings
- `p5_return_pct > 0%` — even the unlucky 5th percentile ordering is profitable
- `worst5_maxdd_pct` — tail-risk check: worst-case drawdown under adversarial ordering
- `spread_ratio > 4` with negative `p5` — wide outcome range; edge is fragile to sequencing

---

### `compute_robustness_score(df_train, cutoff, strategy_name, best_params, perturbation=0.15) → dict`

Perturbs each numeric parameter ±15% individually on the training window.  Flags parameters where a small change causes Sharpe to drop > 0.30 — the classic overfitting signature.

```python
from strategies import compute_robustness_score, INTERVAL_BARS_PER_YEAR

rob = compute_robustness_score(
    df_train      = df_train,
    cutoff        = 0.10,
    strategy_name = "VWAP Scalping",
    best_params   = best_params,       # from optimize_strategy()
    perturbation  = 0.15,              # ±15%
    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"],
)
# {
#   "score"         : 0.82,       # 0–1 (1 = rock-solid, 0 = fragile)
#   "base_sharpe"   : 1.34,
#   "param_results" : {
#     "sd_threshold": {
#       "base"  : 3.0,
#       "minus" : {"value": 2.55, "sharpe": 1.19, "delta": -0.15},
#       "plus"  : {"value": 3.45, "sharpe": 1.28, "delta": -0.06},
#       "stable": True,
#     },
#     "atr_stop": { ... },
#     ...
#   },
#   "fragile_params": ["trailing_atr"],   # params where |worst delta| > 0.30
#   "verdict"       : "MARGINAL",         # ROBUST / MARGINAL / FRAGILE
# }
```

**Verdict rules:**

| Verdict | Condition |
|---------|-----------|
| `ROBUST` | `score ≥ 0.80` AND zero fragile params |
| `MARGINAL` | `score ≥ 0.50` AND ≤ ⌊N/3⌋ fragile params |
| `FRAGILE` | Otherwise |

**What to do with fragile params:**
- Remove them from the grid (fix at VS_DEFAULTS value) and re-run
- Widen the training window (more data = more stable param estimates)
- If the param is always fragile: it's probably noise — remove from model

---

### GMM Regime Detection

Fits a Gaussian Mixture Model on rolling `[vol, mom, vol_ratio, volume_z]` features to learn 4 data-driven market states.  More expressive than ADX-based thresholds — distinguishes quiet trending from volatile trending, and calm ranging from compressed pre-breakout.

**Requires:** `pip install scikit-learn>=1.3.0` (already in `requirements.txt`).

```python
from strategies import (
    fit_gmm_regime, predict_gmm_regime,
    gmm_regime_summary, compute_gmm_features,
    _SKLEARN_AVAILABLE,
)

# Step 1 — Fit (train-window data)
gmm_model, label_map, scaler = fit_gmm_regime(
    df,
    n_regimes       = 4,    # learns: calm / trending / volatile / crash
    lookback        = 60,   # rolling feature window in bars
    stability_window= 5,    # mode-filter: prevents rapid regime flipping
    random_state    = 42,
)
# label_map: {component_idx: "calm" | "trending" | "volatile" | "crash"}

# Step 2 — Predict (any window, same interval)
regime_df = predict_gmm_regime(df, gmm_model, label_map, scaler,
                                lookback=60, stability_window=5)
# regime_df columns:
#   regime_raw  — raw per-bar GMM label
#   regime      — stability-filtered label (str)
#   confidence  — posterior probability of predicted regime (0–1)
#   is_calm / is_trending / is_volatile / is_crash  — bool shortcuts

# Step 3 — Summarise
summary = gmm_regime_summary(regime_df)
# {
#   "regime_counts"     : {"calm": 412, "trending": 318, ...},
#   "regime_pct"        : {"calm": 30.3, "trending": 23.4, ...},
#   "dominant_regime"   : "calm",
#   "avg_confidence"    : 0.83,
#   "current_regime"    : "trending",
#   "current_confidence": 0.91,
# }
```

**Regime labelling heuristic (4 components):**

| Regime | Volatility | Momentum | Typical market condition |
|--------|-----------|----------|--------------------------|
| `calm` | Low | Low | Sideways consolidation, tight range |
| `trending` | Low | High | Clean directional move, low noise |
| `volatile` | High | Mixed | Choppy swings, wide spreads |
| `crash` | Highest | Strong negative | Capitulation, liquidation cascades |

**Using GMM as a strategy filter (example):**
```python
# Only take mean_reversion trades in calm or volatile regimes
# Avoid trending (use bull/bear_pullback instead) and crash

regime_today = summary["current_regime"]
if regime_today in ("calm", "volatile"):
    equity, trades = run_vwap_scalping(df, cutoff, entry_mode="mean_reversion", **params)
elif regime_today == "trending":
    equity, trades = run_vwap_scalping(df, cutoff, entry_mode="bull_pullback", **params)
# else: regime == "crash" → flat, no trades
```

> **Note:** GMM is disabled if scikit-learn is not installed — all other functions work normally.
> Check `from strategies import _SKLEARN_AVAILABLE` to verify before calling GMM functions.

---

### `btv2_validate.py` — One-Shot Validation Runner

Combines all 4 layers (walk-forward OOS + Monte Carlo + Robustness + GMM) into a single CLI call with a unified PASS / CAUTION / FAIL verdict.

```bash
# Full validation — VWAP Scalping, 2021-2023, bull_pullback mode
python BTV2/btv2_validate.py \
    --strategy "VWAP Scalping" \
    --start 2021-01-01 --end 2023-12-31 \
    --entry-mode bull_pullback \
    --exit-res 1m \
    --json-out report.json

# Quick check — no GMM (faster, no scikit-learn needed)
python BTV2/btv2_validate.py --strategy "Mean Reversion" --no-gmm

# Dynamic mode test
python BTV2/btv2_validate.py --strategy "VWAP Scalping" \
    --start 2021-01-01 --end 2023-12-31 --no-gmm --json-out dynamic_report.json
```

**Output format:**
```
────────────────────────────────────────────────────────────
  VALIDATION VERDICT
────────────────────────────────────────────────────────────
  ✅ PASS
    ✓ OOS: PASS     (Sharpe=1.21, 18 trades, WR=55%)
    ✓ MC: PASS      (P(loss)=2.1%, p5=+3.4%, p50=+22.1%)
    ⚠ ROB: MARGINAL (score=0.71, fragile: trailing_atr)
```

**JSON output keys:**
```python
{
    "oos_metrics"  : {...},          # same as compute_metrics() output
    "monte_carlo"  : {...},          # same as monte_carlo_validate() output
    "robustness"   : {...},          # same as compute_robustness_score() output
    "gmm_summary"  : {...},          # same as gmm_regime_summary() output
    "verdicts"     : ["OOS: PASS", "MC: PASS", "ROB: MARGINAL"],
    "overall"      : "PASS",         # PASS / CAUTION / FAIL
    "fold_metrics" : [{...}, ...],   # per-fold breakdown
}
```

**Overall verdict rules:**

| Verdict | Condition |
|---------|-----------|
| `PASS` | 0 FAILs AND ≤ 1 CAUTIONs |
| `CAUTION` | 0 FAILs but ≥ 2 CAUTIONs |
| `FAIL` | Any layer returns FAIL |

---

## v8 Validation Findings

_Updated 2026-04-25 · Agent: Claude_

### VWAP Scalping v6 — Full Walk-Forward Validation Result: FAIL

Three validation runs were performed against local BTC-USDC 5m data (trading_bot_v2/backtesting/data/BTC-USDC_5m_all.csv):

| Test | Period | Bars | OOS Sharpe | Return | Trades | WR | MC Verdict |
|------|--------|------|-----------|--------|--------|-----|-----------|
| Old VS_GRID (HTF on, MR only) | 2021–2023 | 314,860 | -3.84 | -16.5% | 64 | 27% | FRAGILE |
| New VS_GRID (HTF off, BP in grid) | 2022 full | 104,833 | -4.32 | -5.2% | 18 | 22% | FRAGILE |
| Old VS_GRID (HTF on, MR only) | 2022 H1 | 51,841 | -4.06 | -0.7% | 3 | 0% | FRAGILE |

**All three layers FAIL across all periods:** OOS negative Sharpe, 100% Monte Carlo probability of loss, Robustness Score 0.0.

**Consequence:** Live bot `trading_bot_v2/strategies/vwap_scalping.py` was **NOT updated** — the stated gate "update only after validation passes" was not cleared.

### Empirical Findings from 350+ Tests (VWAP_Complete_Results_Summary.md)

These findings drove the v8 defaults change:

| Rank | Variable | Finding | v8 Action |
|------|---------|---------|-----------|
| #1 | `entry_mode` | `bull_pullback` is +118% vs `mean_reversion` | Default changed to `bull_pullback` |
| #2 | `volume_mult` | 2.0 improves trade quality over 1.2 | Default raised to 2.0 |
| #3 | `sd_threshold` | SD=2.0 is the sweet spot (first profitable config) | Default lowered 3.0 → 2.0 |
| #7 | `use_htf_ema` | **True blocks ALL trades** (Phase 7 finding) | Default changed to **False** |

### Strategy Status Summary (post v8 validation)

| Strategy | OOS Edge Confirmed | Live Bot Status | Recommendation |
|----------|-------------------|-----------------|----------------|
| VWAP Scalping | **NO** — all periods FAIL | `vwap_scalping.py` unchanged | Consider `ENABLE_VWAP_SCALPING=false` in .env |
| Mean Reversion | Partial (best fold ~0.78) | In use | Run optimizer Round 1 to update RSI params |
| Momentum Scalping | Not validated | In use | Run optimizer Round 3 |
| MA Crossover | Not validated | In use | Low priority |
| Liquidation Capture | Not validated | In use | Rare signals, validate separately |
| Grid Trading | **NO** — negative 5/7 years | In use | Consider `ENABLE_GRID_TRADING=false` |

### What to Try Next for VWAP Scalping

The strategy has not shown edge on BTC 5m data in any tested configuration (350+ tests, multiple years). Options in order of plausibility:

1. **Different asset**: Test on ETH or SUI — VWAP mean-reversion may work better on lower-liquidity assets with more pronounced VWAP reversion behaviour
2. **Different timeframe**: 15m or 1h VWAP reduces noise significantly (Phase 1 findings show 1h gets 67% WR at 6–15 trades)
3. **Dynamic mode**: `entry_mode="dynamic"` auto-switches between MR/bull/bear based on 1h regime — not yet formally validated through btv2_validate.py
4. **Accept the finding**: VWAP scalping on BTC 5m is a commodity trade with insufficient edge after 0.30% costs

---

## Multi-Strategy & Regime Testing — Correct Workflow

_Added 2026-04-28 · Context: `BTV2/tests/` meta-regime test files reviewed — methodology issues found and documented here._

### ⛔ DO NOT: Write Custom Test Files

**Never** create a new Python file that implements its own EMA, RSI, ATR, or crossover logic to validate a strategy. The existing testing infrastructure in `strategies.py` has been validated over 350+ tests. Custom implementations introduce:

- **Different indicator math** than the live bot uses — EMA from scratch ≠ `STRATEGY_REGISTRY` implementation
- **Daily-bar shortcuts** — testing a 15m strategy on 1d bars gives a completely different (and misleading) signal profile
- **Hardcoded year labels** (lookahead bias) — declaring 2018 "BEARISH" and 2019 "BULLISH" uses future knowledge at test time
- **Statistically meaningless trade counts** — 9 trades over 7 years (1.3/year) cannot confirm or deny an edge

> **The files `BTV2/tests/momentum_scalping_2018_2025.py`, `BTV2/tests/ma_crossover_2018_2025.py`, and `BTV2/tests/mean_reversion_2018_2025.py` are examples of this anti-pattern.** Their results are NOT comparable to live bot performance and should not be used to validate strategy decisions. Do not replicate this pattern.

---

### ✅ DO: Use `btv2_validate.py` Per Strategy Per Year

The correct approach for multi-year regime analysis is to run `btv2_validate.py` once per strategy per year. This uses each strategy's native interval, production signal logic from `strategies.py`, and all 4 validation layers.

```bash
# Run per year — uses strategy's native interval from STRATEGY_TIMEFRAME_CONFIG automatically
for year in 2018 2019 2020 2021 2022 2023 2024; do
    python BTV2/btv2_validate.py \
        --strategy "Momentum Scalping" \
        --start ${year}-01-01 --end $((year+1))-01-01 \
        --no-gmm \
        --json-out BTV2/results/momentum_${year}.json
    echo "--- ${year} done ---"
done

# Then compare the JSON results:
python -c "
import json, glob
for f in sorted(glob.glob('BTV2/results/momentum_*.json')):
    year = f.split('_')[-1].replace('.json','')
    r = json.load(open(f))
    m = r['oos_metrics']
    mc = r['monte_carlo']['verdict']
    print(f'{year}:  Sharpe={m[\"sharpe\"]:+.2f}  Return={m[\"total_return_pct\"]:+.1f}%  Trades={m[\"n_trades\"]}  MC={mc}')
"
```

This runs on **15m bars** for Momentum Scalping (not 1d), uses actual production `run_momentum_scalping()`, and properly applies walk-forward within each year.

---

### Correct Multi-Strategy Yearly Comparison

If you need a single script to compare multiple strategies across multiple years, use `STRATEGY_REGISTRY` and `data_manager.py` — do not re-implement any indicators:

```python
#!/usr/bin/env python3
"""
Multi-strategy yearly regime comparison — CORRECT APPROACH.
Uses STRATEGY_REGISTRY + data_manager.py — no custom EMA/RSI implementations.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    STRATEGY_REGISTRY, STRATEGY_TIMEFRAME_CONFIG, INTERVAL_BARS_PER_YEAR,
    build_windows, compute_metrics, optimize_strategy, stitch_oos_equity,
)
from data_manager import get_candles

STRATEGIES_TO_TEST = ["Momentum Scalping", "MA Crossover", "Mean Reversion"]
YEARS = range(2018, 2025)

results = {}

for strategy_name in STRATEGIES_TO_TEST:
    tf_cfg   = STRATEGY_TIMEFRAME_CONFIG[strategy_name]
    interval = tf_cfg["interval"]   # Momentum=15m, MA Crossover=1d, Mean Reversion=1d
    bars_py  = INTERVAL_BARS_PER_YEAR[interval]
    func, _, defaults = STRATEGY_REGISTRY[strategy_name]
    results[strategy_name] = {}

    for year in YEARS:
        start = datetime(year,   1, 1, tzinfo=timezone.utc)
        end   = datetime(year+1, 1, 1, tzinfo=timezone.utc)

        df = get_candles("BTCUSDT", interval, start, end)
        if len(df) < 100:
            continue

        # Walk-forward within the year using strategy's own window sizes
        windows = build_windows(
            date(year, 1, 1), date(year+1, 1, 1),
            train_months=tf_cfg["train_months"],
            test_months=tf_cfg["test_months"],
        )

        segments, all_trades = [], []
        for w in windows:
            df_train = df.loc[str(w["train_start"]):str(w["train_end"])]
            df_test  = df.loc[str(w["test_start"]):str(w["test_end"])]
            if len(df_train) < 50 or len(df_test) < 10:
                continue

            # optimize_strategy() grids over VS_GRID (strategies.py), not a custom loop
            best = optimize_strategy(df_train, 0.10, strategy_name)
            eq, trd = func(df_test, 0.10, **best)
            segments.append(eq)
            all_trades.extend(trd)

        if not segments:
            results[strategy_name][year] = {"n_trades": 0, "sharpe": None, "skip": "no_folds"}
            continue

        oos_equity = stitch_oos_equity(segments)
        metrics    = compute_metrics(oos_equity, all_trades, bars_per_year=bars_py)
        results[strategy_name][year] = metrics

        print(f"{strategy_name:25s} {year}: interval={interval}  "
              f"Sharpe={metrics['sharpe']:+.2f}  "
              f"Return={metrics['total_return_pct']:+.1f}%  "
              f"Trades={metrics['n_trades']}")

Path("BTV2/results/multi_strategy_yearly.json").write_text(
    json.dumps(results, indent=2, default=str)
)
print("\nSaved: BTV2/results/multi_strategy_yearly.json")
```

**What this gets right vs. custom test files:**

| Issue | Custom test files | This approach |
|-------|-------------------|---------------|
| Indicator math | Re-implements EMA/RSI from scratch | Uses production `run_*` functions from `strategies.py` |
| Native timeframe | Daily bars for every strategy | 15m for Momentum, 1d for MA Crossover — correct per `STRATEGY_TIMEFRAME_CONFIG` |
| Walk-forward | Full-year in-sample (data leakage) | Proper OOS via `build_windows()` + `optimize_strategy()` |
| Regime labels | Hardcoded year → BEARISH/BULLISH | No labels needed — OOS result speaks for itself |
| Sharpe scaling | Fixed `√252` or wrong basis | `INTERVAL_BARS_PER_YEAR[interval]` — correct 24/7 crypto scaling |
| Trade count | 9 trades over 7 years (Momentum) | Typically 30–200+ trades at native interval |

---

### Statistical Significance Floors

Before reporting a strategy as "validated" or "profitable", closed trade counts must reach these minimums:

| Strategy | Interval | Min Annual Trades | Min Total (multi-year) | Note |
|----------|----------|-------------------|------------------------|------|
| Mean Reversion | 1d | 15 | **30** | Slow strategy — few signals/year expected |
| MA Crossover | 1d | 8 | **30** | Few crossovers/year; 30+ needed for MC validity |
| Momentum Scalping | 15m | 30 | **60** | Intraday — needs more to separate noise from edge |
| VWAP Scalping | 5m | 40 | **80** | Very low count = filter stack too tight, not validation |
| Grid Trading | 4h | 20 | **40** | Grid fills accumulate fast when deployed |
| Liquidation Capture | 1d | 5/year | **15** | Rare event — use Monte Carlo verdict, not walk-forward Sharpe |

**The `n_trades >= 10` rule in the automation loop is a floor for `.env` updates, not for strategy validation.** A 7-year "validation" claiming Momentum Scalping is the "best strategy" based on 9 total trades is statistically meaningless regardless of win rate or return.

---

### Regime Detection: Data-Driven, Not Hardcoded Labels

**Do NOT use hardcoded year classifications.** This is a form of lookahead bias — the labels use knowledge that wasn't available at the start of each year.

```python
# ❌ WRONG — lookahead bias
YEAR_CLASSIFICATIONS = {
    2018: "BEARISH", 2022: "BEARISH",   # you only know these were bearish in hindsight
    2019: "BULLISH", 2020: "BULLISH",   # a 2018 bot didn't know 2019 would be bullish
}
```

The correct approach uses the GMM regime detector already in `strategies.py` — it's trained only on data before the test window, so there's no lookahead:

```python
from strategies import fit_gmm_regime, predict_gmm_regime, gmm_regime_summary

# --- No hardcoded labels needed ---

# Train GMM on the training window only (no lookahead)
gmm_model, label_map, scaler = fit_gmm_regime(
    df_train,
    n_regimes       = 4,
    lookback        = 60,
    stability_window= 5,
    random_state    = 42,
)

# Predict the test window's regime from the fitted model
regime_df = predict_gmm_regime(df_test, gmm_model, label_map, scaler,
                                lookback=60, stability_window=5)
summary = gmm_regime_summary(regime_df)

current_regime = summary["dominant_regime"]   # "calm" / "trending" / "volatile" / "crash"
print(f"Test window regime: {current_regime}  (confidence={summary['avg_confidence']:.2f})")
```

For rough annual regime labels (if you genuinely need year-level classification), use annual return of the asset computed from the data itself — not hardcoded strings. But prefer the GMM path: it's the system already built.

---

### ⚠️ Known Lookahead Bias: `regime_strategy_settings.py`

`BTV2/regime_strategy_settings.py` contains:

```python
bearish_years = {2018, 2022, 2026}   # ← 2026 hardcoded as BEARISH
```

This means any code using this file will classify **all of 2026 as a bear market** regardless of actual conditions. The live bot using this file will short-only for the entire year.

**Treat this file as a temporary scaffold, not production logic.** Do not reference it in new tests or validation scripts. Use GMM regime detection or ADX-based per-window regime detection instead.

---

### Grid Trading: Logic Test ≠ P&L Validation

`BTV2/tests/grid_regime_transition.py` tests the `simulate_partial_exit()` function — it verifies that short positions are closed in an uptrend and long positions are closed in a downtrend. This is a **logic unit test**.

- A PASS on this test means: "the code closes the right positions when a regime transition is detected."
- A PASS on this test does **NOT** mean: "grid trading is profitable."

To validate grid trading P&L, run:
```bash
python BTV2/btv2_validate.py --strategy "Grid Trading" \
    --start 2022-01-01 --end 2024-01-01 \
    --json-out BTV2/results/grid_validation.json
```

The research plan in this guide already recommends disabling Grid Trading (`ENABLE_GRID_TRADING=false`) based on -5.91% returns and negative OOS Sharpe in 5 of 7 years.

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

### 2. VWAP must use RAW close (v6 rule)
```python
# CORRECT — raw close for volume-weighted price
vwap, vs = compute_vwap_anchored(df["High"], df["Low"], df["Close"], df["Volume"])

# WRONG — Butterworth compresses deviation, making SD thresholds unreachable
fc = apply_butterworth(df["Close"], cutoff)
vwap, vs = compute_vwap_anchored(df["High"], df["Low"], fc, df["Volume"])  # ← DO NOT
```

### 3. Cost deducted at every entry AND exit
```python
COST_PER_SIDE = 0.0015   # 0.10% fee + 0.05% slippage

# Entry
curr_equity *= (1.0 - COST_PER_SIDE)

# Exit
curr_equity *= (1.0 - COST_PER_SIDE)
```
Total round-trip cost: **0.30%**. Realistic for major exchanges.

### 4. SL checked before TP on the same bar (coarse mode)
```python
if side == "long":
    if low <= sl_price:    hit = True   # SL first (conservative)
    elif high >= tp_price: hit = True   # TP only if SL not triggered
```
Sub-bar mode (`df_exit`) eliminates this ambiguity.

### 5. Sharpe scaling must match candle interval
```python
# CORRECT — 24/7 crypto, 5m bars
sharpe = bar_rets.mean() / bar_rets.std() * math.sqrt(105120)

# Other intervals:
#   15m → math.sqrt(35040), 1h → math.sqrt(8760)
#   4h  → math.sqrt(2190),  1d → math.sqrt(365)
```

### 6. Never apply proposals without OOS confidence
Only apply agent proposals when:
- `n_trades >= 30` for daily strategies (Mean Reversion, MA Crossover) — statistical floor
- `n_trades >= 50` for intraday strategies (Momentum 15m, VWAP 5m) — noise floor is higher at LTF
- `sharpe_delta > 0.10` (meaningful improvement over baseline)
- `oos_sharpe > 0.0` (strategy is net positive after costs)

> ⚠️ The older `n_trades >= 10` rule was insufficient for daily strategies. A 7-year test with 9 total trades (1.3/year) cannot confirm edge regardless of win rate.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'strategies'`**
```bash
cd Bot3/BTV2
python your_script.py   # must run from BTV2/ directory
```

**`G:\Candle Data\ does not exist`**
`data_manager.py` auto-creates the directory on first call to `get_candles()`. Ensure `G:` drive is mounted before running.

**VWAP Scalping returns 0 trades (v6)**

With `sd_threshold=3.0` + `require_reversal_candle=False` + `use_htf_vwap=False`, you should see trades on real BTC data. If you get 0:
1. Verify data has genuine VWAP deviations: `df.describe()` — check High−Low range
2. Confirm `use_session_filter=True` isn't filtering everything if data is UTC-offset
3. Try `sd_threshold=2.5` temporarily to confirm the pipeline works
4. Check `use_stoch_filter=False` (default) — stochastic was filtering all trades in v5

**VWAP Scalping still losing money after v6 fix**

Run the optimizer first — the structural fixes make profitable configs _findable_, but the exact parameters still need tuning on your data:
```bash
python BTV2/optimizer_agent.py --strategy "VWAP Scalping" --n-trials 80 --dry-run
```
Key levers if optimizer doesn't find profit after 80 trials:
- Raise `sd_threshold` range cap toward 5.0
- Try `entry_mode="cross"` (VWAP cross mode — different market structure)
- Reduce date range to 2022–2024 (post-FTX BTC only — cleaner ranging regime)
- Enable `use_htf_vwap=True` as a fixed constraint and re-run

**VWAP Scalping win rate stuck at 33–41% (pre-v6 behavior)**

This indicated entries firing during impulse legs on smoothed VWAP bands. The v6 fix resolves the root cause (Butterworth distortion). If you see this post-v6, the `sd_threshold` may be too low or `use_htf_ema` is leaking trades:
1. Confirm `use_htf_ema=False` in VS_DEFAULTS — True blocks ALL longs in downtrends (Phase 7)
2. Confirm `entry_mode="bull_pullback"` (not mean_reversion) — bull_pullback is #1 by variable importance
3. Confirm VS_DEFAULTS `sd_threshold` is 2.0 (not 3.0 from a stale v6 cache)
4. Delete `__pycache__/` in `BTV2/` and re-import strategies
5. Run `python -c "from BTV2.strategies import VS_DEFAULTS; print(VS_DEFAULTS)"` — verify all v8 values

**`Liquidation Capture` returns 0 trades**
Expected on daily BTC bars — fires ~0–5 times per year. Use a shorter range around known crash events (Mar 2020, May 2021, Nov 2022) to see it activate.

**`require_mss=True` gives 0 trades**
MSS requires a BOS candle within `mss_timeout_bars` bars after the SFP. Defaults: `mss_timeout_bars=6` (6 × 5m = 30 minutes). Verify SFP signals are firing by temporarily setting `require_mss=False`.

**Phase 2 filters drastically reduce trade count**
These filters are intentionally strict. Expected trade reduction: 30–70% per filter. Enable one at a time. If trade count drops below 5/year, the filter is too restrictive.

**Sharpe looks unexpectedly low/high after changing interval**
Verify you're passing the correct `bars_per_year` to `compute_metrics`. Common mistakes:
- Using default `365` for VWAP Scalping on 5m data (should be `105120`)
- Using `8760` (1h convention) for VWAP Scalping after the LTF migration

---

## Research Plan & Optimization Roadmap (v8)

_Updated 2026-04-25 · Agent: Claude_

### Key Findings from Backtesting Audit

| Strategy | OOS Edge Confirmed | Live .env Status | Action |
|----------|-------------------|------------------|--------|
| Mean Reversion | Partial (best fold ~0.78) | rsi_overbought=70 ← should be 75 | **Optimize — Round 1** |
| VWAP Scalping | **FAIL** — all periods negative (v8 validation) | vwap_scalping.py unchanged | Consider disabling; try ETH/SUI or 15m/1h |
| Momentum Scalping | Consistently negative | EMA 9/21 — BTV2 best found 20/50 | **Disable or redesign — Round 3** |
| MA Crossover | Sporadic (depends on regime) | fast=20/slow=50 — aligned | **Low priority — Round 4** |
| Liquidation Capture | Rare signals, very profitable when hits | No direct env vars yet | **Add env vars — Round 5** |
| Grid Trading | **Negative in 5/7 years (2018–2024)** | ENABLE_GRID_TRADING=true | **⚠️ DISABLE** |

> **Grid Trading recommendation:** Set `ENABLE_GRID_TRADING=false` in `.env`. OOS Sharpe on BTC is -0.19. It only performs in sustained sideways markets (2019/2021 bull plateaus).

> **VWAP Scalping status (v8):** Full walk-forward validation (2021–2023) returned OOS Sharpe -3.84, WR 27%, 100% MC loss probability. Live bot was NOT updated. VWAP scalping on BTC 5m has not demonstrated positive edge in 350+ test combinations. See [v8 Validation Findings](#v8-validation-findings).

### Optimizer Rotation Order

Edit `BTV2/optimize.md` and advance through this sequence.

```
Round 1 — Mean Reversion (daily bars, BTC)  ← START HERE
  → fastest backtest, most historical data
  → key params: rsi_overbought (target 75), bb_proximity, adx_max
  → run once normal, once --strict to see live-bot gap

Round 2 — VWAP Scalping (15m or 1h bars, BTC/ETH, 2021–2025)  ← CHANGED
  → 5m BTC VWAP scalping has FAILED validation — change asset or timeframe
  → Try: --strategy "VWAP Scalping" --symbol ETH-USDC --interval 15m
  → Or: test entry_mode="dynamic" which auto-switches MR/bull/bear by 1h regime
  → success = OOS Sharpe > 0.5 with n_trades >= 20 per year

Round 3 — Momentum Scalping (15m bars, BTC)
  → consistently negative OOS — find out if any config is viable
  → if best Sharpe < 0.3 after 100 trials → recommend disable

Round 4 — MA Crossover (daily, BTC)
  → already aligned between BTV2 and live; quick validation run
  → 30 trials is sufficient

Round 5 — Liquidation Capture (daily, BTC)
  → rare signals (< 10/year) — use Monte Carlo not walk-forward
  → add env var mapping before optimizing
```

### Backtest vs Live Bot Gap (STRICT_VALIDATION calibration)

```bash
# Step 1: Find optimal params (normal mode)
python BTV2/optimizer_agent.py -s "VWAP Scalping" -n 80 --dry-run

# Step 2: Run same strategy in strict mode
python BTV2/optimizer_agent.py -s "VWAP Scalping" -n 80 --strict --dry-run

# Expected: 20–40% fewer signals in strict mode
# Calibration target: strict-mode OOS Sharpe ≥ 70% of normal-mode
```

### .env Parameter Discrepancies (as of 2026-04-12)

| Parameter | BTV2 Best Found | Live .env | Priority |
|-----------|-----------------|-----------|----------|
| `MEAN_REVERSION_RSI_OVERBOUGHT` | 75.0 | 70.0 | High — update after optimizer confirms |
| `VWAP_SD_ENTRY_THRESHOLD` | TBD (v6 optimizer needed) | 3.0 | High — run optimizer Round 2 |
| `VWAP_ATR_STOP_MULTIPLIER` | TBD | 7.0 (live) vs 1.0 (BTV2 v6) | **Critical mismatch — live uses 7.0** |
| `VWAP_TRAILING_ATR` | TBD | Not in .env yet | Add after optimizer finds best value |
| `MOMENTUM_EMA_FAST/SLOW` | 20/50 | 9/21 | Medium — after Round 3 sweep |
| `GRID_ADX_THRESHOLD` | 15–17 | 20.0 | Moot — recommend disabling grid |

> ⚠️ **ATR Stop mismatch:** Live bot has `VWAP_ATR_STOP_MULTIPLIER=7.0` (very wide). BTV2 v6 default is 1.0. This divergence means live bot rarely stops out but also rarely hits TP. Confirm live bot intent before syncing this parameter.

### Quality Gates (accept/reject thresholds)

| Metric | Accept | Reject |
|--------|--------|--------|
| OOS Sharpe improvement | Δ ≥ 0.05 | Δ < 0.05 |
| Minimum OOS trades | ≥ 30 (daily) / ≥ 50 (intraday) | < 30 (daily) / < 50 (intraday) |
| Max drawdown | ≥ −30% | < −30% |
| Consistency | ≥ 50% windows Sharpe > 0 | < 50% |
| Strict-mode Sharpe ratio | ≥ 70% of normal-mode | < 70% |

When all gates pass: accept and apply to `.env`.
When any gate fails: reject, log reason, try wider search space or different strategy.
