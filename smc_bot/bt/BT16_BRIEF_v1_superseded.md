# BT16 — the entry. Three ideas, pre-registered.

**Written before any BT16 code was run.** Variant counts below are fixed and may not grow. Anything added later must be
labelled "post-hoc, added after seeing results" in the report.

Read first: `research_notes/SNIPERSPARK_results.md`, `bt/BT15_BRIEF.md`, `results/BT15/report.md`,
`results/BT15/addendum_depth.md`, `results/BT14/report.md`, `bt/AGENT_BRIEF.md`, `bt/common.py`, `bt/bt15_lib.py`.

## Where this starts from
BT15 proved the **exits of the SNIPER_SPARK basket are fully solved**: ~150 combinations of stop ($10–$150),
target ($3–uncapped) and clocks (5 min–4 h). The best shape found is **target $200 / stop $150 at 0.10 lots**, which in
gold price is **20.00 target / 15.00 stop**. It needs the entry to call direction right **50.6 times in 100** to make
$0, down from 62.3 at the shipped $7/$70. The requirement approaches 50 from above and can never go below, because
before costs a coin flip is exactly break-even at any barrier pair.

Four plausible readings of "M15 trend + M1 pullback" measured **49.6%–51.2%** — coin flips — and all lost.

**So the only remaining question is the entry.** BT16 tests three ideas, all at the BT15 best money shape
(20.00 / 15.00 gold price = $200 / $150 at 0.10 lots), and reports **directional accuracy as the primary number**.

