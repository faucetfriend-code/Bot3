# Web Interface & API

<!--
RAG Metadata:
- Category: Interface
- Tags: fastapi, websocket, rest-api, web-ui, interface, html
- Related: 06-hub-system-architecture, 01-core-trading-logic
-->

## Overview

The web interface consists of:
1. **API Server** - FastAPI backend with REST and WebSocket
2. **Web UI** - HTML/CSS/JS frontend
3. **E2E Tests** - Playwright tests for web interface

---

## API Server

**Location**: `trading_bot_v2/api_server.py`

### Purpose
FastAPI server providing REST endpoints and WebSocket for real-time updates.

### Features
- Bot lifecycle management (start/stop)
- Position and trade history endpoints
- WebSocket for real-time updates
- Static file serving for UI
- CORS and authentication support

### Server Configuration

```python
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Trading Bot API",
    description="API for Trading Bot v3",
    version="2.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## REST API Endpoints

### Status Endpoints

```python
@app.get("/api/status")
async def get_status():
    """
    Get bot status.
    
    Returns:
        {
            "running": bool,
            "connected": bool,
            "last_update": timestamp,
            "positions_count": int,
            "pnl": float,
            "regime": str,
        }
    """

@app.get("/api/health")
async def health_check():
    """
    Health check endpoint.
    
    Returns:
        {
            "status": "healthy" | "degraded" | "unhealthy",
            "components": {...},
            "timestamp": timestamp,
        }
    """
```

### Bot Control Endpoints

```python
@app.post("/api/start")
async def start_bot():
    """
    Start the trading bot.
    
    Returns:
        {
            "success": bool,
            "message": str,
            "timestamp": timestamp,
        }
    """

@app.post("/api/stop")
async def stop_bot():
    """
    Stop the trading bot gracefully.
    
    Returns:
        {
            "success": bool,
            "message": str,
            "positions_retained": bool,
        }
    """

@app.post("/api/restart")
async def restart_bot():
    """
    Restart the trading bot.
    
    Returns:
        {
            "success": bool,
            "message": str,
        }
    """
```

### Data Endpoints

```python
@app.get("/api/positions")
async def get_positions(status: str = "open"):
    """
    Get positions.
    
    Args:
        status: Filter by status ("open", "closed", "all")
        
    Returns:
        [{
            "id": str,
            "symbol": str,
            "side": str,
            "quantity": float,
            "entry_price": float,
            "unrealized_pnl": float,
            "stop_loss": float,
            "strategy": str,
        }]
    """

@app.get("/api/trades")
async def get_trades(limit: int = 100, strategy: Optional[str] = None):
    """
    Get trade history.
    
    Args:
        limit: Maximum number of trades to return
        strategy: Filter by strategy type
        
    Returns:
        [{
            "id": str,
            "symbol": str,
            "side": str,
            "entry_price": float,
            "exit_price": float,
            "pnl_dollar": float,
            "strategy": str,
            "entry_time": timestamp,
            "exit_time": timestamp,
        }]
    """

@app.get("/api/signals")
async def get_signals(limit: int = 100, executed_only: bool = False):
    """
    Get signal history.
    
    Returns:
        [{
            "id": str,
            "symbol": str,
            "side": str,
            "confidence": float,
            "strategy": str,
            "executed": bool,
            "outcome": str,
        }]
    """

@app.get("/api/tickers")
async def get_tickers():
    """
    Get current prices for all tracked symbols.
    
    Returns:
        {
            "SOL-USD": {"price": float, "change_24h": float},
            "BTC-USD": {"price": float, "change_24h": float},
            ...
        }
    """
```

### Configuration Endpoints

```python
@app.get("/api/config")
async def get_config():
    """
    Get current bot configuration (non-sensitive).
    
    Returns:
        {
            "strategies_enabled": {...},
            "risk_profile": str,
            "max_exposure": float,
            "symbols": [...],
        }
    """

@app.put("/api/config")
async def update_config(config: ConfigUpdate):
    """
    Update bot configuration.
    
    Note: Some changes require bot restart.
    """
