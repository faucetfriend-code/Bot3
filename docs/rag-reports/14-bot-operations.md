# Trading Bot Operations & Deployment

<!--
RAG Metadata:
- Category: Operations
- Tags: docker, deployment, monitoring, alerting, circuit-breaker, pm2, systemd, aws, production
- Related: 14-bot-operations, 01-core-trading-logic, 07-risk-management
-->

## Overview

This document covers production-ready deployment, monitoring, and operations for trading bots including Docker, cloud deployment, monitoring, and risk management.

---

## Docker Containerization

### Dockerfile

```dockerfile
# Use multi-stage build for smaller images
FROM python:3.11-slim as builder

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Create non-root user for security
RUN useradd -m -u 1000 trader && \
    mkdir -p /app/logs /app/data && \
    chown -R trader:trader /app

# Copy dependencies from builder
COPY --from=builder /root/.local /home/trader/.local
ENV PATH=/home/trader/.local/bin:$PATH

# Copy application code
COPY --chown=trader:trader . .

USER trader

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "-m", "trading_bot.main"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  trading-bot:
    build: .
    container_name: trading-bot
    restart: unless-stopped
    env_file:
      - .env.production
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./config:/app/config:ro
    ports:
      - "8080:8080"
    networks:
      - trading-network
    depends_on:
      - redis
      - postgres
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"

  redis:
    image: redis:7-alpine
    container_name: trading-redis
    restart: unless-stopped
    volumes:
      - redis-data:/data
    networks:
      - trading-network

  postgres:
    image: postgres:15-alpine
    container_name: trading-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: trading
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - trading-network

  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    networks:
      - trading-network

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    volumes:
      - grafana-data:/var/lib/grafana
      - ./monitoring/dashboards:/etc/grafana/provisioning/dashboards:ro
    networks:
      - trading-network

networks:
  trading-network:
    driver: bridge

volumes:
  redis-data:
  postgres-data:
  grafana-data:
```

---

## Process Management

### PM2 Ecosystem

```javascript
// ecosystem.config.js
module.exports = {
  apps: [{
    name: 'trading-bot',
    script: 'python',
    args: '-m trading_bot.main',
    interpreter: 'python3.11',
    instances: 1,
    exec_mode: 'fork',
    autorestart: true,
    watch: false,
    max_memory_restart: '1G',
    env_production: {
      NODE_ENV: 'production',
      BOT_MODE: 'live',
      LOG_LEVEL: 'INFO'
    },
    error_file: './logs/pm2-error.log',
    out_file: './logs/pm2-out.log',
    log_file: './logs/pm2-combined.log',
    time: true,
    cron_restart: '0 4 * * *',  // Daily restart at 4 AM
    restart_delay: 5000,
    kill_timeout: 30000,
    wait_ready: true,
    listen_timeout: 10000
  }]
};
```

### Systemd Service

```ini
# /etc/systemd/system/trading-bot.service
[Unit]
Description=Trading Bot Service
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=simple
User=trader
Group=trader
WorkingDirectory=/opt/trading-bot
Environment="PATH=/opt/trading-bot/venv/bin:/usr/local/bin:/usr/bin"
EnvironmentFile=/opt/trading-bot/.env
ExecStart=/opt/trading-bot/venv/bin/python -m trading_bot.main
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=10
TimeoutStopSec=300
KillSignal=SIGTERM
KillMode=process

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/trading-bot/logs /opt/trading-bot/data
CapabilityBoundingSet=
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictRealtime=true

[Install]
WantedBy=multi-user.target
```

---

## Monitoring & Alerting

### Prometheus Metrics

