# Real entries at every exit shape — what the data says

**Date:** 2026-10-05
**0.10 lots throughout. Our costs ($1.10 round-trip commission).**
12 entry rules × 36 exit shapes × 4 time windows = 432 combinations, with the robot's real
one-basket-at-a-time rule and 60-second cooldown.
Raw tables: `smc_bot/results/BT20/`. Code: `smc_bot/bt/bt20_lib.py`, `bt20_run.py`, `bt20_report.py`.

---

## Before anything else — the 52.55 number does not survive

I had a second agent audit the spike-fade entry independently. The code is **clean** — no peeking at the
future, and it is not hiding in the broker's daily break hour. Both of those were checked properly.

But the number itself is mostly old:

| Spike fade variant | All years | 2009-2020 | 2021-2023 | 2024-mid2025 |
|---|---|---|---|---|
| p99.9 / w7200 | 52.55 | 52.59 (4,980) | **50.76** (1,179) | 55.32 (695) |
| p99.95 / w7200 | 52.50 | 52.34 (2,730) | **50.30** (667) | 57.50 (380) |
| p99.5 / w7200 | 52.10 | 52.51 (21,856) | **50.15** (5,344) | 52.56 (2,912) |
| p99.0 / w7200 | 51.94 | 52.27 (42,446) | 51.19 (10,616) | **50.87** (5,619) |

**73% of those trades are from before 2021. On 2021-2023 it is a coin flip.** The only readings above 52
in recent years have 695 and 380 trades behind them. Every variant with a big recent sample sits at 50.

Measured like-for-like against the scrambled-data bar on the recent feed alone: **52.45 against a bar of
53.58 — it fails by 1.13.**

So: the entry is not broken, it is just not there any more.

---

## 1. Your trade-count question — you were right on both sides

You asked: with a tight stop do we take the same number of trades, more, or fewer? And could more trades
mean more loss?

Same entry rule, same signals, only the exit changes. 2021-2025:

| Shape | Trades | Per day | Per month | Typical minutes | Gross | **Net** | Per basket |
|---|---|---|---|---|---|---|---|
| $30 / −$5 | 8,515 | **6.11** | 157.7 | 0 | −$15 | **−$9,382** | −$1.10 |
| $30 / −$10 | 8,227 | 5.91 | 152.4 | 1 | −$910 | −$9,960 | −$1.21 |
| $30 / −$15 | 7,948 | 5.71 | 147.2 | 2 | −$2,130 | −$10,873 | −$1.37 |
| $30 / −$20 | 7,624 | 5.47 | 141.2 | 4 | −$2,730 | −$11,116 | −$1.46 |
| $30 / −$30 | 6,899 | 4.95 | 127.8 | 7 | −$1,110 | −$8,699 | −$1.26 |
| $30 / −$70 | 5,118 | **3.67** | 94.8 | 15 | −$3,249 | −$8,879 | −$1.74 |

**A $5 stop gives you 66% more trades than a $70 stop** — 6.11 a day instead of 3.67, 158 a month instead
of 95. Exactly as you guessed: the robot is freed up far sooner, so more signals get taken.

**And you were right about the danger too.** Look at the net column. The $5 stop loses *less per basket*
(−$1.10 vs −$1.74) but takes so many more trades that the **total loss is bigger**: −$9,382 against
−$8,879. More trades at a smaller loss each still added up to more money gone.

### On your "give it more breathing room" idea

You thought the stop should be $15 or $20 rather than $5. The data disagrees, mildly:

- $30 target / −$5 stop → **−$1.10** a basket
- $30 target / −$15 stop → −$1.37
- $30 target / −$20 stop → −$1.46

Tighter was better here, not looser. Though note every one of them loses, so this is choosing between
shades of the same answer.

---

## 2. The real test — does anything pay in EVERY era?

This is the test that matters. A setting that works in one window and not the others is noise.

**432 combinations tested. Shapes that make money in 2009-2021 AND 2021-2023 AND 2024-2025: ZERO.**

Not one. Out of every entry rule this project has built, at every target and stop size, with the robot's
own throughput rules applied.

### The closest thing to a survivor

