@echo off
REM Standalone Validation Runner Launcher for Windows
REM Runs independently of the live bot (own process, own DB connection).
REM Sets PYTHONPATH to project root for absolute imports

set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%"

echo Starting validation runner with PYTHONPATH=%PYTHONPATH%
python -m trading_bot_v2.validation.runner --once %*

REM Keep window open if there's an error
if %ERRORLEVEL% neq 0 (
    echo.
    echo Validation runner exited with error code %ERRORLEVEL%
    pause
)
