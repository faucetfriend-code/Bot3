# Trading Bot Playwright E2E Tests

Comprehensive end-to-end tests for the trading bot interface using Playwright.

## Test Structure

### Core Test Files

- **`bot-controls.spec.ts`** - Main bot start/stop button functionality tests
- **`websocket-updates.spec.ts`** - WebSocket real-time update testing
- **`api-integration.spec.ts`** - API endpoint integration and error handling
- **`performance-monitoring.spec.ts`** - Performance benchmarks and monitoring

### Test Categories

#### Bot Control Tests
- ✅ Interface loading with buttons visible
- ✅ Start/stop button API calls
- ✅ Success feedback and toast notifications
- ✅ Bot state changes after operations
- ✅ Error handling (server down, invalid responses, timeouts)
- ✅ Concurrent operations safety
- ✅ Cross-browser compatibility

#### WebSocket Tests
- ✅ Connection establishment
- ✅ Real-time bot status updates
- ✅ Message format validation
- ✅ Connection drop handling
- ✅ High-frequency message processing

#### API Integration Tests
- ✅ Health check endpoints
- ✅ Status endpoint validation
- ✅ Error response handling
- ✅ Rate limiting simulation
- ✅ Network failure scenarios
- ✅ Concurrent API calls
- ✅ Response data validation

#### Performance Tests
- ✅ Response time monitoring (< 2 seconds)
- ✅ Page load performance
- ✅ Memory usage tracking
- ✅ WebSocket processing performance
- ✅ Database query performance
- ✅ UI responsiveness under load
- ✅ Resource cleanup verification

## High-Stakes Scenarios Tested

### API Failures
- Server 500 errors
- Service unavailable (503)
- Rate limiting (429)
- Authentication failures (403)
- Database connection issues
- External API failures

### Async Race Conditions
- Concurrent start/stop operations
- Rapid button clicking
- Multiple API calls in flight
- WebSocket message storms

### Network Issues
- Connection timeouts
- Network failures
- Invalid JSON responses
- Malformed API responses
- WebSocket disconnections

## Setup and Installation

### Prerequisites
- Node.js 16+
- Python 3.8+
- Trading bot server running

### Installation
```bash
# Install dependencies
npm install

# Install Playwright browsers
npm run install-browsers
```

### Environment Setup
The tests automatically start the FastAPI server programmatically. Ensure:

1. `trading_bot_v2/api_server.py` exists
2. Required environment variables are set (or use test defaults)
3. Database is accessible (tests use in-memory SQLite)

## Running Tests

### All Tests
```bash
npm test
```

### Specific Test File
```bash
npx playwright test bot-controls.spec.ts
```

### With Browser UI
```bash
npm run test:ui
```

### Headed Mode (visible browser)
```bash
npm run test:headed
```

### Debug Mode
```bash
npm run test:debug
```

### Generate Report
```bash
npm run report
```

## Test Configuration

### Server Management
- Tests automatically start/stop the FastAPI server
- Uses test environment variables
- In-memory database for isolation
- 30-second startup timeout

### Browser Configuration
- Runs on Chromium, Firefox, WebKit
- Mobile viewport testing included
- Screenshot/video capture on failures
- Trace collection for debugging

### API Mocking
- Selective API route mocking for controlled testing
- Real API calls for integration tests
- Error scenario simulation
- Performance delay simulation

## Performance Benchmarks

### Response Time Requirements
- Page load: < 3 seconds
- API response: < 500ms
- Button click: < 1.5 seconds
- UI update: < 500ms
- Total operation: < 2 seconds

### Resource Usage Limits
- Memory increase: < 50MB per test session
- WebSocket processing: < 100ms average
- Database queries: < 300ms average

## Test Data and Fixtures

### Mock API Responses
- Success scenarios with proper data structure
- Error scenarios with appropriate HTTP status codes
- Invalid response formats for robustness testing
- Timeout simulation for reliability testing

### WebSocket Messages
- Bot status update messages
- Real-time trade notifications
- Error broadcast messages
- Connection health messages

## Debugging Failed Tests

### Common Issues
1. **Server startup timeout**: Check Python environment and dependencies
2. **API connection failures**: Verify server is running on localhost:8000
3. **WebSocket connection issues**: Check if WebSocket endpoints are implemented
4. **Button not found**: Verify interface.html structure matches selectors

### Debug Tools
- Use `npm run test:debug` for step-through debugging
- Check `playwright-report/index.html` for detailed failure analysis
- Enable `DEBUG=pw:api` for API call logging
- Use `page.pause()` in test code for manual inspection

## CI/CD Integration

### GitHub Actions Example
```yaml
- name: Install Playwright
  run: npm ci
- name: Install browsers
  run: npx playwright install
- name: Run tests
  run: npm test
- name: Upload report
  uses: actions/upload-artifact@v3
  if: always()
  with:
    name: playwright-report
    path: playwright-report/
```

## Extending Tests

### Adding New Test Cases
1. Create new `.spec.ts` file in `tests/` directory
2. Follow existing patterns for server management
3. Use descriptive test names with scenario details
4. Include performance assertions where applicable

### Custom Fixtures
```typescript
test.extend({
  customFixture: async ({ page }, use) => {
    // Setup
    const customData = await setupCustomData();
    await use(customData);
    // Teardown
    await cleanupCustomData(customData);
  }
});
```

## Troubleshooting

### Server Won't Start
- Check Python path and virtual environment
- Verify all dependencies are installed
- Check for port conflicts on 8000
- Review server logs in test output

### Tests Time Out
- Increase timeout in `playwright.config.ts`
- Check for hanging async operations
- Verify API endpoints are responding
- Debug with `page.pause()` in test

### WebSocket Tests Fail
- WebSocket implementation might be optional
- Check if server supports WebSocket connections
- Verify WebSocket URL and protocol
- Some tests may need to be skipped if WS not available

## Contributing

1. Follow existing code patterns and naming conventions
2. Add comprehensive error scenarios
3. Include performance monitoring
4. Update this README for new test categories
5. Ensure cross-browser compatibility