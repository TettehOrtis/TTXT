@echo off
REM USDJPY forward-test bot on the Zero demo, with auto-restart if the process dies.
cd /d "%~dp0"
:loop
echo %date% %time% starting bot >> logs\usdjpy\supervisor.log
.venv\Scripts\python.exe -u run_usdjpy.py >> logs\usdjpy\stdout.log 2>> logs\usdjpy\stderr.log
echo %date% %time% bot exited with code %errorlevel%, restarting in 30s >> logs\usdjpy\supervisor.log
timeout /t 30 /nobreak > nul
goto loop
