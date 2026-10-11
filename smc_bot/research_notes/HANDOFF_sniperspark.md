# HANDOFF — the Sniper Spark gold bot investigation (2026-10-05)

Read this first in a new chat. It replaces re-deriving anything. Detail lives in
`SNIPERSPARK_strategy.md` (what the bot is), `SNIPERSPARK_results.md` (the exit testing), and
`results/BT15..BT18/report.md`.

**HOW TO TALK TO THIS USER — read before writing anything.**
Plain English, dollars, short replies. No R-values, percentiles, t-stats, "pre-registered", "out-of-sample".
Say "picks the right way 53 times in 100", not "53% accuracy, t=2.1". **Never change the lot size or units
mid-conversation** — everything below is at 0.10 lots; say so on every table. He checks new numbers against
old ones and treats a mismatch as proof something is broken. He is sharp, pushes hard, and has been right
every time he pushed. He is not a quant and gets angry at vague or shifting answers.

---

## 1. What the bot is
A commercial MT5 gold robot. It opens a group of positions **all at the same price**, with one shared stop
and target, and closes them **all together** when their combined floating money hits a dollar profit cap or a
dollar loss cap. 60-second cooldown, one basket at a time, no averaging down (confirmed with the seller).
Direction comes from an undisclosed 15-minute rule, timing from an undisclosed 1-minute pullback rule.

**It reduces to: one trade of the combined size, closed at +$T or −$S.** The "basket" is presentation — it
changes the trade history (losers get buried inside green baskets) but not one dollar of the arithmetic.

Shipped defaults: **$7 profit cap / $70 loss cap** at 0.10 lots.

## 2. The seller's evidence is real but proves nothing
Verified against our own live gold feed: his screenshot prices and timestamps are genuine. The bot really
trades. His summary showed **+$613 profit and Gross Loss 0.00 over about a week** — no losing trade *yet*,
which is exactly what ~89% win rate looks like before the first loss arrives. Max Drawdown 8.48% means it has
already been ~$270 underwater and recovered. **You cannot measure risk from a record with no losses in it.**
His account is XAUUSDm (Exness Standard, commission 0.00). Ours is XAUUSDz (raw spread + commission).

## 3. The exits are SOLVED — do not re-test them
~150 combinations tested: stops $10–$150, targets $3–uncapped, time limits 5 min–4 h.

**Everything hinges on one number: how often the entry picks gold's right direction.** A coin flip is 50.

| Profit cap | Loss cap | Typical trade | Trades/day | **Needs** |
|---|---|---|---|---|
| $7 | $70 (as sold) | 10 min | 18 | **56.7%** |
| $7 | $20 | 7 min | 18.6 | 56.5% |
| $10 | $20 | 11 min | 14.9 | 54.4% |
| **$15** | **$20** | **17 min** | **11.1** | **52.9%** ← user's pick |
| $20 | $20 | 24 min | 9.2 | 52.3% |
| $30 | $30 | 52 min | 8.3 | **51.6%** |

All at 0.10 lots, his costs (no commission, 0.072 spread). Two key facts:
- **The target is the lever, not the stop.** Stop size barely moves the answer; target size moves it a lot,
  because the fixed cost is a big share of a small win.
- **A time stop does not help.** 90 combinations, all between −$1.79 and −$1.92. 81% of losses are still open
  at 20 minutes, but 76% of everything still open then goes on to win — a clock cuts ~3 winners per loser.
- **It can never need less than 50%**, because before costs a coin flip is exactly break-even at any
  target/stop pair.

