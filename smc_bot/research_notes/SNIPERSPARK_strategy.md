# SNIPER_SPARK V1.0 — strategy specification for testing

A commercial MT5 gold robot the user was shown on 2026-10-05. Spec assembled from the product site, screenshots of its
settings pages, live screenshots of it trading, and the user's own answers from the owner.

---

## 1. The strategy, confirmed (this is the model we test)
Gold (XAUUSD) only.

1. Decide direction from the **M15** chart using undisclosed "built-in indicators".
2. Drop to the **M1** chart and wait for a pullback / retracement entry (condition undisclosed).
3. Open a **group of positions all at once, all at the same price** ("a basket").
4. Watch the group's **combined floating money**. Close **all** of them when it reaches **+$T** (basket profit target)
   or **−$S** (basket stop loss).
5. Wait **60 s** (cooldown), then look for the next entry and open a **fresh** basket.
6. After **2 consecutive losing baskets** in one direction, pause that direction.
7. **Daily cycle:** stop trading for the day on reaching the daily profit target or the daily loss limit. Start fresh
   the next day.

**Confirmed 2026-10-05: it does NOT add positions to a basket that is already losing.** It opens the group once,
manages it, closes it, and only then opens a new one. There is no martingale and no averaging down.

### What that reduces to
> **One trade of the combined size, opened at one price, closed when its floating profit reaches +$T or its floating
> loss reaches −$S.** Direction from an undisclosed M15 rule, timing from an undisclosed M1 rule.

Positions opened at the same price with the same stop and target are mathematically **identical to one position of the
combined size**. "5 positions", "baskets" and "risk split across positions" are presentation, not arithmetic. What the
basket genuinely changes is the **trade history**: losers are closed together with winners as one green basket, so the
history shows an unbroken run of small wins and never shows a losing trade on its own.

## 2. Settings (defaults, for the recommended $300 account)
> **SUPERSEDED 2026-10-05.** The $7 target / $70 stop below is the shipped default and the *worst* shape tested.
> We now use **target $200 / stop $150** (in gold price: 20.00 / 15.00), which drops the required directional
> accuracy from 62.3% to 50.6%. See the starred section at the top of `SNIPERSPARK_results.md`. The table below is
> kept only as the reference point the tests were measured against.
| Setting | Default | What it does |
|---|---|---|
| Lot Size | 0.02 | size of each position |
| Positions Per Entry | 5 | positions opened simultaneously per signal |
| Cooldown Seconds | 60 | wait before considering another basket |
| Trend Timeframe (TrendTF) | M15 | timeframe used to decide direction |
| Entry Timeframe (EntryTF) | M1 | timeframe used to time the pullback entry |
| Reward Multiplier | 2 | stated as "risk $1 to make $2" |
| Block Direction After SL Hits | 2 | pause a direction after 2 losing baskets in a row |
| **Basket Profit Target ($)** | **7.0** | close ALL positions at this combined profit |
| **Stop Loss USD ($)** | **70.0** | close ALL positions at this combined loss |
| Daily Profit Target ($) | 200.0 | stop for the day (0 = off) |
| Daily Loss Limit ($) | 50.0 | stop for the day (0 = off) |
| Run Mode | MODE_LIVE | demo or live |

"Dynamic lot sizing" means **lot scales with account balance** (a $50 account trades smaller than a $300 one). It does
**not** mean lot grows inside a basket or after a loss.

## 3. The money maths — why the target:stop ratio decides everything
At the $300 setup: 0.02 × 5 = **0.10 lots = 10 oz. A $1 move in gold = $10.**
- Target $7 → gold must move about **70 cents** your way.
- Stop $70 → gold moving about **$7** against you closes the basket.

Every basket is one bet: **will gold move 70 cents my way before it moves $7 against me?**

**On a coin-flip market that bet wins exactly 7 / (7 + 0.7) = 90.9% of the time, and makes exactly zero.** The 10:1
loss-to-win size cancels the 10:1 win-to-loss frequency. This is the same barrier geometry BT14 measured.

**So the entry rule must beat a coin flip by enough to pay the costs, or the system loses.** It does not need to be
good — it needs to be measurably better than random. That is the entire question, and it is testable.

Structural problems visible without any testing:
- **$70 stop on a $300 account = 23% of the account per basket.** Three in a row ≈ 70% gone.
- **The daily loss limit ($50) is smaller than one basket's stop ($70)**, so a single basket blows past it. As shipped,
  the daily limit cannot do its job. If the user runs this, the daily limit must exceed one basket's stop.
