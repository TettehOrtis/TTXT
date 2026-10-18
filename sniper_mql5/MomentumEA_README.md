# MomentumEA: what it is, what we are doing, how to run it

**Written:** 2026-10-17 (Saturday night, before the first live session).
**Branch:** `sniper-mql5-ea`. **File:** `sniper_mql5/MomentumEA.mq5`. Sits next to `SniperEA.mq5`, which is untouched.

Plain words first, detail after.

---

## 1. In one paragraph

The old SniperEA waited for a pullback to a moving average and then bought the bounce. Fourteen research studies
(AUT01 to AUT14) and then AUT16 found that this entry has no edge, and that **not waiting for the pullback** is better:
trade *with* the bigger trend on any 1-minute candle that closes in the trend's direction. MomentumEA is that rule,
gold only, in five variants. The research says it made money on 2009-2020 data and on the 2021+ data that was kept sealed
until the end, on two different price feeds. **That is not proof it will make money live.** This EA is running on a
demo account to find out how far real fills, spreads and delays are from the research.

SniperEA was stopped and replaced on 2026-10-17 at 23:51 UTC. Its file, its compiled copy and its trade log are still on
the server and in git. Nothing of it was deleted.

---

## 2. The five variants (pick one with `InpVariant`; default 4)

| # | trend (last CLOSED bar) | trigger (last closed 1-minute candle) | target / stop (price units from fill) |
|---|---|---|---|
| 1 | M15 close above/below EMA200 | candle closes with the trend | +3.11 / -6.89 |
| 2 | M15 close vs EMA200 | candle with the trend and close beyond the M1 EMA20 | +3.11 / -6.89 |
| 3 | M15 close vs EMA200 | candle with the trend and close beyond the M5 EMA20 | +5.11 / -6.89 |
| **4** | **M30 close vs EMA100** | **candle with the trend and close beyond the M1 EMA20** | **+5.11 / -6.89** |
| 5 | M30 close vs EMA50 | candle with the trend and close beyond the M5 EMA20 | +5.11 / -6.89 |

- Buy when the trend is up and the candle is green (and passes the trigger test); sell when the trend is down and the candle is red.
- The distances are in **gold price units**, so they do not change with lot size. At 0.10 lot they are the research's
  +$30 / +$50 target and -$70 stop (after the $1.10 commission). At 0.01 lot the dollars are one tenth.
- Targets and stops are measured from the **real fill price**: the EA sends the order with a stop and target, then moves
  them to the true fill, as the research did.
- One trade at a time. Magic number is `990100 + variant` (so 990104 for variant 4). It cannot collide with SniperEA (990077).

Research names for the variants (for finding them in the result files):
1 = `R3_E_E30_70`, 2 = `R4_M15PX200_T_E20SIDE_E30_70`, 3 = `R4_M15PX200_T_M5E20SIDE_E50_70`,
4 = `R4_M30PX100_T_E20SIDE_E50_70`, 5 = `R4_M30PX50_T_M5E20SIDE_E50_70`.

## 3. Time rules (all in UTC; Ghana time = UTC; Exness server time = GMT+0, confirmed in the startup log)

- **No new entries 20:00 to 24:00 UTC.** The thin evening hours lose money and cause most of the "cut off by the weekend close" trades.
- **No Sunday trading, and none on Monday before 07:00 UTC.** The first trade of the week is Monday 07:00 UTC at the earliest.
  (07:00 was a judgement call: any Monday start from 02:00 to 09:00 UTC tested about the same. 00:00 was slightly worst.)
- **Closed before the Friday weekend close** (from Friday 20:45 UTC) and **after 1 day** if neither target nor stop was hit.
- **No daily profit target or loss limit.** That was the owner's choice for this test. See section 8.

---

## 4. What the research found (short version; full files are in `smc_bot/results/AUT16_layers/`)

### The idea
- The pullback entry loses. Removing the pullback (any candle with the trend) was the only change that beat a random trend-side entry in all
  three open windows, by about $1.2 to $4 a trade.
- Wider exits (+$30 or +$50 target with the same -$70 stop) helped more than any filter.
- The evening block (no entries 20:00 to 24:00 UTC) roughly doubled the worst window and removed about 90% of the trades that got cut off by the weekend close.
- Things that did **not** help: a news pause, closing before news, no-Friday, skipping 16:00-17:00, stacking several trend checks (trend line, structure, RSI),
  a daily profit target. A daily loss limit protected the worst day but added no green days.

