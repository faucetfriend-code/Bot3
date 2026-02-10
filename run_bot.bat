@echo off
REM Trading Bot Launcher for Windows
REM Sets PYTHONPATH to project root for absolute imports

set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%"

echo Starting trading bot with PYTHONPATH=%PYTHONPATH%
python -m trading_bot_v2.api_server %*

REM Keep window open if there's an error
if %ERRORLEVEL% neq 0 (
    echo.
    echo Bot exited with error code %ERRORLEVEL%
    pause
)