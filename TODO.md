# TTXT: TODO and status (updated 2026-09-26)

## Done
- [x] venv (py3.11), `.env` credentials (git-ignored), MT5 connection verified, symbol suffix `m`
- [x] Full M1 history, 2018→2026, for 19 instruments. Spread data is only usable from 2021.
- [x] Cost-realistic simulator (bid/ask, slippage, SL-first, gap-aware stops) with a walk-forward LightGBM pipeline
- [x] Tested: random baseline, FX ML, gold ML, indices/oil/BTC ML, ORB on 3 sessions, daily-target simulation
- [x] **Opus audit**: no edge survives. ORB entry-bar bias and mild threshold look-ahead found and fixed.
- [x] **Fable review**: 15 execution/risk bugs found. Fixed #1–#15: orphans, unknown order outcomes, pause re-arm, floating giveback, stale bars, SL re-anchor, backoff, atomic state, tz-aware history.
- [x] Corrected cost accounting: shorts pay exit spread, no rollover crossing. This killed the USDJPY "edge".
- [x] Execution tested on demo (BTC round trips): 0.5–2.5 s latency, slippage ±$4 on BTC, commission 0
- [x] Gold ML forward-test strategy, live/backtest feature parity exact (1e-8), dry-run verified

## Scalper research on the Zero account (2026-09-27)
- [x] Zero demo account #81749554 (Exness-MT5Trial10, symbols end in `z`) saved in `.env` as Z_*. Second Standard demo #477294180 ($500) saved as ZERO_*.
- [x] Zero history has a spread of 0 in over 99% of minutes on the majors (data_zero/)
- [x] Walk-forward (train → validate → unseen 6-month test, 8 blocks, 2021–2026) on Zero data: research/zero/*_mid.csv
      Gross edge per trade: USDJPY 1.35 pip (8/8 blocks), EURJPY 0.58 (8/8), USDCHF 0.49 (8/8), EURUSD 0.44, GBPUSD 0.57, others ≤0.3
- [x] Real Zero commission test (2026-09-27 22:15 UTC, cost $14.63 of demo money), per lot per side: EURUSD/USDJPY $2.50, GBPUSD $3.15, AUDUSD $3.00,
      USDCAD/EURJPY $3.50, NZDUSD $4.50, USDCHF $5.25, EURGBP/XAUUSD $5.50. Round trip = 0.5–1.1 pip.
      Net after costs: only USDJPY H20 is positive (+0.46 pip, 0.8 trades/day, 5/8 blocks, 2026 block negative), which is weak. All others lose (z_net.py).
- [ ] Waiting on the user: USDJPY-only Zero demo forward test, or stop
- [x] (was) run `.venv\Scripts\python.exe -u commission_test.py`
      to get the REAL Zero commission per pair, then keep only pairs whose gross edge exceeds real cost. Web figures (unverified): EURUSD/USDJPY $3.50 per side.
- [ ] Then build the multi-pair Zero scalper (5–20 min holds) and demo-trade it

## Next
- [ ] Monday: run `run_bot.py --dry-run` for 1 day, then demo-trade and compare `logs/trades.csv` to the backtest
- [ ] Weekly: `research/train_prod.py` (retrain + rolling thresholds)
- [ ] After ≥100 demo trades: decide keep or kill (needs net > 0 and real slippage ≤ 0.5 units)
- [ ] Ideas not yet tested: multi-day gold trend following (not scalping; longs pay ~$0.57/night swap per 0.01 lot), tick-level order-flow features

## Honest limits
- No strategy can guarantee a profit on every trade or every day. The daily lock only limits damage and banks gains; it creates no edge.
- Gold's minimum lot (1 oz) with a 5×ATR stop risks ~$8–15 per trade, which is large for a $100 account.

## USDJPY forward test on the Zero demo (started 2026-09-29 13:41 UTC)
- Bot: `run_usdjpy.py` (model models/usdjpy_h20.*, trained until 2026-03-30, thresholds from the last 6 months). 0.10 lot,
  20-minute hold, 20-pip safety stop, 07:00-20:00 UTC, max 6 trades/day, daily loss stop $30. Magic 660066, logs in logs/usdjpy/.
- Start: double-click `start_usdjpy.bat` (after a reboot or when the laptop is back on). Stop: `powershell -File stop_usdjpy.ps1`.
- Results: `.venv\Scripts\python.exe usdjpy_report.py` (reads broker history).
- Judge after ≥40 trades (~2-4 weeks). Expect ~1 trade/day. Pass = net > 0 after commission. Long-run test expectation: +0.46 pip/trade (≈ +$0.29 per 0.10 lot), weak evidence.
- Fixed on 2026-09-29: the engine fetched only 700 bars, so previous-day features were NaN (it affected the gold bot too); a refetched data file was missing Monday 28 Sep.
- Overnight manual test (15 trades, 27-29 Sep): −$34.18. The exit-grid backtest (research/exit_grid.py) showed that no TP/SL combination beats commission with the trend rule.
