# System Monitoring Instructions for Trading Bot v2

**Target User:** OpenAI o1 Model (Deep Reasoning)
**Purpose:** Monitor trading bot health and report issues
**System:** Pacifica.fi Testnet Trading Bot with Multi-Strategy Regime Detection

---

## 1. System Overview

### Architecture
- **API Server**: FastAPI running on `http://localhost:8000`
- **Database**: SQLite at `trading_bot_v2/data/trading_bot.db`
- **Exchange**: Pacifica.fi Testnet API (`https://test-api.pacifica.fi`)
- **Strategies**: Mean Reversion, MA Crossover, Grid Trading, Liquidation Capture
- **Regime Detection**: ADX-based market classification (Trending Strong, Ranging Calm, Ranging Volatile, Indecisive)

### Key Components
1. **Trading Bot Loop** (`trading_bot.py`): 60-second cycle
2. **Strategy Manager** (`strategy_manager.py`): Signal generation and conflict resolution
3. **Position Tracking**: Real-time sync with Pacifica positions
4. **Risk Management**: 10% circuit breaker, position limits, Kelly Criterion sizing
5. **Funding Tracking**: Hourly perpetual futures funding (Pacifica-specific)

---

## 2. Health Check Procedures

### 2.1 API Server Health Check

**Command:**
```bash
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
curl -s http://localhost:8000/api/status
```

**Expected Response:**
```json
{
  "success": true,
  "data": {
    "bot_running": true|false,
    "positions_count": <number>,
    "trades_count": <number>,
    "total_pnl": <number>
  }
}
```

**Issues to Report:**
- ❌ No response (server down)
- ❌ `"success": false`
- ❌ HTTP error codes (500, 502, 503)
- ⚠️ `bot_running: false` (bot stopped but server running)

---

### 2.2 Position Sync Verification

**Command:**
```bash
curl -s http://localhost:8000/api/positions/sync | python -m json.tool
```

**Expected Response:**
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTC",
      "side": "long|short",
      "quantity": <number>,
      "entry_price": <number>,
      "current_price": <number>,
      "unrealized_pnl": <number>,
      "funding_pnl": <number>,
      "updated_at": "<timestamp>"
    }
  ],
  "synced_from_pacifica": <number>
}
```

**Issues to Report:**
- ❌ `funding_pnl: 0.0` for all positions (funding not syncing)
- ❌ `current_price: 0` or missing (price fetch failing)
- ❌ `synced_from_pacifica` doesn't match actual Pacifica positions count
- ⚠️ `unrealized_pnl` calculation seems incorrect based on side and price

**Validation Formula:**
```python
# Long position
unrealized_pnl = (current_price - entry_price) * quantity

# Short position
unrealized_pnl = (entry_price - current_price) * quantity
```

---

### 2.3 Market Activity Check

**Command:**
```bash
curl -s http://localhost:8000/api/activity | python -m json.tool
```

**Expected Response:** Array of markets with:
```json
{
  "symbol": "BTC",
  "price": "$90917.0000",
  "regime": "Trending Strong|Ranging Calm|Ranging Volatile|Indecisive",
  "rsi_15m": "45.2",
  "rsi_1h": "52.1",
  "active_strategies": "Trend Following, MA Crossover|Mean Reversion|Grid Trading|None",
  "status": "Monitoring for entry conditions|Waiting for RSI extreme|..."
}
```

**Issues to Report:**
- ❌ Empty array (no markets being monitored)
- ❌ Less than 10 markets (should monitor 10 markets including BTC)
- ❌ All markets showing "Indecisive" regime (ADX calculation may be broken)
- ❌ RSI values showing "NaN" or "0.0" for all markets (indicator failure)
- ⚠️ No active strategies for markets in Trending/Ranging regimes

---

### 2.4 Database Integrity Check

**Command:**
```bash
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python -c "
import sqlite3
conn = sqlite3.connect('data/trading_bot.db')

# Check positions table
cursor = conn.execute('SELECT COUNT(*), SUM(funding_pnl) FROM positions WHERE quantity > 0')
pos_count, total_funding = cursor.fetchone()
print(f'Open positions: {pos_count}')
print(f'Total funding P&L: ${total_funding}')

# Check trades table
cursor = conn.execute('SELECT COUNT(*), strategy FROM trades GROUP BY strategy')
print('\nTrades by strategy:')
for row in cursor:
    print(f'  {row[1]}: {row[0]} trades')

# Check for recent activity
cursor = conn.execute('SELECT MAX(updated_at) FROM positions')
last_update = cursor.fetchone()[0]
print(f'\nLast position update: {last_update}')

conn.close()
"
```

**Expected Output:**
```
Open positions: <number>
Total funding P&L: $<number>

