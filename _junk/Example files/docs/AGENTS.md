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
- **Testing**: Maintain 80%+ code coverage with comprehensive E2E tests
- **Performance**: Monitor critical paths with dedicated performance tests
- **Monitoring**: Implement health checks and performance metrics for all components

## Current Trading System Status (Jan 2026)

### Multi-Strategy Implementation (OPERATIONAL ✅)

**Active Strategies with Jan 2026 Updates**:
- **Mean Reversion**: RSI 35/65 (loosened), confidence 0.45 (down from 0.6)
- **MA Crossover**: 50/200 MA with pullback, confidence 0.65 (unchanged)
- **Grid Trading**: 0.4x ATR spacing (tighter), 8 levels (reduced from 10)
- **Liquidation Capture**: 2.5% price move, 2.5x volume spike (loosened)

**Enhanced Market Regime Detection**:
- **TRENDING_STRONG**: ADX > 30.0 (up from 28.0)
- **TRENDING_MODERATE**: ADX 25.0-30.0 (NEW regime)
- **RANGING_VOLATILE**: ADX ≤ 25.0, volatility > 65% (down from 75%)
- **RANGING_CALM**: ADX ≤ 25.0, volatility ≤ 65%
- **INDECISIVE**: Transitional conditions

### Risk Management (PRODUCTION-READY ✅)

**Kelly Criterion Position Sizing**:
- Fractional Kelly (0.5x) with 10% maximum position cap
- 50-trade minimum history requirement
- Strategy-specific fallback percentages

**Circuit Breaker Protection**:
- 10% portfolio loss trigger (configurable)
- 80% warning threshold with automatic halt
- Percentage-based calculation (not fixed dollar)
- Database operations protected by circuit breaker patterns

**Grid Trading Safeguards**:
- Maximum 10 positions per symbol
- ADX > 25.0 emergency stop
- Partial unwind on regime changes
- 5% portfolio emergency stop

### Hub System Architecture (OPERATIONAL ✅)

**Implemented Components**:
- **DataHub**: Thread-safe data management with circuit breaker
- **ConnectionManager**: WebSocket pooling and authentication
- **Component Registry**: Interface-based discovery and health monitoring
- **Event System**: Decoupled communication between components
- **Circuit Breaker**: Database failure protection with exponential backoff
- **Monitoring System**: Real-time health checks and performance metrics

**Communication Patterns**:
1. Real-time WebSocket broadcasts
2. Event-driven component communication
3. Interface-based method calls
4. Circuit breaker protection for external calls
5. Centralized dependency injection
6. Performance monitoring and alerting

### Web Interface Integration (FULLY OPERATIONAL ✅)

**Working Features**:
- ✅ Bot start/stop controls with state management
- ✅ Real-time status updates (30s intervals)
- ✅ Position and trade history display
- ✅ WebSocket live updates with reconnection
- ✅ Professional Bootstrap UI with dark theme
- ✅ Error handling with user-friendly messages
- ✅ Cross-browser compatibility (Chrome, Firefox, Safari)

### Testing Infrastructure (COMPREHENSIVE ✅)

**Available Test Suites**:
- **Unit Tests**: `pytest tests/` - Comprehensive unit test coverage
- **Integration Tests**: API endpoint testing with error scenarios
- **Performance Tests**: `pytest tests/test_performance.py` - Critical path benchmarks
- **Hub System Tests**: Component orchestration and communication testing
- **E2E Tests**: `npm test` from project root - Playwright web interface tests
- **Health Check Tests**: `python scripts/health_check.py` - System validation

**Test Quality Features**:
- ✅ Auto-fixing linting with ruff
- ✅ Coverage reporting with HTML output
- ✅ Missing line indicators in coverage
- ✅ Cross-browser E2E testing
- ✅ API error scenario coverage
- ✅ WebSocket reconnection testing
- ✅ Performance benchmarking with thresholds
- ✅ Memory usage validation under load

## Recent System Improvements & Implemented Repairs (2026)

### ✅ Enhanced Signal Generation & Fixed Trading Inactivity (Jan 2026)
- **Fixed Trading Inactivity**: Loosened confidence thresholds to 0.45 (from 0.6) for increased signal generation
- **Enhanced Regime Detection**: Added TRENDING_MODERATE regime for better market adaptation
- **Improved Grid Spacing**: Reduced to 0.4x ATR for tighter grid formation
- **Expanded RSI Ranges**: Mean reversion now uses 35/65 (from 30/70) for more signals
- **Lowered Liquidation Triggers**: Price move 2.5% and volume spike 2.5x for faster detection
- **Signal Confidence Optimization**: Dynamic confidence scoring with multi-factor validation

### ✅ API Infrastructure Improvements & Process Management (Jan 2026)
- **Fixed Redundant Processes**: Eliminated duplicate bot processes causing resource conflicts
- **Enhanced Rate Limiting**: Improved API rate limiting and retry logic
- **Circuit Breaker Implementation**: Database operations protected by circuit breaker patterns
- **Process Lifecycle Management**: Proper startup/shutdown sequencing with health checks
- **WebSocket Authority Enforcement**: WebSocket-only price feeds with REST fallback disabled
- **Error Recovery Mechanisms**: Automatic retry with exponential backoff for failed operations

### ✅ Data Quality Validation & Error Handling (Jan 2026)
- **Enhanced Data Validation**: Comprehensive validation for market data and signals
- **Error Handling Improvements**: Better API error management with user-friendly messages
- **Data Quality Checks**: Real-time data integrity monitoring and alerts
- **WebSocket Connection Management**: Robust reconnection handling with connection pooling
- **Database Resilience**: Circuit breaker protection for database operations
- **Market Data Caching**: Optimized caching with cache invalidation strategies

### ✅ Real-Time Monitoring & Alerting System (Jan 2026)
- **Comprehensive Monitoring System**: `MonitoringSystem` class with health checks, 
