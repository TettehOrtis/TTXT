# SniperEA / Oracle MT5 — handoff and runbook

**Branch:** `sniper-mql5-ea` (separate from `Main`, which holds the smc_bot research + Python Sniper bot)
**Written:** 2026-10-12, after the first live deploy and the connection-drop incident below.

Read this first if something looks broken. Section 2 is the "it's not working" runbook — start there if
that's why you opened this file.

---

## 1. What this is, in one paragraph

`SniperEA.mq5` is a native MQL5 port of the Python `sniper/` bot (`strategy.py` + `engine.py`), built so it
can run as a standalone MT5 Expert Advisor on the Oracle box with no Python process and no Wine-to-localhost
bridge — the thing that sank the earlier `feature/mt5-ea-bridge` branch on `xaubot-ai-main`. It is **not** a
validated strategy. `smc_bot/results/AUT01_emapull/report.md` already found this exact entry rule has no
edge anywhere in 2009–2025. This EA exists to prove the deployment pipeline works (compiles, attaches, takes
real demo trades, sends Telegram, logs to CSV) so that whichever strategy *does* eventually survive your
research has a ready, working place to land.

## 2. "Something's wrong" — runbook

### Symptom: prices frozen / EA never trades / nothing happens

This is the failure that already happened once (2026-10-12). **Checklist, in order:**

1. **Screenshot the terminal and look at two things:** the bottom-right status bar (hover over it) for
   `Ping:` and `Traffic:`, and whether any symbol's Bid/Ask changes between two screenshots taken ~10s apart.
   ```
   ssh -i <key> ubuntu@51.170.88.11 "DISPLAY=:99 scrot /tmp/a.png"
   scp -i <key> ubuntu@51.170.88.11:/tmp/a.png .
   # wait 10s, repeat as b.png, compare
   ```
   If `Ping: n/a` and the same price shows up byte-for-byte in both shots — **the terminal is not actually
   connected to the broker**, even if the title bar says "Demo Account" and looks logged in. This is the
   whole bug: MT5's UI does not visibly flag this. It just silently stops receiving ticks.

2. **Full clean restart of the Wine/MT5 process tree.** Don't just re-login inside the GUI first — kill
   everything:
   ```
   ssh -i <key> ubuntu@51.170.88.11 "ps aux | grep -iE 'terminal64|wineserver|winedevice|start.exe' | grep -v grep"
   # kill -TERM each pid found (terminal64.exe first, then start.exe wrappers, then wineserver)
   # then kill -9 any leftover winedevice.exe
   ```
   Then relaunch detached so it survives your SSH session ending:
   ```
   ssh -i <key> ubuntu@51.170.88.11 "cd '/home/ubuntu/.wine/drive_c/Program Files/MetaTrader 5' && \
     DISPLAY=:99 nohup wine start /exec 'C:\\Program Files\\MetaTrader 5\\terminal64.exe' \
     /config:'C:\\Program Files\\MetaTrader 5\\Config\\autologin.ini' > /tmp/mt5_relaunch.log 2>&1 < /dev/null &
     disown"
   ```
   Give it 30–60s — it does a self-update check first, spawns a `liveupdate` helper, then launches the real
   `terminal64.exe`. Watch for the real process with `/portable` in its args.

3. **If still frozen after the restart, force a manual re-login.** File → Login to Trade Account → the
   dialog is pre-filled (saved password) → click OK. In the one incident so far, the autologin on process
   start silently failed to ever complete, but a manual login some time later succeeded — this looks like an
   intermittent/slow handshake to Exness's server, not a dead account. Be patient; it took several minutes
   of the terminal just sitting there before a login attempt actually went through.

4. **Confirm it's really live**, same two-screenshot price-diff check as step 1. Also check the Journal tab
   for a line mentioning the MQL5 Cloud Server being found — that confirms general outbound internet works,
   which narrows the problem to the broker-specific connection if quotes are still frozen.