```python
from prometheus_client import Counter, Gauge, Histogram, start_http_server

TRADES_TOTAL = Counter(
    'trading_bot_trades_total',
    'Total number of trades executed',
    ['symbol', 'side', 'strategy']
)

TRADE_PNL = Counter(
    'trading_bot_pnl_total',
    'Total profit/loss in USD',
    ['symbol', 'strategy']
)

POSITION_COUNT = Gauge(
    'trading_bot_positions',
    'Current number of open positions',
    ['symbol']
)

PORTFOLIO_VALUE = Gauge(
    'trading_bot_portfolio_value',
    'Current portfolio value in USD'
)

DRAWDOWN_PERCENT = Gauge(
    'trading_bot_drawdown_percent',
    'Current drawdown from peak equity'
)

ORDER_LATENCY = Histogram(
    'trading_bot_order_latency_seconds',
    'Order execution latency',
    ['exchange', 'order_type'],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

API_ERRORS = Counter(
    'trading_bot_api_errors_total',
    'Total API errors',
    ['exchange', 'error_type']
)

CIRCUIT_BREAKER_STATE = Gauge(
    'trading_bot_circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open, 2=half-open)',
    ['service']
)

class MetricsCollector:
    def __init__(self, port: int = 8080):
        start_http_server(port)
    
    def record_trade(self, symbol: str, side: str, strategy: str, pnl: float):
        TRADES_TOTAL.labels(symbol=symbol, side=side, strategy=strategy).inc()
        TRADE_PNL.labels(symbol=symbol, strategy=strategy).inc(pnl)
```

### Prometheus Alert Rules

```yaml
groups:
  - name: trading_bot_alerts
    rules:
      - alert: HighDrawdown
        expr: trading_bot_drawdown_percent > 10
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "High drawdown detected"
          description: "Drawdown is {{ $value }}%, exceeding 10% threshold"

      - alert: CircuitBreakerOpen
        expr: trading_bot_circuit_breaker_state == 1
        for: 30s
        labels:
          severity: warning

      - alert: APIErrorSpike
        expr: rate(trading_bot_api_errors_total[5m]) > 5
        for: 2m
        labels:
          severity: warning

      - alert: BotNotTrading
        expr: increase(trading_bot_trades_total[1h]) == 0
        for: 2h
        labels:
          severity: warning
```

---

## Notifications

### Telegram/Discord Alerts

```python
import aiohttp

class NotificationService:
    def __init__(self, telegram_token: str = None, discord_webhook: str = None):
        self.telegram_token = telegram_token
        self.discord_webhook = discord_webhook
    
    async def send_alert(self, level: str, title: str, message: str, details: dict = None):
        if self.discord_webhook:
            await self._send_discord(level, title, message, details)
        if self.telegram_token:
            await self._send_telegram(level, title, message, details)
    
    async def _send_discord(self, level: str, title: str, message: str, details: dict = None):
        color_map = {"info": 0x3498db, "warning": 0xf39c12, "critical": 0xe74c3c}
        
        embed = {
            "title": f"{level.upper()}: {title}",
            "description": message,
            "color": color_map.get(level, 0x3498db),
            "fields": [{"name": k, "value": str(v)} for k, v in (details or {}).items()]
        }
        
        async with aiohttp.ClientSession() as session:
            await session.post(self.discord_webhook, json={"embeds": [embed]})
```

---

## Circuit Breaker

```python
from enum import Enum
from datetime import datetime
from dataclasses import dataclass

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject all calls
    HALF_OPEN = "half_open"  # Testing if recovered

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    success_threshold: int = 3
    timeout_seconds: float = 60.0

class CircuitBreaker:
    def __init__(self, name: str, config: CircuitBreakerConfig = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
    
    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN
    
    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return False
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.config.timeout_seconds
    
    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0
    
    def record_failure(self):
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
        elif self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
        self.last_failure_time = datetime.now()
```

---

## Emergency Shutdown

