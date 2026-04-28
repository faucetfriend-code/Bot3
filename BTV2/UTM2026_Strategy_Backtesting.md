# Unity Trading Model (UTM 2026) — Strategy Data for Backtesting

> The complete trading framework. Built around Liquidity Grabs, Market Structure Shifts, Delta Strength, and Time-Based Volume Confirmation.

---

## CORE FRAMEWORK (5-Step)

### Step 1 — Key Level Mapping
**Category:** Technical Analysis Foundation
**Expected R:** 3R to 5R setups

#### Primary Levels
- Supply / Demand Zones
- Previous Week High (PWH)
- Previous Day High (PDH)
- Previous Week Low (PWL)
- Previous Month Low (PML)
- Monday Levels (ML / MH)

#### Additional Confluences
- Support / Resistance
- Order Blocks
- POC / POI
- Psychological levels
- RSI extremes

#### Rule
Plan entries around key HTF levels backed by liquidity.

#### Execution Sequence
1. Wait for the entry zone alert
2. Look for LTF MSS
3. Confirm with 5–15m candle close
4. Validate with delta volume
5. Execute entry

#### Key Principle
The more levels stacking in a region, the higher the probability of reversal or expansion.
If price is not interacting with a clear HTF level → **no trade**.

---

### Step 2 — Setup Planning
**Category:** Identifying Liquidity Events

#### Liquidity Events (Triggers)
- A sweep of a prior high or low
- A final extension spike into a key level
- A Swing Failure Pattern (SFP)
- A rejection trapping late buyers or sellers

#### Rule
Wait for confirmation before trading the next move.

#### Confirmation Sequence
1. Lower timeframe range breakout
2. Structure shift confirmed
3. Delta volume confirmation

#### Key Principle
Trades should only be considered once price action confirms the reaction at the level.

---

### Step 3 — Confirmation Confluences
**Category:** MSS + Delta Volume + Sessions

#### Market Structure Shifts (5m–15m)
- **MSS** — Market Structure Shift: a change in market direction
- **BOS** — Break of Structure: confirmation the new direction is continuing
- **Rule:** No MSS/BOS with strength and candle closure = no entry

#### Delta Volume & Relative Strength
- Check delta confirmation across: 30m, 1h, 4h, Daily (bonus)
- Look for delta volume flipping to catch earliest reversal signal
- **Rule:** MSS + Delta shift across multiple timeframes = A+ setup

#### Trading Sessions (UTC)
| Session | Hours (UTC) | Notes |
|---|---|---|
| Asia (Tokyo) | 00:00 – 09:00 | — |
| London | 08:00 – 17:00 | Large volatility spike at open (08:00) |
| New York | 13:30 – 22:00 | Highest volume period at open (13:30) |
| London + NY Overlap | 13:30 – 17:00 | Most liquid & volatile |

#### Weekly Pattern
| Day | Behaviour |
|---|---|
| Monday | Forms new liquidity range |
| Tuesday | Breakout continuation or liquidity grab |
| Wed / Thu | Point of control — continuation of the big move |
| Friday NY | Usually the biggest moves up or down |
| Sunday | Futures open 23:00 UTC — gap moves into Monday |

#### Highest Volume Times (UTC)
6AM, 11AM, 12PM, 2:30PM, 3:30PM, 7PM, 12AM

---

### Step 4 — Execution
**Category:** Precision Entry & Risk Control

#### Entry Conditions
The ideal entry occurs after:
- Final liquidity sweep
- Delta strength shift
- MSS / BOS confirmation candle closure

#### Stop Loss Placement
Hard stop losses placed at the true invalidation level:
- Beyond the sweep wick (for reversals)
- Beyond the order block invalidation
- Beyond the structure high/low that invalidates the setup

> UTM Principle: Small invalidation = controlled risk.

#### Take Profit Targets
Targets liquidity pools:
- Previous highs/lows: PDH/PDL, PWH/PWL, PMH/PML
- Range extremes
- POC / mid-range magnets
- Psychological levels

