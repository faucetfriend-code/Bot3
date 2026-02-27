# Project Plan: Trading Bot v2 Rebuild

## Executive Summary

This plan outlines the complete rebuild of the trading bot system based on the anewbeginning.md specification. The goal is to create a streamlined, functional system that eliminates 80% of current complexity while maintaining 100% of essential features. The rebuild prioritizes immediate working functionality over future extensibility, with all architecture finalized before implementation begins. Tasks are broken into small, manageable steps to prevent errors and ensure incremental progress.

## System Architecture

### Component Structure
```
trading_bot_v2/
├── config.py              # Simple configuration (50 lines)
├── database.py            # Basic SQLite operations (200 lines)
├── api_server.py         # Focused FastAPI app (300 lines)
├── trading_bot.py         # Core trading logic (200 lines)
├── pacifica_client.py     # Streamlined exchange client (150 lines)
├── interface.html         # Basic web interface (200 lines)
├── requirements.txt       # Minimal dependencies (7 lines)
└── README.md              # Simple setup instructions
```

### Design Principles
1. **Single Responsibility**: Each module has one clear purpose
2. **Direct Dependencies**: No complex initialization chains
3. **Synchronous Operations**: Simpler than async where possible
4. **Environment-Based Config**: Secrets from environment variables
5. **Immediate Startup**: No lazy loading or complex setup
6. **Fail Fast**: Clear error messages, no silent failures

### Data Flow
1. Configuration loads from environment variables
2. Database initializes SQLite tables on startup
3. API server starts FastAPI app with CORS
4. Trading bot connects to Pacifica client
5. Web interface serves from root endpoint
6. All components communicate through direct method calls

## Technology Stack
- **Backend**: Python 3.8+
- **Web Framework**: FastAPI with Uvicorn
- **Database**: SQLite for simplicity
- **API Client**: Requests library for HTTP calls
- **Authentication**: HMAC signatures for Pacifica API
- **Frontend**: Vanilla HTML/CSS/JavaScript
- **Dependencies**: 7 core packages (fastapi, uvicorn, pydantic, python-dotenv, requests, cryptography, solders)

## API Specifications

### Pacifica Exchange API Integration

All Pacifica API calls use HMAC-SHA256 authentication with the following headers:
- `X-API-Key`: Public key from config.pacifica_public_key
- `X-Timestamp`: Unix timestamp in milliseconds (int(time.time() * 1000))
- `X-Signature`: HMAC signature of "METHOD/endpoint/body/timestamp" using private key

#### 1. Get Account Balance
- **Endpoint**: `/account`
- **Method**: GET
- **Headers**: X-API-Key, X-Timestamp, X-Signature
- **Request Body**: None
- **Response Format**:
```json
{
  "balance": "1234.56",
  "available": "1234.56",
  "locked": "0.00"
}
```
- **Error Responses**:
  - 401: {"error": "Invalid signature"}
  - 429: {"error": "Rate limit exceeded"}
- **Rate Limit**: 10 requests per second

#### 2. Get Current Positions
- **Endpoint**: `/positions`
- **Method**: GET
- **Headers**: X-API-Key, X-Timestamp, X-Signature
- **Request Body**: None
- **Response Format**:
```json
{
  "data": [
    {
      "symbol": "BTC/USD",
      "side": "long",
      "quantity": 0.1,
      "entry_price": 45000.00,
      "current_price": 46000.00,
      "unrealized_pnl": 100.00
    }
  ]
}
```
- **Error Responses**:
  - 401: {"error": "Invalid signature"}
  - 429: {"error": "Rate limit exceeded"}
- **Rate Limit**: 10 requests per second

#### 3. Place Order
- **Endpoint**: `/orders`
- **Method**: POST
- **Headers**: X-API-Key, X-Timestamp, X-Signature, Content-Type: application/json
- **Request Body**:
```json
{
  "symbol": "BTC/USD",
  "side": "buy",
  "quantity": 0.1,
  "type": "market",
  "price": null
}
```
- **Response Format**:
```json
{
  "order_id": "12345",
  "symbol": "BTC/USD",
  "side": "buy",
  "quantity": 0.1,
  "price": 45000.00,
  "status": "filled"
}
```
- **Error Responses**:
  - 400: {"error": "Invalid order parameters"}
  - 401: {"error": "Invalid signature"}
  - 429: {"error": "Rate limit exceeded"}
  - 422: {"error": "Insufficient balance"}
- **Rate Limit**: 5 requests per second

#### 4. Cancel Order
- **Endpoint**: `/orders/{order_id}`
- **Method**: DELETE
- **Headers**: X-API-Key, X-Timestamp, X-Signature
- **Request Body**: None
- **Response Format**:
```json
{
  "order_id": "12345",
  "status": "cancelled"
}
```
- **Error Responses**:
  - 401: {"error": "Invalid signature"}
  - 404: {"error": "Order not found"}
  - 429: {"error": "Rate limit exceeded"}
