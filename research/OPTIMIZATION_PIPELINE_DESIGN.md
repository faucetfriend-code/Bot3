# Bot3 Optimization Pipeline Design
**Date:** 2026-04-11 | **Agent:** glitch

---

## 1. Gap Analysis: BTV2 Backtesting vs Live Bot

### What BTV2 Does Well
- Walk-forward analysis with train/test splits per strategy timeframe
- Exhaustive parameter grids already defined (`MR_GRID`, `VWAP_GRID`, etc.)
- Realistic cost model (fee + slippage per side)
- Multi-year data covering multiple market regimes (2018–2025)
- Results stored in JSON/CSV with iteration tracking (grid_btc_iter*.json)

### Critical Gaps (BTV2 vs Live Bot)

| Aspect | BTV2 | Live Bot | Impact |
|--------|------|----------|--------|
| Signal validation | No 8-flag system | Requires ALL 8 flags True | BTV2 dramatically over-generates signals |
| Regime detection | Simplified ADX in regime_detector.py | Full 4h ADX with 5 regimes + overlays | Different strategy activation |
| Timeframes | Daily OHLCV for most strategies | 1m/5m/15m/4h multi-timeframe | Mean reversion uses daily in BTV2, live uses multi-TF |
| Cost model | Fixed 0.15% per side | Separate maker/taker + hourly funding | Funding cost underestimated in BTV2 |
| Kelly sizing | Not modeled | Activates at 50+ trades | Position sizes differ |
| Parameter source | Hardcoded in test files | .env (some strategies don't read it — audit finding) | Configs can drift |

### Known Parameter Mismatches (Live .env vs BTV2 Optimal)

| Strategy | Parameter | BTV2 Best Found | Live .env | Status |
|----------|-----------|-----------------|-----------|--------|
| Mean Reversion | rsi_overbought | 75.0 | 70.0 | ⚠️ Diverged |
| Mean Reversion | rsi_oversold | 25–30 | 30.0 | ✅ Close |
| Momentum | EMA fast/slow | 20/50 | 9/21 | ⚠️ Different logic |
| Grid Trading | adx_threshold | 15–17 | 20.0 | ⚠️ Diverged |
| Grid Trading | spacing_mult | 0.30 | 0.4 (ATR) | ⚠️ Different scale |
| MA Crossover | fast/slow | 20/50 | 20/50 | ✅ Aligned |

### Most Concerning Finding
Grid Trading is **consistently negative** out-of-sample (2018–2024 yearly results show
net loss in 5 of 7 years). The OOS Sharpe from iter4 is -0.19. This strategy should be
disabled or require much tighter regime conditions.

---

## 2. Optimization Pipeline Design

### Core Philosophy (Adapted from autoresearch)
Karpathy's autoresearch pattern: **fixed budget per experiment → single metric → agent
modifies one file → keeps/discards → repeat overnight.**

For trading, the translation is:
- **Fixed budget** = one walk-forward backtest run (usually <60s)
- **Single metric** = OOS Sharpe ratio (or Profit Factor if trade count is low)
- **One file** = `.env` parameter values (or BTV2 parameter dicts)
- **Keep/discard** = update .env if OOS Sharpe improves by ≥ 0.05
- **Repeat** = Optuna TPE sampler or coordinate descent, stored in RAG

### Architecture

```
optimize.md              ← Human instructions (what to optimize, constraints)
    │
    ▼
optimizer_agent.py       ← Main loop
    │
    ├── read_params()           # Load current params from .env
    ├── propose_params()        # Optuna TPE or coordinate descent
    ├── run_backtest()          # Call BTV2 walk-forward for that strategy
    ├── evaluate()              # OOS Sharpe, trade count filter (min 20 OOS trades)
    ├── store_result()          # mcp__rag__rag_ingest experiment log
    └── update_if_better()      # Write to .env if improvement threshold met
```

### optimize.md (the "program.md" equivalent)
```markdown
# Optimization Program

## Current Objective
Maximize OOS Sharpe for Mean Reversion on BTC-USDC (2021–2024 walk-forward).

## Constraints
- Minimum 15 OOS trades per window (reject low-sample results)
- Max drawdown ≤ 25%
- RSI oversold must be in [20, 40]
- RSI overbought must be in [60, 80]
- BB proximity in [0.05, 0.20]
- ATR stop multiplier in [2.0, 4.0]

## Metric
Primary: Mean OOS Sharpe across all walk-forward windows
Secondary: Consistency (% of windows with Sharpe > 0)

## Budget
Run until 100 trials completed or 90 minutes elapsed.
```

### optimizer_agent.py Skeleton
```python
"""
Autonomous parameter optimization for Bot3 strategies.
Pattern: read → propose → run → evaluate → store → update → repeat
"""
import optuna
import subprocess
import json
import os
from pathlib import Path

ENV_PATH = Path("trading_bot_v2/.env")  # or Bot3/.env
STUDY_METRIC = "mean_oos_sharpe"
MIN_IMPROVEMENT = 0.05
MIN_TRADES = 15

def get_current_params(strategy: str) -> dict:
    """Load current .env params for a given strategy prefix."""
    params = {}
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith(f"{strategy}_") and "=" in line:
                key, val = line.split("=", 1)
                params[key.strip()] = val.split("#")[0].strip()
    return params

def run_backtest(strategy: str, params: dict) -> dict:
    """Run BTV2 walk-forward backtest with given params, return metrics."""
    # Build env overrides
    env = os.environ.copy()
    env.update(params)
    result = subprocess.run(
        ["python", "-m", f"BTV2.test_{strategy.lower()}_walkforward"],
        capture_output=True, text=True, env=env, cwd="Bot3/"
    )
    return json.loads(result.stdout.split("JSON_RESULT:")[-1])

def objective(trial: optuna.Trial, strategy: str, current_best: float) -> float:
    params = {
        "MEAN_REVERSION_RSI_OVERSOLD": str(trial.suggest_float("rsi_oversold", 20, 40)),
        "MEAN_REVERSION_RSI_OVERBOUGHT": str(trial.suggest_float("rsi_overbought", 60, 80)),
        "MEAN_REVERSION_BB_PROXIMITY": str(trial.suggest_float("bb_proximity", 0.05, 0.20)),
        "MEAN_REVERSION_ATR_STOP_MULTIPLIER": str(trial.suggest_float("atr_stop", 2.0, 4.5)),
    }
    result = run_backtest("mean_reversion", params)
    if result["n_trades"] < MIN_TRADES:
        return -999.0  # Penalize low-trade configs
    return result["mean_oos_sharpe"]

def optimize(strategy: str = "MEAN_REVERSION", n_trials: int = 100):
    study = optuna.create_study(
        direction="maximize",
        study_name=f"{strategy}_optimization",
        storage="sqlite:///BTV2/optimization_studies.db",  # Persist between runs
        load_if_exists=True,
    )
    study.optimize(lambda t: objective(t, strategy, study.best_value if study.trials else -999), 
                   n_trials=n_trials, timeout=5400)
    
    best = study.best_trial
    # Store to RAG
    store_to_rag(strategy, best)
    # Update .env if improvement
    if best.value > get_current_env_sharpe(strategy) + MIN_IMPROVEMENT:
        apply_params_to_env(strategy, best.params)
        print(f"✅ Improved! New Sharpe: {best.value:.3f} → .env updated")
    else:
        print(f"ℹ️ No improvement over current live params")
```

### Why Optuna Instead of Grid Search
The current BTV2 approach is exhaustive grid search. With 5 parameters × 4 values each,
that's 4^5 = 1,024 combinations — most of which are poor. Optuna's **Tree-structured
Parzen Estimator (TPE)** finds good regions in ~50 trials instead of 1,024.

**Speed comparison:**
- Grid search (4^5): ~1,024 backtest runs
- Optuna TPE (50 trials): ~50 backtest runs, typically finds 90%+ of grid search quality
- **~20x faster**

---

## 3. autoresearch — What to Use vs What to Adapt

### What autoresearch IS
- A system for **overnight autonomous LLM training experiments** on a GPU
- Modifies `train.py` (PyTorch model code) between runs
- Uses `val_bpb` (validation bits per byte) as the single metric
- Requires an H100 or similar GPU, `uv`, specific Python env

### What We CAN'T Use Directly
- The actual train.py / prepare.py / GPU training infrastructure
- The code-modification approach (it edits Python neural net code, not trading params)

### What the Pattern Gives Us (High Value)
The design philosophy maps perfectly:

| autoresearch concept | Trading equivalent |
|---------------------|-------------------|
| `program.md` | `optimize.md` — what metric, what constraints, what strategy |
| `train.py` | `.env` + BTV2 strategy parameter dicts |
| 5-min training budget | One walk-forward backtest run |
| `val_bpb` metric | OOS Sharpe ratio |
| Keep/discard decision | Update .env if Sharpe improves by ≥ 0.05 |
| Agent modifies code | Optimizer proposes new .env values via Optuna |
| Overnight loop | Scheduled task via `mcp__scheduled-tasks` |

### RAG as the Experiment Log
Rather than just CSV results, use the existing RAG system to store every trial:

```python
mcp__rag__rag_ingest(
    id=f"optim/{strategy}-{trial.number}-{date}",
    content=f"""
Strategy: {strategy}
Trial: {trial.number}
Params: {trial.params}
OOS Sharpe: {trial.value}
Win Rate: {result['win_rate']}
Max DD: {result['max_dd']}
Trade Count: {result['n_trades']}
Decision: {'ACCEPTED' if improved else 'REJECTED'}
""",
    metadata={
        "type": "optimization_trial",
        "strategy": strategy,
        "agent": "glitch",
        "tags": ["optimization", strategy.lower(), "trial"]
    }
)
```

This means any agent on any machine can:
```python
mcp__rag__rag_search(query="mean reversion best params OOS Sharpe")
```
...and immediately get the best known configuration with full experimental context.

---

## 4. Immediate Action Items

### Priority 1 — Fix Broken Feedback Loop (This Week)
The audit found strategies have hardcoded values that don't read from .env.
Until this is fixed, changing .env does nothing for those strategies.

**Files to fix:**
- `trading_bot_v2/strategies/mean_reversion.py` — already reads .env ✅ (fixed previously)
- Verify `strategy_manager.py` reads `ENABLE_*` flags
- Fix grid_trading.py constructor defaults mismatch (5 levels vs .env 10)

### Priority 2 — Align BTV2 Signal Generation with Live Bot
The biggest source of backtest inflation is BTV2 not enforcing the 8-flag validation.
Add a `STRICT_VALIDATION=true` mode to BTV2 that:
1. Requires `volume_confirmation` (volume > threshold)
2. Requires `rrr_meets_minimum` (RRR ≥ 1.0)
3. Enforces cooldown periods
This will reduce signal counts but make backtest results trustworthy.

### Priority 3 — Build optimizer_agent.py
Start with Mean Reversion (daily, fastest backtest, most data).
Then VWAP Scalping (5m, most sweep history already done).
Grid Trading should be disabled pending redesign.

### Priority 4 — Schedule Overnight Runs
Use the `schedule` skill to run optimizer_agent.py nightly at 2am UTC.
Store results to RAG. Review RAG search results in morning.

---

## 5. Metrics to Track Per Strategy

| Metric | Minimum Bar | Notes |
|--------|-------------|-------|
| OOS Sharpe | > 0.5 | Primary optimization target |
| Profit Factor | > 1.3 | Secondary guard |
| Win Rate | > 45% | Sanity check |
| Max Drawdown | < 25% | Hard constraint |
| OOS Trade Count | ≥ 15 | Reject low-sample configs |
| Consistency | > 60% windows Sharpe > 0 | Robustness check |

---

## 6. Grid Trading Recommendation

**Disable in live bot until redesigned.** The yearly backtest data is clear:
- 5 of 7 years (2018–2024) show net losses
- OOS Sharpe on BTC: -0.19
- Only profitable in 2019 and 2021 bull conditions
- Grid strategies require sideways ranging markets, which are rare on perpetuals

**Set `ENABLE_GRID_TRADING=false` in .env until a mean-reversion-filtered version is built.**