## The money shape, fixed here
| | |
|---|---|
| Target | 20.00 gold price = **$200** at 0.10 lots, **$20** at 0.01 lots |
| Stop | 15.00 gold price = **$150** at 0.10 lots, **$15** at 0.01 lots |
| Lots | 0.10 throughout the tables (divide by 10 for the user's 0.01) |
| Costs | exactly BT15's: `COMM['XAUUSD']` = $1.10 round trip + an explicit **0.05 price units** of spread (ERRORS #34) + 0.2 pip stop slippage |
| Entry chances | the BT15 grid: **every 30th minute of the clock** |
| Fill | the **open of the opportunity bar**. Every signal input is read only from bars that CLOSED at or before that open (`asof_index`). No exceptions — ERRORS #36. |

**Reuse, declared:** the both-sides outcome tables for this exact shape already exist and are validated
(`results/BT15/parts/dep_{duka,zero}_big_T20.0_S15.0.npz`, written by `bt/bt15_depth.py` through `bt/common.simulate`,
with the fill-minute target correction already applied). BT16 reuses them rather than re-simulating, and re-checks
X\*, the coin-flip floor and the oracle on them before using them.

**Unfinanced, declared up front (ERRORS #39).** At this shape the median basket holds **16–27 hours**, i.e. about one
night. `common.simulate` charges **no overnight financing**, and BT9 established gold swap is negative in *both*
directions. **Every money number in BT16 is therefore an unfinanced number and is optimistic by roughly one night of
swap per basket.** Any positive result must be re-run with swap charged before it means anything.

---

## THE THREE TESTS, with counts fixed

### Test 1 — dollar strength (5 variants)
The only idea here with a mechanism: gold is *quoted* in dollars, so a weakening dollar mechanically lifts the number.

**Construction.** An equal-weighted USD strength index from the 7 USD pairs present in both feeds
(EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCAD, USDCHF):

> `usd(t) = (1/7) · Σ_p s_p · log(close_p at the last bar CLOSED at or before t)`,
> `s_p = +1` when USD is the base (USDJPY, USDCAD, USDCHF), `−1` when USD is the quote (EURUSD, GBPUSD, AUDUSD, NZDUSD).

**Sanity check first, before any conclusion is drawn** (descriptive, not a variant): the **contemporaneous**
correlation of Δlog(gold) against Δusd over non-overlapping 15 min / 1 h / 4 h / 1 day / 5 day windows, per feed and
per date period. If gold and the dollar index are not clearly negatively correlated, the construction is wrong and
nothing below may be reported as a finding.

**Also descriptive, not a variant:** the lead-lag correlation, `corr(Δusd over [t−L, t], Δlog gold over [t, t+H])`,
for every L below and H = 1 day. This is the cleanest statement of whether a past dollar move carries any predictive
content at all.

**The variants.** Gold direction = **opposite of the dollar's move** over lookback L, strictly causal
(`Δusd = usd(t_open) − usd(t_open − L)`, both read from closed bars only):

| # | lookback L |
|---|---|
| 1 | 15 minutes |
| 2 | 1 hour |
| 3 | 4 hours |
| 4 | 1 day (1,440 min) |
| 5 | 5 days (7,200 min) |

**= 5 variants.** The sign-flipped version of each (gold follows the dollar) is reported alongside, but it is
mechanically `100 − accuracy` on the non-tie chances, so it is **not** counted as an extra variant. Zero-signal
chances (Δusd exactly 0) are skipped.

### Test 2 — news events (15 variants)
Never tested in this project: BT7 did sessions, never economic events. **We have no economic calendar feed**, so the
dates are approximated from the calendar, and that weakens any finding here badly. Stated in the report, not hidden.

**Approximate dates (declared as approximate):**
| Event | Rule used | Honesty |
|---|---|---|
| **US NFP** | first Friday of the month, **13:30 UTC**, or **12:30 UTC** during US summer time (2nd Sunday of March → 1st Sunday of November) | the day is right; releases have moved only rarely |
| **US CPI** | the **13th of the month**, moved to the nearest weekday, 13:30/12:30 UTC with the same DST shift | **the day is a guess** (real CPI lands roughly the 10th–15th). Expect a meaningful share of these to be ordinary days |
| **FOMC** | the Wednesday nearest each of 8 nominal dates a year (Jan 28, Mar 18, Apr 29, Jun 17, Jul 29, Sep 17, Oct 28, Dec 15), statement **19:00 UTC** winter / **18:00 UTC** summer | **approximate ±1 week.** Unscheduled/emergency meetings are not captured |

**Sample size warning, written in advance.** About 200 NFPs, 200 CPIs and 135 FOMCs exist in 2009–mid-2025. With
n ≈ 200 the standard error on a directional accuracy is about **3.5 points**, so *nothing below roughly 57% in this
test can be distinguished from a coin flip*, and CPI/FOMC date error pushes the true accuracy of any real effect
toward 50 anyway.

**The variants.** 3 event types × the windows and direction rules below:

| entry window | direction rule(s) available causally | cells per event |
|---|---|---|
| **30 min before** the release | continue the gold move over the 30 min ending at entry | 1 |
| **first minute after** (entry at the open of release+1 min) | (a) continue the 30-min pre-release move; (b) continue the release minute's own candle | 2 |
| **60 min after** the release | (a) continue the 30-min pre-release move; (b) continue the whole post-release move so far | 2 |

**= 3 × 5 = 15 variants.** Pooled "all events" rows are aggregates of these same cells, not new variants. Flips are
reported as `100 − accuracy`, not counted.

**Descriptive, not a variant:** the **volatility** table — average absolute gold move over the 60 minutes after each
event type, against the same clock minute on non-event days. A volatility effect is not an edge by itself (a bigger
move makes both barriers easier to reach), and will not be reported as one.

### Test 3 — M1 and M5 direction rules (18 variants) — the user's own catch
This project has **never used M1 to generate a signal**. M1 was only ever the trade simulator. The lowest signal
timeframe ever tested is M5 (BT1 CHoCH confirmation, REF's M5 CHoCH); BT4 covered M15/M30/H1/H4 only; BT15's four
readings took direction from M15. So the classic rules are re-run with direction taken from **M1 and M5** as well as
M15 (M15 included so the ladder is honest and comparable at the new money shape).

All evaluated at the **same 30-minute opportunity grid** as everything else, so "when it trades" cannot confound
"which way it bets" — only the direction carries the test. All read from the last TF bar CLOSED at or before the
entry bar's open.

| # | rule | definition |
|---|---|---|
| R1 | **EMA cross** | sign(EMA9 − EMA21) of closes |
| R2 | **Structure** | `smc_core.structure(bars, 5)["trend"]` (+1/−1; 0 = skip) |
| R3 | **Break** | close above the highest high of the previous 20 bars → long; below the lowest low → short; else skip |
| R4 | **Retracement** | close in the lowest 38% of the last 20-bar range → long; highest 38% → short; else skip |
| R5 | **MACD** | sign(MACD(12,26) − signal(9)) |
| R6 | **RSI** | RSI(14) ≤ 30 → long; ≥ 70 → short; else skip |

**6 rules × 3 timeframes (M1, M5, M15) = 18 variants.**

R3 and R4 are deliberately opposite in spirit (breakout vs mean-reversion) because **BT4 found structure drifts from
≈0 at H1/H4 to slightly mean-reverting at M15/M30** — so the lower timeframes are checked honestly in *both* signs
rather than only the trend-following one.

### Total pre-registered directional readings: **5 + 15 + 18 = 38**

Not variants (measurements, checks and descriptive output):
X\* for the 20.00/15.00 shape · the coin-flip floor · the oracle ceiling · the contemporaneous and lead-lag
correlation tables · the event volatility table · per-period, per-year and per-session breakdowns.

---

## The acceptance bar — BT14, recalibrated for 38 readings
BT14's **P95 = +0.472 R** was calibrated for a best-of-**49**-readings search over ~**150 trades** each, in R units.
BT16's readings run over **tens of thousands** of baskets in dollars, so that number does not transfer numerically.
The bar is therefore rebuilt here in BT16's own units, by the same logic:

**Block-rotation placebo.** For each of the 38 readings, break the link between signal and outcome by **circularly
rotating the signal vector by a random whole number of weeks** — this keeps the signal's own persistence and the
outcomes' own clustering intact while destroying any real relationship. Recompute money per basket and accuracy, take
the **best of the 38** in that draw, repeat **200 draws**, and report the **P95 of the best-of-38**. That is the bar.

A reading is a candidate **only if all four hold**:
1. money per basket beats the placebo **P95 of best-of-38**;
2. accuracy is above 50% in **HIST and DEV and VAL** separately (not just pooled);
3. the **sign-flipped** mirror is a loser, not an equal-and-opposite winner (ERRORS #36: a positive whose mirror is
   equally negative is an execution artifact);
4. week-block significance, not naive — baskets at this shape hold about a day and overlap heavily.

Even then it is **not** tradeable until it is re-run with overnight swap charged.

## Traps, and how each is handled
| BT14 / BT15 trap | Handling, fixed in advance |
|---|---|
| **Lookahead** (a BT15 run manufactured "+$2.17 a basket, t +19.5" purely by filling at the open of the signal's own bar) | every signal is read via `asof_index` from bars closed at or before the entry bar's **open**; fill is that open. A dedicated test asserts no signal input uses a bar closing after the fill |
| **Overlapping trades inflate significance** | week-block bootstrap `t` reported everywhere; naive `t` never quoted alone |
| **Selecting on sessions** | BT7 found no session edge; BT14 showed "London beats New York" appears in 50% of random-number charts. Sessions are **descriptive output only. No variant may be selected, tuned or recommended on a session.** |
| **Window luck** | every reading reported per date period (HIST 2009–2020, DEV 2021–2023, VAL 2024–mid 2025) **and per year** |
| **TEST leakage** | TEST (2025-07 onward) is **LOCKED and not read.** Opportunities stop at `bt15_lib.LAST_OPP` = 2025-06-27. The report states the latest minute any BT16 simulation touched |
| **No swap** | declared above; every money figure labelled unfinanced |

## Data and periods
XAUUSD plus the 7 USD FX pairs. `data_duka` for **HIST 2009-03-15 → 2020-12-31** (gold history starts 2009-03-15;
duka ends 2024-12-31), `data_zero` for **DEV 2021-01-01 → 2023-12-31** and **VAL 2024-01-01 → 2025-06-27**.
TEST 2025-07-01 onward: **locked.**

## Pre-registered verdict rule
| Outcome | Reading |
|---|---|
| **No reading beats 50% out of sample** | The entry question is closed for these three ideas: nothing in dollar strength, news dates or M1/M5/M15 classic rules calls gold's direction. The robot cannot be rescued by any of them. |
| A reading beats 50% in HIST **and** DEV **and** VAL, clears the placebo P95, and its mirror loses | A candidate. Say exactly what must hold: re-run with swap charged, then forward test on demo at 0.01 lots. |
| Something positive in **one** period only, or a positive whose mirror is equally positive | Window luck or an execution artifact. Report it and discard it. |
| The dollar index is **not** clearly negatively correlated with gold contemporaneously | The construction is broken. Fix it; publish no Test-1 conclusion until it passes. |

## Reporting
The user is **not a quant** and has asked repeatedly for plain English and dollars. In every plain-language section:
no R-values, no percentiles, no t-stats. Lead with **"picks the right way X times in 100"** and **money per basket in
dollars**, then **what a typical day looks like on a $350 account at 0.01 lots**. Flag explicitly whenever a number is
measured under different settings than an earlier table (the BT15 tables are mostly at $7/$70; BT16 is at $200/$150).

## Deliverables
`results/BT16/report.md` · `results/BT16/*.csv` · `bt/bt16_lib.py`, `bt/bt16_run.py`, `bt/bt16_report.py`,
`bt/bt16_tests.py` · checkpoints in `results/BT16/parts/` (the laptop sleeps; every phase resumes) ·
`ERRORS.md` entries · a `PLAN.md` status line.
