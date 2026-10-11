# Stop ONLY the SMC REF trader: its auto-restart supervisor (start_ref_trader.bat) and its python process
# (smc_bot\live\ref_trader.py). The scanner (ref_scanner.py), the USDJPY bot and test_ref_trader.py are not matched.
# Pending REF orders keep their broker-side expiry and open positions keep their SL/TP at the broker.
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'start_ref_trader\.bat' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped supervisor $($_.ProcessId)" }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'python.*[\\/]ref_trader\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped ref_trader $($_.ProcessId)" }
