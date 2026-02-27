<objective>
Implement a comprehensive impact analysis and update strategy for code changes to ensure all dependent sections are updated simultaneously, preventing the "fix one thing, break another" cycle. This is critical for maintaining system stability and avoiding cascading failures in the trading bot codebase.
</objective>

<context>
This is for a complex trading bot system with interconnected Python backend (FastAPI, database, trading logic) and HTML/JavaScript frontend. Changes to one component often affect multiple dependent sections, and users are experiencing frustration with fixes that break other functionality. This prompt will be used for all future code changes to ensure holistic updates.

@api_server.py - main API endpoints and logic
@database.py - database operations and models
@models.py - data models and structures
@config.py - configuration management
@trading_bot_interface.html - frontend interface
</context>

<requirements>
1. Before making any code changes, perform comprehensive impact analysis to identify all files and sections that use the changed component
2. Update all dependent code sections simultaneously to maintain consistency
3. Test all affected functionality after changes to ensure nothing is broken
4. Document the impact analysis and changes made for future reference
</requirements>

<implementation>
Thoroughly analyze the codebase to understand dependencies and usage patterns. For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially.

Go beyond basic fixes - implement a systematic approach that:
- Uses grep and file analysis to find all references to changed functions/classes/variables
- Identifies indirect dependencies (code that depends on code that depends on your changes)
- Updates all affected sections with consistent changes
- Includes comprehensive testing of all impacted areas

After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding.
</implementation>

<output>
Create/modify files with relative paths as needed for the specific changes being made. Always update all dependent sections identified in the impact analysis.

Save impact analysis documentation to: ./diagnoses/[descriptive-name]-impact-analysis.md
</output>

<verification>
Before declaring complete:
1. Verify all identified dependent sections have been updated
2. Test all affected functionality to ensure it works correctly
3. Run the full system (API server + interface) to confirm no regressions
4. Document all changes and their impacts for future reference
</verification>

<success_criteria>
- All dependent code sections are identified and updated
- No functionality is broken by the changes
- System runs stably with all components working
- Impact analysis is documented for future reference
- Changes are consistent across all affected areas
</success_criteria>