# CLAUDE.md

## Project Overview

Multi-strategy cryptocurrency trading bot for **Pacifica.fi** perpetual futures (testnet). 8 strategies with ADX-based regime detection, Kelly Criterion position sizing, and circuit breaker risk management. Python 3.14, FastAPI, SQLite, WebSocket.

## Quick Start

```bash
# Preferred: double-click run_bot.bat (sets PYTHONPATH automatically)

# Or manually:
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3"
python -m trading_bot_v2.api_server

# Dashboard: http://localhost:8000

# Kill stale process (Windows):
netstat -ano | findstr :8000
taskkill /F /PID <pid>
```

**IMPORTANT**: Always run as module (`python -m trading_bot_v2.api_server`), never as script. Relative imports require package context. The server runs with `reload=False` - code changes require full restart.

## Testing

```bash
# All tests (from Bot3 directory)
pytest trading_bot_v2/tests/ -v

# Key test files:
pytest trading_bot_v2/tests/test_signal_routing.py -v      # Signal pipeline
pytest trading_bot_v2/tests/test_strategy_e2e.py -v         # Strategy end-to-end
pytest trading_bot_v2/tests/test_component_orchestration.py -v  # Component integration

# Legacy tests also exist at trading_bot_v2/test_*.py (root level)
pytest trading_bot_v2/test_strategy_manager.py -v

# By pattern
pytest trading_bot_v2/ -k "mean_reversion"
```

## Architecture

```
Bot3/
  trading_bot_v2/
    api_server.py            # FastAPI + serves interface.html + REST/WebSocket
    trading_bot.py           # Core loop (COORDINATOR - delegates everything)
    strategy_manager.py      # Regime->strategy mapping, conflict resolution
    market_regime.py         # ADX-based regime detection (4h timeframe, default)
    volatility_regime.py     # Realized-volatility regime detection (REGIME_MODE=volatility)
    risk_manager.py          # AUTHORITATIVE for position sizing/exposure
    grid_lifecycle_manager.py # Grid state machine (in-memory, lost on restart)
    execution_layer.py       # 1m/5m entry timing refinement
    signal_logger.py         # In-memory + CSV + DB signal logging
    event_system.py          # Synchronous pub/sub EventBus
    pacifica_client.py       # REST API wrapper (Ed25519 auth)
    pacifica_ws_client.py    # WebSocket client (singleton, real-time data)
    multi_timeframe_fetcher.py # 15m/1h/4h candle fetcher with 60s TTL cache
    data_manager.py          # Candle + perpetual-funding store manager (CLI: --coverage,
                             # --funding, --funding-only, --data-dir)
    backtesting/funding.py   # Binance 8h -> venue 1h funding mapping (the assumption)
    kelly_position_sizer.py  # Kelly Criterion (activates at 50+ trades)
    database.py              # SQLite (trading_bot.db, auto-created)
    config.py                # .env loader (override=True), all enums
    models.py                # Signal, OrderSide, Trade dataclasses
    strategies/
      mean_reversion.py      # RSI + BB (RANGING_CALM)
      ma_crossover.py        # Fast/slow MA cross (TRENDING_STRONG, stateful; .env runs 10/30)
      grid_trading.py        # ATR-spaced grid (RANGING_VOLATILE)
      liquidation_capture.py # Cascade detection (ALL regimes, overlay)
      vwap_scalping.py       # VWAP deviation (RANGING regimes)
      funding_arb.py         # Funding rate exploit (ALL regimes, overlay; backtestable
                             # since 2026-07-29 against real funding history)
      momentum_scalping.py   # EMA 9/21 cross (TRENDING regimes)
      orderbook_imbalance.py # L2 bid/ask analysis (ALL regimes, overlay)
    tests/                   # Primary test directory
  interface.html             # Web dashboard (served by api_server.py)
  signals_log.csv            # Signal log output (auto-generated)
  trading_bot.db             # SQLite database (auto-created)
  run_bot.bat                # Windows launcher
  .env                       # Config (100+ vars, strategy enables, API keys)
```

## Signal Flow

```
Market Data -> Regime Detection (ADX 4h) -> Strategy Selection -> Signal Generation
-> Conflict Resolution -> 8-Flag Validation -> Position Sizing -> Execution
-> Signal Logger (memory + CSV + DB)
```

## Regime-to-Strategy Mapping

| Regime | ADX | Strategies (weighted) |
|--------|-----|----------------------|
| TRENDING_STRONG | >25 | MACrossover (60%), MomentumScalping (40%) |
| TRENDING_MODERATE | 20-25 trending | MomentumScalping (100%) |
| RANGING_VOLATILE | <20, high vol | GridTrading (70%), VWAPScalping (30%) |
| RANGING_CALM | <20, low vol | MeanReversion (70%), VWAPScalping (30%) |
| INDECISIVE | 20-25 | Stay flat |

**Overlay strategies** (run in ALL regimes independently): LiquidationCapture, FundingArb, OrderBookImbalance

