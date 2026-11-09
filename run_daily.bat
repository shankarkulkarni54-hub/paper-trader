@echo off
setlocal

:: Navigate to script directory
cd /d "%~dp0"

:: Append timestamp header to log file
echo ======================================================== >> daily_trades.log
echo [START] Execution started on: %date% %time% >> daily_trades.log
echo ======================================================== >> daily_trades.log

:: Activate Python virtual environment
call .venv\Scripts\activate.bat

:: Run trading bot and append both stdout and stderr to daily_trades.log
python bot.py >> daily_trades.log 2>&1

:: Append completion timestamp
echo [END] Execution completed on: %date% %time% >> daily_trades.log
echo ======================================================== >> daily_trades.log
echo. >> daily_trades.log

:: Deactivate virtual environment
call deactivate

endlocal
