# Stop ONLY the read-only REF scanner: its auto-restart supervisor (start_scanner.bat) and its
# python process (smc_bot\live\ref_scanner.py). The scanner never trades, so nothing is left at the broker.
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'start_scanner\.bat' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped supervisor $($_.ProcessId)" }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'python.*[\/]ref_scanner\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped scanner $($_.ProcessId)" }