- **Rate Limit**: 5 requests per second

#### 5. Get Market Data
- **Endpoint**: `/markets/{symbol}`
- **Method**: GET
- **Headers**: X-API-Key, X-Timestamp, X-Signature
- **Request Body**: None
- **Response Format**:
```json
{
  "symbol": "BTC/USD",
  "price": 45000.00,
  "volume": 100.5,
  "bid": 44999.00,
  "ask": 45001.00
}
```
- **Error Responses**:
  - 401: {"error": "Invalid signature"}
  - 404: {"error": "Market not found"}
  - 429: {"error": "Rate limit exceeded"}
- **Rate Limit**: 20 requests per second

#### 6. Get All Markets
- **Endpoint**: `/markets`
- **Method**: GET
- **Headers**: X-API-Key, X-Timestamp, X-Signature
- **Request Body**: None
- **Response Format**:
```json
{
  "data": [
    {
      "symbol": "BTC/USD",
      "base": "BTC",
      "quote": "USD",
      "price": 45000.00,
      "volume": 100.5
    }
  ]
}
```
- **Error Responses**:
  - 401: {"error": "Invalid signature"}
  - 429: {"error": "Rate limit exceeded"}
- **Rate Limit**: 10 requests per second

### Internal API Server Endpoints

#### 1. Serve Web Interface
- **Endpoint**: `/`
- **Method**: GET
- **Response**: HTML content from interface.html file
- **Content-Type**: text/html

#### 2. Get System Status
- **Endpoint**: `/api/status`
- **Method**: GET
- **Response Format**:
```json
{
  "success": true,
  "data": {
    "bot_running": true,
    "positions_count": 2,
    "trades_count": 15,
    "total_pnl": 250.75
  }
}
```

#### 3. Get Trade History
- **Endpoint**: `/api/trades`
- **Method**: GET
- **Query Parameters**: limit (optional, default 100)
- **Response Format**:
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "symbol": "BTC/USD",
      "side": "buy",
      "quantity": 0.1,
      "entry_price": 45000.00,
      "exit_price": 46000.00,
      "entry_time": "2024-01-01T10:00:00",
      "exit_time": "2024-01-01T11:00:00",
      "pnl": 100.00,
      "status": "closed"
    }
  ]
}
```

#### 4. Get Current Positions
- **Endpoint**: `/api/positions`
- **Method**: GET
- **Response Format**:
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "symbol": "BTC/USD",
      "side": "long",
      "quantity": 0.1,
      "entry_price": 45000.00,
      "current_price": 46000.00,
      "unrealized_pnl": 100.00,
      "opened_at": "2024-01-01T10:00:00",
      "updated_at": "2024-01-01T10:30:00"
    }
  ]
}
```

#### 5. Start Trading Bot
- **Endpoint**: `/api/bot/start`
- **Method**: POST
- **Request Body**: None
- **Response Format**:
```json
{
  "success": true,
  "message": "Bot started"
}
```

#### 6. Stop Trading Bot
- **Endpoint**: `/api/bot/stop`
- **Method**: POST
- **Request Body**: None
- **Response Format**:
```json
{
  "success": true,
  "message": "Bot stopped"
}
```

