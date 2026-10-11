# smc_bot: ML + SMC + EMA multi-timeframe variant (planned)

Idea from the user (2026-09-29):
- Top-down: higher timeframe (H1 or M30) decides the DIRECTION, e.g. with an EMA trend and SMC market structure (BOS/CHoCH).
- Drop to M15 (and lower if needed) for the ENTRY: SMC concepts such as order blocks, fair value gaps and liquidity sweeps, filtered by EMA.
- ML (LightGBM) scores/filters the setups.
- Reuse the existing data (no re-download): ../data (Standard, 2021+) and ../data_zero (Zero account, 2021+, spread ~0);
  resample M1 -> M15/M30/H1. Reuse ../research/lib.py (loading, simulator) and ../scalper (execution engine).
- Validate with the same protocol: train -> validate -> unseen 6-month test blocks, REAL Zero commission
  (USDJPY/EURUSD $2.50/lot/side), money per trade.
