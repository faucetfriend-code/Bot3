# Trading Bot Tests

This directory contains test files for the trading bot.

## Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_api_db.py

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=. tests/
```

## Test Files

- `test_api_db.py` - API and database integration tests
- `test_auth_*.py` - Authentication and authorization tests
- `test_ccxt_integration.py` - Exchange integration tests
- `test_config.py` - Configuration tests
- `test_error_handling.py` - Error handling tests
- `test_indicators.py` - Technical indicator tests
- `test_process_manager.py` - Process management tests

## Prerequisites

```bash
pip install pytest pytest-cov pytest-asyncio
```
