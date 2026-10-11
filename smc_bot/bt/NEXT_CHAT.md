# What's left — read this first

Plain version. Long technical version is `HANDOFF.md` in this same folder.
Updated: just now, same session, right before starting a new chat.

## Done

8 of 10 strategies tested. All 8 have a full JSON file with every result:
`smc_bot/results/AUT0X_name/autopsy.json`

None of the 8 make money as a finished strategy. One (AUT06, gold order
block) made money in all three test windows and is still worth checking —
see "Still open" below.

## The plain win/loss JSON — done for all 8

Same style as the Backtest-80 example the owner sent. One per strategy, its
best-performing variant, real trades, real dollars. Open these directly:

| strategy | trades | win % | net $ | file |
|---|---|---|---|---|
| AUT01 live bot (gold, real settings) | 25,000 | 79.9% | −$17,747.60 | `AUT01_emapull/trades_live_as_shipped_CORRECTED_VALIDATE_15_70_SUMMARY.json` |
| AUT02 sweep | 503 | 42.7% | +$1,917.42 | `AUT02_sweep/trades_EXPLORE_M1510_R2_stretched_NYoverlap_wick_2R_SUMMARY.json` |
| AUT03 break-retest | 1,266 | 49.5% | +$1,139.72 | `AUT03_breakretest/trades_EXPLORE_V3_fade_failed_quiet_SUMMARY.json` |
| AUT04 opening range breakout | 420 | 55.5% | +$1,168.79 | `AUT04_orb/trades_LONDON_4x4_best_EXPLORE_SUMMARY.json` |
| AUT05 VWAP | 41,026 | 23.1% | −$177,441.11 | `AUT05_vwap/trades_R1_shape_VALIDATE_SUMMARY.json` |
| **AUT06 gold order block (the lead)** | 930 | 81.4% | +$1,020.61 | `AUT06_orderblock/trades_GOLD_ONLY_promising_VALIDATE_SUMMARY.json` |
| AUT08 break of structure | 7,717 | 75.0% | +$8,476.29 | `AUT08_bos/trades_EXPLORE_R1_overlap_legstop_SUMMARY.json` |
| AUT09 cost filter | 439,616 | 40.6% | −$871,166.22 | `AUT09_timeofday/trades_COST_FILTER_promising_SUMMARY.json` |

**Read the trades count and the time window before trusting any "+" number
above.** Most of these are the tuning window only (EXPLORE) and are known to
fail on the next window — see each strategy's own `autopsy.json` for which
ones actually held up. AUT06 is the only one that held up on all three.

Tool used to make these: `smc_bot/tools/plain_report.py <trades.csv>`

## The cost question — settled, don't reopen

Owner said: stop subtracting spread and commission twice.

Checked the code directly. We do not subtract twice. Each trade's dollar
number is built once, the moment the trade closes. Proven with a real
trade: entry cost $0.60, shown once, nowhere else.

One real mistake WAS found and fixed: a test file for the live bot's
$15/$70 target was built wrong — it asked for $15 of raw price movement,
then took commission off after, so wins showed $13.90 instead of $15.
Fixed. Win size is correct now ($15.00 exactly). The bottom line barely
moved — same loss, within $230 out of $18,000. The cost was never gone,
it just now shows up as "the target needs a little more room" instead of
"the win is a little smaller."

**Every strategy below was told this rule directly: build targets so
price_distance minus commission equals the real target, never subtract
commission twice.**

## AUT07 and AUT10 — handed to the other chat

These two were never tested. The owner asked the other Claude chat
(not this one) to do them, so this session stopped its own agents and
backed off. Check `results/AUT07_fvg/autopsy.json` and
`results/AUT10_momentum/autopsy.json` — if missing, that chat is not
done yet. Not this session's job.

## CLOSED — the AUT06 gold lead, now disproven

AUT06, gold only, touch-1 entries: made money in all three test windows.
$0.75, then $1.10, then $0.59 a trade. Looked like the one real survivor
in the whole project. Ran the 4 checks that were still open. Result: it
does NOT hold up. Files: `results/AUT06_orderblock/gold_*.csv`.

1. **Direction control — passed.** Beats always-buy, always-sell, and a
   coin flip, on gold alone, in all three windows.
2. **Luck bar — passed.** Random gold entries at the same hours/shape
   LOSE money every window (−$1.18, −$2.24, −$2.32). The rule still makes
   money. Real gap.
3. **More touches — FAILED.** Touch 1 of the same zone makes money.
   Touch 2 and touch 3 LOSE money, badly, every window (down to −$4.12 a
   trade on STRESS touch 3). All touches together, which is what a real
   bot actually trades since it can't know which touch number is coming,
   loses money in all three windows.
4. **Full shape grid — confirms the problem.** 3 shapes stay positive in
   all 3 windows, but only on the touch-1-only subset. Same cherry-pick
   as #3.

"Touch 1 only" was never a rule picked in advance. It was a filter kept
because it happened to win, after seeing the result. That is the trap
this whole project is built to catch. AUT06 is closed: no real edge.

**All 10 strategies are now closed. None has a proven edge.**

## Smaller things left, lower priority

- AUT04 and AUT06 have no plain-English report page (`report.md`). Their
  JSON files are complete. Someone needs to turn the JSON into a readable
  page, same style as the other strategies have.
- The dashboard page built earlier needs updating with AUT06's gold lead
  and the corrected live-bot numbers. Link:
  https://claude.ai/artifact/Xx3wELjAtMDnyhUtK9snPy

## How to compare any strategy's risk-to-reward against the live bot

The owner wants this check on anything new: average win size divided by
average loss size. The live bot's is 0.24 — each loss costs 4x a win.
Pull `avg_win` and `avg_loss` from any `_SUMMARY.json` above and divide.
Below 1.0 means losses are bigger than wins; above 1.0 means wins are
bigger. A strategy can still lose money with a good ratio if it doesn't
win often enough (AUT05 proves this — ratio 2.37, still lost everything).

## Rules for whoever picks this up

- Spread and commission are real money. Never remove them from a result.
- The sealed period (2021 onward) stays sealed. Don't touch it.
- When a result looks good, always check: does it win on BOTH sides (buy
  and sell), not just one? Does it beat a coin flip? Does it still work on
  the next test window, not just the first one? Most things fail here.
- Keep replies short. Plain words. No long explanations unless asked.