Trades by strategy:
  mean_reversion: <count>
  ma_crossover: <count>
  ...

Last position update: 2026-01-11 14:21:09
```

**Issues to Report:**
- ❌ Database file missing or corrupted
- ❌ `last_position_update` older than 5 minutes (position sync stopped)
- ❌ All funding_pnl values are 0.0 (funding not being saved)
- ⚠️ Trades count not increasing (strategies not generating signals)

---

### 2.5 Log Analysis

**Command:**
```bash
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
tail -100 <log_file_or_stdout> | grep -i "error\|warning\|critical"
```

**Critical Keywords to Search:**
- `CIRCUIT BREAKER TRIGGERED` (10% loss threshold reached)
- `Error in trading loop`
- `Error updating positions`
- `Error checking signals`
- `Failed to initialize`
- `Connection refused`
- `Rate limit exceeded`
- `Authentication failed`

**Issues to Report:**
- ❌ Any `CRITICAL` level logs
- ❌ Repeated errors (same error > 10 times in last 100 lines)
- ❌ Circuit breaker triggered
- ⚠️ API rate limiting warnings

---

## 3. Performance Metrics to Track

### 3.1 Signal Generation Rate

**What to Check:**
```bash
curl -s http://localhost:8000/api/activity | python -c "
import json, sys
data = json.load(sys.stdin)
markets_with_strategies = [m for m in data['data'] if m['active_strategies'] != 'None']
print(f'Markets with active strategies: {len(markets_with_strategies)}/{len(data[\"data\"])}')
"
```

**Normal Range:**
- Trending markets: 40-60% should have Trend Following + MA Crossover active
- Ranging markets: Should have Mean Reversion or Grid Trading active
- At least 3-5 markets with active strategies at any time

**Issues to Report:**
- ⚠️ 0 markets with active strategies for > 1 hour
- ⚠️ All markets showing same regime (likely calculation error)

---

### 3.2 Position Limits Compliance

**What to Check:**
```python
import sqlite3
conn = sqlite3.connect('data/trading_bot.db')

# Global position limit
cursor = conn.execute('SELECT COUNT(*) FROM positions WHERE quantity > 0')
total_positions = cursor.fetchone()[0]
print(f'Total positions: {total_positions} / 15 (MAX_POSITIONS)')

# Grid position limit
cursor = conn.execute("SELECT COUNT(*) FROM positions WHERE quantity > 0 AND symbol LIKE '%GRID%'")
grid_positions = cursor.fetchone()[0]
print(f'Grid positions: {grid_positions} / 10 (MAX_GRID_POSITIONS)')

conn.close()
```

**Issues to Report:**
- ❌ Total positions > 15 (MAX_POSITIONS exceeded)
- ❌ Grid positions > 10 (MAX_GRID_POSITIONS exceeded)
- ⚠️ Positions stuck at max limits (may prevent new trades)

---

### 3.3 Funding Cost Analysis

**What to Check:**
```python
import sqlite3
conn = sqlite3.connect('data/trading_bot.db')

cursor = conn.execute('''
    SELECT symbol, side, funding_pnl,
           ROUND((funding_pnl / (quantity * entry_price)) * 100, 2) as funding_pct
    FROM positions
    WHERE quantity > 0
    ORDER BY funding_pnl ASC
''')

print('Position | Side | Funding P&L | % of Position Value')
print('-' * 60)
for row in cursor:
    print(f'{row[0]:8} | {row[1]:5} | ${row[2]:8.2f} | {row[3]:5.2f}%')

conn.close()
```

**Issues to Report:**
- ⚠️ Any position with funding cost > 5% of position value (excessive holding cost)
- ⚠️ Grid positions with negative funding > $50 (consider closing)
- 📊 Report average funding rate across all positions

---

## 4. Common Failure Modes

### 4.1 Candle Data Fetch Failures

**Symptoms:**
- Activity endpoint shows all strategies inactive
- Logs show: `Time range too large for 15m interval`

**Diagnostic Command:**
```bash
curl -s http://localhost:8000/api/activity | grep -c "No market data"
```

**Root Cause:** Timestamp conversion error (seconds vs milliseconds)

**Fix Location:** `multi_timeframe_fetcher.py` line 96

---

### 4.2 Position Sync Failures

**Symptoms:**
- Web interface shows 0 positions despite open trades on Pacifica
- Positions endpoint returns empty array

**Diagnostic Command:**
```python
from pacifica_client import PacificaClient, PacificaEnvironment
from config import config

client = PacificaClient(
    config.pacifica_private_key,
    config.pacifica_public_key,
    PacificaEnvironment.TESTNET
)

