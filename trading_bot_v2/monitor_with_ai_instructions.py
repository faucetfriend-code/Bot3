#!/usr/bin/env python3
"""
Trading Bot Monitor with AI Troubleshooting Instructions
Generates structured troubleshooting prompts when issues are detected
"""

import json
import subprocess
import sys
from datetime import datetime
import os


def run_monitor():
    """Run the monitoring script and return results."""
    try:
        result = subprocess.run(
            [sys.executable, "monitor_bot.py", "--json"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(__file__),
        )

        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return {"error": f"Monitor failed: {result.stderr}"}
    except Exception as e:
        return {"error": str(e)}


def generate_ai_instructions(results):
    """Generate AI troubleshooting instructions based on monitoring results."""
    instructions = []
    timestamp = results.get("timestamp", datetime.now().isoformat())

    # Check each monitoring result
    for check_name, check in results.get("checks", {}).items():
        status = check.get("status")
        name = check.get("name")
        message = check.get("message")
        details = check.get("details", {})

        if status in ["CRITICAL", "FAIL"]:
            instruction = create_critical_instruction(name, message, details, timestamp)
            if instruction:
                instructions.append(instruction)
        elif status == "WARN":
            instruction = create_warning_instruction(name, message, details, timestamp)
            if instruction:
                instructions.append(instruction)

    return instructions


def create_critical_instruction(component, message, details, timestamp):
    """Create AI instruction for critical issues."""

    templates = {
        "API Server": f"""CRITICAL ISSUE DETECTED:
Component: API Server
Status: CRITICAL
Message: {message}
Details: {json.dumps(details, indent=2)}
Timestamp: {timestamp}

URGENT ACTION REQUIRED:
1. Check if the FastAPI server is running on localhost:8000
2. Review server logs for crash details
3. Restart the API server if down
4. Verify database connectivity
5. Check for memory/CPU issues

Run diagnostics:
- curl http://localhost:8000/api/status
- Check Windows Task Manager for python processes
- Review any error logs in the trading_bot_v2 directory""",
        "Position Sync": f"""CRITICAL ISSUE DETECTED:
Component: Position Sync
Status: CRITICAL
Message: {message}
Details: {json.dumps(details, indent=2)}
Timestamp: {timestamp}

URGENT ACTION REQUIRED:
1. Check Pacifica API connectivity
2. Verify API credentials in .env file
3. Review position sync logs
4. Check for rate limiting from Pacifica
5. Verify database write permissions

Run diagnostics:
- Test Pacifica API: python -c "from pacifica_client import PacificaClient; print('API OK')"
- Check database: sqlite3 data/trading_bot.db "SELECT COUNT(*) FROM positions"
- Force sync: curl -X POST http://localhost:8000/api/positions/sync""",
        "Position Limits": f"""CRITICAL ISSUE DETECTED:
Component: Position Limits
Status: CRITICAL
Message: {message}
Details: {json.dumps(details, indent=2)}
Timestamp: {timestamp}

URGENT ACTION REQUIRED:
1. IMMEDIATELY stop the trading bot to prevent further position accumulation
2. Review current positions and assess risk
3. Check why position limits were exceeded
4. Consider manual position reduction if needed
5. Investigate strategy logic for position management

Run diagnostics:
- curl http://localhost:8000/api/positions
- python -c "import sqlite3; conn = sqlite3.connect('data/trading_bot.db'); cursor = conn.execute('SELECT COUNT(*) FROM positions WHERE quantity > 0'); print('Open positions:', cursor.fetchone()[0]); conn.close()"
- Stop bot: curl -X POST http://localhost:8000/api/bot/stop""",
    }

    return templates.get(
        component,
        f"""CRITICAL ISSUE DETECTED:
Component: {component}
Message: {message}
Details: {json.dumps(details, indent=2)}
Timestamp: {timestamp}

Please investigate this critical issue immediately.""",
    )


def create_warning_instruction(component, message, details, timestamp):
    """Create AI instruction for warning issues."""

    templates = {
        "API Server": f"""WARNING ISSUE DETECTED:
Component: API Server
Status: WARNING
Message: {message}
Details: {json.dumps(details, indent=2)}
Timestamp: {timestamp}

ACTION NEEDED:
1. The API server is running but the bot is not active
2. Start the trading bot if it should be running
3. Check why the bot stopped
4. Review recent trades and positions

Run diagnostics:
- Start bot: curl -X POST http://localhost:8000/api/bot/start
- Check status: curl http://localhost:8000/api/status""",
        "Signal Generation": f"""WARNING ISSUE DETECTED:
Component: Signal Generation
Status: WARNING
Message: {message}
Details: {json.dumps(details, indent=2)}
Timestamp: {timestamp}

ACTION NEEDED:
1. Check why fewer markets are being monitored than expected
2. Verify market data feeds are working
3. Review strategy activation logic
4. Check for data fetch errors

Run diagnostics:
- curl http://localhost:8000/api/activity
- Check multi-timeframe fetcher logs
- Verify API connectivity to data sources""",
    }

    return templates.get(
        component,
        f"""WARNING ISSUE DETECTED:
Component: {component}
Message: {message}
Details: {json.dumps(details, indent=2)}
Timestamp: {timestamp}

Please review this warning condition.""",
    )


def save_instructions(instructions, results):
    """Save AI instructions to file for later use."""
    if not instructions:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ai_instructions_{timestamp}.md"

    content = f"""# AI Troubleshooting Instructions
Generated: {datetime.now().isoformat()}

## Monitoring Results Summary
- Total Checks: {results.get("summary", {}).get("total", 0)}
- OK: {results.get("summary", {}).get("ok", 0)}
- Warnings: {results.get("summary", {}).get("warn", 0)}
- Critical: {results.get("summary", {}).get("critical", 0)}
- Failures: {results.get("summary", {}).get("fail", 0)}

## Issues Detected
{chr(10).join(f"### Issue {i + 1}" + chr(10) + instruction + chr(10) for i, instruction in enumerate(instructions))}

## Next Steps
1. Review each issue above
2. Run the suggested diagnostic commands
3. Take appropriate corrective actions
4. Re-run monitoring to verify fixes
5. Update this instruction file with resolution notes

---
*This file was automatically generated by the monitoring system.*
*Delete this file after resolving all issues.*
"""

    with open(filename, "w") as f:
        f.write(content)

    return filename


def main():
    """Main monitoring function with AI instruction generation."""
    print(f"Running AI-enhanced monitoring at {datetime.now()}")

    # Run monitoring
    results = run_monitor()

    # Generate AI instructions for any issues
    instructions = generate_ai_instructions(results)

    if instructions:
        # Save instructions to file
        filename = save_instructions(instructions, results)
        print(f"[ALERT] Issues detected! AI instructions saved to: {filename}")

        # Also print summary to console
        critical_count = sum(1 for inst in instructions if "CRITICAL" in inst)
        warning_count = len(instructions) - critical_count

        print(f"[SUMMARY] Issues: {critical_count} critical, {warning_count} warnings")
        print(f"[ACTION] Check {filename} for detailed troubleshooting instructions")

    else:
        print("✅ All systems operational - no AI instructions needed")

    # Always log results
    with open("monitoring_log.json", "a") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "results": results,
                "ai_instructions_generated": len(instructions) if instructions else 0,
            },
            f,
        )
        f.write("\n")


if __name__ == "__main__":
    main()
