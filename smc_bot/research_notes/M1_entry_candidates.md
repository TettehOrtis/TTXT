# M1/M5 entry candidates for gold: what has real evidence

Researched 2026-10-05 by web search. Target: pick direction right ~53 times in 100 on M1 OHLC (tick volume only), 10-20 minute holds, ~10+ trades/day.

## Bottom line (read this first)

- I found **no source that shows a 1-5 minute direction rule on gold beating a coin flip after costs.** Nearly all the solid research is on stocks and stock-index futures, or on longer windows (30 minutes to a day).
- The best-documented effects are real but small, and the one independent re-test I found says the gain was about the same size as trading costs.
- Ideas with a real "someone is forced to trade" reason fire only 1-3 times a day. Ideas that fire 10+ times a day have the weakest reasons. That is the core tension.
- So 53% is plausible only on a **filtered subset of moments**. The user must accept fewer trades or accept 50%.

## Ranked shortlist

Ranked mostly by the strength of the "who is on the other side" story.

| # | Technique | Why it should work | Evidence quality | Trades/day |
|---|---|---|---|---|
| 1 | Fade a very big 1-minute move | A spike is someone who must trade now; patient traders get paid when price snaps back | Medium (stocks, not gold) | 1-5 depending on threshold |
| 2 | Earlier move continues later (intraday momentum) | Hedgers and day traders must buy into rises and sell into falls | Medium-strong for stocks/futures, thin for gold | 2-6 |
| 3 | First-candle direction at a session open | Opening imbalance between buyers and sellers persists | Medium; costs ate it | 2-3 |
| 4 | Price above/below VWAP | Big players trade to VWAP and keep going while an imbalance lasts | Weak for gold | Constant (a state) |
| 5 | Hour-of-day lean (Asia up / US down) | Physical buyers in Asia vs Western selling | Weak, partly unverified | Continuous |

## Candidates in detail

### 1. Fade the extreme 1-minute move
- **Mechanism:** a sudden one-minute spike is usually a forced order, not new information. Patient traders take the other side and are paid when price returns.
- **Evidence:** Rif and Utz, *Quarterly Review of Economics and Finance*, 2021 (exists; I read only the abstract via search). Nasdaq-100 stocks, nanosecond data. After a one-minute drop in the worst 1-in-1,000 minutes, about **31% of the drop reversed in the next minute**. Strongest in the most liquid, largest stocks. Costs not stated in what I saw. Gold not tested.
- **Rule from source:** M1 return in the 0.1% tail of trailing M1 returns; enter against it at the next bar open. On gold that is only ~1.4 per day, so reaching 10/day means loosening it, and the source gives no numbers for looser levels.
- **Accuracy stated:** none (31% is how much of the move comes back, not how often direction is right).
- **Fails because:** on gold, big minutes are often news (US data, Fed) and keep going. The spread also widens exactly when you fade.

### 2. Intraday momentum
- **Mechanism:** option market makers and leveraged-ETF funds must trade in the direction price already moved to stay hedged, pushing it further.
- **Evidence:** Gao, Han, Li, Zhou, *Journal of Financial Economics* (verified): S&P 500 ETF 1993-2013, first half-hour return predicts last half-hour return; stronger on volatile days and news-release days. Baltussen, Da, Lammers, Martens, *JFE* 2021 (verified): 60+ futures incl. commodities and currencies, 1974-2020, rest-of-day return predicts the last 30 minutes. A search snippet says Chinese gold futures show first-half-hour predicting later half-hours (UNVERIFIED, paper not opened). No hit-rate figures found; no 10-minute-hold tests.
- **Rule from source:** sign of return from session open to 30 minutes before the close predicts the final 30 minutes. For gold I would test the first 30 minutes after the London (~08:00 UK) and COMEX (~08:20 New York) opens, then trade the next 30.
- **Fails because:** it is tied to a market close that gold lacks, and the source horizon is 30 minutes, not 10.

### 3. First-candle direction at a session open
- **Mechanism:** the first minutes of a session show which side has the unfilled orders.
- **Evidence:** Zarattini and Aziz, SSRN 4416622, "Can Day Trading Really Be Profitable?" (verified): trade the direction of the first 5-minute candle; 24% win rate with a 10x profit target, so it is a payoff trick, not an accuracy claim. Independent re-test on five indices 2015-2026 (MQL5 blog, not peer-reviewed): the first candle **did** beat random direction by a real margin, but that margin was **about equal to spread plus slippage**; net zero on NQ, negative on 4 of 5.
- **Rule:** at London and COMEX opens, take the colour of the first 5-minute candle, enter at the open of the second. Gold not tested in the sources.
- **Fails because:** costs, and only 2-3 trades per day.

