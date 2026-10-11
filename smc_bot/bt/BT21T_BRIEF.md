# BT21-T — does raising the signal timeframe to M5 help? Pre-registered.

**Written and saved BEFORE any BT21-T code was written or run (2026-10-06, after BT21E/R/V reported).**
The variant count is **FIXED at 6** (T1-T6) and may not grow. Anything added after results are seen must
be labelled "post-hoc, added after seeing results".

Read first: `bt/BT21_BRIEF.md`, `results/BT21_SUMMARY.md`, `results/BT21E/report.md` (T1-T6 are its
M5 twins and must be compared to it variant by variant), `bt/bt21e_lib.py`, `bt/bt20_lib.py`,
`bt/AGENT_BRIEF.md`, `results/BT16/report.md` (the M1/M5/M15 ladder on 7 classic rules),
`results/BT17/report.md` (the M1-vs-M5 SMC pairs).

## The question the owner asked

"Will a higher timeframe like the five minutes yield more edge and take more trades?"

Two separate claims, and the existing evidence points the other way on both:
- **Edge:** 13 same-rule M1-vs-M5-vs-M15 comparisons (BT16 R1-R7, BT17 B/S/O/F pairs) differ by an average
  of ~0.25 of a point, in both directions. The project's best reading anywhere (`B1_bos_M1_MIRROR`, 51.45)
  is on M1. The two fade rules got *worse* going up (RSI 52.77 -> 51.64 -> 50.16).
- **Trade count:** the sniper is throughput-limited, not signal-limited. BT21E measured E3 converting
  335,540 signals into 13,133 baskets (4%) while E6, with 13x fewer signals, still took 8,748 (38%). A 13x
  signal cut produced a 33% trade cut. M5 gives ~5x fewer signals, so it should cut trades ~15-25%, not
  raise them.

BT21-T tests it directly rather than arguing it, on the owner's own setup.

## The 6 variants — FIXED

The trigger moves from M1 to **M5**; everything else matches BT21E so the delta is attributable.

| id | BT21E twin | rule |
|---|---|---|
| T1 | E1 | M15 close > EMA50 · **M5** low <= EMA9 · M5 close > open |
| T2 | E2 | M15 close > EMA50 · **M5** low <= EMA21 · M5 close > open |
| T3 | (new rung) | H1 close > EMA50 · **M5** low <= EMA9 · M5 close > open |
| T4 | (new rung) | H1 close > EMA50 · **M5** low <= EMA21 · M5 close > open |
| T5 | E5 | **M5** sweep: low < min(low, prior 20 M5 bars) − buffer, close back inside the range → fade |
| T6 | E6 | same, 60 M5-bar lookback |

SELL is the mirror of each. T1/T2 raise only the trigger (E1/E2 used an M5 filter + M1 trigger; T1/T2 use
an M15 filter + M5 trigger, i.e. the same 3x ratio one rung up). T3/T4 add the next rung so the ladder has
three points, not two.

## Method — identical to BT21E, no substitutions

- **Reuse `bt20_lib.walk()`** for first-passage and `bt20_run.engine()` for the one-basket-at-a-time replay.
  Do not write a new simulator. Count a tie as a half (BT15/BT17/BT21 convention), **not** the long side —
  see `results/BT21_SUMMARY.md` open issue 4.
- **Causal only.** Trend from the last CLOSED M15/H1 bar, trigger from the last CLOSED M5 bar, **fill at the
  first M1 open after that M5 bar closes** (the bot acts on minutes, not on M5 bars). Run the lookahead
  twin and report it: BT21E's twin was worth +7.5 points on the M1 version, and a 5-minute signal bar is a
  more violent bar, so by BT21R's finding the twin should be *larger* here. Confirm or refute that.
- **Judge against each reading's own luck bar** (`coinflip_same_minutes`), never 50.
- **Three eras, two feeds:** duka 2009-2020, zero 2021-2023, zero 2024-2026. Never pooled.
- Primary shape **$15 / $70** at 0.10 lots; also $7/$70 and the full 36-shape ladder. Costs $1.10
  round-trip commission, 0.05 extra spread.
- Placebo bar: whole-week rotation, best-of-6, 200 draws.
- Pass/fail is BT21_BRIEF's four clauses, applied literally.

## The three numbers this run exists to produce

1. **The M1 -> M5 accuracy delta, variant by variant**, against BT21E's published readings, with the
   standard error beside it so we can say whether any movement is real.
2. **The M1 -> M5 trade-count delta** — signals AND baskets taken, per era. Does cutting signals ~5x cut
   baskets by the ~15-25% predicted from throughput saturation, or by more?
3. **The cost-amortization test.** Over the 36-shape ladder, does the M5 trigger benefit *more* from a wider
   target than the M1 trigger does? i.e. is `best_shape(M5) − best_shape(M1)` larger at $50-$200 targets
   than at $7-$15? This is the one mechanism by which a higher timeframe genuinely should help (fixed cost
   amortized over a bigger move), and it is the hypothesis behind the owner's question. Report it
   explicitly even though the headline verdict will almost certainly be a fail.

## Deliverables
`bt/bt21t_lib.py`, `bt21t_run.py`, `bt21t_report.py` and `results/BT21T/report.md` — 10-line summary at the
top, the three delta tables above, the full ranked table (6 variants x 3 eras, both signs, luck bar beside
each), the placebo bar, the lookahead twin, 10 example trades a human can check on a chart, sanity checks,
any shared-code bug (file:line + proof), and a plain-language verdict that answers the owner's question in
two sentences: does M5 give more edge, and does it give more trades.
