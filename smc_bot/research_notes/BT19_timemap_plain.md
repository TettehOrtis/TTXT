# The time map — how long a trade takes, how far it goes, and when to cut it

**Date:** 2026-10-05
**Everything here is at 0.10 lots.** 380,378 baskets, 2009 to 2025, our costs ($1.10 round-trip
commission). Raw tables: `smc_bot/results/BT19/`. Code: `smc_bot/bt/bt19_timemap.py`, `bt19_report.py`.

I recorded the exact minute every dollar level was touched, up and down. That was never stored before —
the old work measured how far a basket travelled, never when.

---

## First, so you can trust it: these numbers match the old ones

| Check | Old number | This run |
|---|---|---|
| Winners killed by a $10 cut | 39.3% | 38.6% |
| Win rate, 2021-2025 | 88.9% | 89.8% |
| Time to win | 7 min | 7 min |
| Time to lose | 89 min | 105 min |
| Money per basket | −$1.89 | −$1.93 |

Same engine, same costs. A $7 win still pays **+$5.90** after commission. A $70 loss still costs **−$71.10**.

**One thing to know:** the old feed (2009-2021) is about 2.5 times slower than the recent one
(2021-2025). The screenshots you had were the recent feed only. Everything below is the recent feed
unless it says otherwise.

---

## 1. How long does a trade take?

| Target | Typical time to win | 3 out of 4 done by |
|---|---|---|
| **$7 (shipped)** | **7 min** | 31 min |
| $15 | 29 min | 90 min |
| $30 | 80 min | 207 min |
| $50 | 158 min | 356 min |

**The $70 loss takes 105 minutes typically.** Three out of four losers are done inside 4 hours.

Wins are fast, losses are slow. A win takes 7 minutes. A loss takes 15 times longer.

---

## 2. How far up can a trade go?

If nothing closes it, over 24 hours:

- A quarter of baskets reach **$50**
- Half reach **$109**
- One in ten reaches **$328**

**But you don't need 26 hours to get a good chunk of it:**

| Level | Share that reach it | Typical time |
|---|---|---|
| +$7 | 96% | 9 min |
| +$15 | 92% | 37 min |
| +$30 | 85% | **1.9 hours** |
| +$50 | 75% | 3.8 hours |
| +$100 | 53% | 7.5 hours |

**$30 is reachable by 85 out of 100 baskets in under 2 hours.** The 26-hour hold was only needed to catch
the very top, and the top is where the data is least trustworthy anyway.

---

## 3. How deep does a winner dip before it recovers?

Out of baskets that go on to win at $7:

| Dips to | Share of winners | Typical minute it happens |
|---|---|---|
| −$5 | 55% | 1 min |
| −$10 | 37.5% | 3 min |
| −$20 | 20% | 13 min |
| −$30 | 11.8% | 27 min |
| −$50 | 3.8% | 62 min |

Most winners dip shallow and dip **fast** — inside the first few minutes.

---

## 4. The kill rule — the big one

The idea being tested: *"when a trade has gone this much into loss, we know it's not coming back, so cut
it."*

### The clock tells you nothing.

A basket is $30 down and still open. What are its chances of coming back?

| It got $30 down after... | Chance it still wins |
|---|---|
| under 5 min | 48.8% |
| 5–15 min | 51.8% |
| 15–30 min | 51.5% |
| 30–60 min | 51.8% |
| 1–2 hours | 50.0% |
| 2–4 hours | 51.5% |
| over 4 hours | 52.5% |

**It's the same number every time.** A basket $30 down in 5 minutes and one $30 down after 4 hours have
identical chances. The same flatness holds at every depth:

| Still open and down... | Chance it comes back | Changes with time? |
|---|---|---|
| −$10 | 77% | No |
| −$20 | 64% | No |
| −$30 | 51% | No |
| −$50 | 24% | No |

**Depth tells you a lot. Time tells you nothing at all.**

This is why the earlier time-stop testing came out flat across 90 combinations — there was never anything
there to find. Knowing a trade has been open a long time adds zero information about whether it will
recover. **Stop looking there.**

### Where cutting actually lands

Every stop level loses money. The whole range from a $5 cut to a $70 cut is about 40 cents a basket.

| Cut the loss at | Win rate | Money per basket | Winners killed |
|---|---|---|---|
| $5 | 37.7% | −$1.58 | 58.0% |
| $10 | 55.1% | −$1.73 | 38.6% |
| $15 | 65.1% | −$1.79 | 27.5% |
| $20 | 71.4% | −$1.82 | 20.5% |
| $30 | 79.1% | −$1.84 | 11.9% |
| $50 | 86.4% | −$1.84 | 3.7% |
| $70 (shipped) | 89.8% | −$1.97 | — |

Cutting earlier is slightly better. It is still a loss at every single level.

---

## 5. Sessions

| Session (UTC) | Share of chances | Per basket |
|---|---|---|
| Asian 00–07 | 31% | −$1.97 |
| London 07–12 | 22% | −$1.94 |
| Overlap 12–15 | 13% | −$2.72 |
| New York 15–20 | 22% | −$1.73 |
| Late 20–24 | 11% | −$1.68 |

Asian hours give the most chances. New York and Late give the least-bad money. Every session loses, and
picking by session has already failed twice in this project (BT7, BT14). This is a head count, not a lead.

---

## 6. What this actually tells us

The useful thing isn't the kill rule — it's the shape.

| Shape | Needs direction right | Typical trade |
|---|---|---|
| $7 target / $70 stop (shipped) | 62 in 100 | 7 min |
| $15 / $20 | 55 in 100 | 15 min |
| **$30 / $30** | **52.8 in 100** | **52 min** |
| $50 / $30 | 52.8 in 100 | 106 min |

**$30 target with a $30 stop needs the direction right 52.8 times in 100.** That is the lowest bar of any
setting tested.

And the fade-spike entry scores **52.1 to 52.6**.

That is the closest the two halves have ever come. It is still slightly short, and slightly short is still
short. But it is the only combination where the gap is under one point, and **nobody has run those two
together yet.**

---

## Warning when comparing tables

These numbers include **our** $1.10 commission. The older required-accuracy table in
`HANDOFF_sniperspark.md` §3 used the seller's costs, which have **no commission**. The same shape looks
about 2 points easier on his account. **Never put the two tables side by side.**
