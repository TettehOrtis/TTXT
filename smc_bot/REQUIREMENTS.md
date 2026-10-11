# What the user wants (read this before every decision)

Collected from the user's messages (2026-09-29). Strategy spec: `../smc-strategy.md`.

## Goal
- **Make profit.** Find what maximizes profit with the SMC strategy (ML + SMC + EMA, top-down multi-timeframe), on **multiple pairs**.
- Not a 5–20 minute scalp any more. Holding time is **my decision**: short or long, whatever the evidence says maximizes profit.
- Risk per trade and lot size are decided later with the user. "If the strategy is good, lot size doesn't matter."

## How to work
1. **Separate backtests, one per component, numbered**: BT1 liquidity sweep, BT2 order block, BT3 fair value gap, BT4 structure (BOS/CHoCH), BT5 HTF bias/EMA, BT6 full SMC pipeline, BT7+ further ideas. **Do not combine everything and report one number.**
2. **Every backtest produces full trade records**: signal time, entry time and price, SL (where and WHY there), TP (where and why), exit time, price and reason, pips, R, money. Proof, not summaries.
3. **Split the dates. Never tune on the data used to judge.** Develop on one period, confirm on another, keep a final period untouched until the end (like training a model). **No overfitting.** Don't let the system memorize the data.
4. **Think like a trader sitting at the screen.** What is the higher timeframe doing: trend, breakout, retest, range? What would I do at this moment? Where would I put the stop, and why? When would I take profit or hold?
5. **Use multiple agents in parallel** (Opus, Fable). Several can check the same thing. Each backtest gets checked: is it doing what it's supposed to do?
6. **Errors**: document every error found (`ERRORS.md`). After each backtest, ask: is it a bug, or is the strategy losing money? If it's losing, research why (including on the internet) and find a proper solution, not a patch.
7. **Write everything down** (this file, `PLAN.md`, `ERRORS.md`, results) so I can come back to it anytime. If I did something wrong, go back and change it.
8. **ML matters** (it should learn and improve), but only after the component backtests show what works.
9. **Follow the market**: a live scanner records each pair's status and stage (`scanner.py` → `status/`).
10. Agents: use whichever model fits (Fable, Opus, Sonnet), not only Opus. Build as many backtests as needed (BT1..BTn, not limited to 7).
11. The user leaves the decisions to me. Ask the user only when truly needed.
