<objective>
Create a comprehensive chart mapping every API endpoint to its database interactions, showing exactly which tables and columns each API call reads from or writes to. This ensures all data flows are correctly mapped and no database operations are missed.
</objective>

<context>
This is for a trading bot with a FastAPI backend serving data to an HTML frontend. The system has complex data flows between API endpoints and database tables. Users need to verify that all API calls are correctly accessing the right database locations to prevent data inconsistencies or missing functionality.

@api_server.py - contains all API endpoints and their database operations
@database.py - database connection and utility functions
@models.py - data models and table schemas
</context>

<requirements>
1. Analyze every API endpoint in api_server.py
2. Map each endpoint to its database queries (SELECT, INSERT, UPDATE, DELETE)
3. Identify specific tables and columns accessed by each endpoint
4. Document data flow direction (read/write/both)
5. Include any conditional database operations
6. Verify all endpoints have proper database interactions
</requirements>

<implementation>
Thoroughly analyze the entire api_server.py file to identify all endpoints and their database operations. For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially.

Go beyond basic endpoint listing - trace the complete data flow from API request through database operations to response, ensuring every database interaction is documented.

After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding.
</implementation>

<output>
Create comprehensive API-database mapping chart with relative path:
- ./database-double-check.md - detailed chart of all API endpoints and their database interactions
</output>

<verification>
Before declaring complete:
1. Verify all API endpoints are documented
2. Confirm database table/column mappings are accurate
3. Check that data flow directions are correct
4. Validate that all database operations are captured
</verification>

<success_criteria>
- Complete mapping of all API endpoints to database operations
- Clear identification of tables and columns for each endpoint
- Accurate data flow direction documentation
- All database interactions properly documented
- Chart format is clear and easy to understand
</success_criteria>