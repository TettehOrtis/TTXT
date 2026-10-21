# AUT11-AUT14 — four new studies (ideas 3 and 4), written 2026-10-16

Written for: the four agents running these studies. The owner reads it too.

## Why these four

All 10 earlier strategies came from one playbook (SMC / ICT) and all lost about the trading cost per trade.
The owner picked two ways forward:

- **Idea 3: ideas from outside that playbook.** Three studies: AUT11 (daily trend following), AUT12 (price
  around fixed clock events), AUT13 (gold lagging the US dollar).
- **Idea 4: combine the small real edges found so far.** One study: AUT14.

Each agent does ONE study. Your study is in section "Your study" below. Do not touch another agent's folder.

## Read first (in this order)

1. `smc_bot/bt/AUTOPSY_BRIEF.md` (all of it, including CORRECTIONS and ROUND FINDINGS)
2. `smc_bot/bt/AUTOPSY_SCHEMA.md` (the exact JSON shape you must write)
3. `smc_bot/bt/AGENT_BRIEF.md` and `smc_bot/bt/HANDOFF.md` sections 4a, 5.4 (method traps), 9
4. A finished example: `smc_bot/bt/aut04_orb.py` and `smc_bot/results/AUT04_orb/autopsy.json`
5. `smc_bot/bt/autopsy_lib.py` (use `safe_bars`, `gate`, `clip`, `luck_bar`, `Tally`, `stage_of`)

## Hard rules (a break of any of these voids the study)

- **EXAM stays sealed.** Never set `AUTOPSY_EXAM_UNLOCK`. Use only EXPLORE (2005-2011), VALIDATE (2011-2017),
  STRESS (2017-2021). Do NOT read the Exness Zero feed (`data_zero`): it starts 2021, which is the sealed period.
- **Data:** `os.environ.setdefault("SMC_DATASET", "duka")` before importing `common`. Always `safe_bars()`.
  The duka gold feed starts 2009-03-15 and has no spread: inject 0.05 price units exactly the way `aut04_orb.py`
  does. Gold round trip = **$1.60** at 0.10 lot ($1.10 commission + $0.50 spread). Never restate it differently.
- **Cost is paid ONCE**, at close, inside `common.simulate`. Never subtract commission or spread again anywhere.
- **Write only your own files:** `smc_bot/bt/aut1X_*.py` and `smc_bot/results/AUT1X_*/`. Do not edit `common.py`,
  `autopsy_lib.py`, `plain_report.py` or any other agent's file. Never place trades, never call `mt5.order_send`,
  never print `.env`.
- **Caps:** at most 40 conditions tried, at most 3 refinements carried from EXPLORE to VALIDATE, and nothing
  invented after you have seen VALIDATE (put late ideas under `post_hoc_untested`).
- **Closed bars only.** Every decision uses data that existed at that minute.

## The four mandatory controls, per variant

1. Always-long / always-short / coin-flip at the rule's own minutes.
2. Luck bar: random entries, same hours, same shape, same hold, n >= 20,000, 5 seeds, quote the band.
3. Buy and sell reported separately.
4. Look-ahead twin, in BOTH points and dollars.

## Method traps already found in this project (do not fall in them again)

- **Pooled results hide different winners.** If you pool instruments or conditions, split the result by
  instrument INSIDE each stage. A pooled "survivor" whose profit comes from a different pair each stage is
  several unrelated coincidences, not a survivor.
- **Fill-anchored barriers make adverse-fill stress powerless.** If stop/target hang off the fill price, a worse
  fill just moves the barrier with it. Anchor to the signal price, or prove the stress row differs from the
  normal row. A stress test that returns the same dollars at three cost settings has no power.
- **A filter picked after seeing which subset wins is a cherry-pick.** AUT06's "touch 1 only" looked real in all
  three windows and died when touches 2 and 3 were included. State every rule BEFORE you run it.
- **EXPLORE gold is one-directional** (866 to 1429). Any EXPLORE-only gold result is a bull-market reading
  until the buy/sell split says otherwise.
- **Shape beats filter.** A win rate means nothing without the payoff size (see AUT07: 78% wins, break-even
  needs 82%). Always quote the break-even win rate for the shape used.
