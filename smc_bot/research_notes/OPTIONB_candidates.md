# Option B: non-SMC candidates, ranked

Date: 2026-10-03. Web research only, no code. Builds on `SMC_evidence.md` (not repeated here).
Verification key: **[V]** = paper text read (done in the earlier note) or confirmed to exist by search this session; **UNVERIFIED** = I could not read the number myself.

## Honest headline

The documented short-horizon FX effects are real but tiny: about 2 basis points (bps) per window, which is roughly 0.2 pip on EURUSD. Your cost is about 1 pip round trip, and 0.06R on structural stops. A 0.2 pip gross edge on a 10 pip stop is about 0.02R, so it is roughly **3x too small** for your "gross >= 2x cost" rule, before any decay since the paper samples ended. The authors of the main fix paper say the same: returns turn negative at full quoted spreads, and only positive at halved spreads. Neely, Weller and Ulrich (J. Financial and Quantitative Analysis, 2009) [V, existence and abstract] found FX technical-rule profits genuine in the 1970s-80s, gone by the early 1990s, and fading for less-studied rules too. The most likely true answer is: **none of these clear your costs.** The list below is the best of a weak field, ranked by how solid the "who pays whom" story is.

## Ranked shortlist

| Rank | Candidate | Mechanism strength | Expected to clear cost? |
|---|---|---|---|
| 1 | Month-end equity-hedging flow into the London 4pm fix | Strongest: forced, rule-based, dated buyers/sellers | Unknown (size UNVERIFIED), but the only one with large payoff per trade |
| 2 | Fix-window reversal ("W"), conditioned on a big run-up (old BT11) | Good: benchmark orders plus dealer inventory | Almost certainly not unconditionally; conditional version is the only chance |
| 3 | Time-of-day / intraday-versus-overnight drift on EURUSD | Moderate: home-hours buying pressure | Probably not; costs ate all but EURUSD even in 1997-2007 |
| - | Old BT12 extension fade | Weak (own-data hint, not a mechanism) | Drop |
| - | Weekly currency momentum, carry, lead-lag, triangular | See "Rejected" | No |

