#!/usr/bin/env python3
"""
Trading Bot Monitor with Email Alerts
Enhanced version that sends alerts when issues are detected
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import subprocess
import sys
from datetime import datetime
import os

# Email configuration - UPDATE THESE
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = "your-email@gmail.com"
EMAIL_PASS = "your-app-password"
TO_EMAIL = "alerts@your-domain.com"


def send_alert(subject, body, severity="WARNING"):
    """Send email alert."""
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = TO_EMAIL
        msg["Subject"] = f"[{severity}] {subject}"

        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        text = msg.as_string()
        server.sendmail(EMAIL_USER, TO_EMAIL, text)
        server.quit()

        print(f"Alert sent: {subject}")
        return True
    except Exception as e:
        print(f"Failed to send alert: {e}")
        return False


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
            return {
                "error": f"Monitor failed with code {result.returncode}: {result.stderr}"
            }
    except Exception as e:
        return {"error": str(e)}


def analyze_results(data):
    """Analyze monitoring results and generate alerts."""
    if "error" in data:
        send_alert(
            "Monitoring System Error",
            f"Failed to run monitoring: {data['error']}",
            "CRITICAL",
        )
        return

    alerts = []
    critical_issues = []
    warnings = []

    # Check for critical issues
    for check_name, check in data.get("checks", {}).items():
        status = check.get("status")
        message = check.get("message", "")

        if status == "CRITICAL":
            critical_issues.append(f"{check['name']}: {message}")
        elif status == "FAIL":
            critical_issues.append(f"{check['name']}: {message}")
        elif status == "WARN":
            warnings.append(f"{check['name']}: {message}")

    # Send critical alerts
    if critical_issues:
        subject = "CRITICAL: Trading Bot Issues Detected"
        body = f"""Critical issues detected in trading bot monitoring:

{chr(10).join("- " + issue for issue in critical_issues)}

Timestamp: {data.get("timestamp", "Unknown")}

Immediate action required!
"""
        send_alert(subject, body, "CRITICAL")

    # Send warning summary (daily or if many warnings)
    elif warnings:
        # Only send if more than 2 warnings or specific conditions
        if len(warnings) >= 2:
            subject = "WARNING: Trading Bot Issues Detected"
            body = f"""Warning conditions detected:

{chr(10).join("- " + warning for warning in warnings)}

Timestamp: {data.get("timestamp", "Unknown")}

Please review within 1 hour.
"""
            send_alert(subject, body, "WARNING")


def main():
    """Main monitoring function."""
    print(f"Running enhanced monitoring at {datetime.now()}")

    # Run monitoring
    results = run_monitor()

    # Analyze and alert
    analyze_results(results)

    # Always log results
    with open("monitoring_log.json", "a") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "results": results}, f)
        f.write("\n")

    print("Monitoring complete")


if __name__ == "__main__":
    main()
