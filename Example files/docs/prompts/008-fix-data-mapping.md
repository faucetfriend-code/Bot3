<objective>
Audit and fix all data mapping issues in the trading bot system, optimize data organization for maximum efficiency, and ensure the interface displays live data instead of placeholders.

The system should have extensive real market data available, but the interface is currently showing placeholder data. This indicates problems in the data pipeline from API ingestion → database storage → bot processing → interface display. The data organization must also be optimized for performance and scalability.
</objective>

<context>
This is for the trading bot project that collects real-time market data from Pacifica and other sources. The system should have substantial amounts of live trading data (positions, balance history, market data, indicators, etc.) stored in the SQLite database and displayed on the web interface.

The data flow is: External APIs → Data collectors → Database storage → API endpoints → Frontend display. Any breaks in this pipeline result in placeholder data being shown instead of real information.

Tech stack: Python/FastAPI, SQLite database, HTML/JavaScript frontend, real-time data collection from Pacifica DEX.
</context>

<research>
Thoroughly investigate the data pipeline by:
1. Analyzing API data ingestion and mapping to database schemas
2. Checking database table structures and data relationships
3. Examining API endpoint data retrieval and serialization
4. Reviewing frontend data consumption and display logic
5. Identifying any hardcoded placeholder data or fallback values
6. Assessing data organization efficiency (indexes, queries, caching)

Consider multiple optimization approaches for data organization:
- Database normalization vs denormalization for query performance
- Indexing strategies for common query patterns
- Data partitioning for large datasets
- Caching layers for frequently accessed data
- Efficient serialization formats
</research>

<requirements>
1. Audit the complete data flow from API to database to interface
2. Fix any incorrect data mappings or transformations
3. Optimize database schema and queries for performance
4. Remove all placeholder/fallback data and ensure live data display
5. Implement most efficient data organization methods
6. Verify data integrity and consistency across the pipeline
</requirements>

<implementation>
For maximum efficiency, use parallel tool calls when analyzing different parts of the data pipeline.

When examining data flow, trace specific data points through the entire system: `SELECT * FROM balance_history LIMIT 5` to see if real data exists.

Go beyond basic fixes - implement advanced optimization techniques like:
- Composite indexes for multi-column queries
- Query result caching for expensive operations
- Efficient data serialization (JSON vs custom formats)
- Database connection pooling for high-frequency access

Explain WHY certain optimizations matter: "Database indexes are crucial because they reduce query time from O(n) to O(log n) for large datasets"
</implementation>

<output>
Modify any necessary files for data mapping and optimization:
- Update database schemas if needed for better organization
- Fix API endpoint data retrieval logic
- Correct frontend data display mappings
- Optimize database queries and add appropriate indexes

Create comprehensive audit reports:
- ./diagnoses/data-mapping-audit.md (mapping issues and fixes)
- ./diagnoses/data-optimization-report.md (efficiency improvements)
- ./diagnoses/live-data-verification.md (confirmation of live data display)
</output>

<verification>
Before declaring complete, verify:
- Database contains real trading data (not placeholders): `SELECT COUNT(*) FROM balance_history;`
- API endpoints return live data: `curl http://localhost:8000/api/pacifica/balance-history`
- Frontend displays real data: Check interface shows actual balances, positions, market data
- Data flows correctly: Add test data and verify it appears in interface
- Performance is optimized: Query times under 100ms for common operations
- No placeholder text remains in the interface

Test the complete data pipeline by adding new market data and confirming it appears live.
</verification>

<success_criteria>
- All data mappings are correct from API → database → interface
- Interface displays 100% live data with no placeholders
- Database queries are optimized with appropriate indexes
- Data organization uses most efficient methods for the use case
- System can handle large datasets without performance degradation
- Data integrity maintained across the entire pipeline
- Real-time data updates appear immediately in the interface
</success_criteria>