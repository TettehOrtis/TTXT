@echo off
REM Read-only REF status scanner (smc_bot/live), with auto-restart if the process dies. Never trades.
cd /d "%~dp0"
:loop
echo %date% %time% starting scanner >> logs\scanner\supervisor.log
.venv\Scripts\python.exe -u smc_bot\live\ref_scanner.py --loop >> logs\scanner\stdout.log 2>> logs\scanner\stderr.log
echo %date% %time% scanner exited with code %errorlevel%, restarting in 30s >> logs\scanner\supervisor.log
timeout /t 30 /nobreak > nul
goto loop
