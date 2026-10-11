# Brief for every backtest agent (read fully before starting)

Project root: `C:\Users\Tetteh\Desktop\TTXT` (Windows). Python: `.venv\Scripts\python.exe` (run from the root).
Read first: `smc-strategy.md` (the strategy), `smc_bot\REQUIREMENTS.md` (what the user wants), `smc_bot\PLAN.md`, `smc_bot\bt\common.py`.

## Hard rules
1. **Never place trades.** Never call `mt5.order_send`. Never print `.env`. Research only.
2. **Do not edit** `common.py`, `smc_core.py`, or other agents' files. Write only your own script `smc_bot\bt\bt<N>_<name>.py` and
   your own folder `smc_bot\results\BT<N>\`. If you find a bug in shared code, describe it in your report (file:line, proof).
3. **Date split**: explore only on **DEV (2021–2023)**. Use **VAL (2024-01 → 2025-06)** only to confirm your few best variants
   (at most 3 variants go to VAL). **Never** look at TEST (2025-07+). Keep `include_test=False` (the default) everywhere.
4. **No overfitting**: choose variants from trading logic and SMC theory, not from giant grids. At most about 12–20 variants per
   backtest; list every variant tried and its DEV result in the report (the trial count matters).
5. **Causal only**: decide on CLOSED bars; swings confirmed N bars later; a higher-timeframe value only after that HTF bar closed
   (`asof_index`). Orders start at `t_signal` = close time of the decision bar. Use `simulate()` for all fills and exits.
6. **All 10 pairs** (`PAIRS`), real costs (built into `simulate`). Report per pair and pooled.

## What to deliver in `smc_bot\results\BT<N>\report.md`
- **Question** tested, **exact rules** (entry, SL placement and WHY, TP and WHY, hold/exit logic), timeframes.
- **Variants tried** (all of them) with DEV stats. The chosen ≤3 variants with DEV **and** VAL stats: pooled + per pair
  (trades, trades/week, win %, avg R, total R, PF, max DD R, avg net pips, $ per 0.10 lot, avg hold).
- **10 example trades** from the trades CSV (entry time, price, SL, TP, exit, reason, R) that a human can check on a chart.
- **Sanity checks you ran** (e.g. replaying 3 trades by hand on M1 data), **errors/bugs found**, and **your verdict**:
  does this component have an edge after costs? On which pairs, timeframes and sessions? What would a trader do with it?
- Save trades with `save("BT<N>", "<variant>", trades)` (writes `trades_<variant>.csv`, TEST excluded).
- Keep the report concise (tables > prose). Finish with a 10-line summary at the top of the report.

## Useful helpers (common.py)
`bars(sym, tf)` M1/M5/M15/M30/H1/H4 (cached; `time` = open, `tclose` = close) · `structure(bars, N)` (trend, BOS/CHoCH events,
OB zone per break, last swing levels) · `swings` · `fvgs` · `ema` · `atr` · `build_zones` / `first_touch` (30M-style POIs) ·
`asof_index(tf_bars, times)` · `simulate(sym, orders)` · `stats`, `report_table`, `save`, `PERIODS`, `pip`, `COMM`.
`simulate` order columns: t_signal, side (+1/-1), entry_type ('market'|'limit'), entry, expire, sl, tp (NaN=none), be_at_r,
max_hold_min, plus any extra columns you want kept (e.g. sl_reason, tp_reason, context).
