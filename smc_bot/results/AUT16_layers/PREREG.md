# AUT16 pre-registration, written 2026-10-16 BEFORE rounds 3 and 4 were run

Owner approved opening the sealed 2021+ data ONE time ("yes", 2026-10-16), and asked for many more variants, not three.

## What is already known (rounds 1 and 2, 75 variants)
Dropping the pullback (trend + a candle that closes with the trend) is the only change positive in all three open windows on the
live +$15/-$70 exit. It beats random trend-side entries by about $1.2-1.7 a trade. Everything else tried did no better.

## Round 3 (one dimension changed at a time, base = M15 price vs EMA200, no pullback, green/red 1-minute candle, live exit)
- TREND (11): M15 and M30 price vs EMA50 / EMA100; PX200 stacked with RSI, EMA50>200, EMA200 slope; M15 and M30 PX200 agreeing.
- TRIGGER (6): two candles in a row, big body (>= 0.5 x M1 ATR), strong close (top 30% of range), close on the right side of
  the M1 EMA20, close on the right side of the M5 EMA20, M1 and M5 candles both with the trend.
- EXIT (9): +15/-50, +15/-30, +10/-30, +20/-50, +30/-70, +30/-30, +50/-50, +50/-70, and the live +15/-70 with a 60-minute and a
  240-minute time stop. (The live +15/-70 with the 1-day stop is the base.)
- HOURS (4): London 07-12 UTC, overlap 12-16, New York 12-21, everything except Asia (07-21).
- SIDE (2): buys only, sells only.

## Round 4 (crosses)
Top 3 trends x top 2 triggers x top 3 exits from round 3, ranked by mean dollars a trade over the 3 open windows
(a variant needs at least 800 trades in window 1). Duplicates removed.

## Shortlist for the sealed test (rule fixed now)
From ALL rounds (1-4), every variant that, in EACH of the 3 open windows, has net > 0, profit factor > 1.00, and at least 1,000 trades.
If more than 12 qualify, keep the 12 with the best worst-window dollars a trade. Each shortlisted variant must also beat its
own random trend-side control (same exit, same hours, same side, 5 seeds) in each open window, or it is dropped.

## Sealed test (EXAM, 2021-01-01 onward), run ONCE, nothing changed after seeing it
Every shortlisted variant, on (a) the Dukascopy feed 2021 to its end and (b) the Exness Zero feed (the real broker) 2021 to its
end. PASS = net > 0 and profit factor > 1.00 on BOTH feeds, buys and sells both reported, and above the random trend-side control.
A variant that fails is reported as failed. No retuning, no second look.

## Honest count
About 135 variants will have been tried by the end of round 4. At this many tries, a few look good by luck alone. That is why
the sealed test, the random control and the three-window rule are used, and why a pass there is still only a reason to run a demo.

---
# Addendum 2026-10-17: news pause and evening block (written BEFORE running; open windows only, sealed data not touched)

Reason: the sealed test showed stops jumping past -$70 on US data releases and in the thin evening / Sunday-open hours.
Applied to the five variants the owner was shown: R4_M30PX100_T_E20SIDE_E50_70, R4_M15PX200_T_M5E20SIDE_E50_70,
R4_M15PX200_T_E20SIDE_E30_70, R4_M30PX50_T_M5E20SIDE_E50_70, R3_E_E30_70.

Five versions of each (25 runs on W1, W2, W3):
1. BASE: as before.
2. PAUSE: no new entries from 5 minutes before to 30 minutes after 08:30 New York and 14:00 New York, every day (DST handled by New York time).
3. FLATTEN: PAUSE plus no entries in the 20 minutes before each of those times, and any open trade is closed at market 5 minutes before them.
4. EVE: no new entries 20:00-24:00 UTC (covers the evening thin hours and the Sunday open).
5. PAUSE+EVE.  (FLATTEN+EVE also run.)

Judged on the open windows only, by: dollars per trade, profit factor, largest loss, max drawdown, trades a day, in EACH window.
A version counts as an improvement only if worst-window dollars a trade does not fall by more than $0.20 AND its largest loss or max drawdown is better in
all three windows. Nothing is run on 2021+ unless the owner asks, and if asked it is reported as a second look.

---
# Addendum 3, 2026-10-17: hour and weekday filters (owner's notes from the sealed-test report; written BEFORE running; open windows only)

The owner's notes (from reading a sealed-test report): hours 20-21 UTC lose money, trades closed by the Friday/holiday close
lose money (27% win), Friday has the weakest win rate, Sunday is negative, 16-17 UTC slightly negative. These ideas came from sealed data,
so they are tested ONLY on the open windows (W1, W2, W3) and judged there. The sealed data is not touched.

Same five variants as addendum 2. Six versions of each:
- BASE: no filter.
- EVE20_22: no new entries 20:00-22:00 UTC.
- EVE20_24: no new entries 20:00-24:00 UTC (already run, repeated for the table). Covers Sunday, because Sunday gold bars start about 23:00 UTC.
- EVE+FRI17: EVE20_24 plus no new entries on Friday from 17:00 UTC (stops entering trades the weekend close will cut short).
- EVE+NOFRI: EVE20_24 plus no entries on Friday at all.
- EVE+FRI17+H16: EVE+FRI17 plus no entries 16:00-17:00 UTC.
Improvement = worst-window dollars a trade is higher than EVE20_24's, AND the same holds in at least 2 of 3 windows, AND trades a day does not
fall by more than 40% versus BASE.

