# How to start and stop the bots

All of these are run from `C:\Users\Tetteh\Desktop\TTXT`. Open PowerShell there first:

    cd C:\Users\Tetteh\Desktop\TTXT

**Before you start anything: MetaTrader 5 EXNESS must already be open and logged in.**
Nothing here opens MT5 for you. If MT5 is closed, the bots just sit there writing
"terminal disconnected" to their logs forever.

---

## Start everything at once

    .\start_watchdog.bat

The watchdog starts the USDJPY bot, the REF scanner and the REF demo trader for you,
and restarts any of them that crash. It also stops Windows going to sleep and logs your
battery and charger status to `logs\watchdog.log` every 10 minutes.

To stop everything: run `.\stop_watchdog.ps1` FIRST, then the stop scripts below.
If you stop a bot while the watchdog is still up, the watchdog just starts it again.

---

## Start one bot at a time

| Bot | Start | Stop | Trades? |
|---|---|---|---|
| USDJPY bot | `.\start_usdjpy.bat` | `.\stop_usdjpy.ps1` | yes, demo |
| REF scanner | `.\start_scanner.bat` | `.\stop_scanner.ps1` | no, read-only |
| REF demo trader | `.\start_ref_trader.bat --live` | `.\stop_ref_trader.ps1` | yes, demo |
| Sniper | `.\start_sniper.bat --live` | `.\stop_sniper.ps1` | yes, demo |

Each `start_*.bat` opens a window that stays open and restarts the bot if it dies.
Closing that window kills the bot.

### About `--live`
On the REF trader and the sniper, `--live` means "actually place orders". It does NOT
mean real money. Everything above runs on demo account **81749554** (Exness-MT5Trial10).
Leave `--live` off and the bot runs dry and never sends an order.

Leave `--live` on only while you mean to be forward testing.

---

## Things to know

**The watchdog does not restart MT5.** It restarts the bot scripts only. If Exness/MT5
closes, you have to reopen it yourself.

**The sniper is not covered by the watchdog.** If the sniper dies, nothing brings it
back. Its own window restarts it on a clean exit, but if that window closes it is gone.

**Nothing starts on boot.** The Windows startup folder only has Ollama in it. After a
restart, every bot stays down until you launch it from here.

**They share one MT5 terminal.** Running several bots at once makes them knock each
other off and reconnect. The logs fill with "terminal disconnected" warnings. It
recovers, but fewer bots at once means fewer dropped connections.

---

## Checking on them

    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_usdjpy|ref_scanner|ref_trader|sniper\.run|watchdog\.py' } | Select-Object ProcessId, CommandLine

Logs live in `logs\`: `logs\usdjpy\bot.log`, `logs\scanner\stdout.log`,
`logs\ref_trader\trader.log`, `logs\sniper\sniper.log`, `logs\watchdog.log`.

---

## The data download (cancelled)

The Dukascopy tick history download (`start_data2_best.bat`) was cancelled on 2026-10-06
with about 113 hours left to run, and was taken out of the watchdog's job list so it does
not come back on its own. Roughly 7,500 of 96,240 days were fetched into `data_duka_full\`.
Don't start it again unless you specifically want that data; it will run for days and eat
disk space.
