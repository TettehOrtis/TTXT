# BT6: full SMC top-down strategy (spec for the implementing agents)

Read `AGENT_BRIEF.md` first (hard rules, date split, deliverables), then `smc-strategy.md` sections 3, 5 and 6, then the
component reports `results/BT1..BT5,BT7/report.md`. BT6 = the strategy as a disciplined discretionary SMC trader would run it,
corrected by what the component backtests proved.

## Lessons from BT1–BT7 that BT6 must respect
- Every component alone is ~random BEFORE costs (gross R ≈ 0): sweeps (BT1), OBs (BT2), FVGs (BT3), BOS/CHoCH (BT4),
  bias as a trade (BT5), session sweeps (BT7). **So judge BT6 on GROSS R first** (before commission/slippage). If gross ≈ 0,
  no cost or exit tweak can save it.
- Bias: H1 structure trend is WORSE than random at 24h–3d. Use **H4 structure trend (N=3) and/or price vs EMA200(H1)**.
  Block trades right after a fresh H4 breakout/retest; ADX≥25 trend regime was worse than range (BT5).
- Structure: `structure()` labels minor internal breaks as BOS (59%). Offer a **major/external structure** option: a break of
  the swing that produced the latest extreme (BT4).
- Stops: small stops die from costs. LTF-CHoCH confirmation that puts the stop beyond the structural swing cut cost drag ~2/3 (BT1).
  Require stop ≥ 10× round-trip commission.
- Sessions: trade only the London killzone and NY/overlap (DST-aware ~07/08–11 and 12/13–16 UTC), Tue–Fri, flat by 20:00 UTC,
  skip EURGBP (BT7). Only London fills were same-sign on DEV and VAL in BT3 (weak lead).

## The pipeline (smc-strategy.md 5.7, every step must be true, never skip ahead)
1. **Bias (HTF):** H4 structure + EMA200-H1 agree (variants: H4 only; EMA only). No trade if they disagree or an H4 break happened in the last k bars.
2. **POI (30M or H1):** an unmitigated OB or FVG in the bias direction, in discount (buys) / premium (sells) of the H4 dealing range; valid 48–96 bars.
3. **Wait** until price reaches the POI (first touch only). Ideally it sweeps liquidity into the POI (record a flag; test with vs without).
4. **LTF confirmation (5M or 15M):** a CHoCH in the bias direction within 12–36 bars of the touch (major-structure option).
5. **Entry:** limit on the retest of the LTF FVG/OB that created the CHoCH (valid 6–12 bars), or market at the CHoCH close (compare).
6. **Stop:** beyond the LTF swing/sweep extreme + buffer (why: that is where the setup is wrong). Skip if stop < 10× commission.
7. **Target / management (a trader decides how long to hold):** next HTF liquidity (H1/H4 swing) with RR ≥ 2; compare with
   fixed 2R/3R, partial at 1R + runner to liquidity, break-even at 1R, trailing by LTF/HTF structure, and time exits. Holding time is free.

## Protocol
- ≤ 20 variants on DEV, chosen from trading logic. Report gross R, cost R and net R for each. At most 3 go to VAL. TEST is locked.
- Record per trade: every step's timestamp (bias, POI creation, touch, sweep, CHoCH, order, fill, exit), SL/TP and their reasons,
  and context features (for BT8 ML later): bias strength, zone type/size/age, discount %, sweep flag/depth, bars to CHoCH, RR,
  session, ATRs, EMA distances.
- 10 example trades with every timestamp so a human can follow each one on a chart. Replay 3 by hand on M1.