### 1. Month-end hedging flow at the London fix
1. **Who pays whom.** Fund managers holding foreign equities hedge the currency, and reset the hedge at month-end at the 4pm London benchmark fix, because that is what they are measured against. If foreign stocks rose in the month, they hold more foreign currency exposure than their hedge covers, so they must sell that currency. They have to trade, at a set time, whatever the price. The liquidity provider who takes the other side is paid a premium through the pre-fix price push, and it reverses after. They keep paying because the mandate is the benchmark, not the price.
2. **Evidence.** Melvin and Prins, "Equity hedging and exchange rates at the London 4 p.m. fix", Journal of Financial Markets 22, 2015 [V exists, abstract only]: equity appreciation over the month predicts currency depreciation before the month-end fix. Effect size, sample years and cost test: **UNVERIFIED**. Read the paper's table before registering anything.
3. **Rule (pre-registered shape; sign taken from the paper's finding).** Last business day of each month. Signal: the month-to-date return of a global equity index (you need one external daily series; this is the one data dependency). If positive, short the foreign currency against USD (short EURUSD, GBPUSD, AUDUSD, NZDUSD; long USDJPY, USDCAD, USDCHF) from 12:00 to the fix (16:00 London local; DST handled as in BT7); mirror if negative. Exit at the fix. Stop 1.5 x ATR(M15 x 16 bars). A "no trade" band for small equity moves is **not** specified in the paper as far as I can verify; use none. Report the post-fix reversal as a separate, second test, not an optimised exit.
4. **Frequency.** 12 per instrument per year, so about 240 events per pair over 20 years. That is far better than the 66 events the earlier note feared, because you now have 2005-2025.
5. **Survives cost?** Only if the move into the fix is at least 2 to 3 pips on majors. The paper's size is UNVERIFIED, so this is the main open question.
6. **Most likely failure.** The fix scandal (2013-15) and the 2015 move of the benchmark to a 5-minute window changed fix trading behaviour; the effect may be gone after about 2015. Test pre- and post-2015 separately.

### 2. Fix-window reversal, conditional version (old BT11)
1. **Who pays whom.** Customers send benchmark orders to be filled at the fix price. Dealers pre-position, then unwind afterward. The dollar rises into the fix and falls after it. Ito and Yamada (J. International Economics, 2017) [V exists, abstract] find that at the Tokyo fix customers are biased to buy foreign currency in a predictable way and that bank-set fix prices sit above the median traded price. That last part is a quote artifact you cannot capture; the order-flow bias is the useful bit.
2. **Evidence.** Krohn, Mueller and Whelan, "FX Fixings and Returns around the Clock", Journal of Finance 79(1), 541-578, 2024 [V]. G9 versus USD, 1999-2018. Dollar +1.7 to +2.1 bps per window into the Tokyo and London fixes (t 4 to 12), and -1.9 to -2.2 bps after. At full quoted spreads net returns are negative; at halved spreads Sharpe 0.5 to 0.7 at best. The authors say it suits investors with below-average costs.
3. **Rule.** London fix on EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD. At 15:00 UTC winter / 14:00 UTC summer measure the USD-leg return of the previous 60 minutes. Only if it exceeds +1 x ATR(M15 x 4) (moving up) or below -1 x that (down): enter against it at the fix, hold 60 minutes, stop 1.5 x ATR. Window lengths of 60 minutes are my choice, not the paper's; the paper's exact windows must be checked and used instead if they differ.
4. **Frequency.** Roughly 20 to 40 conditional trades per pair per year.
5. **Survives cost?** Unconditional: no (0.2 pip against about 1 pip). Conditional: only if the effect is several times larger on high-move days. The paper says it is stronger in high-volatility and wide-spread states, which is also when your costs are higher.
6. **Most likely failure.** Gross effect is smaller than the spread, as the authors' own cost section implies.

### 3. Time-of-day drift, EURUSD only
1. **Who pays whom.** Participants are net buyers of their home currency's counterpart in their own trading hours (so the home currency tends to depreciate in its own hours). Another way to see it: in Krohn, Mueller and Whelan's "FX premia around the clock" working paper [existence confirmed by search; numbers from a search summary, UNVERIFIED], long-foreign-currency returns are about +5% a year intraday and about -4% overnight, both significant, with US-hours carry doing most of the work.
2. **Evidence.** Breedon and Ranaldo, J. Money, Credit and Banking 45(5), 2013 [V]. Six pairs, hourly EBS data 1997-2007. After bid/ask costs only EURUSD held up (Sharpe about 1.3 and 0.9 for two windows). The hours were not recoverable by me: take them from the paper's table, do not search for them in your data.
3. **Rule.** EURUSD only, the two windows from the paper, one trade per window per day, exit at window end, no stop beyond 2 x ATR(D1)/4.
4. **Frequency.** About 250 per year per window.
5. **Survives cost?** Edge per trade is around 1 bp, below a 1 pip spread. Doubtful.
6. **Most likely failure.** 1997-2007 institutional data. Eleven years of competition afterward.

## Rejected (and why, so you do not revisit them)
- **Old BT12 extension fade.** The reversal after M15 breaks was measured at 0.02 to 0.13 ATR, below the 0.11 to 0.20 ATR round trip (BT4), and the ADX and "fresh breakout" filters came from peeking at DEV. No external mechanism. This is the same family as the sweeps already ruled out.
- **Weekly cross-sectional currency momentum.** A Massey University dissertation (63 currencies, found by search; not peer reviewed, UNVERIFIED) reports momentum above 8% a year, breakeven costs from 2 to 97 bps. It needs emerging-market currencies, which carry the profit and the cost. Your 10 pairs have no such spread, and BT9 already found no multi-day momentum.
- **Carry.** Real, monthly horizon, crash risk, needs swap data and cannot be judged on M1 bars.
- **Cross-rate lead-lag / triangular arbitrage.** The lead-lag literature (arXiv 1906.10388, "Lead-lag Relationships in Foreign Exchange Markets") is about 1-minute and faster moves captured by latency-sensitive firms; a separate paper (arXiv 0812.0913, "The Mirage of Triangular Arbitrage") finds the apparent opportunities are not capturable. Not a retail edge.
- **Gold seasonality, NFP drift, announcement continuation.** Gold's drift is below its spread and built on a bull market; news fills are not realistic on M1 bars.

## Recommended way to test (cheapest first)
1. One **event study in pips** per candidate, no brackets, all 10 instruments, gross and net. Candidates 1 and 2 are a few thousand rows each; run them in one afternoon.
2. Total tests registered in advance: about 6 (month-end x 2 sub-periods, fix x London/Tokyo, time-of-day x 2). Apply Benjamini-Hochberg across these plus the earlier families.
3. Stop at gross < 2 x cost, or fewer than 8 of 11 same-sign instruments. Do not search other clock times for a "fix-like" effect.
4. Keep the TEST period sealed; run VAL once.

## Verification notes
- Confirmed by search this session: Krohn et al. JF 2024 (volume, pages, abstract, the "lower than average transaction costs" caveat); Ito and Yamada JIE 2017; Melvin and Prins JFM 2015 (abstract only); Neely, Weller and Ulrich JFQA 2009; existence of the two arXiv papers and the FX-premia working paper.
- I could not read: the fix window lengths in Krohn et al., the post-2015 subsample table, the Melvin-Prins effect size, the Breedon-Ranaldo hours. The files I could fetch were unreadable or blocked (403), so these four items remain open and must be read from the PDFs before pre-registration.
