# SNIPER_SPARK — what the testing found (plain English)

Full technical reports: `results/BT15/report.md`, `addendum_timestop.md`, `addendum_depth.md`.
The strategy itself is written up in `SNIPERSPARK_strategy.md`.

**What was tested:** 380,378 baskets on real gold prices, minute by minute, 2009 → June 2025, with the real
spread and commission from a raw-spread account. Not a simulation of a simulation — every basket was opened at a
real price and followed minute by minute until it hit its target or its stop.

---

---

# ⭐ THE SETTINGS WE NOW USE (replaces the $7 / $70 defaults)

The shipped $7 target / $70 stop is the worst shape tested. **We do not use it again except as a reference point.**
After ~150 combinations (stop $10→$150, target $3→uncapped, clocks 5 min→4 h), the best shape found is:

| Setting | Shipped | **USE THIS** |
|---|---|---|
| **Profit target** | $7 | **$200** |
| **Stop loss** | $70 | **$150** |
| Needs to be right | 62.3% | **50.6%** |
| Baskets that close green | 88.8% | 45.8% |

Figures above are at 0.10 lots (0.02 × 5 positions). In gold price terms that is a **20.00 target and a 15.00 stop**,
which is what actually matters — the dollar amounts just scale with lot size.

### What it pays, by how good the entry is
Money **per basket**, at 0.10 lots:

| Entry accuracy | $7 / $70 (shipped) | **$200 / $150 (ours)** |
|---|---|---|
| 50% (coin flip) | −$1.91 | **−$1.49** |
| 55% | — | **+$10.55** |
| 60% | — | **+$22.43** |

The runners-up, for the record:

| Target | Stop | Needs | at 50% | at 55% | at 60% |
|---|---|---|---|---|---|
| **$200** | **$150** | **50.6%** | −$1.49 | **+$10.55** | **+$22.43** |
| $200 | $70 | 50.8% | **−$1.06** | +$5.47 | +$12.13 |
| $100 | $150 | 51.3% | −$2.33 | +$6.85 | +$15.86 |
| $100 | $70 | 51.5% | −$1.90 | +$4.61 | +$11.14 |
| $50 | $70 | 51.9% | −$1.88 | +$3.11 | +$7.97 |
| $30 | $70 | 53.0% | −$1.84 | +$1.21 | +$4.18 |

$200/$70 loses the least if the entry turns out to be exactly a coin flip; **$200/$150 pays far more if the entry has
any real skill at all.** Since the whole point is to find an entry with skill, $200/$150 is the one to use.

### Sizing it for a $300–400 account
At 0.10 lots a $150 stop is 43% of a $350 account — far too much. Use **0.01 lots** (the minimum, 1 oz):
- target 20.00 gold → **+$20 a win**
- stop 15.00 gold → **−$15 a loss** (4.3% of a $350 account — sane)
- all the per-basket figures above divide by 10 (so +$10.55 becomes **+$1.06** per basket at 55%)

### Two honest warnings on these settings
1. **They hold much longer.** A 20.00 gold target takes far longer to reach than 0.70. Baskets will run for hours and
   often overnight. **Our simulator charges no overnight financing**, and gold costs money to hold overnight in *both*
   directions (BT9 established this). The real numbers will be somewhat worse than the table above. This must be
   modelled before anything goes live.
2. **It no longer looks like a scalper.** Only 45.8% of baskets close green instead of 88.8%. Expect long losing
   runs and a far bumpier ride, even if the money is better. Do not panic and change it after a bad week.

### What this changed, and what it did not
Moving from $7/$70 to $200/$150 dropped the requirement **from 62.3% to 50.6% — an 11.7 point improvement.** That is
a huge gain and it came entirely from the user's own insight that capping wins at $7 while allowing $70 losses is
backwards.

**But at a coin-flip entry it still loses**, because 50.6% is where it reaches zero, and 50.0% is below that. The
settings are now as good as they can get. Everything from here depends on the entry.

---

