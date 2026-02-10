# Trading Bot v2 - Monitoring System

## Quick Start

### Run Automated Health Check
```bash
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python monitor_bot.py
```

**Sample Output:**
```
======================================================================
Trading Bot v2 - Health Check Report
Timestamp: 2026-01-11 09:29:14
======================================================================

✅ API Server                [OK      ] Healthy - 3 positions, P&L: $-283.90
✅ Position Sync             [OK      ] 3 positions, updated 0.5m ago
✅ Funding Tracking          [OK      ] Tracked across 3 positions (avg $75.98)
✅ Signal Generation         [OK      ] 5/10 markets active (50%)
✅ Circuit Breaker           [OK      ] Portfolio P&L: $-283.90
✅ Position Limits           [OK      ] 3/15 positions (Grid: 0/10)

======================================================================
✅ All 6 checks passed. System operational.
```

### Exit Codes
- `0` - All checks passed
- `1` - Warnings detected (review recommended)
- `2` - Critical issues detected (immediate action required)

---

## Usage Options

### Standard Output (Human-Readable)
```bash
python monitor_bot.py
```

### JSON Output (For Parsing)
```bash
python monitor_bot.py --json > monitoring_results.json
```

**JSON Structure:**
```json
{
  "timestamp": "2026-01-11T09:29:14",
  "checks": {
    "api": {
      "name": "API Server",
      "status": "OK",
      "message": "Healthy - 3 positions, P&L: $-283.90",
      "details": {...}
    },
    ...
  },
  "summary": {
    "total": 6,
    "ok": 6,
    "warn": 0,
    "fail": 0,
    "critical": 0
  }
}
```

### Run Specific Check
```bash
python monitor_bot.py --check api
python monitor_bot.py --check position_sync
python monitor_bot.py --check funding
python monitor_bot.py --check signals
python monitor_bot.py --check circuit_breaker
python monitor_bot.py --check position_limits
```

---

## Setting Up Scheduled Monitoring

### Windows Task Scheduler (Every 5 Minutes)

1. **Create batch file:** `monitor_bot.bat`
```batch
@echo off
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python monitor_bot.py >> monitoring_log.txt 2>&1
```

2. **Open Task Scheduler:** `Win + R` → `taskschd.msc`

3. **Create Basic Task:**
   - Name: Trading Bot Monitor
   - Trigger: Daily, repeat every 5 minutes
   - Action: Start a program
   - Program: `C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2\monitor_bot.bat`

4. **View Logs:**
```bash
tail -100 monitoring_log.txt
```

### Linux/Mac (Cron Job)

Add to crontab:
```bash
*/5 * * * * cd /path/to/trading_bot_v2 && /usr/bin/python3 monitor_bot.py >> monitoring_log.txt 2>&1
```

---

## What Gets Checked

### 1. API Server Health
- ✅ Server responds to requests
- ✅ Returns valid JSON
- ✅ Bot running status
- ✅ Position and trade counts
- ✅ Total P&L

**Warning Triggers:**
- Bot not running (but server is up)
- Unexpected response format

**Critical Triggers:**
- Connection refused
- Request timeout (>10s)
- HTTP errors (500, 502, 503)

---

### 2. Position Sync
- ✅ Positions syncing from Pacifica
- ✅ Database update timestamps
- ✅ Data freshness (within 5 minutes)

**Warning Triggers:**
- Last sync 5-10 minutes ago
- Positions exist but no timestamp

**Critical Triggers:**
- Last sync >10 minutes ago (stale data)

---

### 3. Funding Tracking
- ✅ Funding P&L being recorded
- ✅ Non-zero funding for positions
- ✅ Average funding per position

**Warning Triggers:**
- All positions showing $0 funding (may indicate new positions or sync issue)

---

### 4. Signal Generation
- ✅ Markets being monitored (should be 10)
- ✅ Active strategies count
- ✅ Regime distribution (Trending, Ranging, etc.)
- ✅ Activity percentage

**Warning Triggers:**
- Less than 10 markets monitored
- Zero markets with active strategies

**Critical Triggers:**
- No markets being monitored at all

---

### 5. Circuit Breaker
- ✅ Portfolio P&L calculation
- ✅ Total unrealized + funding P&L
- ✅ Large loss detection

**Warning Triggers:**
- Total P&L < -$1,000 (approaching circuit breaker threshold)

**Note:** Exact percentage calculation requires account balance from API

---

