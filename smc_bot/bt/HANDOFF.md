# HANDOFF — the autopsy round, state as of 2026-10-09

**Written for: the next Claude session picking this up, and the owner reading over its shoulder.**

Read this first, then `bt/AUTOPSY_BRIEF.md` (the method and the round findings) and
`bt/AUTOPSY_SCHEMA.md` (the required JSON). Those two are the governing documents. This file is just
the state of play.

---

## 1. What changed, and why

The owner stopped the old way of working on 2026-10-07. The old way was: pre-register a fixed list of
rule variants, measure them, report pass/fail. It had produced 26 backtest folders, 277 saved trade
files and well over 600 variants — and almost none of it ever asked **why an individual trade won or
lost**.

His instruction, in his words: *"If we are looking at why we lost then we can improve it."* And later:
*"I want every variant to tell me what they did and why it has no edge. I want to see if each of them
tried to fix the whys. Concrete answers and results in JSON. I will not accept any shabby work."*

So every strategy now gets: a baseline, a trade-by-trade autopsy against a catalogue of reasons, an
explicit **chain of attempts to fix the reason it failed**, and four stages with a sealed final exam.

**Do not revert to measuring rules and reporting numbers.** That is the thing he rejected.

---

## 2. The locked period split

| stage | dates | what you may do |
|---|---|---|
| EXPLORE | 2005-01-01 → 2011-01-01 | baseline, autopsy, tune freely |
| VALIDATE | 2011-01-01 → 2017-01-01 | confirm only. A reason that dies here IS DEAD |
| STRESS | 2017-01-01 → 2021-01-01 | adverse costs, worse fills |
| EXAM | 2021-01-01 → 2026-09 | **SEALED.** Once, at the end, with the owner's approval |

Enforced in code: `autopsy_lib.gate("EXAM")` raises unless `AUTOPSY_EXAM_UNLOCK` is set. **No agent has
ever set it, and none should.** The old `period` column inside `results/*/trades_*.csv` uses the OLD
labels and calls 2021-2023 "DEV" — ignore it, use `autopsy_lib.stage_of()`.

**Caveat that matters:** the duka gold feed starts 2009-03-15, so EXPLORE holds only **622,305 gold
bars (1.8 years)**, and gold ran 866 → 1429 across nearly all of it. It is one-directional. Every
EXPLORE-only gold result must be checked with the buy/sell split and the long/short control before it
is believed. Three agents have had a "finding" dissolve under exactly that check.

---

## 3. Status: 8 of 10 strategies done (updated 2026-10-09)

| # | agent | strategy | report | json | verdict |
|---|---|---|---|---|---|
| 1 | AUT01_emapull | EMA pullback — **the sniper's live rule** | YES | YES | no edge; **but see §6 — the "worse than random" reason was WRONG** |
| 2 | AUT02_sweep | liquidity sweep reversal | YES | YES | no edge |
| 3 | AUT03_breakretest | break and retest | YES | YES | no edge |
| 4 | AUT04_orb | opening range breakout | *(JSON only)* | YES | no edge; London is an EXPLORE-only mirage, and **fading beats following again** |
| 5 | AUT05_vwap | VWAP | YES | YES | **untestable** — no volume in the data |
| 6 | AUT06_orderblock | order block retest | `explore.md` + `refinements.md` + `stress.md` | YES | no edge — **but it holds the project's one unresolved lead, §9** |
| 7 | AUT07_fvg | fair value gap | — | — | **stage 0 only** (group F clean; `stage0_causality.md`) |
| 8 | AUT08_bos | break of structure | YES | YES | no edge; 1.8 pts BELOW random |
| 9 | AUT09_timeofday | time of day | YES | YES | no edge in the clock, but found the one shippable filter |
| 10 | AUT10_momentum | momentum continuation | — | — | **not started** (stage 0 only) |

