<objective>
Execute Phase 1 of the trading bot refactoring plan: Foundation. Extract the Grid Lifecycle Manager, implement the Market Regime Detector, and restructure the Risk Manager as an authoritative component. This establishes the core architectural foundation for the layered trading system.
</objective>

<context>
This is the first phase of a comprehensive refactoring to transform the monolithic trading bot into a clean, layered architecture. The current codebase has a Trading Bot that handles everything - coordination, execution, risk, and state management. Phase 1 creates the foundational components that will own their respective domains.

Key architectural goals:
- Grid Lifecycle Manager: Single owner of grid state per symbol
- Market Regime Detector: Independent market condition assessment
- Risk Manager: Authoritative capital allocation gatekeeper

Project structure: Trading bot in trading_bot_v2/ with SQLite database, FastAPI server, WebSocket client, and various trading components.

@trading_bot_v2/trading_bot.py - Current monolithic implementation
@trading_bot_v2/risk_manager.py - Current risk checks
@trading_bot_v2/database.py - Database operations
@trading_bot_v2/api_server.py - FastAPI endpoints
</context>

<requirements>
1. Create GridLifecycleManager class in new file trading_bot_v2/grid_lifecycle_manager.py
   - Extract all grid creation, modification, and exit logic from trading_bot.py
   - Implement single responsibility: own grid state per symbol
   - Add state persistence to database with proper schema
   - Include grid status tracking (active, paused, closed)
   - Provide clean interface for grid operations

2. Create MarketRegimeDetector class in new file trading_bot_v2/market_regime.py
   - Implement independent market condition monitoring
   - Add volatility, trend, and liquidity metrics calculation
   - Define regime classification (bull, bear, sideways, volatile)
   - Create trading permission system per regime type
   - Include emergency stop capabilities

3. Restructure RiskManager in risk_manager.py to be authoritative
   - Change from advisory to mandatory approval system
   - Add explicit capital allocation tracking per symbol
   - Implement position size limits and exposure controls
   - Create emergency stop functionality
   - Require explicit approval for all capital requests

4. Update database schema to support new state tracking
   - Add tables for grid state persistence
   - Include regime detection data storage
   - Ensure backward compatibility with existing data

5. Maintain backward compatibility during transition
   - Add feature flags to enable/disable new components
   - Preserve existing API contracts
   - Allow gradual rollout of new architecture
</requirements>

<implementation>
Follow these architectural principles:

**Component Design:**
- Each component should have single responsibility and clear ownership
- Use dependency injection for loose coupling
- Implement proper error handling and logging
- Add comprehensive type hints and docstrings

**Database Integration:**
- Use existing DatabaseManager for all persistence
- Create migration scripts for schema changes
- Implement proper transaction handling
- Add data validation and integrity checks

**Testing Approach:**
- Create unit tests for each new component
- Add integration tests for component interactions
- Include mock implementations for isolated testing
- Verify backward compatibility

**Code Quality:**
- Follow existing code style and patterns
- Add comprehensive error handling
- Include logging for debugging and monitoring
- Use async/await for I/O operations where appropriate

Avoid:
- Breaking existing functionality without feature flags
- Tight coupling between components
- Direct database access outside DatabaseManager
- Synchronous operations that could block the event loop
</implementation>

<output>
Create/modify the following files:

**New Files:**
- `./trading_bot_v2/grid_lifecycle_manager.py` - GridLifecycleManager class with full implementation
- `./trading_bot_v2/market_regime.py` - MarketRegimeDetector class with regime analysis
- `./trading_bot_v2/tests/test_grid_lifecycle.py` - Unit tests for GridLifecycleManager
- `./trading_bot_v2/tests/test_market_regime.py` - Unit tests for MarketRegimeDetector

**Modified Files:**
- `./trading_bot_v2/risk_manager.py` - Restructure to authoritative model
- `./trading_bot_v2/database.py` - Add schema for new components
- `./trading_bot_v2/trading_bot.py` - Remove extracted grid logic (keep for Phase 2)
- `./trading_bot_v2/api_server.py` - Add endpoints for new components if needed

**Database:**
- Create migration script `./trading_bot_v2/migrations/001_refactor_foundation.sql`
- Update schema.sql with new tables

**Documentation:**
- Update `./README.md` with new component descriptions
- Create `./docs/architecture_phase1.md` documenting the new foundation
</output>

<verification>
Before declaring Phase 1 complete, verify:

1. **Component Isolation**: Each new component can be instantiated and tested independently
2. **Database Schema**: New tables created and migrations run successfully
3. **Backward Compatibility**: Existing functionality works with feature flags disabled
4. **API Contracts**: No breaking changes to existing endpoints
5. **Test Coverage**: Unit tests pass for all new components
6. **Integration**: Components can communicate through defined interfaces

Run comprehensive tests:
- `python -m pytest trading_bot_v2/tests/test_grid_lifecycle.py -v`
- `python -m pytest trading_bot_v2/tests/test_market_regime.py -v`
- `python test_integration.py` (existing integration tests still pass)
</verification>

<success_criteria>
Phase 1 is successful when:
- [ ] GridLifecycleManager fully extracts grid logic from Trading Bot
- [ ] MarketRegimeDetector provides independent regime classification
- [ ] RiskManager requires explicit approval for capital allocation
- [ ] Database schema supports all new state tracking
- [ ] Feature flags allow gradual rollout
- [ ] All existing tests pass
- [ ] New unit tests achieve >80% coverage for new components
- [ ] No performance degradation (>5% increase in response times)
- [ ] Clean component interfaces established for Phase 2 integration
</success_criteria></content>
<parameter name="filePath">prompts/043-phase1-foundation.md

---
Completed at: 2026-01-15T21:39:43.403Z
