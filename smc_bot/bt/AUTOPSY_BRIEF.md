# AUTOPSY — why did this trade win, and why did that one lose?

**Written for: the backtest agents who will run the autopsy stages. The owner reads it too.**

Status: catalogue COMPLETE, stage dates PENDING owner's choice of split. Written 2026-10-07, before any
autopsy code exists.

## Why this document exists

Every backtest in this project so far measured a rule and reported a number. None of them asked why an
individual trade won or lost. There are 277 saved trade files in `results/` with an outcome on every row
and essentially no conditional analysis of them. BT21's most durable finding ("the retest requirement is a
cost, not a filter") came from asking why, not from the pass/fail test. That is the gap this closes.

## The owner's method, which overrides the old two-stage DEV/VAL rule

1. **Explore and tune** on the oldest period. Run the raw rule for a baseline with real spread and
   commission. Then autopsy every trade against the catalogue below. Propose refinements.
2. **Validate** on the next period, untouched during stage 1. The reason must survive here or it dies.
3. **Stress** on a third period, plus adverse conditions (worse spread, worse fills, feed swap).
4. **Final exam** on the newest period, looked at ONCE. A variant that fails stage 3 never gets here.

A refinement that does not survive stage 2 is discarded and recorded as discarded. No exceptions, no
re-tuning on stage 2 data.

## Two questions per trade, not one

- **Filter**: should this trade have been taken at all?
- **Shape**: given it was taken, what target, stop and time limit should it have had?

