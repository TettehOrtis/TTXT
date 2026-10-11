# smc_bot/live: canonical SMC REF on the Zero demo

| file | what |
|---|---|
| `ref_engine.py` | Frozen canonical REF (RECON section 4) on rolling windows of closed bars. `recon_pipeline.build_orders(CANON)` is the signal authority. |
| `ref_scanner.py` | Read-only status scanner (`start_scanner.bat`). Its output is `results/SCANNER/`. |
| `parity_test.py` | Replays history bar by bar: 153 / 153 canonical orders reproduced exactly (`results/SCANNER/parity_report.md`). |
| `ref_trader.py` | **Demo forward-test executor** (this document). |
| `test_ref_trader.py` | Mock-broker tests (31). `smoke` runs a read-only dry-run against the live terminal. |

## Start / stop

```
start_ref_trader.bat              # DRY-RUN loop (default; order_send is never called)
start_ref_trader.bat --live       # trade the Zero DEMO, magic 770077
start_ref_trader.bat --live --risk 5 --daily-loss 20 --max-open 4
powershell -File stop_ref_trader.ps1   # stops ONLY start_ref_trader.bat + smc_bot\live\ref_trader.py
.venv\Scripts\python.exe smc_bot\live\ref_trader.py --once [--example]   # one dry-run pass, prints state
.venv\Scripts\python.exe smc_bot\live\test_ref_trader.py [smoke]
```

