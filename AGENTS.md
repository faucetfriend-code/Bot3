# Agent Guidelines for Trading Bot v2

## Build/Lint/Test Commands

### Core Development Commands
- **Lint (Modern)**: `ruff check .` (Python linting with auto-fix - preferred)
- **Lint (Legacy)**: `flake8 .` (Traditional Python linting)
- **Type Check**: `mypy .` (Python type checking)
- **Format**: `black .` (Python code formatting)
- **Format (Alt)**: `ruff format .` (Fast alternative to black)

### Testing Commands
- **All Tests**: `pytest` (run all tests)
- **Single Test**: `pytest path/to/test_file.py::TestClass::test_method`
- **Coverage**: `pytest --cov=. --cov-report=html`
- **Performance**: `pytest tests/test_performance.py -v`
- **E2E Tests**: `npm test` (from project root - Playwright tests)
- **API Testing**: `python test_api_endpoints.py` (from project root)
- **Health Check**: `python scripts/health_check.py` (comprehensive system validation)
- **Hub System Tests**: `pytest tests/test_hub_system.py` (component orchestration testing)

### Playwright E2E Testing
- **All E2E**: `npm test` (from project root)
- **Specific File**: `npx playwright test tests/bot-controls.spec.ts`
- **Headed Mode**: `npm run test:headed`
- **Debug Mode**: `npm run test:debug`
- **Test UI**: `npm run test:ui`
- **Install Browsers**: `npm run install-browsers`
- **View Report**: `npm run report`

## Code Style Guidelines

### Python Conventions
- **Imports**: Standard library → third-party → relative imports (alphabetized within groups)
- **Types**: Type hints required for all function parameters and return values
- **Naming**: PascalCase classes, snake_case functions/methods, UPPER_CASE constants
- **Error Handling**: Specific exception types in try/except blocks, avoid bare except
- **Async**: Use async/await for I/O operations, asyncio for concurrency
- **Docstrings**: Google/NumPy style docstrings for public functions/classes
- **Line Length**: 88 characters (Black default)
- **String Quotes**: Single quotes for code, double quotes for docstrings
- **Path Handling**: Use `pathlib.Path` instead of `os.path`

### Import Pattern Example
```python
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiosqlite
import fastapi
from pydantic import BaseModel

from core_logic.trading import TradingEngine
from .database import DatabaseManager
```

### Error Handling Pattern
```python
try:
    risky_operation()
except ValueError as e:
    logger.error(f"Value error in operation: {e}")
    handle_value_error(e)
except ConnectionError:
    logger.warning("Connection failed, retrying...")
    retry_connection()
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    raise
```

### General Patterns
- **Classes**: Inherit from ABC for abstract classes, use descriptive method names
- **Documentation**: Docstrings required for public methods/classes
- **Security**: Never expose secrets, use environment variables for sensitive data
- **Circuit Breaker**: Implement resilience patterns for external dependencies
- **Logging**: Use structured logging with appropriate log levels
- **Async Context**: Use `async with` for resource management

### Agent-Specific Guidelines
- **Generator**: Respect existing code style and context when generating code
- **Quality**: Focus on security, performance, and maintainability in reviews
- **Tester**: Write comprehensive pytest/playwright tests with edge cases
- **Security**: Run bandit and detect-secrets scans regularly
- **Documentation**: Update API docs and README when making changes

## Project-Specific Requirements

### Architecture Patterns
- **Core Logic Imports**: Use absolute imports from `core_logic` package
- **Component Interfaces**: Implement ABC-based interfaces for all major components
- **WebSocket Integration**: Follow hub system patterns for real-time communication
- **Hub System Patterns**: Use DataHub for data management, Event System for decoupled communication
- **Circuit Breaker Patterns**: Implement resilience patterns for external dependencies and database operations

### Testing Requirements
- **Coverage**: Maintain 80%+ code coverage with comprehensive E2E tests
- **Performance**: Monitor critical paths with dedicated performance tests
- **Monitoring**: Implement health checks and performance metrics for all components
- **E2E Scope**: Test bot controls, WebSocket updates, API integration, and performance

### Key Files and Directories
- **Main Bot**: `trading_bot_v2/main.py`
- **Database**: `trading_bot_v2/database.py` (SQLite with aiosqlite support)
- **API Server**: `trading_bot_v2/api_server.py` (FastAPI)
- **Web Interface**: `interface.html` (Bootstrap-based UI)
- **Tests**: `tests/` (Playwright E2E tests)
- **Scripts**: `scripts/` (Utility and maintenance scripts)

## Environment and Dependencies

### Python Dependencies
- Core: `requests`, `fastapi>=0.104.0`, `uvicorn>=0.24.0`, `pydantic>=2.5.0`
- Async: `aiosqlite>=0.20.0`
- Testing: `pytest>=7.4.0`, `pytest-playwright`
- Trading: `solders`, `base58>=2.1.0`
- Utilities: `python-dotenv`, `tenacity>=8.2.0`, `PyJWT>=2.8.0`, `loguru>=0.7.0`

### Configuration Files
- **Type Checking**: `pyrightconfig.json` (Python 3.8, basic mode)
- **Node/Playwright**: `package.json` for E2E testing
- **Python Project**: `pyproject.toml` with setuptools build system

### Development Environment
- **Python**: 3.8+ (specified in pyrightconfig.json)
- **Node.js**: 16+ for Playwright E2E tests
- **Database**: SQLite with async support via aiosqlite
- **Browser Testing**: Chromium, Firefox, WebKit via Playwright

## Critical Implementation Notes

### Database Patterns
- Use connection pooling and circuit breaker patterns
- Implement proper transaction management
- Include hourly funding tracking for Pacifica.fi integration
- Use DataCache for frequently accessed data with TTL

### WebSocket Integration
- Real-time price feeds with WebSocket-only mode (REST fallback disabled)
- Connection pooling with authentication and rate limiting
- Automatic reconnection with exponential backoff
- Level 2 orderbook data for advanced strategies

### Security Considerations
- Never commit secrets or API keys
- Use environment variables for sensitive configuration
- Implement proper error handling without exposing internal details
- Follow OWASP guidelines for API security

### Performance Requirements
- API response time: < 500ms
- Page load time: < 3 seconds
- WebSocket processing: < 100ms average
- Database queries: < 300ms average
- Memory usage: < 50MB increase per test session

This file provides comprehensive guidelines for agentic coding agents working on the trading bot v2 codebase, ensuring consistency, quality, and adherence to project-specific patterns and requirements.