**Remaining: AUT07** (stage 0 done, group F clean — resume from `bt/aut07_fvg.py`, which has
`explore()`, `refinements()` and `final()` already written) and **AUT10** (stage 0 only).
AUT04 and AUT06 have no prose `report.md`; their JSONs are complete and their stage markdown is on
disk, so a report is a write-up job, not a measurement job.

**What the 2026-10-08 later session closed:**

1. **AUT01's correction is DONE.** §6 below is rewritten with the answer. The headline reversed: the
   live bot fires on ~41% of pullbacks, not 0.13%, and its entry is slightly **better** than a coin
   flip at all three stages, not worse. The verdict (no edge) is unchanged. New files in
   `results/AUT01_emapull/`: `livecfg_sidebyside.csv`, `livecfg_controls_best_shape.csv`,
   `livecfg_cadence.csv`, `livecfg.json`; new script `bt/aut01_livecfg.py`; `report.md` has a
   CORRECTION section appended and `autopsy.json` two new variants plus a
   `coordinator_correction_live_config` block.
2. **All ten JSON files that can exist, exist.** AUT02 and AUT05 were written. **AUT08's was never
   missing** — the previous handoff was stale; it is complete, 9 variants, every one with a
   fix-attempt chain. Do not re-do it.
3. **Two cost-basis errors found while checking that work — see §4a.** Neither changes a verdict,
   both change numbers people will quote.
4. **Still not started: AUT10** (momentum). Scripts and stage-0 checks exist. AUT04, AUT06 and AUT07
   were handed to agents in that session; check their folders for `autopsy.json` before restarting
   them, and read any `report.md` prose that was left in the chat rather than placed on disk.

**Earlier note, kept because the reasoning still applies:** the four unstarted strategies have scripts
(24-53 KB each) and stage-0
   checks already exist for all four — resume from those files, do not start over.

**Agent IDs do not survive into a new chat.** You must spawn fresh agents. Give each one: this file,
`AUTOPSY_BRIEF.md`, `AUTOPSY_SCHEMA.md`, its own existing script and results folder, and tell it not to
re-derive anything in §4 below.

---

## 4. Settled facts — hand these to every agent, do not let them re-derive

| | |
|---|---|
| Gold round trip | **$1.60** = $1.10 commission + $0.50 spread (0.05 price units). Source ERRORS #34/#44 |
| Break-even, gold | 3.0×/1.0× ATR needs **27.6** right in 100 · 2.0×/1.0× **36.8** · 1.5×/1.0× **44.1** · 1.0×/1.0× **55.0** · the live $15/$70 **55.8-58.1** |
| `data_duka_full` spread unit | **0.0001 per point** for gold (AUT09 settled it). That file reaches back to 2005-01-02 — the missing four gold years are recoverable if someone reconciles the two builds |
| Feed timezone | true UTC, proved two ways. Correct DST handling is worth $0.03 a trade — free, so do it |
| **Gold pip arithmetic** | `pip("XAUUSD")` = **0.1 price units**, `USD_PIP_01["XAUUSD"]` = **$1.00 per pip at 0.10 lot**. So **0.05 price units of spread = 0.5 pips = $0.50**, and `usd_01` equals `net_pips` exactly on gold. Verified empirically 2026-10-08: a buy fills at open + 0.05 (926.40 → 926.45). **Two reports got this wrong — see §4a** |
| **The live bot's trading window is true UTC** | `SNIPER_TRADING_HOURS=07:00-12:00` is read against `_utc_now()` (`sniper/engine.py:28-29,150`), **not** the broker's server clock, so no DST offset enters. With `SNIPER_WINDDOWN_MINUTES=30` a NEW basket opens only while **07:00 ≤ t < 11:30 UTC** (`engine.py:157`, `_may_open`) |

### 4a. Two cost-basis errors in existing reports — do not propagate them

Both were verified first-hand on 2026-10-08. **Neither changes a verdict**, and both change figures
that get quoted.

