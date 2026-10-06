<objective>
Complete Phase 3.5 of the trading bot rebuild by integrating the real SQLite database with the API server and trading bot, replacing all mock data with actual database operations, and validating end-to-end functionality with real Pacifica API data.
</objective>

<context>
This is for the Unity Oracle Aggregator trading bot project, Phase 3.5: Database Configuration & Testing. The database has been implemented with real API data structures. Now we need to integrate it fully and complete the phase.

Who will use this: The complete trading bot system for production-ready data persistence and retrieval.
What it's for: Finalizing Phase 3.5 to enable full functionality with real data storage.
</context>

<requirements>
Complete Phase 3.5 by integrating and testing the database:

1. Update api_server.py to use real database instead of mocks
2. Update trading_bot.py to save trades to real database
3. Implement data format conversions (bid/ask ↔ long/short, string amounts, timestamps)
4. Test all API endpoints with real database operations
5. Validate data flow: API → Database → API responses → Web interface
6. Test trading bot operations with real data persistence
7. Verify performance meets requirements (< 100ms API response times)
8. Document all data conversions and mappings
9. Run comprehensive integration tests
10. Validate Phase 3.5 success criteria are met

Ensure the system works end-to-end with real Pacifica data.
</requirements>

<constraints>
- Do not break existing API endpoint contracts
- Maintain data integrity during conversions
- Respect API rate limits during testing
- Use real Pacifica testnet credentials only
- Keep error messages user-friendly
- Ensure thread safety for concurrent operations
- Document all changes for future maintenance
</constraints>

<implementation>
Follow these integration patterns:
- Replace mock database calls with real Database() instances
- Implement conversion functions for data format differences
- Add proper error handling for database failures
- Test each endpoint individually before integration testing
- Use real API data for validation
- Log data flow for debugging
- Ensure web interface displays correct converted data

Data conversion implementation:
- Create utility functions for format conversions
- Apply conversions at API response level
- Test conversions with real data samples
- Handle edge cases (missing fields, invalid data)
</implementation>

<output>
Create/modify files with relative paths:
- `./trading_bot_v2/api_server.py` - Integrate real database, add data conversions
- `./trading_bot_v2/trading_bot.py` - Update to use real database for trade persistence
- `./trading_bot_v2/data_utils.py` - Create utility functions for data format conversions
- `./analyses/phase-3-5-completion.md` - Document completion validation and results
</output>

<verification>
Before declaring complete, verify your work:
- API server uses real database for all operations
- Trading bot saves trades to database
- Data format conversions work correctly
- Web interface displays real data properly
- All endpoints return data from database
- Performance requirements met
- No data corruption during conversions
- Phase 3.5 success criteria validated
</verification>

<success_criteria>
- Database fully integrated with API server and trading bot
- All mock data replaced with real database operations
- Data format conversions implemented and tested
- End-to-end functionality validated with real API data
- Web interface displays live trading data correctly
- Phase 3.5 completed successfully
- System ready for Phase 4 deployment
</success_criteria></content>
<parameter name="filePath">G:\ai-workspace\Bot 3\prompts\012-complete-phase-3-5.md