- Use `plain_report.py ... --risk 0 --lot 0.10` when your trades file has no usable `R` column.

## Deliverables (all of them)

In `smc_bot/results/<YOUR_FOLDER>/`:

1. `autopsy.json` in the AUTOPSY_SCHEMA shape. Every variant: baseline, named failure reason, `fix_attempts`
   chain, `fix_exhausted_because`. "It failed" is not enough; say WHY in one mechanical sentence.
2. Trades CSVs in `common.simulate` format for each variant, and for the best variant of each refinement an
   all-stages file (a `period` column naming EXPLORE / VALIDATE / STRESS).
3. For each of those, run `smc_bot/tools/plain_report.py` to get the full `_SUMMARY.json`, `_SUMMARY.md`,
   `_READABLE.csv` (win/loss, profit factor, by exit reason / direction / pair / hour / weekday / month / year,
   drawdown, streaks, time in trade). The owner wants the full JSON for every variant, not a small summary.
4. `BREAKDOWN.md`: short intro, then one section per variant with its table, then ONE plain verdict sentence.
5. `report.md`: the plain-English story. **Short words, short sentences, simple English.** No jargon (no R,
   P95, t-stats). Dollars per trade.

When finished, your final message is at most 200 words: the verdict for each variant and the file list.

---

## Your study

### AUT11 — daily trend following (folder `results/AUT11_trend`, script `bt/aut11_trend.py`)

Idea: costs are tiny next to a multi-day move (an FX pair moves ~70 pips a day; the round trip is 1-2 pips).
- Instruments: the 24 duka FX pairs from 2005 (main test, EXPLORE has 6 years) plus gold (secondary, from 2009).
- Build D1 bars from M1. State the day boundary you chose (use the 22:00 UTC rollover) and why.
- Rules to test (fixed up front, no grids beyond these): sign of the N-day return for N = 20, 60, 120;
  Donchian breakout N = 20, 55; moving-average cross 20/100. Exit: opposite signal, or 3 x daily ATR trailing
  stop. Long and short.
- **Overnight swap is NOT in the data.** Report results before swap, state the average nights held, and compute
  the break-even swap per night (net dollars per trade divided by nights held). Mark swap-sensitivity clearly.
- Pooled across pairs: always decompose by pair inside each stage. Report trade counts and the give-or-take
  (few trades means weak evidence; say so plainly).
- Null: random entry days with the same hold, plus always-long / always-short.

### AUT12 — price around fixed clock events (folder `results/AUT12_clock`, script `bt/aut12_clock.py`)

Idea: some minutes of the day are scheduled events. AUT09 already showed the hour of day alone has no edge
(0 of 239 hour-by-weekday cells pays), so the thing tested here is the DIRECTION rule around the event, not the clock.
- Events (convert to true UTC with correct daylight saving; this is worth $0.03 a trade, do it): London gold AM
  fix 10:30 London, PM fix 15:00 London, COMEX gold open 08:20 New York, US data time 08:30 New York every day,
  and the monthly jobs report (first Friday, 08:30 New York, computable from the calendar). No news calendar
  exists in the project; do not pretend one does.
- Rule: direction = the sign of the move over the 30 (or 60) minutes BEFORE the event; test follow and fade.
  Enter at the event minute (next bar open), hold 15 / 30 / 60 / 120 minutes. ATR-sized stop and target.
- Instruments: gold (secondary, bull-market warning applies) and EURUSD, GBPUSD, USDJPY (main, from 2005).
- Null: random direction at the SAME event minutes (this is what separates the direction rule from the clock).

### AUT13 — gold lagging the US dollar (folder `results/AUT13_leadlag`, script `bt/aut13_leadlag.py`)

Idea: gold usually moves against the US dollar; if gold has not yet followed a dollar move, trade the gap.
- Build a US-dollar index from M1 log returns of EURUSD, GBPUSD, AUDUSD, NZDUSD (inverted), USDJPY, USDCAD,
  USDCHF, equal weight. Estimate gold's sensitivity (beta) on the PAST 5 trading days only, never future data.