1. **AUT02's report says "$2.10 a round trip" throughout. The simulation charged the settled $1.60.**
   It is a prose arithmetic slip: it treats 0.05 gold price units as $1.00 instead of $0.50. The
   consequence is NOT that AUT02's dollar figures are 50¢ conservative — they are the standard ones,
   directly comparable with AUT03's and AUT08's. What IS wrong is every statement in that report
   expressing cost as a **share of a win**: "the $2.10 round trip is 27% of 1R on M5" overstates the
   share by about a third, and condition **E1** ("the round trip costs 12% of 1R or less") was cut on
   the inflated figure, so its true threshold is nearer **9%**. The ranking of its conditions is
   unaffected, because the error is a constant. Corrected in `AUT02_sweep/autopsy.json`
   (`coordinator_corrections`).

2. **AUT05 never charged $1.60 in either run on disk.** Its `report.md` claims $1.60 but every number
   printed in it comes from a **zero-spread** pass ($1.10 all-in). The later re-run in `parts/` used
   **0.30** price units — the project's OLD stand-in, still sitting in the `aut05_vwap.py` docstring —
   which is **$3.00**, i.e. about **$4.10 all-in**. Verified independently: every BUY fill in
   `parts/examples.csv` is exactly +0.30 above `parts_nospread/examples.csv` (7 of 7) and every SELL
   is unchanged (3 of 3), because `simulate()` adds the spread to the ask only.
   **No re-run is needed to settle the verdict:** cost enters as a constant subtraction, so dollars per
   trade are monotone decreasing in the spread, and a cell that loses at $1.10 *and* at $4.10 must lose
   at $1.60. The only cells in doubt were the three that read positive at zero spread (+$0.07, +$0.16,
   +$0.11), and the settled spread costs exactly $0.50 a trade, which puts all three near −$0.40.
   Corrected in `AUT05_vwap/autopsy.json` (`coordinator_corrections`).

**The lesson for the next agent:** state the round trip as **both** the price units and the dollars,
and check the two against each other. Two of five reports got this wrong in the same direction.

### The three data bugs, all verified, all now handled by `autopsy_lib.safe_bars()`

1. **No spread anywhere in the duka feed.** Gold: zero on 100% of 5,526,570 bars. FX: zero on
   **92.0-96.6%** of bars on every pair checked. A zero is a *missing reading*, not a zero spread —
   the non-zero bars of the same file carry sane figures. `safe_bars()` fills each zero with that
   symbol's own median non-zero spread. **Every backtest in this project that predates this fix —
   BT15, BT17, BT20, BT21 included — is optimistic by ~$0.50 on gold and ~1 pip on FX.**
2. **14,158 corrupt bars.** `AUDJPY` has **14,155** from 2005-05-01 21:00 (a feed splice: AUDUSD-shaped
   prices like 0.67 and 2.27 interleaved with real AUDJPY prices near 87.9 and 135, inside single
   bars — squarely inside EXPLORE). `GBPCHF` 2, `AUDUSD` 1. Gold and EURUSD are clean. One such bar
   produced a single **−$1,437,434** trade, 97% of a 35,000-trade pooled loss.
3. **`bars()` re-reads a 250 MB parquet on every call.** `safe_bars()` memoises: 1.27 s first call,
   instant after. This turned one agent's 36-shape ladder from hours back into minutes.

**Always use `autopsy_lib.safe_bars(sym, tf)`. Never `C.bars()`.**

---

## 5. The findings, ranked by what the owner can act on

### 5.1 The one thing worth shipping (AUT09, 1.36M random entries, survived all three stages)

**"Trade the overlap" is wrong. "Trade when the round trip is a small share of the move you are
playing for" is right, and it largely subsumes the session clock.** Cost share is 1.9× stronger, and
it wins the conditioning test both ways — hold cost share fixed and Asia is as good as the overlap.
About 60% of the entire session effect *is* cost share.

> Skip when (spread + commission) > **20% of the ATR risked**; never above **35%**.
> Hard-block **19:00-23:00 and 02:00-04:00 UTC**. Worst hours are **20 and 21**, not 22.
> Never enter right after a volatility spike. **Ignore the day of the week** ($0.06 across 1.2M trades).
> **Below ~10% of an ATR the filter has no power** — gold sits at 6-18%, so it does not apply to gold.