5. **Market Watch may reset to the broker's own default symbol list on a fresh connect** (it did — showed
   crypto pairs, not the FX+gold set that had been manually added before). The real symbol names on this
   account use an **`m` suffix**: `XAUUSDm`, `EURUSDm`, not `XAUUSD`/`EURUSD`. If gold's missing, Ctrl+U →
   search `XAUUSD` → you should see both `XAUUSDm` ("Gold vs US Dollar") and `XAUUSD247m` ("Gold vs US
   Dollar 24/7", worth knowing about if you ever want weekend-testable gold). Select → Show Symbol → OK.
   `SniperEA`'s `ResolveSymbol()` already tries the base name, `m`, `z`, and `.raw` suffixes in that order,
   so it will find `XAUUSDm` automatically once it's in Market Watch and a chart exists for it.

### Symptom: Telegram alerts stop arriving but the EA is otherwise fine

**MT5's Options (Tools → Options → Experts) reset to defaults on every process restart** — this is not a one-
time setup, it bites every time you kill/relaunch the terminal. Check for `TelegramSend WebRequest failed,
error=4014` in the Experts log. Fix:
- Tools → Options → Experts tab
- Check "Allow WebRequest for listed URL", double-click the empty "add new URL" row (single-click only
  *selects* the row, doesn't enter edit mode — use F2 or double-click), type `https://api.telegram.org`,
  Tab, OK.
- While there, also re-uncheck "Disable algorithmic trading when the account/profile has been changed" if
  you want AlgoTrading to never auto-pause (these reset to checked on restart too).
- Reattach the EA afterward (detach/reattach, or just drag it onto the chart again) so Notify() calls that
  already fired don't matter — WebRequest permission is checked per-call, so the *next* Telegram send after
  fixing the allowlist will just work without needing a fresh EA instance, but reattaching is a clean way to
  verify it immediately via the startup ping.

### General GUI-automation notes (no VNC on this box)

There's no VNC/RDP — the Wine desktop runs on an `Xvfb` virtual display (`:99`) with nothing watching it.
Interaction is: `xdotool` over SSH to move/click/type, `scrot` to screenshot, `scp` to pull the PNG down to
read it. Gotchas learned the hard way:
- **Single click on a list row only selects it; double-click or F2 enters edit mode.** This matters in the
  EA Inputs grid and the WebRequest URL list — a plain click looks like it worked (row highlights) but
  types nothing.
- Mouse-wheel scroll is `click 4` (up) / `click 5` (down) with xdotool, not a separate scroll command.
- If a click seems to do nothing, check you're not fighting a stale coordinate from a resized/moved dialog —
  re-screenshot before continuing rather than chaining blind actions.

## 3. What's currently live, and how it works

- **Server:** Oracle Cloud, `ubuntu@51.170.88.11`, confirmed **Always Free tier** (`VM.Standard.E2.1.Micro`,
  `free-tier-retained: true` in instance metadata — not the 30-day trial credit, shouldn't expire on its
  own). Only **956MB RAM**, so don't expect to run much else alongside Wine+MT5 without memory pressure.
- **SSH key:** `C:\Users\Tetteh\Downloads\ssh-key-2026-09-05.key` (private) /
  `ssh-key-2026-09-05.key.pub` (public, already on the server).
- **MT5 install:** `/home/ubuntu/.wine/drive_c/Program Files/MetaTrader 5/` under Wine.
  - EA source + compiled binary: `MQL5/Experts/SniperEA.mq5` / `.ex5`
  - Trade log the EA writes: `MQL5/Files/SniperEA_trades.csv` (one row per basket OPEN/CLOSE — see §4)
  - Saved input preset (Telegram token/chat ID + the 24/7 test settings baked in):
    `MQL5/Files/sniperea_test247.set`, loadable from the EA's Inputs tab → Load, so you never have to retype
    the Telegram token again.
  - Account: `476915390` on `Exness-MT5Trial9` (demo), autologin configured in
    `MQL5/../Config/autologin.ini`. A second account exists in `TTXT/.env` (`Z_LOGIN=81749554`,
    `Exness-MT5Trial10`) that the Python Sniper bot is actually configured to use — untested on this Oracle
    MT5 install, but worth knowing about if `Trial9` ever turns out to be the thing that's actually dead
    rather than just flaky.
- **Compiling after an edit:** upload the `.mq5` via `scp`, then headless-compile with:
  ```
  ssh -i <key> ubuntu@51.170.88.11 "cd '/home/ubuntu/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts' && \
    DISPLAY=:99 wine 'C:\Program Files\MetaTrader 5\MetaEditor64.exe' /compile:SniperEA.mq5 \
    /log:SniperEA_compile.log"
  # then read the log (it's UTF-16LE):
  ssh -i <key> ubuntu@51.170.88.11 "iconv -f UTF-16LE -t UTF-8 '.../SniperEA_compile.log'"
  ```
  **MT5 does not auto-reload an already-attached EA when its `.ex5` changes** — you have to detach/reattach
  (double-click it in Navigator, confirm "attach instead of" prompt) to pick up the new binary.

### What the EA actually does (24/7 test config, as currently attached)

- Entry rule: EMA50/200 trend on M15, EMA20 pullback + RSI14 on M1 — identical logic to
  `sniper/strategy.py:entry_signal`. **Confirmed no edge** (see §1).
- Basket: 5 × 0.02 lots, +$15 target / −$70 stop, closed together, 60s cooldown.
- **Currently relaxed for observation, not production defaults:** trading window disabled (`InpUseWindow=
  false`, trades all day/night instead of 07:00–11:30 UTC), daily profit/loss halts disabled (both 0),
  losing-streak direction block effectively disabled (`InpBlockDirectionAfterSL=999999`). This was an
  explicit choice to let it run unattended across every session for observation — **not** how you'd want it
  configured if real money were ever involved.
- Telegram: alerts on EA startup, basket open, basket close, trading-window phase changes (irrelevant while
  the window's off). Bot token/chat ID are the `SNIPER_TELEGRAM_*` ones from `TTXT/.env`.
- CSV log (`SniperEA_trades.csv`): `time_utc, event, symbol, side, legs, lot, ref_price, money, why` — one
  row per OPEN and CLOSE, enough to analyze results by hour/session afterward without scrolling Telegram.

### Known simplifications vs. the Python engine (see the .mq5 file header for the full list)

- Losing-streak/cooldown state lives in EA memory, not a durable file re-read from the broker — a detach/
  reattach resets it. Irrelevant right now since the streak block is disabled anyway.
- Trading-window clock uses `TimeGMT()`, not a verified-true-UTC OS clock like the Python version goes out
  of its way to check. Fine while the window's disabled; re-verify before ever turning it back on.

## 4. Next time you come back to this branch

- Check `MQL5/Files/SniperEA_trades.csv` first (pull it via `scp`) — that's the actual trade history, more
  reliable than scrolling Telegram.
- If it's been more than a few hours since the terminal was last touched, **assume the feed may have
  silently died again** and run the two-screenshot price-diff check in §2 before trusting anything else you
  see.
- Nothing here is a strategy recommendation. The moment a strategy actually survives the Python research
  (none of AUT01–AUT10 have), the plan is: port *that* rule's `entry_signal`-equivalent into a copy of this
  EA skeleton, keep all the plumbing (basket management, Telegram, CSV log, window/limit controls) as is.
