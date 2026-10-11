# BT14 — Placebo calibration of our own research method

## The question
Every backtest we run reports a number. **How big does that number have to be before it means anything?**

BT14 answers it by running our real research pipeline over data that is *known* to contain exactly zero edge,
many times, and recording what it reports. Whatever it reports is error manufactured by the method itself.

This is not a test of a strategy. It is a test of the testing. The thermometer goes in the ice water.

## Why now
- BT6 reported canonical REF = DEV 150 trades, gross +0.262R, **net +0.196R** (t ≈ 1.1).
- BT13 ran that same frozen REF on 16 unseen years (2005–2020, ~13x the trades) → net **+0.036R, t 0.46**.
- BT8b found no ML rescue (0/13) and its autopsy showed the entry carries no information.
- So BT6's +0.196R is very probably method error. BT14 **measures that error** instead of inferring it.
- Under Option B (chosen 2026-10-03: keep TEST sealed, research a new non-SMC idea) this number becomes the
  **acceptance bar** for every future candidate. Without it we have no way to judge the next DEV result either.

## Pre-registered pass/fail (write this down BEFORE looking at any output)
Let `P95` = the 95th percentile of best-variant net R under pure noise (measurement (b) below).

| Outcome | Reading |
|---|---|
| REF's +0.196R **< P95** | The DEV result is fully explained by noise + variant search. BT6 is formally closed as a false positive. |
| REF's +0.196R **between P95 and P99** | Borderline; consistent with noise but not proven to be noise. |
| REF's +0.196R **> P99** | Noise + search does *not* explain BT6; the BT13 failure needs another explanation (regime change, data differences). Investigate, do not celebrate. |

**`P95` is the deliverable**, whatever it turns out to be. It is the bar any future DEV result must clear.

## Design

### 1. Generate the synthetic data
Per instrument, from the real `data_zero` 2021–2025 series:
- Estimate the per-minute log-return standard deviation (per instrument, and per hour-of-day so the intraday
  volatility shape is preserved — session structure matters to REF's killzone logic).
- Generate **zero-drift** GBM at that volatility. Zero drift exactly — document it and assert it in a test.
- **Build M1 OHLC from sub-minute steps** (e.g. 20 steps per minute) so highs and lows are realistic.
  This is not optional: SMC detection reads wicks. Close-to-close bars will not form sweeps or OBs at
  realistic rates and the whole calibration would be meaningless.
- Reuse the **real calendar**: same trading sessions, same weekend gaps, same holiday closures as the real data,
  so `bt/common.py`'s weekend/holiday logic behaves identically.
- Reuse the **real cost model**: measured spread profile, real commission, 0.2 pip stop slippage. Unchanged.
- Window length = one DEV (3 years), 9–10 instruments, matching the real DEV shape.
- Add a dataset switch the same way `SMC_DATASET=duka` was added (e.g. `SMC_DATASET=gbm`), default unchanged,
  and `bt/test_common.py` must still pass.

### 2. Run the real pipeline, unchanged
Run the **exact canonical REF** (the frozen RECON config: BT6b + literal OB zone) over each synthetic dataset
through `bt/common.py`. Do not re-tune anything. Do not special-case synthetic data anywhere in the pipeline.

### 3. Repeat
**N = 200** independent realizations, different RNG seed each, seed recorded per realization.

### 4. Record per realization
n trades, gross R, net R, t-stat, win rate, per-instrument sign, London vs NY split, max drawdown.

### 5. The two measurements
**(a) Single-config false-positive rate.** Distribution of net R for the frozen REF config alone.
Report: mean, sd, P50/P95/P99, and the fraction of noise worlds with net R ≥ +0.196.

**(b) Variant-search inflation — this is the important one.** In each noise world, run the same *breadth* of
search we actually ran in reality (RECON enumerated 448 faithful rule readings; use that set, or a documented
representative subset — state the count and justify it), then take the **best** result in that world.
Report the distribution of those best-of-N results. `P95` comes from here.

Measurement (b) is the honest one because it mirrors what we actually did: we did not test one config, we
looked at many and reported the one that looked best.

## Correctness requirements — these are the traps, get them right
1. **No lookahead.** Generate the entire series first, then run the causal pipeline over it. In a scratch test
   on 2026-10-03 I produced a fake "+0.199R, t +8.36 liquidity sweep edge" on pure noise purely from selecting
   entries with a candle 20 ticks in the future. It looked exactly like REF's real result.
2. **Overlapping trades inflate t-stats.** In the same scratch test, clustered overlapping entries gave
   "+0.041R, t +1.81" on noise. Count trades the way the real harness does and check independence; if trades
   overlap in time, say so and report a block-bootstrap t alongside the naive one.
3. **Setup-rate calibration.** The synthetic data must produce a similar *number* of REF setups per year as
   real data (within ~2x). If it is 10x off, the volatility or the bar construction is wrong — fix it and
   document the fix. A generator that produces no setups calibrates nothing.
4. **Sanity check 1 — the simulator.** Random entries on synthetic data must give win rate ≈ 1/(1+RR) and
   net R ≈ −(cost). If not, the generator or the trade simulator is wrong. Stop and fix before continuing.
5. **Sanity check 2 — the wiring.** Before trusting any synthetic result, run the pipeline on **real** data and
   confirm it reproduces canonical REF (DEV ≈ 150 trades, net ≈ +0.196R). If it does not reproduce the known
   answer, the harness is miswired and every synthetic number is garbage. This check goes in the report.
6. **TEST stays locked.** Do not read, load, or glance at any data from 2025-07 onward. Not for any purpose.

## Deliverables
- `results/BT14/report.md` — method, the two distributions, `P95` and `P99` stated plainly, the pre-registered
  verdict applied, both sanity checks shown, and every error hit (→ `ERRORS.md`).
- `results/BT14/realizations.csv` — one row per noise world, with its seed.
- `results/BT14/best_of_search.csv` — measurement (b), per world.
- `bt/bt14_*.py` — the generator and the runner.
- A plain-words summary suitable for `tools/plain_report.py` style: what the bar is, in R and in money per 0.10 lot.

## Operating notes
- The laptop is on battery and also running the USDJPY bot, the REF scanner, the REF demo trader and a large
  download. **Checkpoint after every realization** into `results/BT14/parts/` so the run resumes after a power
  loss or a usage limit, and keep memory use modest.
- Report in the project's plain-language style; terms in `GLOSSARY.md`.