#### TP Zones
- **TP1** — Partial (early liquidity)
- **TP2** — Major level
- **TP3** — Final target

| Metric | Value |
|---|---|
| Minimum expectation | 2R |
| Common A+ outcome | 4R+ |

---

### Step 5 — Risk & Trade Management
**Category:** Psychology, Risk & Trade Management

#### Risk Management
- Risk only **1–5% per trade**
- Use hard stop losses to control downside
- Leverage affects exposure, not risk — position size determines risk
- Example: 10x leverage + 10% SL = same 1R risk as 100x leverage + 1% SL

#### Trade Management (Scaling Out)
| TP Level | Partial Close % |
|---|---|
| TP1 | 20% |
| TP2 | 30% |
| TP3 | 30% |
| TP4 | 20% |

- Move SL to breakeven once early targets are hit
- TP4 marks the end of the final move → ideal hedge zone

#### Discipline Rules
- Plan trades before executing
- Wait for price to come to your levels
- If 3 losses in a row → step away from the market
- Do not over-trade; focus on highest RR setups

---

## TRADE MODELS

### Model A — Range Reversal
**Environment:** Post large market move → consolidation before next expansion

| Direction | Action |
|---|---|
| Short | Range highs after liquidity raids |
| Long | Range lows after liquidity sweeps |

#### Confirmations
1. SFP or liquidity grab
2. LTF MSS
3. Delta flip on 30m–1h

**Expected R:** 3–5R

---

### Model B — Trend Continuation Breakouts
**Environment:** Clear market trend

#### Trend Pullback Entry
- Structure holds the trend
- Price pulls back into a key level or order block
- Delta confirms continuation

#### Breakout Entry
- Previous high breaks with strong volume
- Delta expansion confirms momentum
- Optional: 15m–1h candle close confirmation

---

## A+ SETUP CHECKLIST (Backtesting Filter)

| # | Condition | Required |
|---|---|---|
| 1 | HTF Liquidity Entry Level aligns with risk-reward | ✅ |
| 2 | Liquidity grab occurs at the level | ✅ |
| 3 | Sharp reversal with 5m MSS/BOS confirmation | ✅ |
| 4 | Delta strength supports the trade (30m–1h, 4h bonus) | ✅ |
| 5 | Entry occurs at OB/POI retest with LTF BOS | ✅ |
| 6 | USDT.D price action supports the trade idea | ✅ |

> **Rule:** If 3 or more factors are missing → do not take the trade.

---

## PRE-TRADE QUESTIONS
1. Is the entry at a support or resistance level?
2. Is market structure + trend bullish or bearish?
3. Is OracleAlgo bullish or bearish?
4. Does the current trading session support the idea?
5. Is there major news affecting markets today?
6. Has candle closure confirmed the move?
7. Has the asset already moved significantly from the previous high or low?
8. Is the stop loss a true invalidation point or should that be your entry?
9. Does the trade fit within your risk management plan?

---

## TRADE CHECKLIST (6-Point)
- [ ] HTF liquidity level mapped
- [ ] Liquidity sweep occurred
- [ ] MSS / BOS confirmed
- [ ] Delta shift confirmed
- [ ] Session alignment
- [ ] Risk defined & SL placed

---

## ANALYST PLAYBOOKS

### Overview

| Playbook | Analyst | Level | Style | Timeframes |
|---|---|---|---|---|
| Breakout Framework | Sveezy | Intermediate | Breakout / Momentum | 5M, 1H |
| Level-to-Level Scalping | Soul | Advanced | Scalping / Momentum | 1M, 3M, 15M |
| Multi-Timeframe Structure Trading | Badillusion | Advanced | Swing / Intraday | 1H, 4H, 1D |
| Standard Entry Reversal Model | Prestige | Intermediate | Reversal / Confirmation | 1M, 5M, 15M |
| HTF Macro-Aligned Framework | Scient | Beginner | Swing / Position | 1D, 3D, 1W |
| Harmonic Trading | Grasady | Advanced | Harmonic / Multi-Timeframe | 1H, 4H, 1D, 1W |

