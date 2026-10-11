@echo off
REM DATA2 FAST job: HistData bulk bid M1 + Dukascopy-sampled spreads -> data_duka\*.parquet + validation. Exits when DONE marker exists.
cd /d "%~dp0"
if not exist logs\data2 mkdir logs\data2
:loop
if exist data_duka\DONE_FAST goto end
echo %date% %time% starting fast job >> logs\data2\fast_supervisor.log
.venv\Scripts\python.exe -u tools\data2_fast.py >> logs\data2\fast_stdout.log 2>> logs\data2\fast_stderr.log
if exist data_duka\DONE_FAST goto end
echo %date% %time% fast job exited with code %errorlevel%, restarting in 60s >> logs\data2\fast_supervisor.log
timeout /t 60 /nobreak > nul
goto loop
:end
echo %date% %time% DONE marker present, supervisor exiting >> logs\data2\fast_supervisor.log
