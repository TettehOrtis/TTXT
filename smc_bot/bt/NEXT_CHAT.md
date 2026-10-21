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

## Live bot on Oracle, and the time-stop test (Oct 16)

- Oracle trade log pulled with the key in `sniper_mql5/HANDOFF.md`. Oct 12-16:
  139 baskets, 74% win, net -$852, PF 0.68. Full file:
  `logs/sniper_oracle/LIVE_ORACLE_REPORT.md`. Rerun: `tools/oracle_ea_report.py`.
- Time-stop backtest (none / 5 / 15 / 20 min) on the last 1 and 2 months of
  real Zero data: `smc_bot/results/LIVE_TIMESTOP/` (full JSON per run, plus
  `ALL_TIMESTOP_RESULTS.json`). No time stop makes it profitable. PF 0.80-0.86
  in every run. The window used (Jul 29 - Sep 29, 2026) is inside the sealed
  period, so it is spent for this rule. Do not tune on it again.

## Ideas 3 and 4 — four more studies, all closed (Oct 16)

Brief: `smc_bot/bt/AUT11_14_BRIEF.md`. Each has `autopsy.json`, `report.md`,
`BREAKDOWN.md` and a full plain JSON per variant in its own folder.

| study | folder | result |
|---|---|---|
| AUT11 daily trend following (24 FX + gold) | `AUT11_trend` | no edge. 2008-09 carried the first window; 13 of 15 variants lost on the confirm window. Swap not in the data. |
| AUT12 clock events (fixes, COMEX, 8:30 data, jobs) | `AUT12_clock` | no edge. 0 of 80 cells paid. COMEX fade beats a coin by ~$0.36, cost ~$1.85. |
| AUT13 gold vs US dollar lag | `AUT13_leadlag` | no edge. Lag is under one minute; real bounce, too small. |
| AUT14 combine small edges | `AUT14_combo` | no edge. Signals are not independent; gain was gold 2009-10 buys. |

Total: 14 strategies closed, none with a proven edge. Smallest real signal
found: AUT12 COMEX-open fade, about $0.36 a trade against about $1.85 cost.

## AUT15 — how cheap would trading have to be? (Oct 16)

`results/AUT15_costcut/` (report.md first). 100 variants replayed with no cost;
30 earn something before cost in all three windows. None pays at today's cost.
Best case: AUT13 gold dollar-gap fade, worst window can carry $0.83 a round trip
against $1.59 paid, and makes only $0.04 a trade at $0.80. Real broker prices
are NOT known: someone must check which account offers a round trip near $0.80
and what its spread does at news. The cost menu in the report is hypothetical.

## Rules for whoever picks this up

- Spread and commission are real money. Never remove them from a result.
- The sealed period (2021 onward) stays sealed. Don't touch it.
- When a result looks good, always check: does it win on BOTH sides (buy
  and sell), not just one? Does it beat a coin flip? Does it still work on
  the next test window, not just the first one? Most things fail here.
- Keep replies short. Plain words. No long explanations unless asked.

## AUT35 - different exits to raise win rate (Oct 19)

Ideas copied from free gold bots (trend-flip exit, split targets). 0 of 5 rules worked.
Split targets raised win rate about 4 points with no trades lost, but cost about $1.1-1.3 a trade.
Flip exits make trades pile up and the fee eats them. Keep the fixed exit.
`results/AUT16_layers/SHAPERULE_REPORT.md`, script `bt/aut35_shaperule.py`, rules in PREREG.md addendum 21.
Free bot copies for reference: C:\Users\Tetteh\Desktop\ea_study (read only, never run).

## AUT36 - fix for the split-then-move-stop rule (Oct 19)

Real cause of the loss in AUT35: re-entering early (2x trades, fees). If the bot waits until the plain trade would have ended, trades stay the same, win rate +4 to +24 points, money per day about the same for gentle shapes (H2_80, T3, H2_60). 0 of 14 passed the written bar (money per day a bit under in one window, STRESS drop a bit above). Not wired into the EA; needs a demo test.
`results/AUT16_layers/SHAPERULE_REPORT.md` (bottom), `bt/aut36_splitbe.py`, PREREG addendum 22.

## AUT37 - entry rules of the free bots (Oct 20)

Trillex (EMA+SAR), mad808 (EMA+RSI), Stochastic Scalper, EMA 10/30 cross, tested as entries on gold and EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD. 0 of 60 passed; all but two near-zero forex cases lose money. Our entry stays. `results/AUT16_layers/BOTENTRIES_REPORT.md`, `bt/aut37_botentries.py`, PREREG addendum 23.
AUT37 follow-up (addendum 24): the same free-bot rules as extra checks on our own entry (gold): 0 of 6 helped. Trend checks repeat our entry; pullback/stretch checks hurt. `BOTENTRIES_REPORT.md` bottom, `bt/aut38_mixfilters.py`.
