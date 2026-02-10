<objective>
Update the project-plan.md to restructure the implementation phases so that the database is built after the API server. Add a configuration pause phase where the database schema can be adjusted based on actual API response data received during testing, ensuring all information is stored correctly and can be validated.
</objective>

<context>
The current plan builds the database first, but the user wants to build the API server first to understand what data structures are needed from real API responses, then configure the database accordingly. This prevents mismatches between API data and database schema.
</context>

<requirements>
1. Read the current project-plan.md file
2. Move Phase 1.3 (database implementation) to after Phase 3 (API server & interface)
3. Create a new Phase 3.5: Database Configuration & Testing
4. Update phase numbering and dependencies throughout the document
5. Add specific tasks for testing API responses and configuring database tables accordingly
6. Ensure the pause allows for manual review and adjustment of database schema based on real API data
</requirements>

<implementation>
Restructure the phases as follows:
- Phase 1: Core Infrastructure (config only)
- Phase 2: Trading Core (Pacifica client, trading bot)
- Phase 3: API Server & Interface
- Phase 3.5: Database Configuration & Testing (new phase)
- Phase 4: Dependencies & Deployment

In the new Phase 3.5, include tasks for:
- Running the API server to receive real API responses
- Analyzing the structure of API data received
- Adjusting database table schemas to match API response formats
- Testing data insertion and retrieval
- Validating that all API information can be stored and retrieved correctly

Explain why this order matters: building the database after seeing real API data ensures the schema perfectly matches the data structures, preventing data loss or incorrect storage.
</implementation>

<output>
Update the existing ./project-plan.md file with:
- Restructured Implementation Phases section
- New Phase 3.5: Database Configuration & Testing
- Updated phase numbering and dependencies
- Clear tasks for API testing and database schema adjustment
</output>

<verification>
Before declaring complete, verify:
- Database implementation moved after API server
- New configuration phase includes testing and schema adjustment
- All phase dependencies updated correctly
- No broken references to old phase numbers
</verification>

<success_criteria>
- Database phase moved to after API server
- New configuration phase allows for API data-driven schema design
- All phases renumbered correctly
- Document remains coherent and executable
</success_criteria>