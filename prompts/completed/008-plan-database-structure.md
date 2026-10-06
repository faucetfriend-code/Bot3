<objective>
Plan the complete database structure for Phase 3.5 of the trading bot v2 system, ensuring perfect alignment with real API response structures from the Pacifica test network. This planning is critical for implementing a robust SQLite database that accurately stores and retrieves trading data without data loss or corruption.
</objective>

<context>
This is for the Unity Oracle Aggregator trading bot project, specifically Phase 3.5: Database Configuration & Testing. The system uses Python with FastAPI, and the database must use SQLite as specified in the project plan. The database will replace mock data in the API endpoints built in Phase 3.

The database structure must perfectly match the API response formats to ensure seamless integration. Real data from the Pacifica test network is essential to avoid discrepancies between development mock data and production data structures.

Who will use this: The database implementation team (agents) for Phase 3.5 development.
What it's for: Designing the foundation for persistent data storage in the trading bot system.
</context>

<data_sources>
@G:\ai-workspace\Bot 3\project-plan.md - Read Phase 3.5 requirements and overall project specifications
@G:\ai-workspace\Bot 3\trading_bot_v2\.env - Extract Pacifica test network credentials for real data access
![python -c "from trading_bot_v2.pacifica_client import PacificaClient; import os; client = PacificaClient(api_key=os.getenv('PACIFICA_API_KEY'), api_secret=os.getenv('PACIFICA_API_SECRET'), testnet=True); print(client.get_positions())"] - Fetch real positions data from test network
![python -c "from trading_bot_v2.pacifica_client import PacificaClient; import os; client = PacificaClient(api_key=os.getenv('PACIFICA_API_KEY'), api_secret=os.getenv('PACIFICA_API_SECRET'), testnet=True); print(client.get_trades())"] - Fetch real trades data from test network
![python -c "from trading_bot_v2.pacifica_client import PacificaClient; import os; client = PacificaClient(api_key=os.getenv('PACIFICA_API_KEY'), api_secret=os.getenv('PACIFICA_API_SECRET'), testnet=True); print(client.get_market_data())"] - Fetch real market data from test network
</data_sources>

<analysis_requirements>
Thoroughly analyze the real API response structures from the Pacifica test network to identify:
- Exact field names, data types, and nested structures
- Required vs optional fields
- Data relationships and foreign keys
- Indexing requirements for performance
- Data validation rules and constraints

Consider multiple approaches for schema design:
- Normalization vs denormalization trade-offs
- Handling time-series data efficiently
- Ensuring thread-safe operations for concurrent bot access
- Future extensibility for additional trading features

Deeply consider edge cases:
- Null values and their implications
- Large datasets and pagination needs
- Data consistency across related tables
- Migration strategies from mock to real data
</analysis_requirements>

<constraints>
- Must use SQLite as the database engine (as specified in project plan)
- Schema must exactly match API response structures to prevent data mapping issues
- Include proper error handling and data validation
- Ensure thread-safe database operations for concurrent access
- Design for performance with appropriate indexes
- Follow Python and SQLite best practices for data integrity
</constraints>

<output_format>
Create a comprehensive database structure plan saved to: `./analyses/database-structure-plan.md`

Structure the plan with these sections:
1. **API Response Analysis** - Detailed breakdown of each endpoint's real data structure
2. **Database Schema Design** - Complete CREATE TABLE statements with all columns, types, constraints
3. **Table Relationships** - Entity relationship diagram description and foreign key mappings
4. **Data Flow Mapping** - How API responses map to database tables and vice versa
5. **Performance Considerations** - Indexing strategy and query optimization plans
6. **Migration Strategy** - Steps to transition from mock data to real database
7. **Error Handling** - Database error scenarios and recovery strategies

Use clear, technical language with code examples for table schemas.
</output_format>

<verification>
Before completing the plan, verify:
- All API response fields are accounted for in the schema
- Data types match exactly (e.g., float vs int, string formats)
- Relationships are properly defined with foreign keys
- The schema supports all Phase 3 API endpoints without modification
- Performance considerations address the bot's real-time requirements
- Thread safety is addressed for concurrent database access
</verification>

<success_criteria>
- Database schema perfectly matches all real API response structures
- Complete CREATE TABLE statements provided for all necessary tables
- Clear documentation of data relationships and constraints
- Performance and thread-safety considerations addressed
- Migration path from mock data clearly defined
- Plan enables seamless implementation in Phase 3.5 without API changes
</success_criteria></content>
<parameter name="filePath">./prompts/008-plan-database-structure.md