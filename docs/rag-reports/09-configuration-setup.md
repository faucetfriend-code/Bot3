# Configuration & Setup

<!--
RAG Metadata:
- Category: Configuration
- Tags: config, environment, env, requirements, dependencies, setup
- Related: 01-core-trading-logic, 04-exchange-integration
-->

## Overview

Configuration is managed through:
1. **Environment Variables** - `.env` file
2. **Config Class** - Centralized configuration
3. **Requirements** - Python dependencies
4. **Package.json** - Node.js dependencies

---

## Environment Variables

### .env File

**Location**: `.env` (root directory)

### Required Variables

```bash
# ===========================================
# PACIFICA EXCHANGE CREDENTIALS
# ===========================================
PACIFICA_AGENT_WALLET_PRIVATE_KEY=your_private_key_here
PACIFICA_ACCOUNT_PUBLIC_KEY=your_public_key_here
PACIFICA_TESTNET=true

# ===========================================
# BOT CONFIGURATION
# ===========================================
RISK_PROFILE=medium
TRADING_SYMBOLS=SOL-USD,BTC-USD,ETH-USD
DATABASE_PATH=data/trading_bot.db

# ===========================================
# RISK MANAGEMENT
# ===========================================
MAX_PORTFOLIO_RISK_PCT=0.05
MAX_PORTFOLIO_EXPOSURE_PCT=0.15
CIRCUIT_BREAKER_LOSS_PCT=0.10

# ===========================================
# STRATEGY ENABLE/DISABLE
# ===========================================
ENABLE_MEAN_REVERSION=true
ENABLE_MA_CROSSOVER=true
ENABLE_GRID_TRADING=true
ENABLE_LIQUIDATION_CAPTURE=true
ENABLE_VWAP_SCALPING=true
ENABLE_FUNDING_ARB=false
ENABLE_MOMENTUM_SCALPING=true
ENABLE_ORDERBOOK_IMBALANCE=true

# ===========================================
# GRID TRADING PARAMETERS
# ===========================================
GRID_TRADING_LEVELS=8
GRID_SPACING_ATR_MULTIPLIER=0.4
GRID_MAX_POSITIONS_PER_SYMBOL=10
GRID_EMERGENCY_STOP_PCT=0.05
GRID_ADX_THRESHOLD=20.0

# ===========================================
# WEBSOCKET CONFIGURATION
# ===========================================
ENABLE_WEBSOCKET=true
WS_RECONNECT_INITIAL_DELAY=1.0
WS_RECONNECT_MAX_DELAY=60.0

# ===========================================
# API SERVER
# ===========================================
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=false
```

### Optional Variables

```bash
# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/trading_bot.log

# Performance
CACHE_TTL_SECONDS=300
MAX_CACHE_SIZE=1000

# Notifications (optional)
SLACK_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
```

---

## Config Class

**Location**: `trading_bot_v2/config.py`

### Purpose
Centralized configuration loaded from environment variables.

### Configuration Class

```python
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum
import os

class AssetClass(str, Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    STOCKS = "stocks"

class TradeQuality(str, Enum):
    STANDARD = "standard"
    HIGH_CONVICTION = "high_conviction"
    SCALP = "scalp"

class StrategyType(str, Enum):
    MEAN_REVERSION = "mean_reversion"
    MA_CROSSOVER = "ma_crossover"
    GRID_TRADING = "grid_trading"
    LIQUIDATION_CAPTURE = "liquidation_capture"
    VWAP_SCALPING = "vwap_scalping"
    FUNDING_ARB = "funding_arb"
    MOMENTUM_SCALPING = "momentum_scalping"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    TREND_FOLLOWING = "trend_following"

class MarketState(str, Enum):
    TRENDING_STRONG = "trending_strong"
    TRENDING_MODERATE = "trending_moderate"
    RANGING_VOLATILE = "ranging_volatile"
    RANGING_CALM = "ranging_calm"
    INDECISIVE = "indecisive"
    UNKNOWN = "unknown"

@dataclass
class Config:
    """Configuration loaded from environment variables."""
    
    # Exchange credentials
    agent_wallet_private_key: str = ""
    account_public_key: str = ""
    testnet: bool = True
    
    # Risk settings
    risk_profile: str = "medium"
    max_portfolio_risk_pct: float = 0.05
    max_portfolio_exposure_pct: float = 0.15
    circuit_breaker_loss_pct: float = 0.10
    
    # Trading settings
    trading_symbols: List[str] = field(default_factory=lambda: ["SOL-USD"])
    database_path: str = "data/trading_bot.db"
    
    # Strategy toggles
    enable_mean_reversion: bool = True
    enable_ma_crossover: bool = True
    enable_grid_trading: bool = True
    enable_liquidation_capture: bool = True
    enable_vwap_scalping: bool = True
    enable_funding_arb: bool = False
    enable_momentum_scalping: bool = True
    enable_orderbook_imbalance: bool = True
    
    # Grid settings
    grid_trading_levels: int = 8
    grid_spacing_atr_multiplier: float = 0.4
    grid_max_positions_per_symbol: int = 10
    grid_emergency_stop_pct: float = 0.05
    grid_adx_threshold: float = 20.0
    
    # WebSocket settings
    enable_websocket: bool = True
    
    @classmethod
    def from_env(cls) -> 'Config':
        """Load configuration from environment variables."""
        return cls(
            agent_wallet_private_key=os.getenv("PACIFICA_AGENT_WALLET_PRIVATE_KEY", ""),
            account_public_key=os.getenv("PACIFICA_ACCOUNT_PUBLIC_KEY", ""),
            testnet=os.getenv("PACIFICA_TESTNET", "true").lower() == "true",
            risk_profile=os.getenv("RISK_PROFILE", "medium"),
            max_portfolio_risk_pct=float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "0.05")),
            max_portfolio_exposure_pct=float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "0.15")),
            circuit_breaker_loss_pct=float(os.getenv("CIRCUIT_BREAKER_LOSS_PCT", "0.10")),
            trading_symbols=os.getenv("TRADING_SYMBOLS", "SOL-USD").split(","),
            # ... more fields
        )

# Global config instance
config = Config.from_env()
```