positions = client.get_positions()
print(f'Pacifica positions: {len(positions)}')
print(f'Database positions: <check via /api/positions>')
```

**Root Cause:** Field mapping mismatch (Pacifica uses 'amount' not 'size', 'bid'/'ask' not 'long'/'short')

**Fix Location:** `api_server.py` lines 100-122, `trading_bot.py` lines 119-144

---

### 4.3 Funding P&L Not Updating

**Symptoms:**
- All positions show `funding_pnl: 0.0`
- Pacifica API returns funding values correctly

**Diagnostic Command:**
```python
# Check raw Pacifica response
positions = client.get_positions()
for pos in positions:
    print(f"{pos['symbol']}: funding = {pos.get('funding', 'MISSING')}")

# Check database values
cursor = conn.execute('SELECT symbol, funding_pnl FROM positions')
for row in cursor:
    print(f"{row[0]}: funding_pnl = {row[1]}")
```

**Root Cause:** `save_position()` not including funding_pnl in UPDATE/INSERT

**Fix Location:** `Example files/core_logic/database.py` lines 539-581

---

### 4.4 Circuit Breaker False Triggers

**Symptoms:**
- Bot stops trading with message: "CIRCUIT BREAKER TRIGGERED"
- Total P&L is not actually at -10%

**Diagnostic Command:**
```python
positions = db.get_positions()
total_pnl = sum(pos.get('unrealized_pnl', 0) + pos.get('funding_pnl', 0) for pos in positions)
account_balance = <get from config or API>
loss_pct = (total_pnl / account_balance) * 100
print(f'Total P&L: ${total_pnl:.2f} ({loss_pct:.2f}%)')
print(f'Circuit breaker threshold: -10%')
```

**Root Cause:** Funding P&L not included in circuit breaker calculation

**Fix Location:** `trading_bot.py` lines 412-446 (`_monitor_risk()`)

---

## 5. Reporting Format

When reporting issues, use this structure:

```markdown
## Issue Report - [TIMESTAMP]

**Severity:** CRITICAL | HIGH | MEDIUM | LOW
**Component:** API Server | Trading Bot | Database | Position Sync | Strategy Manager
**Status:** Active | Resolved | Monitoring

### Symptoms
- Bullet list of observed issues
- Include specific error messages
- Note when issue first appeared

### Diagnostic Results
```
<paste command output>
```

### Impact Assessment
- Trading: Halted | Degraded | Normal
- Data Integrity: Compromised | Intact
- Risk Exposure: Increased | Normal

### Recommended Actions
1. Immediate steps (if critical)
2. Investigation needed
3. Long-term fixes

### Related Metrics
- Affected positions: <count>
- Lost trading opportunities: <estimate>
- Potential P&L impact: $<amount>
```

---

## 6. Automated Monitoring Script

**Create:** `monitor_bot.py`

```python
#!/usr/bin/env python3
"""
Automated health check script for Trading Bot v2
Run every 5 minutes via cron/scheduler
"""

import requests
import sqlite3
import json
from datetime import datetime, timedelta

def check_api_health():
    """Check if API server is responding."""
    try:
        r = requests.get('http://localhost:8000/api/status', timeout=5)
        data = r.json()
        if not data.get('success'):
            return {'status': 'FAIL', 'msg': 'API returned success=false'}
        if not data['data'].get('bot_running'):
            return {'status': 'WARN', 'msg': 'Bot not running'}
        return {'status': 'OK', 'msg': 'API healthy'}
    except Exception as e:
        return {'status': 'CRITICAL', 'msg': f'API unreachable: {e}'}

def check_position_sync():
    """Check if positions are syncing from Pacifica."""
    try:
        conn = sqlite3.connect('data/trading_bot.db')
        cursor = conn.execute('SELECT MAX(updated_at) FROM positions')
        last_update = cursor.fetchone()[0]
        conn.close()

        if not last_update:
            return {'status': 'WARN', 'msg': 'No positions in database'}

        # Check if update is recent (within 5 minutes)
        # Note: This is a simplified check - implement proper datetime parsing
        return {'status': 'OK', 'msg': f'Last update: {last_update}'}
    except Exception as e:
        return {'status': 'FAIL', 'msg': f'Database error: {e}'}

def check_funding_tracking():
    """Check if funding P&L is being tracked."""
    try:
        conn = sqlite3.connect('data/trading_bot.db')
        cursor = conn.execute('SELECT COUNT(*), SUM(ABS(funding_pnl)) FROM positions WHERE quantity > 0')
        count, total_funding = cursor.fetchone()
        conn.close()

        if count > 0 and total_funding == 0:
            return {'status': 'WARN', 'msg': f'{count} positions with zero funding'}
        return {'status': 'OK', 'msg': f'Funding tracked: ${total_funding:.2f}'}
    except Exception as e:
        return {'status': 'FAIL', 'msg': f'Database error: {e}'}