## 1. The one-sentence answer
**As it is sold, it loses about $27 a day on a $350 account and empties it in about 13 trading days** — while
winning almost 9 out of every 10 trades.

## 2. Why it wins so often and still loses
The settings close a basket at **+$7** profit or **−$70** loss.

| | |
|---|---|
| 9 wins × $7 | +$63 |
| 1 loss × $70 | −$70 |
| fees on 10 baskets | −$16 |
| **Total over 10 baskets** | **−$23** |

One loss is ten times bigger than a win, so it eats nine wins and more. The win rate looks spectacular because
the target is close and the stop is far away. That is a design choice, not a market fact.

## 3. Measured, not estimated
Out of 71,075 baskets in one full run:

| | |
|---|---|
| Won | 63,216 (**88.9%**) |
| Lost | 7,859 (**11.1%**) — about 1 loss in every 9 |
| Average win | **+$5.89** |
| Average loss | **−$64.49** |
| Per basket | **−$1.89** |
| Total | **−$134,462** |

## 4. How long trades take
| Hold time | Share |
|---|---|
| under 2 min | 22.6% |
| 2–5 min | 15.4% |
| 5–10 min | 12.0% |
| 10–20 min | 11.8% |
| 20–60 min | 17.0% |
| 60–120 min | 8.3% |
| over 2 hours | 12.9% |

Half finish inside 10 minutes; 13% drag past 2 hours. A typical win takes 7 minutes, a typical loss 89 minutes.

## 5. How deep a winner dips before recovering
Of 337,938 baskets that eventually won:

| | Dipped no more than |
|---|---|
| 1 in 4 | $2.47 |
| Half | **$6.84** |
| 3 in 4 | $16.90 |
| 9 in 10 | $34.00 |
| 19 in 20 | $46.70 |

## 6. Where should the loss be cut? (the $70 is indeed too big — but it barely matters)
| Cut at | Winners killed | Losers saved | Money per basket |
|---|---|---|---|
| $5 | 58.8% | 97.5% | — |
| **$10** | 39.3% | 95.7% | **−$1.82** ← best of all |
| $15 | 28.1% | 94.2% | −$1.86 |
| $20 | 21.0% | 93.0% | −$1.88 |
| $25 | 15.9% | 92.1% | −$1.87 |
| $30 | 12.3% | 91.2% | −$1.96 |
| $50 | 4.1% | 88.4% | −$1.93 |
| $70 (as sold) | — | — | −$1.91 |

Cutting at $10 is better than $70 — by **9 cents a basket**. It saves 96% of losers but kills 39% of winners
(132,638 of them), so the two nearly cancel. **The stop size is not what is wrong with this robot.**

## 7. Does a time limit help? (90 combinations tested — no)
| Clock | Win rate | Avg win | Avg loss | Money per basket |
|---|---|---|---|---|
| 5 min | 45.0% | +$4.52 | −$7.00 | −$1.81 |
| 20 min | 57.4% | +$5.35 | −$11.55 | −$1.85 |
| 60 min | 69.5% | +$5.73 | −$19.37 | −$1.92 |
| none | 88.8% | +$5.89 | −$64.00 | −$1.91 |

A 5-minute clock cuts the average loss from −$64 to −$7 — a huge improvement — but the win rate collapses from
89% to 45%. Every one of the 90 cells lands between −$1.79 and −$1.92.

**Why:** 81% of losses are still open at 20 minutes, but **76% of everything still open at 20 minutes goes on to
win.** A clock cuts roughly three winners for every loser it saves.

## 8. How far can a basket run in profit?
With no cap at all, **half of all baskets run past +$58.90** at some point, and a quarter past **+$151**.
So the $7 cap really is throwing away large moves.

But the losers were never near-misses: half of them never showed more than **+$2.60** profit before dying.

## 9. Bigger targets genuinely help — and then hit a wall
"Needs" = how often it must pick the right direction to break even.

