<objective>
Create and execute a comprehensive plan to fix all critical priority issues and implement API integration for data displays in the trading bot interface. This will transform the static HTML interface into a fully functional trading dashboard with real-time data, authentication, and trading controls.
</objective>

<context>
This is for the Pacifica.fi trading bot project where the HTML interface has comprehensive structure but lacks JavaScript functionality. The critical issues identified include authentication, trading controls, dashboard metrics, and data display integrations. This plan will address the highest priority items needed to make the interface operational for basic trading activities.
</context>

<requirements>
1. **Phase 1: Authentication & Connection** - Implement login modal, connection handling, and account switching
2. **Phase 2: Core Trading Controls** - Make action buttons functional (standby, activate, stop trading)
3. **Phase 3: Dashboard Metrics** - Connect all financial displays to real-time API data
4. **Phase 4: Positions & Market Data** - Implement positions table and market data displays
5. **Phase 5: Real-time Updates** - Add WebSocket connections for live data streaming

Each phase should include:
- JavaScript function implementations
- API endpoint integration
- Error handling and user feedback
- Testing and validation
</requirements>

<implementation>
**Development Approach:**
- Use existing API endpoints documented in CLAUDE.md
- Follow established patterns for error handling and data fetching
- Implement circuit breaker pattern for API calls
- Add proper loading states and user feedback
- Ensure all implementations are testable

**Code Quality Standards:**
- Use async/await for API calls
- Implement proper error handling with user-friendly messages
- Add loading indicators for all async operations
- Follow existing code patterns and naming conventions
- Include comprehensive error logging

**Testing Strategy:**
- Test each component individually before integration
- Verify API responses and error handling
- Test user workflows end-to-end
- Validate data accuracy and real-time updates
</implementation>

<output>
Create a phased implementation plan with detailed tasks:

**Phase 1: Authentication & Connection**
- Implement secureLogin() function for authentication
- Add connection status monitoring and updates
- Implement account switching functionality
- Add session management and error handling

**Phase 2: Core Trading Controls**
- Implement startBotStandby(), activateBotTrading(), stopBot()
- Add bot status state management
- Implement trading mode toggles (paper/real)
- Add confirmation dialogs for critical actions

**Phase 3: Dashboard Metrics Integration**
- Connect all metric displays to /api/status endpoint
- Implement real-time balance, P&L, and position updates
- Add margin utilization calculations and progress bars
- Implement account health monitoring

**Phase 4: Positions & Market Data**
- Implement positions table population from /api/positions
- Add market data display from /api/market-data
- Implement sorting and filtering functionality
- Add position details modal with full information

**Phase 5: Real-time Data Streaming**
- Implement WebSocket connections for live updates
- Add automatic data refresh intervals
- Implement data caching and performance optimization
- Add connection recovery and error handling

Save the implementation plan to: ./plans/critical-fixes-implementation-plan.md
</output>

<verification>
Before declaring complete, verify:
- All critical priority items from the checklist analysis are addressed
- API integrations follow existing patterns in the codebase
- Error handling is comprehensive and user-friendly
- All implementations include proper testing
- Real-time functionality works as expected
</verification>

<success_criteria>
- Complete implementation plan covering all critical issues
- Clear phase-by-phase breakdown with specific deliverables
- All API integrations properly documented
- Testing strategy included for each component
- Plan ready for immediate execution
</success_criteria>