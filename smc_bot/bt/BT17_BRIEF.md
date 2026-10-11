# BT17 — SMC signals generated on M1 and M5. Pre-registered.

**Written and saved BEFORE any BT17 code was written or run.** The variant count below is FIXED at **16** and may
not grow. Anything added later must be labelled "post-hoc, added after seeing results" in the report.

Read first (done): `research_notes/SNIPERSPARK_results.md`, `bt/bt15_lib.py`, `bt/bt15_hiscosts.py`, `smc_core.py`,
`bt/AGENT_BRIEF.md`, `GLOSSARY.md`, `results/BT14/report.md`, `bt/BT16_BRIEF.md` (BT16 was never run; no code exists).

## The gap being closed
Every SMC test in this project generated its signals on M15 or above (BT1 sweeps, BT2 order blocks, BT3 FVGs,
BT4 structure at M15/M30/H1/H4). The lowest signal timeframe anywhere was M5 (BT1's CHoCH confirmation, REF's M5
CHoCH). **M1 has never generated a signal — it was only ever the trade simulator.** BT17 runs the same four SMC
concepts with signals taken from **M1 and M5**.

BT4's shape is the reason not to assume the answer: the raw post-break move was ≈0 at H1/H4 but drifted slightly
**mean-reverting** at M15/M30. At M1 the effect may be larger and inverted. Every variant is therefore reported in
**both signs** (follow and fade).

## The money shape, fixed here
| | |
|---|---|
| Profit cap | **1.5 gold price units = $15 at 0.10 lots** |
| Loss cap | **2.0 gold price units = $20 at 0.10 lots** |
| Lots | **0.10 on every table in this report** (divide by 10 for 0.01 lots) |
| Commission | **`common.COMM['XAUUSD'] = 0.0`** — his broker charges none |
| Spread | **0.072 price units**, his measured spread, applied by shifting both barriers and subtracting it from the payout |
| Stop slippage | 0.2 gold-pips, from `common.py`, unchanged |
| Break-even accuracy at this shape | **52.9 times right in 100** (BT15 `his_costs_grid.csv`, row 15/20) |
| Financing | `common.simulate` charges none. Median hold at this shape is **17 minutes**, so BT17 is *not* materially exposed to the overnight-swap problem that invalidated BT15's one positive. Stated anyway. |

## Fill rule (the lookahead guard)
A signal is decided on the **CLOSE of signal bar k** on its own timeframe. The order's `t_signal` is that bar's close
time, so `common.simulate` fills at the **open of the next M1 bar (k+1)**. No signal input may come from a bar that
closes after that open. Higher-timeframe inputs go through `asof_index`.

`bt/bt17_tests.py` asserts this three ways: (1) every feature's source bar index < entry bar index; (2) a deliberate
**lookahead twin** of one variant that fills at the open of the signal's own bar must show a large fake edge while the
k+1 version does not — this reproduces and isolates the BT15 bug that manufactured "+$2.17 a basket, t +19.5";
(3) random sides at the same signal minutes must measure ≈50% accuracy and the coin-flip money for the shape.

## The outcome table
`common.simulate` is run for **both sides at every M1 minute** of each feed (5.53 M minutes duka, 1.15 M minutes
zero up to the cut), producing `pnl[2, N]` in dollars at 0.10 lots and `texit[2, N]`. Every variant is then an index
selection into that one validated table, so no variant can get a different simulator, cost model or fill rule from
any other. `bt15_lib.fix_fillbar` is applied (simulate never books a target on the fill bar but does book a stop
there; at a $15 target that pessimism is one-sided). Checkpointed to `results/BT17/parts/` per chunk.

## THE 16 VARIANTS (fixed)
All detectors come from `smc_core.py` unchanged: `structure()`, `swings()`, `fvgs()`. `N = 5` for structure/swings
unless stated. A zone or event is discarded if untouched within **50 bars** of its own timeframe. Sweep signals get a
**10-bar same-direction cooldown** so one level cannot fire every minute.

### Family S — liquidity sweeps (4)
A sweep high at bar k: `high[k] > lsh[k]` (last confirmed swing high) **and** `close[k] <= lsh[k]` — poked through,
closed back under. Declared direction = **fade** (short). Sweep low is the mirror (long). Both on one bar → skipped.

| # | timeframe | swing N |
|---|---|---|
| S1 | M1 | 5 |
| S2 | M5 | 5 |
| S3 | M1 | 20 (older, deeper liquidity) |
| S4 | M5 | 20 |

### Family O — order blocks, first retest (2)
`structure()` gives `ob_lo/ob_hi` for the impulsive leg of each break. First later bar whose range trades into the
zone is the retest; entry at that bar's close. Declared direction = **continuation** of the break.

| # | timeframe |
|---|---|
| O1 | M1 |
| O2 | M5 |

### Family F — fair value gaps, first fill (2)
`fvgs(b, min_atr=0.1)`. Bullish gap at k = `(high[k-2], low[k])`. First later bar trading back into the gap is the
fill; entry at that bar's close. Declared direction = **continuation** of the gap.

| # | timeframe |
|---|---|
| F1 | M1 |
| F2 | M5 |

### Family B — break of structure / change of character (4)
`structure()['evt']`: ±1 = BOS, ±2 = CHoCH. Entry at the close of the break bar. Declared direction =
**continuation**; the fade is the mirror.

