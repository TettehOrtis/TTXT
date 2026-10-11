# BT16 — the entry. Three ideas. Pre-registered.

**Written and saved before any BT16 code was written or run.** The variant counts below are FIXED and may not grow.
Anything added later must be labelled "post-hoc, added after seeing results".

> Replaces an earlier draft of this file (kept at `bt/BT16_BRIEF_v1_superseded.md`) which was written against the
> $200/$150 money shape. **The user has since chosen $15 profit cap / $20 loss cap.** That is the shape BT16 uses.

Read first (done): `research_notes/SNIPERSPARK_results.md`, `research_notes/SNIPERSPARK_strategy.md`,
`bt/bt15_lib.py`, `bt/bt15_hiscosts.py`, `bt/AGENT_BRIEF.md`, `GLOSSARY.md`, `results/BT14/report.md`.

## Where this starts
BT15 solved the exit side. At the user's chosen settings and his broker's real costs the whole system
breaks even when the entry picks gold's direction right **52.9 times in 100**. A coin flip is 50. Four readings of
"M15 trend + M1 pullback" measured 49.6–51.2 — coin flips. **The entry is the only open question.**

**52.9 is the break-even floor, not the goal.** BT16 does not report pass/fail against it. It **ranks every reading
tested by directional accuracy, best first**, in all three families, and says for the best few what the money becomes
as accuracy rises. A reading at 51.5 is still information about where to dig.

## The money shape, fixed here
| | |
|---|---|
| Profit cap | **1.5 gold price units = $15** at 0.10 lots |
| Loss cap | **2.0 gold price units = $20** at 0.10 lots |
| Commission | **$0.00** — `common.COMM['XAUUSD'] = 0.0`. His account statement says he pays none |
| Spread | **0.072 price units**, his own measured spread (−$0.36 on 0.05 lots) |
| Lots | **0.10 on every table in this report.** Divide by 10 for his 0.01 |
| Entry chances | the BT15 grid: **every 30th minute of the clock** (Tests 1 and 3). Test 2 uses event clock minutes |
| Fill | the **open of the entry bar**. Every signal input is read only from bars that CLOSED at or before that open (`asof_index`). ERRORS #36: a BT15 run manufactured "+$2.17 a basket" purely by filling at the open of the signal's own bar |

**Headline number = DIRECTIONAL ACCURACY**, because it is settings-independent. Defined as: at each entry chance both
sides are simulated; the side with the better dollar outcome is "the right way"; accuracy = share of chances where the
reading chose it, counted only over chances where the two sides differ (if both sides win, the choice did not matter).

**Unfinanced, declared.** `common.simulate` charges no overnight financing and BT9 established gold swap is negative
both ways. At this shape most baskets close the same day, so the error is far smaller than at $200/$150, but every
money figure in BT16 is still an **unfinanced** number.

---

## Test 1 — dollar strength (5 variants)
The one idea with a mechanism: gold is *quoted* in dollars, so a weaker dollar mechanically lifts the number.

**Construction.** Equal-weighted USD strength index from the 7 USD pairs in both feeds:
`usd(t) = (1/7) · Σ_p s_p · log(close of pair p at the last bar CLOSED at or before t)`, with `s_p = +1` for USDJPY,
USDCAD, USDCHF (USD is the base) and `s_p = −1` for EURUSD, GBPUSD, AUDUSD, NZDUSD (USD is the quote).

**SANITY CHECK FIRST, blocking (descriptive, not a variant).** Contemporaneous correlation of Δlog(gold) against
Δusd over non-overlapping 15 min / 1 h / 4 h / 1 day windows, per feed and per date period. **It must be clearly
negative.** If it is not, the construction is broken and no Test-1 result may be reported as a finding.
Also descriptive: the lead-lag correlation `corr(Δusd over [t−L, t], Δlog gold over [t, t+1 day])` for every L.

**The variants.** Gold direction = **opposite of the dollar's move** over lookback L, strictly causal
(`Δusd = usd(entry open) − usd(entry open − L)`, both from closed bars only):

