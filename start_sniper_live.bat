@echo off
REM Double-click this to trade the account set in .env.
REM
REM It is still governed by SNIPER_RUN_MODE in .env:
REM   MODE_DEMO (default) - refuses to start on a real-money account
REM   MODE_LIVE           - allows real money
REM
REM To stop: press Ctrl+C in the window (answer Y if asked), or close the window.
REM You do NOT need to activate the virtual environment.

cd /d "%~dp0"
echo.
echo  ================================================================
echo   SNIPER BOT - LIVE MODE. This WILL send real orders.
echo   Account and settings come from .env
echo   Press Ctrl+C at any time to stop. Open trades keep their stops.
echo  ================================================================
echo.
call start_sniper.bat --live
