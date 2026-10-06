# Trading Bot Monitoring Setup Guide

## Current Status (from last check)
- ✅ API server running
- ⚠️ Bot not running (needs to be started)
- ⚠️ Only 7/10 markets monitored
- ✅ Position sync working (but data is 4+ hours old)
- ✅ Funding tracking active
- ✅ Position limits OK

## Option 1: Windows Task Scheduler with AI Instructions (Recommended)

### Step 1: Test the AI Instruction Generation
```bash
cd "G:\ai-workspace\Bot 3\trading_bot_v2"
python monitor_with_ai_instructions.py
```

**What happens:**
- Runs monitoring checks
- If issues found, creates `ai_instructions_YYYYMMDD_HHMMSS.md` with detailed troubleshooting steps
- You can then copy-paste the instructions to me for immediate assistance

### Step 2: Set Up Scheduled Task

1. **Open Task Scheduler:** Press `Win + R`, type `taskschd.msc`

2. **Create Basic Task:**
   - Name: `Trading Bot AI Monitor`
   - Description: `Monitor trading bot and generate AI troubleshooting instructions`
   - Trigger: Daily, repeat every 5 minutes, duration 1 day

3. **Action:**
   - Start a program
   - Program: `G:\ai-workspace\Bot 3\trading_bot_v2\monitor_ai.bat`
   - Start in: `G:\ai-workspace\Bot 3\trading_bot_v2`

4. **Settings:**
   - ✅ Run with highest privileges
   - ✅ If the task fails, restart every 1 minute up to 3 times

### Step 3: How to Get AI Help When Issues Occur

When the monitoring detects issues, it will:

1. **Create instruction files** like `ai_instructions_20260111_093734.md`
2. **Log the alert** to `monitoring_ai.log`

**To get my help:**
```bash
# Check for new instruction files
dir ai_instructions_*.md

# Read the latest one
type ai_instructions_20260111_093734.md
```

Then copy-paste the troubleshooting instructions to me, and I'll help resolve the issues!

### Step 4: Optional Email Alerts (Advanced)

If you want email alerts in addition to AI instructions, configure `monitor_with_alerts.py`:
```python
SMTP_SERVER = "smtp.gmail.com"
EMAIL_USER = "your-email@gmail.com"
EMAIL_PASS = "your-app-password"
TO_EMAIL = "your-phone@tmomail.net"
```

Then use `monitor_alerts.bat` instead for email alerts.

## Option 2: N8N Workflow Automation

### N8N Workflow Setup

1. **Install n8n** (if not already installed):
```bash
npm install -g n8n
n8n start
```

2. **Create Workflow:**
   - Add **Schedule Trigger** node (every 5 minutes)
   - Add **Execute Command** node:
     ```
     Command: python monitor_with_alerts.py
     Working Directory: G:\ai-workspace\Bot 3\trading_bot_v2
     ```
   - Add **IF** node to check for critical issues
   - Add **Send Email** node for alerts

3. **Sample N8N Workflow JSON:**
```json
{
  "nodes": [
    {
      "name": "Schedule",
      "type": "n8n-nodes-base.scheduleTrigger",
      "parameters": {
        "rule": {
          "interval": 5,
          "unit": "minutes"
        }
      }
    },
    {
      "name": "Execute Monitor",
      "type": "n8n-nodes-base.executeCommand",
      "parameters": {
        "command": "python monitor_with_alerts.py",
        "cwd": "G:/ai-workspace/Bot 3/trading_bot_v2"
      }
    },
    {
      "name": "Parse JSON",
      "type": "n8n-nodes-base.function",
      "parameters": {
        "functionCode": "return {json: JSON.parse(items[0].json.stdout)};"
      }
    },
    {
      "name": "Check Critical",
      "type": "n8n-nodes-base.if",
      "parameters": {
        "conditions": {
          "boolean": [
            {
              "value1": "={{ $json.summary.critical }}",
              "operation": "greaterThan",
              "value2": 0
            }
          ]
        }
      }
    },
    {
      "name": "Send Alert",
      "type": "n8n-nodes-base.emailSend",
      "parameters": {
        "to": "your-email@example.com",
        "subject": "🚨 CRITICAL: Trading Bot Alert",
        "body": "={{ $json }}"
      }
    }
  ],
  "connections": {
    "Schedule": { "main": [[{ "node": "Execute Monitor", "type": "main", "index": 0 }]] },
    "Execute Monitor": { "main": [[{ "node": "Parse JSON", "type": "main", "index": 0 }]] },
    "Parse JSON": { "main": [[{ "node": "Check Critical", "type": "main", "index": 0 }]] },
    "Check Critical": { "main": [[{ "node": "Send Alert", "type": "main", "index": 0 }]] }
  }
}
```

## Option 3: Simple Cron Job (if using WSL)

If you have WSL (Windows Subsystem for Linux):

```bash
# Add to crontab
crontab -e

# Add this line:
*/5 * * * * cd "/mnt/g/ai-workspace/Bot 3/trading_bot_v2" && python3 monitor_with_alerts.py >> monitoring.log 2>&1
```

## Monitoring Dashboard

### Quick Status Check
```bash
cd "G:\ai-workspace\Bot 3\trading_bot_v2"
python monitor_bot.py
```

