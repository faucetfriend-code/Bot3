# Agent Guidelines for Trading Bot v2

## Build/Lint/Test Commands
- **Lint (Modern)**: `ruff check .` (Python linting with auto-fix - preferred)
- **Lint (Legacy)**: `flake8 .` (Traditional Python linting)
- **Type Check**: `mypy .` (Python type checking)
- **Format**: `black .` (Python code formatting)
- **Format (Alt)**: `ruff format .` (Fast alternative to black)
- **Test**: `pytest` (run all tests)
- **Single Test**: `pytest path/to/test_file.py::TestClass::test_method`
- **Coverage**: `pytest --cov=. --cov-report=html`
- **Performance**: `pytest tests/test_performance.py -v`
- **E2E Tests**: `npm test` (from project root - Playwright tests)
- **API Testing**: `python ../test_api_endpoints.py` (from project root)
- **Health Check**: `python scripts/health_check.py` (comprehensive system validation)
- **Hub System Tests**: `pytest tests/test_hub_system.py` (component orchestration testing)

## Code Style Guidelines

### Python Conventions
- **Imports**: Standard library → third-party → relative imports (alphabetized)
- **Types**: Type hints required for all function parameters and return values
- **Naming**: PascalCase classes, snake_case functions/methods, UPPER_CASE constants
- **Error Handling**: Specific exception types in try/except blocks
- **Async**: Use async/await for I/O operations, asyncio for concurrency
- **Docstrings**: Google/NumPy style docstrings for public functions/classes
- **Line Length**: 88 characters (Black default)
- **String Quotes**: Single quotes for code, double quotes for docstrings
- **Path Handling**: Use `pathlib.Path` instead of `os.path`

### General Patterns
- **Classes**: Inherit from ABC for abstract classes, use descriptive method names
- **Documentation**: Docstrings required for public methods/classes
- **Security**: Never expose secrets, use environment variables for sensitive data
- **Imports**: Group with blank lines between standard/third-party/relative
- **Circuit Breaker**: Implement resilience patterns for external dependencies

### Agent-Specific Guidelines
- **Generator**: Respect existing code style and context when generating code
- **Reviewer**: Focus on security, performance, and maintainability
- **Tester**: Write comprehensive pytest/playwright tests
- **Security**: Run bandit and detect-secrets scans regularly
- **Documentation**: Update API docs and README when making changes

### Project-Specific Requirements
- **Core Logic Imports**: Use absolute imports from `core_logic` package
- **Component Interfaces**: Implement ABC-based interfaces for all major components
- **WebSocket Integration**: Follow hub system patterns for real-time communication
- **Hub System Patterns**: Use DataHub for data management, Event System for decoupled communication
- **Circuit Breaker Patterns**: Implement resilience patterns for external dependencies and database operations
- **Testing**: Maintain 80%+ code coverage with comprehensive E2E tests
- **Performance**: Monitor critical paths with dedicated performance tests
- **Monitoring**: Implement health checks and performance metrics for all components
