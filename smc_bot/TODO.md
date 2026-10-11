# smc_bot: status and next steps

Spec: ../smc-strategy.md (user). Data: ../data_zero (Zero account, 2021+). Costs: real Zero commission (backtest.py COMM).

## Written (2026-09-29), not yet run
- [x] `smc_core.py`: causal resampling (M5/M15/M30/H1), fractal swings, BOS/CHoCH (close-based), OB, FVG, 30M POI zones, first-touch mitigation
- [x] `backtest.py`: 1H bias + 30M agreement → first touch of 30M POI → 5M CHoCH within 36 bars → limit on 5M FVG/OB retest →
      SL beyond zone/sweep + buffer → TP next unbroken 1H swing, RR ≥ 2 → M1 execution (SL-first, weekend flat).
      EMA 200/50/20, discount/premium, sweep, M15 agreement are recorded as FEATURES (not hard rules) so with/without can be compared.
- [x] `ml_walkforward.py`: pooled-pairs LightGBM, 6-month blocks train → validate (threshold) → unseen test
- [x] `scanner.py`: live read-only status per pair → status/status.md, status.json, history.csv ("follow the market")

## Results so far
- EURUSD rules-only (2021–2026): 277 trades, win 17%, avg −0.36R, −2.56 pips/trade, 224 of 277 exits by SL. Needs an audit (bug or reality?) before tuning.

## Next
- [x] Run `backtest.py` for EURUSD
- [ ] Run the other 9 pairs: `for S in GBPUSD USDJPY ...: python smc_bot/backtest.py $S` (blocked on 2026-09-29 by a permission-checker outage)
- [ ] Compare rule variants from recorded features: EMA on/off, discount on/off, sweep on/off (choose on 2021-23, check 2024-26)
- [ ] Tune candle counts (N swings, zone age, CHoCH wait) on 2021-23 only
- [ ] Run `ml_walkforward.py`
- [ ] Opus agent: look-ahead audit of smc_core/backtest; Fable agent: protocol/overfitting review
- [ ] Run scanner live; then the execution bot on the Zero demo; then risk per trade and lot size (with the user)
