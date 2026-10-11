# Stop ONLY the watchdog (its supervisor loop and python process). Bots keep running.
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'start_watchdog\.bat' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped watchdog supervisor $($_.ProcessId)" }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'python.*[\\/]watchdog\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped watchdog $($_.ProcessId)" }