| # | lookback L |
|---|---|
| D1 | 5 minutes |
| D2 | 15 minutes |
| D3 | 1 hour |
| D4 | 4 hours |
| D5 | 1 day (1,440 minutes) |

**= 5 variants.** The sign-flipped mirror (gold follows the dollar) is reported alongside but is mechanically
`100 − accuracy`, so it is **not** an extra variant. Chances with Δusd exactly 0 are skipped.

## Test 2 — news events (18 variants)
**We have NO economic calendar feed.** The dates below are approximated from the calendar. This weakens any finding
here badly and the report says so wherever a Test-2 number appears.

| Event | Rule used | Honesty |
|---|---|---|
| **US NFP** | first Friday of the month, **13:30 UTC**, or **12:30 UTC** during US summer time (2nd Sun of March → 1st Sun of Nov) | the day is right; releases have moved only rarely |
| **US CPI** | the **13th** of the month moved to the nearest weekday, 13:30/12:30 UTC with the same DST shift | **the day is a guess** (real CPI lands ~10th–15th). A meaningful share of these are ordinary days |
| **FOMC** | the Wednesday nearest each of 8 nominal dates a year (Jan 28, Mar 18, Apr 29, Jun 17, Jul 29, Sep 17, Oct 28, Dec 15), statement **19:00 UTC** winter / **18:00 UTC** summer | **approximate ±1 week.** Emergency meetings not captured |

**Sample size warning, written in advance.** About 195 NFPs, 195 CPIs and 130 FOMCs exist in 2009 → mid-2025. At
n ≈ 195 the noise on an accuracy is about **3.5 points either way**, so **nothing below roughly 57 in this test can be
told apart from a coin flip**, and the CPI/FOMC date error drags any real effect toward 50 anyway.

**The variants.** 3 event types × 3 entry windows × 2 direction rules:

| entry window | |
|---|---|
| **W1** | 30 minutes **before** the release (entry at release − 30 min) |
| **W2** | the **first minute after** (entry at the open of release + 1 min) |
| **W3** | **60 minutes after** the release |

| direction rule | |
|---|---|
| **M** | continue gold's own move over the 30 minutes ending at the entry bar's open (momentum) |
| **L** | always long — this is the direct test of "does gold have a directional bias in this window" |

**= 3 × 3 × 2 = 18 variants.** Rule L's mirror (always short) is `100 − accuracy`, not counted. Pooled "all events"
rows are aggregates of these cells, not new variants.

**Descriptive, not a variant:** the volatility table — average absolute gold move in the 60 minutes after each event
type against the same clock minute on non-event days. A bigger move makes **both** barriers easier to reach, so a
volatility effect is not an edge and will not be reported as one.

## Test 3 — direction read from M1 and M5 (21 variants) — the gap nobody filled
This project has **never used M1 to generate a signal** (M1 was only ever the trade simulator) and the lowest signal
timeframe ever tested is M5. So the classic rules are run with direction from **M1 and M5**, plus **M15** so the
ladder is comparable with the four readings already tested.

All read from the last bar of that timeframe CLOSED at or before the entry bar's open. All use the same 30-minute
opportunity grid, so "when it trades" cannot confound "which way it bets" — only direction carries the test.

| # | rule | definition | spirit |
|---|---|---|---|
| R1 | **EMA cross** | sign(EMA9 − EMA21) of closes | follow |
| R2 | **Breakout** | close above the highest high of the previous 20 bars → long; below the lowest low → short; else skip | follow |
| R3 | **Range position** | close in the lowest 38% of the last 20-bar range → long; highest 38% → short; else skip | **fade** |
| R4 | **RSI** | RSI(14) ≤ 30 → long; ≥ 70 → short; else skip | **fade** |
| R5 | **MACD** | sign(MACD(12,26) − signal(9)) | follow |
| R6 | **Bollinger break** | close above the upper 20-bar 2σ band → long; below the lower → short; else skip | follow |
| R7 | **Momentum 10** | sign(close − close 10 bars ago) | follow |

