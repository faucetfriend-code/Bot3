# Trading Bot v2 - Troubleshooting Guide

## Overview

This guide provides solutions for common issues, diagnostic procedures, debugging commands, and escalation paths for the Trading Bot v2 system.

---

## Quick Diagnostic Flowchart

```
┌─────────────────────────────────────────────────────────────┐
│                     Problem Identified                       │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Bot Won't    │ │ API Errors   │ │ Trading      │
│ Start        │ │ /Connection  │ │ Issues       │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Check logs:  │ │ Check        │ │ Check        │
│ - Service    │ │ - Network    │ │ - Balance    │
│ - .env file  │ │ - Credentials│ │ - Strategy   │
│ - Permissions│ │ - Rate limits│ │ - Grid state │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        ▼
           ┌────────────────────────┐
           │   Issue Resolved?      │
           └───────┬────────────────┘
                   │
       ┌───────────┴───────────┐
       │ YES                   │ NO
       ▼                       ▼
┌──────────────┐     ┌──────────────────┐
│   ✅ Done    │     │ Check Advanced   │
│              │     │ Diagnostics      │
└──────────────┘     └──────────────────┘
```

---

## Common Issues & Solutions

### Issue 1: Bot Won't Start

#### Symptoms
- Service fails to start
- `systemctl status trading-bot` shows failed state
- API server not responding

#### Diagnostic Steps

```bash
# 1. Check service status
sudo systemctl status trading-bot

# 2. View service logs
sudo journalctl -u trading-bot -n 100 --no-pager

# 3. Check application logs
sudo tail -n 100 /opt/trading-bot/trading_bot_v2/logs/trading-bot-error.log

# 4. Verify configuration
sudo -u tradingbot /opt/trading-bot/venv/bin/python -c "
from config import config
print('Config loaded')
print(f'Testnet: {config.testnet}')
print(f'Database: {config.database_path}')
"

# 5. Check file permissions
ls -la /opt/trading-bot/trading_bot_v2/
ls -la /opt/trading-bot/trading_bot_v2/data/
```

#### Common Causes & Fixes

**Cause: Missing Environment Variables**
```bash
# Error: "AGENT_WALLET_PRIVATE_KEY not set"

# Fix: Create or update .env file
cat > /opt/trading-bot/trading_bot_v2/.env << 'EOF'
AGENT_WALLET_PRIVATE_KEY=your_key_here
ACCOUNT_PUBLIC_KEY=your_key_here
TESTNET=true
DATABASE_PATH=data/trading_bot.db
EOF

sudo chmod 600 /opt/trading-bot/trading_bot_v2/.env
sudo systemctl restart trading-bot
```

**Cause: Permission Denied**
```bash
# Error: "Permission denied" in logs

# Fix: Correct ownership and permissions
sudo chown -R tradingbot:tradingbot /opt/trading-bot
sudo chmod 750 /opt/trading-bot
sudo chmod 600 /opt/trading-bot/trading_bot_v2/.env
sudo chmod 755 /opt/trading-bot/trading_bot_v2/data
sudo chmod 755 /opt/trading-bot/trading_bot_v2/logs

sudo systemctl restart trading-bot
```

**Cause: Port Already in Use**
```bash
# Error: "Address already in use"

# Find process using port 8000
sudo lsof -i :8000

# Kill process
sudo kill -9 <PID>

# Or use different port (update .env)
echo "API_PORT=8001" >> /opt/trading-bot/trading_bot_v2/.env

sudo systemctl restart trading-bot
```

**Cause: Database Locked**
```bash
# Error: "database is locked"

# Check for zombie processes
ps aux | grep sqlite

# Kill stuck processes
sudo pkill -f "trading_bot.db"

# Restart service
sudo systemctl restart trading-bot

# If persistent, check disk space
df -h
```

---

### Issue 2: API Connection Failures

#### Symptoms
- "Failed to connect to Pacifica API"
- Connection timeout errors
- Authentication failures

#### Diagnostic Steps

