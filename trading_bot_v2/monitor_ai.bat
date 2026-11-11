@echo off
REM Trading Bot Monitoring with AI Instructions
cd /d "%~dp0" || exit /b 1
python monitor_with_ai_instructions.py >> monitoring_ai.log 2>&1
echo AI monitoring run completed at %date% %time% >> monitoring_ai.log