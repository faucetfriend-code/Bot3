# Deep Dive: ORB Methodology, Regime-Switching Models, and Validation Practice

**Date:** 2026-07-19
**Context:** Our naive ORB port (SessionRangeBreakout) backtests at PF 0.34–0.74 on BTC/ETH/SUI perps while a commercial "Dynamic ORB Strategy Suite" on TradingView reports PF 2.26 / 63% WR / 136 trades-yr. This doc explains the gap mechanism-by-mechanism, surveys regime-switching best practice, and lays out a prioritized roadmap for Bot3's regime system.

**Evidence labels used throughout:**
- **[LOCAL]** — verified directly in our codebase this session
- **[WEB-UNVERIFIED]** — extracted from a fetched source by the research harness; adversarial verification pass failed on infrastructure (session limits), so treat as probable but unconfirmed
- **[LIT]** — established published research from model knowledge (paper named); not re-fetched this session

---

## Part 1 — Why their PF 2.26 and our PF 0.5 can both be "real"

### 1.1 The instrument/session mechanism (the big one)

The documented ORB edge lives on **US equities and index futures at the 9:30 ET cash open** — not in 24/7 markets. The canonical research is Zarattini & Barbon, *"Can Day Trading Really Be Profitable?"* (2023, SSRN), which found a 5-minute ORB on QQQ (with leverage, traded only in the direction of the opening move, exit at close) massively outperformed buy-and-hold over 2016–2023; and Zarattini, Barbon & Aziz's follow-up on individual "Stocks in Play," where the edge was **conditional on relative volume** — stocks trading multiples of their normal volume on news. **[LIT]**

Why the open matters mechanically:
1. **Overnight information accumulates while the market is closed.** The open is where ~17 hours of news gets priced in a burst. The opening range is a genuine auction; its breakout direction carries information about order-flow imbalance.
2. **Gap dynamics.** Gap-and-go vs gap-fill classification (open vs prior close) is a strong conditioning variable. **A 24/7 market has no prior close, therefore no gap, therefore this filter cannot exist in crypto.**
3. **Session close provides a natural exit.** ORB strategies are day-trades: exit at 16:00 ET regardless. This truncates the loss tail cheaply. Crypto has no close; our 4h time-exit is a synthetic substitute with no liquidity event behind it.

Crypto perps have session-*like* volatility inflections (00:00 UTC, US equity open) but the information-release mechanism is far weaker: the market never stopped trading, so there is nothing pent up to release. **Our PF ~0.5 result on a faithful port is consistent with the academic mechanism being absent, not with our implementation being broken.** The tightened variant improving to PF 0.74 (US-open session only, stricter volume, wider TP) is consistent too: the US open is the *most* information-bearing hour crypto has, because that's when the correlated legacy market actually opens.

### 1.2 The backtest-engine mechanism (how TradingView numbers inflate)

These claims were extracted from TradingView's own Pine Script documentation, the Bar Magnifier release post, and PineCoders' FAQ. All **[WEB-UNVERIFIED]** (verification pass errored), but they match the documentation as I know it:

- **The broker emulator synthesizes the intrabar price path from OHLC** using a heuristic (open closer to high → assume open-high-low-close path). Stop and limit fills are simulated against a price sequence that may never have occurred.
- **Any price inside the bar's high-low range is treated as fillable, with no intrabar gaps** — so a stop and a target inside one bar resolve by assumption, not data. For a strategy like ORB whose stop and TP frequently sit inside a single volatile 5m bar, the fill-ordering assumption directly manufactures win rate.
- **TradingView's own Bar Magnifier demo showed ~50% profit reduction** when intrabar data replaced the heuristic — their published example, and a decent prior for how much default backtests overstate.
- **Repainting:** `request.security(..., lookahead_on)` feeds a strategy the completed value of a higher-timeframe bar before it closes; live ATR-based exits recomputed on unconfirmed bars drift retroactively. Both are endemic in published scripts and both inflate PF/WR in ways that cannot be reproduced live.
- **Defaults:** zero slippage and, in many published scripts, zero commission; results on non-standard charts (Heikin Ashi, Renko) fill at synthetic prices that never traded — PineCoders publishes an explicit warning that these backtests are unrealistic, and Heikin Ashi bias is *directionally favorable* (synthetic open lower on longs, higher on shorts).
- **Selection/survivorship:** scripts that backtest poorly don't get published or sold. You only ever see the winners of the author's private search.

