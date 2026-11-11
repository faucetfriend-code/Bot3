@echo off
REM Trading Bot Monitoring Script with Alerts
cd /d "%~dp0" || exit /b 1
python monitor_with_alerts.py >> monitoring_alerts.log 2>&1
echo Monitoring run completed at %date% %time% >> monitoring_alerts.log