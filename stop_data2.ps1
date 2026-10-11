# Stop both DATA2 jobs (supervisors first, then workers), identified by command line
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'start_data2_(fast|best)\.bat' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped supervisor $($_.ProcessId)" }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'data2_fast\.py|duka_fetch\.py|hist_fetch\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped worker $($_.ProcessId)" }