- The .bat restarts the process 30 s after it exits.
- Logs go to `logs\ref_trader\` (`supervisor.log`, `stdout.log`, `stderr.log`, `trader.log` / `trader_dry.log`).
- Stopping is safe:
  - pending orders keep their broker-side expiry;
  - positions keep their SL/TP at the broker;
  - on restart, the state is reloaded and live orders/positions are re-attached.
- **Weekend and holiday flattening only happen while the trader is running.**

## Config defaults (`Config` in ref_trader.py; CLI flags in brackets)

| key | default | meaning |
|---|---|---|
| `live` | False (`--live`) | dry-run unless `--live` is passed |
| `risk_usd` | 5.00 (`--risk`) | fixed $ risk entry→SL per trade. lot = floor(risk / loss_per_lot(stop)) to 0.01 |
| `min_lot_risk_mult` | 2.0 | if even 0.01 lot risks more than 2 × risk, the signal is skipped (`sizing`) |
| `daily_loss_usd` | 20.00 (`--daily-loss`) | realized + floating REF P/L of the UTC day ≤ −20 → no new orders until the next UTC day |
| `max_open` | 4 (`--max-open`) | REF positions + pending orders (+ in-flight sends) |
| `magic` / `suffix` | 770077 / z | Zero demo, 9 pairs (EURGBP skipped) |
| `poll_s` | 2 | loop period |
| `late_grace_s` | 90 | a signal first seen more than 90 s after t_signal takes the "late" path (below) |
| `unknown_grace_s` / `max_resends` | 30 s / 1 | unknown order outcome: resend at most once, after 30 s and 3 clean searches |
| `holiday_flat_before_min` | 10 | flatten 10 min before a known holiday close (`CLOSURES`, extend with `--closures file.json`) |
| `max_hold_days` | 10 | `simulate(max_days=10)`; never binding because of the weekend flat |
| `cancel_grace_s` | 3 | own cancel 3 s after `valid_until` if the broker has not expired the order |

## What one M5 bar does

1. **Signal.** `ref_scanner.scan_pair` runs `RefEngine.evaluate`, the same path as the scanner, which has exact parity with the backtest.
   - Every REF order of the last 3 days becomes a record with a deterministic id `PAIR-yymmddHHMM{B|S}` and the comment `REF-<id>` (22 characters).
   - An order with t_signal == T is new. An earlier order that is still valid is late. An expired one is MISSED (trader was not running).
2. **Skip checks.** They run in this order, and the first one that fails is logged to `skips.csv` with its category:
   - `market_closing` (Friday ≥ 20:45 UTC, or a holiday window);
   - `late` (the backtest limit would already have filled, or TP has already traded);
   - `backtest_busy`;
   - `live_busy` (our position/order on the pair, or an in-flight send);
   - `daily_loss`;
   - `max_open`;
   - `gap` (price already beyond the SL);
   - `tp_reached`;
   - `sizing`.
3. **Order.**
   - If the limit is not marketable: BUY/SELL LIMIT at the entry, with SL and TP attached. Expiry is `ORDER_TIME_SPECIFIED` at t_signal + 60 min (all 9 symbols support it: expiration_mode = 15).
     - If the broker returns 10022, the order is resent as GTC and we cancel it ourselves.
     - Filling mode: RETURN first. On 10030 (rejected, so nothing was placed), FOK then IOC are tried.
   - If the limit is marketable (buy: ask ≤ entry), a market order is sent with the same SL/TP. `simulate` fills at min(entry, ask open).
   - The state is written **before** `order_send`.
4. **While pending.** The order is cancelled if the TP trades first (`TP_FIRST`, the M1 bars since t_signal plus the tick), at expiry (`EXPIRY_SELF`, a backup to the broker's expiry), and at the weekend or a holiday.
5. **While open.**
   - SL/TP stay at the absolute signal levels. They are re-attached if missing or changed; if the position has no stop and one cannot be attached, it is closed.
   - The position is closed at Friday 20:45 UTC, 10 min before a known holiday close, and after 10 days.
6. **Close.**
   - `trades.csv` takes all money from the broker's deal history: gross, commission, swap, net, R on the $ risk at the fill, and R in pips.
   - It also records the fill slippage vs the intended entry, in pips, and the backtest twin's result for the same signal.

## Backtest twin (the one-position-per-pair rule, exactly as the backtest)

- Every REF signal is replayed through **`common.simulate` itself** (`one_at_a_time=True`, default arguments) on the live MT5 M1 bars of that pair. This includes signals that were skipped, missed or dry-run.
  - `common.bars` is swapped in memory for the duration of the call; nothing on disk is edited.
- A new signal is `backtest_busy` when either:
  - the twin marks it `skipped_busy`;
  - an earlier non-skipped twin order is still unresolved (pending limit or open position).
- This includes the backtest's busy time of an **unfilled** limit, which lasts until its expiry even after a TP-first cancel.
- The live rule is checked as well, as `live_busy`.
- Resolved twin outcomes go to `twin.csv`: what the backtest would have done with each signal on the same live data. Compare it with `trades.csv` by `sid`.
- Signals skipped for live-only reasons (daily loss, max open, sizing, gap) stay in the twin. The pair's busy state therefore stays identical to the backtest's.

## Outputs: `results/FORWARD/` (live) and `results/FORWARD/dry_run/` (dry-run)

| file | content |
|---|---|
| `orders.csv` | One row per REF signal, rewritten atomically. Columns:<br>• sid, pair, side, status + reason, order kind<br>• stage times: POI created (`t_zone`), `t_touch`, `t_signal` (= CHoCH close), `valid_until`<br>• entry, SL + why, TP + why, RR, stop pips, lot, $ risk<br>• times: sent / placed / cancelled (+ reason) / expired / filled / closed<br>• tickets, all retcodes, the exact request<br>• backtest twin status, fill, exit, R |
| `order_events.csv` | Every lifecycle event with retcode (placed, retry_filling, send_unknown, reconciled, resend, cancelled, filled, closed, …). |
| `trades.csv` | Closed trades from broker deals (see step 6). |
| `skips.csv` | Every skipped or missed signal, with its category and reason. |
| `daily.csv` | One line per UTC day: signals, placed, skipped by reason, trades, wins, $ net, R, balance/equity, shadow count. Also written to the log. |
| `twin.csv` | Backtest-twin outcome per signal (`bt_*` columns). |
| `shadow_candidates.csv` | **ML data.** Every broad BT8 candidate the engine sees: bias and discount/premium are recorded, not required.<br>Computed by `bt8_ml.build_candidates` itself on the engine's live window, with all 32 `f_*` features plus `bias_c/t/x`, `dpos_raw`, `ref_gate` and `ref_signal`.<br>`cid` = pair, t_signal, side, zone time, zone kind. At first start, the last 3 days are backfilled (`seen_late=True`). |
| `shadow_labels.csv` | Independent outcome of each candidate (`simulate(one_at_a_time=False)`, as BT8 labels): status, fill, exit, R, gross_R, win, MFE/MAE. |
| `state.json` | Atomic state, written with the Windows PermissionError retry. |

Shadow parity: on 173 BT8 DEV candidates (GBPUSD, EURJPY, XAUUSD, 2023), the live-window candidates match the batch `candidates_DEV.pkl`: same set, same entry and same `ref_gate`. Every feature agrees to within 4e-10 (the EMA200 warm-up).

## Robustness

- **Restart.** The state is reloaded, and live orders/positions are matched by ticket.
  - Anything with magic 770077 and a `REF-` comment that is missing from the state is **adopted**: the id is parsed from the comment, and SL/TP/expiry are read from the order.
  - Magic-770077 items without a REF comment are never touched. `commission_test.py` used this magic on 2026-09-27 (comment `costtest`).
- **Unknown send** (`None`, timeout 10012, or connection 10031):
  - the record becomes UNKNOWN;
  - each cycle searches open orders, positions and history orders (by comment, or by symbol + side + volume + price + time);
  - if found, it is adopted;
  - if it is confirmed absent for 30 s over 3 clean searches, it is resent once (the signal must still be valid);
  - otherwise it is ABANDONED.
  - If any search call fails, absence is not confirmed.
  - A signal id already in the state is never sent again, so re-evaluating the same bar is harmless.
- **Disconnect.** The IPC link is re-attached without a re-login (`ref_scanner.ensure`), and the cycle is skipped while the terminal is disconnected. Orders carry broker-side SL/TP/expiry, so nothing depends on the loop for protection except the weekend and holiday flat.
- **Demo only.**
  - The account must be DEMO, and its login must equal `Z_LOGIN`.
  - The trader attaches to the running terminal and never logs in again (it falls back to `Broker.connect()` only if no terminal is running).
  - Dry-run: `MT5Gateway.send` raises, and live orders are never adopted.

## Deviations from `common.simulate` (all unavoidable or deliberate)

1. **Intrabar order.**
   - The backtest assumes SL before TP inside an M1 bar, and never counts a TP on the fill bar. These are pessimistic M1-resolution assumptions.
   - Live, the broker's attached SL/TP execute in true tick order, so a TP inside the fill minute counts.
2. **Stop slippage and costs.** The backtest assumes 0.2 pip stop slippage, a fixed commission in pips, and no swap. Live uses the real fills, commission and swap from deals.
3. **Marketable limit.**
   - `simulate` fills at the first M1 open after t_signal. Live sends a market order seconds after t_signal, at the current ask/bid with deviation 10 points.
   - Sizing uses the stop distance from that price, so the $ risk is unchanged.
4. **Gap beyond the stop at send time.**
   - `simulate` books GAP_SL: an immediate stop at −(slip + commission) ≈ 0 R.
   - Live skips the signal (`gap`), because a buy with the SL above the price is invalid.
   - The same applies to a TP that has already traded (`tp_reached`; `simulate` gives not_filled/invalid).
5. **TP-first cancel.**
   - `simulate` compares M1 bars exactly. Live polls every 2 s, using M1 highs/lows since t_signal and the tick.
   - If the broker fills the limit in the same moment, the cancel fails and the position is kept. `simulate` also lets the fill win within the same bar.
   - The sell side uses the M1 bar's recorded spread.
6. **Late signals.**
   - A signal seen more than 90 s after t_signal (restart, a slow data sync) is placed only if the backtest limit would still be pending. It is placed for the remaining validity.
   - Otherwise it is MISSED (`late`), and the twin still books it.
7. **Holiday closures.**
   - `simulate` flattens before *any* gap longer than 6 h. It knows the gap from the data; live cannot.
   - Live uses a calendar (`CLOSURES`, derived from the 2021-2026 Zero M1 gaps) and flattens 10 min before the close, instead of at the last bar before the gap.
   - Unexpected closures are not caught. **The calendar must be verified against Exness notices.**
8. **Weekend.** The backtest exits at the close of the Friday 20:45 M1 bar. Live closes at market at ≥ 20:45:00 UTC, as soon as the loop sees it. Saturday and Sunday before 21:00 UTC also count as the weekend.
9. **Max hold.** `simulate` counts 10 days as 14,400 M1 bars; live counts 10 calendar days. Neither is ever reached, because of the weekend flat.
10. **Live-only limits** (daily loss $20, max 4 open, min-lot risk ≤ 2 × risk). They do not exist in the backtest, but they do not change the twin's busy state (see above).
11. **Price rounding.** Entry, SL and TP are rounded to the symbol's digits: gold 3, JPY 3, other FX 5. The unrounded levels are kept in `orders.csv`.
12. **Evaluation latency.** Signals are acted on 3-10 s after the M5 close. `ref_scanner.get_raw` can block for up to ~30 s if the terminal's history is not synced, which delays the other pairs' checks. Orders stay broker-protected in the meantime.
