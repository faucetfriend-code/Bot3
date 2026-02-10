<objective>
Clean up the disorganized trading bot project by identifying and removing unused files, reorganizing the file structure into a logical and maintainable layout, and ensuring all references (imports, paths, configurations) are updated to reflect any moved files. This cleanup is critical to eliminate potential disruptions to the project's current functioning and improve maintainability.
</objective>

<context>
This is a Python/Node.js trading bot project with components for API serving, trading logic, market data collection, and a web interface. The project contains various old files including backups (trading_bot.db.backup, trading_bot.db.old), diagnostic reports, temporary files, and potentially obsolete scripts. The cleanup should preserve all functional code while removing clutter that could interfere with operations.

Key project files to preserve:
- Core Python modules: api_server.py, main.py, database.py, etc.
- Configuration files: config.py, config.yaml.example
- Web interface: trading_bot_interface.html
- Test files in tests/
- Documentation in docs/
- Build scripts and requirements

Examine the project structure thoroughly to understand dependencies and usage patterns.
</context>

<requirements>
1. Thoroughly analyze the entire codebase to identify unused files:
   - Files not imported or referenced anywhere in the active codebase
   - Old backup files, temporary files, and obsolete diagnostics
   - Duplicate or redundant files
   - Files with no clear purpose or documentation

2. Assess the current file structure and propose a logical reorganization:
   - Group related files into appropriate directories
   - Separate concerns (core logic, tests, docs, scripts, data)
   - Follow Python/Node.js best practices for project layout

3. Remove identified unused files safely:
   - Create backups before deletion if uncertain
   - Ensure no active code depends on removed files

4. Update all references for moved files:
   - Python imports (relative and absolute)
   - Configuration file paths
   - Script references
   - Documentation links
   - HTML/JavaScript asset references

5. Verify the cleanup doesn't break functionality:
   - Run existing tests
   - Check that the bot can start and basic operations work
   - Validate configuration loading
</requirements>

<implementation>
Use systematic analysis with tools to explore the codebase:

- Use grep to find all references to files before moving/removing
- Use glob to identify file patterns and structure
- Use read to examine key files for dependencies
- Execute bash commands for file operations (mv, rm, mkdir)

Be conservative: when in doubt about a file's usage, document it rather than delete it. Explain WHY you're keeping or removing each file.

For reorganization, consider this structure:
- / (root): main scripts, README, configs
- /agent/: agent-related code
- /tests/: all test files
- /docs/: documentation
- /scripts/: utility scripts
- /data/: database and data files
- /python-sdk/: SDK code
- /node_modules/: keep as-is
- /monitoring/: monitoring configs

Update all import statements and configuration references accordingly.
</implementation>

<output>
Create a summary document of changes made:
- ./cleanup-summary.md - Document all files removed, moved, and updated references

Ensure all moved files are in their new locations with updated paths throughout the codebase.
</output>

<verification>
Before declaring complete, verify your work:
1. Run `python -m pytest tests/` to ensure tests pass
2. Execute `python api_server.py --help` to verify the server starts
3. Check that `python config.py` loads without errors
4. Run `npm run check` if applicable for the web interface
5. Manually verify key imports work in Python files

Document any issues found and how they were resolved.
</verification>

<success_criteria>
- All unused files identified and removed
- File structure is logical and follows best practices
- All references updated and no broken imports
- Project tests pass and core functionality works
- Cleanup summary documented in ./cleanup-summary.md
</success_criteria>