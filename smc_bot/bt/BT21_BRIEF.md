# BT21 — the owner's five chart setups, measured. Pre-registered.

**Written and saved BEFORE any BT21 code was written or run (2026-10-06).** The variant count is
**FIXED at 18** (three families of 6) and may not grow. Anything added after results are seen must be
labelled "post-hoc, added after seeing results" in the report.

Read first: `bt/AGENT_BRIEF.md` (hard rules), `bt/BT15_BRIEF.md` §6, `results/BT16/report.md`,
`results/BT17/report.md`, `results/BT18/report.md`, `research_notes/BT20_entries_x_shapes_plain.md`,
`research_notes/M1_entry_candidates.md`, `bt/bt20_lib.py`, `bt/bt15_lib.py`, `bt/common.py`,
`../sniper/strategy.py`.

## The five setups the owner named

1. **EMA pullback (trend continuation).** 5m or 15m trend filter (price above/below the 50 EMA). On M1,
   wait for a pullback to the 9/21 EMA, enter when a candle closes back in the trend direction.
2. **Liquidity sweep reversal.** Price wicks past a recent high/low, then closes back inside the range.
   Enter on that close, stop beyond the wick.
3. **Break and retest.** Price breaks a micro support/resistance, comes back, retests, holds. Enter on
   the rejection candle.
4. **Opening range breakout.** Mark the first 5–15 minutes of the London or NY open, trade the break
   **with a retest**.
5. **VWAP bounce/rejection.** In a trending session, pullback to VWAP with a confirmation candle.

## What was already tested, and the exact gap each family closes

| Setup | Prior coverage | Gap BT21 closes |
|---|---|---|
| 1 EMA pullback | BT15 §6 variant (a) (M15 EMA50/200 → M1 EMA20 pullback): 50.15 / 49.88 / 49.98 on 210k/53.6k/27.3k signals. BT16 R1_emacross M1/M5/M15: 49.2–49.8. It is also the sniper's live rule (`sniper/strategy.py`). | The owner's spec is **not** BT15 (a): his filter is *price vs a single 50 EMA* on M5 **or** M15, his pullback EMA is **9 or 21**, and he requires a **close back in the trend direction**. Run that exact spec. |
| 2 Liquidity sweep | BT1 (M15+), BT17 S1–S4 sweep-fade M1/M5, C1/C2 sweep+CHoCH. Best in the whole family < 51.5 vs a 52.9 break-even and a 51.64 placebo bar. | BT17's sweeps used SMC swing definitions and a fixed dollar stop. The owner's spec is a plain N-bar extreme and a **stop beyond the wick** — a structural stop, never tested in this project. |
| 3 Break and retest | BT17 O1/O2 (order-block retest), F1/F2 (FVG fill), B1–B4 (BOS). | A plain **horizontal micro S/R** break → retest → rejection-candle entry has never been run. |
| 4 ORB | `research/orb.py` (XAUUSD NY/LON, US30, USTEC, DE30; 5/15/30-min ranges; trained 2021-24, confirmed 2025-26) — immediate breakout only, no edge, and an Opus audit found + fixed entry-bar bias. BT18 first-5-minute-candle colour at session opens. BT20 COMEX 30-min momentum. | **No retest variant anywhere.** The owner specifically says "with a retest". Run break-and-retest ORB with immediate-break controls beside it. |
| 5 VWAP | **Never tested.** Only ranked #4 (weak, "test last") in `research_notes/M1_entry_candidates.md`. | The whole thing. |

## The 18 variants — FIXED

### Family E — EMA pullback + sweep (the owner's exact specs)
| id | rule |
|---|---|
| E1 | M5 close > EMA50 (trend up) · M1 low ≤ EMA9 · M1 close > open |
| E2 | M5 close > EMA50 · M1 low ≤ EMA21 · M1 close > open |
| E3 | M15 close > EMA50 · M1 low ≤ EMA9 · M1 close > open |
| E4 | M15 close > EMA50 · M1 low ≤ EMA21 · M1 close > open |
| E5 | sweep: M1 low < min(low, prior 20 bars) − buffer, close back **inside** the range → BUY |
| E6 | same, 60-bar lookback |
SELL is the mirror of each. Trend from the last **closed** M5/M15 bar only; trigger from the last
**closed** M1 bar; fill at the next M1 open.