- Signal: gap = gold's return over the last k minutes minus (beta x dollar return over the same k minutes).
  Trade gold to close the gap (fade the gap). k = 1, 3, 5, 15 minutes. Hold 5 / 15 / 30 / 60 minutes.
- Gold has only 1.8 years of EXPLORE (a bull run). Replicate the same structure on EURUSD against the basket of
  the other six USD pairs, which has 6 years of EXPLORE and is the more trustworthy test of whether a lead-lag
  exists at all.
- At M1 the lag may be shorter than a minute. If so, say so: that is a legitimate "no edge" finding with the reason.
- Null: random direction at the same minutes; also the gap rule with the sign reversed.

### AUT14 — combine the small real edges (folder `results/AUT14_combo`, script `bt/aut14_combo.py`)

Idea: three earlier rules each beat a random entry by a small real amount but fell short of the $1.60 cost:
S1 = AUT02 R1 (sweep fade, stretched from the H1 average, M30), S2 = AUT03 failed-retest fade,
S3 = AUT04 London opening-range breakout. Does trading only when two or three agree beat the cost?
- **Step 0, before anything else:** regenerate each signal's times and directions (import the existing `aut02_sweep`,
  `aut03_breakretest`, `aut04_orb` modules read-only; do not edit them). Measure how often two signals fire in the
  same direction within 60 minutes, per stage. If the overlap is too small to test (under about 200 events in
  EXPLORE), STOP and report `untestable_on_this_data` with the counts. Forcing a test on a handful of events is worse than
  no test.
- **Independence check:** are the three signals really different? If their entries and outcomes mostly coincide,
  or all three are "fade the last move", combining adds nothing; say so with the numbers.
- If testable: write the combination rules in a file BEFORE running (at most 3 rules, for example "any two
  agree", "all three agree", "S1 plus S2"), run EXPLORE, carry at most 3 to VALIDATE, touch VALIDATE once.
- Null: the same count of random entries at the same minutes, plus each component signal alone.

---

### AUT15 — how cheap would trading have to be? (folder `results/AUT15_costcut`, script `bt/aut15_costcut.py`)

Idea (owner, 2026-10-16): 14 studies say entries earn less than the trading cost. Cutting the cost is the one
lever not tried. Question: for each study's best variants, how much cost can they carry, and is any of them
profitable at a cost a cheaper account could really offer?

- **Part A, all 14 studies (AUT01-AUT14).** For each study's headline variants and each stage, measure the edge
  BEFORE cost (re-run the existing trade builder with spread = 0 and commission = 0, by changing values inside
  YOUR OWN script's process only; never edit `common.py` or another agent's file) and at the current cost.
  Report the break-even round-trip cost in dollars at 0.10 lot (edge before cost per trade) next to the actual
  cost paid. Mark each variant by: before-cost edge positive in ALL of EXPLORE, VALIDATE, STRESS or not.
- **Part B, at most 6 variants.** Pre-register the rule for picking them BEFORE running: positive before-cost
  edge in all three stages, buy and sell both non-negative or an honest note if one side carries it, and at
  least 500 trades per stage. Then re-run those at a fixed menu of round-trip costs for gold at 0.10 lot:
  $1.60 (today), $1.20, $0.80, $0.50, $0.25, $0. For FX pairs scale commission and spread by the same factors
  (1.0, 0.75, 0.5, 0.31, 0.16, 0). The menu is hypothetical. Do NOT invent broker prices; say plainly that the
  owner must check which account actually offers a given cost.
- Cost scaling must have power: anchor stops and targets to the signal price, and confirm the dollars differ
  across menu rows.
- Report for each Part B variant: the cost at which it breaks even in each stage, and the worst-stage figure.
  Judge by the WORST stage. Buy/sell split and the four controls still apply. EXAM stays sealed, nothing from
  2021 on. No hand-picking: if nothing passes Part A's rule, that is the result, say so with the counts.
- Warn in `report.md`: cheaper accounts often have variable spread, wider spread at news, and more slippage, none
  of which this data can show. Also note that a cheaper account does not change the entry's own edge.
