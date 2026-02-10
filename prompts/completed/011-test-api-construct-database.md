<objective>
Test API connections with real Pacifica testnet credentials and use the accurate data formats returned to construct and implement the SQLite database schema. This ensures the database perfectly matches real API response structures for Phase 3.5 database implementation.
</objective>

<context>
This is for the Unity Oracle Aggregator trading bot project, Phase 3.5.1-3.5.4: Test API Server with Mock Data, Analyze API Response Structures, Design Database Schema Based on API Data, Implement Basic Database Layer. The system has environment variables loaded and needs to connect to Pacifica testnet to get authentic data for database design.

Who will use this: Development team to validate API connections and build database schema from real data.
What it's for: Ensuring database design matches actual Pacifica API responses, not assumptions.
</context>

<requirements>
Test and analyze Pacifica API connections, then implement database:

1. Test API authentication and connection using real credentials from .env
2. Fetch real data from Pacifica testnet endpoints (balance, positions, markets, trades)
3. Analyze and document exact response formats and data types
4. Identify data format differences (e.g., bid/ask vs long/short, string amounts)
5. Design SQLite database schema based on real API structures
6. Implement database tables with correct column types and constraints
7. Create CRUD operations matching real data formats
8. Test database operations with real API data samples

Focus on getting accurate data formats first, then building database around them.
</requirements>

<constraints>
- Must use valid Pacifica testnet credentials from .env file
- Do not perform actual trades during testing
- Respect API rate limits (10 requests/second)
- Document all API response formats exactly
- Database schema must match real API data structures
- Include error handling for API failures
- Keep database implementation simple but accurate
</constraints>

<implementation>
Follow these testing and database implementation patterns:
- Load environment variables securely before API calls
- Test each API endpoint individually with error handling
- Log API responses for analysis without exposing credentials
- Map API response fields to database columns precisely
- Use appropriate SQLite data types (TEXT for strings, REAL for floats, INTEGER for timestamps)
- Include proper indexing for query performance
- Test database insertion and retrieval with real API data

Data format focus:
- Pacifica returns "side": "bid"/"ask" but UI expects "long"/"short"
- Amounts are strings, need conversion for calculations
- Timestamps are integers, may need ISO conversion
- Positions include unrealized_pnl calculations
</implementation>

<output>
Create/modify files with relative paths:
- `./trading_bot_v2/database.py` - Implement real SQLite database with schema based on real API data
- `./analyses/api-data-analysis.md` - Document real API response formats and data mappings
</output>

<verification>
Before declaring complete, verify your work:
- API connections work with real credentials
- Real data fetched and analyzed from all endpoints
- Database schema matches exact API response structures
- Data types and formats preserved correctly
- CRUD operations work with real API data samples
- No data corruption during storage/retrieval
- Database ready for Phase 3.5 integration
</verification>

<success_criteria>
- API connections tested and working with real Pacifica data
- Database schema designed from authentic API responses
- SQLite implementation matches real data formats perfectly
- Data format conversions identified and handled
- Database ready for full Phase 3.5 integration
- Real API data successfully stored and retrieved
</success_criteria></content>
<parameter name="filePath">C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\prompts\011-test-api-construct-database.md