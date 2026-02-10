<objective>
Create a comprehensive bugs document that catalogs all issues encountered in the trading bot project, including syntax errors, connection problems, data display issues, and stability problems. This document will serve as a reference for systematic fixes and prevent overlooking issues.
</objective>

<context>
This is for a complex trading bot system with Python backend and HTML/JavaScript frontend. Multiple issues have been identified through diagnostics, user reports, and testing, including server startup failures, interface connection errors, incorrect data display, and bot stability problems. A centralized bugs document is needed for tracking and resolution.

All project files and diagnostics reviewed during troubleshooting.
</context>

<requirements>
1. Analyze all diagnostics, error messages, and user reports from the troubleshooting process
2. Categorize bugs by severity (critical, high, medium, low) and component (server, interface, database, bot)
3. Document each bug with description, symptoms, impact, and potential root causes
4. Include reproduction steps where possible
5. Prioritize bugs based on system functionality impact
</requirements>

<implementation>
Thoroughly review all diagnostics, error logs, and conversation history to identify every issue mentioned. For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially.

Go beyond basic listing - provide detailed analysis of each bug's impact and interdependencies.

After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding.
</implementation>

<output>
Create the bugs document with relative path:
- ./bugs.md - comprehensive catalog of all identified issues
</output>

<verification>
Before declaring complete:
1. Verify all known issues from diagnostics are included
2. Ensure bugs are properly categorized and prioritized
3. Check that each bug has clear description and impact assessment
4. Validate the document is well-organized and readable
</verification>

<success_criteria>
- All identified bugs are documented in bugs.md
- Bugs are categorized by severity and component
- Each bug includes description, symptoms, impact, and root cause analysis
- Document serves as comprehensive reference for fixes
</success_criteria>