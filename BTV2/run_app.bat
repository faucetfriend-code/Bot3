@echo off
cd /d "%~dp0"
echo Starting Walk-Forward Analysis Dashboard...
echo.
streamlit run app.py
pause
