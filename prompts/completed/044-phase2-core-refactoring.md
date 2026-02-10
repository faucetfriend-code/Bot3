<objective>
Execute Phase 2 of the trading bot refactoring plan: Core Engine Refactoring. Transform the Trading Bot from a monolithic do-everything component into a pure coordinator, enforce WebSocket-only price data, and implement component communication protocols. This establishes clean separation between coordination and execution layers.
</objective>

<context>
Phase 1 established the foundational components (Grid Lifecycle Manager, Market Regime Detector, Authoritative Risk Manager). Phase 2 focuses on the core engine - removing execution logic from the Trading Bot and making it a pure coordinator that orchestrates between specialized components.

Current state: Trading Bot handles coordination, execution, risk management, and state management. Target state: Trading Bot coordinates only, delegating execution to specialized components.

Key architectural changes:
- Trading Bot becomes pure coordinator (no execution)
- WebSocket cache becomes authoritative (no REST fallback for live trading)
- Component communication becomes event-driven or interface-based
- Clear separation between coordination and execution layers

Dependencies from Phase 1:
@trading_bot_v2/grid_lifecycle_manager.py - Grid state owner
@trading_bot_v2/market_regime.py - Regime assessment
@trading_bot_v2/risk_manager.py - Capital allocation authority

Current files to refactor:
@trading_bot_v2/trading_bot.py - Monolithic implementation to refactor
@trading_bot_v2/pacifica_ws_client.py - WebSocket price handling
@trading_bot_v2/strategy_manager.py - Strategy coordination
</context>

<requirements>
1. Refactor Trading Bot to pure coordinator role
   - Remove all direct order execution logic
   - Extract execution responsibilities to appropriate components
   - Implement component orchestration pattern
   - Add event-driven communication between components
   - Maintain backward compatibility during transition

2. Enforce WebSocket-only price data
   - Modify get_ticker_ws() to never fall back to REST API
   - Make WebSocket cache the single source of truth for live trading
   - Implement cache validation and freshness checks
   - Add backfill mechanism for historical data only
   - Monitor cache hit rates and WebSocket reliability

3. Implement component communication protocol
   - Define clean interfaces between components
   - Implement event system for loose coupling
   - Add request/response patterns for authoritative components
   - Create component registry for dependency injection
   - Establish clear boundaries and responsibilities

4. Update strategy execution flow
   - Modify strategy signals to route through coordinator
   - Integrate regime detection into signal validation
   - Connect strategy outputs to grid lifecycle management
   - Ensure risk approval required for all trades

5. Enhance error handling and monitoring
   - Add component health checks
   - Implement graceful degradation for component failures
   - Create comprehensive logging for component interactions
   - Add performance monitoring for critical paths
</requirements>

<implementation>
Follow these implementation principles:

**Coordinator Pattern:**
- Trading Bot should only coordinate, never execute
- Use dependency injection for component relationships
- Implement event-driven architecture for loose coupling
- Add circuit breakers for component failure scenarios

**WebSocket Authority:**
- Cache becomes the single source of truth for live prices
- REST API used only for backfill and historical data
- Implement cache invalidation and refresh mechanisms
- Add monitoring for cache performance and WebSocket health

**Communication Protocol:**
- Define interfaces for component interactions
- Use async patterns for non-blocking operations
- Implement proper error propagation and handling
- Add request/response correlation for tracking

**Testing Strategy:**
- Create integration tests for component orchestration
- Add mock components for isolated testing
- Verify event flows between components
- Test failure scenarios and graceful degradation

Avoid:
- Direct execution logic in coordinator components
- REST API usage for live trading decisions
- Tight coupling between components
- Synchronous operations that could block coordination
</implementation>

<output>
Create/modify the following files:

**New Files:**
- `./trading_bot_v2/component_interfaces.py` - Interface definitions for components
- `./trading_bot_v2/event_system.py` - Event-driven communication system
- `./trading_bot_v2/component_registry.py` - Dependency injection registry
- `./trading_bot_v2/tests/test_component_orchestration.py` - Integration tests
- `./trading_bot_v2/tests/test_websocket_authority.py` - WebSocket enforcement tests

**Modified Files:**
- `./trading_bot_v2/trading_bot.py` - Major refactoring to coordinator role
- `./trading_bot_v2/pacifica_ws_client.py` - Enforce WebSocket-only approach
- `./trading_bot_v2/strategy_manager.py` - Update for new coordination flow
- `./trading_bot_v2/api_server.py` - Integrate new component endpoints

**Configuration:**
- `./trading_bot_v2/config.py` - Add component configuration options
- `./trading_bot_v2/feature_flags.py` - Feature flags for gradual rollout

**Documentation:**
- `./docs/architecture_phase2.md` - Document coordinator pattern and communication protocols
- Update `./README.md` with new architectural patterns
</output>

<verification>
Before declaring Phase 2 complete, verify:

1. **Coordinator Purity**: Trading Bot contains no direct execution logic
2. **WebSocket Authority**: get_ticker_ws() never falls back to REST for live prices
3. **Component Communication**: Clean interfaces established between all components
4. **Event System**: Event-driven communication working between components
5. **Backward Compatibility**: Existing functionality preserved with feature flags
6. **Integration Tests**: Component orchestration tests pass
7. **Performance**: No significant degradation in response times

Run verification tests:
- `python -m pytest trading_bot_v2/tests/test_component_orchestration.py -v`
- `python -m pytest trading_bot_v2/tests/test_websocket_authority.py -v`
- `python test_integration.py` (verify end-to-end flows)
- Manual testing: Confirm WebSocket prices used exclusively
</verification>

<success_criteria>
Phase 2 is successful when:
- [ ] Trading Bot acts as pure coordinator with no execution logic
- [ ] WebSocket cache is authoritative for all live price data
- [ ] Component communication protocol established and tested
- [ ] Event-driven architecture implemented for loose coupling
- [ ] Strategy execution flows through proper coordination
- [ ] All integration tests pass
- [ ] No REST API usage for live trading decisions
- [ ] Performance maintained or improved
- [ ] Clean interfaces ready for Phase 3 integration
</success_criteria></content>
<parameter name="filePath">prompts/044-phase2-core-refactoring.md

---
Completed at: 2026-01-15T21:48:11.338Z
