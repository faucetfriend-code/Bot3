<objective>
Begin implementation of the trading bot project by completing Phase 1: Core Infrastructure. Read the project-plan.md to understand the complete architecture and requirements, then implement the directory structure and configuration system. Delegate to specialized agents as needed for code generation, testing, and review.
</objective>

<context>
This is the start of building the streamlined trading bot v2 based on the detailed project plan. Phase 1 focuses on the foundational configuration system that all other components will depend on. The implementation must follow the exact specifications in the plan, including all API details and data structures.
</context>

<requirements>
1. Read and understand the complete project-plan.md file
2. Create the trading_bot_v2/ directory structure with all 8 required files
3. Implement the config.py module exactly as specified, including all environment variables and validation
4. Ensure the configuration system is robust and handles all edge cases
5. Test the configuration loading and validation thoroughly
6. If any aspect is unclear or missing details, ask the user for clarification rather than making assumptions
</requirements>

<implementation>
Start with the simplest components first to establish a solid foundation. Use the exact specifications from the plan for all implementations. For code generation tasks, delegate to the @generator agent. For testing, delegate to the @tester agent. For code review, delegate to the @reviewer agent.

When delegating:
- Provide complete context from the project plan
- Include all API specifications and data structures
- Specify exact requirements for each component
- Request thorough testing and validation

If you encounter any ambiguity in the plan or need clarification on implementation details, stop and ask the user before proceeding.
</implementation>

<output>
Create the trading_bot_v2/ directory with:
- config.py - Complete configuration module
- Empty placeholder files for the other 7 components (database.py, api_server.py, etc.)

All files should be created in the correct relative paths as specified in the plan.
</output>

<verification>
Before declaring Phase 1 complete, verify:
- Directory structure matches the plan exactly
- Configuration loads all required environment variables
- Validation works for missing/invalid credentials
- No syntax errors or import issues
- Configuration can be imported and used by other modules
</verification>

<success_criteria>
- Phase 1 tasks 1.1 and 1.2 completed successfully
- Configuration system fully functional and tested
- Ready to proceed to Phase 2
- All code follows the plan specifications exactly
</success_criteria>