### 1.3 The multiple-testing mechanism

Bailey, Borwein, López de Prado & Zhu (*"Pseudo-Mathematics and Financial Charlatanism,"* 2014, and the Deflated Sharpe Ratio papers): **a backtest reported without the number of configurations tried is uninterpretable** — with enough trials, a PF 2.26 / 63% WR over 136 trades arises from noise with high probability. Their stronger claim: reporting a backtest while withholding trial count borders on fraud. **[LIT / WEB-UNVERIFIED]** (the portfoliooptimizationbook.com chapter restating this was fetched; verification errored).

At 136 trades, the standard error on a 63% win rate is ±4%; on PF it's larger. One year, one instrument, unknown trial count = a screenshot, not evidence.

### 1.4 What the "Dynamic" in Dynamic ORB suites actually is — and what transfers

Mechanisms commercial/serious ORB implementations layer on (practitioner consensus + the Stocks-in-Play paper) **[LIT]**, with a transfer verdict for crypto perps:

| Mechanism | What it does | Transfers to 24/7 crypto? |
|---|---|---|
| Relative volume filter (day's volume vs same-time-of-day average) | Trades only instruments/sessions with abnormal participation | **YES** — best candidate we're missing; our 1.5x-vs-20-bar filter is intrabar, not vs. time-of-day baseline |
| Gap classification (gap-and-go direction bias) | Conditions direction on overnight gap | **NO** — no close, no gap |
| ATR-normalized range definition (skip if range/ATR abnormal) | Rejects dead or blown-out opens | **YES** — we have a %-of-price filter; ATR-relative is better |
| Dynamic range window (end range when volatility contracts, not at fixed 30 min) | Adapts to how long the auction takes | **PARTIAL** — worth testing; crypto "auctions" are less structured |
| First-breakout-only, in direction of opening move | One decision per day, no chop re-entry | **YES** — we already do 1-trade/session |
| Retest entry (enter on pullback to range edge, not first close beyond) | Better price, filters false breaks | **YES** — untested lever in our impl |
| Session-close hard exit | Free tail truncation | **NO** — synthetic time-exit only (we have it) |
| Daily loss limit / no-trade after N losses | Caps regime mismatch damage | **YES** — cheap, we lack it |
| HTF trend alignment | Only break out with the tide | **YES** — the tuning task now running adds exactly this |
| Scale-out + trailing runner (e.g. half at 1R, trail rest) | Converts 40% WR math into positive expectancy | **YES** — we exit all-or-nothing today |

Honest bottom line: even with all transferable mechanisms, published evidence for a *standalone* ORB edge in crypto is thin to nonexistent — the harness found no credible academic study demonstrating one (the fetches toward Concretum/SSRN/arXiv failed this session, but no search result surfaced a crypto-ORB paper equivalent to the QQQ result either). Expectation management: transferable mechanisms might lift PF 0.74 toward ~1.0–1.3; they will not conjure the index-futures edge into a market with no open.

### 1.5 If the friend is genuinely profitable live

Both can be true: the screenshot's backtest is inflated **and** the trader makes money — typically because the human adds discretionary session/instrument selection, skips bad days, and sizes down in chop (i.e., *they* are the regime filter). The constructive questions to ask them:

1. Is that a **live** track record or the strategy-tester report? (TradingView shows both; only live fills matter.)
2. Bar Magnifier on or off? Commission and slippage settings?
3. Standard candles or Heikin Ashi/Renko?
4. How many parameter sets were tried before this one was published?
5. What instrument and session — index futures at the US open? (If yes, their edge may be real *and* structurally unavailable on crypto perps.)

---

## Part 2 — Regime-switching: state of the art vs. Bot3

### 2.1 The model landscape

- **Threshold heuristics (ADX/vol — what we run):** transparent, cheap, zero training. Weaknesses: hard thresholds create boundary chatter; no probabilistic confidence; regime definitions are hand-labeled, not learned.
- **Gaussian Mixture Models (what we have scaffolded):** unsupervised clustering of feature vectors into k regimes. No temporal structure — each bar classified independently, so raw GMM output flickers and needs external smoothing.
- **Hidden Markov Models (Hamilton 1989, the canonical regime model in finance [LIT]):** GMM emissions *plus* a transition matrix. The transition matrix is the killer feature: high self-transition probabilities give you **learned hysteresis** — the model demands strong evidence to switch, and Viterbi/forward-backward smoothing comes free. An HMM is roughly "GMM + exactly the dwell-time discipline our ADX detector lacks." Natural upgrade path from our GMM scaffold (same features, same k).
- **Change-point detection (BOCPD, CUSUM):** detects *that* the distribution changed, fast, without pre-defining regimes. Best as a circuit-breaker complement ("something broke, de-risk now"), not as the allocator.
- **Regime count:** equity/macro literature (Ang & Timmermann, *Regime Changes and Financial Markets* [LIT]) consistently identifies **2–4 meaningful regimes**; crypto studies typically land on 3–4 (trend-up, trend-down, high-vol chop, low-vol drift). Beyond 4, regimes stop being statistically distinguishable out-of-sample. Our 5-label taxonomy is fine, but note our GMM is configured for 3 components mapped onto the 5 labels — and can never emit TRENDING_STRONG or INDECISIVE **[LOCAL]** (`ml/gmm_regime.py` `_DEFAULT_REGIME_MAP`).
- **Selection vs. modulation:** the literature and fund practice favor **both**: regime-conditional *strategy selection* (what we do) plus regime-conditional *parameter modulation* — position size, stop width, confidence gates varying by regime. Sizing modulation is widely considered the higher-Sharpe lever of the two, because being half-size in the wrong regime dominates being flat slightly late.

### 2.2 Bot3's regime system, audited [LOCAL — all verified this session]

**What works:**
- Clean 4h ADX taxonomy with 1h fallback; 1h TTL cache (`market_regime.py:160-287`, `:289-339`)
- A real, if weak, confirmation mechanism: regime must be detected on 2 consecutive cache cycles (`_detect_regime_with_confirmation` :352-399) — count-based, ~1–2h effective delay
- Grid strategy gets genuine regime-transition handling: partial unwind on regime-disallowed, keeping trend-aligned positions (`grid_lifecycle_manager.py:872-937`)
- Full GMM scaffold with 6-feature engineering, train/predict/persist code, and transparent integration into the detector behind `USE_ML_REGIME` (`ml/gmm_regime.py`, `ml/feature_engineering.py`)
- Real walk-forward optimization inside Optuna (`optuna_runner.py:251-282` — window-averaged objective with variance penalty)

**The gaps, ranked by how much regime value they leave on the table:**

1. **No regime observability.** `EventType.REGIME_CHANGED` is defined and subscribed (Telegram) but **never emitted anywhere**; no regime-history table exists; regime is persisted only as denormalized columns on signals/grids. We cannot currently measure dwell times, flip frequency, or per-regime strategy PnL — meaning every other improvement below is unmeasurable.
2. **Regime modulates nothing but strategy selection.** Zero regime references in `kelly_position_sizer.py`, `confidence_sizer.py`, stops, or confidence gates. Regime is an on/off switch and a combine-weight; best practice makes it a risk dial.
3. **Weak hysteresis.** 2-count confirmation, no minimum dwell time, no hysteresis band on the volatility score (single 65.0 cutoff), no transition probabilities. ADX thresholds do form a natural band (enter trending >25, but "moderate" starts at 20), yet vol-score chatter at ~65 flips RANGING_CALM/VOLATILE with only the 1-cycle delay.
4. **GMM is dormant and cannot be enabled:** no training pipeline calls `train()`, no model artifact exists, plus a latent bug — `_try_load_model` writes `self._config` while predict reads `self.config`, so a loaded model's config is silently ignored (`gmm_regime.py:353` vs `:135`).
5. **Optuna cannot fit per-regime parameters** — one strategy, one flat window per study; search-space comments *mention* regimes but nothing conditions on them.
6. **Dead code / stale docs:** INDECISIVE is unreachable in `detect_regime` (the ADX ranges fully cover it); docstrings cite old 30/25/75-percentile thresholds vs. actual 25/20/65.
7. **`backtesting/walk_forward.py` never uses its training window** — it is segmented OOS reporting, not walk-forward optimization (the real WF lives only inside Optuna).

### 2.3 Roadmap: regime switching to maximum benefit (prioritized)

**P0 — Measure before modeling (small, unblocks everything):**
- Emit `REGIME_CHANGED` from the detector on confirmed transitions; persist to a new `regime_history` table (symbol, old, new, adx, vol_score, confidence, timestamp).
- Backfill analysis: reconstruct approximate regime series from historical candles offline; report dwell-time distribution and flips/week per symbol. **Decision data:** if median dwell < ~12h on 4h detection, the detector is chattering and P1 is urgent.
- Add per-regime PnL attribution to StrategyMonitor (join trades to regime_history) — this answers "which strategy actually earns in which regime," which is currently assumed, not measured.

**P1 — Hysteresis hardening (cheap, high value):**
- Asymmetric bands: enter TRENDING_STRONG at ADX > 25, exit only below 22; vol-score band 60/70 instead of a single 65.
- Minimum dwell time (e.g., no re-classification within 4h of a confirmed switch) layered on the existing 2-count confirmation.
- Fix INDECISIVE: make it a real state (e.g., ADX in 20–25 *with* falling ADX slope) or delete it and the stale docstrings.

**P2 — Regime-conditional risk modulation (the biggest expected-value item):**
- Per-regime position-size multipliers in RiskManager (e.g., TRENDING_STRONG 1.0, RANGING_VOLATILE 0.6, INDECISIVE 0.4) — RiskManager stays authoritative, strategies untouched.
- Per-regime stop-width (ATR-multiple) and confidence-gate adjustments.
- Regime-transition position review for non-grid positions (the grid manager already has this; nothing else does): on confirmed flip, re-evaluate open positions against the new regime's strategy set.

**P3 — Activate the ML regime path properly:**
- Build the missing training pipeline (load 2000+ 4h candles per symbol, call `GMMRegimeDetector.train()`, persist artifact + LATEST marker). Fix the `_config`/`config` bug first.
- Prefer upgrading GMM → **HMM** (hmmlearn: `GaussianHMM`, same 6 features): the transition matrix subsumes P1's hand-tuned hysteresis with learned self-transition probabilities. Keep k=3, map to our labels, keep ADX as the always-on fallback.
- Ship shadow-mode first: run ML detector alongside ADX, log both to regime_history, compare dwell stability and (via P0 attribution) which classification would have allocated better. Promote only on evidence.

**P4 — Per-regime parameter optimization:**
- Extend the Optuna adapter to segment backtest windows by detected regime and run regime-conditioned studies (per-strategy, per-regime parameter sets), using the existing walk-forward objective. Store as `params[regime]` overlays; strategies read the overlay for the current regime.

**P5 — Validation stack (applies to every strategy, not just ORB):**
- Make `walk_forward.py` actually optimize on train windows (wire it to the Optuna adapter) so standalone WF = train-on-window-N, test-on-window-N+1.
- Add a Deflated Sharpe Ratio / trial-count report to the sweep output (López de Prado & Bailey [LIT]): every sweep records how many configs were tried; the report deflates accordingly.
- Adopt the ex-ante gate we already used for ORB as standing policy: hypothesis written before tuning, PF/DSR thresholds on untouched OOS data, minimum 30–50 trades per symbol, cross-asset consistency, then testnet paper period before size.

---

## Part 3 — Practical answer to "how do we structure our models?"

1. **Keep the regime-switching architecture.** It is the correct response to "no single algo survives all regimes." The gap is not the architecture — it's that regime currently only gates *which* strategies run, not *how much* they risk (P2), and that we can't yet measure whether the regime labels are even stable (P0).
2. **Treat ORB as a minor overlay, not a savior.** Apply the transferable mechanisms (relative-volume-vs-time-of-day filter, retest entry, scale-out/trailing runner, daily loss limit, HTF trend alignment — the last already in flight in the tuning task) and re-gate. If it clears PF > 1.3 OOS, it earns a small overlay weight; if not, archive it — the edge may simply not exist off index futures.
3. **Invest the freed effort in P0→P2.** Regime observability + regime-conditional sizing improves *all eight existing strategies* simultaneously, which strictly dominates adding a ninth strategy of unproven edge.
4. **Adopt the validation stack (P5) as house rules.** The single biggest difference between our process and "shiny backtest" vendors is that we count our trials and hold out data. That discipline is the actual golden algo.

---

*Research provenance note: web-sourced claims in Part 1.2–1.3 were extracted by a fan-out research run on 2026-07-19; the adversarial verification phase failed on session rate limits (all 25 verifier panels errored), so those claims carry the [WEB-UNVERIFIED] label. Sources: tradingview.com Pine Script strategy docs, TradingView Bar Magnifier release post, PineCoders non-standard-chart FAQ, crosstrade.io repainting guide, portfoliooptimizationbook.com ch. 8.3. Academic fetches (SSRN/arXiv/Concretum) did not complete; [LIT] items are cited from model knowledge and should be pulled directly if used for anything load-bearing.*

---

## Validation policy (P5, standing house rules)

Adopted 2026-07-20. These rules apply to EVERY strategy (existing and future), not just ORB. The implementation lives in `trading_bot_v2/validation/` (statistics + gate), the trial registry in `database.py` (`trial_registry` table), and the walk-forward optimizer in `trading_bot_v2/backtesting/walk_forward.py`.

1. **Ex-ante hypothesis before tuning.** Write down what edge the strategy is supposed to capture and why, BEFORE running any parameter search. If the hypothesis cannot be stated, the strategy is curve-fitting by construction.
2. **Every optimization records its trial count.** This is automatic: `OptunaRunner.optimize()` writes a row to the `trial_registry` table (strategy, regime, scope, n_trials = completed + pruned, variance of trial objective values when the objective is a Sharpe proxy, study name). The registry's `get_total_trials(strategy)` is the N used to deflate reported Sharpe ratios. Manual/sweep experiments that explore configurations outside Optuna must record themselves too (`DatabaseManager.save_trial_registry_entry(scope="manual")`).
3. **Out-of-sample only via walk-forward test windows.** Performance claims come from the aggregate OOS series produced by `python -m trading_bot_v2.backtesting.walk_forward --strategy X ...`: each window optimizes on its train slice and is evaluated on the untouched test slice. Full-period in-sample backtests are for debugging, never for promotion decisions.
4. **Gate thresholds** (env-tunable, defaults in parentheses; endpoint `GET /api/validation/gate-policy`, CLI `python -m trading_bot_v2.validation.gate`):
   - minimum closed trades per symbol: GATE_MIN_TRADES (30)
   - pooled profit factor: > GATE_MIN_PF (1.3)
   - PSR >= GATE_MIN_PSR (0.95), or DSR >= GATE_MIN_PSR when the registry knows N
   - cross-symbol consistency: positive expectancy on >= 2 symbols
5. **Testnet paper period before size.** A strategy that clears the gate runs on testnet paper for its observation period before any capital-at-risk sizing; live results must not degrade materially from the OOS backtest before size is added.
6. **What DSR means.** The Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) re-tests the observed Sharpe against the Sharpe that the BEST of N skill-less configurations would show purely from selection. DSR >= 0.95 means less than 5% probability that the result is a fluke of the search size. A high raw Sharpe with a failing DSR is exactly the signature of an overfit search.