`autopsy_lib.cost_share()`, `COST_SHARE_SKIP/_HARD/_FLOOR`, `BAD_HOURS_UTC`.

The avoid-list is the dependable half: the same −$0.30 a trade on three separate six-year windows.

### 5.2 The live bot — REWRITTEN 2026-10-08 after the §6 correction

**Read §6 before quoting anything here.** Two of the bullets that used to sit in this section were
measuring a rule the live bot does not run.

- **The entry is NOT worse than a coin flip.** The −2.82 / −1.72 / −1.34 points figure is real, but it
  belongs to the rule AUT01's report measured (M15 close-above-EMA50, M1 EMA21, 5 clear bars, no RSI
  gate). Measured at the live bot's **own** configuration, the entry is **above** its own hour- and
  hold-matched luck bar at all three stages: **+1.86 / +0.51 / +0.90 points**, and it beats a coin flip
  in **eight of nine** stage-shape cells by $0.38 to $2.37. It is the first entry in this project to
  beat a coin flip at every stage.
- **It still has no edge, and the reason is now cost, not direction.** The edge over a coin flip is
  about $0.45–$1.20 a trade and the round trip is $1.60. Every stage outside EXPLORE is negative at
  every shape measured.
- **Nothing beats "pick one direction with hindsight."** In nearly every stage some FIXED direction
  beats the rule, and which one changes every time: always-long on EXPLORE all-day (+$1.70 against the
  rule's +$1.59), always-short on VALIDATE (−$0.47 against −$1.94) and on the VALIDATE window (+$2.15
  against −$1.01), always-long on STRESS. That is the definition of a regime artifact.
