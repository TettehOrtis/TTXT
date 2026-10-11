# BT18 — three entry ideas from the literature search. Pre-registered.

**Written and saved BEFORE any BT18 code was written or run.** The variant count below is **FIXED at 25** and may
not grow. Anything added after results are seen must be labelled "post-hoc, added after seeing results" in the report.

Read first (done): `research_notes/M1_entry_candidates.md` (the source for all three ideas),
`research_notes/SNIPERSPARK_results.md`, `bt/bt15_lib.py`, `bt/bt16_lib.py`, `bt/bt17_lib.py`,
`results/BT16/report.md`, `results/BT17/report.md`, `bt/AGENT_BRIEF.md`, `GLOSSARY.md`, `bt/BT17_BRIEF.md`.

## The gap being closed

BT16 tested dollar strength, news events and the classic indicator set. BT17 tested the four SMC concepts on M1
and M5. Neither touched the three ideas the literature search actually ranked first:

1. **fading an extreme one-minute move** (Rif & Utz 2021 — never tested on gold),
2. **intraday momentum over ~30 minutes** (Gao/Han/Li/Zhou JFE; Baltussen et al. JFE),
3. **the first 5-minute candle at a session open** (Zarattini & Aziz).

One result is consistent across every test so far: **fading beats following at this horizon** (BT16 50.78 vs
49.43; BT17 51.45 vs 48.55 on M1 structure; BT4 saw the same drift two timeframes higher). Idea 1 is that thread
pulled to its natural extreme. Idea 2 is deliberately the opposite sign — a FOLLOW rule — because the horizon at
which follow starts beating fade is itself the finding.

## The money shape, fixed here (identical to BT17, so numbers compare directly)

| | |
|---|---|
| Profit cap | **1.5 gold price units = $15 at 0.10 lots** |
| Loss cap | **2.0 gold price units = $20 at 0.10 lots** |
| Lots | **0.10 on every table in this report** (divide by 10 for 0.01 lots) |
| Commission | **`common.COMM['XAUUSD'] = 0.0`** — his broker charges none |
| Spread | **0.072 price units**, his measured spread |
| Stop slippage | 0.2 gold-pips, from `common.py`, unchanged |
| Break-even accuracy | **52.85 times right in 100** (BT15 `his_costs_grid.csv`, row 15/20) |
| Financing | `common.simulate` charges none; median hold at this shape is ~19 min, so BT18 is not materially exposed to it. Stated anyway. |

## The outcome table — reused, not rebuilt

BT17 already ran `common.simulate` for **both sides at every M1 minute** of both feeds at exactly this money shape
and cached it in `results/BT17/parts/`. BT18 **loads that same validated table read-only** via
`bt17_lib.outcome_table()`. No simulator, cost model or fill rule is re-implemented; no BT17 file is edited.
Every BT18 variant is only an index selection into that one table, so no variant can get different costs from
another. If the cache is missing for a feed, BT18 rebuilds it into `results/BT18/parts/` with the same code path.

## Fill rule (the lookahead guard)

A signal is decided on the **CLOSE of bar k**. The basket is opened at the **OPEN of bar k+1**, i.e. the outcome
table is read at column `k+1`. No input may come from a bar closing after that open. Rolling thresholds are
computed on windows **ending at bar k-1** (`shift(1)`), which is stricter than necessary.