- **Daily profit target $200 on a $300 account = 67% in a day** (~29 clean winning baskets). Not a real target.
- "Block direction after 2 SL hits" is an admission that losses cluster — what happens when the structure fights a trend.
- Shape = many small wins, rare very large losses: a long attractive equity curve followed by a cliff.

## 4. Live screenshots — VERIFIED genuine (2026-10-05, ~08:24 UTC)
Checked against our own Exness XAUUSD feed: gold traded **4159–4170 between 08:00 and 08:45 UTC** on 2026-10-05
(full 00:00–12:00 range 4124.41–4170.50). The screenshots show 4167.228 / 4167.310 / 4168.131 at a clock of 08:24.
**Prices, date and time are real — the EA genuinely trades live.** That verifies it runs. It verifies nothing about
whether it makes money.

What they showed:
1. **All positions open at the same price** — ~14 rows at 4167.228; another shot ~13 rows at 4167.310. Not laddered.
2. **One shared S/L and T/P**: S/L 4150.311, T/P 4218.883 vs entry 4167.228 → stop **$16.92** away, target **$51.66**
   away (~1:3). This matches neither "Reward Multiplier 2" nor the $7/$70 pair. The basket dollar target closes the
   trade long before 4218 is reached, so the per-position T/P is largely decorative.
3. **~14 positions per entry, not 5** — a higher Positions Per Entry on a larger account. Consistent with a basket
   opening once and being replaced only after it closes.
4. **Each position opened at exactly −$0.36** — that is the spread at the moment of opening.
5. **That account is ~$3,194**, not $300 — scaled-up values, not the "verified for $300" defaults.

**The decisive number, straight from the screenshot:**
14 × −$0.36 = **−$5.04 of spread paid the instant the basket opened.** Against the documented $7 target that is **72%
of the whole reward gone before gold moves at all** — gold must produce ~$12 gross to deliver a $7 win. That account is
larger so its target is presumably scaled too; **the ratio of cost to target is what must be measured.**

## 5. Evidence available
**None.** No backtest, no verified track record, no win rate, no audited statement. Nothing that can be checked.
A real screenshot of winning trades is not evidence — this structure produces winning screenshots almost every day,
by design, right up until it does not.

## 6. What is still unknown — and why it does not block the test
Unresolved: (a) which indicators set the M15 trend, (b) what exactly counts as the M1 "retracement" entry.
Both are deliberately undisclosed and we will not learn them without the compiled EA.

**This does not block us.** With a small target against a large stop, the outcome is dominated by the target:stop
ratio and the cost per basket; the entry rule can only shift the odds. So we bracket it: test entry rules from
*random* up to *one that cheats and sees the future*. If the cheating entry cannot make money after costs, no entry
rule can and the secret is irrelevant. If it can, we measure exactly how good an entry would have to be, and then
judge whether the real one is plausibly that good.

## 7. Test design (→ becomes BT15, needs a pre-registered brief before any run)
**The user's setup, agreed 2026-10-05: raw-spread account (their Exness Zero, real measured costs and commission —
not guesses), starting balance $300–400, gold only.**

Test the structure on ~20 years of real XAUUSD data (`data_zero` 2021+, `data_duka` 2005–2025):
- **Sweep the basket target and stop** across a range rather than assuming $7/$70 — show which combinations survive,
  instead of picking a number for the user.
- **Ladder of entry rules**: random entry (the floor, i.e. what the structure alone does) → plausible readings of
  "M15 trend + M1 pullback" (several, since the real rule is secret) → a deliberately strong entry → **a cheating
  entry with perfect knowledge of the future** (the ceiling).
- Include the real mechanics: 60 s cooldown, block-direction-after-2-losses, daily profit target and daily loss limit
  (the user's own values, **not** the default $200), and flat-before-weekend.
- **Count every variant tried** and apply the BT14 bar to any positive result.

### How results get reported — the user's framing
The user operates this **day by day**, so report it that way, in dollars, not in R:
- what a typical day makes or loses
- how many days win vs lose
- best day, worst day
- worst run of losing days in a row
- what a month looks like

and only then the long-run total. Testing many days is not a different question from the user's daily one — it is the
only way to learn what an average day is actually worth.

**Note on the daily limits (established 2026-10-05):** they do not create profit. Stopping after a $20 win does not
make that $20 bigger; it is put back at risk the next day. The daily **loss** limit is genuine protection (it caps how
bad one day can be) and the daily **profit** target genuinely reduces total cost paid (trading less bleeds less) —
but neither turns a losing system into a winning one. They change the ride, not the destination.