### Family R — break and retest, and ORB with retest
| id | rule |
|---|---|
| R1 | M1 micro S/R (swing confirmed 5 bars) broken by ≥ buffer, retest within 30 bars, rejection candle closes back on the break side → follow |
| R2 | R1 faded |
| R3 | ORB London: range = 07:00–07:15 UTC; break, then retest of the broken edge that holds, enter on the rejection close |
| R4 | ORB New York: range = 13:30–13:45 UTC, otherwise as R3 |
| R5 | ORB London, **immediate** break (control — reproduces the known no-edge and isolates what the retest adds) |
| R6 | ORB New York, immediate break (control) |

### Family V — VWAP
| id | rule |
|---|---|
| V1 | Day-anchored VWAP (00:00 UTC), tick volume. Trend = close vs VWAP. Pullback touches VWAP, candle closes back on the trend side → follow |
| V2 | V1 faded |
| V3 | London-anchored VWAP (07:00 UTC), rule as V1 |
| V4 | NY-anchored VWAP (13:30 UTC), rule as V1 |
| V5 | VWAP ±1σ band (σ = running std of typical price around VWAP): close back inside the band → revert toward VWAP |
| V6 | VWAP ±2σ band, rule as V5 |

## Method — mandatory, no substitutions
- **Reuse `bt/bt20_lib.py`.** `walk()` gives first-passage times for every (target, stop) on the LADDER;
  one pass prices every shape. Do not write a new simulator. Each family builds its **own**
  `walk_<family>_<ds>.npz` in its own results folder (signal bars differ per family).
- **Causal only.** Decide on closed bars, fill at the **next** M1 open. BT17 §3 shows filling at the
  signal bar's own open manufactures a large fake edge. Assert it.
- **Judge against the setup's own luck bar, never against 50.** Report `coinflip_same_minutes`
  (random direction at the rule's own entry minutes). Rules that select volatile minutes face a bar of
  64–66, not 50 — this killed BT16's RSI fade and BT17's BOS fade.
- **Three eras, two feeds.** duka 2009-2020 · zero 2021-2023 · zero 2024-2026. A number pooled across
  all years hides exactly the decay BT18/BT20 found.
- **Money shapes.** The sniper's live shape (`.env`: **$15 target / $70 stop** at 0.10 lots) is primary;
  also report $7/$70 (the shipped shape) and the best shape on the ladder. Our costs: $1.10 round-trip
  commission, 0.05 extra spread. Gross first, then net.
- **Placebo bar.** Rotate each family's signals by whole weeks, take best-of-6, 200 draws → the accuracy
  and $/basket the method invents from nothing. Anything under that bar is a non-finding.
- **TEST discipline.** The gold work is judged by era, not by the DEV/VAL/TEST split, but **do not tune**:
  pick nothing from the 2024-2026 era.

## Pre-registered pass/fail — write this down before looking at output
A variant **passes** only if ALL of:
1. net $/basket > 0 at the primary shape after commission, in **all three eras**;
2. accuracy > its own `coinflip_same_minutes` bar in **all three eras**;
3. accuracy > the family's placebo bar;
4. ≥ 500 resolved baskets in **each** era.

Anything else is a **fail** and is reported as a fail. A variant that is positive in one era and not the
others has found nothing — that is the lesson of BT18's 52.55 and BT20's COMEX momentum.

## Deliverables (each family, in `results/BT21<F>/`)
`report.md` with a 10-line summary at the top, the full ranked table (all 6 variants × 3 eras, both
signs), the luck bar beside every reading, the placebo bar, the money at all three shapes, 10 example
trades a human can check on a chart, the sanity checks run (including the lookahead twin), any bug found
in shared code (file:line + proof), and a verdict in plain language.