```

---

## WebSocket API

### Connection

```javascript
// JavaScript client
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onopen = () => {
    console.log('WebSocket connected');
    // Subscribe to channels
    ws.send(JSON.stringify({
        action: 'subscribe',
        channels: ['positions', 'signals', 'tickers']
    }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Received:', data);
};
```

### Message Types

```python
# Server -> Client messages

# Position update
{
    "type": "position_update",
    "data": {
        "action": "open" | "close" | "update",
        "position": {...}
    },
    "timestamp": "2026-01-15T10:30:00Z"
}

# Signal generated
{
    "type": "signal",
    "data": {
        "symbol": "SOL-USD",
        "side": "buy",
        "strategy": "MeanReversion",
        "confidence": 0.65
    }
}

# Ticker update
{
    "type": "ticker",
    "data": {
        "symbol": "SOL-USD",
        "price": 150.25,
        "change_24h": 2.5
    }
}

# Bot status update
{
    "type": "status",
    "data": {
        "running": true,
        "connected": true,
        "pnl": 125.50
    }
}

# Error message
{
    "type": "error",
    "data": {
        "code": "POSITION_LIMIT",
        "message": "Maximum positions reached"
    }
}
```

### WebSocket Handler

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.
    
    Protocol:
    1. Client connects
    2. Client subscribes to channels
    3. Server pushes updates for subscribed channels
    4. Client can send commands (start/stop)
    """
    await connection_manager.connect(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            if data.get("action") == "subscribe":
                # Subscribe to channels
                channels = data.get("channels", [])
                await subscribe_channels(websocket, channels)
                
            elif data.get("action") == "unsubscribe":
                # Unsubscribe from channels
                channels = data.get("channels", [])
                await unsubscribe_channels(websocket, channels)
                
            elif data.get("action") == "start_bot":
                # Start bot
                await start_bot()
                
            elif data.get("action") == "stop_bot":
                # Stop bot
                await stop_bot()
                
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket)
```

---

## Web UI

**Location**: `interface.html`

### Features
- Professional dark theme with Bootstrap
- Real-time position display
- Bot start/stop controls
- Trade history table
- Signal log viewer
- WebSocket auto-reconnection

### UI Components

```html
<!-- Bot Control Panel -->
<div class="card">
    <div class="card-header">Bot Control</div>
    <div class="card-body">
        <button id="startBtn" class="btn btn-success">Start</button>
        <button id="stopBtn" class="btn btn-danger">Stop</button>
        <span id="status" class="badge">Stopped</span>
    </div>
</div>

<!-- Positions Table -->
<div class="card">
    <div class="card-header">Open Positions</div>
    <div class="card-body">
        <table id="positionsTable" class="table">
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Quantity</th>
                    <th>Entry</th>
                    <th>PnL</th>
                    <th>Strategy</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>
</div>

<!-- Trade History -->
<div class="card">
    <div class="card-header">Trade History</div>
    <div class="card-body">
        <table id="tradesTable" class="table">
            <!-- Trade history rows -->
        </table>
    </div>
</div>
```

### JavaScript Client

```javascript
// WebSocket connection with auto-reconnect
class TradingBotClient {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.reconnectDelay = 1000;
        this.connect();
    }
    
    connect() {
        this.ws = new WebSocket(this.url);
        
        this.ws.onopen = () => {
            console.log('Connected');
            this.reconnectDelay = 1000; // Reset delay
            this.subscribe(['positions', 'signals', 'tickers']);
        };
        
        this.ws.onclose = () => {
            console.log('Disconnected, reconnecting...');
            setTimeout(() => this.connect(), this.reconnectDelay);
            this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
        };
        
        this.ws.onmessage = (event) => {
            this.handleMessage(JSON.parse(event.data));
        };
    }
    
    subscribe(channels) {
        this.ws.send(JSON.stringify({
            action: 'subscribe',
            channels: channels
        }));
    }
    
    handleMessage(data) {
        switch (data.type) {
            case 'position_update':
                this.updatePosition(data.data);
                break;
            case 'ticker':
                this.updateTicker(data.data);
                break;
            case 'status':
                this.updateStatus(data.data);
                break;
        }
    }
    
    startBot() {
        this.ws.send(JSON.stringify({action: 'start_bot'}));
    }
    
    stopBot() {
        this.ws.send(JSON.stringify({action: 'stop_bot'}));
    }
}
```

---

## API Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        API Server Architecture                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         Web Browser                                  │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                    interface.html                               │ │
│  │  Bot Controls | Positions Table | Trade History | Signals     │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                    │ HTTP                    │ WebSocket
                    ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FastAPI Server                                   │
│  ┌────────────────────┐  ┌────────────────────────────────────┐   │
│  │    REST Endpoints   │  │      WebSocket Handler             │   │
│  │                     │  │                                    │   │
│  │  GET /api/status    │  │  /ws                               │   │
│  │  GET /api/positions │  │  - Subscribe to channels          │   │
│  │  GET /api/trades    │  │  - Broadcast updates              │   │
│  │  POST /api/start    │  │  - Handle commands                │   │
│  │  POST /api/stop     │  │                                    │   │
│  └────────────────────┘  └────────────────────────────────────┘   │
│              │                         │                            │
│              └───────────┬─────────────┘                            │
│                          │                                          │
│                          ▼                                          │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │                    ConnectionManager                        │   │
│  │  - Connection pooling                                       │   │
│  │  - Broadcast management                                     │   │
│  │  - Authentication                                           │   │
│  └────────────────────────────────────────────────────────────┘   │
│                          │                                          │
└──────────────────────────┼──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       DataHub                                       │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐   │
│  │ TradingBot     │  │ DatabaseManager│  │ Price Cache        │   │
│  └────────────────┘  └────────────────┘  └────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Running the Server

```bash
# Development
cd trading_bot_v2
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000

# Production
uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 4

# With SSL
uvicorn api_server:app --ssl-keyfile key.pem --ssl-certfile cert.pem
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `api_server.py` | ~2200 | FastAPI server |
| `interface.html` | ~500 | Web UI |

---

## Related Reports

- [06-hub-system-architecture.md](./06-hub-system-architecture.md) - ConnectionManager
- [01-core-trading-logic.md](./01-core-trading-logic.md) - Bot integration
- [10-testing-infrastructure.md](./10-testing-infrastructure.md) - E2E tests
