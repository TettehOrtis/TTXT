# Sniper Spark clone — a configurable MT5 basket bot

A working rebuild of the Sniper Spark EA: opens a group of positions in one direction, manages them on
their **combined floating dollars**, and closes the whole group at a dollar profit target or a dollar
stop. Every setting from the vendor's Inputs tab lives in `.env`.

> **Read this before you fund anything.** Twenty backtests in this project (BT1–BT20, 2009–2025) found
> **no entry rule on gold that beats a coin flip after costs**, including this one. The shipped
> $7 / $70 settings lose about **$1.93 per basket** at 0.10 lots. This bot is built to be *correct and
> safe*, not because it is expected to make money. Demo only until your own forward test says otherwise.
>
> **The $7 / $70 shape cannot be fixed by a better entry.** After the $1.10 commission a win is $5.90 and
> a loss is $71.10, so breaking even needs a **92.3%** hit rate. Luck alone, on the exact minutes this
> rule selects, already delivers **89.6%** — so the famous ~89% win rate is marginally *below* chance,
> and the number required is 2.7 points above it. No entry rule in seventeen years of data on two feeds
> has come close. That is arithmetic, not a tuning problem.

---

## Start here

Open MetaTrader 5 first and log into the account you want — the bot attaches to the running terminal.

**You do NOT need to activate the virtual environment.** Everything below uses `.venv\Scripts\python.exe`
by full path.

```powershell
cd C:\Users\Tetteh\Desktop\TTXT

# 1. look around - read-only, sends nothing
.venv\Scripts\python.exe -m sniper.run --status

# 2. one dry pass - shows what it WOULD do
.venv\Scripts\python.exe -m sniper.run --once

# 3. dry-run loop - watches the market, never sends an order
.\start_sniper.bat

# 4. trade the account in .env
.\start_sniper.bat --live
```