```python
class EmergencyShutdown:
    def __init__(self, bot, notification_service):
        self.bot = bot
        self.notifications = notification_service
        self.is_shutdown = False
    
    async def execute(self, reason: str, close_positions: bool = True, 
                     cancel_orders: bool = True):
        if self.is_shutdown:
            return
        
        self.is_shutdown = True
        
        # Step 1: Stop accepting new signals
        self.bot.trading_enabled = False
        
        # Step 2: Cancel all open orders
        if cancel_orders:
            for exchange in self.bot.exchanges.values():
                orders = await exchange.fetch_open_orders()
                for order in orders:
                    await exchange.cancel_order(order["id"])
        
        # Step 3: Close all positions
        if close_positions:
            for pos in await self.bot.get_positions():
                side = "sell" if pos["side"] == "long" else "buy"
                await exchange.create_market_order(pos["symbol"], side, pos["size"], 
                                                  params={"reduceOnly": True})
        
        # Step 4: Send notification
        await self.notifications.send_alert(
            level="critical",
            title="EMERGENCY SHUTDOWN",
            message=f"Reason: {reason}",
            details={"positions_closed": close_positions, "orders_cancelled": cancel_orders}
        )
```

---

## Structured Logging

```python
from loguru import logger
import json

def setup_logging(log_dir: str = "logs", log_level: str = "INFO"):
    logger.remove()
    
    # Console with colors
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
               "<level>{level: <8}</level> | {message}",
        level=log_level
    )
    
    # Rotating file
    logger.add(
        f"{log_dir}/trading_bot_{{time:YYYY-MM-DD}}.log",
        rotation="00:00",
        retention="30 days",
        level="DEBUG"
    )
    
    # JSON structured logs
    def json_sink(message):
        record = message.record
        log_entry = {
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "message": record["message"],
            "extra": record["extra"]
        }
        print(json.dumps(log_entry), 
              file=open(f"{log_dir}/structured.jsonl", "a"))
    
    logger.add(json_sink, level="DEBUG", format="{message}")
    
    return logger
```

---

## Health Check Endpoints

```python
from fastapi import FastAPI, Response
from enum import Enum

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

def create_health_app(bot) -> FastAPI:
    app = FastAPI()
    
    @app.get("/health")
    async def health_check():
        components = {}
        
        # Database check
        try:
            await bot.db.execute("SELECT 1")
            components["database"] = {"status": HealthStatus.HEALTHY}
        except:
            components["database"] = {"status": HealthStatus.UNHEALTHY}
        
        # Exchange check
        try:
            await bot.exchange.fetch_time()
            components["exchange"] = {"status": HealthStatus.HEALTHY}
        except:
            components["exchange"] = {"status": HealthStatus.UNHEALTHY}
        
        return {
            "status": HealthStatus.HEALTHY if all(
                c.get("status") == HealthStatus.HEALTHY for c in components.values()
            ) else HealthStatus.DEGRADED,
            "components": components
        }
    
    @app.get("/health/live")
    async def liveness():
        return {"status": "alive"}
    
    @app.get("/health/ready")
    async def readiness():
        if not bot.is_ready:
            return Response(content="Not ready", status_code=503)
        return {"status": "ready"}
    
    return app
```

---

## AWS Deployment (Terraform)

```hcl
provider "aws" {
  region = "us-east-1"
}

resource "aws_vpc" "trading_vpc" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_instance" "trading_bot" {
  ami = "ami-0abcdef1234567890"
  instance_type = "t3.medium"
  subnet_id = aws_subnet.private.id
  iam_instance_profile = aws_iam_instance_profile.trading_bot.name
  
  user_data = base64encode(<<-EOF
    #!/bin/bash
    apt-get update
    apt-get install -y docker.io docker-compose
  EOF
  )
}

resource "aws_secretsmanager_secret" "exchange_keys" {
  name = "trading-bot/exchange-keys"
}
```

---

## Key Resources

| Category | Resource |
|----------|----------|
| Docker | Multi-stage builds, non-root user |
| Process Management | PM2, systemd |
| Monitoring | Prometheus, Grafana |
| Alerting | Telegram, Discord, Slack |
| Cloud | AWS Terraform, GCP, DigitalOcean |
| Security | Secrets Manager, IAM |

---

## Related Reports

- [01-core-trading-logic.md](./01-core-trading-logic.md) - Bot architecture
- [07-risk-management.md](./07-risk-management.md) - Risk controls
- [10-testing-infrastructure.md](./10-testing-infrastructure.md) - Testing
- [11-pacifica-integration.md](./11-pacifica-integration.md) - Exchange integration