```bash
# 1. Test network connectivity
ping api.pacifica.fi

# 2. Test HTTPS connection
curl -v https://api.pacifica.fi/v1/health

# 3. Verify credentials
curl -X POST https://api.pacifica.fi/v1/auth/verify \
  -H "X-API-Key: $ACCOUNT_PUBLIC_KEY" \
  -H "X-Signature: <signature>"

# 4. Check rate limits in logs
sudo grep -i "rate limit" /opt/trading-bot/trading_bot_v2/logs/*.log
```

#### Common Causes & Fixes

**Cause: Network Connectivity**
```bash
# Fix: Check firewall rules
sudo ufw status

# Allow outbound HTTPS
sudo ufw allow out 443/tcp

# Test again
curl https://api.pacifica.fi/v1/health
```

**Cause: Invalid Credentials**
```bash
# Error: "Invalid API key" or "Authentication failed"

# Fix: Verify and update credentials
# 1. Login to Pacifica platform
# 2. Generate new API keys
# 3. Update .env file
sudo nano /opt/trading-bot/trading_bot_v2/.env

# Restart service
sudo systemctl restart trading-bot
```

**Cause: Rate Limiting**
```bash
# Error: "Rate limit exceeded"

# Check current rate limit status
curl -s https://api.pacifica.fi/v1/rate-limit \
  -H "X-API-Key: $ACCOUNT_PUBLIC_KEY"

# Fix: Implement backoff (automatic in bot)
# Wait and retry
sleep 60
sudo systemctl restart trading-bot
```

---

### Issue 3: WebSocket Connection Issues

#### Symptoms
- "WebSocket disconnected"
- No real-time price updates
- WebSocket reconnection loops

#### Diagnostic Steps

```bash
# 1. Check WebSocket status
curl http://localhost:8000/api/bot/health | jq '.data.components.websocket'

# 2. Test WebSocket manually
wscat -c ws://localhost:8000/ws

# 3. Check WebSocket logs
sudo grep -i websocket /opt/trading-bot/trading_bot_v2/logs/*.log

# 4. Verify network for WebSocket
nc -zv api.pacifica.fi 443
```

#### Common Causes & Fixes

**Cause: WebSocket Library Missing**
```bash
# Install websockets library
/opt/trading-bot/venv/bin/pip install websockets

sudo systemctl restart trading-bot
```

**Cause: Connection Timeout**
```bash
# Check connection stability
ping -c 10 api.pacifica.fi

# If packet loss, check network
mtr api.pacifica.fi

# Increase timeout in config (if needed)
echo "WS_TIMEOUT=30" >> /opt/trading-bot/trading_bot_v2/.env
```

---

### Issue 4: Trading Issues

#### Symptoms
- Trades not executing
- Order placement failures
- Unexpected position sizes

#### Diagnostic Steps

```bash
# 1. Check account balance
curl http://localhost:8000/api/status | jq '.data.account_balance'

# 2. Check positions
curl http://localhost:8000/api/positions | jq '.data | length'

# 3. Check trading logs
sudo grep -i "trade\|order" /opt/trading-bot/trading_bot_v2/logs/*.log

# 4. Verify strategy status
curl http://localhost:8000/api/strategies | jq '.data[] | {name, enabled}'

# 5. Check circuit breaker
curl http://localhost:8000/api/status | jq '.data.circuit_breaker_triggered'
```

#### Common Causes & Fixes

**Cause: Insufficient Balance**
```bash
# Error: "Insufficient balance"

# Check balance
curl http://localhost:8000/api/status | jq '.data.account_balance'

# Fix: Deposit funds or reduce position size
# Update max position size in config
sudo nano /opt/trading-bot/trading_bot_v2/.env
# MAX_POSITIONS=3  # Reduce from 5

sudo systemctl restart trading-bot
```

**Cause: Circuit Breaker Triggered**
```bash
# Error: "Circuit breaker active"

# Check circuit breaker status
curl http://localhost:8000/api/status | jq '.data.circuit_breaker_triggered'

# Fix: Reset circuit breaker (requires manual review)
# 1. Review loss logs
sudo grep -i "drawdown\|loss" /opt/trading-bot/trading_bot_v2/logs/*.log

# 2. Reset only if appropriate
curl -X POST http://localhost:8000/api/bot/reset-circuit-breaker
```

