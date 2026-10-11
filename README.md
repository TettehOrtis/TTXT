# TTXT: MT5 gold bot (Exness demo)

## Bottom line
In testing I did **not** find a scalping edge that survives spread and slippage. This covers 13 FX pairs, gold, US30, NAS100, DAX, oil and BTC, 2021–2026, with over 800 configurations. An independent audit (Opus) reached the same result.

The bot runs the best candidate found as a **demo forward test**. Treat it as unproven:

| Candidate: gold, LightGBM 120-minute return model, top 1% of scores | Walk-forward 2023–26 |
|---|---|
| Trades | ~0.3–0.5 per day |
| Net per trade at 0.01 lot (1 oz), after spread and slippage | about +$0.10 to +$0.50 |
| Statistical strength | t ≈ 0.2–1.2, which is **not significant** |
| Worst trade / worst day at 0.01 lot | about −$27 to −$55 |

## Commands
```
.venv\Scripts\python.exe run_bot.py --dry-run --capital 250   # log signals only
.venv\Scripts\python.exe run_bot.py --capital 250              # trade the demo (capital 100-500)
.venv\Scripts\python.exe research\train_prod.py                # retrain + refresh thresholds (weekly)
.venv\Scripts\python.exe tests_logic.py                        # offline logic tests
.venv\Scripts\python.exe close_all.py                          # flatten all bot positions
```
Logs: `logs/bot.log`, and `logs/trades.csv`, which records every trade with intended vs filled price, slippage, spread and **net** P/L.

## Safety features
- Refuses to trade on non-demo accounts.
- Every order has a server-side stop, re-anchored to the actual fill price. If the stop can't be attached, the bot closes the position.
- If an order's result is unknown, the bot looks the position up instead of re-sending, and adopts or closes orphan positions.
- Signals are only acted on within 15 s of the bar close, which matches the backtest timing.
- The bot is flat before the 17:00 New York rollover and the weekend.
- It stops for the day on the daily target, the daily loss limit, or giving back half of the realized peak. It also stops after max trades, and pauses after a losing streak.

## Findings
- EURUSD and other FX majors: spread is ~50% of a typical 1-minute range, so random entries lose ~1.2 pips per trade.
- Gold has the best cost-to-volatility ratio, but model IC is only ~0.015–0.02, so gross edge ≈ cost.
- Opening-range breakouts (NY/London/Frankfurt): best training config +0.036R became −0.034R on 2025–26.
- The first USDJPY "edge" (t=4.7) was an accounting artefact. Shorts pay the spread at exit, and trades that span the rollover pay a 5-pip spread. Once that is counted, it disappears.
- The gold edge only exists with no protective stop and a pure 2-hour time exit. Any 2–5×ATR stop removes it.
- A daily profit target does not create an edge. With zero-edge entries, "stop at +2%" gives ~50% green days, but red days are bigger and the average day stays negative.