Under `REGIME_MODE=volatility` the taxonomy is three terciles of trailing
realized volatility instead, with a **proposed** mapping grounded in what each
strategy needs mechanically (VOL_LOW -> MeanReversion, VOL_MID -> GridTrading +
VWAPScalping, VOL_HIGH -> MomentumScalping + MACrossover, VOL_WARMUP -> flat).
`docs/REGIME-VOLATILITY.md` has the measurement and the argument, including why
the ADX mapping above looks inverted against it.

## Critical Gotchas

### Pacifica API Side Values
The Pacifica API uses **non-standard side names** - this has caused multiple bugs:
- **Orders**: `"bid"` (buy) / `"ask"` (sell) - NOT "BUY"/"SELL"
- **Positions**: `"long"` / `"short"` (lowercase) - NOT "LONG"/"SHORT"
- **Amount**: String type (e.g., `"1.5"` not `1.5`)
- **Funding**: Hourly (24x/day), not 8h like most exchanges

### Grid State is In-Memory Only
`GridLifecycleManager._grids` dict lives in memory. On restart, grids are orphaned. The bot now re-adopts orphaned grids from exchange orders on startup (`_sync_existing_grids`), but if that fails, use the "Cancel Orders" button in the dashboard which also clears grid state.

### EventBus is Synchronous
`publish_event()` calls all subscribers immediately inline. Code after `publish_event()` runs AFTER all callbacks complete. This means signal execution happens synchronously when the signal event is published.

### .pyc Cache Staleness
Python caches bytecode in `__pycache__/`. After code changes, stale `.pyc` files can serve old code even after restart. If behavior doesn't match code, delete all `.pyc`:
```bash
Get-ChildItem -Recurse -Filter "*.pyc" | Remove-Item -Force
```

### Mixed Logging
- Most modules: `import logging` (standard library)
- `signal_logger.py`, `kelly_position_sizer.py`: `from loguru import logger`

## Key Component Rules

**RiskManager** is AUTHORITATIVE - all position sizing and exposure limits go through it. Other components delegate, never calculate their own.

**TradingBot** is COORDINATOR only - does not own risk logic (RiskManager) or grid state (GridLifecycleManager). Orchestrates via ComponentRegistry.

**Signal validation** requires all 8 flags True: `volume_confirmation`, `multi_timeframe_alignment`, `support_resistance_valid`, `rrr_meets_minimum`, `liquidation_buffer_safe`, `account_risk_ok`, `margin_drawdown_ok`, `forbidden_conditions_clear`.

**Circuit breaker** triggers at 10% portfolio loss (`CIRCUIT_BREAKER_LOSS_PCT`), warning at 80% of threshold.

## API Endpoints (Key)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard (interface.html) |
| `/api/status` | GET | Bot status, balance, regime, grids |
| `/api/activity` | GET | Recent market activity with indicators |
| `/api/signals/recent` | GET | Signal log (from SignalLogger) |
| `/api/signals/stats` | GET | Signal statistics by strategy |
| `/api/trades` | GET | Trade history |
| `/api/positions` | GET | Open positions |
| `/api/bot/start` | POST | Start trading |
| `/api/bot/stop` | POST | Stop trading |
| `/api/orders/cancel-all` | POST | Cancel all orders + clear grid state |
| `/api/grids/{symbol}/clear` | POST | Clear specific grid state |
| `/api/debug/key-config` | GET | Verify API key configuration |

## Import Patterns

```python
# In trading_bot_v2/strategies/*.py:
from ..models import Signal, OrderSide
from ..config import StrategyType, AssetClass, MarketState, TradeQuality

# In trading_bot_v2/*.py (top level):
from .models import Signal, OrderSide
from .config import StrategyType, config
```

## Adding a New Strategy

1. Create `trading_bot_v2/strategies/your_strategy.py` with `generate_signals()` method
2. Add to `StrategyType` enum in `config.py`
3. Export from `strategies/__init__.py`
4. Register in `StrategyManager.__init__()` with enable flag
5. Add regime mapping in `market_regime.py`
6. Add `ENABLE_YOUR_STRATEGY=true` to `.env`

## Environment Variables (.env)

Key variables:
- `AGENT_WALLET_PRIVATE_KEY` / `ACCOUNT_PUBLIC_KEY` - Pacifica auth (Ed25519)
- `TESTNET=true` - Testnet mode
- `ENABLE_MEAN_REVERSION=true` (one per strategy)
- `REGIME_MODE=adx` - Which regime taxonomy the detector uses. `adx` (default,
  shipped) or `volatility` (trailing-realized-volatility terciles,
  `trading_bot_v2/volatility_regime.py`). The volatility scheme measures 2.6-6x
  better on forward-volatility discrimination than the ADX label
  (`docs/REGIME-VOLATILITY.md`), but switching it on re-gates every strategy on
  every symbol and invalidates the campaign, the census, every stored overlay
  and every per-regime study. Tunables:
  `REGIME_VOL_WINDOW=14`, `REGIME_VOL_REFERENCE_DAYS=120`,
  `REGIME_VOL_MIN_OBSERVATIONS=40`, plus the proposed strategy mapping
  `REGIME_VOL_STRATEGIES_{LOW,MID,HIGH,WARMUP}` and
  `REGIME_VOL_WEIGHTS_{LOW,MID,HIGH,WARMUP}`.
