@echo off
REM DATA2 BEST job: full Dukascopy BID+ASK per-day fetch (slow, 2 workers) -> data_duka_full\. Exits when DONE marker exists.
cd /d "%~dp0"
if not exist logs\data2 mkdir logs\data2
:loop
if exist data_duka_full\DONE_BEST goto end
echo %date% %time% starting best job >> logs\data2\best_supervisor.log
.venv\Scripts\python.exe -u tools\duka_fetch.py best --workers 2 --nowait >> logs\data2\best_stdout.log 2>> logs\data2\best_stderr.log
if exist data_duka_full\DONE_BEST goto end
echo %date% %time% best job exited with code %errorlevel%, restarting in 60s >> logs\data2\best_supervisor.log
timeout /t 60 /nobreak > nul
goto loop
:end
echo %date% %time% DONE marker present, supervisor exiting >> logs\data2\best_supervisor.log
