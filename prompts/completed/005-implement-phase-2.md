<objective>
Complete Phase 2: Trading Core by implementing the Pacifica client and trading bot logic. Read the project-plan.md to understand the exact API specifications and trading logic requirements, then delegate to specialized agents for code generation, testing, and review.
</objective>

<context>
Phase 2 builds on the Phase 1 configuration foundation. The Pacifica client handles all exchange API interactions with HMAC authentication, while the trading bot implements the core trading logic using mock data initially (since database comes later). All implementations must follow the exact API specifications and data structures defined in the plan.
</context>

<requirements>
1. Read and understand Phase 2 requirements from project-plan.md
2. Implement pacifica_client.py with all 6 API methods (get_balance, get_positions, place_order, cancel_order, get_market_data, get_markets)
3. Implement trading_bot.py with core logic, position tracking, signal generation, and risk monitoring
4. Use the exact API endpoints, request/response formats, and error handling specified
5. Ensure all components integrate with the config system from Phase 1
6. If any API details are unclear, ask the user for clarification rather than making assumptions
</requirements>

<implementation>
Implement in order: client first, then bot. Use the exact specifications from the plan for all API calls. For code generation, delegate to @generator with complete API specs. For testing, delegate to @tester with mock API responses. For review, delegate to @reviewer.

When delegating:
- Include all API specifications from the plan
- Specify exact request/response formats
- Provide authentication details (HMAC generation)
- Request comprehensive error handling for all API calls
- Ensure mock data for testing until database is available

If you encounter any ambiguity in API specs or implementation details, stop and ask the user before proceeding.
</implementation>

<output>
Update the trading_bot_v2/ directory with:
- pacifica_client.py - Complete API client with all methods
- trading_bot.py - Core trading logic and bot implementation

All files should follow the exact specifications from the project plan.
</output>

<verification>
Before declaring Phase 2 complete, verify:
- All 6 API methods implemented with correct endpoints and auth
- Trading bot initializes and runs without database (using in-memory state)
- API error handling works for 401, 429, 4xx, 5xx responses
- Integration with config system works correctly
- No syntax errors or import issues
</verification>

<success_criteria>
- Phase 2 tasks 2.1 and 2.2 completed successfully
- Pacifica client handles all API interactions correctly
- Trading bot implements core logic with mock data
- Ready to proceed to Phase 3
- All code follows plan specifications exactly
</success_criteria>