### Numbers (dollars per trade at 0.10 lot, cost paid once, one trade at a time)

| # | open windows 2009-10 / 2011-16 / 2017-20 | sealed Zero feed 2021+ | sealed Dukascopy 2021+ | trades/day Zero / Duka |
|---|---|---|---|---|
| 1 | +2.42 / +2.92 / +2.10 | +1.10 | +2.73 | 35 / 11 |
| 2 | +2.15 / +3.18 / +2.08 | +1.92 | +3.10 | 28 / 10 |
| 3 | +2.13 / +4.45 / +2.69 | +3.30 | +3.48 | 17 / 6 |
| 4 | +2.77 / +4.06 / +2.28 | +3.43 | +2.74 | 17 / 6 |
| 5 | +2.54 / +4.50 / +1.75 | +2.78 | +2.62 | 17 / 6 |

(Open-window and sealed numbers are with the evening block and the Monday 07:00 start on.)
Variant 4 was chosen as the default: best on the real Zero feed, lowest max drawdown there ($14,852 at 0.10 lot), about 17 trades a day.

### Feeds
- **Dukascopy ("duka")**: long history (gold from March 2009). Gold has no usable spread in it, so 0.05 price units was added. Used for the long tests.
- **Exness Zero ("zero")**: prices recorded from the Exness Zero account, 2021 to Oct 2026. Closest to what this EA trades. Gold has moved much faster since 2021, so trades are quicker and more frequent there.

### Honest limits
- The simulator fills at the next candle's open with a flat spread. Real fills, news spreads and delays will cost something.
- 150 variants were tried. The sealed 2021+ data was looked at **three times** (first look: the 12 shortlisted; second look: with the evening and Monday filters, which were chosen after the first look; third look: account tests). The first look is the only clean one.
- The five variants are one idea, not five independent results.
- Forex: the idea did **not** carry over (EURUSD and GBPUSD lost money in the sealed test). Only EURJPY held up, as one unproven pair. This EA is gold only.

### The bad days (real, not modelled by a limit)
On the Zero feed at 0.10 lot (divide by 10 for 0.01): about 55% of days win, worst day about -$6,400 to -$7,100, worst month about -$10,000 to -$13,000,
max drawdown $15,000 to $25,000. On 2 Feb 2026 the bot would have taken 395 trades in one day and lost $6,436, because each stop was hit within a minute and it kept re-entering.
Stops can also jump: the largest single loss in the sealed test was -$492 before the evening block and about -$95 after it.

### Account sizing (rolling 12-month accounts on the Zero feed, fixed lot, all five variants)
| start | lot 0.01 | 0.02 | 0.03 | 0.05 | 0.10 |
|---|---|---|---|---|---|
| $100 | 15% blown | 29% | 43% | 61% | 76% |
| $500 | 4% | 4% | 5% | 15% | 29% |
| $1,000 | 4% | 4% | 4% | 4% | 15% |

So $500 at 0.01 to 0.03 lot is the smallest sensible setup. $100 is too small.

---

## 5. How it is set up right now (2026-10-17)

- **Server:** the Oracle Free Tier VM (address, SSH key location and the full runbook are in `sniper_mql5/HANDOFF.md`). MT5 runs under Wine, display `:99`.
- **Account:** the demo account on `Exness-MT5Trial9` (login number in `HANDOFF.md`). **Balance was $4,501.70 on 2026-10-17.** The research was sized for $500; the owner plans to reset it to $500.
- **Chart:** `XAUUSDm,H1`. The EA reads M1/M5/M15/M30 itself, so the chart timeframe does not matter. Symbol suffix `m` is found automatically.
- **Settings:** variant 4, lot 0.01, loaded from `MQL5/Presets/MomentumEA_v4.set` on the server. That file holds the Telegram token, so it is **not** in git.
- **Files on the server** (`/home/ubuntu/.wine/drive_c/Program Files/MetaTrader 5/MQL5/`):
  `Experts/MomentumEA.mq5` and `.ex5`, `Presets/MomentumEA_v4.set`, and `Files/MomentumEA_trades.csv` (written when the first trade happens).
- **Telegram:** same bot and chat as before. Every message is headed **"Momentum EA vN"**. Needs `https://api.telegram.org` in Tools > Options > Experts > Allow WebRequest (this setting resets on every terminal restart).
- **SniperEA:** removed from the chart (23:51 UTC). Still on the server and in git.