## 4. The entry — everything tested, nothing works
| Idea | Best score | Notes |
|---|---|---|
| 4 readings of "M15 trend + M1 pullback" | 49.6–51.2 | coin flips |
| SMC on M1/M5 (sweeps, order blocks, FVG) | 50.0–50.5 | as empty as at M15+ |
| **M1 break of structure, FADED** | **51.45** | above 50 in **17 of 17 years** — real |
| Dollar strength | 50.52 | correlation −0.42 to −0.56 is real but **lead-lag is 0.00** — simultaneous, not predictive |
| News (NFP/CPI/FOMC) | noise | 57% on 195 chances → 49% on 190,000 |
| M1 RSI fade | 52.77 | missed by 0.08 but decays 53.4 → 51.1 → 50.5 |
| Spike fade (1-in-200) | 52.07 | best money, 5.5 trades/day |
| Spike fade (1-in-1,000) | 52.55 | but its **own** break-even is 54.57 (post-spike volatility raises the bar) |
| Intraday momentum, 30 min | 52.05 | 0.7 trades/day |
| Late session 20:00–21:00 UTC | 67–70 **DEAD** | lived almost entirely in hour 21 UTC = the broker's daily break, median spread **0.304**, our account does not quote it. In the tradeable hour 20 it is 52.5/50.4 by 2024 and fading. |

**Placebo bar: scrambled data with 25 variants reaches 53.16 by luck. Our best real reading is 52.55 — it does
not beat the fake data.**

**THE ONE CONSISTENT REAL FINDING: fading beats following at short horizons, by 1–2 points.** Independently in
BT4, BT16 (50.78 vs 49.43), BT17 (51.45 vs 48.55) and BT18. Gold mean-reverts after short moves. **The spread
costs about 3 points. The edge is real and smaller than the toll.**

