# Trading Bot v2 - Deployment Guide

## Overview

This guide provides step-by-step instructions for deploying the enhanced Trading Bot v2 from development through production, including environment setup, configuration, validation, and go-live procedures.

---

## Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **CPU** | 4 cores | 8 cores |
| **RAM** | 8 GB | 16 GB |
| **Storage** | 50 GB SSD | 100 GB SSD |
| **Network** | 10 Mbps | 100 Mbps |
| **OS** | Ubuntu 20.04 LTS | Ubuntu 22.04 LTS |

### Software Requirements

- **Python**: 3.8 or higher
- **Node.js**: 16.x or higher (for E2E tests)
- **Git**: 2.30 or higher
- **SQLite**: 3.35 or higher

### Network Requirements

- Outbound HTTPS (port 443) to Pacifica API
- Outbound WSS (port 443) for WebSocket connections
- Inbound HTTP (port 8000) for web interface
- Inbound WebSocket (port 8000) for real-time updates

---

## Phase 1: Environment Preparation

### Step 1.1: System Update

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install required system packages
sudo apt install -y python3-pip python3-venv nodejs npm git sqlite3

# Verify installations
python3 --version  # Should be 3.8+
node --version     # Should be 16.x+
git --version      # Should be 2.30+
```

### Step 1.2: Create Project Directory

```bash
# Create project directory
mkdir -p /opt/trading-bot
sudo chown $USER:$USER /opt/trading-bot
cd /opt/trading-bot

# Clone repository (if applicable)
git clone <repository-url> .
# OR extract archive
tar -xzf trading-bot-v2.tar.gz
```

### Step 1.3: Set Up Python Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Verify activation
which python  # Should show /opt/trading-bot/venv/bin/python
```

---

## Phase 2: Application Installation

### Step 2.1: Install Python Dependencies

```bash
# Navigate to trading bot directory
cd trading_bot_v2

# Upgrade pip
pip install --upgrade pip

# Install production dependencies
pip install -r requirements.txt

# Verify installation
pip list | grep -E "(fastapi|uvicorn|aiosqlite)"
```

**requirements.txt** should include:
```
fastapi>=0.104.0
uvicorn>=0.24.0
pydantic>=2.5.0
aiosqlite>=0.20.0
requests>=2.31.0
python-dotenv>=1.0.0
websockets>=12.0
psutil>=5.9.0
loguru>=0.7.0
```

### Step 2.2: Install Node.js Dependencies (for E2E tests)

```bash
# From project root
cd /opt/trading-bot

# Install npm dependencies
npm install

# Install Playwright browsers
npx playwright install chromium
```

### Step 2.3: Initialize Database

```bash
# Navigate to trading bot directory
cd /opt/trading-bot/trading_bot_v2

# Run database initialization
python init_db.py

# Verify database creation
ls -la data/trading_bot.db
```

---

## Phase 3: Configuration

### Step 3.1: Environment Variables

Create the `.env` file in `/opt/trading-bot/trading_bot_v2/`:

```bash
# Navigate to trading bot directory
cd /opt/trading-bot/trading_bot_v2

# Create .env file
cat > .env << 'EOF'
# =============================================================================
# Pacifica API Credentials
# =============================================================================
AGENT_WALLET_PRIVATE_KEY=your_agent_wallet_private_key_here
ACCOUNT_PUBLIC_KEY=your_account_public_key_here

# =============================================================================
# Environment Settings
# =============================================================================
TESTNET=true
DATABASE_PATH=data/trading_bot.db
LOG_LEVEL=INFO

# =============================================================================
# Trading Parameters
# =============================================================================
MAX_POSITIONS=5
DEFAULT_LEVERAGE=10
MAX_RISK_PER_TRADE=0.02
MAX_DRAWDOWN_PCT=0.10

# =============================================================================
# Strategy Configuration
# =============================================================================
ENABLE_MEAN_REVERSION=true
ENABLE_MA_CROSSOVER=true
ENABLE_GRID_TRADING=true
ENABLE_LIQUIDATION_CAPTURE=true
ENABLE_VWAP_SCALPING=false
ENABLE_FUNDING_ARB=false
ENABLE_MOMENTUM_SCALPING=false
ENABLE_ORDERBOOK_IMBALANCE=true

# =============================================================================
# Risk Management
# =============================================================================
KELLY_FRACTION=0.5
MIN_TRADE_HISTORY=50
CIRCUIT_BREAKER_THRESHOLD=0.10

# =============================================================================
# Performance Monitoring
# =============================================================================
ENABLE_PERFORMANCE_MONITORING=true
METRICS_RETENTION_HOURS=24
ALERT_COOLDOWN_MINUTES=5

# =============================================================================
# Grid Trading Settings
# =============================================================================
MAX_GRIDS_PER_SYMBOL=3
GRID_LEVELS=8
GRID_ATR_MULTIPLIER=0.4

# =============================================================================
# API Rate Limiting
# =============================================================================
API_RATE_LIMIT=100
API_RATE_LIMIT_WINDOW=60
EOF

# Set appropriate permissions
chmod 600 .env
```

