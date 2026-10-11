# SMC backtest program

## Date split (fixed, never changed)
| Period | Dates | Use |
|---|---|---|
| **DEV** | 2021-01-01 → 2023-12-31 | explore, build, compare variants, fix bugs |
| **VAL** | 2024-01-01 → 2025-06-30 | confirm the few choices made on DEV; if a variant only works on DEV, it is dropped |
| **TEST** | 2025-07-01 → today | **locked**. Run once, on the final system only. Never used to choose anything. |

Anti-overfitting rules: small, sensible parameter sets chosen from trading logic (not large grids); every variant tried is counted
in the backtest's report; a choice is kept only if it is positive on DEV **and** VAL, on most pairs, and not driven by one month.

## Pairs and costs
EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURGBP EURJPY XAUUSD. Zero-account M1 history (`../data_zero`), spread from data,
real commission (measured 2026-09-27), stop slippage 0.2 pip. Money is reported per 0.10 lot.

## Shared harness (`bt/common.py`), used by every backtest
Cached bars (M1/M5/M15/M30/H1/H4), causal SMC detection (`smc_core.py`), a trade simulator on M1 (limit/market entries, SL/TP,
break-even, max hold, flat before weekends and holiday closures, SL-first when ambiguous), a one-position-per-pair rule, and a
standard report + trades.csv. Agent rules: `bt/AGENT_BRIEF.md`. Harness self-test: `bt/test_common.py`.

## Backtests
| # | Question | Owner / status |
|---|---|---|
| BT1 | **Liquidity sweep**: does trading the reversal after a sweep of a swing high/low make money? Which timeframe, which stop, which target? | Opus, **done**: no edge. 18/18 DEV lose, gross ≈ 0 (coin flip); best = M5 CHoCH confirmation −0.05R (VAL −0.06R) |
| BT2 | **Order block**: does a first retest of an unmitigated OB hold? Entry at edge vs 50%, stop beyond OB, targets | Opus, **done**: no edge. 21/21 DEV variants lose, 3/3 VAL lose; roughly random before costs |
| BT3 | **Fair value gap**: does price reverse from FVGs? Entry at edge vs 50%, stop beyond candle-1, targets | Sonnet, **done**: no edge. 16/16 DEV ≤ 0, VAL best +0.012R = noise; ≈ random before costs |
| BT4 | **Structure**: BOS continuation vs CHoCH reversal, entry on retest; which timeframe | Sonnet, **done**: no edge. 92/96 DEV cells negative; raw post-break move ≈ 0 (H1/H4), slight mean reversion (M15/M30). Context only |
| BT5 | **HTF bias + EMA**: does 1H/4H structure + EMA200 predict direction? Trending vs ranging, breakout vs retest context | Fable, **done**: small real signal from H4 structure and EMA200-H1; H1 structure worse than random; bias alone loses (16/16 DEV, 3/3 VAL). Use as a veto only |
| BT6 | **Full SMC top-down** (smc-strategy.md 5.7) built from what BT1–BT5 prove; stop placement and exit/hold variants | Opus (BT6b) + Fable (BT6a) + RECON, **done**: canonical REF DEV 150 trades net +0.196R, VAL 88 net +0.113R. Robust to rule readings (RECON), but t ≈ 1 → unproven. Only positive result so far |
| BT7 | **Timing/context**: sessions (London, NY, killzones), day of week, volatility regime | Sonnet, **done**: no session edge (14/14 DEV, 3/3 VAL negative, gross ≈ 0). Use sessions as a cost filter: London + NY killzones, Tue–Fri, flat by 20:00 UTC, skip EURGBP |
| BT8 | **ML** on the BT6 setups (walk-forward inside DEV+VAL), learns from new trades | Opus, **done**: ML does not beat REF or a random filter OOS (DEV-OOS and VAL). Kept as a shadow scorer with a monthly retrain + guard |
| BT9 | **Multi-day trend** (time-series momentum 63/126d, slow bias held 3d, Donchian 20/10 & 55/20), flat-weekend and hold modes | Opus, **done**: no edge. Best DEV gross +0.034R; VAL TSMOM-126 lost on all 9 FX pairs; only gain = one gold long (bull run). Swap negative on every side |
| BT10 | **Round-number liquidity** event study | Sonnet, **done**: no effect (breach = placebo, no bounce) |
| DATA2 | **More history** (user request 2026-09-29): Dukascopy M1 bid+ask 2005-01 → 2025-06 for our 10 instruments + 15 liquid crosses → `data_duka/`, checked against data_zero on the 2021+ overlap | Sonnet agent, running |
| BT13 | **Frozen REF on 2005–2020 (HIST)**: never-seen history, 25 instruments, per era and per pair. The decisive test of the REF edge | Opus, **done: FAILED** the pre-registered rule. HIST net +0.036R (t 0.46), crosses −0.028R. The DEV window was lucky. REF has no demonstrated edge |
| BT8b | **Opus, done: NOTHING PASSES** (0/13, best t 0.59). **ML re-run on 2005–2025 (≈70k setups)**: loss autopsy (why each trade lost, per era), bigger-picture features (D1/W1 trend, regime, currency strength, risk-on/off), dynamic take/skip + target + break-even choice, SHAP explanations, yearly walk-forward (`bt/BT8b_BRIEF.md`) | after DATA2 + BT13 |
| BT14 | **Placebo calibration of the method itself**: run canonical REF over 200 zero-drift GBM worlds (known zero edge) and measure what our own pipeline reports. P95 of the best-of-search distribution = the acceptance bar for every future candidate (`bt/BT14_BRIEF.md`) | Opus, **done** 2026-10-05: 200 zero-edge worlds. Bar = **P95 +0.472R**. 42% of noise worlds beat REF's +0.196R, 31.5% reproduce its whole headline, and 'London beats NY' is a 50/50 coin flip. BT6 closed as a false positive |
| BT14 | **Placebo calibration of the method**: run the frozen REF (and the same breadth of variant search) over synthetic data known to contain zero edge, 200 times. How big must a backtest number be before it means anything? | Opus, **done**: the method invents results. Best-of-search under pure noise: **P95 = +0.35R**, P99 = +0.48R. REF's +0.196R is BELOW P95 → BT6 closed as a false positive. P95 is now the acceptance bar for every candidate |
| BT11+ | added as needed | |