## 6. The trade log (compare the demo with the research)

`MomentumEA_trades.csv`, one row per OPEN and CLOSE:
`time_utc, event, variant, symbol, side, lot, price, sl, tp, spread_points, entry_delay_s, profit_usd, reason`.
- `entry_delay_s` = seconds after the candle opened that the order went in. The research assumed 0.
- `spread_points` = the spread at entry. The research used a flat 0.05 price units.
- `reason` on a CLOSE is `TP`, `SL`, `TIME` (1-day limit), `WEEKEND` or `OTHER`.

## 7. What to check on Monday 2026-10-19

1. A Telegram message "Momentum EA v4 - Started on XAUUSDm" should already be there from Saturday.
2. After 07:00 UTC the EA should start taking trades. On the real Zero data, variant 4 took about 17 a day (range 8 to 25).
3. Open `MomentumEA_trades.csv` (pull it with scp, see `HANDOFF.md`). Look at `entry_delay_s` (should be a few seconds) and `spread_points`.
4. Make sure no trade opened outside the rules: none before Monday 07:00 UTC, none 20:00-24:00 UTC, none on Sunday.
5. After a week, compare dollars a trade and win rate with the research table above. With the filters on, variant 4 won about 60-61% of trades in the sealed test (a $50 win against a $70 loss; break-even is 58.3%, so the margin is only about 2-3 points).
6. If prices freeze or nothing happens, use the runbook in `HANDOFF.md` section 2 (connection-drop incident).

## 8. Decisions still open

- **Reset the demo balance to $500** so the test matches the research (it was $4,501.70).
- **A daily loss limit.** None is set, as requested. In the older, calmer data a limit of -$200 to -$300 (at 0.10 lot; scale it with the lot) cut the worst day by 25-40% for a small cost. It was not tested on volatile days like 2 Feb 2026, and those are where it would matter.
- **Which variant.** Four is the default. Three is the next best on the real data. One is the simplest but weakest on the Zero feed.
- **Lot size.** Start at 0.01. Only raise it after the demo agrees with the research.
- **Monday start** 00:00 vs 07:00 is a judgement call; the data cannot tell them apart.

## 9. Operating notes

- **Change the variant:** double-click the chart's EA, Inputs tab, set `InpVariant` (1 to 5). Or load a preset.
- **Dry run:** `InpDryRun = true` logs signals as `SIGNAL` rows and places no orders.
- **Compile on the server** (the folder name has spaces, so copy via `/tmp`):
  ```
  scp -i <key> sniper_mql5/MomentumEA.mq5 ubuntu@<server>:/tmp/MomentumEA.mq5
  ssh -i <key> ubuntu@<server> "cp /tmp/MomentumEA.mq5 '/home/ubuntu/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts/MomentumEA.mq5' && \
    cd '/home/ubuntu/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts' && \
    DISPLAY=:99 wine 'C:\Program Files\MetaTrader 5\MetaEditor64.exe' /compile:MomentumEA.mq5 /log:MomentumEA_compile.log"
  ```
  Then refresh the Navigator (right-click an EA > Refresh) and re-attach; MT5 does not reload an attached EA on its own.
- **Attach:** double-click MomentumEA in the Navigator while the gold chart is active; the prompt asks to replace the current EA.
- **A trade left open** across an EA restart is picked up again by its magic number.

## 10. Where the research lives

All in `smc_bot/results/AUT16_layers/` (not all of it is committed yet):
`PREREG.md` (every rule written before the run), `EXAM_RESULT.md` (sealed test), `INDEX_all_variants.md` and `variants/` (all 150 variants, one readable page
and one JSON each), `NEWS_RESULT.md`, `HOURS_ALL.csv`, `MONDAYSWEEP_ALL.csv`, `DAILY_ALL.csv`, `ACCOUNT_*.csv`, `FX_ALL.csv`, `FXEXAM_ALL.csv`.
Scripts are `smc_bot/bt/aut16_layers.py` to `aut28_fxexam.py`.

---

**Bottom line:** a research-backed gold rule, built and running on a demo, not proven. The point of the next few weeks is to see whether the demo's
real fills and dollars a trade agree with the research. If they do, think about a bigger lot. If they do not, the CSV will show where they differ.