**Security Note**: Never commit the `.env` file to version control.

### Step 3.2: Configuration Validation

```bash
# Test configuration loading
python -c "from config import config; print('Config loaded successfully')"

# Verify required variables are set
python << 'EOF'
from config import config
import sys

required = ['pacifica_private_key', 'pacifica_public_key']
missing = [var for var in required if not getattr(config, var, None)]

if missing:
    print(f"Missing required config: {missing}")
    sys.exit(1)
else:
    print("All required configuration present")
EOF
```

### Step 3.3: Directory Structure Setup

```bash
# Create required directories
mkdir -p data logs backups config

# Set permissions
chmod 755 data logs backups config

# Verify structure
tree -L 2 /opt/trading-bot/trading_bot_v2
```

Expected structure:
```
trading_bot_v2/
├── api_server.py
├── config.py
├── database.py
├── data/
│   └── trading_bot.db
├── logs/
├── backups/
├── config/
├── .env
├── requirements.txt
└── ...
```

---

## Phase 4: Testing & Validation

### Step 4.1: Unit Tests

```bash
# Run all unit tests
cd /opt/trading-bot/trading_bot_v2
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=. --cov-report=html

# Check coverage report
coverage report
```

Expected output:
```
============================= test session starts ==============================
platform linux -- Python 3.10.12
pytest-7.4.0

tests/test_hub_system.py::test_hub_system_initialization PASSED
tests/test_performance.py::test_performance_monitor PASSED
...
============================== 45 passed in 12.34s =============================
```

### Step 4.2: Integration Tests

```bash
# Start API server in background
python api_server.py &
SERVER_PID=$!
sleep 5

# Run integration tests
pytest tests/test_integration.py -v

# Stop server
kill $SERVER_PID
```

### Step 4.3: End-to-End Tests

```bash
# Ensure server is running
python api_server.py &
sleep 5

# Run Playwright E2E tests
npm test

# Or run specific test
npx playwright test tests/bot-controls.spec.ts
```

### Step 4.4: Performance Validation

```bash
# Run performance benchmarks
python -c "
from performance_monitor import get_performance_monitor
import asyncio

async def benchmark():
    monitor = await get_performance_monitor()
    await monitor.start()
    
    # Run benchmarks
    summary = await monitor.get_summary()
    print(f'System metrics: {summary}')

asyncio.run(benchmark())
"
```

---

## Phase 5: Production Deployment

### Step 5.1: Systemd Service Setup

Create service file `/etc/systemd/system/trading-bot.service`:

```bash
sudo tee /etc/systemd/system/trading-bot.service << 'EOF'
[Unit]
Description=Trading Bot v2
After=network.target

[Service]
Type=simple
User=tradingbot
Group=tradingbot
WorkingDirectory=/opt/trading-bot/trading_bot_v2
Environment=PATH=/opt/trading-bot/venv/bin
Environment=PYTHONPATH=/opt/trading-bot/trading_bot_v2
EnvironmentFile=/opt/trading-bot/trading_bot_v2/.env
ExecStart=/opt/trading-bot/venv/bin/python api_server.py
ExecReload=/bin/kill -HUP $MAINPID
KillMode=mixed
Restart=always
RestartSec=5
StandardOutput=append:/opt/trading-bot/trading_bot_v2/logs/trading-bot.log
StandardError=append:/opt/trading-bot/trading_bot_v2/logs/trading-bot-error.log

[Install]
WantedBy=multi-user.target
EOF
```

### Step 5.2: Create Dedicated User

