@echo off
REM Trading Bot Monitoring Script with Alerts
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python monitor_with_alerts.py >> monitoring_alerts.log 2>&1
echo Monitoring run completed at %date% %time% >> monitoring_alerts.log