---

### Playbook 1 — Breakout Framework
**Analyst:** Sveezy | **Level:** Intermediate | **Style:** Breakout / Momentum | **Timeframes:** 5M, 1H

Volume-based breakout strategy focusing on clean breaks through resistance with clear expansion space.

#### Identifying Resistance
Start by identifying a clear resistance level on the 1-hour chart. Must be a level price has respected multiple times. The setup should lead to expansion, not immediate chop.

#### Volume Gap Analysis
Look left for low-volume areas. If there's a visible volume gap above resistance, price can move quickly once it clears the level. If the area above is crowded with volume → pass.

#### Execution Model
- Drop to the 5-minute chart
- Mark the last bearish order block below the resistance level
- Wait for price to prove it can break that bearish structure with strength and volume

#### Trade Validation
- Clean break through + clear space before next high-volume zone → look for a long
- Weak, messy break or runs straight into nearby volume → pass and wait

#### Core Principle
Only take trades where the path forward is clear. If the market doesn't offer space, don't force it.

---

### Playbook 2 — Level-to-Level Scalping
**Analyst:** Soul | **Level:** Advanced | **Style:** Scalping / Momentum | **Timeframes:** 1M, 3M, 15M

Momentum-based scalping using volume profile and confirmation entry models for precise level-to-level execution.

#### Trading Philosophy
Scalping strategy consisting mainly of executing trades from level to level. Most executions are catching momentum moves, not limit orders.

#### Strategy 1: Volume Profile
- Uses fixed volume profile from previous sessions
- Waits for price to reach previous session POIs
- Aligned with extra confluences: local flats on different timeframes, FVG, OB
- Reads OI and how it aligns with price movements for confident momentum scalps

#### Strategy 2: Confirmation Entry Model
- Based on a confirmation entry model used mainly on 1M and 3M timeframes
- 4 rules to be respected — delivers very well if followed

#### Additional Tools
- Harmonic patterns (Fibonacci-based) — normal and anti-harmonics
- Stochastic RSI
- Simple supply and demand concepts

---

### Playbook 3 — Multi-Timeframe Structure Trading
**Analyst:** Badillusion | **Level:** Advanced | **Style:** Swing / Intraday | **Timeframes:** 1H, 4H, 1D

Comprehensive framework combining macro analysis, HTF structure, and pattern-based entries with defined TP strategies.

#### Daily Macro Checks
Monitor: USDT.D, Stablecoin dominance, BTC, Total, Total3, BTC.D, combined dominance charts. These form the foundation of directional bias before any trade consideration.

#### Structure Analysis
- Mark levels on HTF (Yearly / 6-month / Monthly / Weekly / Daily) with fibs from bottom/top on wicks
- Check structure on Daily/4H and mark bullish/bearish MSS/BOS + OBs + identify ranges
- Form bias with main confluence of Stables and BTC

#### Intraday Entry Model
Based on:
- Trend lines (ascending & descending)
- S/R flips + patterns (Quasimodos, cup & handles, bull/bear flags)
- MSS/BOS + HTF levels marked by fibs
- Alignment with Order Blocks

#### Swing Entry Model
- Longs: Based on HTF levels, ideally 6-month / bi-6-month or higher (yearly/bi-yearly) if coin is strong
- Shorts: Inverse logic
- Swings mostly on top 100/200 coins
- Range trading works well until one side breaks

#### Take Profit Strategy
| Scenario | TP1 | TP2 | TP3 | TP4 | Runner |
|---|---|---|---|---|---|
| Standard | 35% | 25% | 25% | — | runner |
| Strong Trend | 20% | 20% | 35% | 15% | runner |

- SL set at 1RR in line with sizing
- After TP1: always move stops to entry

