<objective>
Execute Phase 3 of the trading bot refactoring plan: Integration & Testing. Wire up the new architecture components, implement comprehensive testing, and add monitoring capabilities. This completes the transformation from monolithic to layered architecture with full validation.
</objective>

<context>
Phases 1 and 2 established the foundational and core components with proper separation of concerns. Phase 3 focuses on integration - connecting all components, comprehensive testing, and production readiness. The goal is a fully functional, well-tested, layered trading system.

Current state: Individual components created and refactored with clean interfaces. Target state: Fully integrated system with comprehensive testing and monitoring.

Key integration points:
- Component wiring through dependency injection
- End-to-end testing of trading flows
- Monitoring and observability
- Production deployment readiness

Dependencies from previous phases:
@trading_bot_v2/grid_lifecycle_manager.py - Grid state management
@trading_bot_v2/market_regime.py - Regime detection
@trading_bot_v2/risk_manager.py - Authoritative risk control
@trading_bot_v2/component_interfaces.py - Communication protocols
@trading_bot_v2/event_system.py - Event-driven architecture
@trading_bot_v2/trading_bot.py - Refactored coordinator

Integration targets:
@trading_bot_v2/api_server.py - Component exposure and monitoring
@trading_bot_v2/database.py - Full schema and migration support
</context>

<requirements>
1. Integrate new components into the application startup
   - Update api_server.py for proper component initialization
   - Implement dependency injection registry usage
   - Wire up event system for component communication
   - Add component health checks and startup validation

2. Implement comprehensive testing suite
   - Create unit tests for all new and modified components
   - Add integration tests for end-to-end trading flows
   - Implement performance tests for critical paths
   - Create chaos testing for failure scenarios
   - Achieve >80% test coverage across all components

3. Add monitoring and observability
   - Implement component health metrics
   - Add distributed tracing for component interactions
   - Create dashboard endpoints for system status
   - Implement alerting for component failures
   - Add performance monitoring and profiling

4. Validate end-to-end trading flows
   - Test complete signal → approval → execution → persistence cycle
   - Verify regime detection integration
   - Confirm WebSocket authority in live trading
   - Test emergency stop and circuit breaker functionality

5. Implement production readiness features
   - Add graceful shutdown handling
   - Implement configuration management for components
   - Create deployment and rollback procedures
   - Add comprehensive logging and error tracking
</requirements>

<implementation>
Follow these integration principles:

**Component Wiring:**
- Use dependency injection for clean component relationships
- Implement proper startup sequencing and health checks
- Add configuration-driven component activation
- Ensure graceful handling of component failures

**Testing Strategy:**
- Unit tests for isolated component functionality
- Integration tests for component interactions
- End-to-end tests for complete trading workflows
- Performance tests for latency and throughput
- Chaos engineering for resilience testing

**Monitoring Approach:**
- Health check endpoints for each component
- Metrics collection for performance tracking
- Event logging for debugging and analysis
- Alert thresholds for critical system states

**Production Readiness:**
- Configuration externalization
- Log aggregation and correlation
- Graceful degradation strategies
- Backup and recovery procedures

Avoid:
- Tight coupling in integration layer
- Synchronous operations that could cause blocking
- Insufficient error handling and recovery
- Missing monitoring for critical components
</implementation>

<output>
Create/modify the following files:

**New Files:**
- `./trading_bot_v2/tests/test_end_to_end.py` - Complete trading flow tests
- `./trading_bot_v2/tests/test_performance.py` - Performance benchmarking
- `./trading_bot_v2/monitoring.py` - Health checks and metrics
- `./trading_bot_v2/config/components.yaml` - Component configuration
- `./trading_bot_v2/scripts/health_check.py` - System health validation
- `./trading_bot_v2/docs/integration_guide.md` - Integration documentation

**Modified Files:**
- `./trading_bot_v2/api_server.py` - Component integration and monitoring endpoints
- `./trading_bot_v2/trading_bot.py` - Final coordinator integration
- `./trading_bot_v2/database.py` - Complete schema and migrations
- `./trading_bot_v2/config.py` - Component configuration support

**Testing:**
- `./trading_bot_v2/tests/test_integration.py` - Enhanced integration tests
- `./pytest.ini` - Test configuration and coverage settings
- `./requirements-dev.txt` - Testing and monitoring dependencies

**Documentation:**
- `./docs/architecture_complete.md` - Final architecture documentation
- `./docs/deployment_guide.md` - Production deployment procedures
- `./docs/monitoring_guide.md` - Monitoring and alerting setup
- Update `./README.md` with complete system overview
</output>

<verification>
Before declaring refactoring complete, verify:

1. **Component Integration**: All components start up and communicate correctly
2. **End-to-End Flows**: Complete trading workflows function from signal to execution
3. **WebSocket Authority**: Live trading uses WebSocket prices exclusively
4. **Test Coverage**: >80% coverage across all components and integration points
5. **Performance**: No degradation, improved monitoring capabilities
6. **Monitoring**: Health checks, metrics, and alerting functional
7. **Production Ready**: Graceful shutdown, configuration management, logging

Run final validation:
- `python -m pytest --cov=. --cov-report=html` (coverage report)
- `python trading_bot_v2/scripts/health_check.py` (system health)
- Manual testing: Complete trading cycle with real signals
- Performance testing: Load testing and profiling
</verification>

<success_criteria>
Refactoring is complete when:
- [ ] All components integrate seamlessly through dependency injection
- [ ] End-to-end trading flows work from signal generation to execution
- [ ] WebSocket cache is authoritative for all live trading decisions
- [ ] Comprehensive test suite achieves >80% coverage
- [ ] Monitoring system provides full observability
- [ ] Performance maintained or improved vs. original system
- [ ] Production deployment procedures documented and tested
- [ ] Emergency stops and circuit breakers functional
- [ ] Clean, maintainable, layered architecture established
- [ ] All original functionality preserved with enhanced reliability
</success_criteria></content>
<parameter name="filePath">prompts/045-phase3-integration-testing.md

---
Completed at: 2026-01-15T22:56:33.867Z