COMEX-open momentum (a 30-minute move after 13:30 UTC, followed not faded), $50 target / $5 stop:

| Era | Trades | Per month | Accuracy | Net | Per basket | Per month |
|---|---|---|---|---|---|---|
| 2009-2021 | 3,017 | 21.2 | **49.29** | −$2,894 | −$0.96 | −$20.40 |
| 2021-2023 | 773 | 21.5 | 52.01 | +$400 | +$0.52 | +$11.10 |
| 2024-2025 | 384 | 21.3 | 53.39 | +$518 | +$1.35 | +$28.80 |

It makes money in the last 4.5 years. It was a **coin flip for the twelve years before that** — 49.29 out
of 100, worse than guessing. That is what noise looks like when you go hunting through 432 combinations.
It earns about $17 a month at 0.10 lots on 21 trades.

---

## 3. The two new entries I added — both collapse

The inventory agent found two rules nobody had re-tested at other shapes. Both were worth checking. Both
fail badly.

**M1 RSI fade** — scored 52.77 in the old work, the highest of anything in this project:

| Era | Trades | Accuracy | Its own bar | Per basket |
|---|---|---|---|---|
| 2009-2021 | 72,008 | 52.02 | 63.72 | −$0.62 |
| 2021-2023 | 21,171 | 50.58 | 64.90 | −$1.02 |
| 2024-2025 | 12,916 | **48.44** | 65.88 | −$1.36 |

On recent data it is **below a coin flip**. And look at the bar: 65 out of 100. The minutes when RSI is
extreme are violent minutes, so the same target and stop are much harder to hit in the right order. It
needs 65 and gets 48.

**M1 break of structure, faded** — the one that was above 50 in 17 of 17 years:

| Era | Trades | Accuracy | Its own bar | Per basket |
|---|---|---|---|---|
| 2009-2021 | 62,998 | 50.64 | 64.05 | −$0.88 |
| 2021-2023 | 18,836 | 50.75 | 64.29 | −$1.00 |
| 2024-2025 | 11,147 | **47.99** | 64.87 | −$1.23 |

Same story. Real but tiny, and the bar on its own minutes is far above it.

**This is the trap in the whole project:** every entry that picks "interesting" minutes picks *volatile*
minutes, and volatile minutes have a much higher bar. The edge and the cost arrive together.

---

## 4. Sessions — how many trades, and which is best

Spike fade p99.5, $30 target / $10 stop, 2021-2025:

| Session (UTC) | Trades | Per day | Per month | Net | Per basket |
|---|---|---|---|---|---|
| Late 20-24 | 295 | 0.21 | 5.5 | **+$86** | **+$0.29** |
| Asian 00-07 | 817 | 0.59 | 15.1 | −$189 | −$0.23 |
| London 07-12 | 866 | 0.62 | 16.0 | −$693 | −$0.80 |
| Overlap 12-15 | 4,566 | 3.28 | 84.6 | −$6,283 | −$1.38 |
| New York 15-20 | 1,683 | 1.21 | 31.2 | −$2,881 | −$1.71 |

**Asian is better than London**, and both are far better than the London/New York overlap.

The pattern is consistent across entry rules: **the busiest session is the worst one.** The overlap
(12:00-15:00 UTC) takes more than half the trades and loses the most per trade. New York is the worst of
all per trade.

**Do not get excited about the Late row.** It is 0.21 trades a day — five a month — and hours 20-24 UTC
include the broker's daily break, which this project has already had one false finding die in. The
handoff lists the late-session lead as dead for exactly this reason.

**The honest session read:** if you traded this at all, you would skip the overlap and New York and keep
Asian and London. That cuts the bleeding roughly in half. It does not make it positive.

---

## 5. Where this leaves it

Running every real entry at every exit shape, with the robot's own trade-throughput rules, the best the
exits can do is take the loss from **−$1.93 a basket down to about −$1.10.**

They never reach zero. Not in one combination out of 432, across three independent time windows.

The exits were never the problem, and now they have been tested to exhaustion against the real entries
rather than a coin flip. The entry is the problem, and the two entries that looked most promising both
turn out to pick minutes where the bar rises faster than their skill does.