---
# Addendum 4, 2026-10-17: Monday start (written BEFORE running; open windows only)
Owner: no Sunday trading, first trade on Monday; decide later whether Monday starts at 00:00 or 07:00. Ghana time = UTC, Exness server time GMT+0.
Base for all versions: EVE20_24 (no entries 20:00-24:00 UTC, which also removes Sunday). Versions:
- MON00: first trade Monday 00:00 UTC (identical to EVE20_24 on the same data; repeated for the table).
- MON07: no entries Monday before 07:00 UTC.
- MON10: no entries Monday before 10:00 UTC.
Improvement over MON00 = worst-window dollars a trade higher AND higher in at least 2 of the 3 windows, with trades a day not down more than 10%.

---
# Addendum 5, 2026-10-17: SECOND LOOK at the sealed data (owner approved: "1", all five variants, not one)
The sealed 2021+ data was already looked at once (first exam). This is a SECOND look, so it is weaker evidence: the evening block and
the Monday start were suggested by what the first look showed. It is run once, nothing changed after, and reported as a second look.
Variants: the same five as addendums 2-4. Three versions each, on the Dukascopy feed AND the Exness Zero feed:
- BASE: no filter (repeat of the first look, as the anchor).
- EVE: no new entries 20:00-24:00 UTC.
- FINAL: EVE plus no entries on Monday before 07:00 UTC (so no Sunday, first trade Monday 07:00).
Also the random trend-side control (5 seeds, same hours/filters/exit) for FINAL.
Read-out: FINAL "holds up" if, on BOTH feeds, net > 0 and profit factor > 1.00 and dollars a trade above the control's best seed, for at least 4 of the 5 variants.
The filters "helped" only if FINAL dollars a trade is above BASE on both feeds for at least 4 of 5 variants. Otherwise say they did not.

---
# Addendum 6, 2026-10-17: Monday start sweep (owner asked about 00:00 / 05:00 / 06:00; written BEFORE running; open windows only)
Base: no entries 20:00-24:00 UTC (removes Sunday). Monday first-entry hour tested: 00, 02, 04, 05, 06, 07, 08, 09 UTC. Five variants, W1/W2/W3.
Reading rule: a start hour counts as best only if its WORST-window dollars a trade is the highest AND it also leads on average across the five variants.
If the differences between hours are smaller than the give-or-take (about +/-$0.3 in W2, +/-$0.7 in W1/W3), say it makes no real difference.

---
# Addendum 7, 2026-10-17: daily profit target and daily loss limit (owner wants more green days; written BEFORE running; open windows only)
Base: the five variants with the FINAL filters (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC), W1/W2/W3, 0.10 lot.
Rule replayed on the finished trade list, by entry date (UTC = Ghana time): once that day's closed profit reaches +X, or loss reaches -Y, no more new
entries that day. X in {none, 100, 200, 300}, Y in {none, 100, 200, 300} dollars at 0.10 lot = 16 combos. Approximation: skipped trades
would have shifted later same-day trades slightly; ignored and stated.
Read-out per combo: share of winning days, average day, worst day, worst week, total profit, max drawdown, trades a day.
A combo "helps" only if, in ALL three windows: winning days rise by >= 5 points, worst day improves, and total profit falls by no more than 25% versus no limit.

---
# Addendum 8, 2026-10-17: account test (owner request): starting balance $100 and $500, lot 0.1 / 0.2 / 0.3, all five variants, NO daily loss limit
Base: the five variants with the FINAL filters (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC). Fixed lot all the way (no compounding).
Each of the three open windows (W1 2009-10, W2 2011-16, W3 2017-20) is run as its own account starting at the full balance on its first day.
Dollar result per trade is the 0.10-lot result scaled by lot/0.10. The account is "blown" the first time the closed-trade balance is <= $0; trading stops there.
Reported: blown or not (and the date and trade number), final balance, lowest balance, biggest drop in %, trades a day.
Not modelled (stated): margin calls / broker stop-out before the balance reaches zero, the floating loss inside an open trade, and the sealed 2021+ data (not used here).

Addendum 8b (same day, written before running): the 0.1/0.2/0.3 lots mostly blow small accounts, so the same account test is also run at lots 0.01, 0.02, 0.03 and 0.05 (same trade lists, same rules).

---
# Addendum 9, 2026-10-17: account test on the SEALED 2021+ data (THIRD look, owner said "yes please"; written BEFORE running)
Weaker evidence than a fresh test: the sealed data has been seen twice; the filters were chosen after seeing it. Purpose here is only to show what an account would have lived through.
Five variants, FINAL filters (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC), both feeds (Exness Zero 2021-Oct 2026; Dukascopy 2021-end of its data).
Starting balance $100 and $500 (also $1,000 for reference); lots 0.01, 0.02, 0.03, 0.05, 0.10; fixed lot, no compounding, no daily limit.
Two views: (a) one continuous account from the first day; (b) rolling 12-month accounts started on the first day of every month (share blown, median final, median lowest, worst final).
Blown = closed-trade balance <= $0. Margin call and floating loss inside open trades are not modelled.

---
# Addendum 10, 2026-10-17: do the five variants work on the 9 FX majors? (owner: "yes please"; written BEFORE running; open windows only)
Pairs: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURGBP, EURJPY (Dukascopy feed, 2005-2020). Gold stays the anchor.
Same five variants (same trend, same trigger). Exit rebuilt from gold's own dollar exit into multiples of each pair's M15 ATR(14), fixed from gold's median
M15 ATR 2009-2020 (1.466 price units; see fx_k.json): +30/-70 -> target 2.122 x ATR, stop 4.700 x ATR; +50/-70 -> target 3.486 x ATR, stop 4.700 x ATR.
Targets and stops anchored at the fill. Costs: that pair's measured commission in pips (common.COMM) plus a 0.5 pip spread floor, paid once inside simulate. Dollars are at 0.10 lot.
Two versions each: BASE (no filter) and FINAL (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC). Random trend-side control for FINAL, 3 seeds, per pair.
Windows: W1 2005-2010, W2 2011-2016, W3 2017-2020. Sealed 2021+ is NOT used.
A variant "carries over to forex" only if FINAL is profitable (net > 0, profit factor > 1.00) in ALL three windows AND above the control's best seed for at least 5 of the 9 pairs.
Report pair by pair. A pooled number that comes from a single pair does not count.