## 4b. BT19 — the time map (new, 2026-10-05). Time carries no information.
Plain-English version for the user: `research_notes/BT19_timemap_plain.md`.
380,378 baskets, first-passage times to every dollar level, 2009–2025, **our** costs ($1.10 commission).
Reconciles with BT15: winners killed by a $10 cut 38.6% (BT15 said 39.3%); 2021-25 win 89.8%, 7 min to the
win, 105 min to the loss (BT15's "7 and 89").

**THE FINDING: the chance a losing basket comes back does not change with how long it has been open.**
Down $10 → 77% come back, whether that took 5 minutes or 4 hours (76.7 / 77.5 / 77.5 / 76.9 / 75.1 / 73.7).
Down $20 → 64%, flat. Down $30 → 51%, flat. Down $50 → 24%, flat. **Depth is informative, the clock is not.**
This is *why* the 90 time stops in §3 were all flat — there was never anything there to find. Do not re-test
"cut it if it has been open too long" in any form.

Clock, 2021-25 (2009-21 is ~2.5x slower; always say which): median minutes to the win $7→7, $15→29,
$30→80, $50→158. Median to the $70 loss: 105. Winners' dips: 55% never pass −$5, 37.5% touch −$10,
11.8% touch −$30, 3.8% touch −$50.
Ceiling with nothing closing it: median peak **$109** in 24 h, but the peak lands ~11 h in; $30 is reached
by 85% of baskets within ~2 h, $50 by 75% within ~3.8 h.
Sessions (share of chances): Asian 31%, London 22%, Overlap 13%, NewYork 22%, Late 11%. Per-basket is
negative in every one. Selection by session already failed in BT7 and BT14 — this is a count, not a lead.

**CAUTION when comparing to §3:** §3's required-accuracy table is at HIS costs (no commission). BT19 is at
OURS ($1.10 round trip), so BT19's bar is ~2 points higher for the same shape. Never put them in one table.

## 4c. BT20 — real entries x every exit shape (2026-10-05). §7 option 1 is now CLOSED.
Plain-English: `research_notes/BT20_entries_x_shapes_plain.md`. Code `bt/bt20_*.py`, data `results/BT20/`.
12 entry rules x 36 shapes (targets $7-$50, stops $5-$70) x 4 windows, WITH the robot's one-at-a-time and
60 s cooldown, our costs. This is the combination §7 option 1 asked for. It does not work.

**432 combinations. Shapes that make money in 2009-2021 AND 2021-2023 AND 2024-2025: ZERO.**
Best any shape does is take the loss from −$1.93 to about **−$1.10** a basket. Nothing crosses zero.

- **The 52.55 spike fade is mostly pre-2021.** Audited independently: code CLEAN (no lookahead, not the
  daily break — both checked properly). But 73% of its trades are pre-2021, it reads **50.76 on 2021-2023**,
  and the only >52 recent readings have 695 and 380 trades. Like-for-like on the recent feed it scores
  52.45 against its own scrambled-data bar of 53.58 — **fails by 1.13**. Do not build on 52.55.
- **RSI fade (52.77) and BOS fade both COLLAPSE at every shape.** RSI fade reads **48.44** on 2024-2025
  against an own-bar of **65.9**; BOS fade 47.99 against 64.9. Reason: rules that pick "interesting"
  minutes pick VOLATILE minutes, and a volatile minute's break-even bar is 10+ points higher. The edge and
  the cost arrive together. This is the project's central trap, now measured directly.
- **Throughput, measured at last.** A $5 stop frees the robot fast: **6.11 trades/day (158/month)** vs
  **3.67/day (95/month)** at a $70 stop — 66% more. But total loss got WORSE (−$9,382 vs −$8,879): more
  trades at a smaller loss each still adds up. Tighter stops beat looser ones per basket ($30/−$5 = −$1.10,
  $30/−$20 = −$1.46), so "more breathing room" is the wrong direction.
- **Sessions:** the busiest is the worst. Overlap 12-15 UTC takes >50% of trades at −$1.38; NewYork −$1.71;
  London −$0.80; **Asian −$0.23 (best real one)**. The "Late" row looks positive but is 0.21 trades/day and
  sits in the daily-break hours — same trap as the dead §4 late-session lead. Skipping Overlap+NewYork
  roughly halves the bleed and still does not reach zero.
- Closest thing to a survivor: COMEX-open momentum at $50/$5, +$0.79/basket on 2021-2025 — but **49.29
  accuracy over 2009-2021**, i.e. a coin flip for twelve years. 21 trades/month, ~$17/month. Noise.

## 5. Measured facts worth keeping
- **Our live gold spread (Exness Zero, XAUUSDz): 0.040 median at every hour.** Except the daily break —
  21:00–22:00 UTC in summer, 22:00–23:00 winter — where it is 0.304 or gold does not quote at all.
- **His spread: 0.072** (from his own screenshot: −$0.36 on 0.05 lots at open).
- Break-even at $15/$20 confirmed independently on two data feeds: **52.85 / 52.87**.
- Each point of accuracy above break-even is worth about **$0.31 a basket**.

## 6. Data bugs found (fix before trusting old work)
- **ERRORS #34:** the gold `spread` column is 0 on ~97% of minutes in both datasets — every gold backtest
  before BT15 effectively charged **no spread**.
- **ERRORS #42:** the Exness Zero feed jumps **+$6.61 per 0.10 lot across every daily rollover** (61% upward);
  Dukascopy shows $0.04. Affects BT9 and all overnight gold work. (Checked — it did NOT cause the late-session
  lead; that was killed by the spread instead.)

## 7. Options from here, best first
1. ~~**Combine the best entry with the cheapest settings.**~~ **DONE — IT FAILS. See §4c (BT20).** All 432
   entry x shape combinations run: zero make money across all three eras. The premise was wrong in two
   ways: the 52.55 is mostly pre-2021 (coin flip on 2021-2023), and a bigger target does NOT lower the bar
   for these entries because they fire on volatile minutes whose own bar is 10+ points higher. Do not redo.
2. **Attack the cost, not the entry.** The edge is 1–2 points; the cost is ~3. Halving the cost is
   mathematically equivalent to finding 1.5 points of skill, and is far more achievable. Compare brokers and
   account types on gold spread specifically.
3. **Ask the friend for a real track record** — months, with losing baskets in it. If he has 6 months of
   genuinely profitable live results, something in our analysis is wrong and finding out what is valuable.
4. **Try another instrument.** The bot also runs on BTC. The question is whether the typical move is large
   relative to the spread — gold's is not, by about 3 points. Untested.
5. **Demo forward test anyway.** Costs nothing but time, and real fills settle arguments that backtests cannot.
6. **Stop.** The evidence genuinely supports this.

## 8. What NOT to redo
Exits (fully solved, §3). Time stops (90 combinations, flat). SMC on any timeframe including M1/M5. Dollar
strength (mechanism verified real, predictive power verified zero). News events. The late-session lead.
Sessions/hours as a *selector* — BT7 found no session edge and BT14 showed "London beats New York" turns up in
50% of charts made from pure random numbers.
