# HTML Interface Audit and Update

## Objective
Audit `trading_bot_interface.html` to ensure compatibility with recent API server improvements (Phase 6A/6B), identify any mismatches, and optimize frontend efficiency.

## Context
Recent API changes include:
- New `/api/metrics` endpoint for performance monitoring
- Pagination on `/api/trades` (limit, offset, symbol params)
- Circuit breaker status available
- WebSocket lifecycle management improvements
- Rate limiting on all endpoints

## Tasks

### 1. API Endpoint Compatibility Audit
Review all fetch calls in `trading_bot_interface.html` and verify:
- Endpoints still exist and return expected format
- New pagination parameters are used where available
- Error responses are handled correctly
- Rate limit errors (429) are handled gracefully

### 2. Error Handling Review
Check that the frontend:
- Displays meaningful error messages from API responses
- Handles network failures gracefully
- Shows loading states during API calls
- Doesn't crash on unexpected response formats

### 3. Efficiency Improvements
- Identify redundant API calls
- Check polling intervals are appropriate
- Verify caching is used where beneficial
- Ensure WebSocket is used instead of polling where possible

### 4. New Feature Integration
Consider adding:
- Performance metrics display (from `/api/metrics`)
- Pagination controls for trades table
- Circuit breaker status indicator

## Output
List of:
1. Breaking issues found (API mismatches)
2. Recommended improvements
3. Code changes needed