### View Recent Logs
```bash
tail -20 monitoring_alerts.log
```

### Check Alert History
```bash
grep "Alert sent" monitoring_alerts.log | tail -10
```

## Report Format Standards

### Real-Time Monitoring Reports
All real-time monitoring outputs must include timing context to distinguish from test reports:

```json
{
  "report_type": "real_time_monitoring",
  "generated_at": "2026-01-16T14:30:00Z",
  "monitoring_period": {
    "start": "2026-01-16T14:25:00Z",
    "end": "2026-01-16T14:30:00Z",
    "duration_minutes": 5
  },
  "system_uptime": {
    "total_hours": 47.5,
    "continuous_since": "2026-01-14T19:00:00Z"
  },
  "next_check": "2026-01-16T14:35:00Z",
  "status": "healthy"
}
```

### Stability Test Reports
Stability test reports must clearly show test duration vs actual execution time:

```json
{
  "report_type": "stability_test",
  "test_name": "endurance_24h",
  "execution_context": {
    "start_time": "2026-01-15T10:00:00Z",
    "end_time": "2026-01-16T10:00:00Z",
    "duration_target": "24h 0m",
    "duration_actual": "24h 0m 0s",
    "status": "completed"
  },
  "performance_metrics": {
    "memory_mb": 145.2,
    "cpu_percent": 12.3,
    "events_processed": 43200
  },
  "validation_criteria": {
    "uptime_threshold": ">99.9%",
    "memory_stable": true,
    "no_critical_errors": true
  }
}
```

## 🤖 AI Instruction System

### How It Works

The monitoring system automatically generates **structured troubleshooting instructions** when issues are detected. These instructions are designed to be copied directly to AI assistants for immediate, expert help.

**Example Generated Instructions:**
```
WARNING ISSUE DETECTED:
Component: API Server
Status: WARNING
Message: Bot not running

ACTION NEEDED:
1. The API server is running but the bot is not active
2. Start the trading bot if it should be running
3. Check why the bot stopped

Run diagnostics:
- Start bot: curl -X POST http://localhost:8000/api/bot/start
- Check status: curl http://localhost:8000/api/status
```

### Getting AI Help

1. **Monitor runs automatically** every 5 minutes
2. **When issues detected**: Check for new `ai_instructions_*.md` files
3. **Copy-paste instructions** to me or any AI assistant
4. **Get immediate expert help** with specific, actionable steps

**Quick Check Commands:**
```bash
# See if any instruction files exist
dir ai_instructions_*.md /b

# Read the most recent one
for /f %%i in ('dir ai_instructions_*.md /b /o-d') do type %%i & goto :done
:done
```

## Monitoring Architecture

### Real-Time Monitoring vs Stability Testing

#### Real-Time Monitoring (Continuous Operation)
- **Purpose**: Continuous health monitoring of production systems
- **Duration**: Indefinite (24/7 operation)
- **Frequency**: Configurable intervals (default: 5 minutes for health checks)
- **Scope**: System health, component status, WebSocket connectivity, position sync
- **Output**: Status dashboards, alert generation, AI troubleshooting instructions
- **Example Report**:
  ```json
  {
    "monitoring_type": "real_time",
    "system_status": "healthy",
    "uptime": "47h 23m",
    "last_check": "2026-01-16T14:30:00Z",
    "check_frequency": "5 minutes",
    "next_check": "2026-01-16T14:35:00Z"
  }
  ```

#### Stability Testing (Finite Duration Tests)
- **Purpose**: Validate system reliability under controlled conditions
- **Duration**: Fixed time periods (hours to days)
- **Frequency**: Continuous during test window
- **Scope**: Endurance testing, load testing, memory leak detection, performance benchmarking
- **Output**: Test reports with execution metrics, performance benchmarks, failure analysis
- **Example Report**:
  ```json
  {
    "test_type": "stability_test",
    "test_name": "24_hour_endurance",
    "start_time": "2026-01-15T10:00:00Z",
    "end_time": "2026-01-16T10:00:00Z",
    "duration_actual": "24h 0m 0s",
    "duration_target": "24h 0m 0s",
    "status": "passed",
    "metrics": {
      "memory_usage_mb": 145.2,
      "cpu_usage_percent": 12.3,
      "websocket_uptime_percent": 99.97,
      "events_processed": 43200
    }
  }
  ```

## What Gets Monitored

- **API Server Health** - Server responsiveness and bot status
- **Position Sync** - Data freshness from Pacifica exchange
- **Funding Tracking** - P&L calculations and funding costs
- **Signal Generation** - Market monitoring and strategy activity
- **Circuit Breaker** - Risk management and loss thresholds
- **Position Limits** - Compliance with trading limits

## Alert Triggers

- **CRITICAL**: System down, position limits exceeded, circuit breaker triggered
  → Generates urgent AI instructions with immediate action steps
- **WARNING**: Bot stops, stale data, reduced market coverage
  → Generates AI instructions for investigation and resolution
- **OK**: All systems normal
  → No AI instructions needed

## Next Steps

1. **Configure email settings** in `monitor_with_alerts.py`
2. **Test the alert system** manually
3. **Set up scheduling** (Task Scheduler recommended)
4. **Start the trading bot** if it's not running
5. **Monitor logs** for the first few hours

The system will now automatically monitor your bot and alert you to any issues!