`bt/bt18_tests.py` asserts four things and blocks the run if any fails:
1. every feature's source bar index is `< ` the entry bar index;
2. a deliberate **lookahead twin** (fill at the open of the signal's own bar) shows a large fake edge while the
   k+1 version does not — this reproduces the BT15 bug that manufactured "+$2.17 a basket, t +19.5";
3. **mirrors are exact complements**: `accuracy(mirror) == 100 - accuracy(real)` to machine precision on the
   identical trigger set;
4. random sides at the same minutes measure ~50 right in 100 and lose the coin-flip amount; TEST is NaN.

## THE 25 VARIANTS (fixed)

### Idea 1 — fade the extreme one-minute move (14 variants)

The one-minute move of bar k is `m[k] = close[k] - open[k]` in gold price units. The threshold is the `q`-th
percentile of `|m|` over a trailing window of W one-minute bars **ending at bar k-1**. A signal fires when
`|m[k]| > threshold[k]`. **Declared direction = FADE**: sell after an up-spike, buy after a down-drop.
Entry at the open of bar k+1.

| # | percentile q | trailing window W | expected chances/day |
|---|---|---|---|
| A1 | 90.0 | 1440 (1 day) | ~144 |
| A2 | 95.0 | 1440 | ~72 |
| A3 | 98.0 | 1440 | ~29 |
| A4 | 99.0 | 1440 | ~14 |
| A5 | 99.5 | 1440 | ~7 |
| A6 | 99.9 | 1440 | ~1.4 |
| A7 | 99.95 | 1440 | ~0.7 |
| A8–A14 | the same seven percentiles | 7200 (5 days) | as above |

The W = 7200 block is a **declared robustness check on every one of the seven thresholds** (not a cherry-picked
extra), and it is counted in the variant total and in the placebo bar anyway.

**Required deliverable:** the curve of **directional accuracy against chances per day** across all 14, because the
user needs about 10 trades a day and the extreme threshold fires about 1.4 times a day. He chooses the trade-off.

**Asymmetry (reported, not counted as variants):** every A-variant is additionally split into its **up-spike**
subset (fade = sell) and its **down-drop** subset (fade = buy), so the report says whether the two sides differ.
Splitting a variant's own chances is a breakdown, like hour of day, not a new reading.

### Idea 2 — intraday momentum, a FOLLOW rule (7 variants)

| # | rule | chances/day |
|---|---|---|
| B1 | non-overlapping 5-minute blocks: sign of the last 5 minutes' move, entered at the next minute | ~288 |
| B2 | the same on non-overlapping 15-minute blocks | ~96 |
| B3 | the same on non-overlapping 30-minute blocks | ~48 |
| B4 | the same on non-overlapping 60-minute blocks | ~24 |
| B5 | sign of the first 30 minutes after the **London open** (07:00–07:30 UTC), entered 07:30 | ~1 |
| B6 | sign of the first 30 minutes after the **COMEX open** (13:30–14:00 UTC), entered 14:00 | ~1 |
| B7 | B5 and B6 pooled | ~2 |

Blocks are anchored to the clock (`minute-of-day % L == 0`), so a block's own move is read from closed bars only
and the entry is the very next minute. **Declared direction = FOLLOW**; the fade is the reported mirror.
The horizon at which follow overtakes fade is an explicit output of the report.

### Idea 3 — first 5-minute candle at a session open (4 variants)

Direction = colour of the first M5 candle of the window; entry at the **open of the second** M5 candle
(i.e. the M1 bar five minutes later). **Declared direction = FOLLOW**.

| # | window |
|---|---|
| C1 | London open, first M5 candle 07:00–07:05 UTC, enter 07:05 |
| C2 | COMEX open, first M5 candle 13:30–13:35 UTC, enter 13:35 |
| C3 | C1 and C2 pooled |
| C4 | the same two opens at **local** session time, so the UTC hour moves with daylight saving (London 08:00 local; New York 08:20 local), pooled |

### Total pre-registered readings: **25**

The **sign-flipped mirror of each** is reported alongside. On an identical trigger set a mirror is exactly
`100 − accuracy`, so it is **not** an extra reading and is not counted. Not readings either: the all-minutes
baseline, the break-even accuracy, the oracle ceiling, the spike/drop split, and the per-hour / per-session /
per-year / per-period breakdowns.

## How accuracy is defined (one definition, used everywhere — the same as BT17)

At every chance both sides are known from the outcome table. **Accuracy = the share of chances where the side the
rule chose was the better-paying of the two** (ties count as half). That is exactly the skill variable `X` behind
BT15's break-even, so it is comparable with **52.85**. Money per basket is the plain mean of the chosen side's
dollars at 0.10 lots. Each variant's **own** break-even is reported next to it, because a rule that selects
unusual minutes (a spike minute is not an average minute) can have a different break-even from 52.85.

## The acceptance bar — block rotation, best-of-25, 200 draws

For each of the 25 readings, circularly rotate the chosen-side vector by a random whole number of weeks. Signal
persistence and outcome clustering survive; any real link is destroyed. Recompute accuracy and money, take the
**best of the 25** in that draw, repeat **200 draws**, report the **P95 of the best-of-25**. Because both signs of
every reading are reported, the **two-sided** bar (best of `max(a, 100-a)`) is the headline bar.

A reading is a candidate only if **all four** hold:
1. accuracy beats the two-sided placebo P95 of best-of-25;
2. accuracy is above 50 in **2009-2020 and 2021-2023 and 2024-mid2025 separately**;
3. money per basket is positive, i.e. accuracy is above that variant's own break-even;
4. week-block significance, not naive — these signals overlap and cluster;
   **and the sample is usable** — at least 1,000 chances, since BT16 died on a 195-chance "57%" that became 49% on
   190,000.

## Hour of day — required output, never a selector

Accuracy and money per basket for every hour 0–23 UTC and each of the five sessions, in all three date periods,
for every variant. **GUARD, fixed here:** no variant may be selected, tuned, ranked or recommended on an hour.
BT7 found no session edge; BT14 showed "London beats New York" appears in half of all charts built from random
numbers; BT16's 67–70 late-session lead turned out to live in hour 21.

**THE BROKER BREAK, fixed here.** The broker's daily break is **21:00–22:00 UTC in summer and 22:00–23:00 UTC in
winter** (EET/EEST server time), with a median spread of **0.304** there against 0.040 the rest of the day. Our
Zero account does not even quote it. Therefore:
- a DST-aware break mask is computed, and **every headline number is reported both with and without it**;
- any apparent edge whose chances fall inside the break is **declared untradeable in those words**;
- the conservative variant that drops **both** hours 21 and 22 UTC is reported too.

## Data and periods

| Period | Feed | Dates |
|---|---|---|
| **2009-2020** | `data_duka` | 2009-03-15 → 2020-12-31 |
| **2021-2023** | `data_zero` | 2021-01-01 → 2023-12-31 |
| **2024-mid2025** | `data_zero` | 2024-01-01 → 2025-06-27 |
| **TEST** | — | **2025-07-01 onward: LOCKED. Not read, not loaded, not reported.** |

Last entry chance anywhere = `bt15_lib.LAST_OPP` = **2025-06-27 00:00**. Per-year tables for every year.
**A rule reading 53 / 51 / 50 across the three periods is decaying and is a non-finding — the report says so in
those words.**

## Pre-registered verdict rule

| Outcome | Reading |
|---|---|
| No reading above 50 in all three periods | All three literature ideas are empty on gold at this horizon. Say so. |
| Above 50 in all three periods but below its own break-even | A direction to dig, not a system. Report the full ranking. |
| Clears all four conditions with ≥1,000 chances | A candidate. Next step is a demo forward test at 0.01 lots. |
| A positive whose mirror is equally positive | Execution artifact. Report and discard. |
| A positive whose chances sit in the broker break | Untradeable. Report and discard. |

## Deliverables

`results/BT18/report.md` · `results/BT18/*.csv` (ranked, by_period, by_year, by_hour, by_session, curve,
spike_vs_drop, money_ladder) · `bt/bt18_lib.py`, `bt/bt18_run.py`, `bt/bt18_tests.py`, `bt/bt18_report.py` ·
checkpoints in `results/BT18/parts/` written as each phase finishes (the laptop sleeps; every phase resumes from
disk) · **no edits** to `common.py`, `smc_core.py`, `bt15_lib.py`, `bt16_lib.py`, `bt17_lib.py` or any other
agent's files.

## Reporting rules

Plain English and dollars in every plain-language section: **no R-values, no percentiles, no t-stats there**.
Lead with **"picks the right way X times in 100"**. **Every money figure is at 0.10 lots and every table says so.**
Everything is ranked by accuracy, best first: 52.85 is the break-even **floor**, not a pass mark, so the full
ranked list is reported whether or not anything clears it.
