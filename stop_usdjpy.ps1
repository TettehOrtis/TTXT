# Stop the USDJPY bot AND its auto-restart loop (open positions keep their 20-pip stop at the broker)
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'start_usdjpy.bat' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped supervisor $($_.ProcessId)" }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_usdjpy.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped bot $($_.ProcessId)" }
