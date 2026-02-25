# Testing Infrastructure

<!--
RAG Metadata:
- Category: Testing
- Tags: pytest, playwright, e2e, unit-tests, integration, coverage, performance
- Related: 08-web-interface-api, 01-core-trading-logic
-->

## Overview

The testing infrastructure includes:
1. **Unit Tests** - pytest for component testing
2. **Integration Tests** - API and database testing
3. **E2E Tests** - Playwright for web interface testing
4. **Performance Tests** - Benchmarking critical paths
5. **Health Check Tests** - System validation

---

## Test Commands

### Unit/Integration Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest trading_bot_v2/tests/test_hub_system.py

# Run specific test
pytest trading_bot_v2/tests/test_hub_system.py::TestHubSystem::test_data_hub_initialization

# Run with coverage
pytest --cov=. --cov-report=html

# Run with verbose output
pytest -v

# Run performance tests
pytest trading_bot_v2/tests/test_performance.py -v

# Run with markers
pytest -m "not slow"  # Skip slow tests
pytest -m "integration"  # Only integration tests
```

### E2E Tests (Playwright)

```bash
# Run all E2E tests
npm test

# Run with UI
npm run test:ui

# Run headed (visible browser)
npm run test:headed

# Debug mode
npm run test:debug

# Generate report
npm run report
```

### Linting & Type Checking

```bash
# Lint with ruff
ruff check .

# Auto-fix linting issues
ruff check . --fix

# Format with black
black .

# Type check with mypy
mypy .
```

---

## Unit Test Structure

**Location**: `trading_bot_v2/tests/`

### Test Files

| File | Purpose |
|------|---------|
| `test_hub_system.py` | DataHub and ConnectionManager tests |
| `test_performance.py` | Performance benchmarks |
| `test_market_regime.py` | Regime detection tests |
| `test_grid_lifecycle.py` | Grid state machine tests |
| `test_websocket_authority.py` | WebSocket client tests |
| `test_risk_manager.py` | Risk management tests |
| `test_strategies.py` | Strategy unit tests |
| `test_database.py` | Database operations tests |

### Test Patterns

```python
import pytest
from unittest.mock import Mock, AsyncMock, patch
from trading_bot_v2.hub_system import DataHub

class TestHubSystem:
    """Tests for Hub System components."""
    
    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        return Mock()
    
    @pytest.fixture
    def mock_database(self):
        """Create mock database."""
        db = AsyncMock()
        db.get_positions = AsyncMock(return_value=[])
        return db
    
    @pytest.fixture
    def data_hub(self, mock_config, mock_database):
        """Create DataHub instance."""
        return DataHub(
            config=mock_config,
            database=mock_database,
            trading_bot=Mock(),
            risk_manager=Mock()
        )
    
    @pytest.mark.asyncio
    async def test_data_hub_initialization(self, data_hub):
        """Test DataHub initializes correctly."""
        assert data_hub._cache_lock is not None
        assert data_hub._db_lock is not None
        assert data_hub._semaphore is not None
    
    @pytest.mark.asyncio
    async def test_get_positions_with_circuit_breaker(self, data_hub, mock_database):
        """Test position retrieval with circuit breaker protection."""
        # Normal operation
        positions = await data_hub.get_positions()
        assert positions == []
        
        # Simulate failures
        mock_database.get_positions.side_effect = Exception("DB Error")
        
        for _ in range(5):
            try:
                await data_hub.get_positions()
            except:
                pass
        
        # Circuit breaker should be open
        assert data_hub._db_circuit_breaker_failures >= 5
```

### Async Test Pattern

```python
@pytest.mark.asyncio
async def test_async_operation():
    """Test async function."""
    result = await some_async_function()
    assert result is not None
```

### Mock Pattern

```python
from unittest.mock import Mock, AsyncMock, patch

def test_with_mock():
    """Test with mock object."""
    mock_client = Mock()
    mock_client.get_balance.return_value = 1000.0
    
    result = some_function(mock_client)
    assert result == expected_value

