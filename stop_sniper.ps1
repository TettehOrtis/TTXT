# Stops ONLY the Sniper bot: the start_sniper.bat supervisor and any sniper.run process.
# Other bots in this project (ref_trader, usdjpy, scanner) are left running.
#
#   powershell -File stop_sniper.ps1
#
# Open positions are NOT closed - they keep their disaster stop at the broker. To flatten as well,
# close them in MT5, or run:  .venv\Scripts\python.exe -m sniper.run --status   to see what is open.

$ErrorActionPreference = "SilentlyContinue"
$killed = 0

Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -match 'sniper\.run' -or
        $_.CommandLine -match 'start_sniper\.bat'
    )
} | ForEach-Object {
    Write-Host ("stopping PID {0}: {1}" -f $_.ProcessId, $_.Name)
    Stop-Process -Id $_.ProcessId -Force
    $script:killed++
}

if ($killed -eq 0) {
    Write-Host "no sniper process was running"
} else {
    Write-Host ("stopped {0} process(es). Open positions are untouched at the broker." -f $killed)
}
