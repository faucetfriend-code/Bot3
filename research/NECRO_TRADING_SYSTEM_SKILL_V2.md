---
name: necro-trading-system
description: "Reversal-focused cryptocurrency trading system using confluence-based decision framework with 5 custom indicators. Catches exhaustion and momentum shifts at key supply/demand zones."
version: 2.0
last_updated: 2026-01-21
---

# NECRO TRADING SYSTEM

## PRIMARY DIRECTIVE
**This system is designed to catch REVERSALS and EXHAUSTION, not follow trends.** Let price come to you at key zones. Define risk before entry. Skip only when no clear zone or confluence <80.

**Core Philosophy**: When lower timeframes diverge from higher timeframes at supply/demand zones, that IS the trading opportunity - you're catching the reversal early.

---

## QUICK DECISION FRAMEWORK

### Before ANY Analysis:
1. **4H Oracle Strength** â†’ Sets CONTEXT (what we're reversing against, not what we follow)
2. **Lower TF Oracle Strength** â†’ Shows MOMENTUM SHIFT (the actual trade signal)
3. **Oracle Strength Momentum** â†’ Track RATE OF CHANGE (e.g., 4.6 â†’ 0.84 = 82% momentum loss = reversal)
4. **Identify Zone** â†’ Demand (for longs) or Supply (for shorts) via Oracle AIO
5. **Calculate Confluence** â†’ Score using all 5 indicators (only ADD points, never subtract to negative)
6. **Decision Threshold** â†’ 80+ = Trade | <80 = SKIP

### Confluence Thresholds:
- **95+ points** = HIGH conviction (full position)
- **85-94 points** = NORMAL conviction (standard position)
- **80-84 points** = LOW conviction (reduced 50-75%)
- **<80 points** = SKIP (no trade)

### CRITICAL UNDERSTANDING:
**4H bullish + 1H/30M bearish = SHORT opportunity (reversal)**
**4H bearish + 1H/30M bullish = LONG opportunity (reversal)**
**This divergence is THE SIGNAL, not a reason to skip!**

The system catches reversals BEFORE 4H confirms - that's the edge!

---

## THE FIVE INDICATORS

### 1. ORACLE STRENGTH (OS) - Primary Momentum Indicator
**What it shows**: Directional momentum and strength

**Key Levels**:
- **Green above plots** = Strong bullish
- **Red below plots** = Strong bearish
- **Between plots** = Transition zone
- **>3.0** = Extreme momentum (watch for exhaustion)
- **<-3.0** = Extreme bearish (watch for reversal)

**Timeframe Roles** (NOT priority for alignment!):
- **4H** = Context (what trend we're reversing against)
- **1H** = Momentum shift signal (primary reversal indicator)
- **30M** = Confirmation of shift
- **15M** = Micro-timing for entries

**REVERSAL LOGIC** (Most Important):
When analyzing for reversals (primary strategy):
- 4H provides CONTEXT (strong trend to reverse)
- 1H/30M show MOMENTUM SHIFT (the actual signal)
- Look for divergence between 4H and lower TFs

**CONTINUATION LOGIC** (Secondary):
When all timeframes align:
- HIGH conviction if 4H + 1H + 30M same direction
- This is rarer but indicates strong trend continuation

**ORACLE STRENGTH MOMENTUM** (Critical Addition):
Track the RATE OF CHANGE on Oracle Strength:

Examples:
- 1H OS: 4.6 â†’ 0.84 = 82% momentum loss = Strong reversal signal
- 1H OS: -2.5 â†’ 0.3 = Bullish reversal forming
- 1H OS: 0.5 â†’ 0.6 = Minimal change = No signal

**How to calculate:**
```
Momentum Loss % = ((Previous - Current) / Previous) * 100

Example: (4.6 - 0.84) / 4.6 * 100 = 82% momentum loss
```

**Scoring Impact:**
- >70% momentum loss = +10 pts (strong reversal)
- 50-70% momentum loss = +7 pts (moderate reversal)
- 30-50% momentum loss = +5 pts (weak reversal)
- <30% momentum loss = +0 pts (no reversal signal)

---

### 2. AIO TREND - Trend Confirmation
**What it shows**: Multi-timeframe trend direction

**Signals**:
- **All green** = Strong bullish trend (for continuations)
- **All red** = Strong bearish trend (for continuations)
- **Mixed** = Transition/reversal zone (opportunity!)

**Use**: 
- For CONTINUATIONS: Want all aligned
- For REVERSALS: Mixed signals at zone = good (shows shift happening)

---

### 3. ORACLE AIO - Key Levels & Market Structure
**What it shows**: Supply/demand zones, key levels, structure signals

**Features**:
- **Supply & Demand Zones** â†’ Primary entry zones
- **Key Period Levels** â†’ PMH, PWH, PWL, PDL (use for TPs)
- **BOS (Break of Structure)** â†’ Continuation signal
- **MSS (Market Structure Shift)** â†’ Reversal confirmation

**Use**: 
- Identify where to place limit orders (supply/demand zones)
- Set TP targets at key period levels
- BOS confirms trend continuation (less useful for reversals)
- MSS confirms reversal (very useful for our strategy!)

---

### 4. MASTER SNIPER - Visual Bias Confirmation
**What it shows**: Candle color system + buy/sell signals

**Candle Colors**:
- **Green** = Bullish bias/momentum
- **Red** = Bearish bias/momentum  
- **Grey** = TRANSITION (directional shift occurring)

**Signals**:
- Buy/sell markers provide additional confirmation
- NOT primary entry trigger

**Critical Transitions** (REVERSAL SIGNALS):
- **Green â†’ Grey** = Bullish weakening â†’ Expect RED next (SHORT signal)
- **Red â†’ Grey** = Bearish exhausting â†’ Expect GREEN next (LONG signal)
- **Grey appearing** = Momentum shift in progress (NOT automatic skip!)

**IMPORTANT**: Grey candles are TRANSITION signals, not skip signals!
- If recent candles green â†’ grey appearing â†’ expect red (SHORT)
- If recent candles red â†’ grey appearing â†’ expect green (LONG)

---

### 5. MASTER CODE - Momentum Confirmation
**What it shows**: Works with Master Sniper for dual confirmation

**Integration**:
- Both green = Strong bullish (for LONG continuations)
- Both red = Strong bearish (for SHORT continuations)
- Misalignment = Transition happening (reversal opportunity!)

**Transition Analysis** (For Reversals):
- **After pump**: Green â†’ Grey â†’ expect RED (SHORT signal)
- **After dump**: Red â†’ Grey â†’ expect GREEN (LONG signal)
- Grey is a WARNING of shift, not a skip reason

---

## CONFLUENCE SCORING SYSTEM (100 Point Scale)

### SCORING PHILOSOPHY:
- **Start at 0 points**
- **Only ADD points** for things that support your trade direction
- **Never go below 0** (no negative confluence scores)
- **Minimum 80 points required** to execute

### FOR SHORT SETUPS (Reversal Example):

**Oracle Strength (40 points max)**:
- 4H bullish (context): +0 pts (provides reversal context but not aligned)
- 1H bearish/weakening: +15 pts (primary signal)
- 30M bearish: +15 pts (confirmation)
- 15M bearish: +10 pts (micro confirmation)

**Oracle Strength Momentum (10 points max)** [NEW]:
- >70% momentum loss: +10 pts
- 50-70% momentum loss: +7 pts
- 30-50% momentum loss: +5 pts
- <30% momentum loss: +0 pts

**AIO Trend (10 points max)**:
- All TFs aligned bearish: +10 pts
- Majority (2/3) aligned bearish: +7 pts
- Mixed (shows transition): +3 pts

**Oracle AIO (20 points max)**:
- At supply zone: +15 pts
- At key period level: +5 pts
- MSS reversal signal: +10 pts (bonus for reversals!)
- BOS continuation: +0 pts (not relevant for reversal)

**Master Sniper + Code (15 points max)**:
- Lower TF color aligned with direction: +15 pts
- Buy/sell signal present: +5 pts
- Grey candles transitioning: +7 pts (reversal signal!)
- 4H color opposite: +0 pts (expected for reversal)

### FOR LONG SETUPS (Reversal Example):

**Oracle Strength (40 points max)**:
- 4H bearish (context): +0 pts (provides reversal context)
- 1H bullish/strengthening: +15 pts (primary signal)
- 30M bullish: +15 pts (confirmation)
- 15M bullish: +10 pts (micro confirmation)

[Same structure for other indicators, adjusted for LONG direction]

### ADDITIONAL FACTORS:

**Chart Pattern & Zones (20 points max)**:
- Clear demand zone (long): +15 pts
- Clear supply zone (short): +15 pts
- At key Oracle AIO level: +5 pts
- Pattern completion: +5 pts

**Risk/Reward (20 points max)**:
- R/R >1:3: +20 pts
- R/R >1:2: +15 pts
- R/R >1:1.5: +10 pts
- R/R >1:1: +5 pts

**Post-Event Context (10 points max)** [REVISED]:
- Post-pump + lower TF bearish: +10 pts (SHORT setup!)
- Post-dump + lower TF bullish: +10 pts (LONG setup!)
- Post-pump/dump but no TF shift: +0 pts (wait for signal)

**IMPORTANT**: There are NO negative deductions in scoring!
- If something doesn't support the trade, simply don't add points
- Minimum score is always 0, never negative

---

## TRADE STRUCTURE & EXECUTION

### ENTRY TYPES (Use What Fits The Setup!)

**Remove arbitrary percentages** - each coin and situation is different. Choose based on:
- Current price relative to zone
- Volatility of the asset
- Conviction level
- Risk management needs

**1. CMP (Current Market Price)**:
When to use:
- Strong reversal signal happening NOW
- Oracle Strength momentum shift confirmed
- At key zone with rejection visible
- Risk/reward still favorable at current price

Example: HANA at $0.0216, momentum dropped 82%, at supply zone = CMP SHORT makes sense

**2. LIMIT (Single Entry)**:
When to use:
- Zone clearly defined
- Price slightly away from optimal entry
- Want to wait for better price
- Clear invalidation level

**3. SCALED_LIMIT (Multiple Entries)**:
When to use:
- Zone spans 2-4% range
- Want to average into position
- Uncertain of exact reversal point
- Reduce risk of single bad entry

Split examples:
- 40/60 (conservative first, aggressive second)
- 30/30/40 (three-tier scaling)
- 50/50 (equal distribution)

**4. MIX TECHNIQUES**:
Example: 50% CMP + 50% Limit deeper
- Get exposure immediately
- Add if goes deeper into zone
- Reduces FOMO while maintaining discipline

---

### POSITION SIZING (Flexible)

**HIGH conviction (95+)**: 
- 100% standard position
- Can go up to 150% if R/R exceptional

**NORMAL conviction (85-94)**: 
- 100% standard position
- Standard approach

**LOW conviction (80-84)**: 
- 50-75% standard position
- Reduced exposure for lower confidence

**Standard risk**: 1-2% of portfolio per trade
**High conviction**: Up to 3% max
**Volatile altcoins**: Start smaller, can scale if thesis confirms

---

### STOP LOSS PLACEMENT

**Zone-Based (Preferred)**:
- **Demand longs**: Stop 1-2% below demand zone
- **Supply shorts**: Stop 1-2% above supply zone
- **Volatile altcoins**: 2-4% stops (learned from experience)

**Invalidation Logic**:
- Stop = zone failed
- If price breaks through zone, thesis invalid
- Better to take small loss than hope for recovery

**CRITICAL**: NEVER move stop against your position

---

### TAKE PROFIT STRATEGY

**Use Oracle AIO Key Levels**:
- PMH (Previous Month High)
- PWH (Previous Week High)
- PDL (Previous Day Low)
- PWL (Previous Week Low)

**Scaled Exits (Preferred)**:
- **TP1**: First key level (50% position)
- **TP2**: Next key level (30-40% position)
- **TP3**: Extended target/runner (10-20% position)

**For Reversals**:
- TP1: First obvious demand/supply zone
- TP2: Next major level
- Trail stops on runners

**Full Exit When**:
- Oracle Strength reverses back
- Master Sniper/Code flip colors
- MSS signal appears against you
- Key level rejection

---

## ANALYSIS WORKFLOW (Reversal-Focused)

### STEP 1: Context Check (2 min)

**4H Oracle Strength**:
- [ ] Current reading? (e.g., +0.43 = weak bullish)
- [ ] Position relative to plots?
- [ ] Sets CONTEXT: "What trend am I potentially reversing?"

### STEP 2: Momentum Shift Detection (3 min)

**1H Oracle Strength** (Most Important for Reversals):
- [ ] Current reading?
- [ ] Previous reading? (e.g., was 4.6, now 0.84)
- [ ] Momentum change: (Previous - Current) / Previous * 100
- [ ] >50% change = Strong signal!

**30M Oracle Strength**:
- [ ] Confirms 1H shift?
- [ ] Already crossed into opposite zone?

**Key Question**: "Is lower TF momentum diverging from 4H?"
- If YES = Potential reversal setup
- If NO = Either continuation or no trade

### STEP 3: Zone Identification (2 min)

**Oracle AIO Check**:
- [ ] Where are supply/demand zones?
- [ ] Current price relative to zones?
- [ ] AT zone? (can enter now)
- [ ] NEAR zone? (wait for better entry)
- [ ] FAR from zone? (skip or wait)

**Key Period Levels**:
- [ ] Note PMH, PWH, PDL, PWL for TPs

### STEP 4: Candle Color Analysis (1 min)

**Master Sniper + Code**:
- [ ] Current colors on each TF?
- [ ] Any GREY transitions? (shift happening!)
- [ ] Recent color history?
- [ ] If green â†’ grey â†’ expect red (SHORT signal)
- [ ] If red â†’ grey â†’ expect green (LONG signal)

### STEP 5: Confluence Scoring (3 min)

**Score Sheet** (for SHORT example):
```
Oracle Strength:
- 4H: +0 pts (bullish context, expected for reversal)
- 1H: +15 pts (weakening/bearish)
- 30M: +15 pts (bearish)
Subtotal: 30/40

OS Momentum:
- 1H momentum loss: +10 pts (>70% loss)
Subtotal: 10/10

AIO Trend:
- Lower TFs bearish: +7 pts
Subtotal: 7/10

Oracle AIO:
- At supply: +15 pts
- Key level: +5 pts
Subtotal: 20/20

Master Sniper/Code:
- Lower TF RED: +15 pts
Subtotal: 15/15

Zones & Patterns:
- Supply zone: +15 pts
- Post-pump: +10 pts (with bearish TFs = SHORT signal!)
Subtotal: 25/30

Risk/Reward:
- R/R 1:5: +20 pts
Subtotal: 20/20

TOTAL: 30+10+7+20+15+25+20 = 127/140 possible
```

**Result**: 127 points = WAY ABOVE 80 threshold = TRADEABLE!

### STEP 6: Trade Structure Decision (2 min)

Based on confluence score and setup:

**If 80-84 points** (LOW):
- 50-75% position
- Tighter stops
- Conservative approach

**If 85-94 points** (NORMAL):
- Full position
- Standard stops
- Can use CMP if at zone

**If 95+ points** (HIGH):
- Full position
- Can even oversize slightly
- High confidence in setup

**Entry Type Selection**:
- At zone + strong signal = CMP works
- Near zone = LIMIT
- Wide zone = SCALED_LIMIT
- Mix if want immediate exposure + deeper adds

### STEP 7: Execute & Monitor (2 min)

- [ ] Set entry orders
- [ ] Set stop loss (mandatory!)
- [ ] Set TP orders
- [ ] Log trade in system
- [ ] Monitor Oracle Strength for reversal back

---

## POST-PUMP/DUMP SCENARIOS (Reversal Opportunities!)

### POST-PUMP (e.g., +154% move)

**What to look for**:
1. Price at/near supply zone
2. 1H Oracle Strength declining rapidly (e.g., 4.6 â†’ 0.84)
3. Master Sniper/Code: Green â†’ Grey or RED
4. Volume declining

**Signal**: Post-pump + lower TF momentum shift = SHORT setup!
**NOT**: "Post-pump = skip" âŒ
**YES**: "Post-pump + exhaustion signs = opportunity" âœ…

**Scoring**:
- Post-pump context: +0 pts (neutral)
- Lower TF bearish momentum: +15 pts per TF
- Momentum loss >70%: +10 pts
- At supply zone: +15 pts
- **Total from post-pump context: 40+ points!**

### POST-DUMP (Major Selloff)

**What to look for**:
1. Price at/near demand zone
2. 1H Oracle Strength increasing (e.g., -3.2 â†’ -0.5)
3. Master Sniper/Code: Red â†’ Grey or GREEN
4. Volume declining (capitulation)

**Signal**: Post-dump + lower TF bullish shift = LONG setup!

---

## COMMON TRADING SCENARIOS

### SCENARIO 1: Strong Reversal (Like HANA)

**Setup**:
- 4H: +0.43 (weak bullish - context)
- 1H: 4.6 â†’ 0.84 (82% momentum loss!)
- 30M: -1.34 (bearish - confirmation)
- Price: $0.0216 at supply $0.0200-$0.0210
- Master Sniper: Some grey/red appearing

**Analysis**:
- Context: 4H bullish (reversing against this)
- Signal: 1H massive momentum loss
- Confirmation: 30M already bearish
- Zone: AT supply zone
- Candles: Transitioning bearish

**Confluence**:
- Oracle Strength: 30/40 (lower TFs)
- OS Momentum: 10/10 (82% loss)
- Oracle AIO: 20/20 (at supply + key level)
- Zones: 15/20 (clear supply + post-pump)
- R/R: 15/20 (1:5 to 1:12)
- **Total: 90/100 = NORMAL conviction SHORT** âœ…

**Result**: SHORT at $0.0216, stop $0.0240, TP1 $0.0190
**Outcome**: Up 16%, TP1 hit! âœ…

### SCENARIO 2: Weak Setup (Actual Skip)

**Setup**:
- 4H: +2.1 (strong bullish)
- 1H: +1.8 (still bullish, barely changed)
- 30M: +1.5 (bullish)
- Price: $0.0180 (between zones)
- Master Sniper: Green everywhere

**Analysis**:
- Context: 4H strong bullish
- Signal: No momentum shift on lower TFs
- Zone: Not at any key level
- Candles: All green (no transition)

**Confluence**:
- Oracle Strength: 0/40 (all bullish, no reversal)
- OS Momentum: 0/10 (no significant change)
- Oracle AIO: 0/20 (not at zone)
- Zones: 0/20 (no clear pattern)
- **Total: 0/100 = SKIP** âŒ

**This is a real skip** - no zone, no momentum shift, no trade.

---

## KEY LESSONS INTEGRATED

### Lesson #1: Divergence = Opportunity
**Old thinking**: "4H and 1H don't align = skip"
**Correct thinking**: "4H and 1H diverge at zone = reversal trade!"

The system is DESIGNED to catch these divergences early.

### Lesson #2: Oracle Strength Momentum Matters
**Track the rate of change**:
- 4.6 â†’ 0.84 = 82% loss = Strong reversal
- 0.5 â†’ 0.6 = 20% gain = Weak signal
- Monitor this actively!

### Lesson #3: Grey Candles = Transition Signal
**Old**: Grey = skip
**Correct**: Grey = momentum shift occurring
- Green â†’ Grey = Expect RED next (SHORT)
- Red â†’ Grey = Expect GREEN next (LONG)

### Lesson #4: Post-Pump/Dump = Check Lower TFs
**Not**: "Parabolic move = skip"
**Correct**: "Parabolic move + lower TF shift = high-probability reversal"

### Lesson #5: No Negative Confluence
**Scoring**: Start at 0, only ADD points
- Can't have negative confluence
- Just don't add points for misaligned factors
- Minimum score = 0, not negative

### Lesson #6: Entry Type Flexibility
**Not**: "80% must be LIMIT orders"
**Correct**: Use what fits the setup
- At zone + strong signal = CMP works
- Can mix CMP + LIMIT
- Can use SCALED_LIMIT
- Adapt to situation!

---

## CRITICAL REMINDERS

1. **This is a REVERSAL system** - divergences are opportunities
2. **4H provides CONTEXT**, not direction to follow
3. **1H/30M provide SIGNALS** for momentum shifts
4. **Oracle Strength momentum** (rate of change) is critical
5. **Grey candles** = transition, not skip
6. **Post-pump/dump** + TF shift = trade opportunity
7. **Confluence** never goes negative
8. **Entry types** - use what fits, be flexible
9. **80+ confluence** = tradeable (regardless of 4H alignment)
10. **Skip** only when: no zone, no momentum shift, or <80 confluence

---

## BEFORE EVERY ANALYSIS, ASK:

1. âœ… **Where is the 4H Oracle Strength?** (sets context)
2. âœ… **Is 1H/30M Oracle Strength diverging?** (the signal)
3. âœ… **What's the Oracle Strength momentum change?** (>50% = strong)
4. âœ… **Are we at a supply/demand zone?** (entry point)
5. âœ… **Are candles transitioning?** (grey = shift happening)
6. âœ… **Confluence score?** (>80 = trade)
7. âœ… **Risk/reward?** (use Oracle AIO levels for TPs)
8. âœ… **Entry type?** (CMP/LIMIT/SCALED - what fits?)

---

**END OF SKILL - Version 2.0**
**Updated: 2026-01-21**
**Focus: REVERSALS and MOMENTUM SHIFTS**