---

### Playbook 4 — Standard Entry Reversal Model
**Analyst:** Prestige | **Level:** Intermediate | **Style:** Reversal / Confirmation | **Timeframes:** 1M, 5M, 15M

Confirmation-based reversal pattern for optimal R:R entries at key POIs with defined dealing ranges.

#### Model Overview
Waits for confirmation rather than simply setting a limit on the higher timeframe. Powerful for achieving good Risk/Reward.

#### Entry Process
1. Price approaches POI → enter a lower timeframe (15m to 1m)
2. Price makes structure
3. A sweep of recent structure (A) into POI occurs
4. After rejection from POI, when a displacement (FVG) closes below the swing high/low of the structure that created the sweep → confirmed Shift (B)
5. The low-to-high (or vice versa) of the swing that created the Shift = the Dealing Range (D)

#### Execution
- Wait for price to retrace to **Discount** level (bullish) or **Premium** level (bearish) of the Dealing Range
- Enter opposite to displacement direction from the FVG, OB, or OTE (0.618 or 0.786 fib of dealing range)

#### Targets
- **TP near (E):** Recent swing high/low
- **TP far (F):** Higher timeframe swing high/low (for major POIs)

#### Valid POIs
Support/Resistance, Supply/Demand Zones, Daily/Weekly Highs/Lows, Monthly/Quarterly Opens/Closes

> Note: The sweep into shift can happen BEYOND the POI. Watch PA once POI is hit.

#### Why Confirmation Matters
Setting limits at POIs risks price cutting through or breaking out. Confirmation reduces this risk significantly.

---

### Playbook 5 — HTF Macro-Aligned Framework
**Analyst:** Scient | **Level:** Beginner | **Style:** Swing / Position | **Timeframes:** 1D, 3D, 1W

High timeframe, patience-focused approach built on institutional levels and macro alignment for asymmetric risk.

#### Trading Philosophy
Built on simplicity, patience, and asymmetric risk. Does NOT trade frequently. Does NOT chase price. Focuses on HTF levels and takes trades ONLY when risk is clearly defined and reward is meaningful.

#### HTF Levels
Everything starts with HTF Support & Resistance on 1D, 3D, 1W. These represent institutional decision points.
**No HTF level = no trade.**

#### Macro Bias (Required before entry)
| Indicator | Purpose |
|---|---|
| USDT.D | BTC macro direction |
| BTC.D | Altcoins vs BTC strength |
| TOTAL | Overall market structure |
| TOTAL2 | Altcoin & ETH health |

Does NOT take trades against macro bias.

#### Trend & Market Environment
- Higher highs & higher lows → bullish
- Lower highs & lower lows → bearish
- Range → range-based execution only
- Does not predict reversals — reacts to structure

#### Range Identification
Uses AMD (Accumulation → Manipulation → Distribution), PO3 (Power of Three), and ICT premium/discount concepts.
**Never enters in the middle of a range.**

---

### Playbook 6 — Harmonic Trading
**Analyst:** Grasady | **Level:** Advanced | **Style:** Harmonic / Multi-Timeframe | **Timeframes:** 1H, 4H, 1D, 1W

Macro harmonic patterns on higher timeframes combined with lower timeframe confirmation for precise entries.

#### Mapping Macro Harmonics
Start on any significant higher timeframe (daily or weekly) and map out important macro harmonics.

#### Forming Bias
Based on the levels created by these harmonics, form a bias and identify areas to look for positions.

#### Lower Timeframe Patterns
Once charts get near said areas, zoom in to 4H or 1H to look for lower timeframe patterns — these give the entry area.

#### Execution & Risk Management
- Wait for any reaction to form, or any liquidity to be taken to execute positions
- **Stop Loss:** Based on recently created support levels
- **Take Profits:** Around the 0.382 and 0.618 fib taken from the last significant price leg

---

*Generated from Unity Academy Dashboard — dashboard.unityacademy.io/strategies*