---
# Addendum 11, 2026-10-17: sealed 2021+ test on EURUSD, GBPUSD, EURJPY (owner: "yes"; FIRST look at the sealed data for forex; written BEFORE running)
The three pairs were chosen from the open-window forex table (a selection, which is why a fresh out-of-sample test matters). Same five variants, same exits
(gold dollar exit converted to ATR multiples, fx_k.json), same costs, FINAL filters (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC) and BASE for comparison.
Feeds: Dukascopy 2021-end of data, and Exness Zero 2021-Oct 2026. Random trend-side control (3 seeds) per pair and variant.
A pair/variant PASSES a feed if FINAL net > 0, profit factor > 1.00, and dollars a trade above the control's best seed.
A variant "carries over to forex" if it passes on at least 2 of the 3 pairs on BOTH feeds. All results are reported, pass or fail. Run once; nothing changed after.

---
# Addendum 12, 2026-10-18: why does a gold rule fail on forex? (owner: "find out why"; written BEFORE running; open windows 2005-2020 only, sealed 2021+ untouched)
Pure measurement, no tuning. For gold and the 9 FX majors, per window (W1 2005-10, W2 2011-16, W3 2017-20; gold from 2009-03):
1. Size: median M15 range (ATR14) in dollars at 0.10 lot, and the round-trip trading cost as a share of that range.
2. Trend persistence: variance ratio of 1-minute returns at 5, 15 and 60 minutes (above 1 = moves continue, below 1 = moves reverse), and lag-1 correlation of 5-minute returns.
3. Event study: after the signal "M15 close vs EMA200 trend + a 1-minute candle with the trend" (variant Simple's entry), the average move in the trend direction over the next 5, 15, 30 and 60 minutes, in units of the M15 range, versus (a) the same trend with no candle condition, (b) the same trend after a candle AGAINST the trend (the pullback case). Reported with its give-or-take.
Question answered: does gold simply trend more at these time scales, and is its move big enough compared with cost, so that the same entry has an edge there and not on forex?

---
# Addendum 13, 2026-10-18: cooldown after a loss, and volatility-sized exits (owner: "run the cooldown test, the exit volatility too"; written BEFORE running; open windows only)
Base: the five variants with the FINAL filters (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC), W1/W2/W3, 0.10 lot, one trade at a time.
Cooldown: after a LOSING trade (closed below zero), no new entry for N minutes. N in {0, 1, 2, 3, 5, 10, 15}. Plus two streak versions: after 2 losses in a row wait 10 minutes; after 2 losses in a row wait 30 minutes.
Winning trades never trigger a wait. Every signal is simulated independently and the one-at-a-time and cooldown rules are applied afterwards in time order (exact, not an approximation).
Exits (target / stop, in multiples of the M15 ATR(14) at the signal, anchored at the fill): the calibrated pair (target 2.122 for the +$30 variants, 3.486 for the +$50 ones; stop 4.700), a tighter stop (3.5), a wider stop (6.0), and a bigger target (1.5 x the calibrated target). Compared with the current fixed-price exit.
Read-out: dollars a trade, profit factor, trades a day, busiest-day trade count, worst day, max drawdown, per window.
An exit or cooldown "helps" only if worst-window dollars a trade is not lower than the current setup's AND the worst day or max drawdown improves in at least 2 of 3 windows for at least 3 of 5 variants.
Note: the open windows (2009-2020) are calmer than 2026; a volatility-sized exit's main claim (it copes with fast markets) can only be confirmed on 2021+ data, which would be a further look needing the owner's approval.

---
# Addendum 14, 2026-10-18: can forex be rescued? Items 2 (best pairs, London+New York hours, cost-share filter) and 3 (slower trend, bigger targets) (owner: "go ahead"; written BEFORE running; open windows 2005-2020 only)
Pairs: GBPUSD, EURUSD, EURJPY (the three with the biggest range compared with cost in DIAGNOSE_REPORT.md; chosen from open-window data). Dukascopy feed, windows W1 2005-10, W2 2011-16, W3 2017-20.
Base: FINAL filters (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC), trigger = a 1-minute candle with the trend that closes beyond the M1 EMA20 (as V4), one trade at a time, 0.10 lot, cost paid once.
Grid (every combination run, nothing added after seeing results):
- Trend (price vs EMA of the last closed bar): M15 EMA200, M30 EMA100, M30 EMA200, H1 EMA100, H1 EMA200.  (the last three are the "slower trend")
- Exit in multiples of the M15 ATR(14) at the signal, anchored at the fill (target / stop): 2.122/4.700 (the +$30 style), 3.486/4.700 (the +$50 style, = V4), 5.229/7.050 (1.5x), 6.972/9.400 (2x).  (the last two are the "bigger targets")
- Hours: all hours, or only 12:00-16:00 UTC (London and New York both open).
- Cost-share filter: none, or only signals where the round-trip cost is at most 12% of the M15 ATR in dollars at 0.10 lot.
= 5 x 4 x 2 x 2 = 80 configurations per pair.
Costs per pair at 0.10 lot (as in DIAGNOSE): GBPUSD $1.13, EURUSD $1.00, EURJPY $1.02 (commission + 0.5 pip spread floor).
Reference for comparison: M30 EMA100, 3.486/4.700, all hours, no filter (= the earlier forex result).
A configuration "WORKS" for a pair if net > 0 and profit factor > 1.00 in ALL THREE windows with at least 300 trades in each. The best configurations (worst-window dollars a trade) get the random trend-side control (3 seeds).
A configuration "RESCUES FOREX" if it works on at least 2 of the 3 pairs. Nothing from 2021 on is used; if something rescues forex, a fresh test on the sealed 2021+ forex data would need the owner's approval (the sealed forex data was already looked at once for the three pairs with the older setup).

---
# Addendum 15, 2026-10-19: can a filter on the entry raise the win rate without losing many trades? (owner: "the goal is to increase the win rate and the profit factor; do not reduce the trades; test all the ideas; at most 2 or 3 filters"; written BEFORE running; open windows 2009-2020 only, sealed 2021+ NOT used)
Base: the five variants with the FINAL filters (no entries 20:00-24:00 UTC, none Monday before 07:00 UTC), one trade at a time, 0.10 lot, cost paid once, their own exit. Every signal is simulated on its own, the filter drops signals, and one-at-a-time is applied afterwards (as in addendum 13).
Win rate = target hits / (target hits + stop hits). Also reported: profit factor, dollars a trade, trades kept as a share of the base.
Fitted ONLY on W1 (2009-10), then applied unchanged to W2 (2011-16) and W3 (2017-20): every cut-off below.
Filters (one at a time first), all measured at the signal candle (the last closed 1-minute bar), in units of the M15 ATR(14):
- STRETCH: how far the close is beyond the 1-minute EMA20 in the trade direction. Skip the largest 10% / 20% (W1 cut-off).  ("chasing")
- RUN: how far the close is from the lowest low (buy) / highest high (sell) of the last 30 minutes. Skip the largest 10% / 20%.  ("chasing")
- SMALL: candle body size. Skip the smallest 10% / 20%.  ("weak push")
- BIG: candle body size. Skip the largest 10% / 20%.  ("exhaustion")
- CONF2: the candle before also closed with the trade direction.
- BREAK: the close is beyond the previous candle's high (buy) / low (sell).
- MTF: M15 close vs EMA200 AND M30 close vs EMA100 both agree with the trade direction.
- HOUR3: skip the 3 worst UTC hours (lowest W1 win rate, at least 100 W1 trades) among the hours already allowed.
- SPREAD filter: NOT testable on the open windows (the Dukascopy gold feed has no real spread; 0.05 is injected). It goes into the live EA only as a safety (skip if the spread is wider than a fixed limit), and is judged on the live log.
Control: the same share of signals removed at random (30 seeds). A filter beats chance in a window if its profit factor is above the 90th percentile of the random removals.
A filter HELPS one variant if, in ALL THREE windows: kept >= 80% of the base trades, win rate >= base, profit factor >= base; AND it beats chance in at least 2 of the 3 windows.
A filter PASSES if it helps at least 3 of the 5 variants.
Stage 2 (only after stage 1 is read): every combination of up to 3 PASSING filters, same rule but kept >= 70% of the base trades and compared with the base AND with each member alone. Anything that passes is a candidate for a separate live demo version, not for the sealed data.
Caveat stated in advance: gains of 1-2 points of win rate can be luck; only all-three-windows results count.

---
# Addendum 16, 2026-10-19: reverse direction of the chasing filters (written AFTER stage 1 of addendum 15 was read: all 12 filters made every variant worse in all 3 windows, and the stretched / large-run candles were the best signals)
Stated honestly: this idea comes from looking at the stage 1 result, so it is exploratory; it is judged on the same rule and the same windows, with cut-offs fitted on W1 only, and the stage 1 result stays on record as a failure.
New filters: STRETCHLOW10 / STRETCHLOW20 = skip the SMALLEST 10% / 20% of STRETCH (signal candle closes barely beyond the 1-minute EMA20); RUNLOW10 / RUNLOW20 = skip the SMALLEST 10% / 20% of RUN (price has barely moved off the last 30-minute extreme).
Same HELPS and PASSES rules as addendum 15 (kept >= 80%, win rate and profit factor not below base in all three windows, beats chance in at least 2 windows, at least 3 of 5 variants).

---
# Addendum 17, 2026-10-19: do stale entries lose? (owner: 'then test it here'; written BEFORE running; open windows only)
Claim from the live log (12 buckets): entries that opened right after another bucket closed (10-54 s into the minute) won 1 of 6; fresh-minute entries won 5 of 6.
Test A: on the five variants (FINAL filters, own exit, one at a time), split the research trades into CHAIN (signal at most 2 minutes after the previous trade closed; also split by whether the previous trade won or lost) and FRESH. Compare win rate (TP vs SL) and profit factor.
Test B: the same signals entered 60 seconds late (one bar later, price and stop/target anchored there) versus the normal next-bar-open entry.
Read-out per window. Supported only if CHAIN / DELAYED has a lower win rate AND profit factor than FRESH / NORMAL in all 3 windows for at least 3 of 5 variants. Live-log check: Fisher exact test on 1/6 vs 5/6.

---
# Addendum 18, 2026-10-19: size rule from the chain finding (owner: 'run it'; written BEFORE running; open windows only; the idea comes from addendum 17, so it is exploratory)
Rule: a trade gets FULL size (5 of 5 legs) if its signal came within 2 minutes after the previous trade closed AND that trade won; every other trade (after a loss, or fresh after more than 2 minutes) gets a reduced size: 3, 2 or 1 of 5 legs (weights 0.6 / 0.4 / 0.2). Every trade is still taken; only size changes.
Base = all trades full size. Five variants, FINAL filters, own exit, one at a time, 0.10 lot = full size, W1/W2/W3.
Read-out: weighted profit factor, win rate by count (unchanged by sizing), net dollars, net per unit of size traded, worst day, max drawdown, largest loss.
A size level WORKS for a variant if, in ALL three windows: profit factor > base, net per unit of size > base, and max drawdown and worst day not larger than base. It WORKS overall if it works for at least 3 of 5 variants. Caveat: the 2-minute-after-a-win chain is a pattern in the open data; it must be confirmed on the live demo before it is trusted.

---
# Addendum 19, 2026-10-19: indicators from mql5.com/en/code/mt5/indicators/page3 (owner: 'which indicators can help our bot; test them before you wire them; you can tell me we do not need any')
Written BEFORE running; open windows only; same machinery, rule and cut-off fitting (W1 only) as addendum 15; the five variants; at most 3 filters would be combined.
Candidates picked because they measure something the bot does not already measure (everything else on the page is a drawing tool, a dashboard, a utility, a pattern scanner for reversals, or an SMC zone marker; SMC / sweep / order-block ideas were tested in the earlier AUT programme and failed, and the bot is a trend-continuation bot):
- ADX15: ADX(14) of the last closed M15 bar. Skip the lowest 10% / 20% (trendless market). (from 'Completed Bar Trend Regime Dashboard' and 'Multi Timeframe Trend Matrix')
- DI15: the +DI / -DI of that M15 bar agrees with the trade direction.
- HURST: rolling Hurst exponent (rescaled range, 100 M15 closes). Skip the lowest 10% / 20% (mean-reverting regime). (from 'Hurst Exponent Regime Switch')
- SUPERTREND: SuperTrend (ATR 10, multiplier 3) on M15 agrees with the trade direction. (from 'SuperTrend')
- FVG: the signal candle leaves a 3-candle fair value gap in the trade direction (low of candle i above high of candle i-2 for a buy, mirrored for a sell). (from 'Fair Value Gap')
- ATRLOW / ATRHIGH: M15 ATR(14) relative to its own median of the last 500 M15 bars; skip the lowest 10% (dead market) / the highest 10% (spike). (from the volatility readouts of the regime dashboard)
- SESSIONBOX: not tested separately, the hour test already covered session timing and failed (addendum 15 HOUR3).
HELPS / PASSES exactly as in addendum 15 (kept >= 80%, win rate and profit factor not below base in all three windows, beats the random-removal control in at least 2 windows, at least 3 of 5 variants).
Honest expectation stated in advance: the bot already uses EMA trend, M15 and M30 agreement, and candle momentum, so most of these probably repeat it.

---
# Addendum 20, 2026-10-19: trade-management ideas from mql5.com/en/code/mt5/experts pages 1-3 (120 listings; owner: 'yes look there'; written BEFORE running; open windows only)
Most listings are risk guards, panels, loggers, breakout / grid / SMC bots, or tick-level scalpers (cannot be tested on M1 bars). The ideas that act AFTER entry and can be tested with our simulator: (a) break-even / lock (Trade managers, Safe Risk Manager, RiskPilot), (b) time-in-trade exit (Smart Loss Exit: close a position that has been open too long). Entry-side breakout bots (London / session-reopen / ORB / daily breakout) are different strategies with a few trades a day and are not tested here; the daily-loss guards were handled in the account tests.
Base: the five variants, FINAL filters, own exit (fixed target / stop), one trade at a time, 0.10 lot.
Break-even (BE): once price has gone F of the way to the target (F = 0.4, 0.6, 0.8), move the stop to entry (plus commission). Time exit (HOLD): close at market after N minutes if neither target nor stop was hit (N = 5, 15, 30, 60, 120).
Each rule is simulated per signal on its own, then one-at-a-time is applied afterwards in time order.
Read-out: profit factor, net per trade, share of trades closed by the stop, largest loss, worst day, max drawdown; win rate = profit above zero.
An exit rule HELPS a variant if in ALL THREE windows: profit factor > base, net per trade >= base, and max drawdown <= base. It PASSES if it helps at least 3 of 5 variants. Pure measurement of old data; a passing rule would still go to a separate live demo version.

---
# Addendum 21, 2026-10-19: win-rate ideas from free gold scalper source code (owner: 'we want to better our bot by looking at what others do; do not cut trades'; written BEFORE running; open windows only; the bot is not changed, this is a measurement)
Read: trillex-10s-ea (3 EMA + Parabolic SAR; leaves a trade when the trend flips, no fixed target), Gold_Sniper_Scalper by mad808 (3 trades per signal at stepped targets, trailing from +40 points; its grid / martingale part is NOT used). Both are copies in C:\Users\Tetteh\Desktop\ea_study, read only, never run.
Two ideas, both keep every entry the bot already takes. Only how a trade ends changes:
- FLIP: besides the fixed target and stop, close the trade at the close of the first M1 bar that turns against it. Three versions: FLIP_E20 (M1 bar closes on the wrong side of the M1 EMA20), FLIP_TREND (the variant's own M15/M30 trend turns against the trade), FLIP_2OPP (two M1 candles in a row against the trade).
- SPLIT5: the same total size (0.10 lot) is cut into 5 equal legs with one shared stop. Leg targets sit at 20 / 40 / 60 / 80 / 100 percent of the variant's own target. SPLIT5_BE is the same, and when the first leg hits its target the stop of the other four moves to entry plus commission.
Base = the variant's single fixed target and stop, exactly as the bot trades now. Five variants, FINAL filters (no 20:00-24:00 UTC, no Monday before 07:00 UTC), one basket at a time, 0.10 lot full size, costs paid once (spread on entry, 1.10 pip commission in total whatever the number of legs, stop slip as in common.py). Same stop-before-target rule inside a bar, same weekend flat. Counted tries: 5 rules x 5 variants x 3 windows.
A trade (basket) is a WIN when its money after cost is above zero. Win rate = share of baskets that are wins.
A rule WORKS for a variant if in ALL THREE windows: trades kept >= 98% of base (no trades lost), win rate at least 1.0 point above base, profit factor above base, net dollars per trade at least base, max drawdown not above base. It WORKS overall if it works for at least 3 of 5 variants. The money tests are there because a higher win rate bought by cutting winners short is not an improvement.
Check before trusting any number: the base replay must match common.simulate trade for trade (same count, same net dollars).
Honest expectation stated in advance: SPLIT5_BE and FLIP will probably raise the win rate by closing small, but the money and drawdown tests are the hard part; the flip exits may also trim winners.

---
# Addendum 22, 2026-10-19: can the split-then-move-stop-to-entry rule (SPLIT5_BE of addendum 21) be fixed? (owner: 'can we improve this?'; written BEFORE running; open windows only; this is a second look at an idea that failed, so it is exploratory and the bar stays the same)
Why SPLIT5_BE lost in addendum 21 (from its own numbers): it ended most baskets at about zero, which freed the bot to enter again at once. Trades per day rose about 2.3 times, each new trade paid the 1.10 fee, and money per trade fell below zero. Two repairs are tested, nothing else:
1. Shape: where the first leg is cashed and how big it is. Moving the stop to entry after a tiny first target (20% of the target) throws away many trades that would have gone on to the full target. Shapes (all: stop of the remaining legs moves to entry plus commission when the FIRST leg hits its target; leg weights sum to 1; targets as percent of the variant's own target):
   S5BE (reference from addendum 21: 5 legs, 20/40/60/80/100) | H2_40 (2 legs of 0.5: 40 / 100) | H2_60 (60 / 100) | H2_80 (80 / 100) | H5_40 (5 legs: 40/55/70/85/100) | H5_60 (60/70/80/90/100) | T3 (3 legs, weights 0.2 / 0.3 / 0.5: 50 / 75 / 100).
2. Slot: FREE = the bot may enter again as soon as the basket is closed (as in addendum 21). SLOT = the bot may not enter again until the plain fixed target / stop trade that it would have taken has finished (a 'shadow' trade; an EA can track that in code). SLOT keeps exactly the same entries as the bot trades now.
Counted tries: 7 shapes x 2 slot modes x 5 variants x 3 windows. Same rules as addendum 21 for entries, costs (commission once per basket), stop-first inside a bar and weekend flat.
A rule WORKS for a variant if in ALL THREE windows: trades per day at least 98% of base (no trades lost; extra trades are allowed), win rate at least 1.0 point above base, profit factor above base, net dollars PER DAY at least base, max drawdown not above base. It WORKS overall if it works for at least 3 of 5 variants. (Per day, not per trade, because FREE changes the number of trades.)
Honest expectation stated in advance: cashing a part early and protecting the rest trades some big winners for small ones; with the money test unchanged, I expect most shapes to fail. SLOT should remove the extra-fee problem, so any gap left is the cost of the early stop itself.

---
# Addendum 23, 2026-10-20: the entry rules of three free bots, tested as entries (owner: 'yes, and if gold does not work try forex, the 5 potent currencies'; written BEFORE running; open windows only)
Source code read from C:\Users\Tetteh\Desktop\ea_study (read only, never run): trillex-10s-ea (3 EMA + Parabolic SAR), Gold_Sniper_Scalper by mad808 (EMA + RSI), Stochastic Scalper XAUUSD (forge.mql5.io), plus the EMA 10/30 cross from the MQL5 Code Base (code 77180). Their stops, targets, grids and lot rules are NOT used: every entry gets the same two exits our own entry uses, so the only thing that differs is the entry.
Five entries, all decided on a CLOSED bar and entered at the next M1 open:
- TRI_NORMAL: M1 EMA5 > EMA20 > EMA40 and Parabolic SAR (0.02, 0.2) below the close, and the same on M5; one entry at the bar where this state turns on (mirror for sells).
- TRI_FLIP: the M1 SAR flipped to our side one bar ago and still holds, M1 EMA stack and the M5 state agree. (The bot uses 10-second bars; we only have 1-minute bars, so this is a slower copy.)
- MAD_RSI: M5 EMA13 above EMA34 and M5 RSI(14) at or below 48 when ADX(14) < 25, at or below 30 when ADX >= 25 (sells: EMA13 below EMA34 and RSI at or above 52 / 70). Checked at every M5 close.
- STOCH: M1 Stochastic (14, 3, 3): %K crosses up through 20 with %K above %D (buy); crosses down through 80 with %K below %D (sell).
- EMA_X: M15 EMA10 crosses EMA30 (at the M15 close).
Exits: gold E30_70 (+$30 / -$70) and E50_70 (+$50 / -$70) at 0.10 lot, exactly like our entry. Forex: the same ATR-multiples file used in addendum 10 (fx_k.json), 0.10 lot. FINAL filters (nothing 20:00-24:00 UTC, nothing Monday before 07:00 UTC), one trade at a time, cost paid once, windows W1 / W2 / W3 (EXPLORE / VALIDATE / STRESS).
Reference: our own entry on the same exit (gold: R3_E_E30_70 for E30_70, R4_M30PX100_T_E20SIDE_E50_70 for E50_70; forex: the same two in fx_*.csv).
Control: same signal minutes, random buy or sell (3 seeds, the best seed is used).
An entry PASSES (is a real signal) for an exit if in ALL THREE windows: money per trade above zero after cost, and money per trade above the best coin-flip control. It IMPROVES OUR ENTRY if it also has money per trade at least that of our own entry in all three windows. Counted tries on gold: 5 entries x 2 exits x 3 windows (plus 3 control seeds each).
Forex runs only if no entry passes on gold. Pairs: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD (the five most traded majors that have Dukascopy minute data since 2005).
If an entry passes on any market, a SECOND step (separately written down before running) tests it as an extra rule on top of our entry, keeping at least 80% of the trades.
Honest expectation stated in advance: copied textbook entries mostly sit near a coin flip once cost is paid; counter-trend ones (STOCH, MAD_RSI) fight our trend finding; the best chance is the trend-following TRI_NORMAL and EMA_X, which our own entry already resembles.

---
# Addendum 23, 2026-10-20: size rule (addendum 18) together with the split-target shape T3 and the slot (addendum 22) (owner: 'can we attach T3 to the current or will you test it first'; written BEFORE running; open windows only; exploratory, built from two earlier exploratory findings)
Four rules, five variants, three windows, 0.10 lot = full size, FINAL filters, costs paid once per basket: BASE (plain fixed exit, every trade full) | SIZE (plain exit, size rule: full if the previous basket won and this signal comes within 2 minutes after it was released, else 0.4 size = 2 of 5 legs) | T3S (shape T3, SLOT, always full: legs 0.2/0.3/0.5 at 50/75/100% of the target, stop of the rest moves to entry+commission after leg 1; no re-entry until the plain trade would have finished) | T3S_SIZE (T3S with the same size rule; 'released' = the slot end; 'won' = the basket closed above zero).
Read-out: win rate (basket above zero), profit factor, net dollars per day, net per unit of size traded, worst day, max drawdown, trades per day.
T3S_SIZE WORKS for a variant if in ALL THREE windows: trades per day >= 98% of BASE, win rate >= BASE + 1.0 point, profit factor > BASE and > both T3S and SIZE, net per unit of size >= BASE, max drawdown <= BASE and worst day not worse than BASE. It WORKS overall if it works for at least 3 of 5 variants. If it does not, the live demo keeps the current 2.20 (size rule only).

---
# Addendum 24, 2026-10-20: re-enter after the early (split) exit instead of waiting for the plain trade to finish (owner: 'what if after that short win we let it take another trade ... give it some time then re-enter ... does it disturb the main flow, does it give more profit factor, does it win more but costs eat it, what is the pattern in the wins'; written BEFORE running; open windows only; exploratory)
Setup: T3 split basket (addendum 22/23) with the stop to entry after leg 1. Release rule = the time a new basket may start: WAIT N = basket exit + N minutes, but never later than the slot end (the end of the plain trade). N in {0, 1, 2, 5, 10, 15, 30, 60} and SLOT (= current v3, wait for the plain trade). N = 0 is the 'free' re-entry of addendum 22.
Each is run with the size rule (full if the previous basket won and the signal comes within 2 minutes of the release, else 0.4) and without it. Five variants, three windows, 0.10 lot full size, costs paid per basket.
Q1 disturbance: trades per day against SLOT, and the share of entries that are not entries of the plain bot.
Q2 money: profit factor, net dollars a day, net per unit of size, win rate, worst day, max drawdown, cost per day (baskets x about 1.60).
Q3 pattern: with N = 0 every trade is tagged by how the previous basket ended (loss / early exit with the stop moved / full target) and the gap since it ended (0-2, 2-5, 5-15, 15+ minutes); win rate and average dollars per group.
A WAIT N BEATS SLOT for a variant if in ALL THREE windows: profit factor > SLOT, net per day >= SLOT and max drawdown <= SLOT (same sizing mode). It beats SLOT overall if it does so for at least 3 of 5 variants. Otherwise v3 keeps the slot.

---
# Addendum 25, 2026-10-20: which previous result should earn full size? (owner: 'test it'; from the pattern table of addendum 24; written BEFORE running; open windows only; exploratory)
Base = the current v3 rule: T3 split with the slot, size rule 'full if the previous basket closed above zero and this signal comes within 2 minutes of the slot release, else 0.4'. Candidates (same T3 + slot, only the size rule changes; weak = 0.4):
- ANY (current): previous basket closed above zero.
- FULLTP: previous basket reached its FULL target (all 3 legs hit).
- PLAIN: the plain fixed-exit trade of the previous slot hit its target.
- TIER3: 1.0 after a FULL-target basket, 0.7 after an early exit that closed above zero, 0.4 after a loss or fresh.
Five variants, three windows. A candidate WORKS for a variant if in ALL THREE windows: profit factor > ANY, net per unit of size >= ANY, max drawdown <= ANY and worst day not worse than ANY. It replaces the current rule only if it works for at least 3 of 5 variants. Caveat: chosen after seeing a pattern table, so it is exploratory; the demo log must agree before trusting it.

---
# Addendum 24, 2026-10-20: the free bots' rules as EXTRA FILTERS on our own entry (owner: 'why not mix them with our entry like the filters?'; written BEFORE running; gold only; open windows only)
Addendum 23 tested them standing alone. This one keeps our five variants exactly as they are and only asks: does adding one more agree-check from those bots raise the win rate while keeping most trades? Same machinery, same pass rule and same random-removal control (30 seeds) as addendum 15 (aut32_filters.py).
Filters (a trade is kept only if the check agrees with the trade side; states, not events):
- TRI_BOTH: M1 EMA5 > EMA20 > EMA40 with SAR below the close AND the same on M5 (mirror for sells).
- TRI_M1: only the M1 part of that.
- EMA_X: M15 EMA10 above EMA30 (below for sells).
- MAD_TREND: M5 EMA13 above EMA34 (below for sells).
- MAD_RSI: MAD_TREND and the M5 RSI pullback limit (buy: RSI <= 48 when ADX < 25 else <= 30; sells mirrored). This one will keep few trades and is included to show it.
- STOCH_ROOM: M1 Stochastic (14,3,3) %K below 80 for buys, above 20 for sells (not already stretched).
Counted tries: 6 filters x 5 variants x 3 windows. A filter HELPS a variant if in ALL THREE windows: trades kept >= 80%, win rate and profit factor not below base, and profit factor above the 90th-best of 30 random removals in at least 2 windows. It PASSES if it helps at least 3 of 5 variants. The user wants no big loss of trades, so a filter that keeps less than 80% cannot pass whatever its win rate.
Honest expectation stated in advance: our entry already requires M15/M30 trend and a strong candle, so most of these repeat it and change little; MAD_RSI and STOCH_ROOM go against the 'strong candle is a good signal' finding of addendum 15 and will probably hurt.

---
# Addendum 26, 2026-10-20: can the stop be smaller than the target? (owner: 'find how to make the wins big and the losses small: wins like $50 and losses like $20; go back to research and find our problem'; written BEFORE running; open windows only)
Autopsy first (existing V4 trades, 2009-2020, +$50/-$70): winners take a median of 53 min, losers 125 min; 58% of winners went more than 1.0 of price ($10 at 0.10 lot) against us before winning, 40% more than 2.0, 27% more than 3.0; 74% of losers had been in profit by more than 1.0 first. So a tight stop will cut many winners. Whether it still pays is tested, not assumed.
Grid (target T and stop S in price units, $10 per 1.00 at 0.10 lot): S in {2, 3, 4, 5} x T in {5, 8, 12, 20}, plus the current exit (S 6.89, T 5.11) for reference. Five variants, FINAL filters (no 20-24 UTC, no Monday before 07:00), one trade at a time, 24 h hold limit, cost paid once as in the research, W1/W2/W3.
Read-out: win rate (TP vs SL), average win and average loss in dollars, profit factor, dollars a trade, trades a day, max drawdown, median minutes to win and to lose.
A (S, T) pair WORKS for a variant if in ALL THREE windows: profit factor > the current exit AND dollars a trade >= the current exit AND average loss smaller than average win (win/loss ratio > 1) AND max drawdown <= the current exit. It WORKS overall if it works for at least 3 of 5 variants. Anything that works would then be re-checked in the live-regime replay (2025-26 broker data) before any demo change.

---
# Addendum 27, 2026-10-20: simulator overlap check (found while comparing the research simulator with the real EA replay trade by trade; written BEFORE running; open windows only)
Finding: common.simulate sets busy_until = t1[k] (the OPEN time of the exit bar k). A signal whose time equals that open is therefore accepted and filled at the open of bar k, i.e. the next trade starts at the open of the same bar in which the previous trade exits, before that exit has happened. The real EA can only enter at the open of bar k+1.
Test: the five variants, FINAL filters, own plain exit, W1/W2/W3, one trade at a time. A = as before (next entry allowed from the open of the exit bar). B = corrected (next entry allowed only from the open of the bar AFTER the exit bar, i.e. busy = exit bar open + 1 minute). Every signal is simulated on its own and the selection is applied afterwards.
Read-out: trades per day, win rate, profit factor, dollars a trade, per window. If B's profit factor stays above 1.00 with dollars a trade >= 0 in all three windows for at least 3 of 5 variants, the entry edge survives the correction; otherwise it does not.

---
# Addendum 28, 2026-10-20: honest search for reward-bigger-than-risk strategies on higher timeframes (owner: 'find a strategy where wins are bigger than losses and wins are more than losses; maybe higher time frames with 1:3, 1:5; start small'; written BEFORE running; open windows only; simulator with the BT20 fix)
Gold, Dukascopy feed, windows W1 2009-10, W2 2011-16, W3 2017-20. Sealed 2021+ is NOT used. Costs as in the research (round trip about $1.60 at 0.10 lot, paid once). One trade at a time, entry at the first M1 open after the signal bar closes, 5-day hold limit, no weekend carry beyond the simulator's own weekend flat.
Timeframes: M15, H1, H4 (signal bar closed; ATR(14) of that timeframe).
Strategies (4, defined now, no tuning): DON = close above the highest high of the previous 20 bars (long) / below the lowest low (short); PULL = in an uptrend (EMA50 > EMA200 and close > EMA200) the bar's low touches EMA20 and it closes back above EMA20 (long; mirrored short); RSI = in the same uptrend RSI(14) closes below 30 (long; mirrored short, above 70); MOM = a bar with body at least 1.0 ATR closing in the direction of the EMA50 > EMA200 trend.
Exits: stop = K x ATR from the fill, K in {1.0, 1.5, 2.0}; target = RR x stop, RR in {1.5, 2, 3, 5}.
Grid = 4 strategies x 3 timeframes x 3 K x 4 RR = 144 configurations, each with a random-direction control at the SAME signal times and exits (one seed) to measure how often chance passes.
Read-out per window: trades, win rate, average win and average loss in R, profit factor, expectancy in R.
A configuration PASSES if in ALL THREE windows: at least 150 trades, profit factor >= 1.10 AND expectancy > 0, and its profit factor is above its random control's. We report how many of the 144 pass, and how many random controls would pass the same rule (the chance rate). Survivors are then run in the real-tick replay on 2025-2026 broker data, which no rule has been tuned on.

---
# Addendum 29, 2026-10-20: the addendum 28 search on 10 markets (owner: 'GO'; written BEFORE running; open windows only; BT20-fixed simulator)
Same 144 configurations (4 strategies x M15/H1/H4 x K 1.0/1.5/2.0 x RR 1.5/2/3/5), same entry/exit rules and the same random-direction control, on gold and the 9 FX majors (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURGBP, EURJPY), Dukascopy feed, windows W1 2005-10, W2 2011-16, W3 2017-20 (gold from 2009). Costs per market as in common.COMM (round trip, paid once). Sealed 2021+ is NOT used.
A configuration WORKS on a market if, in all three windows, trades >= 100, profit factor >= 1.10, expectancy > 0, and profit factor above its random control's. A configuration WORKS BROADLY if it works on at least 5 of the 10 markets. Also reported: the average profit factor of each configuration across markets and windows, and the chance rate (how many random-control configurations pass the same profit-factor rule, out of 1,440).
A broad survivor is then run in the real-tick replay on 2025-2026 broker data (the markets the broker has), which no rule has been tuned on. If nothing works broadly, the conclusion is that these classic signals have no edge on these markets and costs.