@pytest.mark.asyncio
async def test_with_async_mock():
    """Test with async mock."""
    mock_client = AsyncMock()
    mock_client.get_positions.return_value = []
    
    result = await async_function(mock_client)
    assert result == []
```

---

## E2E Test Structure

**Location**: `tests/`

### Test Files

| File | Purpose |
|------|---------|
| `bot-controls.spec.ts` | Bot start/stop controls |
| `api-integration.spec.ts` | API endpoint testing |
| `websocket-updates.spec.ts` | WebSocket real-time updates |
| `performance-monitoring.spec.ts` | Performance monitoring |

### Playwright Configuration

**Location**: `playwright.config.ts`

```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  
  use: {
    baseURL: 'http://localhost:8000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],

  webServer: {
    command: 'cd trading_bot_v2 && uvicorn api_server:app --port 8000',
    url: 'http://localhost:8000',
    reuseExistingServer: !process.env.CI,
  },
});
```

### E2E Test Example

```typescript
// tests/bot-controls.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Bot Controls', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should display bot status', async ({ page }) => {
    const statusElement = page.locator('#status');
    await expect(statusElement).toBeVisible();
  });

  test('should start bot when clicking start button', async ({ page }) => {
    const startBtn = page.locator('#startBtn');
    const statusBadge = page.locator('#status');
    
    await startBtn.click();
    
    // Wait for status to change
    await expect(statusBadge).toHaveText(/Running/i, { timeout: 10000 });
  });

  test('should stop bot when clicking stop button', async ({ page }) => {
    const stopBtn = page.locator('#stopBtn');
    const statusBadge = page.locator('#status');
    
    // Ensure bot is running first
    await page.locator('#startBtn').click();
    await expect(statusBadge).toHaveText(/Running/i);
    
    // Stop bot
    await stopBtn.click();
    await expect(statusBadge).toHaveText(/Stopped/i, { timeout: 10000 });
  });
});
```

### WebSocket Test Example

```typescript
// tests/websocket-updates.spec.ts
import { test, expect } from '@playwright/test';

test.describe('WebSocket Updates', () => {
  test('should receive position updates via WebSocket', async ({ page }) => {
    // Listen for WebSocket messages
    const wsMessages: any[] = [];
    page.on('websocket', ws => {
      ws.on('framesreceived', frames => {
        const data = JSON.parse(frames[0].payload as string);
        wsMessages.push(data);
      });
    });

    await page.goto('/');
    
    // Wait for WebSocket connection
    await page.waitForTimeout(2000);
    
    // Verify messages are being received
    expect(wsMessages.length).toBeGreaterThan(0);
  });
});
```

---

## Performance Tests

**Location**: `trading_bot_v2/tests/test_performance.py`

### Performance Benchmarks

```python
import pytest
import time
from trading_bot_v2.indicators import calculate_rsi, calculate_atr, calculate_adx

class TestPerformance:
    """Performance benchmarks for critical paths."""
    
    @pytest.mark.performance
    def test_rsi_calculation_performance(self):
        """RSI calculation should be under 10ms for 1000 candles."""
        prices = [100.0 + (i % 10) for i in range(1000)]
        
        start = time.perf_counter()
        for _ in range(100):
            rsi = calculate_rsi(prices, period=14)
        elapsed = time.perf_counter() - start
        
        avg_time_ms = (elapsed / 100) * 1000
        assert avg_time_ms < 10, f"RSI calculation too slow: {avg_time_ms}ms"
    
    @pytest.mark.performance
    def test_regime_detection_performance(self):
        """Regime detection should be under 50ms."""
        market_data = {
            'high': [100.0 + (i % 5) for i in range(200)],
            'low': [100.0 - (i % 5) for i in range(200)],
            'close': [100.0 + (i % 3) for i in range(200)],
        }
        
        detector = MarketRegimeDetector()
        
        start = time.perf_counter()
        for _ in range(50):
            regime = detector.detect_regime(market_data)
        elapsed = time.perf_counter() - start
        
        avg_time_ms = (elapsed / 50) * 1000
        assert avg_time_ms < 50, f"Regime detection too slow: {avg_time_ms}ms"
    
    @pytest.mark.performance
    def test_signal_generation_performance(self):
        """Signal generation should be under 100ms per symbol."""
        # ... benchmark signal generation
