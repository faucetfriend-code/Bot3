# `min_confidence` means three different things

Date: 2026-08-02
Status: mean_reversion fixed; grid_trading identical and **not** fixed (pending a decision)
Related: `docs/MEANREVERSION-MTF-CONFIDENCE-2026-08-02.md`

## The finding

Nine strategies declare a `min_confidence` parameter. Six of them have it
tuned by Optuna. The name is the same in every case; the behaviour is not.
Classified by walking each class's AST — asking whether the attribute is ever
an operand of a comparison, and whether it is ever assigned *into* the
confidence value:

| strategy | compared against? | assigned to `confidence`? | meaning | in search space? |
|---|---|---|---|---|
| `ma_crossover` | yes (`:687`, `:766`) | no | **gate** | yes |
| `funding_arb` | yes (`:325`) | yes (`:256`) | **gate** | yes |
| `momentum_scalping` | no | yes (`:529`) | floor/seed | yes |
| `orderbook_imbalance` | no | yes (`:398`, `:423`) | floor/seed | yes |
| `vwap_scalping` | no | yes (`:830`) | floor/seed | yes |
| `vwap_pullback` | no | yes (`:436`) | floor/seed | no |
| `session_range_breakout` | no | yes (`:406`) | floor/seed | no |
| `mean_reversion` | no | no | **inert** | ~~yes~~ removed 2026-08-02 |
| `grid_trading` | no | no | **inert** | **yes — still tuned** |

(Update 2026-10-10: `funding_arb` was removed from the codebase as never tested; its
row above is kept as part of the original finding.)

Three distinct semantics:

**Gate.** `if confidence < self.min_confidence: return None`. A higher value
means fewer signals. This is what the name implies and what a reader assumes.

**Floor / seed.** `confidence = self.min_confidence`, sometimes
`max(self.min_confidence, ...)`. It is not a minimum admission threshold at
all — it *sets* or floors the confidence the signal is emitted with. Raising
it does not filter anything; it makes surviving signals look more confident,
which downstream means **larger** live positions via `ConfidenceSizer`. Tuning
it is legitimate, but anyone reading the parameter name will predict the
opposite sign of effect.

**Inert.** Assigned in `__init__`, then referenced only inside debug log
f-strings. Nothing reads it. Tuning it samples noise.

## Why inert is worse than it sounds

For `mean_reversion` and `grid_trading` there is no code path from the value to
any outcome, in backtest or live:

- Neither strategy compares anything against it.
- The only gate that could act on confidence is
  `StrategyManager._apply_regime_confidence_gate` (`strategy_manager.py:1597-1644`),
  whose threshold is the global `MIN_SIGNAL_CONFIDENCE_FLOOR` (0.0 in `.env`)
  plus a per-regime adjustment that is non-zero only for `INDECISIVE` and
  `RANGING_VOLATILE`. `mean_reversion` is admitted only in `RANGING_CALM`
  (`market_regime.py:1026`), where the adjustment is 0.0, so the gate returns
  early every time.
- The backtest engine's sizing is fixed-fractional and never reads confidence
  (`engine.py:1202-1207`).

Measured proof for `mean_reversion` — BTC-USDC 2024-01-01..2024-07-01, adopted
params, only `min_confidence` varied:

| arm | trades | PF | final equity |
|---|---|---|---|
| absent | 86 | 0.794955 | 9969.0244 |
| 0.5228 (the adopted value) | 86 | 0.794955 | 9969.0244 |
| 0.05 | 86 | 0.794955 | 9969.0244 |
| 0.95 | 86 | 0.794955 | 9969.0244 |

Identical to eight decimal places. A value of 0.95 against a gate would have
suppressed nearly every signal.

## What was done

`min_confidence` was removed from the `mean_reversion` search space
(`search_spaces.py`, both the range dict and `PARAMETER_TYPES`) and from
`ADOPTED_PARAMS` (`monthly_retune.py`), where it had held 0.5228
(`vol_low:trend`) and 0.5375 (`vol_mid:trend`). The other four values per state
are byte-identical.

The removal is behaviour-preserving by construction, which is why it needed no
adoption decision: nothing was replaced, a dimension that could not affect the
objective was withdrawn. Optuna had been spending trial budget on it that the
four effective dimensions could have used — so every past "tuned beats default"
edge for `mean_reversion` was really achieved on four dimensions, not five.

The attribute itself is kept (config and overlay compatibility) with its
docstring and log lines now stating plainly that it gates nothing.
`trading_bot_v2/tests/test_optimization/test_min_confidence_removed.py` pins
this, including an AST guard that fails if anyone starts comparing against it —
verified to fire on a real gate rather than passing vacuously.

## Why not the other option

Enforcing it — `if confidence < self.min_confidence: return None` — would make
it a genuine lever, and was rejected. `mean_reversion.py` states twice, at
`:643-644` and `:816-817`, *"Prompt 058: Confidence affects SIZE, not
permission / Low confidence = smaller position via ConfidenceSizer, not
blocked."* Enforcing reverses a deliberate design decision, and justifying that
reversal needs evidence that permission-gating beats size-scaling. Nobody has
that evidence, and the backtest cannot produce it: confidence is structurally
unmeasurable there (see the companion doc). Shipping it would have been a blind
behaviour change justified only by "the parameter ought to do something".

## Open

**`grid_trading` has the identical defect and is still tuned.** Same AST
verdict, same absence of any consumer, and it is in the search space. It has no
`ADOPTED_PARAMS` entry, so the fix is the one-line search-space removal alone.
Left for a decision rather than bundled in, since the task scope was
`mean_reversion`.

**The floor/seed group is not a defect but is badly named.** For those five,
raising `min_confidence` increases live position size — the opposite of what
the name suggests. Worth renaming to something like `base_confidence`, which is
a mechanical change with no behavioural effect, but it touches five strategies
and their env vars, so it is not free.
