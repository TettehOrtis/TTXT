@echo off
REM Sniper Spark bot supervisor. Restarts the bot 15 s after a CRASH, so a dropped connection or a
REM Windows hiccup that kills the process cannot leave a basket unmanaged.
REM
REM   start_sniper.bat              DRY RUN  - never sends an order (default, safe)
REM   start_sniper.bat --live       trades the account in .env (demo unless SNIPER_RUN_MODE=MODE_LIVE)
REM   start_sniper.bat --live --symbol BTCUSD --stop 30
REM
REM TO STOP IT:  press Ctrl+C in this window (answer Y if it asks "Terminate batch job").
REM              It will NOT come back - Ctrl+C is understood as "a human asked me to stop".
REM              If you lost the window, use:  powershell -File stop_sniper.ps1
REM
REM You do NOT need to activate the virtual environment. This file uses .venv\Scripts\python.exe
REM directly and cd's to its own folder, so it works even if you double-click it.

setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set ARGS=%*
if not exist "%PY%" (
  echo Cannot find %PY%
  echo Run this file from the TTXT folder, where the .venv folder lives.
  pause
  exit /b 1
)
if not exist "logs\sniper" mkdir "logs\sniper"

:loop
echo [%date% %time%] starting sniper %ARGS% >> logs\sniper\supervisor.log
"%PY%" -m sniper.run %ARGS%
set RC=%ERRORLEVEL%
echo [%date% %time%] sniper exited with code %RC% >> logs\sniper\supervisor.log

if "%RC%"=="3" (
  echo.
  echo Stopped by you. Open trades are left alone at the broker with their stops.
  echo [%date% %time%] stopped by user - not restarting >> logs\sniper\supervisor.log
  exit /b 0
)
if "%RC%"=="2" (
  echo.
  echo CONFIGURATION or SAFETY error - not restarting. Fix .env and try again.
  echo [%date% %time%] config/safety error - not restarting >> logs\sniper\supervisor.log
  pause
  exit /b 2
)
if "%RC%"=="1" (
  echo.
  echo Could not reach MT5. Is the MetaTrader 5 terminal open and logged in?
  echo [%date% %time%] exit 1 - retrying >> logs\sniper\supervisor.log
)

echo.
echo Bot stopped unexpectedly ^(code %RC%^). Restarting in 15 seconds... press Ctrl+C to give up.
timeout /t 15 /nobreak >nul
goto loop