```bash
# Create trading bot user
sudo useradd -r -s /bin/false tradingbot

# Set ownership
sudo chown -R tradingbot:tradingbot /opt/trading-bot

# Set permissions
sudo chmod 750 /opt/trading-bot
sudo chmod 600 /opt/trading-bot/trading_bot_v2/.env
```

### Step 5.3: Configure Logging

```bash
# Create log rotation configuration
sudo tee /etc/logrotate.d/trading-bot << 'EOF'
/opt/trading-bot/trading_bot_v2/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 tradingbot tradingbot
    sharedscripts
    postrotate
        /bin/kill -HUP $(cat /var/run/syslogd.pid 2> /dev/null) 2> /dev/null || true
    endscript
}
EOF
```

### Step 5.4: Firewall Configuration

```bash
# Allow web interface port
sudo ufw allow 8000/tcp

# Verify rules
sudo ufw status
```

### Step 5.5: Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable trading-bot

# Start service
sudo systemctl start trading-bot

# Check status
sudo systemctl status trading-bot
```

### Step 5.6: Health Verification

```bash
# Check service health
curl http://localhost:8000/api/status

# Run health check script
cd /opt/trading-bot/trading_bot_v2
python scripts/health_check.py

# Verify logs
sudo tail -f /opt/trading-bot/trading_bot_v2/logs/trading-bot.log
```

---

## Phase 6: Post-Deployment Validation

### Step 6.1: Functional Testing

```bash
# Test API endpoints
curl -s http://localhost:8000/api/status | jq

# Test bot controls
curl -X POST http://localhost:8000/api/bot/start
sleep 5
curl http://localhost:8000/api/status | jq '.data.is_running'

# Test WebSocket
curl -i -N \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Host: localhost:8000" \
  -H "Origin: http://localhost:8000" \
  http://localhost:8000/ws
```

### Step 6.2: Performance Baseline

```bash
# Record initial performance metrics
curl http://localhost:8000/api/metrics/system | tee baseline_metrics.json

# Monitor for 5 minutes
for i in {1..30}; do
    curl -s http://localhost:8000/api/status | jq '.data'
    sleep 10
done
```

### Step 6.3: Monitoring Setup

```bash
# Install monitoring script
sudo cp scripts/health_check.py /usr/local/bin/trading-bot-health-check
sudo chmod +x /usr/local/bin/trading-bot-health-check

# Add cron job for health monitoring
crontab -l | { cat; echo "*/5 * * * * /usr/local/bin/trading-bot-health-check >> /var/log/trading-bot-health.log 2>&1"; } | crontab -
```

---

## Phase 7: Rollback Procedures

### Step 7.1: Backup Current State

```bash
# Create backup directory
BACKUP_DIR="/opt/backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p $BACKUP_DIR

# Backup database
cp /opt/trading-bot/trading_bot_v2/data/trading_bot.db $BACKUP_DIR/

# Backup configuration
cp /opt/trading-bot/trading_bot_v2/.env $BACKUP_DIR/

# Backup application code
tar -czf $BACKUP_DIR/trading_bot_v2.tar.gz /opt/trading-bot/trading_bot_v2/

echo "Backup created at $BACKUP_DIR"
```

### Step 7.2: Service Rollback

```bash
# Stop service
sudo systemctl stop trading-bot

# Restore from backup
sudo cp $BACKUP_DIR/trading_bot.db /opt/trading-bot/trading_bot_v2/data/
sudo cp $BACKUP_DIR/.env /opt/trading-bot/trading_bot_v2/

# Fix permissions
sudo chown -R tradingbot:tradingbot /opt/trading-bot

# Restart service
sudo systemctl start trading-bot

# Verify rollback
curl http://localhost:8000/api/status
```

---

## Troubleshooting

### Common Deployment Issues

#### Issue: Service fails to start

```bash
# Check service logs
sudo journalctl -u trading-bot -n 100

# Check application logs
sudo tail -n 100 /opt/trading-bot/trading_bot_v2/logs/trading-bot-error.log

# Verify configuration
sudo -u tradingbot /opt/trading-bot/venv/bin/python -c "from config import config; print(config)"
```

#### Issue: Permission denied

```bash
# Fix permissions
sudo chown -R tradingbot:tradingbot /opt/trading-bot
sudo chmod 600 /opt/trading-bot/trading_bot_v2/.env
sudo chmod 755 /opt/trading-bot/trading_bot_v2/data
sudo chmod 755 /opt/trading-bot/trading_bot_v2/logs