**7 rules × 3 timeframes (M1, M5, M15) = 21 variants.** Every one is also reported with its sign flipped
(`100 − accuracy`), because **BT4 found structure drifts from ≈0 at H1/H4 toward slightly MEAN-REVERTING at
M15/M30** — so both signs are checked honestly. Flips are not counted as extra variants.

### Total pre-registered readings: **5 + 18 + 21 = 44**

Not variants (measurements and descriptive output): the 52.9 break-even check, the coin-flip floor, the oracle
ceiling, the correlation tables, the event volatility table, and all per-period / per-year / per-hour / per-session
breakdowns.

---

## Hour of day — report it, never select on it
The user asked for directional accuracy and money **by hour of day 0–23 UTC** and by session, for every test.
It is reported for every reading. **GUARD, fixed here:** BT7 found no session edge and BT14 showed "London beats
New York" turns up in **50% of charts built from pure random numbers**. So **no reading may be chosen, tuned or
recommended on the basis of an hour or a session**, and the report states plainly that any single good-looking hour
is inside what noise produces unless it is large AND consistent across all three date periods.

## The acceptance bar — BT14, recalibrated for 44 readings
BT14's P95 = +0.472 R was calibrated for a best-of-49 search over ~150 trades in R units; it does not transfer.
Rebuilt here in BT16's own units by the same logic:

**Block-rotation placebo.** For each reading, break the link between signal and outcome by **circularly rotating the
signal vector by a random whole number of weeks**. This keeps the signal's own persistence and the outcomes' own
clustering intact while destroying any real relationship. Recompute accuracy and money per basket, take the **best of
the 44** in that draw, repeat **200 draws**, report the **P95 of the best-of-44** in accuracy points and in dollars.
That is the bar a positive must clear.