**In PowerShell you must type `.\` before a .bat file.** PowerShell refuses to run anything from the
current folder without it (`start_sniper.bat` alone gives "not recognized"). In an old-style CMD window
the `.\` is not needed. Or just **double-click `start_sniper_live.bat`** in File Explorer and skip the
terminal entirely.

**Dry run is the default.** Nothing is ever sent without `--live`.

### Stopping it

| Situation | What to do |
|---|---|
| You can see the window | **Press Ctrl+C.** Answer `Y` to "Terminate batch job". It stays stopped — it will not restart |
| You can see the window | Or just close the window |
| You lost the window | `powershell -File stop_sniper.ps1` |

**Stopping does not close your trades.** They stay open at the broker with their emergency stop attached.
Start the bot again and it adopts them and carries on managing them to the same target and stop. If you
stop mid-basket and walk away, that basket is on its own until you come back.

Run the tests any time: `.venv\Scripts\python.exe sniper\test_sniper.py` (81 checks, no MT5 needed).

---

## Settings — all in `.env`

The vendor's `$300` sheet is the default. Delete a line to fall back to that default.

| `.env` key | Default | What it does |
|---|---|---|
| `SNIPER_SYMBOL` | `XAUUSD` | **No broker suffix.** Resolved per account — `XAUUSDz` on Zero, `XAUUSDm` on Standard. Use `BTCUSD`, `EURUSD`, `US30`… |
| `SNIPER_ACCOUNT` | `ZERO` | Which credential block in `.env`: `MT5`, `ZERO` or `Z` |
| `SNIPER_RUN_MODE` | `MODE_DEMO` | `MODE_DEMO` **refuses to attach to a real-money account.** `MODE_LIVE` allows it |
| `SNIPER_LOT_SIZE` | `0.02` | Lot per position |
| `SNIPER_POSITIONS_PER_ENTRY` | `5` | Positions opened per signal |
| `SNIPER_BASKET_PROFIT_TARGET_USD` | `7.0` | Close the whole basket at this combined profit |
| `SNIPER_STOP_LOSS_USD` | `70.0` | Close the whole basket at this combined loss |
| `SNIPER_COOLDOWN_SECONDS` | `60` | Wait after a basket closes |
| `SNIPER_TREND_TIMEFRAME` | `M15` | Timeframe for the trend filter |
| `SNIPER_ENTRY_TIMEFRAME` | `M1` | Timeframe for the retracement trigger |
| `SNIPER_REWARD_MULTIPLIER` | `2.0` | Per-position take-profit = this × the disaster-stop distance |
| `SNIPER_BLOCK_DIRECTION_AFTER_SL` | `2` | Pause a direction after this many losing baskets in a row (resets daily) |
| `SNIPER_DAILY_PROFIT_TARGET_USD` | `200.0` | Stop trading for the day at this profit. `0` = off |
| `SNIPER_DAILY_LOSS_LIMIT_USD` | `50.0` | Stop trading for the day at this loss. `0` = off |
| `SNIPER_TRADING_HOURS` | *(blank)* | **The window.** `07:00-12:00` UTC. Blank = all day, as V1 behaved |
| `SNIPER_WINDDOWN_MINUTES` | `30` | Stop opening new baskets this long before the window ends |
| `SNIPER_FLAT_AT_WINDOW_END` | `True` | Close everything at the window end. **Keep this on for laptop use** |

### The trading window

Built for the laptop that cannot stay on all night. With `SNIPER_TRADING_HOURS=07:00-12:00` the day runs:

| Time (UTC = your clock in Ghana) | What it does |
|---|---|
| before 07:00 | Idle. Nothing open, nothing opened |
| 07:00 → 11:30 | Normal trading |
| 11:30 → 12:00 | **Wind-down.** Manages open baskets, opens nothing new |
| 12:00 | **Force-flat.** Closes anything still open, whatever the price |
| after 12:00 | Idle. Safe to shut the laptop |

The wind-down is the point. Gold's basket takes about 9 minutes but the tail is long, so stopping new
entries half an hour early lets most finish on their own rather than being closed at a random price.

It is safe to start late: launched at 09:00 it simply begins; launched at 13:00 it logs "the trading
window has closed for today" and sits idle. A basket adopted after a restart past 12:00 is still closed.

Telegram tells you when the window opens, when it starts winding down, when 10 minutes are left, and
when it shuts. `/flat` closes everything and stands down in one command, for when the laptop has to go
now — it pauses *before* it closes, so nothing can reopen in the gap.

**Measure any window before you trust it.** `sniper.expect` reads the window from `.env` and models the
force-flat:

```
.venv\Scripts\python.exe -m sniper.expect                   # gold, the window in .env
.venv\Scripts\python.exe -m sniper.expect --symbol EURUSD   # a different market
.venv\Scripts\python.exe -m sniper.expect --all-day        # compare against all day
```

Watch the **"cut off by the window end"** line. On gold it is 11% of trades; on EURUSD it is 61%, because
a $70 stop is 70 pips there and the basket needs 2-3 hours to resolve. A market whose baskets cannot
finish inside the window is not suitable for one. See `sniper/V2.md` for the full table.


**The entry rule** (the vendor does not publish theirs — this is a reconstruction of their description):

| Key | Default | Meaning |
|---|---|---|
| `SNIPER_TREND_EMA_FAST` / `_SLOW` | `50` / `200` | Trend is up when fast EMA is above slow, on the trend timeframe |
| `SNIPER_ENTRY_EMA` | `20` | Price must pull back to this EMA on the entry timeframe |
| `SNIPER_ENTRY_RSI_PERIOD` | `14` | RSI length |
| `SNIPER_ENTRY_RSI_BUY_BELOW` | `45.0` | How deep the pullback must go for a BUY |
| `SNIPER_ENTRY_RSI_SELL_ABOVE` | `55.0` | …and for a SELL |
| `SNIPER_REQUIRE_TREND` | `True` | `False` = ignore the trend filter, trade both ways |
| `SNIPER_ENTRY_REQUIRE_BODY_HOLD` | `False` | `True` = the bar's **body** must close back on the trend's side of the EMA, not just its wick touch. See the warning below |

### ⚠️ The RSI gate and what it actually selects

Worth knowing before you touch either setting, because it is not what the rule looks like it does.

A BUY needs the wick to reach the EMA (`low <= EMA20`), the bar to close up (`close > open`), **and**
`RSI14 <= 45`. But a bar that closed up *and* back above the EMA has just been pushed upward, so its RSI
cannot be low — measured on gold M1 2021-2026, the 10th percentile of RSI on such bars is **49.5**,
already above the 45 cut-off.

The consequence, measured over five years: **107,101 bars matched "wick pierced the EMA, body closed
back above it, bar closed up". Nineteen of them cleared `RSI <= 45`.** So 99.98% of every BUY this bot
takes is the *opposite* case — a bar whose body closed **through** the EMA. The rule only ever fires on
deep pullbacks, never on the EMA holding as support.

That is arithmetic, not a market phase; it is the same in both halves of the data. It is not a bug in the
code — the code does exactly what the four conditions say — but the four conditions contradict each
other, so the rule is far narrower than it looks.

`SNIPER_ENTRY_REQUIRE_BODY_HOLD=True` makes the other pattern expressible. **It needs the RSI gate opened
to do anything** (`SNIPER_ENTRY_RSI_BUY_BELOW=100`, `SNIPER_ENTRY_RSI_SELL_ABOVE=0` turns it off
entirely; 60/40 loosens it). The bot warns at startup if you set the body-hold without opening the gate,
rather than silently taking no trades.

**It does not make money.** 75 combinations were tested across both patterns, RSI 45/50/55/60/off, trend
timeframes M5/M15/M30/H1/none, EMA 20/50/100 and three confirmations, on two data feeds covering 2009-2026.
None beat its own luck-only benchmark by a meaningful margin. The switch exists so the rule matches its
documentation and so the pattern can be tried, not because it is expected to pay.

**Shape and safety:**

| Key | Default | Meaning |
|---|---|---|
| `SNIPER_HARD_SL_POINTS` | `0.0` | `0` = the disaster stop is derived from `STOP_LOSS_USD` × 1.5 |
| `SNIPER_INCLUDE_COMMISSION` | `True` | Count commission in the basket's floating money. Leave on for Zero/raw accounts |
| `SNIPER_MAX_SLIPPAGE_POINTS` | `200` | How far price may move during a fill before the broker rejects it. Gold's spread alone is ~40 points |
| `SNIPER_MAX_SPREAD_POINTS` | `0.0` | `0` = off. Skip entries when the spread is wider than this |
| `SNIPER_FLAT_BEFORE_WEEKEND` | `True` | Close everything Friday 20:45 UTC |
| `SNIPER_MAGIC` | `990077` | The bot only ever touches positions carrying this magic number |
| `SNIPER_POLL_SECONDS` | `1.0` | Loop period |

CLI flags override `.env` for one run: `--symbol BTCUSD --stop 30 --daily-loss 20`.

---

## Telegram

Optional. Leave `SNIPER_TELEGRAM_TOKEN` blank in `.env` and the bot runs exactly the same without it.

**Make a bot of its own.** In Telegram, message **@BotFather** → `/newbot` → copy the token into
`SNIPER_TELEGRAM_TOKEN`. Do **not** reuse a token from another bot: Telegram lets only one program poll a
token at a time, so two bots sharing one would steal each other's commands. The bot detects this and says
so in the log.

Only `SNIPER_TELEGRAM_CHAT_ID` is obeyed. Commands from any other chat are logged and ignored.

| Command | What it does |
|---|---|
| `/status` `/s` | Account, open basket, floating money, today's result, streaks, cooldown |
| `/positions` `/pos` `/p` | Every open leg with its own profit, and the combined total |
| `/daily` `/d` | Today's realised money, baskets, win rate, limits |
| `/settings` | The configuration it is actually running |
| `/pause` | Stop opening **new** baskets. An open one is still managed to target or stop |
| `/resume` | Start looking for entries again |
| `/close` | Close the open basket now. **Asks you to confirm** by sending it twice |
| `/stop` | Shut the bot down. **Asks you to confirm.** Open trades are left at the broker |
| `/help` | The list |

It also messages you on: startup and shutdown, every basket opened and closed, the daily target or loss
limit being hit, and the connection dropping and coming back. Set `SNIPER_TELEGRAM_ALERTS=False` to keep
the commands but silence the running commentary.

**Two design rules worth knowing**, because they are what keep Telegram from being a liability:

1. **Telegram can never hurt trading.** Network calls run on their own threads, outgoing messages go
   through a queue so sending cannot block the trading loop, and every failure is swallowed and logged.
   If Telegram is down or misconfigured, the bot trades on unaffected.
2. **Command handlers never touch MT5**, because the MT5 library is not thread-safe. The trading loop
   publishes a snapshot of its state each pass and Telegram only reads that. Commands that *do*
   something (`/pause`, `/close`, `/stop`) leave a request on a queue, which the trading loop picks up
   and executes on its own thread. If a reply looks stale, it says so.

## Switching symbol or account

```bat
REM gold on the Zero demo (default)
start_sniper.bat --live

