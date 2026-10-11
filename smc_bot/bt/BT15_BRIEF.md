# BT15 — the SNIPER_SPARK basket structure on real gold

Strategy spec: `research_notes/SNIPERSPARK_strategy.md`. Read it first.

## The question
A commercial MT5 gold robot opens a group of positions at one price and closes them all when their combined profit
reaches a small dollar target (+$T) or their combined loss reaches a large dollar stop (−$S). Defaults: $7 target,
$70 stop, on 0.10 lots.

Because every position opens at the same price with the same stop and target, the basket is arithmetically **one trade
of the combined size**. So the model is simply:

> One gold trade. Target +T. Stop −S, where S is about 10x T. Direction from an undisclosed M15 trend rule, timing
> from an undisclosed M1 pullback rule. 60 s cooldown. A direction pauses after 2 consecutive losing baskets.
> Daily profit target and daily loss limit end the trading day.

**On a coin-flip market a target:stop of 1:10 wins 10/11 = 90.9% of the time and makes exactly zero.** So the whole
system rests on one thing: **does the entry rule pick direction better than a coin flip, by enough to pay the costs?**

We cannot see the entry rule. **So do not try to guess it. Measure the requirement instead.**

## The primary, decisive measurement — pre-register this
**Find the directional accuracy at which this structure breaks even after real costs.** Call it `X*`.

Run the structure with synthetic entries of controlled skill: an entry that picks the correct direction exactly X% of
the time (X = 50.0, 51, 52, 53, 54, 55, 57.5, 60). 50% is a coin flip. Interpolate the break-even point `X*`.

Then state plainly: **"this robot must pick direction correctly X*% of the time to make $0."**

This is not a search over variants, it is a single measurement, so it carries no multiple-comparison penalty.

Then compare `X*` with what this project has ever actually observed from a directional rule on real data. BT4 measured
raw post-break move ≈ 0 on H1/H4; BT5 found H1 structure worse than random and only a small real signal from H4
structure and EMA200-H1; BT13's 69k candidates showed the entry carries no information. Report the comparison honestly.

## Secondary tests (all pre-registered, counts fixed here)
1. **Random entry** (X = 50) — the floor. Shows what the structure plus costs does on its own. Expect ≈ −(cost) per
   basket; if it is not, the simulator is wrong.
2. **Target/stop sweep — 6 x 5 = 30 combinations.** Work in gold price distance, not dollars, since lot size scales
   dollars linearly. Target distance: 0.3, 0.5, 0.7, 1.0, 1.5, 2.0. Stop distance: 3, 5, 7, 10, 15.
   (The shipped default is target 0.7 / stop 7.0 at 0.10 lots = $7 / $70.) For each combination report `X*`.
   **The useful output is a table of "required accuracy" per combination** — i.e. is there ANY target/stop shape whose
   requirement is plausibly reachable?
3. **Four plausible readings of "M15 trend + M1 pullback"** — exactly four, fixed now, no more:
   (a) M15 EMA50 vs EMA200 for direction, enter on a pullback to M1 EMA20;
   (b) M15 close above/below EMA200 for direction, enter on an M1 RSI(14) crossing back from <30 / >70;
   (c) M15 higher-highs/higher-lows structure for direction, enter on a 38–62% retrace of the last M15 leg;
   (d) M15 MACD sign for direction, enter on the first M1 close back in the trend direction after 3 counter closes.
   Measure each one's actual directional accuracy and its money result. **Apply the BT14 bar to any positive result**,
   adjusted for this variant count — BT14's P95 = +0.472R was calibrated for 49 readings; state clearly that a larger
   search needs a higher bar, and if any of these looks positive, re-run the BT14 calibration for this exact shape
   before believing it.
4. **Oracle entry** (perfect future knowledge of direction) — a ceiling and a simulator sanity check. It must win
   comfortably; if it does not, the simulator is broken. This is a correctness check, not a finding.

