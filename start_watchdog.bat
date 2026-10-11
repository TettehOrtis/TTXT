@echo off
REM Keep-awake + job watchdog (tools\watchdog.py). Restarts the watchdog itself if it dies. Stop: stop_watchdog.ps1
cd /d "%~dp0"
:loop
.venv\Scripts\python.exe -u tools\watchdog.py >> logs\watchdog_stdout.log 2>&1
timeout /t 60 /nobreak > nul
goto loop