def check_signal_generation():
    """Check if strategies are generating signals."""
    try:
        r = requests.get('http://localhost:8000/api/activity', timeout=10)
        data = r.json()
        markets = data.get('data', [])

        active_count = sum(1 for m in markets if m.get('active_strategies') != 'None')

        if len(markets) < 10:
            return {'status': 'WARN', 'msg': f'Only {len(markets)} markets monitored (expected 10)'}
        if active_count == 0:
            return {'status': 'WARN', 'msg': 'No markets with active strategies'}

        return {'status': 'OK', 'msg': f'{active_count}/{len(markets)} markets active'}
    except Exception as e:
        return {'status': 'FAIL', 'msg': f'Activity check failed: {e}'}

def main():
    """Run all health checks and report results."""
    print(f"\n{'='*60}")
    print(f"Trading Bot Health Check - {datetime.now()}")
    print(f"{'='*60}\n")

    checks = {
        'API Server': check_api_health(),
        'Position Sync': check_position_sync(),
        'Funding Tracking': check_funding_tracking(),
        'Signal Generation': check_signal_generation()
    }

    # Print results
    critical_count = 0
    for name, result in checks.items():
        status = result['status']
        msg = result['msg']

        emoji = {
            'OK': '✅',
            'WARN': '⚠️',
            'FAIL': '❌',
            'CRITICAL': '🚨'
        }.get(status, '❓')

        print(f"{emoji} {name:20} [{status:8}] {msg}")

        if status in ['CRITICAL', 'FAIL']:
            critical_count += 1

    print(f"\n{'='*60}")

    if critical_count > 0:
        print(f"⚠️  {critical_count} critical issue(s) detected!")
        return 1
    else:
        print("✅ All systems operational")
        return 0

if __name__ == '__main__':
    exit(main())
```

**Usage:**
```bash
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python monitor_bot.py
```

---

## 7. Key Configuration Values

**File:** `.env`

```env
# Critical Settings to Monitor
ENABLE_AUTO_TRADING=true          # If false, bot won't place real orders
TESTNET=true                      # Must be true for testnet
MAX_POSITIONS=15                  # Global position limit
MAX_GRID_POSITIONS=10             # Grid-specific limit
CIRCUIT_BREAKER_LOSS_PCT=0.10    # 10% portfolio loss stops trading
```

**Verify Configuration:**
```bash
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python -c "from config import config; print(f'Auto-trading: {config.enable_auto_trading}'); print(f'Testnet: {config.testnet}'); print(f'Circuit breaker: {config.circuit_breaker_loss_pct * 100}%')"
```

---

## 8. Emergency Procedures

### 8.1 Stop Trading Immediately

```bash
curl -X POST http://localhost:8000/api/bot/stop
```

**Verification:**
```bash
curl -s http://localhost:8000/api/status | grep "bot_running"
# Should return: "bot_running": false
```

---

### 8.2 Close All Positions (Manual)

**Note:** Bot does not have automatic position closing. Must be done manually via Pacifica UI or API.

---

### 8.3 Restart Bot

```bash
# Stop bot
curl -X POST http://localhost:8000/api/bot/stop

# Wait 10 seconds
sleep 10

# Start bot
curl -X POST http://localhost:8000/api/bot/start
```

---

## 9. Performance Baselines

**Normal Operating Ranges:**

| Metric | Normal Range | Warning Threshold | Critical Threshold |
|--------|--------------|-------------------|-------------------|
| API Response Time | < 500ms | > 2s | > 5s |
| Position Count | 0-15 | N/A | > 15 |
| Signal Generation | 3-7 markets active | < 2 markets | 0 markets |
| Funding Cost (per position) | -2% to +2% | > 3% | > 5% |
| Position Sync Lag | < 2 minutes | > 5 minutes | > 10 minutes |
| Database Queries | < 100ms | > 500ms | > 1s |

---

## 10. Contact Information

**Developer:** User z_shi
**System Location:** `C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2`
**Documentation:** `CLAUDE.md`, `MONITORING_INSTRUCTIONS.md`
**GitHub Issues:** (if applicable)

---

## Appendix A: Quick Reference Commands

```bash
# Health check
curl -s http://localhost:8000/api/status

# Force position sync
curl -s http://localhost:8000/api/positions/sync

# View market activity
curl -s http://localhost:8000/api/activity

# Stop bot
curl -X POST http://localhost:8000/api/bot/stop

# Start bot
curl -X POST http://localhost:8000/api/bot/start

# Check database
sqlite3 data/trading_bot.db "SELECT COUNT(*) FROM positions WHERE quantity > 0"

# Check logs (if logging to file)
tail -f trading_bot.log
```

---

**Version:** 1.0
**Last Updated:** 2026-01-11
**Compatible With:** Trading Bot v2 (Multi-Strategy + Funding Tracking)
