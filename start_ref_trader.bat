@echo off
REM SMC REF forward-test executor (smc_bot\live\ref_trader.py) on the Zero DEMO, magic 770077, with auto-restart.
REM   start_ref_trader.bat            -> DRY-RUN (never sends orders)
REM   start_ref_trader.bat --live     -> sends orders to the Zero DEMO account
REM Extra arguments are passed through (e.g. --risk 5 --daily-loss 20 --max-open 4). Stop: stop_ref_trader.ps1
cd /d "%~dp0"
if not exist logs\ref_trader mkdir logs\ref_trader
:loop
echo %date% %time% starting ref_trader %* >> logs\ref_trader\supervisor.log
.venv\Scripts\python.exe -u smc_bot\live\ref_trader.py %* >> logs\ref_trader\stdout.log 2>> logs\ref_trader\stderr.log
echo %date% %time% ref_trader exited with code %errorlevel%, restarting in 30s >> logs\ref_trader\supervisor.log
timeout /t 30 /nobreak > nul
goto loop