---

## Python Dependencies

**Location**: `trading_bot_v2/requirements.txt`

### Core Dependencies

```txt
# Web Framework
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
websockets>=12.0

# HTTP Client
httpx>=0.25.0
requests>=2.31.0

# Database
aiosqlite>=0.19.0

# Data Processing
pandas>=2.1.0
numpy>=1.26.0

# Logging
loguru>=0.7.0

# Type Checking
pydantic>=2.5.0

# Environment
python-dotenv>=1.0.0

# Async Support
asyncio>=3.4.3
```

### Development Dependencies

```txt
# Testing
pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-cov>=4.1.0
pytest-mock>=3.12.0

# Linting
ruff>=0.1.0
flake8>=6.1.0

# Type Checking
mypy>=1.7.0

# Formatting
black>=23.11.0
```

### Installation

```bash
# Install dependencies
pip install -r trading_bot_v2/requirements.txt

# Install in development mode
pip install -e .
```

---

## Node.js Dependencies

**Location**: `package.json`

### Purpose
E2E testing with Playwright.

```json
{
  "name": "trading-bot-v3",
  "version": "2.0.0",
  "description": "Trading Bot E2E Tests",
  "scripts": {
    "test": "playwright test",
    "test:ui": "playwright test --ui",
    "test:headed": "playwright test --headed",
    "test:debug": "playwright test --debug",
    "report": "playwright show-report"
  },
  "devDependencies": {
    "@playwright/test": "^1.40.0",
    "@types/node": "^20.10.0",
    "typescript": "^5.3.0"
  }
}
```

### Installation

```bash
# Install Node.js dependencies
npm install

# Install Playwright browsers
npx playwright install
```

---

## Directory Structure

```
Bot3/
├── .env                    # Environment variables (not in git)
├── .env.example            # Example environment file
├── .gitignore              # Git ignore rules
├── data/
│   └── trading_bot.db      # SQLite database
├── logs/                   # Log files (created at runtime)
├── trading_bot_v2/
│   ├── __init__.py
│   ├── config.py           # Config class
│   ├── requirements.txt    # Python dependencies
│   └── ...
├── tests/                  # E2E tests (Playwright)
│   └── ...
├── package.json            # Node.js dependencies
├── tsconfig.json           # TypeScript config
└── playwright.config.ts    # Playwright config
```

---

## Setup Guide

### 1. Clone Repository

```bash
git clone <repository-url>
cd Bot3
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Install Python Dependencies

```bash
pip install -r trading_bot_v2/requirements.txt
```

### 4. Configure Environment

```bash
# Copy example file
cp .env.example .env

# Edit with your credentials
nano .env  # or use your preferred editor
```

### 5. Initialize Database

```bash
python -c "from trading_bot_v2.database import DatabaseManager; DatabaseManager()"
```

### 6. Install Node.js Dependencies (for E2E tests)

```bash
npm install
npx playwright install
```

### 7. Run the Bot

```bash
# Start API server
cd trading_bot_v2
uvicorn api_server:app --reload

# Or run directly
python -m trading_bot_v2.trading_bot
```

---

## Configuration Validation

```python
def validate_config(config: Config) -> List[str]:
    """Validate configuration and return list of errors."""
    errors = []
    
    # Check required credentials
    if not config.agent_wallet_private_key:
        errors.append("PACIFICA_AGENT_WALLET_PRIVATE_KEY is required")
    if not config.account_public_key:
        errors.append("PACIFICA_ACCOUNT_PUBLIC_KEY is required")
    
    # Validate risk settings
    if config.max_portfolio_risk_pct > 0.1:
        errors.append("MAX_PORTFOLIO_RISK_PCT should not exceed 10%")
    if config.max_portfolio_exposure_pct > 0.3:
        errors.append("MAX_PORTFOLIO_EXPOSURE_PCT should not exceed 30%")
    
    # Validate symbols
    if not config.trading_symbols:
        errors.append("At least one TRADING_SYMBOLS is required")
    
    return errors
```

---

## Key Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables |
| `.env.example` | Example configuration |
| `trading_bot_v2/config.py` | Config class |
| `trading_bot_v2/requirements.txt` | Python dependencies |
| `package.json` | Node.js dependencies |

---

## Related Reports

- [01-core-trading-logic.md](./01-core-trading-logic.md) - Config usage
- [04-exchange-integration.md](./04-exchange-integration.md) - Credentials
- [10-testing-infrastructure.md](./10-testing-infrastructure.md) - Test configuration
