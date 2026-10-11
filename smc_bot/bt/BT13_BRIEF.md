# BT13: frozen canonical REF on never-seen history (HIST 2005–2020, Dukascopy)

Read `AGENT_BRIEF.md` (hard rules), `results/RECON/report.md` (canonical REF definition) and `results/DATA2/report.md` (data
validation). Run everything with `SMC_DATASET=duka` (see `common.py`): data `../data_duka`, bar cache `cache_duka/`, 25 instruments,
periods HIST 2005-01-01 → 2021-01-01, DEV, VAL (TEST stays locked and is never generated).

## Question
Is the REF edge real? On DEV+VAL it is +0.2R / +0.11R net with t ≈ 1 (about 240 trades). HIST should yield ≈ 13× more trades.
REF is **frozen**. Do not change any rule, parameter or filter. This is a confirmation test, not a search.

## Pre-registered decision rule (fixed before running)
- **Primary:** pooled HIST net avg R on the ORIGINAL 9 traded instruments (EURGBP excluded as in REF), block-bootstrap by week.
  Edge confirmed if net > 0 with t ≥ 2.5 AND ≥ 2 of the 3 eras net > 0 AND ≥ 6/9 instruments gross > 0.
- **Secondary:** the 15 new crosses (never seen at all), same rule with ≥ 9/15 instruments.
- Report GROSS first (before commission and slippage), then net. Costs on duka = real Dukascopy spread + Zero commission
  (conservative; older years have wider spreads), so also report net using only a constant spread = the instrument's 2021–23
  median, to separate "edge died" from "costs were higher back then".

## Required output (`results/BT13/`)
- Per era (2005–08, 2009–14, 2015–20), per instrument, per year: trades, win%, gross R, net R, PF, t, maxDD R, $ per 0.10 lot.
- The same REF run on duka DEV+VAL (2021-01 → 2025-06), compared trade by trade with the Zero-data canonical REF trades
  (`results/RECON/trades_CANON_*.csv`): overlap %, R correlation. This proves the duka data gives the same setups.
- London vs NY, long vs short, top-5-winners-removed sensitivity, and each instrument removed in turn.
- Every trade CSV with the full timestamps (zone, touch, CHoCH, order, fill, exit), SL/TP and reasons, and the feature columns
  that BT8 used (for BT8b).
- 10 example trades from HIST, and 3 replayed by hand on M1.
- A blunt verdict against the pre-registered rule.

## Also produce for BT8b
Broad candidate sets (bias and discount/premium as features, not gates) on HIST for all 25 instruments with BT8's feature
columns and independent labels, reusing `bt/bt8_ml.py` functions (import them; do not edit that file).