The break-even bar is not fixed. At $15 target / $70 stop it is 55.8-58.1 right in 100. A wider, rarer,
ATR-sized trade needs far less. Expect more from the shape question than from the filter question.
Denominate every target and stop in ATR or percent, never dollars (BT21 finding #2).

## THE CATALOGUE — every reason a trade may have won or lost

Each condition below must be computable from data available strictly BEFORE entry. Anything that peeks at
the entry bar's own close or later is banned and is itself reason F1.

### A. Regime and context
| id | condition |
|---|---|
| A1 | higher timeframe agreement: M15, H1, H4 trend all same side as the trade, or in conflict |
| A2 | how many bars since the trend filter last flipped (fresh trend vs stale trend) |
| A3 | trend strength vs chop: EMA slope, EMA separation, efficiency ratio (net move / total travel) |
| A4 | volatility level: current ATR as a rank against its own trailing year |
| A5 | volatility direction: ATR expanding or contracting over the last N bars |
| A6 | how much of the day's typical range is already spent at entry time |
| A7 | how extended price is from the HTF mean (distance to M15/H1 EMA in ATR) |
| A8 | day of week; the day after an unusually large daily move |
| A9 | gold-specific cross-market state: DXY and US real-yield direction, correlation break |

### B. Time and session
| id | condition |
|---|---|
| B1 | session: Asia, London, New York |
| B2 | London/NY overlap active or not |
| B3 | minutes elapsed since the session open; first 30 min and last 30 min flagged separately |
| B4 | rollover and thin-liquidity hours; hours when the spread is historically widest (`research/spread_profile.py`) |
| B5 | high-impact news windows. No calendar in the project, so proxy it: a minute-level volatility spike far above that minute-of-day's own norm. Flag as proxy, never as fact |
| B6 | month-end, holiday weeks, COMEX settlement window (BT20 touched this), option expiry |

### C. Level and structure quality
| id | condition |
|---|---|
| C1 | **room to run**: is there untouched liquidity (a prior high/low not yet taken) between entry and target. The single most likely real reason — test it first |
| C2 | distance to the nearest OPPOSING level. A target sitting behind a prior swing is a target you do not reach |
| C3 | freshness of the level traded: first touch vs third touch of the same zone |
| C4 | round-number proximity (BT10 covered this; reuse, do not redo) |
| C5 | position inside the prior day's range and the overnight range |
| C6 | was the level formed in the same session or carried over from a previous one |

### D. Signal quality — the trigger candle itself
| id | condition |
|---|---|
| D1 | trigger candle body size relative to ATR |
| D2 | wick-to-body ratio; where the close sits inside the candle's range |
| D3 | pullback depth: how far past the EMA price actually went (a shallow tag and a deep overshoot are different trades) |
| D4 | number of bars the pullback took |
| D5 | did the trigger candle engulf the one before it |
| D6 | volume at the trigger. **Dukascopy `tick_volume` is the constant 1 on all 5.5M gold bars** — volume work must use the Exness feed only, and must say so |

### E. Shape — what the trade should have been given
| id | condition |
|---|---|
| E1 | stop distance vs ATR: was the stop sitting inside normal noise and guaranteed to be clipped |
| E2 | worst point the trade reached before resolving, and best point. A loser that got 90% of the way to target is a shape problem, not an entry problem |
| E3 | time to resolution; would a time stop have helped or hurt |
| E4 | would moving the stop to break-even have helped, and at what point |
| E5 | spread and commission paid at that specific minute, not the average |
| E6 | the full target/stop ladder: for this condition, which shape was best, in ATR terms |

### F. Data and execution artifacts — the boring killers, check these FIRST
| id | condition |
|---|---|
| F1 | which bar the fill happened on. Filling at the signal bar's own open gave ORB a fake +21 points and +$13.80 a trade. A look-ahead check reading "no difference" is NOT proof of cleanliness — it can be two biases cancelling |
| F2 | feed disagreement: does the same trade win on Dukascopy and lose on Exness |
| F3 | missing bars and gaps around the trade (`check_holes.py`) |
| F4 | stale higher-timeframe values: confirm `asof_index` used the last CLOSED HTF bar |
| F5 | same-bar target-and-stop ambiguity: which was hit first, resolved from M1 or ticks, never assumed |
| F6 | era drift: is the condition's meaning stable as gold repriced (BT21 finding #2) |

### G. The null that stays on the table
| id | condition |
|---|---|
| G1 | **there is no reason.** The winners and losers came from the same distribution and any separation found is noise. Every claimed reason is measured against a luck bar built from random signals with the same rules, same times, same costs. Beating 50 is not the test. Beating the luck bar is |

## Method

1. Build ONE shared feature table over existing trades before anyone refines anything: every row a trade
   from the 277 saved files, every column a condition above. No re-running needed to start.
2. For each condition, report how far the hit rate moves, how many trades remain, and the net dollars per
   trade — all three. A condition that lifts the hit rate while cutting trades by 90% has probably found
   nothing.
3. Log the total number of conditions tried, per agent and in total. The trial count is part of the result.
4. Carry at most 3 refinements per strategy into stage 2.

## Hard rules carried over from AGENT_BRIEF.md

Never place trades, never call `mt5.order_send`, never print `.env`. Do not edit `common.py`, `smc_core.py`
or another agent's files. Decide on CLOSED bars only. Report per pair and pooled. Write only your own
`bt/<name>.py` and your own `results/<NAME>/` folder.

---

# LOCKED DECISIONS (owner, 2026-10-07)

## The split — final, no changes

| stage | dates | gold M1 bars | what you may do |
|---|---|---|---|
| EXPLORE | 2005-01-01 → 2011-01-01 | ~1.9M | baseline, autopsy every trade, tune freely |
| VALIDATE | 2011-01-01 → 2017-01-01 | ~2.1M | confirm only. A reason that dies here IS DEAD |
| STRESS | 2017-01-01 → 2021-01-01 | ~1.4M | adverse spread, worse fills, feed swap |
| EXAM | 2021-01-01 → 2026-09 | ~2.0M | **SEALED.** Once, at the end, with the owner's approval |

`autopsy_lib.gate("EXAM")` raises unless `AUTOPSY_EXAM_UNLOCK` is set. Do not set it.
**The old `period` column in existing CSVs calls 2021-2023 "DEV". Under this split those years are
the EXAM. Ignore that column; use `autopsy_lib.stage_of()`.**

Substrate already available with no re-running: **118,569 saved trades inside EXPLORE**
(113,733 of them gold) across 36 files — `autopsy_lib.load_trades("EXPLORE")`.

## What the owner wants out of this — you are judged on these three

1. **A better entry.** Not the online skeleton. The skeleton plus the conditions that actually separated
   its winners from its losers.
2. **A higher win rate** — more right in 100, measured against the luck bar, not against 50.
3. **A higher profit factor** (gross winnings divided by gross losses). Report it at every stage.

"It failed" is not an acceptable finding on its own. If it failed, the deliverable is **why** — which
condition in the catalogue separated the winners, how much it was worth, and what you would change.
Every strategy on the slate is treated as very important. None gets a cursory pass.

## Use ATR, never dollars — and note how much the bar moves

Measured on EXPLORE gold (median M15 ATR 1.685):

| shape | needs, to break even |
|---|---|
| 1.5×ATR target / 1.0×ATR stop | **42.9 right in 100** |
| the sniper's live $15 / $70 | **55.8-58.1 right in 100** |
| 0.5×ATR target / 2.0×ATR stop | 82.7 right in 100 |

The bar is not a property of the market. It is a property of the shape you choose. Moving from the live
dollar shape to a sensible ATR shape drops the bar by about **13 points** — more than any entry filter in
this project has ever produced. **Work the shape question as hard as the filter question.**
`autopsy_lib.ATR_LADDER`, `breakeven_rate()` and `shape_dollars()` are there for this.

## The ten strategies, one agent each

| # | strategy | agent folder |
|---|---|---|
| 1 | EMA pullback: HTF 50-EMA trend → M1 pullback to 9/21 EMA | `results/AUT01_emapull/` |
| 2 | Liquidity sweep reversal: wick past an N-bar extreme, close back inside | `results/AUT02_sweep/` |
| 3 | Break and retest of a horizontal micro S/R | `results/AUT03_breakretest/` |
| 4 | Opening range breakout, London and New York | `results/AUT04_orb/` |
| 5 | VWAP bounce and rejection, including the ±σ bands | `results/AUT05_vwap/` |
| 6 | Order block retest (SMC) | `results/AUT06_orderblock/` |
| 7 | Fair value gap fill (SMC) | `results/AUT07_fvg/` |
| 8 | Break of structure / CHoCH continuation | `results/AUT08_bos/` |
| 9 | Time-of-day and session edge | `results/AUT09_timeofday/` |
| 10 | Momentum continuation / trend following | `results/AUT10_momentum/` |

## Overfitting caps — these are hard limits

- At most **40 conditions** from the catalogue per strategy. Log every one in a `Tally`. The trial count
  goes in the report.
- At most **3 refinements** carried from EXPLORE to VALIDATE.
- **No condition may be invented after seeing VALIDATE.** If you think of one, it goes in the report
  under "post-hoc, not tested" and nowhere else.
- Every claimed reason must be sayable as one mechanical sentence. "The model prefers it" is not a reason.
- Every hit rate is reported beside its **luck bar** (`autopsy_lib.luck_bar`, matched on hours) and beside
  the **trade count** and **net dollars per trade**. All four numbers or the finding does not count.
- Check catalogue group **F first**. A look-ahead bug gave ORB a fake +21 points and +$13.80 a trade in
  BT21, and a look-ahead test that reads "no difference" can be two biases cancelling.

---

# CORRECTIONS (2026-10-07, after AUT02 reported — all verified)

**1. The duka gold feed has NO SPREAD.** `data_duka/XAUUSD_M1.parquet` spread column: 5,526,570 bars,
one unique value, zero. Under `SMC_DATASET=duka`, `simulate()` sets ask = bid and gold trades free.
Every duka gold backtest that did not inject a spread — **BT15, BT17, BT20, BT21 included** — is
optimistic by the whole spread. duka FX pairs are unaffected (EURUSD mean 0.31 pips, max 8).
**Action: inject one.** `autopsy_lib.gold_spread()` returns $0.30 as a documented conservative
stand-in. State the figure you used in your report. Do not edit common.py.

**2. The EXPLORE window table above was wrong for gold.** The duka gold feed starts **2009-03-15**,
so EXPLORE holds **622,305** gold M1 bars (1.8 years), not ~1.9M. Worse, those 1.8 years are almost
all one direction — gold ran 866 → 1429. AUT02 proved this matters: its best EXPLORE condition read
+$9.43 on buys against -$1.29 on sells, and the ranking flipped on VALIDATE's 1920 → 1050 crash.
**Treat any EXPLORE-only result as a bull-market reading until the side split says otherwise, and
report BUY and SELL separately.**

`data_duka_full/` holds **2,051,541** gold EXPLORE bars back to 2005-01-02 and carries a real spread
column, but its units are unverified (median 361 against gold near $1274 — consistent with points of
0.001, i.e. ~$0.36, NOT with common.py's 0.1 pip scaling). Do not switch to it until that is settled.

**3. Three bugs in autopsy_lib, now fixed** (AUT02 found them; thank it, do not re-report them):
- `breakeven_rate()` omitted the spread — it now takes one and defaults to `gold_spread()`.
- `luck_bar()` matched `hours` on the bar's OPEN while callers pass hours derived from `t_signal`,
  a close — up to an hour out. Now matches on `tclose`.
- `luck_bar()` hung sl/tp off the raw bid open while `simulate` fills a buy at the ask. Now offset
  by half a spread.
If you already ran a luck bar, re-run it; the old one was slightly wrong in your favour.

**4. Feed swap at STRESS is impossible.** duka and Exness Zero overlap only inside the sealed EXAM.
Substitute an adverse-cost run instead and say so.

**5. More instruments beats more filters.** AUT02's recommendation, and it is a good one: the duka
feed has 25 FX pairs with full 2005-2011 history. A condition that replicates across pairs is worth
more than one more condition tried on 1.8 years of one-directional gold.

---

# ROUND FINDINGS — apply these, do not re-derive them (updated 2026-10-08)

## The one filter worth shipping (AUT09, 1.36M random entries, survived all three stages)

**"Trade the overlap" is the WRONG instruction. "Trade when the round trip is a small share of the
move you are playing for" is the right one, and it largely subsumes the session clock.**

Cost share is **1.9x stronger** than the session, and it wins the conditioning test both ways: holding
cost share fixed, Asia and the late hours are as good as the overlap. About **60% of the whole session
effect IS cost share**. It needs no session calendar, no DST logic, and transfers across pairs and eras.

> Skip when (spread + commission) > **20% of the ATR risked**; never trade above **35%**.
> Hard-block **19:00-23:00 and 02:00-04:00 UTC**. The worst hours are **20 and 21**, not 22.
> Never enter right after a volatility spike (-$1.09, the worst single condition measured).
> **Ignore the day of the week** entirely - $0.06 of spread across 1.2M trades.
> **Below ~10% of one ATR the filter has no power.** Gold's round trip is 6-18% of an ATR, so gold's
> five cost groups have NO ordering. Check your instrument against that line before applying it.

`autopsy_lib.cost_share()`, `COST_SHARE_SKIP/_HARD/_FLOOR`, `BAD_HOURS_UTC`.

**There is no edge in WHEN you trade: 0 of 239 hour-by-weekday cells pays a positive dollar.** But
*when* changes how much you LOSE by $1.07 a trade - larger than any entry filter this project has found.

## Settled facts

| | |
|---|---|
| Gold round trip | **$1.60** ($1.10 commission + $0.50 spread = 0.05 price units) |
| Break-even, gold | 3.0x/1.0x ATR needs **27.6** · 2.0x/1.0x **36.8** · 1.5x/1.0x **44.1** · 1.0x/1.0x **55.0** · live $15/$70 **55.8-58.1** |
| EXPLORE gold | **622,305 bars** from 2009-03-15 (1.8 years), gold 866 -> 1429. One-directional |
| `data_duka_full` spread unit | **0.0001 per point** for gold, not 0.001 (AUT09 settled it) |
| Feed timezone | true UTC, proved two ways. Getting DST right is worth **$0.03 a trade** - do it, it is free |

## Mandatory controls (all four, per variant)

1. **Always-long / always-short / coin flip** at the rule's own minutes. AUT05 invented it; it has since
   been decisive for every agent. It is how you tell skill from a one-directional sample.
2. **Luck bar** shape-matched, hour-matched AND hold-matched (`max_hold_min`), at **n>=20,000** with the
   seed error band quoted. At n=4,000 the spread across seeds is 2.67 points.
3. **Buy and sell reported separately.**
4. **Look-ahead twin in BOTH points and dollars.** AUT01 measured one at +2.75 points but **+$2.29 a
   trade** - a hit-rate-only check would have passed a dirty pipeline. AUT09 notes the converse: a
   random-direction entry has no information to leak, so that test has almost no power on one.

## What four agents have already proved - do not spend your 40 conditions here

- **Every level-quality condition makes things WORSE.** Room to run, untouched liquidity, first touch,
  freshness, clean air, major break: negative in AUT01, AUT02, AUT03 and AUT08 independently. An
  untouched level ahead behaves as a **magnet, not a wall** - a destination, not an obstacle.
- **Following loses; fading is the only thing above a coin flip** - and still too small to pay the
  spread. Four independent instances (BT17, BT21 sweeps, AUT03, AUT08).
- **Shape beats every filter**, by 3-6x, every time it has been measured.
- **Judge shapes at matched RISK, never at a matched lot.** Widening the target rescales the shortfall;
  it does not close it.
- **Stop placement:** the obvious structural level is usually the WORST stop (inside normal noise, median
  0.82 ATR, clipped constantly). The **origin of the impulse leg** was the best tested (median 5.4 ATR).

## Open bugs in shared code (reported, NOT fixed - do not fix them yourself)

1. `common.py:172/200` - a corrupt bar can only ever hurt you: TP is disabled on the fill bar, the stop
   is not, and its fill is unbounded. `:172` only tests `risk > 0`, so a negative stop price passes.
   One bar produced a single **-$1,437,434** trade. **The fix belongs in shared code, not in ten
   agents' separate screens.** Until then, always use `autopsy_lib.safe_bars()`.
2. `common.py:264` - `save()` reads `trades.period` before checking it exists.
3. `common.py:72` - `bars()` has no in-memory cache; `safe_bars()` memoises.
