<objective>
Conduct a comprehensive project status assessment to identify all working and non-working components of the trading bot project. This analysis will help determine the next steps needed to ensure full system functionality and reliability.
</objective>

<context>
This is a Solana-based trading bot project with multiple components including:
- Python backend (API server, trading logic, database)
- HTML/JavaScript frontend interface
- Database (SQLite with trading data)
- Testing suite
- Configuration and environment management

The project uses technologies like FastAPI, Loguru, Pydantic, and various trading-related libraries. The goal is to have a fully functional automated trading system.

@api_server.py - Main API server
@database.py - Database connection and operations
@trading_bot_interface.html - Frontend interface
@tests/ - Test suite
@config.py - Configuration management
@requirements.txt - Dependencies
</context>

<requirements>
Perform a thorough, systematic assessment of the entire project:

1. **Code Quality & Structure**
   - Analyze all Python files for syntax errors, import issues, and structural problems
   - Check JavaScript/HTML for syntax and structural issues
   - Verify configuration loading and environment variable handling

2. **Database & Data Layer**
   - Test database connections and schema integrity
   - Check data migration status and table structures
   - Validate data consistency and relationships

3. **API Endpoints**
   - Test all API endpoints for functionality
   - Check authentication and authorization
   - Verify error handling and response formats

4. **Testing Suite**
   - Run all tests and analyze results
   - Identify failing tests and their root causes
   - Assess test coverage and quality

5. **Frontend Interface**
   - Check HTML structure and JavaScript functionality
   - Test user interface interactions
   - Verify data display and form submissions

6. **Dependencies & Environment**
   - Verify all required packages are installed
   - Check environment configuration
   - Validate external service connections (if any)

7. **Integration Testing**
   - Test end-to-end workflows
   - Verify component interactions
   - Check system stability under load

For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially.
</requirements>

<analysis_approach>
Thoroughly analyze each component using appropriate tools:
- Use code analysis tools for syntax and structure checks
- Execute tests to identify functional issues
- Run API validation scripts
- Check database integrity
- Examine logs and error outputs

After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding.

Go beyond basic checks - perform deep validation of critical trading bot functionality including balance management, position tracking, market data feeds, and execution logic.
</analysis_approach>

<output_format>
Create a comprehensive status report saved to: `./docs/project-status-report.md`

Structure the report with:
- Executive Summary (working vs non-working overview)
- Detailed Component Analysis (one section per major component)
- Critical Issues (blocking problems that prevent operation)
- Minor Issues (non-blocking but need attention)
- Working Components (fully functional areas)
- Next Steps Recommendations (prioritized action items)
- Risk Assessment (potential impact of issues)

Include specific error messages, test failures, and code issues with file/line references.
</output_format>

<verification>
Before declaring complete, verify your assessment by:
- Running a final integration test if possible
- Cross-referencing findings across components
- Ensuring all major functionality has been tested
- Validating that recommendations are actionable and prioritized

Confirm the report accurately reflects the current project state and provides clear guidance for next steps.
</verification>

<success_criteria>
- All major project components have been analyzed
- Working and non-working elements are clearly identified
- Specific error details and locations are documented
- Actionable next steps are provided with priority levels
- Report is comprehensive yet focused on critical issues
- Findings are validated through testing where possible
</success_criteria>