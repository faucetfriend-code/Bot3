@echo off
REM Trading Bot Monitoring with AI Instructions
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
python monitor_with_ai_instructions.py >> monitoring_ai.log 2>&1
echo AI monitoring run completed at %date% %time% >> monitoring_ai.log