# Restart service
sudo systemctl restart trading-bot
```

#### Issue: Port already in use

```bash
# Find process using port 8000
sudo lsof -i :8000

# Kill process
sudo kill -9 <PID>

# Restart service
sudo systemctl restart trading-bot
```

#### Issue: Database locked

```bash
# Check for zombie processes
ps aux | grep sqlite

# Restart service (will release locks)
sudo systemctl restart trading-bot

# If problem persists, check disk space
df -h
```

---

## Maintenance Procedures

### Daily Checks

```bash
#!/bin/bash
# daily_check.sh

echo "=== Trading Bot Daily Check ==="
echo "Date: $(date)"

# Check service status
if ! systemctl is-active --quiet trading-bot; then
    echo "ERROR: Trading bot service is not running"
    exit 1
fi

# Check API health
STATUS=$(curl -s http://localhost:8000/api/status)
if [ $? -ne 0 ]; then
    echo "ERROR: API is not responding"
    exit 1
fi

# Check disk space
DISK_USAGE=$(df /opt/trading-bot | tail -1 | awk '{print $5}' | tr -d '%')
if [ $DISK_USAGE -gt 80 ]; then
    echo "WARNING: Disk usage is at ${DISK_USAGE}%"
fi

echo "✅ All checks passed"
```

### Weekly Maintenance

```bash
#!/bin/bash
# weekly_maintenance.sh

echo "=== Weekly Maintenance ==="

# Rotate logs
sudo logrotate -f /etc/logrotate.d/trading-bot

# Backup database
BACKUP_DIR="/opt/backups/weekly/$(date +%Y%m%d)"
mkdir -p $BACKUP_DIR
cp /opt/trading-bot/trading_bot_v2/data/trading_bot.db $BACKUP_DIR/

# Clean old backups (keep 4 weeks)
find /opt/backups/weekly -type d -mtime +28 -exec rm -rf {} \;

# Update system packages
sudo apt update && sudo apt upgrade -y

echo "✅ Maintenance completed"
```

---

## Security Hardening

### Step 1: Disable Root Login

```bash
# Edit SSH config
sudo sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
```

### Step 2: Configure Fail2Ban

```bash
# Install fail2ban
sudo apt install fail2ban -y

# Create custom config
sudo tee /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3

[trading-bot]
enabled = true
port = 8000
filter = trading-bot
logpath = /opt/trading-bot/trading_bot_v2/logs/trading-bot.log
maxretry = 5
EOF

# Create filter
sudo tee /etc/fail2ban/filter.d/trading-bot.conf << 'EOF'
[Definition]
failregex = ^.*Failed authentication attempt from <HOST>.*$
            ^.*Invalid API key from <HOST>.*$
ignoreregex =
EOF

# Start fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

### Step 3: Enable HTTPS (Production)

```bash
# Install certbot
sudo apt install certbot -y

# Obtain certificate
sudo certbot certonly --standalone -d your-domain.com

# Configure API server for HTTPS (update .env)
echo "SSL_CERT=/etc/letsencrypt/live/your-domain.com/fullchain.pem" >> .env
echo "SSL_KEY=/etc/letsencrypt/live/your-domain.com/privkey.pem" >> .env
```

---

## Go-Live Checklist

### Pre-Deployment

- [ ] All tests passing (unit, integration, E2E)
- [ ] Configuration validated
- [ ] Database initialized
- [ ] Backups configured
- [ ] Documentation reviewed

### Deployment

- [ ] Service installed and configured
- [ ] Firewall rules applied
- [ ] User permissions set
- [ ] Service started successfully
- [ ] Health checks passing

### Post-Deployment

- [ ] API responding correctly
- [ ] Web interface accessible
- [ ] WebSocket connections working
- [ ] Monitoring alerts configured
- [ ] Log rotation working
- [ ] Backup system verified

### Production Verification

- [ ] Trading bot operational for 24 hours
- [ ] No critical errors in logs
- [ ] Performance metrics within targets
- [ ] Alert system tested
- [ ] Rollback procedure tested

---

**For additional support, refer to [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) and [PERFORMANCE_MONITORING.md](./PERFORMANCE_MONITORING.md).**