#### 7. Get Available Markets
- **Endpoint**: `/api/markets`
- **Method**: GET
- **Response Format**:
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTC/USD",
      "base": "BTC",
      "quote": "USD",
      "price": 45000.00,
      "volume": 100.5
    }
  ]
}
```

## Implementation Phases

### Phase 1: Core Infrastructure (1 day)
Focus: Basic configuration

### Phase 2: Trading Core (1-2 days)
Focus: Exchange integration and trading logic

### Phase 3: API Server & Interface (1 day)
Focus: Web interface and API endpoints

### Phase 3.5: Database Configuration & Testing (1-2 days)
Focus: API-driven database schema design and testing

### Phase 4: Dependencies & Deployment (0.5 days)
Focus: Packaging and documentation

## Task Details

### Phase 1: Core Infrastructure

#### 1.1 Create Project Directory Structure
- Create `trading_bot_v2/` directory
- Create empty files for all 8 components
- Verify directory structure matches architecture

#### 1.2 Implement Simple Configuration (config.py)
- Create Config class with __init__ method
- Add environment variable loading for database, Pacifica API, trading parameters, logging
- Implement @property for pacifica_base_url
- Add validate() method for required credentials
- Create global config instance

### Phase 2: Trading Core

#### 2.1 Implement Streamlined Pacifica Client (pacifica_client.py)
- Import required modules (requests, hmac, hashlib, time, typing)
- Create PacificaClient class with __init__ method (private_key, public_key, testnet)
- Implement _sign_request method: generate HMAC-SHA256 signature for "METHOD/endpoint/body/timestamp"
- Implement _make_request method: make authenticated HTTP request with X-API-Key, X-Timestamp, X-Signature headers
- Implement get_balance method: GET /account, return balance dict
- Implement get_positions method: GET /positions, return data array from response
- Implement place_order method: POST /orders with symbol, side, quantity, type, price; return order dict
- Implement cancel_order method: DELETE /orders/{order_id}, return cancellation dict
- Implement get_market_data method: GET /markets/{symbol}, return market data dict
- Implement get_markets method: GET /markets, return data array from response
- Add error handling for 401 (auth), 429 (rate limit), 4xx/5xx (API errors)

#### 2.2 Implement Core Trading Logic (trading_bot.py)
- Import required modules (time, logging, typing, config, database, pacifica_client)
- Create TradingBot class with __init__ method (initialize db and client)
- Implement start method: set is_running=True, log start, begin trading loop in background thread
- Implement stop method: set is_running=False, log stop
- Implement _trading_loop method: while running, call _update_positions, _check_signals, _monitor_risk, sleep 60s
- Implement _update_positions method: call client.get_positions(), update db for each position, calculate pnl
- Implement _check_signals method: call client.get_markets(), check first 5 markets, random buy/sell signals (5% chance each)
- Implement _execute_signal method: check position limits, call client.place_order(), save trade to db
- Implement _monitor_risk method: calculate total pnl, warn if > $1000 loss
- Implement get_status method: return dict with is_running, positions_count, recent_trades, total_pnl

### Phase 3: API Server & Interface

#### 3.1 Implement Focused API Server (api_server.py)
- Import required modules (fastapi, CORSMiddleware, uvicorn, typing, logging, config, database, trading_bot)
- Initialize FastAPI app with title "Trading Bot API v2"
- Initialize Database() and TradingBot() instances
- Add CORSMiddleware with allow_origins=["*"], allow all methods and headers
- Configure logging with logging.basicConfig(level=getattr(logging, config.log_level))
- Implement serve_interface endpoint: GET /, read interface.html, return HTMLResponse
- Implement get_status endpoint: GET /api/status, return success/data with bot_running, positions_count, trades_count, total_pnl
- Implement get_trades endpoint: GET /api/trades?limit=100, call db.get_trades(limit), return success/data
- Implement get_positions endpoint: GET /api/positions, call db.get_positions(), return success/data
- Implement start_bot endpoint: POST /api/bot/start, if not running start in thread, return success/message
- Implement stop_bot endpoint: POST /api/bot/stop, call bot.stop(), return success/message
- Implement get_markets endpoint: GET /api/markets, call bot.client.get_markets(), return success/data
- Add if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)

#### 3.2 Create Minimal Web Interface (interface.html)
- Create HTML5 document with head, title "Trading Bot v2", viewport meta
- Add CSS: body font Arial, .status padding 10px border-radius 5px, .running green, .stopped red, button padding 10px, table collapse borders
- Create status div with id="status", span id="bot-status", p elements for positions, trades, pnl
- Add controls div with Start Bot, Stop Bot, Refresh buttons with onclick handlers
- Create positions table with thead: Symbol, Side, Quantity, Entry Price, Current Price, P&L
- Create trades table with thead: Symbol, Side, Quantity, Entry Price, Exit Price, P&L, Time
- Add JavaScript: const API_BASE = '/api', apiCall function with fetch and error handling
- Implement updateStatus: call /api/status, update bot-status class and text, update counts and pnl
- Implement updatePositions: call /api/positions, populate tbody with symbol, side, quantity, entry_price, current_price, unrealized_pnl
- Implement updateTrades: call /api/trades?limit=10, populate tbody with symbol, side, quantity, entry_price, exit_price, pnl, entry_time
- Implement refreshData: await all update functions
- Implement startBot: POST /api/bot/start, alert success/error, refresh after 1s
- Implement stopBot: POST /api/bot/stop, alert success/error, refresh after 1s
- Add window.onload: refreshData(), setInterval(refreshData, 30000)

### Phase 3.5: Database Configuration & Testing

#### 3.5.1 Test API Server with Mock Data
- Start the API server with mock/placeholder database calls
- Test all endpoints return valid JSON structures
- Verify API server starts without database dependencies
- Document the exact data structures expected from API responses

#### 3.5.2 Analyze API Response Structures
- Run API calls to Pacifica (if credentials available) or use documented response formats
- Examine the structure of position data, trade data, market data
- Identify all fields and data types from real API responses
- Note any variations in response formats for different scenarios

#### 3.5.3 Design Database Schema Based on API Data
- Create database tables that match the exact structure of API responses
- Ensure all fields from API responses have corresponding database columns
- Add appropriate data types (TEXT, REAL, INTEGER) for each field
- Include indexes for frequently queried fields

#### 3.5.4 Implement Basic Database Layer (database.py)
- Import required modules (sqlite3, json, datetime, typing)
- Create Database class with __init__ and init_db methods
- Implement trades table creation based on API trade data structure
- Implement positions table creation based on API position data structure
- Implement market_data table creation based on API market data structure
- Add save_trade method with parameters matching API trade format
- Add get_trades method returning data in API-compatible format
- Add get_positions method returning data in API-compatible format
- Add update_position method with parameters matching API position updates
- Add save_market_data method with parameters matching API market data

#### 3.5.5 Test Database Operations
- Insert sample data matching API response formats
- Test retrieval operations return data in expected structures
- Verify data types are preserved correctly
- Test error handling for invalid data

#### 3.5.6 Integrate Database with API Server
- Update API server to use real database instead of mocks
- Test all endpoints with database persistence
- Verify data flows correctly from API responses to database storage
- Confirm database queries return data in API response formats

### Phase 4: Dependencies & Deployment

#### 4.1 Create Minimal Requirements (requirements.txt)
- fastapi==0.104.1
- uvicorn==0.24.0
- pydantic==2.5.0
- python-dotenv==1.0.0
- requests==2.31.0
- cryptography==41.0.7
- solders==0.20.4

#### 4.2 Write Setup Instructions (README.md)
- Title: # Trading Bot v2
- Description: A streamlined, functional trading bot for Pacifica.fi.
- Setup section: 1. Install: pip install -r requirements.txt, 2. Configure .env with AGENT_WALLET_PRIVATE_KEY, ACCOUNT_PUBLIC_KEY, TESTNET=true, DATABASE_PATH=trading_bot.db, MAX_POSITIONS=5, DEFAULT_LEVERAGE=10, MAX_RISK_PER_TRADE=0.02, LOG_LEVEL=INFO, 3. Run: python api_server.py, 4. Open http://localhost:8000
- Features: Simple web interface, basic trading bot, position management, SQLite database, Pacifica API integration, start/stop controls
- Development: config.py (config), database.py (db ops), api_server.py (web API), trading_bot.py (logic), pacifica_client.py (exchange client)

## Risk Mitigation

### Small Task Approach
- Each task focuses on one specific function or component
- Tasks can be completed in 15-30 minutes each
- Immediate testing possible after each task
- Errors isolated to individual functions
- Easy rollback by reverting single changes

### Architecture Finalization
- Complete system design reviewed before coding
- All component interfaces defined upfront
- Data flow mapped out completely
- No architectural changes during implementation
- Consistency ensured across all features

### Incremental Validation
- Test each component independently
- Validate API endpoints individually
- Check database operations separately
- Verify frontend-backend integration last
- Full system testing before deployment

## Success Criteria

### Technical Metrics
- Startup time: < 1 second
- Memory usage: < 100MB
- Lines of code: ~1000 total
- Dependencies: 7 core packages
- No complex async patterns

### Functional Metrics
- Core features working: 100% (trading, monitoring, data persistence)
- API response time: < 100ms for status endpoints
- Database query time: < 50ms for common operations
- Error rate: < 1% during normal operation
- All endpoints return valid JSON

### User Experience Metrics
- Interface load time: < 2 seconds
- Real-time updates: 30-second refresh
- Error messages: Clear and actionable
- Learning curve: < 30 minutes for basic operation
- No JavaScript errors in console

## Implementation Order Rationale

The phases build sequentially with clear dependencies:
- Infrastructure first (config only) - foundation for everything
- Trading core second (client, bot) - depends on config, uses mock data initially
- API/interface third - depends on trading core, provides endpoints for testing
- Database configuration (3.5) - uses real API responses to design perfect schema
- Deployment last - packages the complete system

This order ensures the database schema is designed based on actual API data structures, preventing mismatches and data loss. The pause in Phase 3.5 allows for manual review and adjustment of the database design based on real-world API responses.

## Next Steps

After completing this plan, proceed to Phase 1 implementation. Each task should be marked complete only after:
1. Code written and syntax validated
2. Basic functionality tested
3. No import or runtime errors
4. Integration with existing components verified

Note: Phase 3.5 includes a deliberate pause for API testing and database schema refinement. Do not rush through this phase - take time to analyze real API responses and ensure the database perfectly matches the data structures.

The small task size ensures that any issues are immediately identifiable and fixable, preventing the accumulation of technical debt that plagued the original system.