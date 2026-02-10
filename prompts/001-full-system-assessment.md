<objective>
Conduct a comprehensive assessment of the Pacifica trading bot's systems to determine what is working correctly, what has issues, and what needs improvement. This assessment will give the project owner a clear picture of the current state and prioritized action items.

The end goal is a detailed status report that can be used to prioritize development efforts and understand the overall health of the trading system.
</objective>

<context>
This is a multi-strategy cryptocurrency trading bot for Pacifica.fi perpetual futures. The bot implements:
- Regime-based strategy selection (Mean Reversion, MA Crossover, Grid Trading, Liquidation Capture)
- Kelly Criterion position sizing
- Risk management with circuit breakers
- Web interface for monitoring
- WebSocket real-time data feeds

Recent changes (Jan 2026) loosened thresholds for more trade generation:
- ADX thresholds adjusted (trending: 30, ranging: 25)
- Grid settings tuned (8 levels, 0.4x ATR spacing, 45% min confidence)
- Cooldowns reduced by ~50%
- Risk allowances increased (5% per trade, 15% exposure, 15% grid capital)
- Trading loop reduced from 120s to 30s

Read CLAUDE.md for full project architecture and conventions.
</context>

<research>
Thoroughly examine all system components by reading and analyzing:

1. **Core Trading Logic**
   - @trading_bot_v2/trading_bot.py - Main bot loop, signal execution
   - @trading_bot_v2/strategy_manager.py - Strategy orchestration, conflict resolution
   - @trading_bot_v2/market_regime.py - Regime detection (ADX-based)
   - @trading_bot_v2/multi_timeframe_fetcher.py - Candle data fetching

2. **Strategies**
   - @trading_bot_v2/strategies/mean_reversion.py
   - @trading_bot_v2/strategies/ma_crossover.py
   - @trading_bot_v2/strategies/grid_trading.py
   - @trading_bot_v2/strategies/liquidation_capture.py

3. **Risk & Position Management**
   - @trading_bot_v2/risk_manager.py - Centralized risk control
   - @trading_bot_v2/kelly_position_sizer.py - Kelly Criterion sizing

4. **Infrastructure**
   - @trading_bot_v2/api_server.py - FastAPI web server, endpoints
   - @trading_bot_v2/hub_system.py - WebSocket hub
   - @core_logic/pacifica_client.py - Exchange API wrapper
   - @trading_bot_v2/database.py - SQLite persistence

5. **Interface**
   - @trading_bot_v2/interface.html - Web dashboard

6. **Configuration**
   - @trading_bot_v2/config.py - Environment configuration
   - Check for .env file if present
</research>

<analysis_requirements>
For each system component, evaluate:

1. **Functionality Status**
   - Does it work as intended?
   - Are there any errors in the code logic?
   - Are there missing error handlers or edge cases?

2. **Integration Health**
   - Does it communicate correctly with other components?
   - Are data formats consistent across boundaries?
   - Are there any race conditions or timing issues?

3. **Configuration Correctness**
   - Are parameters sensible for the use case?
   - Are there any conflicting settings?
   - Are defaults appropriate?

4. **Code Quality**
   - Are there any obvious bugs?
   - Is error handling adequate?
   - Are there any deprecated patterns?

Run diagnostic commands where helpful:
!python -c "import trading_bot_v2.config; print('Config loads OK')"
!python -c "from trading_bot_v2.market_regime import MarketRegimeDetector; print('Regime detector OK')"
</analysis_requirements>

<output_format>
Create a comprehensive assessment report saved to: `./assessment/system-status-report.md`

Structure the report as follows:

## Executive Summary
- Overall system health score (1-10)
- Count of critical/high/medium/low issues
- Top 3 priorities

## System Status Matrix

| Component | Status | Health | Issues Found | Notes |
|-----------|--------|--------|--------------|-------|
| Trading Bot Core | Working/Partial/Broken | Green/Yellow/Red | # | Brief note |
| Strategy Manager | ... | ... | ... | ... |
| Market Regime | ... | ... | ... | ... |
| Mean Reversion | ... | ... | ... | ... |
| MA Crossover | ... | ... | ... | ... |
| Grid Trading | ... | ... | ... | ... |
| Liquidation Capture | ... | ... | ... | ... |
| Risk Manager | ... | ... | ... | ... |
| Kelly Position Sizer | ... | ... | ... | ... |
| Multi-TF Fetcher | ... | ... | ... | ... |
| API Server | ... | ... | ... | ... |
| WebSocket Hub | ... | ... | ... | ... |
| Pacifica Client | ... | ... | ... | ... |
| Database | ... | ... | ... | ... |
| Web Interface | ... | ... | ... | ... |

## Detailed Issue Breakdown

For each issue found:

### [CRITICAL/HIGH/MEDIUM/LOW] Issue Title
- **Component**: Which file/system
- **Location**: File:line_number
- **Description**: What is the problem
- **Impact**: What does this cause
- **Root Cause**: Why this happens
- **Recommended Fix**: How to fix it
- **Effort**: Quick (< 30 min) / Medium (1-4 hours) / Large (1+ day)

## Data Flow Analysis
- Document how data flows through the system
- Identify any bottlenecks or failure points
- Note any format mismatches

## Recent Changes Assessment
- Evaluate the Jan 2026 threshold changes
- Are the new settings appropriate?
- Any unintended consequences?

## Prioritized Action Items

### Critical (Fix Immediately)
1. [Issue] - [Brief description] - [Effort]

### High Priority (Fix This Week)
1. [Issue] - [Brief description] - [Effort]

### Medium Priority (Fix When Possible)
1. [Issue] - [Brief description] - [Effort]

### Low Priority (Nice to Have)
1. [Issue] - [Brief description] - [Effort]

## Recommendations
- Strategic recommendations for improvement
- Suggested monitoring/alerting additions
- Testing recommendations
</output_format>

<verification>
Before completing, verify:
- [ ] All 15 components in the status matrix have been evaluated
- [ ] Each issue has a file:line reference where applicable
- [ ] Priority assignments are justified
- [ ] Fix recommendations are actionable
- [ ] No component was skipped or given placeholder status
- [ ] Data flow between components was traced
- [ ] Recent threshold changes were specifically evaluated
</verification>

<success_criteria>
- Complete status matrix with all components assessed
- At least 5 issues identified (or explicit confirmation system is healthy)
- Each issue includes location, impact, and fix recommendation
- Prioritized action list is clear and actionable
- Report can be understood by someone unfamiliar with the codebase
</success_criteria>
