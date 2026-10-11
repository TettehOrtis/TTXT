# BT8b: ML on 2005–2025 data: loss autopsy, bigger picture, dynamic decisions

User request (2026-09-29): "across all trades the ML should figure out the real reason why a trade loses and look at the bigger
picture. The rules are strict but the ML can be dynamic. That's why we gave it more data; retrain it."

Runs after DATA2 (Dukascopy 2005–2025) and BT13. Use `SMC_DATASET=duka`. Read `BT8` report first (`results/BT8/report.md`):
ML on 5k setups did not beat REF or random filters. The only real structure found was the bias × discount/premium AND gate.

## Data
- Broad candidates (REF with bias and discount/premium as features, not gates) on 25 instruments, HIST 2005–2020 + DEV + VAL
  (≈ 70k+ setups). Each candidate is simulated independently, keeping the full path after entry. For each trade record:
  MAE/MFE in R, time to MFE and MAE, R reached before reversal, and whether the H4 bias flipped during the trade.
- TEST stays locked.

## 1. Loss autopsy (the "why")
Label every losing trade with its failure mode, computed from its path. Suggested modes:
- **No follow-through:** stopped out, never reached +0.5R.
- **Right idea, too-far target:** reached ≥ +1R, then reversed to the stop.
- **Stop too tight:** the stop was hit, then price went on to the target within N hours.
- **HTF bias flipped mid-trade.**
- **News or volatility spike:** a bar > k × ATR against the trade.
- **Chop:** hit the time exit or the weekend exit near 0.

Then find which pre-trade conditions concentrate each mode, e.g. "70% of no-follow-through losses come when D1 is against the
trade", or "too-far-target losses cluster when the target is > 4R in a low-volatility regime". Report in trader language with
counts, and check that each finding holds in every era (2005–08, 2009–14, 2015–20, DEV, VAL), not just pooled.
Use SHAP values to explain individual trades: show 10 losers and 10 winners with the model's top reasons.

## 2. Bigger-picture features (added to BT8's 32, all causal)
- D1 and W1 trend: structure plus EMA, and distance to D1/W1 highs and lows in ATR.
- Regime: efficiency ratio / ADX on H4 and D1; volatility percentile vs the last year.
- Currency strength: for each currency, the average return across the 25 instruments over 1d and 5d. Is the trade aligned
  with the strong/weak currency?
- Risk-on/off proxy: JPY-cross basket and gold direction.
- Position in the week and month; days since the last large D1 range.
- Optional, only if the historical calendar is clean: high-impact news within ±2h.

## 3. Dynamic decisions (what the ML may change; the rules still find the setups)
- **Take or skip:** a filter, as in BT8.
- **Target choice:** per trade, pick the nearest H1 liquidity, 2R, or partial at 1R plus runner, based on the predicted MFE
  distribution (quantile regression or a classifier over the options).
- **Break-even or time exit:** yes or no, based on the predicted path.
- **Size:** optional; confidence tiers (0.5×/1×/1.5× risk), only if calibration is good.

## Protocol (anti-overfitting)
- Walk-forward by year across 2005–2025: train on everything before year Y, predict year Y. Choose model family,
  hyperparameters and thresholds on HIST out-of-fold only. Then freeze and report DEV and VAL once.
- Baselines: REF hand rules, unfiltered, a random filter with the same take-rate, and the shuffle-label test.
  ML is adopted only if it beats REF and random out-of-sample, in most years and most eras, on both gross and net.
- ≤ 15 variants in total, all counted. Report gross before net. Money per 0.10 lot.
- Deliver the model files plus a `predict()` usable by `live/ref_trader.py` in shadow mode, and the monthly retrain schedule with
  the guard from BT8.