- **The "+$1.31 from changing the shape" does NOT reproduce on the live signal set.** Against the live
  bot's own $15/$70 baseline the 2.0×/4.0× ATR shape is worth **+$0.45 on EXPLORE, −$1.22 on VALIDATE
  and +$0.99 on STRESS**. The original +$1.31 came from the AUTOPSY signal set, a different and far
  smaller set of bars. **Do not ship it as a free win.** It stays defensible on era-stability grounds
  (BT21 finding #2) but not on measured dollars.
- **What the bot would actually do at its real configuration** (07:00–11:30 UTC, one basket at a time,
  $15/$70, 0.10 lots): **3.3 baskets a week and +$3.78 a week on EXPLORE**, then **12.7 a week and
  −$25.28 a week on VALIDATE** and **11.7 a week and −$6.98 a week on STRESS**. It takes only ~8% of
  the signals that fire, because it is nearly always already in a trade.
- **Keep the 07:00–11:30 window ON.** It creates no edge, but it cuts the weekly loss from −$91.61 to
  −$25.28 on VALIDATE and from −$59.92 to −$6.98 on STRESS, purely by trading less.
- The best single result in the whole round: at 3.0×/3.0× ATR the entry is marginally *above* random
  (+1.14 → +0.33 → +0.06) with profit factor **0.96** on both EXPLORE and VALIDATE. Still under 1.0.

### 5.3 What four agents proved independently — do not spend conditions re-testing it

- **Every level-quality condition makes results WORSE.** Room to run, untouched liquidity, first touch,
  freshness, clean air, major break — negative in AUT01, AUT02, AUT03 and AUT08 separately. An
  untouched level ahead behaves as a **magnet, not a wall**: a destination, not an obstacle.
- **Following loses, fading is the only thing above a coin flip** — and still too small to pay the
  spread. Four independent instances now (BT17, BT21 sweeps, AUT03, AUT08).
- **Shape beats every filter by 3-6×, every time it is measured.**
- **Judge shapes at matched RISK, never at a matched lot.** Widening the target rescales the shortfall,
  it does not close it.
- **The obvious structural stop is usually the worst stop** (median 0.82 ATR — inside normal noise,
  clipped constantly). The **origin of the impulse leg** was the best tested (median 5.4 ATR).
- **VWAP is untestable here.** `tick_volume` is the constant 1 on all 5.5M gold bars, so a
  volume-weighted VWAP is arithmetically identical to a plain running average. The only feed with real
  volume starts 2021 — inside the sealed exam.

### 5.4 Method traps discovered this round

- **A look-ahead check on win rate alone can pass a dirty pipeline.** AUT01 measured a twin at +2.75
  points but **+$2.29 a trade**. Always check both.
- **"No difference" is not proof of cleanliness** — it can be two biases cancelling. AUT02 proved this
  on sweeps, where entry-price and stop-placement bias pull opposite ways.
- **A random-direction entry has no information to leak**, so a look-ahead test on one has almost no
  power. AUT09 flagged this about its own work.
- **Anchoring barriers to the signal close rather than the fill** makes the hit rate *rise* on a worse
  feed (35.68 → 36.63 on identical signals).
- **A pooled multi-instrument result can hold its sign across every stage while the instrument
  producing it changes completely.** AUT06's best refinement reads **+$2.09 / +$0.74 / +$5.18** across
  EXPLORE, VALIDATE and STRESS — which looks like the first thing in this project to survive the full
  gauntlet. It is not. The money comes from a **disjoint source each time**: the small-sample tail of
  exotic crosses pays +$5.23 a trade on EXPLORE (n=929) and **−$2.63** on VALIDATE (n=109), while gold
  pays +$1.85 on VALIDATE (n=506, **88% of the pooled dollars**) off only 120 EXPLORE trades. Three
  unrelated accidents with the same sign, not a replication. **Always decompose by instrument WITHIN
  each stage, never judge a pooled figure alone.** This is the single most useful thing found on
  2026-10-09.
- **Anchoring barriers to the FILL makes adverse-fill tests invisible** — the exact mirror of the
  signal-close trap below. If the target and stop are computed as ATR multiples *off the fill price*,
  then worsening the fill moves the barriers with it, and any trade exiting at target nets the same
  pips regardless of entry. AUT06's stress table proves it: refinement R6 reports an **identical
  +$11.712 a trade, PF 1.828, 89.4 right in 100 at three different cost settings**. A cost test that
  returns the same number at three different costs has no power. **An adverse-fill test must hold the
  barrier PRICES fixed and move only the entry.** Neither default — signal close or fill — is safe.
- **A look-ahead leak can be small in points and large in dollars.** On AUT04's headline refinement
  the twin reads **0.48 points but $4.38 a trade**. A points-only check would have passed it as nearly
  clean while the pipeline handed it five dollars a trade. (The inverse of AUT01's trap, which was
  +2.75 points but +$2.29.) **Both currencies, every time.**
- **For a resting-LIMIT entry the look-ahead twin means something different.** AUT06's twin is $7.94
  a trade *worse*, not better, because filling at the signal bar's own open means filling before price
  reached the limit. It still shows no leak, but it is weaker evidence of cleanliness than the same
  test on a market entry, and should be labelled as such.
- **The luck bar must match the rule's hold**, not just its shape and hours. An unmatched five-day bar
  read about a point low and had inflated one agent's conclusion.

---

## 6. CLOSED — what the live bot actually trades (answered 2026-10-08)

This was the project's most actionable open item. It is done. Script `bt/aut01_livecfg.py`; files
`livecfg_sidebyside.csv`, `livecfg_controls_best_shape.csv`, `livecfg_cadence.csv` and `livecfg.json`
in `results/AUT01_emapull/`; full write-up in that folder's `report.md` (CORRECTION section) and
`autopsy.json` (`coordinator_correction_live_config`, plus the variants `LIVE_AS_SHIPPED` and
`DESCRIBED_BODY_HOLD`).

### The question, and the short answer

AUT01 claimed "the live bot is not trading this rule" because the RSI depth gate passes 136 of 104,202
pullbacks (0.13%). **The claim was backwards.**

| | EXPLORE | VALIDATE | STRESS |
|---|---|---|---|
| pullback minutes (live config, gold) | 163,523 | 595,423 | 397,003 |
| **RSI gate alone — what the bot trades** | **67,026 (40.99%)** | **235,852 (39.61%)** | **154,415 (38.90%)** |
| body hold alone | 56,843 (34.76%) | 213,816 (35.91%) | 147,949 (37.27%) |
| **RSI *and* body hold — what the owner describes** | **13 (0.008%)** | **63 (0.011%)** | **33 (0.008%)** |
| the described rule, inside 07:00–11:30 UTC | **0** | 7 | 4 |

The 136-of-104,202 figure was the RSI gate measured **together with** body hold. Each condition alone
is ordinary — about four pullbacks in ten. Only the **combination** is empty, and that combination is
not what ships. **So the live bot fires constantly, not almost never.**

### What this means, stated plainly

- **The live bot trades the RSI-gated rule with body hold OFF**, on every qualifying minute, M1 EMA20,
  M15 EMA50-above-EMA200, inside 07:00–11:30 UTC. That rule is measurable, and it was measured.
- **The rule the owner describes is not a different, worse rule — it is not a tradeable rule at all.**
  13 signals in EXPLORE's 1.8 years, and **zero** inside the bot's own trading hours. Configured as
  described, the bot would have taken no trade whatsoever in the whole of EXPLORE.
- **The verdicts agree: no edge either way** — but for different reasons. The live rule because its
  real edge over a coin flip is smaller than the $1.60 round trip; the described rule because it
  cannot be measured.
- One correction to a code comment: `sniper/config.py:86-87` says body hold takes "a different set of
  bars, not a subset". **As rules, DESCRIBED is a strict subset of LIVE** — body hold off imposes no
  condition on the close, so every body-hold bar also satisfies the shipped rule.

### The owner's decision list

1. **Do NOT turn `SNIPER_ENTRY_REQUIRE_BODY_HOLD` on.** It would take the bot from 67,026 signals to
   13 in 1.8 years, and to **zero** inside its own hours. If the pattern is wanted, move the RSI
   thresholds to ~60/40 (or off, with 100/0) **first** — `sniper/config.py:182` already advises this.
   That changed rule has **not** been measured; it is a new rule and needs a fresh pre-registration.
2. **Keep the trading window on**, for the loss reduction in §5.2.
3. **Do not ship the ATR shape change as a free +$1.31.** See §5.2 — it does not reproduce on the live
   signal set.
4. **Stop describing the entry as worse than a coin flip.** It is slightly better than one. It is still
   not better than the round trip, which is the finding that matters and which has not changed.

### Method notes worth carrying forward

- **`build()` could not measure the live rule at all, and that is why it had never been measured.** Its
  per-signal loop over confirmed M15 swings is O(signals × swings); on raw cadence it ran over eight
  minutes without finishing a single stage. A reduced builder (`aut01_livecfg.build_light`) was
  verified to reproduce `build()`'s trigger **exactly** — same count and identical `t_signal`, `side`,
  `f_atr`, `sig_close`, `f_rsi_ok` and `f_body_hold` — for both configurations, and the script refuses
  to run if it does not. **If you need a feature-light signal set, do this rather than trusting a
  rewrite.**
- **`common.simulate` is linear in orders** (a Python loop per order, walking M1 bars). 236,000 signals
  × two shapes × four direction controls × a look-ahead twin is hours. Subsample to ~25,000 and report
  the true population separately; 25,000 trades pins a hit rate far finer than these questions need.
- **Run the direction control at the shape that reads positive, not just at the baseline shape.** The
  first pass here ran it only at 1.5×/1.0×, which is negative everywhere — so the one control that
  could dissolve the result was missing from exactly the cell that needed it. It did dissolve it.
- **A one-basket-at-a-time run is a different question from a per-signal run**, and for a live bot it
  is the more honest one: it takes only ~8% of signals. Report both.

## 7. Practical notes for running the agents

- **The session limit has killed every agent four times.** Ten in parallel lasts about twenty minutes.
  **Run three at a time.** Tell each agent to cache intermediate results to its own folder as it goes,
  and that if budget runs short it must write the report and JSON with what it has and mark the
  unfinished stages — never leave nothing.
- **Subagents are blocked from writing prose `report.md` files** ("Subagents should return findings as
  text, not write report files"). JSON goes through fine. When a report is refused the agent should
  hand the text back and the coordinator writes it — AUT03 correctly refused to route around the
  guardrail with a shell heredoc, which is the right call. Expect to place 2-3 reports by hand.
- `results/` is now **6.4 GB**. The per-symbol trade CSVs and `AUT09/_map_trades.parquet` (183 MB) are
  the bulk; the quality-map CSVs and the JSONs are the reusable artifacts. Prune if disk matters.
- Run everything with `SMC_DATASET=duka` and `.venv/Scripts/python.exe`.

---

## 8. What I would do next, if the owner asks

1. **Settle §9, the gold lead, FIRST.** It is the only live thing in the project and the four checks
   that would kill or keep it are all cheap. Everything else below is tidying by comparison.
   **1b. Finish the remaining two strategies: AUT07 (fair value gap) and AUT10 (momentum).** AUT04 and
   AUT06 are done (JSONs written 2026-10-09). AUT07's stage 0 is clean and its script has
   `explore()`, `refinements()` and `final()` already written. **Check for `autopsy.json` before
   restarting anything.**
   **1a. Re-read the live bot's exit, not its entry.** The §6 correction showed the live rule's entry
   is marginally better than random at every stage while the strategy still loses — so what is left is
   the exit and the cost, and the exit has only ever been measured at two shapes on this signal set.
   A full ATR ladder on the LIVE signal set (not the AUTOPSY one) has never been run and is cheap:
   `aut01_livecfg.signal_sets()` plus `aut01_emapull.ladder()`.
2. **Fix `common.py:172/200` properly.** A corrupt bar can only ever hurt you: the target is disabled
   on the fill bar, the stop is not, and its fill is unbounded. The fix belongs in shared code, not in
   ten agents' separate screens. It has been reported three times and not fixed because of the
   no-editing rule — the owner should lift that rule for this one fix.
3. **Re-run the gold conclusions of BT15/BT17/BT20/BT21 with a real spread.** They were all decided on
   a zero-spread feed, so their verdicts are if anything too generous — but no gold number in this
   project is trustworthy until that is redone.
4. **Go wider on instruments, not deeper on filters.** The duka feed has 25 pairs with full 2005-2011
   history. A reason that holds across 25 markets is worth far more than a 41st condition tested on
   1.8 years of one-directional gold. AUT02 recommended this; AUT08 did it and got the strongest result
   of the round because of it.
5. **Recover the missing gold years.** `data_duka_full/` reaches to 2005-01-02 — 2,051,541 EXPLORE bars
   instead of 622,305, and its spread column is real. Its unit is settled (0.0001/point). Reconciling
   the two builds would more than triple the gold explore window and remove the one-directional problem.

**UPDATE 2026-10-09:** eight strategies now measured, none has an edge. Two things changed. First,
AUT06 produced what looked like the project's first VALIDATE survivor (+$2.09 / +$0.74 / +$5.18) and
it is an artifact — the instrument carrying it changes every stage (§5.4). Second, inside that failure
there is **one genuine unresolved lead**: the unfiltered rule on gold alone is positive on all three
open periods (§9). It will probably dissolve like everything else, but it has not yet, and it is
cheap to settle. AUT04 died ordinarily, and gave the round its fifth independent instance of fading
beating following.

**The honest summary for the owner (revised 2026-10-08, later session):** six strategies measured
properly, none has an edge. The durable gains are the cost-share filter and trading less — not the ATR
shape change, which was the previous summary's second pillar and which **does not reproduce on the
rule the live bot actually runs** (§5.2). They reduce losses substantially and turn nothing profitable.

What genuinely improved today is the *diagnosis*, not the result. The live bot's entry is slightly
better than a coin flip at all three stages — the first thing in this project that is — and it still
loses, because the edge is worth about $0.45–$1.20 a trade against a $1.60 round trip. That is a
cleaner and more useful statement than "the entry is worse than random", which was measuring a rule
the bot does not run.

At its real configuration the bot would lose roughly **$7 to $25 a week at 0.10 lots** on the two
periods it has never seen, and make **$3.78 a week** on the one-directional stretch it was tuned on.
**The demo forward test remains unstarted and there is still nothing that justifies starting it.**

---

## 9. The project's one unresolved lead — gold order-block retests (new, 2026-10-09)

Everything else in this round is dead. This is the only thing that has not dissolved yet, and it is
cheap to settle. **It is a lead, not a finding.** Do not trade it and do not quote it as a result.

**What it is:** AUT06's rule with **no filter at all** — an H1 order block, entered with a resting
limit at the proximal edge, 0.5× ATR target behind a 2.0× ATR stop, first touch only — measured on
**gold alone** instead of pooled across 25 pairs.

| stage | trades | right in 100 | PF | $/trade | buys | sells |
|---|---|---|---|---|---|---|
| EXPLORE | 300 | 82.0 | 1.066 | **+$0.749** | −$2.211 | +$4.367 |
| VALIDATE | 930 | 81.4 | 1.083 | **+$1.097** | +$1.477 | +$0.694 |
| STRESS | 606 | 79.2 | 1.051 | **+$0.585** | −$0.032 | +$1.257 |

Gold's own hour- and hold-matched luck bar is **76.97** right in 100 at **−$1.786** a trade (n=20,000
× 5 seeds, band 76.61–77.35), so EXPLORE sits about **5 points and $2.54** above random.

**Why it is worth a look**
- Positive on all three open periods, with a profit factor above 1.05 every time, on samples worth
  having (300 / 930 / 606) — nothing else in this project has managed that.
- It is the **unfiltered** rule, so none of the condition-search overfitting applies. There was no
  condition search to overfit.
- Nobody looked at it because the study was built pooled over 25 pairs, and **gold is 1.5% of the
  pooled trade count** — it was invisible inside the total.

**Why it is NOT a finding yet, and the reason to expect it to dissolve**
- **The side that pays keeps changing.** Sells +$4.367 against buys −$2.211 on EXPLORE — a $6.58 gap
  on roughly 150 trades a side, far too wide to be stable. Balanced on VALIDATE. Sells again on
  STRESS. A real effect does not keep swapping which side carries it.
- **It has had no direction control of its own.** The always-long / always-short / coin-flip bracket
  was run on the pooled 25-pair set, not on gold. Every promising result in this project that has
  been put through that bracket on its own subset has died, including the live bot's on 2026-10-08.
- EXPLORE gold is 1.8 years and one-directional.

**What would settle it, in order, all cheap**
1. Always-long / always-short / coin flip **on gold alone, per stage**, at this shape. If a fixed
   direction beats it, stop.
2. A **gold-only luck bar per stage**, not just the EXPLORE one.
3. **More gold trades.** The entries table is already generated with `max_touch=3` and then filtered
   to `touch_n == 1`, so touches 2 and 3 are **on disk and have never been scored**. Also §8 item 5:
   recovering the 2005-2009 gold years from `data_duka_full` would roughly triple the EXPLORE sample.
4. The **full 36-cell shape ladder on gold alone**, rather than inheriting the pooled best cell.

Full detail and the per-stage decomposition: `results/AUT06_orderblock/autopsy.json`
(`the_one_open_lead`) and `scrutiny_all_stages.csv`.

**Read §5.4 first.** The reason this lead exists at all is that the pooled refinements looked like
they survived and did not — the instrument carrying them changed every stage. Do not repeat that
mistake on gold: decompose by period and by side before believing anything.