Each backtest folder `results/BTn/` contains: `report.md` (method, variants tried, DEV/VAL tables per pair, findings, errors),
`trades_*.csv` (every trade), and its script `bt/btn_*.py`.

## Status log
- 2026-09-29: harness `bt/common.py` built and hand-checked (`bt/test_common.py` passes); bar cache built for 10 pairs (M1–H4).
- 2026-09-29: running in parallel: BT1 sweep (Opus), BT2 order block (Opus), BT3 FVG (Sonnet), BT4 structure (Sonnet),
  BT5 HTF bias/context (Fable), BT7 sessions (Sonnet); plus the Opus audit of the old combined backtest (feeds ERRORS.md).
- 2026-09-29: BT5 done, BT2 done (see table). Harness fixes: ns time precision (speed), exit before holiday closures (ERRORS #3, #5).
  Next: review every report (bug vs strategy), log errors, then design BT6 (full pipeline) from what survives DEV+VAL.
- 2026-09-29: ALL component backtests done (BT1, BT2, BT3, BT4, BT5, BT7): every component is ~random before costs and loses after.
  Harness fixed along the way (ERRORS #3, #5, #10, #13). BT6 spec written from the lessons (`bt/BT6_BRIEF.md`).
  Now running: BT6a (Fable) and BT6b (Opus), two independent implementations of the full pipeline for cross-checking, plus a
  research agent (Sonnet, web) on published SMC evidence and ranked hypotheses (→ research_notes/SMC_evidence.md).
- 2026-09-29: research agent done → `research_notes/SMC_evidence.md`. No credible public test shows an after-cost SMC/ICT edge
  (StatOasis: 648 ICT backtests, 31/32 t-stats < 2). Documented edges: multi-day trend (time-series momentum), fix-window flows
  (small), round-number stop cascades (Osler, 1990s data). Queued after BT6 (frozen parameters from the papers, no grids):
  BT9 multi-day trend position · BT10 round-number stop-cascade/bounce event study (tests the "liquidity" idea directly) ·
  BT11 fix-window reversal · BT12 extension fade in the overlap. Rule: gross ≥ 2× cost, same sign in ≥ 8/11 instruments, BH correction.
- 2026-09-29: **BT6b done: first positive GROSS R on both DEV (+0.26R, 150 trades) and VAL (+0.17R, 89 trades)**, net +0.20 / +0.10,
  cost only 0.06R (structural stops). NOT significant (t ≈ 1.1 / 0.4; ~1 trade/week over 9 pairs; pair signs flip). London beat NY
  in both periods (post-hoc → the single pre-registered hypothesis). BT6a killed by the checker outage → resume to cross-check.
  Next: (1) resume BT6a and reconcile trade by trade; (2) freeze REF (+ London-only); (3) forward-test REF on the Zero demo,
  signals + small size; (4) BT8 ML on the BT6 features; (5) BT9–BT12 from research. TEST stays locked until one system is frozen.
- 2026-09-29 17:30 UTC: Wi-Fi outage had stopped the agents (and likely the USDJPY bot at 15:16). Bot restarted under an
  auto-restart loop (start_usdjpy.bat → logs/usdjpy/supervisor.log). Resumed BT6a (Fable, cross-check of BT6b, same REF config).
  Started BT9 multi-day trend (Opus) and BT10 round-number liquidity event study (Sonnet) in parallel.
- 2026-09-29: BT10 done: round-number liquidity has no tradable effect (breach = placebo, no bounce); liquidity question closed.
- 2026-09-29 22:10 UTC: agents had stopped on an API usage limit (reset 20:50 UTC). BT6a had finished its runs: its REF build
  disagrees strongly with BT6b (790 vs 150 DEV trades; −0.11R vs +0.20R net), so the BT6 "edge" may depend on specific rule
  readings → RECON agent (Opus) reconciling. BT9 resumed. USDJPY bot survived a broker disconnect at 22:06 (auto-reconnect).
- 2026-09-29 22:40 UTC: BT9 done (report `results/BT9/report.md`): trend following has no edge gross or net, DEV or VAL
  (best block-bootstrap t +0.71 DEV / +0.13 VAL). The one documented FX edge does not show in 2021–2025 data. Slow trend state stays
  a veto only. Remaining open question: RECON (is BT6b's +0.2R real or one rule reading?).
- 2026-09-29 23:00 UTC: RECON done (`results/RECON/report.md`). The A/B gap = a BT6a bug (REF path skipped the bias filter, ERRORS #25)
  + two unfaithful readings. Canonical REF frozen = BT6b + literal OB zone: DEV 150 / gross +0.262 / net +0.196R; VAL 88 / +0.180 /
  +0.113R. 448 faithful rule readings: 94% net positive (median +0.34R). Edge needs BOTH bias alignment and discount/premium.
  Weak statistically (t≈1; without top-5 winners ≈ 0). Next: BT8 ML on REF candidates; live signal scanner + demo forward test of REF.
- 2026-09-29 23:05 UTC: started BT8 ML filter on REF candidates (Opus) and the live REF status scanner with a bar-by-bar parity test (Opus, read-only).
- 2026-09-29: BT8 done (`results/BT8/report.md`): no ML filter now; REF hand rules stay. ML runs in shadow on live candidates and is switched on only if its guard passes.
- 2026-09-29: user asked for far more data for the ML. Decision: not other people's trades (different strategy, no features,
  survivor bias) but more price history: Dukascopy 2005–2020 (new period "HIST", never seen by REF) × 25 instruments.
  Our 2021+ split is unchanged and TEST stays locked. Plan: DATA2 download + validation → BT13 frozen REF on HIST → BT8b ML.
- 2026-09-29: common.py dataset switch added (SMC_DATASET=duka → data_duka, cache_duka, 25 instruments, period HIST; default unchanged, test_common passes). BT13 brief written with a pre-registered pass/fail rule (`bt/BT13_BRIEF.md`). DATA2 download running (Sonnet).
- 2026-09-29 23:48 UTC: live REF scanner done (`smc_bot/live/`): parity 153/153 signals identical to canonical REF (DEV 2023 + VAL H1 2024), MT5 bars == backtest bars. Running read-only under start_scanner.bat (logs/scanner/, results/SCANNER/status_log.csv, signals.csv). Started the demo executor build (Opus, ref_trader.py, magic 770077, dry-run first, mock-broker tests).
- 2026-09-30: user: the ML must find WHY trades lose, look at the bigger picture, and be dynamic while the rules stay strict. BT8b scope expanded accordingly (`bt/BT8b_BRIEF.md`).
- 2026-09-30 00:08 UTC: Dukascopy per-day download throttled to a crawl (HTTP 503, stalled at ~630 of 159k day-files). DATA2 switched: bulk M1 bid prices from HistData.com (yearly zips, ~500 requests) + spread profile sampled from Dukascopy (pair × year × hour). Laptop power: sleep=never (AC+DC), hibernate=never (set by user).
- 2026-09-30 03:30 UTC: agents checked after the usage limit. BT6a report written from its files (superseded by RECON). The old-backtest audit was never finished and is closed as superseded (ERRORS #2). The demo-trader agent was resumed. DATA2: the HistData download finished (500 year-files); I fixed the time-zone conversion (New York local with DST, not fixed EST) and validated against Exness; the build is running. The best-data Dukascopy job runs detached.
- 2026-09-30 03:25 UTC: DATA2 done: 25 instruments built in data_duka/ (HistData M1, NY-local→UTC fixed, today's Zero spread profile). Validated vs Exness 2021–23: 0.2–0.6 pip median close diff (`results/DATA2/validation_vs_zero.txt`).
  **Demo forward test STARTED**: ref_trader.py --live on the Zero demo (magic 770077, $5 risk/trade, $20 daily loss stop, max 4 open), 31/31 tests pass, backtest twin + shadow ML logging (`results/FORWARD/`). Start/stop: start_ref_trader.bat --live / stop_ref_trader.ps1.
  BT13 started (Opus): frozen REF on HIST 2005–2020, 25 instruments, pre-registered rule.
- 2026-09-30 05:10 UTC: **BT13 FAILED** (`results/BT13/report.md`): frozen REF on 16 unseen years = net +0.036R/trade, t 0.46; 15 crosses negative. REF = no demonstrated edge; the demo trader continues only as a plumbing check ($5 risk). Fixed the bt6b cache-key hazard (ERRORS #29). BT13 delivered 69k labelled broad candidates with path info → BT8b next.
- 2026-09-30 05:15 UTC: BT8b started (Opus): loss autopsy + bigger-picture features + dynamic decisions on 69k candidates 2005–2024, yearly walk-forward, adoption bar t ≥ 2.5 on HIST-OOS.
- 2026-09-30: **BT8b done: nothing passes** (`results/BT8b/report.md`). With 13× data, bigger-picture features and dynamic exits, ML cannot beat REF out of sample; REF itself has no edge (BT13). The loss autopsy says the entry carries no information (gross ≈ 0 under every exit).
  Pre-registered for NEW data only (demo forward log / TEST): H1 skip limits ≥ 1.5 M5-ATR from the CHoCH close; H2 trade only with 5-day currency strength. Pass = net > REF by ≥ 0.05R with t ≥ 2.5.
  **Decision point for the user:** the SMC program (BT1–BT13, BT8/8b) found no tradable edge. The demo trader continues as a plumbing check only.
- 2026-09-30: user asked for plain-language reports like their 'Backtest 80' JSON. Added `tools/plain_report.py` (any trades CSV → *_READABLE.csv with plain column names, *_SUMMARY.json/md: wins, losses, win rate, PF, balance, drawdown, streaks, by exit/direction/pair/session/year/month/weekday/hour; money = fixed $ risk per trade like the bot) and `GLOSSARY.md` (every term in plain words). Generated for REF DEV, VAL, HIST. Use it for every future backtest.
- 2026-10-03: **user chose OPTION B**: the locked TEST period (2025-07 →) stays sealed; research a new non-SMC idea with
  the same DEV/VAL/HIST method. Option A (spend TEST on REF + H1 + H2) is declined — BT13 already gave REF a far harder
  exam (16 years, ~13× the trades) than TEST's ~60 trades could.
- 2026-10-03: **forward test had taken 0 trades in 3 days.** Both signals (XAUUSD SELL 10-01 and 10-02) were correctly
  refused on sizing: XAUUSD 0.01 lot (1 oz) risks $1 per $1 of stop, so 106 and 238 pip stops force $10.61 and $23.78
  of risk, over the old `min_lot_risk_mult` 2.0 ceiling ($10). User's choice: keep `risk_usd` $5 (FX unchanged) and
  raise `min_lot_risk_mult` to **5.0** ($25 ceiling) so forced-min-lot gold is accepted. `daily_loss_usd` stays $20,
  so one max gold loss now ends that UTC day — flagged to the user. Trader restarted 16:27 UTC by the watchdog.
  Plumbing verified so far: signals detected 3–5 s after the bar, sized, logged, twin recorded. No fills yet.
- 2026-10-03: user asked about trading **Deriv's Volatility 150 (1s)**. Assessed and rejected: Deriv synthetics are a
  certified RNG running zero-drift geometric Brownian motion, so there is no order flow for SMC concepts to read and no
  edge is *possible*, not merely absent. Confirmed by simulation (20M ticks, 150% annualised vol, non-overlapping
  trades): random entry, sweep+reversal, momentum and fade all land on the 1/(1+RR) = 25% break-even win rate and
  ~0.00R gross; costs make each negative. The run also reproduced our own failure modes — a lookahead bug gave a fake
  "+0.199R, t +8.36" and overlapping trades gave "+0.041R, t +1.81" on data built from pure noise.
- 2026-10-03: **BT14 started** (Opus, `bt/BT14_BRIEF.md`): placebo calibration of our own method. Runs canonical REF over
  200 realizations of zero-drift GBM matched to real per-instrument/per-hour volatility, with the real calendar and real
  costs, and measures (a) the single-config false-positive distribution and (b) the best-of-search distribution over the
  same breadth we actually searched (RECON's 448 faithful readings). Pre-registered: **P95 of (b) is the acceptance bar**
  for every Option B candidate; if REF's +0.196R < P95, BT6 is formally closed as a false positive. Two blocking sanity
  checks: random entries must give ≈1/(1+RR) and ≈−cost, and the pipeline must reproduce canonical REF on real data first.
- 2026-10-03: Option B candidate research done → `research_notes/OPTIONB_candidates.md`. Ranked: (1) month-end
  equity-hedging flow at the London 4pm fix (Melvin & Prins, J. Fin. Markets 2015), (2) fix-window reversal = the old
  BT11, conditional (Krohn, Mueller & Whelan, JF 2024: ~2 bp, t 4–12, but their own cost section turns it negative at
  full spreads), (3) EURUSD time-of-day drift (Breedon & Ranaldo, JMCB 2013, survived costs only on EURUSD, 1997–2007).
  Agent's honest read: the effects look real but ~3× too small for our `gross ≥ 2× cost` rule, consistent with Neely,
  Weller & Ulrich (JFQA 2009). **Several citations are UNVERIFIED (403 / abstract only) and must be read from the PDFs
  before anything is pre-registered** — notably Krohn's exact windows and its post-2015 subsample (the 2015 fix change
  may have killed the effect) and the Melvin–Prins effect size. BT12 (extension fade) dropped: no external mechanism,
  its reversal already measured below cost in BT4, filters came from peeking at DEV. Also rejected: weekly currency
  momentum, carry, cross-rate lead-lag, gold seasonality.
- 2026-10-03: **BT14 done: the acceptance bar is measured** (`results/BT14/report.md`). The frozen canonical REF was run
  through the real harness over 24 synthetic zero-edge worlds (zero-drift GBM, per-hour volatility from real DEV, M1 wicks from
  20 sub-minute steps, real calendar, real spread/commission/slippage; REF setups form at 1.0-1.25x the real rate). Both blocking
  checks passed: random entries give win rate = 1/(1+RR) and net R = -(cost); the same code on REAL data reproduces canonical REF
  exactly (150 trades, gross +0.262, net +0.196, t 1.07). Mean GROSS R over noise worlds = +0.037 (z +1.21) -> no lookahead.
  (a) The frozen config alone on data with no edge: net R mean -0.028, sd 0.148; 8% of worlds beat +0.196R.
  (b) Best of the 49 faithful rule readings (RECON's 48-cell major grid + canonical) in each world: mean +0.168,
  **P95 = +0.350R, P99 = +0.484R**; 42% of noise worlds beat +0.196R and 33% reproduce the BT6 headline (net >= +0.196R and t >= 1).
  **Pre-registered verdict: +0.196R < P95 -> BT6 is formally closed as a false positive.** 'London beat New York' happens in
  46% of worlds with no London effect in the data. Trade overlap is only 6%, so block-bootstrap t ~= naive t: the
  variant search, not trade correlation, is what inflated BT6.
  ERRORS #31-#33. `common.py` gained `SMC_DATASET=gbm` (default unchanged, test_common passes).
- 2026-10-05: **BT14 FINAL — the entry above was written at 24 worlds before the agent hit a usage limit; the run finished
  200 worlds and `bt14_report.py` was re-run over all of them. These numbers supersede it:**
  (a) frozen config alone: mean **-0.082R**, sd 0.160, P95 +0.187; **4.0%** of zero-edge worlds beat +0.196R.
  (b) best of the 49 faithful readings: mean **+0.175R**, median +0.181, **P95 = +0.472R** (bootstrap 95% CI 0.393-0.522),
  P99 +0.594, max +0.836; **42.0%** of worlds beat +0.196R and **31.5%** reproduce the full BT6 headline (net >= +0.196R AND t >= 1).
  'London beats New York' — BT6's single pre-registered hypothesis — happens in **50%** of worlds containing no London effect,
  i.e. it was a coin flip. Trade overlap 7%, week-block t sd 1.18 vs naive 1.17, so the variant search alone inflated BT6.
  Verdict unchanged and stronger: **+0.196R is below P95, so BT6 is formally closed as a false positive.**
  **ACCEPTANCE BAR for every Option-B candidate (use this number, not the +0.35R above): a DEV result below ~+0.47R per
  trade (= $+7.57 per trade at 0.10 lot; $+2.36 per trade at the demo's $5 risk) is inside what our own method invents from
  nothing.** The bar is specific to this search shape: pre-register the rules AND the variant count before the next
  backtest, and re-run BT14 for that shape if the count changes.
- 2026-10-05: **BT15 done — the SNIPER_SPARK basket robot is arithmetically dead** (`results/BT15/report.md`,
  brief `bt/BT15_BRIEF.md`, scripts `bt/bt15_{lib,run,tests,report}.py`). The basket = one 0.10-lot gold trade,
  target 0.7 / stop 7.0 of gold price ($7 / $70), with the robot's own mechanics, run through `bt/common.simulate`
  over **380,378 baskets, 2009-03 to 2025-06**, on both feeds (duka = HIST, Zero = DEV/VAL). TEST untouched: the
  latest minute used anywhere is 2025-06-27 01:28.
  **HEADLINE (a measurement, not a search, so no multiple-comparison penalty): X\* = 62.3%.** The secret entry must
  pick the better direction **62.3 times in 100** (a coin flip is 50) — equivalently close **91.67%** of baskets green
  where a coin flip already closes **88.84%** green. Stable everywhere: HIST 62.1 / DEV 62.6 / VAL 62.7, every year
  59-64, both eras 62.1/62.2, every session 58.6-64.2. **Across all 30 target/stop shapes X\* = 54.2%-76.4%** and it
  is driven by **cost ÷ target**, barely at all by the 1:10 ratio — the shipped 70-cent target eats
  $1.62 of cost = 23% of the reward, so even the most forgiving cell ($20 target / $50 stop) still needs 54.2%.
  **The four pre-registered readings of "M15 trend + M1 pullback" scored 49.6%-51.2% accuracy — a coin flip — and lost
  money in all 12 period-rule cells** (-$0.64 to -$2.69 per basket). Coin-flip floor -$1.89/basket; a typical day
  -$27 at 14 baskets/day with **47% green days and an 88.9% basket win rate**, worst day -$476, worst losing run
  16 days, **$350 gone in ~11 trading days** (36 days at 5 baskets/day). Daily target/loss limits shrink the loss in
  proportion to the trading and never change its sign. **Pre-registered verdict: X\* is far above anything this
  project has ever measured from a directional rule (BT4, BT5, BT9, BT13, BT8b all ~0), so the robot cannot work as
  sold — do not run it with real money.**
  Sanity checks all pass: oracle +$5.86/basket at 99.8% win; coin flip -$1.98 vs $1.62 of named cost (residual
  -$0.36 = 0.5% of the stop, same on both feeds, conservative, decomposed in §3); independent from-scratch minute
  replay 160/160 baskets exact. **I hit the BT14 lookahead trap myself and caught it** — the readings originally
  filled at the open of their own trigger bar and reported +$0.55/+$2.17/+$0.28 at t +5.7/+19.5/+3.9 with the
  mirror-image rule at -$2.95, t -20; fixed to fill at bar k+1. ERRORS #34-#37 (gold spread column is ~0 before 2026
  so gold backtests have effectively assumed zero spread — BT15 charges the measured 0.05; TEST-lock disclosure;
  the lookahead bug; and `simulate`'s no-TP-on-the-fill-bar rule being a 15.5% one-sided pessimism for tiny targets).
  No shared code was changed.
- 2026-10-05: **BT15 addendum — the time stop the user proposed was measured, and it does not work**
  (`results/BT15/addendum_timestop.md`, `bt/bt15_timestop.py`). His premise was right (median win 7 min, median loss
  89 min; 80.7% of losses still open at 20 min against 32% of wins), so it deserved a test rather than an argument.
  Clean A/B on the same entry chances, same costs, only the exit rule changed: time stop 5/10/15/20/30/45/60/120/240
  minutes plus none, crossed with $20/$35/$70 dollar stops, read at 50/55/60% entry accuracy = **90 cells, 0
  positive**. Coin-flip money per basket stays in a **10-cent band (-$1.81 to -$1.91)** across the entire sweep,
  while the win rate moves from **88.8% to 45.0%** and the average loss from **-$64 to -$7** — the two cancel to the
  cent. Day money gets slightly **worse** with a short clock (more baskets, more cost). A short time stop also
  **raises** the required accuracy from 62.3% to 67.2%. Reason: of everything still open at 20 minutes **76% still
  goes on to win**, and the mark-to-market you bank is fair value — optional stopping, the same answer BT8b got for
  dynamic exits. The addendum also carries a **representative** 18-basket sequence with a running balance (hold-time
  quartiles 2/10/46 min against the sample's 2/10/45, 2 losses in 18 = the true 11.1% rate; $350 -> $301.80 in one
  day), replacing an earlier 12-trade sample that was unrepresentative and had confused the user. ERRORS #38.
- 2026-10-05: **BT15 addendum 2 — tighter stops, bigger targets, no profit cap: all fail, and the floor is 50%**
  (`results/BT15/addendum_depth.md`, `bt/bt15_depth.py`). The user asked why the win is capped at $7 while the loss
  runs to $70, where the stop should really go, and what happens outside the first grid. 30 pre-declared variants
  (16 tight-stop + 12 big-target + 2 let-it-run), same entry chances, 380,378 baskets each.
  **(a) Depth:** of baskets that eventually WIN, half dip no more than **$6.84** into loss and three quarters no more
  than **$16.90**, but one in ten dips past **$34**, one in a hundred past **$65**, and the deepest recovery came back
  from **$240**. So **cutting at $20 kills 21% of the winners** (at $10, 39%; at $5, 59%), and every cut point,
  actually simulated, is still a loss — best is a **$10 stop at -$1.82** against the shipped -$1.91. Holding the $7
  target and moving the stop from $25 to $10 moves the money by five cents a basket while the win rate falls from
  75% to 55%: **the stop is not the problem.**
  **(b) The $7 cap costs nothing.** A losing basket cannot have passed +$7 (it would have closed green), and removing
  the target altogether gives +$0.135/basket at **week-block t +0.11** — zero — while holding **1.09 nights per
  basket** that the harness charges no swap for (ERRORS #39; BT9: gold swap negative both sides). The
  break-even-at-+$7 variant is -$1.17.
  **(c) 0 of 28 cells positive on a coin flip**, and the key structural finding: **required accuracy falls only with
  the TARGET (62.3% at $7, 58.5% at $10, 55.7% at $15, 53.0% at $30, 51.5% at $100, 50.6% at $200 — tracking cost as a
  share of target) and is flat in the stop (62.0-62.2% across $10-$25). It approaches 50% from above and can never go
  below, because before costs a coin flip is exactly break-even at any target/stop pair.** The single lever that has
  ever moved the requirement is target size relative to cost, and its best case is a coin flip.
  Also added a hold-time histogram (23% of baskets finish inside 2 minutes, 50% inside 10, 13% run past 2 hours;
  middle basket 10 min, middle winner 7 min, average 61 min) to settle the user's confusion about the quoted median.
  TEST still untouched: latest minute reached by either addendum is 2025-06-27 20:45 (the Friday flat).

### BT16 (2026-10-05) - THE ENTRY. 44 pre-registered readings. Status: DONE, nothing found; one post-hoc lead.
Pre-registered in `bt/BT16_BRIEF.md` before any code was written. Money shape = the user's own: **$15 profit cap /
$20 loss cap at 0.10 lots** (1.5 / 2.0 gold price), **no commission**, his measured **0.072** spread. Break-even
re-measured on both feeds: **52.85 right calls in 100**; a coin flip pays **-$0.90 a basket**. 190,189 entry chances,
2009-03-15 -> 2025-06-26, every 30th minute, both sides simulated through `common.simulate`. **TEST untouched**:
latest minute reached anywhere in BT16 is 2025-06-26 23:30.
**Ranked result (all 44, `results/BT16/ranked_all_readings.csv`):** dollar strength best **50.52**; news events best
**57.47** on 195 chances and killed by a post-hoc control (the same rule is 49.0 / 49.7 on 190k chances);
M1/M5/M15 classic rules best **52.77** (RSI-fade on M1) still losing $0.02 a basket. Block-rotation placebo P95
(best-of-26 grid readings) = **51.30 / 51.89** - the best real reading does not clear it. **No reading clears 52.9 in
all three date periods with a usable sample.** (ERRORS #40)
**Two things worth keeping.** (i) Gold vs the dollar: contemporaneous correlation **-0.42 to -0.56** everywhere
(mechanism confirmed), lead-lag vs the next day **0.00** on all 8 cells - the idea is closed cleanly (ERRORS #41).
(ii) **Data problem**: the Zero feed jumps **+$6.61 per 0.10 lot across every daily rollover** and Dukascopy does
not - cross-check every overnight gold result on duka (ERRORS #42).
**The one lead, labelled post-hoc:** fade rules in the **20:00-21:00 UTC** hour, 67.6 / 68.2 / 53.7 right in 100
across the three periods, confirmed on the other feed - but decaying, hour-selected, and its money dies if the real
late-session spread is above ~0.30. **Next action: measure his spread at 20:00-21:30 UTC for two weeks before
anything else** (ERRORS #43).
