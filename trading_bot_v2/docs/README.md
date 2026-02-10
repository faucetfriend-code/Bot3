# Trading Bot v2

A streamlined, functional trading bot for Pacifica.fi perpetual futures exchange, rebuilt to eliminate 80% of complexity while maintaining 100% essential functionality.

## Features

- **Web Interface**: Clean, responsive UI for monitoring positions and trades
- **Real-time Data**: Live Pacifica API integration with automatic data refresh
- **SQLite Database**: Persistent storage for trades, positions, and market data
- **Automated Trading**: Configurable bot with position management and risk controls
- **API Endpoints**: RESTful API for programmatic access and integration
- **Secure Authentication**: HMAC-SHA256 signed requests to Pacifica API
- **Environment Configuration**: Secure credential management via .env files

## Architecture (Phase 2 Complete)

The trading bot has completed a comprehensive architectural refactoring across two phases, transforming it from a monolithic application into a clean, layered, component-based system.

### Phase 1: Foundation Components ✅

**Core Components:**
- **GridLifecycleManager**: Authoritative owner of grid trading state per symbol
- **MarketRegimeDetector**: Independent market condition assessment (5 regimes)
- **RiskManager (Enhanced)**: Authoritative capital allocation gatekeeper

**Database Schema:** New tables for `grid_state`, `grid_levels`, `regime_history`, `capital_approvals`

### Phase 2: Coordinator Pattern ✅

**Architectural Transformation:**
- **Trading Bot**: Pure coordinator (no execution logic)
- **WebSocket Authority**: Single source of truth for live prices (no REST fallback)
- **Component Communication**: Event-driven architecture with clean interfaces
- **Dependency Injection**: Component registry for loose coupling

**New Infrastructure:**
- **Component Interfaces**: Clean contracts between components
- **Event System**: Decoupled component communication
- **Component Registry**: Centralized dependency management
- **Feature Flags**: Safe rollout and rollback capabilities

### Monitoring Architecture

The system implements a comprehensive monitoring framework with clear distinctions between different monitoring modes:

#### Real-Time Monitoring (Continuous Operation)
- **Purpose**: Continuous health monitoring of production systems
- **Duration**: Indefinite (24/7 operation)
- **Frequency**: Configurable intervals (default: 5 minutes for health checks, 120 seconds for trading loops)
- **Scope**: System health, component status, WebSocket connectivity, position sync
- **Output**: Status dashboards, alert generation, AI troubleshooting instructions

#### Stability Testing (Finite Duration Tests)
- **Purpose**: Validate system reliability under controlled conditions
- **Duration**: Fixed time periods (hours to days)
- **Frequency**: Continuous during test window
- **Scope**: Endurance testing, load testing, memory leak detection, performance benchmarking
- **Output**: Test reports with execution metrics, performance benchmarks, failure analysis

#### Key Distinctions
- **Real-time monitoring** tracks ongoing production health and generates alerts
- **Stability tests** validate system behavior over defined time periods with specific success criteria
- **Monitoring reports** show current system state and uptime statistics
- **Test reports** show test execution duration, pass/fail criteria, and performance metrics

### Architectural Benefits

- **Separation of Concerns**: Each component has exactly one responsibility
- **State Ownership**: Authoritative components prevent conflicts
- **Emergency Controls**: Robust failure handling and circuit breakers
- **Event-Driven**: Loose coupling through event bus communication
- **WebSocket Priority**: Real-time data consistency across all operations
- **Maintainability**: Clear interfaces and component boundaries
- **Scalability**: Independent component scaling and replacement
- **Testability**: Isolated component testing with comprehensive coverage

### Component Communication Flow

```
Strategy Manager → SIGNAL_GENERATED Event → Trading Bot (Coordinator)
Trading Bot → CAPITAL_REQUESTED Event → Risk Manager
Risk Manager → CAPITAL_APPROVED Event → Trading Bot
Trading Bot → Execution Client (Order Placement)
Execution Client → ORDER_PLACED Event → Trading Bot
```

### Database Schema Extensions

**Phase 1 + 2 Tables:**
- `grid_state`: Grid lifecycle state per symbol
- `grid_levels`: Individual grid order levels
- `regime_history`: Market regime detection history
- `capital_approvals`: Audit trail for risk management decisions

**WebSocket-Only Prices:** Trading decisions use WebSocket cache exclusively, ensuring real-time consistency.

See `docs/architecture_phase1.md` and `docs/architecture_phase2.md` for complete specifications.

## Prerequisites

- Python 3.8 or higher
- pip package manager
- Valid Pacifica testnet or mainnet credentials

## Installation

1. Clone or download the project
2. Navigate to the trading_bot_v2 directory
3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the trading_bot_v2 directory with your Pacifica credentials:

```env
# Pacifica API Credentials
AGENT_WALLET_PRIVATE_KEY=your_agent_wallet_private_key_here
ACCOUNT_PUBLIC_KEY=your_account_public_key_here

# Environment Settings
TESTNET=true
DATABASE_PATH=trading_bot.db

# Trading Parameters
MAX_POSITIONS=5
DEFAULT_LEVERAGE=10
MAX_RISK_PER_TRADE=0.02

# Logging
LOG_LEVEL=INFO
```

**Security Note**: Never commit your `.env` file to version control. The `.env` file is already in `.gitignore`.

## Usage

### Starting the Bot

1. Ensure your `.env` file is configured
2. Run the API server:

```bash
python api_server.py
```

3. Open your browser to `http://localhost:8000`
4. Use the web interface to start/stop the trading bot

### Web Interface

The web interface provides:
- **Status Dashboard**: Bot running status, position counts, P&L
- **Positions Table**: Current open positions with real-time updates
- **Trades History**: Recent trading activity
- **Bot Controls**: Start/stop trading operations

### API Endpoints

#### GET /api/status
Returns system status information.

**Response:**
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

#### GET /api/positions
Returns current open positions.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "symbol": "BTC/USD",
      "side": "long",
      "quantity": 0.1,
      "entry_price": 45000.0,
      "current_price": 46000.0,
      "unrealized_pnl": 1000.0,
      "opened_at": "2024-01-01T10:00:00",
      "updated_at": "2024-01-01T10:30:00"
    }
  ]
}
```

#### GET /api/trades
Returns trade history (default limit: 100).

**Query Parameters:**
- `limit` (optional): Number of trades to return

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "symbol": "BTC/USD",
      "side": "buy",
      "quantity": 0.1,
      "entry_price": 45000.0,
      "exit_price": 46000.0,
      "entry_time": "2024-01-01T10:00:00",
      "exit_time": "2024-01-01T11:00:00",
      "pnl": 1000.0,
      "status": "closed"
    }
  ]
}
```

#### POST /api/bot/start
Starts the automated trading bot.

**Response:**
```json
{
  "success": true,
  "message": "Bot started"
}
```

#### POST /api/bot/stop
Stops the automated trading bot.

**Response:**
```json
{
  "success": true,
  "message": "Bot stopped"
}
```

#### GET /api/markets
Returns available trading markets.

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTC/USD",
      "price": 45000.0,
      "volume": 100.5
    }
  ]
}
```

## Development

### Project Structure

```
trading_bot_v2/
├── config.py              # Configuration management
├── database.py            # SQLite database operations
├── api_server.py         # FastAPI server and endpoints
├── trading_bot.py         # Core trading logic (being refactored)
├── pacifica_client.py     # Pacifica API client
├── interface.html         # Web interface
├── data_utils.py          # Data format conversion utilities
├── requirements.txt       # Python dependencies
├── README.md              # This file
├── grid_lifecycle_manager.py    # NEW: Authoritative grid state management
├── market_regime.py             # NEW: Market regime detection
├── risk_manager.py              # UPDATED: Authoritative capital allocation
├── tests/
│   ├── test_grid_lifecycle.py   # NEW: Grid manager tests
│   └── test_market_regime.py    # NEW: Regime detector tests
├── migrations/
│   └── 001_refactor_foundation.sql  # NEW: Database schema updates
└── docs/
    └── architecture_phase1.md    # NEW: Phase 1 architecture docs
```

### Testing

Run the test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=.
```

### Code Style

- Follow PEP 8 Python style guidelines
- Use type hints for function parameters and return values
- Add docstrings to all public functions
- Keep functions focused and single-purpose

## Troubleshooting

### Common Issues

**API Connection Failed**
- Verify your `.env` file contains valid Pacifica credentials
- Check TESTNET setting (true for testnet, false for mainnet)
- Ensure network connectivity to Pacifica API

**Database Errors**
- Check DATABASE_PATH in .env file
- Ensure write permissions to database directory
- Verify SQLite is available (built-in with Python)

**Bot Not Starting**
- Confirm all required environment variables are set
- Check log output for specific error messages
- Verify Pacifica API credentials are correct

**Web Interface Not Loading**
- Ensure API server is running on port 8000
- Check browser console for JavaScript errors
- Verify CORS settings if accessing from different domain

### Logging

Adjust log verbosity in `.env`:

```env
LOG_LEVEL=DEBUG  # For detailed logging
LOG_LEVEL=INFO   # For normal operation
LOG_LEVEL=WARNING  # For production
```

Logs are output to console by default.

## Deployment

### Production Setup

1. Set up production environment with proper security
2. Configure production Pacifica credentials (TESTNET=false)
3. Set up monitoring and alerting
4. Configure backup procedures for database
5. Set up log aggregation

### Security Considerations

- Store credentials securely (environment variables, not code)
- Use HTTPS in production
- Implement proper access controls
- Regular security updates for dependencies
- Monitor for unusual trading activity

## Contributing

1. Follow the established code style
2. Add tests for new functionality
3. Update documentation as needed
4. Ensure all tests pass before submitting changes

## License

This project is proprietary software for the Unity Oracle Aggregator trading system.