**Cause: Disabled Strategies**
```bash
# Check which strategies are enabled
curl http://localhost:8000/api/strategies | jq '.data[] | select(.enabled == false) | .name'

# Enable strategy
curl -X POST http://localhost:8000/api/strategies/mean_reversion/toggle \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

**Cause: Grid Issues**
```bash
# Check active grids
curl http://localhost:8000/api/grids | jq '.data | length'

# Check grid state
sudo grep -i "grid" /opt/trading-bot/trading_bot_v2/logs/*.log | tail -20

# Fix stuck grid
curl -X POST http://localhost:8000/api/grids/{grid_id}/stop \
  -H "Content-Type: application/json" \
  -d '{"close_positions": true}'
```

---

### Issue 5: Performance Issues

#### Symptoms
- Slow response times
- High CPU/memory usage
- Database timeouts

#### Diagnostic Steps

```bash
# 1. Check system resources
htop
df -h
free -h

# 2. Check bot resource usage
ps aux | grep trading-bot
sudo systemctl status trading-bot

# 3. Check database performance
curl http://localhost:8000/api/metrics/system | jq '.data'

# 4. Profile slow queries
sudo sqlite3 /opt/trading-bot/trading_bot_v2/data/trading_bot.db
> .timer on
> SELECT COUNT(*) FROM trades;
> .quit
```

#### Common Causes & Fixes

**Cause: High Memory Usage**
```bash
# Check memory usage
free -h
ps aux --sort=-%mem | head -10

# Fix: Restart service to clear memory
sudo systemctl restart trading-bot

# Enable memory monitoring
echo "ENABLE_MEMORY_MONITORING=true" >> /opt/trading-bot/trading_bot_v2/.env

# Schedule automatic restart if needed
# Add to crontab: 0 4 * * * systemctl restart trading-bot
```

**Cause: Slow Database Queries**
```bash
# Optimize database
sudo sqlite3 /opt/trading-bot/trading_bot_v2/data/trading_bot.db << 'EOF'
-- Analyze tables
ANALYZE;

-- Check for missing indexes
.schema trades
.schema positions

-- Vacuum database
VACUUM;
EOF

# Consider archiving old data
# Move trades older than 90 days to archive table
```

**Cause: Too Many Open Files**
```bash
# Check file descriptors
lsof | grep trading-bot | wc -l
ulimit -n

# Increase limits
sudo nano /etc/security/limits.conf
# Add:
tradingbot soft nofile 65536
tradingbot hard nofile 65536

# Re-login or restart service
sudo systemctl restart trading-bot
```

---

## Advanced Diagnostics

### Debug Mode

Enable debug logging for detailed diagnostics:

```bash
# Enable debug mode
sudo nano /opt/trading-bot/trading_bot_v2/.env
# Set: LOG_LEVEL=DEBUG

sudo systemctl restart trading-bot

# Monitor debug logs
sudo tail -f /opt/trading-bot/trading_bot_v2/logs/trading-bot.log
```

### Health Check Script

```bash
#!/bin/bash
# comprehensive_health_check.sh

echo "=== Trading Bot Health Check ==="
echo "Timestamp: $(date)"
echo ""

# Service status
echo "1. Service Status:"
sudo systemctl is-active trading-bot && echo "   ✅ Running" || echo "   ❌ Not Running"
echo ""

# API health
echo "2. API Health:"
STATUS=$(curl -s http://localhost:8000/api/status)
if [ $? -eq 0 ]; then
    echo "   ✅ API Responding"
    echo "   Bot Running: $(echo $STATUS | jq -r '.data.is_running')"
    echo "   Positions: $(echo $STATUS | jq -r '.data.positions_count')"
    echo "   Balance: $(echo $STATUS | jq -r '.data.account_balance')"
else
    echo "   ❌ API Not Responding"
fi
echo ""

# System resources
echo "3. System Resources:"
echo "   CPU: $(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)%"
echo "   Memory: $(free | grep Mem | awk '{printf "%.1f%%", $3/$2 * 100.0}')"
echo "   Disk: $(df -h /opt/trading-bot | tail -1 | awk '{print $5}')"
echo ""

# Recent errors
echo "4. Recent Errors (Last Hour):"
ERRORS=$(sudo grep -i "error\|exception\|failed" /opt/trading-bot/trading_bot_v2/logs/*.log 2>/dev/null | wc -l)
echo "   Error Count: $ERRORS"
if [ $ERRORS -gt 10 ]; then
    echo "   ⚠️  High error count detected"
fi
echo ""

# Database check
echo "5. Database Check:"
sudo sqlite3 /opt/trading-bot/trading_bot_v2/data/trading_bot.db "SELECT COUNT(*) FROM trades;" 2>/dev/null && echo "   ✅ Database Accessible" || echo "   ❌ Database Error"
echo ""

echo "=== End Health Check ==="
```

### Performance Profiling

```python
# profile_performance.py
import asyncio
import time
from performance_monitor import get_performance_monitor

async def profile():
    monitor = await get_performance_monitor()
    await monitor.start()
    
    # Collect metrics for 60 seconds
    await asyncio.sleep(60)
    
    # Get summary
    summary = await monitor.get_summary()
    
    print("=== Performance Profile ===")
    print(f"CPU Usage: {summary['system']['cpu_percent']}%")
    print(f"Memory Usage: {summary['system']['memory_percent']}%")
    print(f"Active Tasks: {summary['tasks']['pending']}")
    print(f"Avg Task Time: {summary['tasks']['avg_processing_time']}ms")
    
    # Identify bottlenecks
    if summary['system']['cpu_percent'] > 80:
        print("⚠️  High CPU usage detected")
    if summary['system']['memory_percent'] > 85:
        print("⚠️  High memory usage detected")

asyncio.run(profile())
```

---

## Log Analysis

### Common Log Patterns

```bash
# Search for errors
sudo grep -i "error\|exception\|traceback" /opt/trading-bot/trading_bot_v2/logs/*.log

# Search for trading activity
sudo grep -i "trade\|order\|position" /opt/trading-bot/trading_bot_v2/logs/*.log

# Search for WebSocket issues
sudo grep -i "websocket\|connection" /opt/trading-bot/trading_bot_v2/logs/*.log

# Real-time log monitoring
sudo tail -f /opt/trading-bot/trading_bot_v2/logs/trading-bot.log | grep -i error
```

### Log Rotation Issues

```bash
# Check log file sizes
ls -lh /opt/trading-bot/trading_bot_v2/logs/

# If logs are too large, force rotation
sudo logrotate -f /etc/logrotate.d/trading-bot

# Clear old logs manually (if needed)
sudo find /opt/trading-bot/trading_bot_v2/logs/ -name "*.log.*" -mtime +30 -delete
```

---

## Recovery Procedures

### Complete Service Recovery

```bash
#!/bin/bash
# emergency_recovery.sh

echo "=== Emergency Recovery ==="

# 1. Stop service
echo "Stopping service..."
sudo systemctl stop trading-bot

# 2. Backup current state
echo "Creating backup..."
BACKUP_DIR="/opt/backups/recovery/$(date +%Y%m%d_%H%M%S)"
mkdir -p $BACKUP_DIR
cp /opt/trading-bot/trading_bot_v2/data/trading_bot.db $BACKUP_DIR/
cp /opt/trading-bot/trading_bot_v2/.env $BACKUP_DIR/
cp /opt/trading-bot/trading_bot_v2/logs/*.log $BACKUP_DIR/

# 3. Clear temporary data
echo "Clearing temporary data..."
rm -f /opt/trading-bot/trading_bot_v2/data/*.tmp
rm -f /opt/trading-bot/trading_bot_v2/data/*.lock

# 4. Check database integrity
echo "Checking database..."
sudo sqlite3 /opt/trading-bot/trading_bot_v2/data/trading_bot.db "PRAGMA integrity_check;"

# 5. Fix permissions
echo "Fixing permissions..."
sudo chown -R tradingbot:tradingbot /opt/trading-bot
sudo chmod 600 /opt/trading-bot/trading_bot_v2/.env

# 6. Start service
echo "Starting service..."
sudo systemctl start trading-bot
sleep 5

# 7. Verify
echo "Verifying..."
if sudo systemctl is-active --quiet trading-bot; then
    echo "✅ Service recovered successfully"
    curl -s http://localhost:8000/api/status | jq '.data.is_running'
else
    echo "❌ Recovery failed. Check logs:"
    sudo journalctl -u trading-bot -n 50
fi
```

### Database Recovery

```bash
# If database is corrupted

# 1. Stop service
sudo systemctl stop trading-bot

# 2. Backup corrupted database
cp /opt/trading-bot/trading_bot_v2/data/trading_bot.db \
   /opt/trading-bot/trading_bot_v2/data/trading_bot.db.corrupt.$(date +%Y%m%d)

# 3. Attempt repair
sqlite3 /opt/trading-bot/trading_bot_v2/data/trading_bot.db << 'EOF'
.mode insert
.output dump.sql
.dump
.quit
EOF

# 4. Recreate database
mv /opt/trading-bot/trading_bot_v2/data/trading_bot.db \
   /opt/trading-bot/trading_bot_v2/data/trading_bot.db.old

sqlite3 /opt/trading-bot/trading_bot_v2/data/trading_bot.db < dump.sql

# 5. Start service
sudo systemctl start trading-bot
```

---

## Escalation Procedures

### When to Escalate

| Issue | Severity | Escalate To |
|-------|----------|-------------|
| Security breach | CRITICAL | Security Team |
| Data loss | CRITICAL | DevOps Lead |
| Funds at risk | CRITICAL | Trading Team Lead |
| Service down >1hr | HIGH | Operations Team |
| Performance degraded | MEDIUM | Development Team |
| Minor issues | LOW | Self-service (this guide) |

### Escalation Contacts

```
L1 Support: support@yourcompany.com
L2 Engineering: engineering@yourcompany.com
Security: security@yourcompany.com (24/7)
On-Call: +1-xxx-xxx-xxxx
Pacifica Support: support@pacifica.fi
```

### Incident Report Template

```markdown
## Incident Report

**Date/Time**: YYYY-MM-DD HH:MM  
**Severity**: [CRITICAL/HIGH/MEDIUM/LOW]  
**Reporter**: Name  

### Summary
Brief description of the issue

### Impact
- Affected systems:
- User impact:
- Financial impact:

### Timeline
- HH:MM - Issue detected
- HH:MM - Initial response
- HH:MM - Escalation
- HH:MM - Resolution

### Root Cause
[To be filled during post-mortem]

### Resolution
Steps taken to resolve

### Prevention
Actions to prevent recurrence
```

---

## Useful Commands Reference

### Service Management
```bash
# Start/Stop/Restart
sudo systemctl start trading-bot
sudo systemctl stop trading-bot
sudo systemctl restart trading-bot

# Status and logs
sudo systemctl status trading-bot
sudo journalctl -u trading-bot -f
sudo journalctl -u trading-bot --since "1 hour ago"
```

### API Testing
```bash
# Health check
curl http://localhost:8000/api/status | jq

# Bot controls
curl -X POST http://localhost:8000/api/bot/start
curl -X POST http://localhost:8000/api/bot/stop

# Get positions
curl http://localhost:8000/api/positions | jq

# Get trades
curl http://localhost:8000/api/trades | jq
```

### Database Operations
```bash
# Connect to database
sudo sqlite3 /opt/trading-bot/trading_bot_v2/data/trading_bot.db

# Common queries
SELECT * FROM positions WHERE status = 'open';
SELECT COUNT(*) FROM trades WHERE status = 'closed';
SELECT SUM(pnl) FROM trades WHERE status = 'closed';
```

### Network Diagnostics
```bash
# Test connectivity
ping api.pacifica.fi
curl -I https://api.pacifica.fi

# Check ports
sudo lsof -i :8000
sudo netstat -tulpn | grep 8000

# Trace route
mtr api.pacifica.fi
```

---

**For issues not covered in this guide, refer to [PERFORMANCE_MONITORING.md](./PERFORMANCE_MONITORING.md) or escalate to the engineering team.**
