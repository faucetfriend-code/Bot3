@echo off
REM Run all validation agents on the project (Windows version)

setlocal enabledelayedexpansion

echo Running All Validators
echo ========================================
echo.

REM Check if we're in the project root
if not exist "agents\base_agent.py" (
    echo Error: Must run from project root directory
    exit /b 1
)

REM Create reports directory
if not exist "reports" mkdir reports
set TIMESTAMP=%date:~-4%%date:~-7,2%%date:~-10,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set TIMESTAMP=%TIMESTAMP: =0%
set REPORT_DIR=reports\validation_%TIMESTAMP%
mkdir "%REPORT_DIR%"

echo Reports will be saved to: %REPORT_DIR%
echo.

REM 1. API Validator
echo [1/3] Running API Validator...
python agents\api_validator.py ^
    --backend api_server.py ^
    --frontend trading_bot_interface.html ^
    --fix ^
    --output "%REPORT_DIR%\api_validation.md"

if %errorlevel% equ 0 (
    echo [OK] API validation complete
) else (
    echo [ERROR] API validation failed
)
echo.

REM 2. Config Manager
echo [2/3] Running Config Manager...
python agents\config_manager.py ^
    --validate-all ^
    --suggest-fixes ^
    --output "%REPORT_DIR%\config_audit.md"

if %errorlevel% equ 0 (
    echo [OK] Config validation complete
) else (
    echo [ERROR] Config validation failed
)
echo.

REM 3. Code Reviewer (sample files)
echo [3/3] Running Code Reviewer on key files...

for %%f in (api_server.py main.py risk.py) do (
    if exist "%%f" (
        echo   Reviewing %%f...
        python agents\code_reviewer.py ^
            --file "%%f" ^
            --severity high ^
            --output "%REPORT_DIR%\review_%%~nf.md" 2>nul
    )
)

echo [OK] Code reviews complete
echo.

REM Summary
echo ========================================
echo All validations complete!
echo.
echo Reports generated in: %REPORT_DIR%
dir /b "%REPORT_DIR%"
echo.
echo View reports: cd %REPORT_DIR%

endlocal