## Mechanics that must be simulated faithfully
- 60 s cooldown after a basket closes before another may open.
- Pause a direction after 2 consecutive losing baskets in it (and define/record when it un-pauses).
- **Daily profit target and daily loss limit** end the trading day; fresh start next day. Use the user's values, NOT
  the shipped $200 target. Sweep a small, fixed set: daily target $10/$20/$40 and daily loss $20/$40/$80, and also
  "off" for both, so we can see what the daily rules actually change.
- Flat before weekends and holiday closures (the harness already does this).
- One basket at a time.

## Costs — use the real ones, no guesses
The user will run a **raw-spread account (their Exness Zero)** with a **$300–400 balance**. Use this project's measured
Exness Zero XAUUSD spread and commission plus the standard 0.2 pip stop slippage, exactly as `bt/common.py` already
does for other backtests. Do not assume a spread. Note from the live screenshots that each position opened at −$0.36,
i.e. the opening spread toll was −$5.04 on a 14-position basket — a cost of that size against a $7 target is 72% of
the reward, so **cost is likely to dominate the result and must be modelled precisely.**

## Data and period
XAUUSD only. `data_zero` (2021+) and `data_duka` (2005–2025) via the existing dataset switch.
**The TEST period (2025-07 onward) stays locked** — do not read it.

### Split the dates — report every one of these separately, never only the total
| Period | Dates | Use |
|---|---|---|
| **HIST** | 2005-01-01 → 2020-12-31 | 16 years, the big sample |
| **DEV** | 2021-01-01 → 2023-12-31 | |
| **VAL** | 2024-01-01 → 2025-06-30 | |
| **TEST** | 2025-07-01 → | **LOCKED. Do not read it.** |

Also report **per year** and per era within HIST (2005–08, 2009–14, 2015–20). Gold had a huge bull run from 2020, so a
result that only exists after 2020 is a result about that bull run, not about the robot. Say so explicitly if it happens.

### Split the sessions — report all of them
Report every measurement broken out by session (UTC): **Asian 00:00–07:00, London 07:00–12:00, Overlap 12:00–15:00,
New York 15:00–20:00, Late 20:00–24:00**. Give `X*` (required accuracy) and the money result per session.

**GUARD — this matters, read it.** Report the sessions; do **not** pick a session from the results. BT7 already tested
sessions directly and found no session edge (14/14 DEV and 3/3 VAL negative, gross ≈ 0). BT14 then showed that
"London beats New York" — the one thing BT6 had written down in advance as its finding — turns up in **50% of charts
generated from pure random numbers**. So a session looking better here is the single most likely place for this
backtest to fool us. Pre-registered rule: **a session breakdown is descriptive output only. No variant may be
selected, tuned or recommended on the basis of which session looked best.** If one session does look much better,
say plainly that it is within what noise produces and do not build on it.

## Deliverables
- `results/BT15/report.md` — method, the `X*` table, the four readings' actual accuracy, the daily-rule comparison,
  per-year and per-era breakdowns, every variant counted, and the honest verdict.
- `results/BT15/required_accuracy.csv` — the target/stop grid with `X*` for each cell.
- `results/BT15/trades_*.csv` and `daily_*.csv` — full records.
- `bt/bt15_*.py`.

## How to report it — the user's framing
The user runs this **day by day**. Lead with days, in dollars, not in R:
- what a typical day makes or loses
- how many days win vs lose
- best day, worst day, worst run of losing days in a row
- what a month looks like
- how long until the $300–400 account is gone, if it is losing

Only then the long-run totals. Plain words — the user is not a quant and has asked repeatedly for plain English and
money rather than statistics vocabulary. Terms in `GLOSSARY.md`; style per `tools/plain_report.py`.

## Pre-registered verdict rule — write this down before looking
| Outcome | Reading |
|---|---|
| `X*` is above anything this project has ever measured from a directional rule | The robot cannot work as sold. Report that plainly. |
| `X*` is reachable AND one of the four readings reaches it out of sample AND it clears the recalibrated BT14 bar | Worth a real forward test. Say exactly what would need to hold. |
| Oracle entry fails to win | The simulator is broken. Fix it; publish nothing until it passes. |
