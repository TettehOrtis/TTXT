# SMC (Smart Money Concepts) Strategy: Full Guide for a Forex Bot

## 1. Core Idea

Big players (banks, funds) move price in ways that leave footprints. SMC is about reading those footprints so you trade with them, not against them.

---

## 2. Building Blocks

### 2.1 Trend (Market Structure)
- **Uptrend:** higher highs (HH) and higher lows (HL).
- **Downtrend:** lower highs (LH) and lower lows (LL).
- **BOS (Break of Structure):** price breaks the last high (uptrend) or last low (downtrend). The trend is continuing.

### 2.2 Fair Value Gap (FVG)
- A 3-candle pattern where candle 1's wick and candle 3's wick do not overlap, leaving a gap around candle 2.
- It shows price moved so fast that orders were left unfilled.
- Price often returns to fill part of the gap before continuing. It is used as an entry zone.
- **Bullish FVG:** candle 3 low > candle 1 high. Zone = candle 1 high to candle 3 low. Bearish is the reverse.

### 2.3 Order Block (OB)
- The last opposite candle before a strong move.
- **Bullish OB:** the last down candle before a strong up move.
- **Bearish OB:** the last up candle before a strong down move.
- It marks where big players likely placed orders. Price often returns to it.

### 2.4 Liquidity
- Stop-losses cluster above old highs and below old lows.
- Smart money often pushes price to sweep those levels (a "liquidity grab") before reversing.
- **Liquidity sweep:** price wicks beyond a previous swing high/low, then closes back inside.

### 2.5 CHoCH (Change of Character, also called Change of Direction)
- The first sign the trend may be flipping.
- In an uptrend: price breaks below the last higher low, so the HH/HL pattern is broken.
- In a downtrend: price breaks above the last lower high.
- **BOS = trend continues. CHoCH = trend might reverse.**

### 2.6 Basic Example (Buy)
1. Downtrend exists, price sweeps liquidity below a low.
2. CHoCH appears (price breaks above the last lower high), so the trend may be flipping.
3. Price pulls back into a bullish OB or FVG.
4. Enter long there, stop below the OB, target the next high.

---

## 3. Multi-Timeframe (Top-Down) Approach

High time frame (HTF) = **1H to 30M**.

| Time frame | Job |
|---|---|
| **1H** | Overall bias: trend, BOS/CHoCH, big OBs and FVGs, liquidity highs/lows |
| **30M** | Refine the 1H picture: confirm the bias and narrow the 1H zone (POI) |
| **15M** | Drop here once price reaches the POI, and watch how it reacts |
| **5M** | Look for the CHoCH (confirmation) and the entry zone |
| **1M** | Optional, for a tighter entry and smaller stop |

Most people pick two or three lower frames, not all of them. A common combo is 1H/30M for bias, then 5M for entry.

### Step by Step
1. **On 1H:** Mark the trend, the last BOS, and the unmitigated OB/FVG zones. Decide the bias: buy or sell.
2. **On 30M:** Check that it agrees with the 1H. Pick the exact POI, an OB or FVG inside the 1H zone.
3. **Wait.** Do nothing until price reaches that POI. Ideally it also sweeps liquidity first.
4. **Drop to 15M/5M:** Once price is in the POI, look for a **CHoCH in your direction**. For a buy, price breaks the last lower high on the lower time frame.
5. **Enter** on the lower time frame's OB or FVG that formed the CHoCH.
6. **Stop loss:** Just beyond that lower time frame OB, or beyond the sweep low/high.
7. **Target:** The next HTF high/low (1H or 30M liquidity), which gives good risk-to-reward.

### Example (Buy)
- 1H uptrend, price pulls back into a 1H bullish OB.
- 30M shows the same zone and sweeps a low there.
- On 5M, price breaks the last lower high (CHoCH), then pulls back into a 5M FVG.
- Enter long there, stop below the 5M swing low, target the last 1H high.

### Key Rules
- The HTF decides direction, the LTF only decides timing.
- Never take an LTF CHoCH that goes against the 1H bias.
- A ratio of about 1:6 between HTF and entry time frame works well (1H to 10M/5M).

---

## 4. Adding EMA

**Use EMA as a filter, not as an entry signal.**

### Why EMA Helps
- **Objective trend bias:** Structure (HH/HL) is a bit subjective. EMA gives a clear rule: price above = buy bias, below = sell bias. Bots need exactly that.
- **Filters bad trades:** Stops the bot from taking a 5M CHoCH buy while the 1H is clearly bearish.
- **Confluence:** An OB or FVG that lines up with the EMA is a stronger zone.
- **Easy to code:** One line of math, no interpretation needed.

### Honest Downsides
- It lags. It won't catch early reversals, which is what CHoCH is for.
- It is partly redundant, since BOS/CHoCH already show the trend.
- In choppy, ranging markets, price crosses it constantly and gives false signals.

Use it to say "only trade in this direction," never "enter because price touched the EMA." Backtest with and without it, because that is the only way to know if it actually helps.

