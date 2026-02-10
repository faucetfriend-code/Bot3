#!/bin/bash
# Bot Health Monitor Script
# Monitors the trading bot for stability issues and crash patterns

echo "🤖 Trading Bot Health Monitor"
echo "=============================="
echo ""

# Check if bot is running
echo "1. Checking bot status..."
STATUS=$(curl -s http://localhost:8000/api/status | python -c "import sys, json; data=json.load(sys.stdin); print('RUNNING' if data.get('data', {}).get('bot_running') else 'STOPPED')")

if [ "$STATUS" = "RUNNING" ]; then
    echo "✅ Bot is RUNNING"
else
    echo "❌ Bot is STOPPED"
fi
echo ""

# Check positions
echo "2. Checking positions..."
POSITIONS=$(curl -s http://localhost:8000/api/status | python -c "import sys, json; data=json.load(sys.stdin); print(data.get('data', {}).get('positions_count', 0))")
echo "📊 Current positions: $POSITIONS"
echo ""

# Check for error patterns in logs
echo "3. Checking for recent errors..."
ERROR_COUNT=$(tail -100 bot_success.log 2>/dev/null | grep -c "ERROR\|CRITICAL\|FATAL\|Exception\|Traceback" || echo "0")
WARNING_COUNT=$(tail -100 bot_success.log 2>/dev/null | grep -c "WARNING" || echo "0")

echo "🚨 Recent errors: $ERROR_COUNT"
echo "⚠️  Recent warnings: $WARNING_COUNT"
echo ""

# Check memory usage
echo "4. Checking system resources..."
if command -v python &> /dev/null; then
    PYTHON_PROCESSES=$(ps aux | grep python | grep -v grep | wc -l)
    echo "🐍 Python processes: $PYTHON_PROCESSES"
fi

# Check API server responsiveness
echo "5. Checking API server health..."
API_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/status)
if [ "$API_RESPONSE" = "200" ]; then
    echo "🌐 API server: HEALTHY (HTTP 200)"
else
    echo "🌐 API server: UNHEALTHY (HTTP $API_RESPONSE)"
fi
echo ""

# Check for data issues
echo "6. Checking for data quality issues..."
INSUFFICIENT_DATA=$(tail -50 bot_success.log 2>/dev/null | grep -c "Insufficient data\|No candles returned\|insufficient" || echo "0")
ZERO_RSI=$(curl -s http://localhost:8000/api/activity 2>/dev/null | python -c "import sys, json; data=json.load(sys.stdin); print(sum(1 for item in data.get('data', []) if '0.0' in item.get('rsi_15m', '')))" 2>/dev/null || echo "0")

echo "📈 Insufficient data errors: $INSUFFICIENT_DATA"
echo "📊 Zero RSI readings: $ZERO_RSI"
echo ""

echo "📋 RECOMMENDATIONS:"
echo "=================="

if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "• Check bot_success.log for error details"
fi

if [ "$INSUFFICIENT_DATA" -gt 0 ]; then
    echo "• Some symbols have insufficient historical data"
    echo "• Consider reducing the number of monitored symbols"
fi

if [ "$ZERO_RSI" -gt 0 ]; then
    echo "• Some symbols show RSI=0.0 (data quality issue)"
    echo "• Check if these symbols have enough trading history"
fi

if [ "$STATUS" = "STOPPED" ]; then
    echo "• Bot is not running - start it via web interface or API"
fi

echo ""
echo "💡 To monitor continuously: watch -n 30 ./bot_health_check.sh"