- `ADX_TRENDING_THRESHOLD=25.0` - Regime detection. **Do not turn this without
  reading `docs/REGIME-DISCRIMINATION.md`**: it is one global parameter that
  re-gates every strategy on every symbol, and the measured answer is that the
  regime label carries almost no forward information at any threshold. Also
  `ADX_RANGING_THRESHOLD=20.0`, `ADX_MODERATE_THRESHOLD=20.0`,
  `VOLATILITY_HIGH_PERCENTILE=65.0` (all four were documented but unwired
  before 2026-07-28), plus `ADX_EXIT_TRENDING`, `VOL_SCORE_ENTER`,
  `VOL_SCORE_EXIT`, `MIN_REGIME_DWELL_HOURS`.
- `VWAP_ACTIVE_REGIMES` - Which regimes VWAPScalping is admitted to. Unset means
  the shipped `RANGING_VOLATILE,RANGING_CALM,INDECISIVE`; trending regimes are
  refused whatever you set, because VWAP's entries are counter-trend. It exists so
  a regime mapping can be falsified without editing code, and it has been: all four
  reachable subsets were measured over BTC's full contiguous history and the best is
  PF 0.81. **VWAP has no edge under any mapping** - see `docs/VWAP-LEVERS.md` before
  spending time here.
- `CIRCUIT_BREAKER_LOSS_PCT=0.10` - 10% portfolio loss stop
- `KELLY_FRACTION=0.5` / `KELLY_MIN_TRADES=50`
- `LOG_LEVEL=INFO`
- `BACKTEST_FUNDING_MODEL=flat` - How the backtest charges perpetual funding.
  `flat` (default, shipped) charges the constant `BACKTEST_FUNDING_HOURLY_PCT=0.0001`
  at every hourly settlement, longs always paying. That constant is the mean Binance
  **8-hour** BTC rate applied **hourly**, i.e. an 87.6%/yr carry against a measured
  11.66%/yr, and it can never go negative. `historical` charges the real ingested
  series instead (`{SYMBOL}_funding.parquet`, Binance USD-M, BTC from 2019-09-10);
  `funding_arb` is meaningless without it. The default stays `flat` only so the
  existing campaign remains comparable. Related: `BACKTEST_FUNDING_CONVERSION`
  (`prorata` default / `identity`), `BACKTEST_FUNDING_SCALE=1.0` (unmeasured
  cross-venue basis), `BACKTEST_FUNDING_INTERVAL_HOURS` (defaults to the exchange
  adapter's capability: Pacifica 1, Blofin 8). **Binance 8h rates as a proxy for
  Pacifica hourly funding is a modelling assumption** - see `docs/BACKTESTING_GUIDE.md`.
- `BACKTEST_SEED=0` - Seeds the SL/TP same-candle tie-break. Before it existed the
  shuffle used the global `random` module and identical runs could disagree
  (vwap_scalping/BTC 2022-06..08: PF 0.9485 vs 1.0287). Runs now reproduce exactly.
- `VALIDATION_ANCHOR_END` / `--anchor-end` - Pin the validation runner's newest window
  to a fixed date instead of the store's advancing trailing edge (FOLLOW-UPS 8e).
- `BACKTEST_HISTORY_LOOKBACK=60` - Candles of rolling history the backtest engine
  hands each strategy per timeframe. Must exceed the longest indicator lookback
  in play (a 200-period slow MA needs 201) or that strategy generates nothing.
- `BACKTEST_WARMUP_CANDLES=0` - Candles loaded before the window start so bar 1
  of the replay already has a full history slice. 0 = mirror the lookback.

## Troubleshooting

**"Verification failed" on orders**: Check `AGENT_WALLET_PRIVATE_KEY` is base58 private key (not public), agent wallet authorized on Pacifica dashboard, IP whitelisted. Use `/api/debug/key-config` to verify.

**Position sync shows 0**: Pacifica returns lowercase `"long"`/`"short"` and zero-quantity ghost positions exist. Code normalizes with `.upper()` and filters qty==0.

**Grids orphaned after restart**: Grid state is in-memory. Bot re-adopts from exchange orders on startup. If broken, use Cancel Orders button (clears both exchange orders and grid state).

**Market order `'"success"'` error**: Intermittent error on order execution. Debug logging captures raw API response. Check server logs for `Raw order response` messages.

**Env vars not updating**: `config.py` uses `load_dotenv(override=True)`. Full restart required (`reload=False`).