A reading is a **candidate** only if all four hold:
1. it beats the placebo P95 of best-of-44;
2. accuracy is above 50 in **HIST and DEV and VAL separately**, not just pooled;
3. its sign-flipped mirror is a loser, not an equal-and-opposite winner (ERRORS #36);
4. week-block significance, not naive — baskets overlap.

Even then it is not tradeable until it is forward tested on demo.

**Consistency outranks the headline.** A reading at 56 in one period and 49 in another has found nothing and will be
reported as a non-finding. The user has been burned by exactly that shape before (51.17 / 50.62 / 49.63 across the
three periods — reported as a non-finding, correctly).

## Traps and handling
| Trap | Handling, fixed in advance |
|---|---|
| **Lookahead** | every signal via `asof_index` from bars closed at or before the entry bar's **open**; fill is that open. `bt/bt16_tests.py` asserts it |
| **Overlapping baskets inflate significance** | week-block `t` (`bt15_lib.block_t`) reported; naive `t` never quoted alone |
| **Selecting on hours/sessions** | descriptive only, per the guard above |
| **Window luck** | every reading reported per period **and per year** |
| **TEST leakage** | TEST (2025-07-01 onward) is **LOCKED and not read.** Entry chances stop at `bt15_lib.LAST_OPP` = 2025-06-27 |
| **No swap** | declared; every money figure is unfinanced |

## Data and periods
XAUUSD plus the 7 USD FX pairs. `data_duka` for **HIST 2009-03-15 → 2020-12-31** (gold duka history starts
2009-03-15), `data_zero` for **DEV 2021-01-01 → 2023-12-31** and **VAL 2024-01-01 → 2025-06-27**.

## Reporting
Plain English and dollars. No R-values, no percentiles, no t-stats in plain-language sections. Lead with
**"picks the right way X times in 100"** and **money per basket in dollars at 0.10 lots** (stated on every table).
Then the full ranked list of all 44 readings. Then, for the best few, what the money becomes at the measured accuracy
and at higher accuracies, per basket and per day.

## Deliverables
`results/BT16/report.md` · `results/BT16/*.csv` · `bt/bt16_lib.py`, `bt/bt16_run.py`, `bt/bt16_report.py`,
`bt/bt16_tests.py` · checkpoints in `results/BT16/parts/` (the laptop sleeps; every phase resumes).

---

## POST-HOC LOG — everything added AFTER results were seen (appended 2026-10-05, after the run)
The brief above was fixed before any code was written. The variant count stayed at 44. These are the extra
*controls and diagnostics* that were run after looking at the results, listed here so the record is honest.
None of them is a new variant; every one exists to try to KILL a result, not to find one.

| # | added | why | where |
|---|---|---|---|
| P1 | non-event-day control for the news rules (same clock minute, ordinary days; plus the same rules on the whole 30-minute grid) | three news cells cleared 52.9 in all three periods on ~60 chances each. The control shows the same rules are a 49 on 190,000 chances | `bt/bt16_followup.py`, report §4b |
| P2 | hour x period crossed table | the brief required hour-of-day; checking consistency across periods needs hour crossed with period, which the first pass reported pooled | `bt16_run.phase_hours`, report §6 |
| P3 | hold-time per hour | to find out whether the hours-20/21 long bias is an overnight hold that our unfinanced simulator is flattering | `bt16_followup.hour20`, report §6b |
| P4 | "minutes before gold's daily close" buckets | hour 21 UTC only exists on ~43% of days (the close moves with US summer time), so "hour 21" is a slice of the year, not an hour | `bt16_followup.preclose`, report §6b |
| P5 | daily rollover gap, both feeds | to test whether the late-session bias is a data artifact. It found one: the Zero feed jumps +$6.61 per 0.10 lot across every rollover and Dukascopy does not (ERRORS #42) | `bt16_followup.rollover_gap`, report §6b |
| P6 | cross-feed check: the same windows on Dukascopy over 2021-2024 | the only way to tell a market fact from a feed artifact | `bt16_followup.crosscheck`, `late_fade`, report §6b/§6c |
| P7 | late-session fade, spread sensitivity (0.072 / 0.20 / 0.40 / 0.60) | the 0.072 spread was measured in normal hours; the fade result sits near gold's close where the spread widens. Accuracy barely moves, the money collapses | `bt16_followup.late_spread`, report §6c |
| P8 | splitting 20-21 UTC from 22-23 UTC | the data's own spread column records ~1.0-1.4 price units at 22:00-23:00 against ~0.20 at 08:00, while 20:00-21:00 is below the daytime average. The two halves had to be priced separately | `bt16_followup.late_bands`, report §6c |

**The post-hoc lead (§6c) is labelled a lead and not a finding, for exactly the reason the brief gave: it was
selected on an hour, which the brief banned.** Its next step is a new pre-registered brief, after one measurement
(his real spread at 20:00-21:30 UTC).

| P9 | straddler test (`bt/bt16_straddle.py`) | the §6c lead sits in the hour immediately before the daily rollover that P5 found a +$6.61 jump across. Measured the break minute from the data (zero: starts 20:58/21:58, resumes 22:01/23:01), flagged every basket still open at it, split the lead, and re-ran it with straddlers removed on BOTH feeds | report §6d |
| P10 | live spread by hour (measured outside BT16, from the terminal) | median 0.040 at every hour except **21:00 UTC (median 0.304, average 0.676)**, and plain XAUUSDz does not trade 21:00-22:00. Forced a final split of §6c into hour 20 (tradeable) and hour 21 (not) | report §6d |

**Outcome of P9/P10: the rollover artifact is CLEARED (straddlers are the weak group on both feeds, and the
always-long control shows no accuracy difference between the groups), but the lead dies anyway** - the big numbers
are all in hour 21, whose spread is 7.6x normal, and hour 20 is at or below break-even in 2024-mid2025
(52.5 / 50.4 right in 100, -$0.07 / -$0.47 a basket). **Nothing in BT16 is tradeable.** (ERRORS #44)