### 4. VWAP side and "noise area"
- **Mechanism:** big players trade to match VWAP; price leaving its normal daily range signals imbalance.
- **Evidence:** Zarattini and Aziz, "VWAP: The Holy Grail for Day Trading Systems", SSRN 4631351 (exists): long above VWAP, short below, QQQ/TQQQ 2018-2023, commissions included, a bull-market stock-index result. Barbon, Zarattini, Aziz, "A Profitable Day Trading Strategy For The U.S. Equity Market" (exists): 14-day "noise area", 7,000+ US stocks 2016-2023. I could not verify effect size or cost treatment for either.
- **Rule:** VWAP from session start using tick volume; long if close above, short if below; or trade breaks of the prior-14-day average-move band.
- **Fails because:** gold has no real volume, so VWAP is a guess, and a 24-hour market has no natural day start.

### 5. Hour-of-day lean
- **Mechanism:** physical demand in Asia lifts gold in Asian hours; Western hours give it back. Around the alleged London-fix manipulation years, price tended to drop at fix times.
- **Evidence:** a "hat-shaped" pattern paper (gold rises in eastern hours, falls in the rest) seen only in search summaries; I could not retrieve it or confirm authors, so UNVERIFIED. A news article: gold up 12.9% in Asian hours vs down 15% in North American hours in H1 2026 (one period). Iwatsubo, Watkins, Xu, *Journal of Commodity Markets* 2018 (verified): Tokyo gold dominated by uninformed trading, New York has informed trading; says nothing on direction.
- **Rule:** compute average direction of each hour 2009-2025, split into two halves of years, keep only hours where both halves agree.
- **Fails because:** a drift of cents per hour is far below one-minute noise; it can tilt odds, not decide a trade.

## What is known about gold by hour

| Time / event | What I could verify |
|---|---|
| Asia hours | Slow drift, thin trade; tendency to rise (unverified paper) |
| London open (~08:00 UK) | Volume and range jump, spreads tighten; often sets a day extreme. **No directional claim**; one source says outright "None of this predicts anything" |
| London PM fix (3pm UK) | Caminschi and Heaney, *Journal of Futures Markets* 2014 (verified): volume and volatility spike right after the fix starts; trades in its first minutes predicted fix direction (some cases over 90%). Needs real trades, which we lack. I believe the fix was redesigned in 2015 (my recollection, not searched), so it likely no longer works |
| COMEX open / US data | More volatility and more informed trading in New York hours; **no persistent direction found**. Gao et al. find momentum stronger on data days |

I found **no verified hour with a persistent, tradable direction.** The good-trade hours are the volatile ones (London open, COMEX open, US data), where moves are large relative to costs, but which way is not predictable from anything I found.

## Looks good, is not

- **Bid-ask bounce (negative autocorrelation on M1):** well documented in FX, but it is price flipping between buy and sell prices; harvesting it costs the same spread. One study found the pattern changes too fast to use.
- **VPIN / bulk volume classification (guessing buyers vs sellers from bars):** published method, but Andersen and Bondarenko found it worse than simple tick rules, its forecasts mostly an artefact. Avoid.
- **Squeeze / volatility contraction:** says a big move is coming, not which way. One unverified blog test of 399 squeezes: 49.6% direction unfiltered; the 71% for "graded" setups is not credible without out-of-sample proof.

## Test order

1. Overshoot fade (1), thresholds as percentile of trailing M1 moves scanned from 0.1% to 5%, to see accuracy vs frequency.
2. Session-open first-candle and 30-minute momentum (2, 3) at London and COMEX opens.
3. Hour-of-day table as a filter, not an entry.
4. VWAP last.

## Honest read

Published effects at this horizon are one to three points above a coin flip before costs, and shrink or vanish after spreads. 53% is only just reachable on selected moments (big spikes, session opens), which happen far fewer than 10 times a day. Rules that fire often should be expected to stay near 50%, as the four already tested did. Everything above is from stocks or indices unless stated; none is shown on gold M1 bars, so each is a hypothesis to test, not a proven edge.