REM bitcoin instead
start_sniper.bat --live --symbol BTCUSD

REM the Standard account from .env
start_sniper.bat --live --account MT5
```

You do **not** write the broker suffix. The bot lists what the account offers and picks the match, so the
same `.env` works on a Zero account (`XAUUSDz`) and a Standard one (`XAUUSDm`).

**Sizing warning when you change symbol.** `$7` and `$70` are dollar amounts, not distances. On gold at
0.10 total lots, $70 is about a $7 move. On BTC the same $70 is a very different distance, and on
`EURUSD` 0.10 lots it is enormous. Re-think the target and stop whenever you change instrument.

---

## What happens when the wifi drops

This was a requirement, so it is worth being precise about.

**The broker is the source of truth — not this process.** Every loop the bot re-reads its open positions
from MT5 and re-reads the day's realised money from MT5's own deal history. It does not rely on its own
memory for anything it can ask the terminal about.

That gives you:

- **Connection lost mid-basket** → every MT5 call raises a recoverable error, the loop logs it once and
  retries with a backoff (5 s doubling to 120 s). The basket is untouched and still has its disaster stop
  at the broker. When the connection returns, the bot picks the same basket back up and keeps managing it.
- **Bot killed / machine rebooted mid-basket** → on restart it finds the positions by magic number, logs
  `adopted an existing basket`, and carries on managing them to the same target and stop.
- **State file deleted or corrupted** → the day's money is rebuilt from MT5 deal history, which is
  authoritative. Only the losing-streak counter resets, and it resets to the safe value (0).
- **Order rejected or partially filled** → if some legs open and others fail, it manages the legs it got
  and says so. If no leg opens, the basket is abandoned cleanly.
- **Close fails on some legs** → it retries every loop until the basket is flat. It never assumes a close
  worked; it re-reads positions to confirm.
- **Process exits for any reason** → `start_sniper.bat` restarts it after 15 s. The one exception is a
  configuration or safety error (exit code 2), which deliberately does **not** restart, because retrying
  a bad config in a loop would just hide it.

**The disaster stop.** Every position gets a broker-side stop placed at roughly 1.5× the basket's dollar
stop — far enough away that it should never fire while the bot is running. It exists for exactly one
case: this process dies and never comes back. Without it a basket could sit open indefinitely.

---

## Safety rails

- **Dry run by default.** No order is sent without `--live`.
- **Demo guard.** With `SNIPER_RUN_MODE=MODE_DEMO` the bot refuses to start on a real-money account and
  exits rather than trading.
- **Magic-number isolation.** It only ever reads or closes positions carrying `SNIPER_MAGIC` on its own
  symbol. Your other bots and manual trades are invisible to it.
- **Daily limits** stop new baskets at the profit target or loss limit, and reset at UTC midnight.
- **Weekend flat** closes everything Friday 20:45 UTC.
- **Window flat** closes everything at `SNIPER_TRADING_HOURS`' end, so no basket is left unmanaged when
  the laptop closes. It keeps trying on later passes if a close fails, and still fires on a basket
  adopted after a restart.
- **`/flat`** closes everything and stops opening more, in one command.
- **Startup warnings** when settings contradict each other — a daily loss limit below one basket's stop,
  or a window with the force-flat switched off.
- **Stopping is safe.** `stop_sniper.ps1` kills only this bot. Open positions stay at the broker with
  their stops; restart and it adopts them again.

---

## Files

| File | What |
|---|---|
| `sniper/config.py` | Every setting, read from `.env`, with validation |
| `sniper/broker.py` | MT5 gateway: connect, reconnect, symbol resolution, orders, history |
| `sniper/strategy.py` | The trend + retracement entry rule |
| `sniper/engine.py` | Basket lifecycle, daily limits, cooldown, crash recovery |
| `sniper/run.py` | CLI and the never-die main loop |
| `sniper/expect.py` | What the current settings would have paid, per symbol and per window |
| `sniper/test_sniper.py` | 146 tests against a fake broker |
| `sniper/V2.md` | The V2 plan: what is built, what is not, and the per-symbol measurements |
| `start_sniper.bat` / `stop_sniper.ps1` | Supervisor and stopper |
| `logs/sniper/` | `sniper.log` (live), `sniper_dry.log`, `state.json`, supervisor logs |

---

## Honest limits

1. **The entry rule is not theirs.** The vendor publishes only "identifies trend using built-in
   indicators" and "waits for retracement meeting defined entry criteria". This is a reasonable
   reconstruction. It will not fire on the same minutes as their EA, and every threshold is exposed in
   `.env` so you can tune it toward whatever theirs does.
2. **No edge is claimed.** See BT20: 432 entry × exit combinations, none profitable across all eras.
3. **There is no laddering.** Every leg opens at market at the same price with the same stop, which is
   what was confirmed with the seller and why the basket reduces to one trade of the combined size. Their
   page says "defined spacing"; if you ever learn they really do ladder the entries, that needs pending
   orders and is a genuine change, not a setting.
4. **Commission is estimated as symmetric** (entry commission doubled for the round trip). True on
   Exness; check if you move broker.
5. **Not tested through a real outage yet.** The recovery paths are unit-tested against a fake broker,
   but pull your wifi during a demo basket and watch the log before trusting it with anything larger.