```

---

## Coverage Requirements

### Minimum Coverage

| Component | Minimum Coverage |
|-----------|-----------------|
| Core Logic | 80% |
| Strategies | 85% |
| Risk Manager | 90% |
| Database | 75% |
| API Server | 70% |

### Running Coverage

```bash
# Generate coverage report
pytest --cov=trading_bot_v2 --cov-report=html --cov-report=term

# View HTML report
open htmlcov/index.html  # Mac/Linux
start htmlcov/index.html # Windows
```

### Coverage Configuration

**Location**: `pyproject.toml` or `.coveragerc`

```toml
[tool.coverage.run]
source = ["trading_bot_v2"]
omit = [
    "*/tests/*",
    "*/__pycache__/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
]
fail_under = 80
```

---

## Health Check Tests

**Location**: `trading_bot_v2/scripts/health_check.py`

### Purpose
Comprehensive system validation for production monitoring.

```python
#!/usr/bin/env python
"""Health check script for Trading Bot v3."""

import asyncio
import sys
from typing import Dict, List

async def run_health_checks() -> Dict[str, bool]:
    """Run all health checks."""
    results = {}
    
    # Database connectivity
    results['database'] = await check_database()
    
    # Exchange API connectivity
    results['exchange_api'] = await check_exchange_api()
    
    # WebSocket connectivity
    results['websocket'] = await check_websocket()
    
    # Configuration validity
    results['config'] = check_config()
    
    return results

async def check_database() -> bool:
    """Check database connectivity."""
    try:
        from trading_bot_v2.database import DatabaseManager
        db = DatabaseManager()
        db.get_positions()
        return True
    except Exception as e:
        print(f"Database check failed: {e}")
        return False

async def check_exchange_api() -> bool:
    """Check exchange API connectivity."""
    try:
        from trading_bot_v2.pacifica_client import PacificaClient
        client = PacificaClient(testnet=True)
        # Make simple API call
        return True
    except Exception as e:
        print(f"Exchange API check failed: {e}")
        return False

def check_config() -> bool:
    """Check configuration validity."""
    from trading_bot_v2.config import config
    required = ['agent_wallet_private_key', 'account_public_key']
    return all(getattr(config, attr, None) for attr in required)

if __name__ == "__main__":
    results = asyncio.run(run_health_checks())
    
    all_healthy = all(results.values())
    
    print("Health Check Results:")
    for check, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {check}: {status}")
    
    sys.exit(0 if all_healthy else 1)
```

---

## Test Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Testing Infrastructure                          │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        E2E Tests (Playwright)                        │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────┐  │
│  │  Bot Controls   │ │  API Integration │ │  WebSocket Updates  │  │
│  │  (Chromium)     │ │  (Firefox)       │ │  (WebKit)          │  │
│  └─────────────────┘ └─────────────────┘ └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Integration Tests                              │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────┐  │
│  │  API Endpoints  │ │  Database Ops   │ │  Exchange Client    │  │
│  └─────────────────┘ └─────────────────┘ └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          Unit Tests                                  │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────┐  │
│  │  Strategies     │ │  Risk Manager   │ │  Hub System         │  │
│  │  Indicators     │ │  Grid Lifecycle │ │  Regime Detector    │  │
│  └─────────────────┘ └─────────────────┘ └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Performance Tests                               │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Indicator benchmarks | Signal generation | Memory usage     │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `test_hub_system.py` | ~300 | Hub system tests |
| `test_performance.py` | ~200 | Performance benchmarks |
| `test_strategies.py` | ~400 | Strategy unit tests |
| `bot-controls.spec.ts` | ~150 | E2E bot controls |
| `health_check.py` | ~100 | System validation |

---

## Related Reports

- [08-web-interface-api.md](./08-web-interface-api.md) - API testing
- [01-core-trading-logic.md](./01-core-trading-logic.md) - Component testing
- [09-configuration-setup.md](./09-configuration-setup.md) - Test configuration