| # | timeframe | event |
|---|---|---|
| B1 | M1 | BOS |
| B2 | M1 | CHoCH |
| B3 | M5 | BOS |
| B4 | M5 | CHoCH |

### Family C — combinations of two concepts (4)
| # | rule |
|---|---|
| C1 | M1 sweep (as S1) followed by an M1 CHoCH in the fade direction within 10 M1 bars; entry at the CHoCH bar's close |
| C2 | the same on M5, within 10 M5 bars |
| C3 | M1 FVG first fill (as F1) where the **M5 structure trend** (`asof_index`, closed M5 bars only) agrees with the gap direction |
| C4 | M1 order-block retest (as O1) where the **M5 structure trend** agrees with the break direction |

### Total pre-registered readings: **16**
The **sign-flipped mirror of each** is reported alongside. On an identical trigger set a mirror is exactly
`100 − accuracy`, so it is **not** an extra reading and is not counted. "Fading beat following" is read off these
pairs. Not readings either: the all-minutes baseline, the break-even accuracy, the oracle ceiling, and the
per-hour / per-session / per-year / per-period breakdowns.

## How accuracy is defined (one definition, used everywhere)
At every chance both sides are known from the outcome table. **Accuracy = the share of chances where the side the
rule chose was the better of the two** (ties count as half). This is exactly the skill variable `X` behind BT15's
break-even number, so it is comparable with the **52.9** bar. Money per basket is the plain mean of the chosen side's
dollars. Each variant's **own** break-even is reported next to it, because a rule that selects unusual minutes can
have a slightly different break-even from the 30-minute grid's 52.9.

## The acceptance bar — BT14's logic, recalibrated for 16 readings
BT14's +0.472 R was calibrated for a best-of-49 search over ~150 trades in R units and does not transfer. Rebuilt in
BT17's units by the same method:

**Block-rotation placebo.** For each of the 16 readings, circularly rotate the chosen-side vector by a random whole
number of weeks. Signal persistence and outcome clustering survive; any real link is destroyed. Recompute accuracy
and money, take the **best of the 16** in that draw, repeat **200 draws**, and report the **P95 of the best-of-16**
for accuracy and for money. That is the bar.

A reading is a candidate only if **all four** hold:
1. accuracy beats the placebo P95 of best-of-16;
2. accuracy is above 50% in **2009-2020 and 2021-2023 and 2024-mid2025 separately**, not just pooled;
3. money per basket is positive, i.e. accuracy is above that variant's own break-even;
4. week-block significance, not naive — M1 signals overlap heavily.

**Ranking, per the user's clarification.** 52.9 is the break-even floor, not a pass mark. All 16 readings (and their
mirrors) are ranked by directional accuracy, highest first, with the best in each family named, so the direction to
dig is visible even where nothing clears the floor. For the best few, the money at $15/$20 and 0.10 lots is tabulated
as accuracy rises (52.9 / 54 / 55 / 56 / 58) so the prize is visible. **A reading that scores high in one period and
below 50 in another has found nothing and is reported as a non-finding regardless of its pooled headline.**

## Hour of day — required output, never a selector
Directional accuracy and money per basket for every hour 0-23 UTC and for each of the five sessions, for **every**
variant, in all three date periods. **GUARD, fixed here:** no variant may be selected, tuned, ranked or recommended
on an hour or a session. BT7 found no session edge and BT14 showed "London beats New York" appears in 50% of charts
built from pure random numbers. Any good-looking hour is reported as noise unless it is both large **and** consistent
across all three date periods, and that will be said in those words.

## Data and periods
| Period | Feed | Dates |
|---|---|---|
| **2009-2020** | `data_duka` | 2009-03-15 (gold history starts) → 2020-12-31 |
| **2021-2023** | `data_zero` | 2021-01-01 → 2023-12-31 |
| **2024-mid2025** | `data_zero` | 2024-01-01 → 2025-06-27 |
| **TEST** | — | **2025-07-01 onward: LOCKED. Not read, not loaded for inspection, not reported.** |

Last entry chance anywhere = `bt15_lib.LAST_OPP` = **2025-06-27 00:00**; the Friday 20:45 UTC weekend-flat rule in
`common.simulate` closes every such basket before July. The report states the latest minute any BT17 simulation
touched. Per-year tables for every year in all three periods.

## Pre-registered verdict rule
| Outcome | Reading |
|---|---|
| No reading above 50% in all three periods | M1/M5 is the last untested timeframe and it is empty too. The SMC entry question closes. |
| A reading above 50% in all three periods but below its own break-even | A direction to dig, not a system. Report the ranking and say so. |
| A reading clears all four conditions | A candidate. Name exactly what must happen next: demo forward test at 0.01 lots. |
| A positive whose mirror is equally positive | Execution artifact. Report and discard. |

## Deliverables
`results/BT17/report.md` · `results/BT17/*.csv` · `bt/bt17_lib.py`, `bt/bt17_outcomes.py`, `bt/bt17_run.py`,
`bt/bt17_report.py`, `bt/bt17_tests.py` · checkpoints in `results/BT17/parts/` (the laptop sleeps; every phase
resumes from disk) · no edits to `common.py`, `smc_core.py` or any other agent's files.

## Reporting rules
Plain English and dollars in every plain-language section: no R-values, no percentiles, no t-stats there. Lead with
**"picks the right way X times in 100"**. **Every money figure is at 0.10 lots and every table says so.**
