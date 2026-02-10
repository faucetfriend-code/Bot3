# Database Structure Plan for Trading Bot v2 - Phase 3.5

## 1. API Response Analysis

### Positions Endpoint (/positions) - REAL DATA STRUCTURE
**Real API Response Structure (from Pacifica test network):**
```json
[
  {
    "symbol": "BTC",
    "side": "bid",
    "amount": "0.21157",
    "entry_price": "91246.722777",
    "margin": "0",
    "funding": "-39.402633",
    "isolated": false,
    "liquidation_price": "53013.745579",
    "created_at": 1765448050569,
    "updated_at": 1765749605994
  }
]
```

**Field Analysis (based on real data):**
- `symbol`: String (TEXT) - Trading pair symbol (e.g., "BTC", "LTC")
- `side`: String (TEXT) - "bid" (long position) or "ask" (short position)
- `amount`: String (TEXT) - Position size as string (convert to REAL for calculations)
- `entry_price`: String (TEXT) - Entry price as string (convert to REAL)
- `margin`: String (TEXT) - Margin used (convert to REAL)
- `funding`: String (TEXT) - Funding payments/fees (convert to REAL)
- `isolated`: Boolean - Whether position is isolated margin
- `liquidation_price`: String (TEXT) - Liquidation price as string (convert to REAL)
- `created_at`: Integer - Unix timestamp in milliseconds
- `updated_at`: Integer - Unix timestamp in milliseconds

### Trades Data Structure (Internal)
**Response Structure:**
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

**Field Analysis:**
- `id`: Integer (INTEGER) - Unique trade identifier
- `symbol`: String (TEXT) - Trading pair symbol
- `side`: String (TEXT) - "buy" or "sell"
- `quantity`: Float (REAL) - Trade quantity
- `entry_price`: Float (REAL) - Entry price
- `exit_price`: Float (REAL) - Exit price (nullable)
- `entry_time`: Timestamp (TEXT) - ISO format timestamp
- `exit_time`: Timestamp (TEXT) - ISO format timestamp (nullable)
- `pnl`: Float (REAL) - Realized profit/loss (nullable)
- `status`: String (TEXT) - "open", "closed", "cancelled"

### Market Data Endpoint (/markets/{symbol})
**Response Structure:**
```json
{
  "symbol": "BTC/USD",
  "price": 45000.00,
  "volume": 100.5,
  "bid": 44999.00,
  "ask": 45001.00
}
```

**Field Analysis:**
- `symbol`: String (TEXT) - Trading pair symbol
- `price`: Float (REAL) - Current price
- `volume`: Float (REAL) - Trading volume
- `bid`: Float (REAL) - Best bid price
- `ask`: Float (REAL) - Best ask price

### Markets List Endpoint (/markets)
**Response Structure:**
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

**Field Analysis:**
- `symbol`: String (TEXT) - Trading pair symbol
- `base`: String (TEXT) - Base currency
- `quote`: String (TEXT) - Quote currency
- `price`: Float (REAL) - Current price
- `volume`: Float (REAL) - Trading volume

## 2. Database Schema Design

### Positions Table (Updated for Real API Structure)
```sql
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('bid', 'ask')),
    amount TEXT NOT NULL,  -- Store as string to preserve precision
    entry_price TEXT NOT NULL,  -- Store as string to preserve precision
    margin TEXT NOT NULL,
    funding TEXT NOT NULL,
    isolated BOOLEAN NOT NULL,
    liquidation_price TEXT,
    created_at INTEGER NOT NULL,  -- Unix timestamp in milliseconds
    updated_at INTEGER NOT NULL   -- Unix timestamp in milliseconds
);
```

### Trades Table
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    pnl REAL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled'))
);
```

### Markets Table
```sql
CREATE TABLE markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT UNIQUE NOT NULL,
    base TEXT NOT NULL,
    quote TEXT NOT NULL,
    price REAL,
    volume REAL,
    bid REAL,
    ask REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Market Data History Table (Optional for time-series)
```sql
CREATE TABLE market_data_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    volume REAL,
    bid REAL,
    ask REAL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (symbol) REFERENCES markets(symbol)
);
```

## 3. Table Relationships

- **Positions**: Standalone table for current open positions
- **Trades**: Standalone table for trade history
- **Markets**: Master table for available trading pairs
- **Market Data History**: References markets table for historical price data

No direct foreign keys between positions and trades, as trades may span multiple positions or be independent.

## 4. Data Flow Mapping

### API Response to Database
- **Positions**: Direct mapping from `/positions` response array to positions table (real data structure)
- **Trades**: Internal trade records stored in trades table, not directly from API (structure based on planned format)
- **Markets**: `/markets` response data array maps to markets table (endpoints not verified)
- **Market Data**: `/markets/{symbol}` maps to market_data_history for historical tracking (endpoints not verified)

### Database to API Response
- **Positions**: Query positions table, convert to API format with proper data types and field names
- **Trades**: Query trades table with optional limit, format as `{"success": true, "data": [trade_dicts]}`
- **Markets**: Query markets table, format as `{"success": true, "data": [market_dicts]}`

## 5. Performance Considerations

### Indexing Strategy
```sql
CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_side ON positions(side);
CREATE INDEX idx_positions_created_at ON positions(created_at);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_entry_time ON trades(entry_time);
CREATE INDEX idx_markets_symbol ON markets(symbol);
CREATE INDEX idx_market_data_symbol_timestamp ON market_data_history(symbol, timestamp);
```

### Query Optimization
- Positions queries: Filter by symbol for specific pairs
- Trades queries: Order by entry_time DESC, limit for pagination
- Market data: Time-range queries for historical analysis
- Use WAL mode for concurrent reads/writes

## 6. Migration Strategy

### From Mock Data to Real Database
1. **Backup existing data** (if any mock data exists)
2. **Create tables** using the updated schema with real API field mappings
3. **Migrate positions** from real `/positions` API responses (Pacifica test network data)
4. **Initialize markets** from `/markets` endpoint (if available)
5. **Start logging trades** from order placements (structure based on planned format)
6. **Update positions** periodically from real `/positions` endpoint data

### Data Validation
- Ensure all required fields are present before insertion
- Validate data types match schema expectations
- Handle null values appropriately for optional fields

## 7. Error Handling

### Database Connection Errors
- Implement retry logic for transient failures
- Log connection issues with timestamps
- Graceful degradation to read-only mode if writes fail

### Data Integrity Issues
- Use transactions for multi-table operations
- Validate foreign key constraints
- Handle constraint violations with appropriate error messages

### Concurrent Access
- Use connection pooling if needed
- Implement row-level locking for critical operations
- Handle SQLite busy errors with exponential backoff

This database structure ensures perfect alignment with Pacifica API response formats while providing efficient storage and retrieval for the trading bot's real-time requirements.