### Suggested EMAs
| Time frame | EMA | Purpose |
|---|---|---|
| 1H | EMA 200 | Main bias |
| 30M | EMA 50 | Confirms bias |
| 5M | EMA 20 (optional) | Momentum check on entry |

---

## 5. How to Describe the Strategy to a Forex Bot

Give it rules in exact, testable terms.

### 5.1 Definitions
- **Swing high:** A candle whose high is higher than the N candles on each side (N = 3 or 5).
- **Swing low:** The same, but for lows.
- **Uptrend:** Last swing high > previous swing high AND last swing low > previous swing low. Downtrend is the opposite.
- **BOS:** A candle **closes** beyond the last swing high (uptrend) or last swing low (downtrend). Wicks alone don't count.
- **CHoCH:** In an uptrend, a candle closes **below** the last higher low. In a downtrend, a candle closes **above** the last lower high.
- **Bullish FVG:** Candle 3 low > candle 1 high. Zone = candle 1 high to candle 3 low. Bearish is the reverse.
- **Bullish OB:** The last bearish candle before an impulsive move up that breaks structure. Zone = that candle's high to low. Bearish OB is the reverse.
- **Liquidity sweep:** Price wicks beyond a previous swing high/low, then closes back inside.

### 5.2 Bias (1H and 30M)
- Buy bias only if: 1H structure is bullish AND price is above the 1H EMA 200 AND 30M agrees.
- Sell bias is the mirror image.
- If they disagree, **no trade**.

### 5.3 Point of Interest (30M)
- Pick an unmitigated OB or FVG in the direction of the bias.
- Prefer zones in the discount half (for buys) or premium half (for sells) of the current range.
- Ideally the zone also caused a liquidity sweep.

### 5.4 Trigger (5M)
- Wait for price to enter the POI. Do nothing before that.
- Then wait for a 5M **CHoCH in the bias direction**.
- Enter on the retest of the 5M OB or FVG that made the CHoCH (limit order).
- The entry must be on the correct side of the 5M EMA 20 (optional filter).

### 5.5 Risk
- **Stop loss:** Beyond the 5M OB or the sweep extreme, plus a small buffer for spread.
- **Take profit:** The next 1H swing high/low (liquidity), minimum RR 1:2. Skip the trade if RR is below 1:2.
- **Risk per trade:** Fixed percent (for example 0.5% to 1%). One trade at a time per pair.

### 5.6 Rules for Not Trading
- Outside London/New York sessions.
- Around high-impact news.
- Spread too wide.
- Daily loss limit hit (for example 2 losses or 2%).
- The POI has already been touched once (mitigated).

### 5.7 Order of Operations (Important for Code)
Bias (1H) -> POI (30M) -> price reaches POI -> CHoCH (5M) -> entry on retest -> manage SL/TP.
Every step must be true, and the bot must never skip ahead.

---

## 6. Lookback and Wait (Candle Counts)

Two different things:
- **Lookback:** how many past candles the bot reads.
- **Wait:** how long a setup stays valid before the bot drops it.

These are starting points. Backtest and adjust.

### 6.1 Lookback

| Time frame | Swing strength (N candles each side) | Lookback for structure | Load for indicators |
|---|---|---|---|
| **1H** | 3 to 5 | 100 to 150 (about 4 to 6 days) | 300+ (EMA 200 needs warm-up) |
| **30M** | 3 to 5 | 100 to 150 (about 2 to 3 days) | 150+ (EMA 50) |
| **15M** | 3 | 60 to 100 | 100 |
| **5M** | 2 to 3 | 50 to 100 (about 4 to 8 hours) | 50+ (EMA 20) |

Load extra candles beyond the lookback so the EMA is properly warmed up. An EMA calculated on too little data gives wrong values.

### 6.2 Wait (How Long a Setup Stays Valid)
- **POI (OB/FVG) on 30M:** Valid for about 48 to 96 candles (1 to 2 days). If price hasn't come back by then, the zone is stale, so drop it.
- **After price enters the POI:** Give the 5M CHoCH about 12 to 36 candles (1 to 3 hours) to appear. If there is no CHoCH by then, cancel the setup. The zone is not reacting.
- **Entry limit order on the retest:** Cancel it after about 6 to 12 candles (5M) if it hasn't filled.
- **Zone dies immediately if:** A candle closes fully through it (invalidated), or price has already tapped it once (mitigated).

### 6.3 Swing Confirmation Delay
A swing high with N = 3 is only confirmed **3 candles after it forms**. So the bot always knows a swing late. That delay is normal, and the bot must never treat an unconfirmed swing as real, or it will repaint and give fake signals.

### 6.4 Rules That Keep the Bot Honest
- Only use **closed candles**. Never decide on the candle still forming.
- Re-scan structure on each new closed candle of that time frame, not on every tick.
- The higher the time frame, the longer things stay valid. A 1H zone can live for days, while a 5M zone lives for hours.
- Bigger N gives fewer but cleaner swings. Smaller N gives more but noisier ones. Start with N = 3 on the lower time frames and 5 on 1H.