| Target | Needs | Money per basket at a coin flip (50%) |
|---|---|---|
| $7 (as sold) | 62.3% | −$1.91 |
| $10 | 58.5% | — |
| $15 | 55.7% | — |
| $30 | 53.0% | −$1.84 |
| $100 | 51.5% | −$1.90 |
| $200 | **50.6%** | **−$1.49** |
| no cap | 49.9% | +$0.14 (not real — see §10) |

**Raising the target from $7 to $200 drops the requirement from 62.3% to 50.6%.** That is a real, large improvement.

**But look at the right-hand column: every single one still loses at a coin flip.** "Needs 50.6%" does not mean
50% is close enough. 50.0% loses **$1.49 a basket**; 50.6% is the point where it reaches **zero**. The gap between
them *is* the cost.

And it never goes below 50%, because before costs a coin flip is exactly break-even at any target/stop pair.
50% is the floor, and the floor pays nothing.

## 10. The one combination that came out positive — and why it is not real
"No target at all, $70 stop" measured **+13 cents a basket**. Flagged honestly, and rejected because:
- it holds each basket **26 hours on average** — not a scalper any more
- the simulator **charges no overnight financing**, and gold costs money to hold overnight in *both* directions
  (BT9 established this). Once financed, it goes negative.
- the win rate falls to **28%** — losing 7 trades in 10
- statistically it is indistinguishable from zero

## 11. Two different numbers people confuse
- **Direction accuracy** = how often it picks the right way for gold. A coin flip is 50%.
- **Basket win rate** = how often a basket closes green. This is set by the target/stop shape, not by skill.

At $7/$70 a *coin flip* produces an **88.8%** basket win rate. At $200/$150 the same coin flip produces **45.8%**.
Same skill, totally different-looking results. A high win rate says nothing about whether a system makes money.

## 12. What the entry rules actually achieved
Four plausible readings of "M15 trend + M1 pullback" were built and measured:

| | Direction accuracy | Money per basket |
|---|---|---|
| Rule a (EMA50/200 + M1 EMA20) | 49.9–50.2% | −$1.75 to −$1.93 |
| Rule b (EMA200 + M1 RSI) | 49.6–51.2% | −$0.64 to −$2.22 |
| Rule c (M15 structure + retrace) | 49.7–50.2% | −$1.88 to −$2.69 |
| Rule d (MACD + M1 reversal) | 49.8–50.1% | −$1.76 to −$2.01 |

**All four are coin flips. All four lose, in every period.** This matches everything else this project has measured
(BT4, BT5, BT9, BT13, BT8b): no directional rule has ever beaten 50% on this data.

## 13. Dates and sessions
Stable everywhere — this is not about one period or gold's bull run.

| Period | Needs |
|---|---|
| 2009–2020 | 62.1% |
| 2021–2023 | 62.6% |
| 2024–mid 2025 | 62.7% |

Every individual year lands between 59% and 64%.

| Session | Needs |
|---|---|
| Asian | 63.9% |
| Overlap | 64.2% |
| London | 62.9% |
| New York | 59.6% |
| Late | 58.6% |

**London is not the best session** — it is one of the worse ones. And per the rule set before the test, no setting
is chosen on the basis of a session looking good: BT7 found no session edge at all, and BT14 showed "London beats
New York" appears in half of all charts built from random numbers.

## 14. The bottom line
The exits are now fully explored — stop size $10 to $150, target $3 to uncapped, clocks 5 minutes to 4 hours,
about 150 combinations in total. **The best of all of them loses 5–9 cents a basket less than the shipped settings.
None of them makes money.**

Only one thing is left that could make this robot work: **whether its secret entry beats a coin flip.**

- At 50% it loses, at every setting tested.
- At 55% with a $200 target it would make **+$10.55 a basket**.
- At 60% with a $200 target it would make **+$22.43 a basket**.

So the robot is not inherently hopeless — it is entirely a bet on an entry rule nobody has shown any evidence for.
Four honest attempts to build that entry all landed on 50%, and no directional rule in two weeks of this project
has ever done better.
