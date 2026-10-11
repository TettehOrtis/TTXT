# Glossary: every term used in the reports, in plain words

## Money and results
| Term | Meaning |
|---|---|
| **R** ("risk") | The amount you lose if the stop loss is hit. +2R = you made twice what you risked; −1R = you lost what you risked. With $5 risk per trade, +2R = +$10. |
| **result_in_risks_R** | The trade's result measured in R. |
| **Gross** | Result **before** costs (spread, commission, slippage). |
| **Net** | Result **after** all costs. This is what you actually keep. |
| **Pips after costs** | Price distance won or lost, minus costs. |
| **Profit factor (PF)** | Total money won ÷ total money lost. Above 1 = profitable; 1.5+ is good; below 1 = losing. |
| **Expectancy per trade** | Average profit per trade (wins and losses together). |
| **Payoff ratio** | Average win ÷ average loss. |
| **Max drawdown** | The biggest drop from a balance high to a later low: the worst losing stretch you would have lived through. |
| **Win rate** | % of trades that made money. A low win rate is fine if wins are much bigger than losses (and vice versa). |
| **$ per 0.10 lot** | Money if every trade used 0.10 lot. At 0.01 lot divide by 10. |
| **Commission** | The Zero account's fee per lot (e.g. EURUSD $2.50 per lot per side). |
| **Spread** | Difference between the buy and sell price. It is a cost on every trade. |
| **Slippage** | Getting filled at a worse price than planned (we assume 0.2 pip on stops). |

## What happened inside a trade
| Term | Meaning |
|---|---|
| **MFE**, "best_open_profit_R" | *Maximum Favourable Excursion*: the most the trade was **in profit** at any moment before it closed. MFE 1.7R on a loser = it was up 1.7× the risk, then reversed. |
| **MAE**, "worst_open_loss_R" | *Maximum Adverse Excursion*: the most the trade was **in loss** at any moment before it closed. |
| **Stop loss (SL)** | The price where the trade closes at a loss because the setup was wrong. |
| **Take profit (TP)** | The price where the trade closes in profit. |
| **Reward-to-risk (RR)**, e.g. 1:3 | The target distance compared with the stop distance. 1:3 = the target is 3× further than the stop. |
| **Break-even (BE)** | Moving the stop to the entry price, so the trade can no longer lose. |
| **Limit order** | An order waiting at a chosen price. It fills only if price comes back to it. |
| **Weekend close** | We close everything on Friday 20:45 UTC so no trade is open over the weekend. |

## SMC terms
| Term | Meaning |
|---|---|
| **OB**, Order Block | The last opposite-colour candle before a strong move: a zone price may come back to. |
| **FVG**, Fair Value Gap | A 3-candle gap left by a fast move: a zone price may come back to fill. |
| **POI** | *Point of Interest*: an OB or FVG we wait for price to reach. |
| **BOS** | *Break of Structure*: price breaks the last swing in the trend direction (trend continues). |
| **CHoCH** | *Change of Character*: price breaks the last swing against the trend (a possible reversal). We use it on the 5-minute chart to confirm the entry. |
| **Liquidity sweep** | Price pokes past an old high or low (taking out stop orders), then comes back. |
| **Discount / premium** | The lower / upper half of the recent 4-hour range. Buy in discount, sell in premium. |
| **Bias** | The direction we allow trades in (from the 4-hour trend + EMA200 on 1-hour). |
| **Killzone** | The active session windows: London ~07–10 UTC, New York ~12–15 UTC. |
| **HTF / LTF** | Higher timeframe (1H, 4H) / lower timeframe (5M, 15M). |
| **EMA** | Exponential moving average (EMA200 = average of the last 200 candles, weighted to recent ones). |

## Testing terms
| Term | Meaning |
|---|---|
| **DEV** | 2021–2023: the years used to build and compare ideas. |
| **VAL** | 2024 to June 2025: unseen years used once to confirm. |
| **TEST** | July 2025 onward: locked, run once on the final system only. |
| **HIST** | 2005–2020: 16 older years downloaded later, never seen by the strategy. |
| **REF** | The frozen, canonical version of the full SMC strategy. |
| **Out-of-sample** | Tested on data the strategy or model never saw while it was being built. The only results that count. |
| **Overfitting** | Tuning a strategy until it fits past data perfectly but fails on new data. |
| **t-stat (t)** | How sure we are a result isn't luck. Below ~2 = could easily be luck; 2.5–3+ = probably real. |
| **Walk-forward** | Train on the past, test on the next period, move forward, repeat. Never test on data used for training. |
| **Shuffle test** | Scramble the answers and retrain. If the model does just as well, it learned nothing real. |
| **Feature** | One fact the ML looks at (e.g. "4-hour trend up", "stop size"). |
| **Forward test / demo** | Running the bot live on the demo account to check that live trading matches the backtest. |
