# RESUME: where things stand (written 2026-10-03 16:30 UTC)

Read this first after a usage-limit reset or a new session. Then read PLAN.md (status log at the bottom), GLOSSARY.md
(plain-language terms) and ERRORS.md.

## ⚠️ POWER PROBLEM — check this first
The laptop ran on battery all of 2026-10-03 and **slept through most of it**: the watchdog logs every 10 minutes but
logged 00:46, then 08:47, then 16:00. ~15 hours lost. Battery drain while awake is ~36%/hour (6% per 10 min), so it has
under 2 hours unplugged. Keep-awake is being overridden on battery (or the lid was closed). **Everything stops when it
sleeps, including the download and any running agents.** Ask the user to keep the charger in.

## Running on the laptop (no Claude needed)
| Job | Start / stop | Logs |
|---|---|---|
| Watchdog: keep-awake, relaunches every job below if its supervisor dies | start_watchdog.bat / stop_watchdog.ps1 | logs/watchdog.log |
| USDJPY ML bot, Zero demo, magic 660066 | start_usdjpy.bat / stop_usdjpy.ps1 | logs/usdjpy/ |
| REF read-only scanner | start_scanner.bat | logs/scanner/, smc_bot/results/SCANNER/ |
| **REF demo trader LIVE on Zero demo**, magic 770077, $5 risk, $20 daily stop, max 4 open | start_ref_trader.bat --live / stop_ref_trader.ps1 | logs/ref_trader/, smc_bot/results/FORWARD/ |
| Best-data Dukascopy full download → data_duka_full/ | start_data2_best.bat | logs/data2/, smc_bot/results/DATA2/progress.json |

Download at 2026-10-03 16:12: **39,266 / 48,931** files, 0 failures. Measured rate ~4 files/min → **~40 h of awake time
left** (the job's own `eta_h` says ~19 h; it averages over faster earlier stretches). Will not finish in a day.

Not persistent across a REBOOT: the Startup-folder auto-start was blocked by the permission checker, and it is the
user's decision. After a reboot, run start_watchdog.bat; it starts everything else.

## Closing out the SMC program
- **BT13 FAILED** (`results/BT13/report.md`): frozen REF on 16 unseen years = net +0.036R/trade, t 0.46.
- **BT8b: nothing passes** (0/13, `results/BT8b/report.md`). Loss autopsy: the entry carries no information.
- **REF has no demonstrated edge.** The demo trader runs only as a plumbing check.

## DECIDED 2026-10-03: Option B
The locked TEST period (2025-07 → now) **stays sealed**. We do not spend it on REF. Instead: research a new non-SMC
idea with the same DEV/VAL/HIST method. (Option A — run REF + H1 + H2 once on TEST — was declined, because BT13 already
gave REF a far harder exam than TEST's ~60 trades ever could.)

## Agents
- **BT14: DONE 2026-10-05** (`results/BT14/report.md`). 200 zero-edge worlds. Both blocking checks passed, including
  reproducing canonical REF exactly on real data (150 trades, net +0.196R, t 1.07), so the measurement is trustworthy.
  **THE BAR = +0.472R per trade** ($7.57 at 0.10 lot; $2.36 at the demo's $5 risk). REF's +0.196R is less than half of it.
  42% of worlds with no edge at all beat REF; 31.5% reproduce its entire headline (money AND t-stat); 'London beats New
  York' — BT6's one pre-registered hypothesis — comes up in 50% of them. **BT6 is closed as a false positive.**
  Note: the agent died on a usage limit at 24 worlds and wrote a stale bar of +0.35R into PLAN.md; the run finished and
  `bt14_report.py` was re-run over all 200. Use +0.472R. The bar is specific to this search shape (49 readings) — if a
  future candidate searches a different number of variants, re-run BT14 for that shape.
- **Option B candidate research: DONE** → `research_notes/OPTIONB_candidates.md`. Top 3 are fix/flow effects
  (Melvin & Prins 2015; Krohn, Mueller & Whelan 2024; Breedon & Ranaldo 2013), but the agent's honest read is that they
  are **~3× too small for our `gross ≥ 2× cost` rule**. **Several citations are UNVERIFIED (403 / abstract only)** and
  must be read from the PDFs before anything is pre-registered — especially Krohn's exact windows and post-2015
  subsample. Do not pre-register off the summary.

## Rejected 2026-10-03: Deriv Volatility 150 (and all synthetic indices)
They are a certified RNG running zero-drift geometric Brownian motion. There is no order flow, so no edge is *possible*,
not merely absent. A 20M-tick simulation confirmed every entry rule (random, sweep+reversal, momentum, fade) lands on
the 1/(1+RR) = 25% break-even win rate and ~0.00R gross, negative after costs. If the user raises this again, that
simulation is the answer.

## Forward test: 0 trades in 3 days, now unblocked
Both signals (XAUUSD SELL 10-01, 10-02) were correctly refused on sizing. XAUUSD 0.01 lot (1 oz) risks $1 per $1 of
stop, so 106 and 238 pip stops force $10.61 and $23.78 — over the old `min_lot_risk_mult` 2.0 ceiling of $10.
**Changed 2026-10-03 (user's choice): `risk_usd` stays $5 (FX unchanged), `min_lot_risk_mult` 2.0 → 5.0 = $25 ceiling**
([ref_trader.py:93](live/ref_trader.py#L93)). Trader restarted 16:27 UTC. `daily_loss_usd` is still $20, so **one max
gold loss now ends that UTC day** — the user was offered a raise to ~$40 and has not answered.
Plumbing proven so far: signals detected 3–5 s after the bar, sized, logged, twin recorded. No fills yet, so no
live-vs-backtest parity data. Note the Oct 2 balance drop 450.89 → 437.67 was the **USDJPY bot** (same account), not REF.

## Morning checks
1. logs/watchdog.log: jobs running? Any sleep gaps or on-battery warnings?
2. smc_bot/results/FORWARD/: any REF orders or fills? Compare trades.csv with twin.csv (live vs backtest).
3. The USDJPY bot: `usdjpy_report.py`.
4. Download progress: smc_bot/results/DATA2/progress.json.

## Reports for the user
Run `tools/plain_report.py` on any trades CSV (plain words + summary); terms in `GLOSSARY.md`. The user wants money
terms and plain language, and is not a professional quant — explain in those terms, not in jargon.

## Open items for the user
- **Keep the charger plugged in** (see the power problem above).
- Daily loss stop: raise $20 → ~$40 to match the new $25 gold risk ceiling? Not answered.
- Windows Update may restart the laptop 00:00–06:00 (active hours 06–00). Pausing needs the user (no admin rights here).
- Auto-start after a reboot (Startup folder): user's decision.
- Live risk and lot size, when/if anything is ever worth trading live.
