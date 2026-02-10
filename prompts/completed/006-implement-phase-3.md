<objective>
Complete Phase 3: API Server & Interface by implementing the FastAPI server and web interface. Read the project-plan.md to understand the exact API endpoints and interface requirements, then delegate to specialized agents for code generation, testing, and review.
</objective>

<context>
Phase 3 builds the web layer on top of Phase 2. The API server provides REST endpoints for status, trades, positions, and bot control, while the interface serves a simple web dashboard. All endpoints must match the specifications in the plan, including request/response formats.
</context>

<requirements>
1. Read and understand Phase 3 requirements from project-plan.md
2. Implement api_server.py with all 7 endpoints (serve interface, status, trades, positions, start/stop bot, markets)
3. Implement interface.html with complete web interface including status display, controls, and data tables
4. Ensure API server integrates with trading bot from Phase 2
5. Test all endpoints and interface functionality
6. If any endpoint or interface details are unclear, ask the user for clarification rather than making assumptions
</requirements>

<implementation>
Implement in order: API server first, then interface. Use the exact endpoint specifications and response formats from the plan. For code generation, delegate to @generator with complete specs. For testing, delegate to @tester with endpoint validation. For review, delegate to @reviewer.

When delegating:
- Include all API endpoint specifications from the plan
- Specify exact HTML structure and JavaScript functionality
- Request integration testing with the trading bot
- Ensure CORS and error handling are properly implemented

If you encounter any ambiguity in the specs or implementation details, stop and ask the user before proceeding.
</implementation>

<output>
Update the trading_bot_v2/ directory with:
- api_server.py - Complete FastAPI server with all endpoints
- interface.html - Complete web interface with all features

All files should follow the exact specifications from the project plan.
</output>

<verification>
Before declaring Phase 3 complete, verify:
- All 7 API endpoints return correct JSON responses
- Web interface loads and displays data correctly
- Start/stop controls work with the trading bot
- No syntax errors or import issues
- Interface updates data in real-time
</verification>

<success_criteria>
- Phase 3 tasks 3.1 and 3.2 completed successfully
- API server provides all required endpoints
- Web interface is fully functional
- Ready to proceed to Phase 3.5
- All code follows plan specifications exactly
</success_criteria>