### 6. Position Limits
- ✅ Total positions vs MAX_POSITIONS (15)
- ✅ Grid positions vs MAX_GRID_POSITIONS (10)
- ✅ Near-limit warnings

**Warning Triggers:**
- Positions ≥90% of limit (13+ positions)

**Critical Triggers:**
- Position limit exceeded (>15 total positions)

---

## Manual Checks

### Quick API Test
```bash
curl -s http://localhost:8000/api/status | python -m json.tool
```

### Force Position Sync
```bash
curl -s http://localhost:8000/api/positions/sync | python -m json.tool
```

### View Market Activity
```bash
curl -s http://localhost:8000/api/activity | python -m json.tool
```

### Database Query
```bash
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python -c "
import sqlite3
conn = sqlite3.connect('data/trading_bot.db')
cursor = conn.execute('SELECT symbol, side, unrealized_pnl, funding_pnl FROM positions WHERE quantity > 0')
print('Symbol | Side  | Unrealized | Funding')
print('-' * 45)
for row in cursor:
    print(f'{row[0]:6} | {row[1]:5} | ${row[2]:9.2f} | ${row[3]:8.2f}')
conn.close()
"
```

---

## Emergency Procedures

### Stop Bot Immediately
```bash
curl -X POST http://localhost:8000/api/bot/stop
```

### Start Bot
```bash
curl -X POST http://localhost:8000/api/bot/start
```

### Restart API Server
```bash
# Kill existing process
taskkill /F /IM python.exe

# Start new instance
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
start python api_server.py
```

---

## Interpreting Results

### ✅ OK Status
- System functioning normally
- No action required
- Continue monitoring

### ⚠️ WARN Status
- Non-critical issue detected
- Review recommended within 1 hour
- May impact functionality
- Example: Bot stopped, but server running

### ❌ FAIL Status
- Functional failure detected
- Review required within 15 minutes
- System degraded but operational
- Example: Database query failed

### 🚨 CRITICAL Status
- Critical system failure
- **Immediate action required**
- System may be completely down
- Example: API server unreachable

---

## Integration with OpenAI o1 (OpenCode)

For automated monitoring with deep reasoning:

1. **Copy monitoring instructions:**
   - `MONITORING_INSTRUCTIONS.md` - Full monitoring guide
   - `monitor_bot.py` - Automated health check script

2. **Provide to o1 model:**
   ```
   Please monitor the trading bot system using the instructions in
   MONITORING_INSTRUCTIONS.md. Run monitor_bot.py every 5 minutes and
   report any issues following the reporting format in section 5.
   ```

3. **Expected o1 behavior:**
   - Run automated health checks
   - Analyze results with deep reasoning
   - Identify patterns and anomalies
   - Generate detailed issue reports
   - Recommend corrective actions

4. **Sample o1 report format:**
   ```markdown
   ## Issue Report - 2026-01-11 09:30:00

   **Severity:** HIGH
   **Component:** Position Sync
   **Status:** Active

   ### Symptoms
   - Last position sync 12 minutes ago (stale)
   - Expected sync every 60 seconds

   ### Diagnostic Results
   ```
   Position Sync: [FAIL] Last sync 12.3m ago (stale)
   ```

   ### Root Cause Analysis
   Likely causes:
   1. Trading bot loop crashed (check logs for exceptions)
   2. Pacifica API rate limiting (check HTTP 429 errors)
   3. Network connectivity issue

   ### Recommended Actions
   1. IMMEDIATE: Check bot running status via /api/status
   2. Review logs for errors in _update_positions()
   3. If bot stopped, restart via /api/bot/start
   4. Monitor for 5 minutes to confirm sync resumes
   ```

---

## Files Reference

- **`MONITORING_INSTRUCTIONS.md`** - Complete monitoring guide for o1
- **`monitor_bot.py`** - Automated health check script
- **`MONITORING_README.md`** - This file (quick start guide)
- **`monitoring_log.txt`** - Log file (created when scheduled)

---

## Support

**Documentation:**
- `CLAUDE.md` - Project architecture and conventions
- `MONITORING_INSTRUCTIONS.md` - Deep monitoring guide

**Key Endpoints:**
- API: `http://localhost:8000`
- Status: `http://localhost:8000/api/status`
- Positions: `http://localhost:8000/api/positions`
- Activity: `http://localhost:8000/api/activity`

**Database:**
- Path: `trading_bot_v2/data/trading_bot.db`
- Tables: positions, trades, market_data, account_balances

---

**Last Updated:** 2026-01-11
**Version:** 1.0
